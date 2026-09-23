// Renderer: views, quiz session state, and calls into study_api.py via preload.

const $ = (id) => document.getElementById(id);

const state = {
  data: null,        // last library payload
  session: null,     // { items: [{paper, title, question}], index, results: [] }
  revealed: false,
  libraryFilter: "all",
};

// Theme: "system" follows the OS; light/dark pin it. A per-machine
// preference, so localStorage is the right home for it.
function storedTheme() {
  try { return localStorage.getItem("dtr:theme") || "system"; } catch { return "system"; }
}

function applyTheme(choice) {
  if (choice === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.dataset.theme = choice;
  for (const button of document.querySelectorAll("[data-theme-choice]")) {
    button.classList.toggle("is-active", button.dataset.themeChoice === choice);
  }
}
applyTheme(storedTheme());

const LEVEL_LABELS = {
  overview: "Overview", approach: "Approach", evidence: "Evidence", critique: "Critique",
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
  // Reading gets the whole window: the sidebar steps aside.
  document.body.classList.toggle("is-studying", view === "study");
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

function shortDate(iso) {
  return new Date(iso.length === 10 ? iso + "T12:00:00" : iso)
    .toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

function icon(name) {
  return `<svg><use href="#i-${name}"/></svg>`;
}

function coverImage(paper, kind = paper.reader_kind || paper.kind) {
  if (paper.cover) return `<img src="${coverUrl(paper.cover)}" alt="" />`;
  return kind === "article"
    ? `<span class="cover-placeholder">${icon("globe")}WEB</span>`
    : `<span class="cover-placeholder">${icon("doc")}PDF</span>`;
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
  const card = $("suggested-card");
  card.classList.toggle("feature--empty", !paper);
  if (paper) {
    $("suggested-title").textContent = paper.title;
    const { total, seen, correct } = paper.counts;
    $("suggested-meta").textContent = !total
      ? "Kept for reading · no questions yet"
      : seen
        ? `${plural(total, "question")} · last time you got ${correct} of ${seen}`
        : `${plural(total, "question")} · not studied yet`;
    // The first question, as a reason to start.
    const first = paper.questions[0];
    $("suggested-teaser").hidden = !first || Boolean(seen);
    $("suggested-question").textContent = first ? first.question : "";
    $("suggested-cover").innerHTML = coverImage(paper);
    $("suggested-cover").dataset.study = paper.stem;
    $("start-suggested").textContent = total ? "Start studying" : "Start reading";
    $("start-suggested").disabled = false;
  } else {
    $("suggested-teaser").hidden = true;
    $("suggested-title").textContent = papers.length ? "Pick a paper from the library" : "Nothing to read yet";
    $("suggested-meta").textContent = papers.length
      ? ""
      : "Paste a link or drop a PDF into the Inbox. Questions are written overnight.";
    // Nothing to start, so the button goes where the next step is.
    $("start-suggested").textContent = papers.length ? "Open the library" : "Add a paper";
    $("start-suggested").disabled = false;
  }
  $("shuffle").disabled = papers.length < 2;

  $("review-card").hidden = stats.review_pile === 0;
  $("review-count").textContent = stats.review_pile;
  $("review-title").textContent = `${stats.review_pile === 1 ? "question" : "questions"} you missed last time`;

  // The last seven days, from the same data as the reading grid.
  const weekAgo = new Date();
  weekAgo.setDate(weekAgo.getDate() - 6);
  const since = weekAgo.toISOString().slice(0, 10);
  const week = (stats.days || []).filter((day) => day.date >= since);
  const read = week.reduce((n, day) => n + day.read.length, 0);
  const answered = week.reduce((n, day) => n + day.answered, 0);
  $("week-count").textContent = read;
  $("week-meta").textContent = `${read === 1 ? "paper" : "papers"} read · ${plural(answered, "question")} answered`;

  const studied = papers.filter((p) => p.last_studied).slice(0, 4);
  $("recent").innerHTML = studied.length
    ? `<div class="section-head"><h2>Recently studied</h2></div>
       <div class="list">${studied.map(paperRow).join("")}</div>`
    : "";
}

// ---- library ------------------------------------------------------------

function paperRow(paper) {
  const { total, seen, correct, review } = paper.counts;
  const tags = [
    total ? `<span class="tag">${plural(total, "question")}</span>` : `<span class="tag">for reading</span>`,
    seen ? `<span class="tag tag--done">${correct}/${seen} right</span>` : "",
    review ? `<span class="tag tag--review">${review} to review</span>` : "",
  ].join("");
  const when = paper.last_studied ? `Studied ${shortDate(paper.last_studied)}` : "";
  return `
    <article class="list-row" data-stem="${paper.stem}">
      <div class="thumb">${coverImage(paper)}</div>
      <div>
        <h3>${escapeHtml(paper.title)}</h3>
        <div class="tags">${tags}<span class="hint">${when}</span></div>
      </div>
      <div class="actions"><button class="btn btn--sm" data-study="${paper.stem}">Open</button></div>
    </article>`;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function coverUrl(path) {
  return `file://${path.replace(/\\/g, "/")}`;
}

function shelfCard(paper) {
  const { total, seen, correct, review } = paper.counts;
  const added = new Date(paper.added).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  const facts = [paper.pages ? `${paper.pages} pages` : "", `added ${added}`].filter(Boolean);
  const tags = [
    paper.read_at ? `<span class="tag tag--read">Read</span>` : "",
    total ? `<span class="tag">${plural(total, "question")}</span>` : `<span class="tag">For reading</span>`,
    total && seen ? `<span class="tag tag--done">${correct}/${seen} right</span>` : "",
    review ? `<span class="tag tag--review">${review} to review</span>` : "",
    paper.notes ? `<span class="tag tag--notes">Notes</span>` : "",
  ].join("");
  return `
    <article class="shelf-card" data-stem="${paper.stem}">
      <div class="cover" data-cover-for="${paper.stem}" data-study="${paper.stem}" title="Open">
        ${coverImage(paper)}
      </div>
      <div class="shelf-body">
        <h3 title="${escapeHtml(paper.title)}">${escapeHtml(paper.title)}</h3>
        <p class="shelf-facts">${facts.join(" · ")}</p>
        <div class="tags">${tags}</div>
        <div class="row">
          <button class="btn btn--sm btn--primary" data-study="${paper.stem}">${total ? "Study" : "Read"}</button>
          ${total ? `<button class="btn btn--sm btn--ghost" data-peek="${paper.stem}">Questions</button>` : ""}
        </div>
      </div>
    </article>`;
}

function libraryMatches(paper) {
  const query = $("library-search").value.trim().toLowerCase();
  if (query && !paper.title.toLowerCase().includes(query)) return false;
  if (state.libraryFilter === "unread") return !paper.read_at;
  if (state.libraryFilter === "review") return paper.counts.review > 0;
  return true;
}

function renderLibrary() {
  const { papers } = state.data;
  const shown = papers.filter(libraryMatches);
  const read = papers.filter((p) => p.read_at).length;
  $("library-lede").textContent = papers.length
    ? `${plural(papers.length, "paper")} · ${read} read`
    : "Everything you've added.";
  $("library-list").innerHTML = !papers.length
    ? `<div class="empty-state" style="grid-column:1/-1"><h3>Your library is empty</h3>
         <p>Drop a PDF on this window or paste a link in the Inbox.</p></div>`
    : shown.length
      ? shown.map(shelfCard).join("")
      : `<div class="empty-state" style="grid-column:1/-1"><p>No papers match.</p></div>`;
  loadMissingCovers(shown);
  renderQueue();
}

// A queued paper, as a row: the same in the Library and the Inbox.
function queueRow(paper) {
  const size = Math.max(0.1, Math.round(paper.size_bytes / 1e5) / 10);
  return `
    <article class="list-row">
      <div class="thumb">${paper.cover ? `<img src="${coverUrl(paper.cover)}" alt="" />` : icon(paper.kind === "pdf" ? "doc" : "globe")}</div>
      <div>
        <h3>${escapeHtml(paper.title)}</h3>
        <div class="tags"><span class="tag">${paper.kind === "pdf" ? "PDF" : "Web article"}</span><span class="tag">${size} MB</span></div>
      </div>
      <div class="actions">
        <button class="btn btn--sm" data-process="${escapeHtml(paper.name)}" title="Write questions for this one now">Write questions</button>
        <button class="btn btn--sm" data-shelve="${escapeHtml(paper.name)}" title="Keep it to read, without questions">Just read it</button>
        <button class="btn btn--sm btn--ghost btn--icon btn--remove" data-remove="${escapeHtml(paper.name)}" title="Remove from the queue">${icon("x")}</button>
      </div>
    </article>`;
}

function renderQueue() {
  const queued = state.data.inbox || [];
  $("library-queue").innerHTML = queued.length
    ? `<div class="section-head"><h2>Not processed yet</h2>
         <p class="hint">Questions are written at 02:00, or when you ask for them.</p></div>
       <div class="list list--dashed">${queued.map(queueRow).join("")}</div>`
    : "";
}

// Covers are rendered on demand, one at a time, so a big library doesn't
// stall the view while PDFs are rasterised.
async function loadMissingCovers(papers) {
  for (const paper of papers) {
    if (paper.cover || !paper.pdf) continue;
    try {
      const { info } = await window.study.paperInfo(paper.stem);
      paper.cover = info.cover;
      paper.pages = info.pages;
      const slot = document.querySelector(`[data-cover-for="${paper.stem}"]`);
      if (slot && info.cover) slot.innerHTML = `<img src="${coverUrl(info.cover)}" alt="" />`;
      const facts = document.querySelector(`.shelf-card[data-stem="${paper.stem}"] .shelf-facts`);
      if (facts && info.pages && !facts.textContent.includes("pages")) {
        facts.textContent = `${info.pages} pages · ${facts.textContent}`;
      }
    } catch {
      // a cover is a nicety; a failure here shouldn't disturb the library
    }
  }
}

function togglePeek(stem) {
  const row = document.querySelector(`.shelf-card[data-stem="${stem}"]`);
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
    ? inbox.map(queueRow).join("")
    : `<p class="list-empty">Nothing waiting. Paste a link above or drop PDFs on the window.</p>`;
  const pip = $("inbox-pip");
  pip.hidden = inbox.length === 0;
  pip.textContent = inbox.length;
  // Each row has its own button; the bulk one only earns its place with several.
  $("run-inbox").hidden = inbox.length < 2;
  $("run-inbox").textContent = `Write questions for all ${inbox.length}`;
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

function hideAddPanels() {
  $("preview").hidden = true;
  $("paste-box").hidden = true;
}

// Extract first and show what we got: a page can look fine and still yield
// navigation crumbs, so the reader confirms before it joins the queue.
async function previewUrl(url) {
  if (!url) return;
  hideAddPanels();
  state.urlActivity = activity("url");
  state.urlActivity.start("Reading the page…");
  $("add-url").disabled = true;

  try {
    let result = await window.study.addUrl({ url, dryRun: true });
    if (!result.ok && result.needs_render) {
      // A JavaScript-rendered page: run it in a hidden window and retry.
      state.urlActivity.update({ sub: "the page needs a browser — rendering it…" });
      const htmlFile = await window.study.renderUrl(url);
      result = await window.study.addUrl({ url, htmlFile, dryRun: true });
      state.pendingHtmlFile = htmlFile;
    }
    if (!result.ok) throw new Error(result.error || "couldn't read that page");
    state.pendingUrl = url;
    showPreview(result);
    state.urlActivity.finish("Read the page", "Check the preview below.");
  } catch (err) {
    state.urlActivity.finish("Couldn't read that page", `${err.message} — you can paste the text instead.`);
    $("paste-box").hidden = false;
  }
  state.urlActivity = null;
  $("add-url").disabled = false;
}

function showPreview(result) {
  $("preview").hidden = false;
  $("preview-title").textContent = result.title;
  const facts = [
    result.source === "pdf" ? "the PDF will be used" : `${result.characters.toLocaleString()} characters`,
    result.headings && result.headings.length ? `${plural(result.headings.length, "section")}` : "",
    result.note || "",
  ].filter(Boolean);
  $("preview-meta").textContent = facts.join(" · ");
  $("preview-text").textContent = result.preview || "";
}

async function confirmAdd() {
  $("preview").hidden = true;
  state.urlActivity = activity("url");
  state.urlActivity.start("Adding it…");
  try {
    const result = await window.study.addUrl({ url: state.pendingUrl, htmlFile: state.pendingHtmlFile });
    if (!result.ok) throw new Error(result.error || "couldn't add that link");
    const detail = result.note || `${result.characters.toLocaleString()} characters` +
      `${result.pdf ? ", with the PDF to read" : ""}. Press Process now when ready.`;
    state.urlActivity.finish(`Added ${result.title}`, detail);
    $("url-input").value = "";
    state.dismissedClipUrl = state.pendingUrl;
    $("clip-offer").hidden = true;
  } catch (err) {
    state.urlActivity.finish("Couldn't add it", err.message);
  }
  state.pendingUrl = state.pendingHtmlFile = null;
  state.urlActivity = null;
  refresh();
}

async function addPastedText() {
  const text = $("paste-text").value.trim();
  const title = $("paste-title").value.trim();
  if (text.length < 500) return toast("Paste the whole article — that's too short to work from", 5000);
  try {
    const result = await window.study.addText({ title, text });
    if (!result.ok) throw new Error(result.error);
    hideAddPanels();
    $("paste-text").value = "";
    $("paste-title").value = "";
    $("url-input").value = "";
    toast(`Added ${result.title}`);
  } catch (err) {
    toast(err.message, 8000);
  }
  refresh();
}

async function writeQuestionsNow() {
  const paper = state.session?.items[0]?.paper;
  if (!paper) return;
  const count = Number($("new-count").value) || 12;
  state.runProgress = { papers: 0, done: 0 };
  state.runActivity = activity("write");
  state.runActivity.start("Reading the paper…");
  $("write-questions").disabled = true;
  try {
    const result = await window.study.writeQuestions({ paper, count });
    if (!result.ok) throw new Error(result.error || "couldn't write questions");
    state.runActivity.finish(`${plural(count, "question")} written`, "Reopen the paper to start.");
    await refresh();
    startPaper(paper);
  } catch (err) {
    state.runActivity.finish("Couldn't write questions", err.message);
  }
  state.runActivity = null;
  $("write-questions").disabled = false;
}

async function toggleRead() {
  const paper = state.session?.items[0]?.paper;
  if (!paper) return;
  const wasRead = Boolean(paperByStem(paper)?.read_at);
  try {
    const result = await window.study.markRead({ paper, unread: wasRead });
    if (!result.ok) throw new Error(result.error);
    toast(wasRead ? "Marked as unread" : "Marked as read");
    await refresh();
    showReadState(paper);
  } catch (err) {
    toast(err.message, 8000);
  }
}

function showReadState(paper) {
  const readAt = paperByStem(paper)?.read_at;
  const button = $("mark-read");
  button.textContent = readAt ? "Read ✓" : "Mark as read";
  button.classList.toggle("btn--read", Boolean(readAt));
  button.title = readAt
    ? `Read on ${new Date(readAt).toLocaleDateString()} — click to undo`
    : "Mark this paper as read";
}

function showTab(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("is-active", tab.dataset.tab === name);
  }
  $("tab-questions").hidden = name !== "questions";
  $("tab-notes").hidden = name !== "notes";
  if (name === "notes") $("notes-text").focus();
}

// Notes live in the paper's question file, so they're there next time.
async function loadNotes(paper) {
  state.notesPaper = paper;
  const box = $("notes-text");
  box.value = "";
  $("notes-state").textContent = "";
  try {
    const result = await window.study.notes(paper);
    if (result.ok && state.notesPaper === paper) {
      box.value = result.notes;
      $("notes-dot").hidden = !result.notes.trim();
    }
  } catch {
    // no question file yet; the panel just stays empty
  }
}

function queueNotesSave() {
  clearTimeout(state.notesTimer);
  $("notes-state").textContent = "";
  state.notesTimer = setTimeout(saveNotes, 1200);
}

async function saveNotes() {
  const paper = state.notesPaper;
  if (!paper) return;
  const text = $("notes-text").value;
  try {
    const result = await window.study.saveNotes({ paper, text });
    if (!result.ok) throw new Error(result.error);
    $("notes-state").textContent = "· saved";
    $("notes-dot").hidden = !text.trim();
  } catch (err) {
    $("notes-state").textContent = `· not saved (${err.message})`;
  }
}

async function shelveWithoutQuestions(name) {
  try {
    const result = await window.study.shelve(name);
    if (!result.ok) throw new Error(result.error);
    toast(`${result.title} is in your library to read`);
  } catch (err) {
    toast(err.message, 8000);
  }
  refresh();
}

async function removeFromQueue(name) {
  try {
    const result = await window.study.removePaper(name);
    if (!result.ok) throw new Error(result.error);
    toast(`Removed ${name}`);
  } catch (err) {
    toast(err.message, 8000);
  }
  refresh();
}

// If there's a link on the clipboard, offer it rather than making them paste.
async function offerClipboardUrl() {
  try {
    const url = await window.study.clipboardUrl();
    const known = !url || url === state.dismissedClipUrl || url === state.lastOfferedUrl;
    $("clip-offer").hidden = Boolean(known);
    if (!known) {
      state.lastOfferedUrl = url;
      $("clip-url").textContent = url;
    }
  } catch {
    $("clip-offer").hidden = true;
  }
}

async function runInbox(only) {
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

  if (only && only.length) show("inbox");
  state.runProgress = { papers: 0, done: 0 };
  state.runActivity = activity("run");
  state.runActivity.start("Starting…");
  $("run-inbox").disabled = true;
  try {
    const result = await window.study.processInbox(only);
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

// A calendar heatmap of what you read. Intensity is papers read that day,
// with questions answered as a lesser signal, so a day of revision still
// shows. The tooltip names the papers, which is the part worth having.
function heatLevel(day) {
  const weight = (day.read?.length || 0) * 2 + (day.answered ? 1 : 0);
  if (!weight) return 0;
  return Math.min(4, weight <= 1 ? 1 : weight <= 3 ? 2 : weight <= 6 ? 3 : 4);
}

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function renderHeatmap(days) {
  const byDate = new Map(days.map((day) => [day.date, day]));
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - 181);
  start.setDate(start.getDate() - start.getDay()); // columns are weeks, starting Sunday

  const cells = [];
  const months = [];
  let column = 0;
  let lastMonth = null;
  for (const day = new Date(start); day <= today; day.setDate(day.getDate() + 1)) {
    const date = day.toISOString().slice(0, 10);
    const entry = byDate.get(date);
    cells.push(`<span class="cell level-${entry ? heatLevel(entry) : 0}" data-date="${date}"></span>`);
    if (day.getDay() === 0) {
      column += 1;
      // Label a column when its week opens a new month, as GitHub does.
      if (day.getMonth() !== lastMonth && day.getDate() <= 7) {
        lastMonth = day.getMonth();
        months.push(`<span style="grid-column:${column}">${MONTH_NAMES[lastMonth]}</span>`);
      }
    }
  }
  $("heatmap").innerHTML = cells.join("");
  $("heatmap-months").innerHTML = months.join("");
}

function heatTooltip(cell) {
  const date = cell.dataset.date;
  const day = (state.data?.stats?.days || []).find((d) => d.date === date);
  const when = new Date(date + "T12:00:00").toLocaleDateString(undefined,
    { weekday: "long", day: "numeric", month: "long" });
  const lines = [`<b>${when}</b>`];
  if (day && day.read.length) {
    lines.push(`${plural(day.read.length, "paper")} read:` +
      `<ul>${day.read.map((title) => `<li>${escapeHtml(title)}</li>`).join("")}</ul>`);
  }
  if (day && day.answered) lines.push(`${day.correct}/${day.answered} questions right`);
  if (!day || (!day.read.length && !day.answered)) lines.push("Nothing that day");

  const tip = document.createElement("div");
  tip.className = "heat-tip";
  tip.innerHTML = lines.map((line) => `<div>${line}</div>`).join("");
  document.body.appendChild(tip);
  const box = cell.getBoundingClientRect();
  tip.style.left = `${Math.min(box.left, window.innerWidth - tip.offsetWidth - 12)}px`;
  tip.style.top = `${Math.max(8, box.top - tip.offsetHeight - 8)}px`;
  state.heatTip = tip;
}

function hideHeatTooltip() {
  state.heatTip?.remove();
  state.heatTip = null;
}

function renderProgress() {
  const s = state.data.stats;
  const accuracy = s.answered ? Math.round((s.correct / s.answered) * 100) : 0;
  $("figures").innerHTML = [
    [`${s.papers_read}<small> / ${s.papers}</small>`, "papers read"],
    [s.papers_studied, "papers quizzed"],
    [s.answered, "questions answered"],
    [s.answered ? `${accuracy}%` : "–", "answered right"],
    [s.review_pile, "in the review pile"],
  ]
    .map(([value, label]) => `<div class="figure"><b>${value}</b><span>${label}</span></div>`)
    .join("");

  renderHeatmap(s.days || []);

  const sticking = s.sticking_points || [];
  $("sticking-card").hidden = sticking.length === 0;
  $("sticking").innerHTML = sticking
    .map((item) => `<li>
      <span class="q">${escapeHtml(item.question)}<span class="where">${escapeHtml(item.paper)}</span></span>
      <span class="misses">missed ${item.misses}×</span>
    </li>`)
    .join("");
  // Each bar is that day's questions, with the share you got right filled in,
  // so a bad day reads as a long pale bar rather than an empty one.
  const days = s.by_day.slice(-10).reverse();
  const most = Math.max(1, ...days.map((d) => d.answered));
  $("day-card").hidden = days.length === 0;
  $("days").innerHTML = days
    .map(
      (d) => `<li title="${d.correct} of ${d.answered} right">
        <span>${d.date === s.today ? "Today" : shortDate(d.date)}</span>
        <span class="bar"><i class="answered" style="width:${(d.answered / most) * 100}%"></i><i class="right" style="width:${(d.correct / most) * 100}%"></i></span>
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
  loadNotes(items[0].paper);
  showReadState(items[0].paper);
  $("no-questions").hidden = true;
  $("quiz-progress").hidden = false;
  showTab("questions");
  state.session = { items, index: 0, results: [], title };
  show("study");
  $("quiz-done").hidden = true;
  $("quiz-body").hidden = false;
  renderQuestion();
}

function startPaper(stem) {
  const paper = paperByStem(stem);
  if (!paper) return;
  if (!paper.questions.length) {
    // Added for reading only: no quiz, but the paper and its notes open.
    state.session = { items: [{ paper: paper.stem, title: paper.title, reader: paper.reader,
                               readerKind: paper.reader_kind, question: null }],
                      index: 0, results: [], title: paper.title };
    show("study");
    $("quiz-body").hidden = true;
    $("quiz-done").hidden = true;
    $("no-questions").hidden = false;  // say so, rather than showing a blank panel
    $("quiz-progress").hidden = true;
    $("study-title").textContent = paper.title;
    loadPdf(state.session.items[0]);
    showReadState(paper.stem);
    showTab("questions");
    return loadNotes(paper.stem);
  }
  startSession(
    paper.questions.map((q) => ({ paper: paper.stem, title: paper.title, reader: paper.reader,
                                  readerKind: paper.reader_kind, question: q })),
    paper.title,
  );
}

function startReview() {
  const items = [];
  for (const entry of state.data.review) {
    const paper = paperByStem(entry.paper);
    const question = paper?.questions.find((q) => q.key === entry.key);
    if (question) items.push({ paper: paper.stem, title: paper.title, reader: paper.reader,
                               readerKind: paper.reader_kind, question });
  }
  startSession(items, "Review pile");
}

// The reader is a module (reader.mjs) and may still be loading on the
// first click; wait for it rather than dropping the request.
function getReader() {
  if (window.reader) return Promise.resolve(window.reader);
  return new Promise((resolve) => window.addEventListener("reader-ready", () => resolve(window.reader), { once: true }));
}

async function loadPdf(item) {
  (await getReader()).open(item.reader, item.readerKind === "pdf" ? "pdf" : "article");
}

async function showEvidence() {
  const item = state.session.items[state.session.index];
  const q = item.question;
  const button = $("jump");
  button.disabled = true;
  const found = await (await getReader()).showQuote(q.evidence, q.page);
  button.disabled = false;
  if (!found) toast(q.page ? `Couldn't find the exact words, so here's page ${q.page}` : "Couldn't find that passage in the paper", 4000);
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

  $("quiz-count").textContent = `${index + 1} of ${items.length}`;
  const level = LEVEL_LABELS[q.type] || (q.type ? q.type[0].toUpperCase() + q.type.slice(1) : "Question");
  $("q-type").dataset.level = q.type || "";
  $("q-type").innerHTML = `<i></i>${escapeHtml(level)}${state.session.title === "Review pile" ? ` · <span>${escapeHtml(item.title)}</span>` : ""}`;
  $("q-text").textContent = q.question;
  $("a-text").textContent = q.answer;

  const hasEvidence = Boolean(q.evidence);
  $("evidence").hidden = !hasEvidence;
  if (hasEvidence) {
    $("e-text").textContent = `“${q.evidence}”`;
    $("jump").hidden = !item.reader;
    $("e-page").textContent = q.page && item.readerKind === "pdf" ? ` · p. ${q.page}` : "";
    // Long quotes are clamped; offer the rest only when there is more.
    $("evidence").classList.add("is-clamped");
    $("evidence-more").hidden = q.evidence.length < 360;
  }

  // The answer box is always there — writing it down is the point. The
  // checkbox only decides whether the model marks it or you do.
  const ai = $("ai-toggle").checked;
  $("answer-stage").hidden = false;
  $("typed").value = "";
  $("reveal").hidden = ai;
  $("check").hidden = !ai;
  $("check").disabled = false;
  $("verdict").hidden = true;
  $("answer-block").hidden = true;
}

function revealAnswer() {
  state.revealed = true;
  // Keep what they wrote in view beside the real answer.
  const typed = $("typed").value.trim();
  $("your-answer").hidden = !typed;
  $("your-answer").innerHTML = typed ? `<span class="eyebrow">You wrote</span>${escapeHtml(typed)}` : "";
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
  $("score-note").textContent = right === items.length
    ? "Every one. Nothing goes to the review pile."
    : `${plural(items.length - right, "question")} will come back in your review pile.`;
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
offerClipboardUrl();
  toast("Reloaded");
});

$("start-suggested").addEventListener("click", () => {
  if (state.data?.suggested) return startPaper(state.data.suggested);
  show(state.data?.papers.length ? "library" : "inbox");
});
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
  const remove = event.target.closest("[data-remove]");
  if (remove) return removeFromQueue(remove.dataset.remove);
  const process = event.target.closest("[data-process]");
  if (process) return runInbox([process.dataset.process]);
  const shelve = event.target.closest("[data-shelve]");
  if (shelve) return shelveWithoutQuestions(shelve.dataset.shelve);
});

$("ai-toggle").addEventListener("change", () => {
  const ai = $("ai-toggle").checked;
  $("reveal").hidden = ai;
  $("check").hidden = !ai;
});

$("reveal").addEventListener("click", revealAnswer);
$("check").addEventListener("click", checkTypedAnswer);
$("mark-right").addEventListener("click", () => mark(true));
$("mark-wrong").addEventListener("click", () => mark(false));
$("jump").addEventListener("click", showEvidence);
$("evidence-more").addEventListener("click", () => {
  $("evidence").classList.remove("is-clamped");
  $("evidence-more").hidden = true;
});
$("open-pdf").addEventListener("click", () => {
  const item = state.session?.items[state.session.index];
  if (item?.reader) window.study.openExternal(item.reader);
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

document.querySelectorAll("[data-theme-choice]").forEach((button) => {
  button.addEventListener("click", () => {
    const choice = button.dataset.themeChoice;
    try { localStorage.setItem("dtr:theme", choice); } catch { /* still applies for this session */ }
    applyTheme(choice);
  });
});

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

$("add-url").addEventListener("click", () => previewUrl($("url-input").value.trim()));
$("url-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter") previewUrl($("url-input").value.trim());
});
$("preview-add").addEventListener("click", confirmAdd);
$("preview-cancel").addEventListener("click", hideAddPanels);
$("preview-paste").addEventListener("click", () => {
  $("preview").hidden = true;
  $("paste-box").hidden = false;
  $("paste-title").value = $("preview-title").textContent;
  $("paste-text").focus();
});
$("paste-add").addEventListener("click", addPastedText);
$("paste-cancel").addEventListener("click", hideAddPanels);
$("clip-add").addEventListener("click", () => {
  const url = $("clip-url").textContent;
  $("clip-offer").hidden = true;
  show("inbox");
  $("url-input").value = url;
  previewUrl(url);
});
$("clip-dismiss").addEventListener("click", () => {
  state.dismissedClipUrl = $("clip-url").textContent;
  $("clip-offer").hidden = true;
});
window.study.onAddUrlLog((line) => {
  if (state.urlActivity) state.urlActivity.update({ sub: line });
});
window.addEventListener("focus", offerClipboardUrl);

$("add-papers").addEventListener("click", async () => {
  try {
    reportAdded(await window.study.choosePapers());
  } catch (err) {
    toast(err.message, 8000);
  }
});

$("run-inbox").addEventListener("click", () => runInbox());
$("library-search").addEventListener("input", renderLibrary);
document.querySelectorAll("[data-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    state.libraryFilter = button.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach((b) => b.classList.toggle("is-active", b === button));
    renderLibrary();
  });
});

// The divider between paper and questions can be dragged; the width sticks.
(function splitter() {
  const handle = $("splitter");
  const study = $("view-study");
  const clamp = (width) => Math.max(340, Math.min(width, window.innerWidth - 420));
  try {
    const saved = Number(localStorage.getItem("dtr:quiz-width"));
    if (saved) study.style.setProperty("--quiz-width", `${clamp(saved)}px`);
  } catch { /* default width */ }
  handle.addEventListener("pointerdown", (event) => {
    handle.setPointerCapture(event.pointerId);
    handle.classList.add("is-dragging");
    document.body.classList.add("is-resizing");
  });
  handle.addEventListener("pointermove", (event) => {
    if (!handle.classList.contains("is-dragging")) return;
    study.style.setProperty("--quiz-width", `${clamp(window.innerWidth - event.clientX)}px`);
  });
  handle.addEventListener("pointerup", () => {
    handle.classList.remove("is-dragging");
    document.body.classList.remove("is-resizing");
    try {
      localStorage.setItem("dtr:quiz-width", parseInt(study.style.getPropertyValue("--quiz-width"), 10));
    } catch { /* a nicety */ }
  });
  handle.addEventListener("dblclick", () => {
    study.style.removeProperty("--quiz-width");
    try { localStorage.removeItem("dtr:quiz-width"); } catch { /* a nicety */ }
  });
})();
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => showTab(tab.dataset.tab));
});
$("review-from-progress").addEventListener("click", startReview);
$("write-questions").addEventListener("click", writeQuestionsNow);
$("mark-read").addEventListener("click", toggleRead);
$("heatmap").addEventListener("mouseover", (event) => {
  const cell = event.target.closest(".cell");
  if (cell) {
    hideHeatTooltip();
    heatTooltip(cell);
  }
});
$("heatmap").addEventListener("mouseout", hideHeatTooltip);
$("notes-text").addEventListener("input", queueNotesSave);
$("notes-text").addEventListener("blur", saveNotes);
window.study.onProcessLog((line) => {
  if (!state.runActivity) return;
  const update = describeRunLine(line, state.runProgress);
  if (update) state.runActivity.update(update);
});

// Keyboard: space/enter reveals, y/n marks, Ctrl+F finds in the paper,
// Esc leaves.
document.addEventListener("keydown", (event) => {
  if (!$("view-study").classList.contains("is-active")) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
    event.preventDefault();
    return window.reader?.focusFind();
  }
  if (["TEXTAREA", "INPUT", "SELECT"].includes(event.target.tagName)) return;
  if (event.key === "Escape") return $("leave-study").click();
  if (!$("quiz-done").hidden || $("tab-questions").hidden) return;
  if (!state.session?.items[state.session.index]?.question) return;
  if (!state.revealed && (event.key === " " || event.key === "Enter")) {
    event.preventDefault();
    $("ai-toggle").checked ? checkTypedAnswer() : revealAnswer();
  } else if (state.revealed && (event.key === "y" || event.key === "n")) {
    mark(event.key === "y");
  }
});

refresh();
offerClipboardUrl();
