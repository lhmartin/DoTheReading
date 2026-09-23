// Electron main process. All study data comes from study_api.py in the repo
// root, so the app never parses question files or history itself.
const { app, BrowserWindow, clipboard, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const os = require("os");
const fs = require("fs");

const REPO_DIR = path.join(__dirname, "..");

// Installed builds ship the pipeline frozen by PyInstaller; running from a
// checkout uses the repo's .venv (or whatever Python is on PATH).
function pipelineCommand(args) {
  if (app.isPackaged) {
    const exe = path.join(process.resourcesPath, "pipeline", process.platform === "win32" ? "study_api.exe" : "study_api");
    return { command: exe, args, cwd: path.dirname(exe) };
  }
  const candidates =
    process.platform === "win32"
      ? [path.join(REPO_DIR, ".venv", "Scripts", "python.exe"), "python"]
      : [path.join(REPO_DIR, ".venv", "bin", "python"), "python3"];
  const python = candidates.find((p) => !p.includes(path.sep) || fs.existsSync(p)) || candidates.at(-1);
  return { command: python, args: [path.join(REPO_DIR, "study_api.py"), ...args], cwd: REPO_DIR };
}

// Runs study_api.py and resolves with the JSON object it prints last.
// onLine gets every other JSON line (the streamed log of a run).
function callApi(args, onLine) {
  return new Promise((resolve, reject) => {
    const { command, args: commandArgs, cwd } = pipelineCommand(args);
    const child = spawn(command, commandArgs, { cwd, env: process.env });
    let stdout = "";
    let stderr = "";
    let last = null;

    child.stdout.on("data", (data) => {
      stdout += data;
      const lines = stdout.split("\n");
      stdout = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        let parsed;
        try {
          parsed = JSON.parse(line);
        } catch {
          continue; // stray print from a library
        }
        if (parsed.log !== undefined && onLine) onLine(parsed.log);
        else last = parsed;
      }
    });
    child.stderr.on("data", (data) => (stderr += data));
    child.on("error", (err) => reject(new Error(`Couldn't run Python: ${err.message}`)));
    child.on("close", (code) => {
      // A command that reports {ok: false, ...} is telling the caller
      // something structured (e.g. needs_render); only an unhandled failure
      // — a bare {error} — is thrown.
      if (last && last.ok === false) resolve(last);
      else if (last && last.error) reject(new Error(last.error));
      else if (last) resolve(last);
      else reject(new Error(stderr.trim().split("\n").at(-1) || `the pipeline exited with code ${code}`));
    });
  });
}

// Self-test: launch with DTR_SELFTEST=<file> and the app loads the UI, asks
// the pipeline for its environment through the real preload bridge, writes
// the result to that file and exits. CI runs this against the packaged build
// so a broken pipeline path is caught before a release goes out.
function runSelfTest(win, resultFile) {
  const finish = (result) => {
    try {
      fs.writeFileSync(resultFile, JSON.stringify(result));
    } catch (err) {
      console.error(err);
    }
    app.exit(result.ok ? 0 : 1);
  };
  const timer = setTimeout(() => finish({ ok: false, error: "timed out after 90s" }), 90_000);
  win.webContents.once("did-finish-load", async () => {
    try {
      // Every $("id") in the renderer must resolve: removing an element while
      // leaving a lookup behind throws at click time, not at load.
      const missingIds = await win.webContents.executeJavaScript(`(async () => {
        const source = await (await fetch("app.js")).text();
        const ids = [...source.matchAll(/\\$\\("([A-Za-z0-9_-]+)"\\)/g)].map((m) => m[1]);
        return [...new Set(ids)].filter((id) => !document.getElementById(id));
      })()`);
      const environment = await win.webContents.executeJavaScript("window.study.environment()");
      // The hidden attribute is easy to break with a stray display rule, and
      // an overlay stuck on top makes the app unusable.
      const overlayHidden = await win.webContents.executeJavaScript(
        "getComputedStyle(document.getElementById('dropzone')).display === 'none'",
      );
      clearTimeout(timer);
      finish({
        ok: Boolean(environment && environment.settings && environment.settings.model)
          && overlayHidden
          && missingIds.length === 0,
        overlayHidden,
        missingIds,
        packaged: app.isPackaged,
        model: environment?.settings?.model,
        ollamaRunning: environment?.ollama?.running,
      });
    } catch (err) {
      clearTimeout(timer);
      finish({ ok: false, error: String(err && err.message ? err.message : err) });
    }
  });
}

function createWindow() {
  const win = new BrowserWindow({
    show: !process.env.DTR_SELFTEST,
    width: 1400,
    height: 900,
    backgroundColor: "#12151c",
    title: "DoTheReading",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      plugins: true, // Chromium's built-in PDF viewer
    },
  });
  win.loadFile(path.join(__dirname, "renderer", "index.html"));

  if (process.env.DTR_SELFTEST) runSelfTest(win, process.env.DTR_SELFTEST);

  // Dev hook: DTR_SHOT=<file> renders, optionally runs DTR_SHOT_JS, saves a
  // PNG of the window and exits. Used to check the UI while developing.
  if (process.env.DTR_SHOT) {
    win.webContents.once("did-finish-load", async () => {
      const wait = (ms) => new Promise((done) => setTimeout(done, ms));
      await wait(1200);
      if (process.env.DTR_SHOT_JS) await win.webContents.executeJavaScript(process.env.DTR_SHOT_JS);
      await wait(Number(process.env.DTR_SHOT_DELAY || 900));
      fs.writeFileSync(process.env.DTR_SHOT, (await win.webContents.capturePage()).toPNG());
      app.quit();
    });
  }
  return win;
}

ipcMain.handle("library", () => callApi(["library"]));

ipcMain.handle("record", (_event, { paper, question, correct }) =>
  callApi(["record", "--paper", paper, "--question", question, "--correct", correct ? "1" : "0"]),
);

ipcMain.handle("grade", (_event, { paper, question, reference, evidence, answer }) =>
  callApi([
    "grade",
    "--paper", paper,
    "--question", question,
    "--reference", reference,
    "--evidence", evidence || "",
    "--answer", answer,
  ]),
);

ipcMain.handle("process-inbox", (event) =>
  callApi(["process-inbox"], (line) => event.sender.send("process-log", line)),
);

ipcMain.handle("settings", () => callApi(["settings"]));

ipcMain.handle("save-settings", (_event, { model, numQuestions, guidance }) => {
  const args = ["save-settings"];
  if (model) args.push("--model", model);
  if (numQuestions) args.push("--num-questions", String(numQuestions));
  if (guidance !== undefined) args.push("--guidance", guidance);
  return callApi(args);
});

ipcMain.handle("environment", () => callApi(["environment"]));

ipcMain.handle("pull-model", (event, model) =>
  callApi(["pull-model", "--model", model], (line) => event.sender.send("pull-log", line)),
);

// Many sites render their text in the browser, so the HTML we'd fetch is an
// empty shell. We already ship Chromium: load the page, let it run, and take
// the rendered HTML. Written to a temp file so it doesn't ride on argv.
async function renderPage(url) {
  const win = new BrowserWindow({
    show: false,
    webPreferences: { javascript: true, images: false, sandbox: true, contextIsolation: true },
  });
  try {
    await win.loadURL(url, { userAgent: "Mozilla/5.0 (compatible; DoTheReading/1.0)" });
    await new Promise((done) => setTimeout(done, 1500)); // let late content settle
    const html = await win.webContents.executeJavaScript("document.documentElement.outerHTML");
    const file = path.join(os.tmpdir(), `dothereading-${Date.now()}.html`);
    fs.writeFileSync(file, html, "utf-8");
    return file;
  } finally {
    win.destroy();
  }
}

ipcMain.handle("render-url", (_event, url) => renderPage(url));

ipcMain.handle("add-text", (_event, { title, text }) => {
  const file = path.join(os.tmpdir(), `dothereading-${Date.now()}.txt`);
  fs.writeFileSync(file, text, "utf-8");
  return callApi(["add-text", "--title", title || "", "--text-file", file]);
});

ipcMain.handle("remove-paper", (_event, name) => callApi(["remove-paper", "--name", name]));

ipcMain.handle("add-url", (event, { url, htmlFile, dryRun }) => {
  const args = ["add-url", "--url", url];
  if (htmlFile) args.push("--html-file", htmlFile);
  if (dryRun) args.push("--dry-run");
  return callApi(args, (line) => event.sender.send("add-url-log", line));
});

// Offer what's on the clipboard, if it's a link we haven't seen.
ipcMain.handle("clipboard-url", () => {
  const text = (clipboard.readText() || "").trim();
  return /^https?:\/\/\S+$/i.test(text) && text.length < 500 ? text : null;
});

ipcMain.handle("paper-info", (_event, paper) => callApi(["paper-info", "--paper", paper]));

ipcMain.handle("start-ollama", () => callApi(["start-ollama"]));

ipcMain.handle("ollama-memory", (_event, action) => callApi(["ollama-memory", "--action", action]));

ipcMain.handle("schedule", (_event, { action, time }) =>
  callApi(["schedule", "--action", action, ...(time ? ["--time", time] : [])]),
);

ipcMain.handle("add-papers", (_event, paths) =>
  paths.length ? callApi(["add-papers", "--files", ...paths]) : { added: [], skipped: [] },
);

ipcMain.handle("choose-papers", async (event) => {
  const { canceled, filePaths } = await dialog.showOpenDialog(BrowserWindow.fromWebContents(event.sender), {
    title: "Add papers to the inbox",
    properties: ["openFile", "multiSelections"],
    filters: [{ name: "PDFs", extensions: ["pdf"] }],
  });
  return canceled ? { added: [], skipped: [] } : callApi(["add-papers", "--files", ...filePaths]);
});

ipcMain.handle("open-external", (_event, target) => shell.openPath(target));

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
