// Electron main process. All study data comes from study_api.py in the repo
// root, so the app never parses question files or history itself.
const { app, BrowserWindow, ipcMain, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

const REPO_DIR = path.join(__dirname, "..");

function pythonPath() {
  const candidates =
    process.platform === "win32"
      ? [path.join(REPO_DIR, ".venv", "Scripts", "python.exe"), "python"]
      : [path.join(REPO_DIR, ".venv", "bin", "python"), "python3"];
  return candidates.find((p) => !p.includes(path.sep) || fs.existsSync(p)) || candidates.at(-1);
}

// Runs study_api.py and resolves with the JSON object it prints last.
// onLine gets every other JSON line (the streamed log of a run).
function callApi(args, onLine) {
  return new Promise((resolve, reject) => {
    const child = spawn(pythonPath(), [path.join(REPO_DIR, "study_api.py"), ...args], {
      cwd: REPO_DIR,
      env: process.env,
    });
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
      if (last && last.error) reject(new Error(last.error));
      else if (last) resolve(last);
      else reject(new Error(stderr.trim().split("\n").at(-1) || `study_api.py exited with code ${code}`));
    });
  });
}

function createWindow() {
  const win = new BrowserWindow({
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
