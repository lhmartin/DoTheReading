// Renderer: views, quiz session state, and calls into study_api.py via preload.

const $ = (id) => document.getElementById(id);

const state = {
  data: null,        // last library payload
  session: null,     // { items: [{paper, title, question}], index, results: [] }
  revealed: false,
};

// ---- helpers ------------------------------------------------------------

function toast(message, ms = 2600) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), ms);
}

function show(view) {
  for (const section of document.querySelectorAll(".view")) {
    section.classList.toggle("is-active", section.id === `view-${view}`);
  }
  for (const button of document.querySelectorAll(".rail-btn[data-view]")) {
    button.classList.toggle("is-active", button.dataset.view === view);
  }
}

function paperByStem(stem) {
  return state.data?.papers.find((p) => p.stem === stem) || null;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// ---- loading ------------------------------------------------------------

async function refresh() {
  try {
    state.data = await window.study.library();
  } catch (err) {
    toast(err.message, 8000);
    return;
  }
  renderToday();
  renderLibrary();
  renderInbox();
  renderProgress();
}

// ---- today --------------------------------------------------------------

function renderToday() {
  const { papers, suggested, stats } = state.data;
  $("today-date").textContent = new Date().toLocaleDateString(undefined, {
    weekday: "long", day: "numeric", month: "long",
  });

  const paper = paperByStem(suggested);
  if (paper) {
    $("suggested-title").textContent = paper.title;
    const { total, seen, correct } = paper.counts;
    $("suggested-meta").textContent = seen
      ? `${plural(total, "question")} · last time you got ${correct} of ${seen}`
      : `${plural(total, "question")} · not studied yet`;
    $("start-suggested").disabled = false;
  } else {
    $("suggested-title").textContent = papers.length ? "Pick a paper from the library" : "Nothing processed yet";
    $("suggested-meta").textContent = papers.length ? "" : "Drop PDFs in the inbox, then process them.";
    $("start-suggested").disabled = true;
  }
  $("shuffle").disabled = papers.length < 2;

  $("review-card").hidden = stats.review_pile === 0;
  $("review-title").textContent = `${plural(stats.review_pile, "question")} you missed`;

  const studied = papers.filter((p) => p.last_studied).slice(0, 4);
  $("recent").innerHTML = studied.length
    ? `<p class="eyebrow">Recently studied</p><div class="stack" style="margin-top:14px">${studied
        .map((p) => paperRow(p, false))
        .join("")}</div>`
    : "";
}

// ---- library ------------------------------------------------------------

function paperRow(paper, withPeek = true) {
  const { total, seen, correct, review } = paper.counts;
  const tags = [
    `<span class="tag">${plural(total, "question")}</span>`,
    seen ? `<span class="tag tag--done">${correct}/${seen} right</span>` : `<span class="tag">unstudied</span>`,
    review ? `<span class="tag tag--review">${review} to review</span>` : "",
    paper.pdf ? "" : `<span class="tag">no PDF</span>`,
  ].join("");
  return `
    <article class="paper-row" data-stem="${paper.stem}">
      <div>
        <h3>${escapeHtml(paper.title)}</h3>
        <div class="tags">${tags}</div>
      </div>
      <div class="row" style="margin:0">
        ${withPeek ? `<button class="btn btn--ghost" data-peek="${paper.stem}">Questions</button>` : ""}
        <button class="btn" data-study="${paper.stem}">Study</button>
      </div>
    </article>`;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function renderLibrary() {
  const { papers } = state.data;
  $("library-list").innerHTML = papers.length
    ? papers.map((p) => paperRow(p)).join("")
    : `<p class="meta">No processed papers yet.</p>`;
}

function togglePeek(stem) {
  const row = document.querySelector(`.paper-row[data-stem="${stem}"]`);
  const existing = row.querySelector(".questions-peek");
  if (existing) return existing.remove();
  const paper = paperByStem(stem);
  const items = paper.questions
    .map((q) => `<li>${escapeHtml(q.question)}${q.needs_review ? " <em>(missed)</em>" : ""}</li>`)
    .join("");
  row.insertAdjacentHTML("beforeend", `<div class="questions-peek"><ol>${items}</ol></div>`);
}

// ---- inbox --------------------------------------------------------------

function renderInbox() {
  const { inbox } = state.data;
  $("inbox-list").innerHTML = inbox.length
    ? inbox.map((name) => `<li>${escapeHtml(name)}</li>`).join("")
    : `<li class="empty">Nothing waiting — drag PDFs onto the window, or use Add PDFs…</li>`;
  const pip = $("inbox-pip");
  pip.hidden = inbox.length === 0;
  pip.textContent = inbox.length;
  $("run-inbox").disabled = inbox.length === 0;
}

// The pipeline and Ollama narrate themselves in log lines meant for the log
// file. Translate them into one plain sentence and a progress fraction.
function activity(id) {
  const box = $(`${id}-activity`);
  const bar = $(`${id}-bar`);
  return {
    start(main) {
      box.hidden = false;
      box.classList.remove("is-done");
      $(`${id}-main`).textContent = main;
      $(`${id}-sub`).textContent = "";
      bar.hidden = true;
      bar.firstElementChild.style.width = "0%";
    },
    update({ main, sub, fraction }) {
      if (main !== undefined) $(`${id}-main`).textContent = main;
      if (sub !== undefined) $(`${id}-sub`).textContent = sub;
      if (fraction !== undefined) {
        bar.hidden = false;
        bar.firstElementChild.style.width = `${Math.round(fraction * 100)}%`;
      }
    },
    finish(main, sub = "") {
      box.hidden = false;
      box.classList.add("is-done");
      $(`${id}-main`).textContent = main;
      $(`${id}-sub`).textContent = sub;
      bar.hidden = true;
    },
  };
}

// One pipeline log line -> what to show, or null to ignore it.
function describeRunLine(line, progress) {
  const text = line.trim();
  let match;
  if ((match = text.match(/^Found (\d+) paper/))) {
    progress.papers = Number(match[1]);
    progress.done = 0;
    return { main: `Processing ${plural(progress.papers, "paper")}…`, sub: "" };
  }
  if ((match = text.match(/^Processing (.+)\.pdf/))) {
    progress.paper = match[1];
    progress.dropped = 0;
    return { main: `Reading ${match[1]}`, sub: "extracting the text", fraction: 0 };
  }
  if ((match = text.match(/^title: (.+)$/))) {
    progress.paper = match[1];
    return { main: `Reading ${match[1]}` };
  }
  if ((match = text.match(/^section (\d+)\/(\d+)/))) {
    const [, current, total] = match.map(Number);
    // Generation is most of the work; leave room for the checking that follows.
    return { sub: `writing questions — section ${current} of ${total}`, fraction: (current / total) * 0.6 };
  }
  if (text.startsWith("ranking")) return { sub: "choosing the best questions", fraction: 0.65 };
  if (text.startsWith("verifying")) return { sub: "checking each answer against the paper", fraction: 0.75 };
  if (text.startsWith("dropped:")) {
    progress.dropped = (progress.dropped || 0) + 1;
    return { sub: `checking answers — ${plural(progress.dropped, "question")} dropped so far` };
  }
  if ((match = text.match(/^(\d+) of (\d+) questions checked passed/))) {
    return { sub: `kept ${match[1]} questions`, fraction: 0.95 };
  }
  if (text.startsWith("Done.")) {
    progress.done += 1;
    return { main: `Finished ${progress.paper || "the paper"}`, sub: "", fraction: 1 };
  }
  if (/^(Can't start:|WARNING:|ERROR)/.test(text)) {
    return { sub: text.replace(/^(Can't start:|WARNING:|ERROR)\s*/, "") };
  }
  return null; // timings, tracebacks and the like stay in the log file
}

async function runInbox() {
  // Check the model is there before starting: a run that can't work takes
  // minutes to say so otherwise.
  try {
    const env = await window.study.environment();
    if (!env.ollama.running) {
      toast("Ollama isn't running — start it, then try again", 6000);
      show("settings");
      return renderSettings();
    }
    if (!env.models.selected_installed) {
      toast(`${env.models.selected} isn't downloaded yet — get it in Settings`, 6000);
      show("settings");
      return renderSettings();
    }
  } catch (err) {
    return toast(err.message, 8000);
  }

  state.runProgress = { papers: 0, done: 0 };
  state.runActivity = activity("run");
  state.runActivity.start("Starting…");
  $("run-inbox").disabled = true;
  $("run-note").textContent = "";
  try {
    const result = await window.study.processInbox();
    if (result && result.ok === false) throw new Error(result.error || "the run stopped early");
    const { done } = state.runProgress;
    state.runActivity.finish(done ? `Done — ${plural(done, "paper")} ready to study` : "Nothing to process");
  } catch (err) {
    state.runActivity.finish("Couldn't finish the run", err.message);
  }
  state.runActivity = null;
  $("run-inbox").disabled = false;
  await refresh();
}

async function addPapers(paths) {
  if (!paths.length) return;
  try {
    const result = await window.study.addPapers(paths);
    reportAdded(result);
  } catch (err) {
    toast(err.message, 8000);
  }
}

function reportAdded(result) {
  const { added = [], skipped = [] } = result || {};
  if (added.length) {
    toast(`Added ${plural(added.length, "paper")} to the inbox`);
    show("inbox");
  } else if (skipped.length) {
    toast(`Nothing added — ${skipped[0].name}: ${skipped[0].why}`, 5000);
  }
  refresh();
}

// Drag a PDF anywhere onto the window to queue it.
let dragDepth = 0;
window.addEventListener("dragenter", (event) => {
  event.preventDefault();
  dragDepth += 1;
  $("dropzone").hidden = false;
});
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("dragleave", (event) => {
  event.preventDefault();
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) $("dropzone").hidden = true;
});
window.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  $("dropzone").hidden = true;
  const paths = [...event.dataTransfer.files].map((file) => window.study.pathForFile(file)).filter(Boolean);
  addPapers(paths);
});

// ---- progress -----------------------------------------------------------

function renderProgress() {
  const s = state.data.stats;
  const accuracy = s.answered ? Math.round((s.correct / s.answered) * 100) : 0;
  $("figures").innerHTML = [
    [s.papers_studied + " / " + s.papers, "Papers studied"],
    [s.answered, "Questions answered"],
    [accuracy + "%", "Answered right"],
    [s.review_pile, "In review pile"],
  ]
    .map(([value, label]) => `<div class="figure"><b>${value}</b><span>${label}</span></div>`)
    .join("");

  const days = s.by_day.slice(-10).reverse();
  const most = Math.max(1, ...days.map((d) => d.answered));
  $("day-card").hidden = days.length === 0;
  $("days").innerHTML = days
    .map(
      (d) => `<li>
        <span>${d.date === s.today ? "Today" : d.date}</span>
        <span class="bar"><i style="width:${(d.correct / most) * 100}%"></i></span>
        <span>${d.correct}/${d.answered}</span>
      </li>`,
    )
    .join("");
}

// ---- settings -----------------------------------------------------------

const QUALITY_LABEL = {
  best: "best questions",
  weaker: "weaker questions, checks less reliably",
  weakest: "shallowest questions, least reliable checks",
};

// Installed models plus the suggestions worth downloading, as one list.
function modelRows(models) {
  const installed = new Map(models.installed.map((m) => [m.name, m]));
  const rows = models.suggested.map((m) => ({ ...m, installed: installed.has(m.name) }));
  for (const m of models.installed) {
    if (!rows.some((row) => row.name === m.name)) {
      rows.push({ name: m.name, size: `${(m.size_bytes / 1e9).toFixed(1)} GB`, installed: true, quality: "", note: "" });
    }
  }
  return rows;
}

function renderModelPicker(models) {
  state.modelRows = modelRows(models);
  const option = (m) =>
    `<option value="${escapeHtml(m.name)}"${m.name === models.selected ? " selected" : ""}>` +
    `${escapeHtml(m.name)} — ${m.size}${m.quality ? ` · ${QUALITY_LABEL[m.quality] || m.quality}` : ""}</option>`;
  const downloaded = state.modelRows.filter((m) => m.installed);
  const available = state.modelRows.filter((m) => !m.installed);
  $("model-select").innerHTML =
    (downloaded.length ? `<optgroup label="Downloaded">${downloaded.map(option).join("")}</optgroup>` : "") +
    (available.length ? `<optgroup label="Not downloaded">${available.map(option).join("")}</optgroup>` : "");
  describeSelectedModel();
}

// One line under the dropdown, saying what this choice costs you.
function describeSelectedModel() {
  const name = $("model-select").value;
  if (!state.pullActivity) $("pull-activity").hidden = true;
  const model = (state.modelRows || []).find((m) => m.name === name);
  $("model-download").hidden = !(model && !model.installed);
  const detail = $("model-detail");
  detail.textContent = model ? (model.installed ? "" : `Not downloaded (${model.size}). `) + (model.note || "") : "";
  detail.classList.toggle("is-warning", Boolean(model && model.quality && model.quality !== "best"));
}


async function renderSettings() {
  let env;
  try {
    env = await window.study.environment();
  } catch (err) {
    return toast(err.message, 8000);
  }
  state.env = env;
  const { ollama, models, tesseract, scheduled_task: task, settings } = env;

  const checks = [
    [ollama.running, ollama.running ? `Ollama ${ollama.version} is running` : "Ollama isn't running — start it, then hit Refresh"],
    [models.selected_installed, models.selected_installed
      ? `Model ready: ${models.selected}`
      : `Model not downloaded: ${models.selected} — pick one below`],
    [tesseract, tesseract ? "Tesseract found (scanned PDFs can be OCR'd)" : "No Tesseract — scanned PDFs will be skipped"],
  ];
  const memory = env.memory_settings || { supported: false, ok: true };
  if (memory.supported) {
    checks.push([memory.ok, memory.ok
      ? "Ollama is set up to keep the model on the GPU"
      : "Ollama is missing its speed settings — the model will partly run on the CPU (~3x slower)"]);
  }
  if (task.supported) {
    checks.push([task.registered, task.registered ? "Nightly run is scheduled" : "No nightly run scheduled"]);
  }
  $("checks").innerHTML = checks
    .map(([ok, text]) => `<li class="${ok ? "is-ok" : "is-warn"}">${escapeHtml(text)}</li>`)
    .join("");
  $("ollama-row").hidden = ollama.running;
  // Offer to apply the settings, or to undo them: they make the 32B model ~3x
  // faster, but they change how Ollama loads models, so they're reversible.
  $("memory-row").hidden = !memory.supported || !ollama.running;
  $("fix-memory").hidden = memory.ok;
  $("clear-memory").hidden = !memory.ok;
  $("memory-note").textContent = memory.ok
    ? "Turn these off if Ollama's model server keeps crashing."
    : "Flash attention and an 8-bit context cache keep the 32B model on the GPU (~3x faster).";
  const blocking = !ollama.running || !models.selected_installed;
  $("settings-pip").hidden = !blocking;

  renderModelPicker(models);

  $("num-questions").value = settings.num_questions;
  $("guidance").value = settings.guidance;
  $("settings-note").textContent = "";

  $("schedule-card").hidden = !task.supported;
  $("schedule-state").textContent = task.registered
    ? "The 02:00 job is registered and will wake the machine."
    : "Nothing scheduled: papers are only processed when you press Process now.";
  $("schedule-add").hidden = task.registered;
  $("schedule-remove").hidden = !task.registered;
}

async function saveSettings() {
  const model = $("model-select").value;
  try {
    await window.study.saveSettings({
      model,
      numQuestions: Number($("num-questions").value) || undefined,
      guidance: $("guidance").value,
    });
    $("settings-note").textContent = "Saved — applies to the next run.";
    renderSettings();
  } catch (err) {
    toast(err.message, 8000);
  }
}

async function pullModel(name) {
  const button = $("model-download");
  const select = $("model-select");
  button.disabled = true;
  select.disabled = true;
  button.textContent = "Downloading…";

  state.pullActivity = activity("pull");
  state.pullActivity.start(`Downloading ${name}`);
  // Ollama stays quiet for a few seconds while it fetches the manifest, so
  // say something straight away rather than showing an empty box.
  state.pullActivity.update({ sub: "contacting Ollama…" });
  $("pull-activity").scrollIntoView({ behavior: "smooth", block: "nearest" });

  try {
    const result = await window.study.pullModel(name);
    if (result && result.ok === false) throw new Error(result.error || "the download failed");
    state.pullActivity.finish(`${name} is ready`);
    toast(`${name} downloaded`);
  } catch (err) {
    state.pullActivity.finish("Download failed", err.message);
    toast(err.message, 8000);
  }
  state.pullActivity = null;
  button.disabled = false;
  select.disabled = false;
  button.textContent = "Download";
  renderSettings();
}

async function setSchedule(action) {
  try {
    const result = await window.study.schedule({ action, time: "02:00" });
    if (!result.ok) throw new Error(result.error || "Couldn't change the schedule");
    toast(action === "add" ? "Nightly run scheduled" : "Nightly run removed");
  } catch (err) {
    toast(err.message, 8000);
  }
  renderSettings();
}

// ---- study session ------------------------------------------------------

function startSession(items, title) {
  if (!items.length) return toast("No questions there yet");
  state.session = { items, index: 0, results: [], title };
  show("study");
  $("quiz-done").hidden = true;
  $("quiz-body").hidden = false;
  renderQuestion();
}

function startPaper(stem) {
  const paper = paperByStem(stem);
  if (!paper) return;
  startSession(
    paper.questions.map((q) => ({ paper: paper.stem, title: paper.title, pdf: paper.pdf, question: q })),
    paper.title,
  );
}

function startReview() {
  const items = [];
  for (const entry of state.data.review) {
    const paper = paperByStem(entry.paper);
    const question = paper?.questions.find((q) => q.key === entry.key);
    if (question) items.push({ paper: paper.stem, title: paper.title, pdf: paper.pdf, question });
  }
  startSession(items, "Review pile");
}

function loadPdf(item, page) {
  const frame = $("pdf");
  const empty = $("reader-empty");
  if (!item.pdf) {
    frame.hidden = true;
    frame.removeAttribute("src");
    delete frame.dataset.paper;
    empty.hidden = false;
    return;
  }
  frame.hidden = false;
  empty.hidden = true;
  // Only (re)load when the paper changes, or when jumping to a page on
  // request: setting src re-fetches the file and loses the reader's place.
  if (frame.dataset.paper === item.pdf && !page) return;
  frame.dataset.paper = item.pdf;
  frame.src = `file://${item.pdf.replace(/\\/g, "/")}#page=${page || 1}`;
}

function renderQuestion() {
  const { items, index, results } = state.session;
  const item = items[index];
  const q = item.question;
  state.revealed = false;

  $("study-title").textContent = item.title;
  loadPdf(item);

  $("quiz-dots").innerHTML = items
    .map((_, i) => {
      const mark = results[i] === undefined ? (i === index ? "is-current" : "") : results[i] ? "is-right" : "is-wrong";
      return `<span class="dot ${mark}"></span>`;
    })
    .join("");

  $("q-type").textContent = `Question ${index + 1} of ${items.length}${q.type ? " · " + q.type : ""}`;
  $("q-text").textContent = q.question;
  $("a-text").textContent = q.answer;

  const hasEvidence = Boolean(q.evidence);
  $("evidence").hidden = !hasEvidence;
  if (hasEvidence) {
    $("e-text").textContent = `“${q.evidence}”`;
    $("jump").hidden = !q.page || !item.pdf;
    $("e-page").textContent = q.page || "";
  }

  const ai = $("ai-toggle").checked;
  $("answer-stage").hidden = false;
  $("typed-field").hidden = !ai;
  $("typed").value = "";
  $("reveal").hidden = ai;
  $("check").hidden = !ai;
  $("check").disabled = false;
  $("verdict").hidden = true;
  $("answer-block").hidden = true;
}

function revealAnswer() {
  state.revealed = true;
  $("answer-stage").hidden = true;
  $("answer-block").hidden = false;
}

async function checkTypedAnswer() {
  const { items, index } = state.session;
  const item = items[index];
  const answer = $("typed").value.trim();
  if (!answer) return toast("Write an answer first, or turn grading off");

  const verdict = $("verdict");
  verdict.hidden = false;
  verdict.className = "is-thinking";
  verdict.innerHTML = `<p class="eyebrow">Marking</p><p>Asking the model…</p>`;
  $("check").disabled = true;

  try {
    const result = await window.study.grade({
      paper: item.paper,
      question: item.question.question,
      reference: item.question.answer,
      evidence: item.question.evidence,
      answer,
    });
    verdict.className = `is-${result.verdict}`;
    const label = { correct: "Correct", partly: "Partly right", incorrect: "Not quite" }[result.verdict] || "Marked";
    verdict.innerHTML = `<p class="eyebrow">${label}</p><p>${escapeHtml(result.feedback)}</p>`;
    revealAnswer();
    if (result.verdict === "correct") $("mark-right").focus();
  } catch (err) {
    verdict.className = "is-thinking";
    verdict.innerHTML = `<p class="eyebrow">Couldn't grade</p><p>${escapeHtml(err.message)}</p>`;
    $("check").disabled = false;
  }
}

async function mark(correct) {
  const { items, index, results } = state.session;
  const item = items[index];
  results[index] = correct;
  try {
    await window.study.record({ paper: item.paper, question: item.question.question, correct });
  } catch (err) {
    toast(err.message, 6000);
  }

  if (index + 1 < items.length) {
    state.session.index += 1;
    renderQuestion();
  } else {
    finishSession();
  }
}

function finishSession() {
  const { items, results } = state.session;
  const right = results.filter(Boolean).length;
  $("quiz-body").hidden = true;
  $("quiz-done").hidden = false;
  $("score").textContent = `${right} / ${items.length}`;
  const missed = items.filter((_, i) => !results[i]);
  $("missed").innerHTML = missed.length
    ? `<p class="eyebrow">Back in the review pile</p>` +
      missed.map((item) => `<li>${escapeHtml(item.question.question)}</li>`).join("")
    : "";
  refresh();
}

// ---- wiring -------------------------------------------------------------

document.querySelectorAll(".rail-btn[data-view]").forEach((button) => {
  button.addEventListener("click", () => {
    show(button.dataset.view);
    if (button.dataset.view === "settings") renderSettings();
  });
});

$("refresh").addEventListener("click", () => {
  refresh();
renderSettings();
  toast("Reloaded");
});

$("start-suggested").addEventListener("click", () => startPaper(state.data.suggested));
$("start-review").addEventListener("click", startReview);
$("shuffle").addEventListener("click", () => {
  const others = state.data.papers.filter((p) => p.stem !== state.data.suggested);
  if (!others.length) return;
  state.data.suggested = others[Math.floor(Math.random() * others.length)].stem;
  renderToday();
});

document.addEventListener("click", (event) => {
  const study = event.target.closest("[data-study]");
  if (study) return startPaper(study.dataset.study);
  const peek = event.target.closest("[data-peek]");
  if (peek) return togglePeek(peek.dataset.peek);
});

$("reveal").addEventListener("click", revealAnswer);
$("check").addEventListener("click", checkTypedAnswer);
$("mark-right").addEventListener("click", () => mark(true));
$("mark-wrong").addEventListener("click", () => mark(false));
$("jump").addEventListener("click", () => {
  const item = state.session.items[state.session.index];
  loadPdf(item, item.question.page);
});
$("open-pdf").addEventListener("click", () => {
  const item = state.session?.items[state.session.index];
  if (item?.pdf) window.study.openExternal(item.pdf);
});
$("leave-study").addEventListener("click", () => {
  show("today");
  refresh();
});
$("finish").addEventListener("click", () => {
  show("today");
  state.session = null;
});

$("save-settings").addEventListener("click", saveSettings);
$("model-select").addEventListener("change", describeSelectedModel);
$("model-download").addEventListener("click", () => pullModel($("model-select").value));
$("start-ollama").addEventListener("click", async () => {
  const button = $("start-ollama");
  button.disabled = true;
  button.textContent = "Starting…";
  try {
    const result = await window.study.startOllama();
    toast(result.ok ? "Ollama is running" : result.error, result.ok ? 3000 : 8000);
  } catch (err) {
    toast(err.message, 8000);
  }
  button.disabled = false;
  button.textContent = "Start Ollama";
  renderSettings();
});

async function changeMemorySettings(action) {
  const buttons = [$("fix-memory"), $("clear-memory")];
  buttons.forEach((b) => (b.disabled = true));
  try {
    const result = await window.study.ollamaMemory(action);
    if (!result.ok) throw new Error(result.error || "couldn't change the settings");
    const applied = action === "set" ? "Speed settings applied" : "Speed settings turned off";
    toast(result.restarted ? `${applied} — Ollama restarted` : result.error, result.restarted ? 3000 : 8000);
  } catch (err) {
    toast(err.message, 8000);
  }
  buttons.forEach((b) => (b.disabled = false));
  renderSettings();
}

$("fix-memory").addEventListener("click", () => changeMemorySettings("set"));
$("clear-memory").addEventListener("click", () => changeMemorySettings("clear"));

$("schedule-add").addEventListener("click", () => setSchedule("add"));
$("schedule-remove").addEventListener("click", () => setSchedule("remove"));
window.study.onPullLog((line) => {
  if (!state.pullActivity) return;
  // Ollama says e.g. "pulling manifest" or "downloading ... — 42% of 14.4 GB".
  const percent = line.match(/(\d+)% of ([\d.]+ GB)/);
  state.pullActivity.update(
    percent
      ? { sub: `${percent[1]}% of ${percent[2]}`, fraction: Number(percent[1]) / 100 }
      : { sub: line.replace(/\s+—.*$/, "") },
  );
});

$("add-papers").addEventListener("click", async () => {
  try {
    reportAdded(await window.study.choosePapers());
  } catch (err) {
    toast(err.message, 8000);
  }
});

$("run-inbox").addEventListener("click", runInbox);
window.study.onProcessLog((line) => {
  if (!state.runActivity) return;
  const update = describeRunLine(line, state.runProgress);
  if (update) state.runActivity.update(update);
});

// Keyboard: space/enter reveals, y/n marks.
document.addEventListener("keydown", (event) => {
  if (!$("view-study").classList.contains("is-active") || $("quiz-done").hidden === false) return;
  if (event.target.tagName === "TEXTAREA") return;
  if (!state.revealed && (event.key === " " || event.key === "Enter")) {
    event.preventDefault();
    $("ai-toggle").checked ? checkTypedAnswer() : revealAnswer();
  } else if (state.revealed && (event.key === "y" || event.key === "n")) {
    mark(event.key === "y");
  }
});

refresh();
renderSettings();
