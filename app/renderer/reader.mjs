// The reading pane: PDFs through pdf.js, saved web articles as a page of
// their own. Both can find a quoted passage and highlight it, which is what
// "Show in the paper" needs — a page number alone still leaves you hunting.
//
// A module, so it loads after app.js; app.js reaches it through window.reader.

import * as pdfjs from "../node_modules/pdfjs-dist/build/pdf.min.mjs";
import {
  EventBus, PDFLinkService, PDFFindController, PDFViewer,
} from "../node_modules/pdfjs-dist/web/pdf_viewer.mjs";

pdfjs.GlobalWorkerOptions.workerSrc = "../node_modules/pdfjs-dist/build/pdf.worker.min.mjs";

const $ = (id) => document.getElementById(id);
const FIND_NOT_FOUND = 1;   // pdf.js FindState
const FIND_PENDING = 3;
const MIN_SCALE = 0.4;
const MAX_SCALE = 4;

const bus = new EventBus();
const links = new PDFLinkService({ eventBus: bus });
const finder = new PDFFindController({ eventBus: bus, linkService: links });
const viewer = new PDFViewer({
  container: $("pdf-scroll"),
  viewer: $("pdf-viewer"),
  eventBus: bus,
  linkService: links,
  findController: finder,
  removePageBorders: true,
});
links.setViewer(viewer);
// Links inside the paper (DOIs, code) open in the browser, not in the pane.
links.externalLinkTarget = 2; // LinkTarget.BLANK; main.js routes new windows out

let current = null;       // { path, kind, doc? }
let loading = null;
let fitting = true;       // "Fit" stays on until you zoom by hand
let pageWidth = 0;        // first page's width at 100%, in CSS pixels
const GUTTER = 56;        // room either side of the page when fitting

function show(kind) {
  $("pdf-scroll").hidden = kind !== "pdf";
  $("article-scroll").hidden = kind !== "article";
  $("reader-tools").hidden = kind !== "pdf";
  $("reader-empty").hidden = kind !== "none";
}

// C:\Papers\a#1.pdf -> file:///C:/Papers/a%231.pdf; each part is escaped so a
// "#" or "?" in a filename isn't read as the start of a fragment or query.
function fileUrl(path) {
  const parts = path.replace(/\\/g, "/").split("/").map(encodeURIComponent);
  const joined = parts.join("/").replace(/^([A-Za-z])%3A/, "$1:");
  return `file://${joined.startsWith("/") ? "" : "/"}${joined}`;
}

// Where each paper was left, so reopening it doesn't start from page one.
function savedPage(path) {
  try { return Number(localStorage.getItem(`dtr:page:${path}`)) || 1; } catch { return 1; }
}
function savePage(path, page) {
  try { localStorage.setItem(`dtr:page:${path}`, String(page)); } catch { /* a nicety */ }
}

async function open(path, kind) {
  if (!path) {
    current = null;
    show("none");
    return;
  }
  if (current && current.path === path) return;
  closeFind();
  const previous = current;
  current = { path, kind };
  if (previous?.doc) previous.doc.destroy();

  if (kind === "pdf") {
    show("pdf");
    $("reader-loading").hidden = false;
    const task = pdfjs.getDocument({
      url: fileUrl(path),
      cMapUrl: "../node_modules/pdfjs-dist/cmaps/",
      cMapPacked: true,
      standardFontDataUrl: "../node_modules/pdfjs-dist/standard_fonts/",
    });
    loading = task.promise;
    try {
      const doc = await task.promise;
      if (current?.path !== path) return doc.destroy();
      current.doc = doc;
      viewer.setDocument(doc);
      links.setDocument(doc);
      $("page-total").textContent = `of ${doc.numPages}`;
    } catch (err) {
      show("none");
      $("reader-empty").textContent = `Couldn't open this PDF: ${err.message}`;
    } finally {
      $("reader-loading").hidden = true;
    }
  } else {
    show("article");
    await openArticle(path);
  }
}

// pdf.js's own "page-width" runs the page to the pane's edges; a reading
// margin either side is easier on the eye.
function fit() {
  if (!pageWidth) return;
  const width = $("pdf-scroll").clientWidth - GUTTER;
  viewer.currentScale = Math.max(MIN_SCALE, Math.min(2.2, width / pageWidth));
}

bus.on("pagesinit", async () => {
  const first = await current.doc.getPage(1);
  pageWidth = first.getViewport({ scale: 1 }).width * (96 / 72);
  fitting = true;
  fit();
  if (current) viewer.currentPageNumber = Math.min(savedPage(current.path), viewer.pagesCount);
  $("page-input").value = viewer.currentPageNumber;
});

bus.on("pagechanging", ({ pageNumber }) => {
  $("page-input").value = pageNumber;
  if (current) savePage(current.path, pageNumber);
});

bus.on("scalechanging", ({ scale }) => {
  $("zoom-label").textContent = `${Math.round(scale * 100)}%`;
});

// Keep fitting when the pane is resized or the splitter moves.
new ResizeObserver(() => {
  if (current?.doc && fitting) fit();
}).observe($("pdf-scroll"));

// ---- saved web articles ---------------------------------------------------

// The saved file is HTML we wrote ourselves (article.as_reader_html), but it
// is rebuilt from text here rather than injected, so nothing in it runs.
async function openArticle(path) {
  const box = $("article");
  box.replaceChildren();
  let html;
  try {
    html = await (await fetch(fileUrl(path))).text();
  } catch (err) {
    show("none");
    $("reader-empty").textContent = `Couldn't open this article: ${err.message}`;
    return;
  }
  const doc = new DOMParser().parseFromString(html, "text/html");
  for (const node of doc.body.querySelectorAll("h1, h2, h3, p")) {
    const link = node.tagName === "P" && node.querySelector("a[href^='http']");
    if (link && node.textContent.trim() === link.textContent.trim()) {
      const p = document.createElement("p");
      p.className = "source";
      const a = document.createElement("a");
      a.href = link.getAttribute("href");
      a.textContent = link.getAttribute("href");
      a.target = "_blank";
      p.append(a);
      box.append(p);
      continue;
    }
    const el = document.createElement(node.tagName === "H3" ? "h2" : node.tagName.toLowerCase());
    el.textContent = node.textContent;
    box.append(el);
  }
  $("article-scroll").scrollTop = 0;
}

function words(text) {
  return text.normalize("NFKC").match(/[\p{L}\p{N}]+/gu) || [];
}

// A pattern that matches these words with any spacing or punctuation
// between them, since quotes rarely survive extraction character-for-character.
function loosePattern(list) {
  const escaped = list.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return new RegExp(escaped.join("[^\\p{L}\\p{N}]+"), "iu");
}

function clearMarks() {
  for (const mark of $("article").querySelectorAll("mark")) mark.replaceWith(...mark.childNodes);
  $("article").normalize();
}

// Wraps the first unmarked match of pattern in a <mark>, or returns null.
function markText(pattern) {
  const walker = document.createTreeWalker($("article"), NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => (node.parentElement.closest("mark, .source")
      ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const match = pattern.exec(node.data);
    if (!match) continue;
    const range = document.createRange();
    range.setStart(node, match.index);
    range.setEnd(node, match.index + match[0].length);
    const mark = document.createElement("mark");
    range.surroundContents(mark);
    return mark;
  }
  return null;
}

function markInArticle(quote) {
  clearMarks();
  const all = words(quote);
  if (!all.length) return false;
  // The whole quote; failing that each sentence (a quote can span
  // paragraphs); failing that its opening words.
  let first = markText(loosePattern(all));
  if (!first) {
    for (const sentence of quote.split(/(?<=[.!?])\s+/).map(words).filter((w) => w.length >= 5)) {
      const mark = markText(loosePattern(sentence));
      first ||= mark;
    }
  }
  if (!first && all.length > 10) first = markText(loosePattern(all.slice(0, 10)));
  if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
  return Boolean(first);
}

// ---- finding in a PDF -----------------------------------------------------

function find(query, { again = false, previous = false } = {}) {
  bus.dispatch("find", {
    source: null,
    type: again ? "again" : "",
    query,
    caseSensitive: false,
    entireWord: false,
    highlightAll: true,
    findPrevious: previous,
    matchDiacritics: false,
  });
}

function closeFind() {
  bus.dispatch("findbarclose", { source: null });
  $("find-count").textContent = "";
}

// Resolves once pdf.js has searched: true if it found the query.
function searchOnce(query) {
  return new Promise((resolve) => {
    const onState = ({ state }) => {
      if (state === FIND_PENDING) return;
      bus.off("updatefindcontrolstate", onState);
      resolve(state !== FIND_NOT_FOUND);
    };
    bus.on("updatefindcontrolstate", onState);
    find(query);
  });
}

async function showQuote(quote, page) {
  if (!current) return false;
  if (current.kind !== "pdf") return markInArticle(quote);
  await loading;
  if (!current.doc) return false;
  // Extraction and the PDF's own text layer rarely agree on every
  // character, so fall back from the whole quote to its pieces.
  const text = quote.replace(/\s+/g, " ").trim();
  const sentences = text.split(/(?<=[.!?])\s+/).filter((s) => s.split(" ").length >= 5);
  const opening = text.split(" ").slice(0, 8).join(" ");
  for (const attempt of [text, ...sentences, opening]) {
    if (await searchOnce(attempt)) {
      $("find-input").value = "";
      $("find-count").textContent = "";
      return true;
    }
  }
  closeFind();
  if (page) viewer.currentPageNumber = Math.min(page, viewer.pagesCount);
  return false;
}

bus.on("updatefindmatchescount", ({ matchesCount }) => {
  if (!$("find-input").value) return;
  $("find-count").textContent = matchesCount.total
    ? `${matchesCount.current} of ${matchesCount.total}`
    : "no matches";
});

// ---- toolbar --------------------------------------------------------------

function zoom(step) {
  fitting = false;
  const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, viewer.currentScale * step));
  viewer.currentScale = Math.round(next * 100) / 100;
}

$("page-prev").addEventListener("click", () => viewer.previousPage());
$("page-next").addEventListener("click", () => viewer.nextPage());
$("page-input").addEventListener("change", () => {
  const page = Number($("page-input").value);
  if (page >= 1 && page <= viewer.pagesCount) viewer.currentPageNumber = page;
  else $("page-input").value = viewer.currentPageNumber;
});
$("page-input").addEventListener("focus", () => $("page-input").select());
$("zoom-in").addEventListener("click", () => zoom(1.15));
$("zoom-out").addEventListener("click", () => zoom(1 / 1.15));
$("zoom-fit").addEventListener("click", () => {
  fitting = true;
  fit();
});
$("find-input").addEventListener("input", () => {
  const query = $("find-input").value.trim();
  if (query) find(query);
  else closeFind();
});
$("find-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter") find($("find-input").value.trim(), { again: true, previous: event.shiftKey });
  if (event.key === "Escape") {
    $("find-input").value = "";
    closeFind();
    $("find-input").blur();
  }
});

// Ctrl+scroll zooms the paper, as in any PDF reader.
$("pdf-scroll").addEventListener("wheel", (event) => {
  if (!event.ctrlKey) return;
  event.preventDefault();
  zoom(event.deltaY < 0 ? 1.1 : 1 / 1.1);
}, { passive: false });

// The self-test parses this through the worker, so a packaged build whose
// pdf.js files didn't make it into the installer fails in CI, not for you.
const TINY_PDF = "%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
  + "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
  + "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 72 72]>>endobj\n"
  + "trailer<</Root 1 0 R>>\n%%EOF";

async function selfTest() {
  const doc = await pdfjs.getDocument({ data: new TextEncoder().encode(TINY_PDF) }).promise;
  const pages = doc.numPages;
  await doc.destroy();
  return pages;
}

window.reader = {
  open,
  selfTest,
  showQuote,
  focusFind() {
    if (current?.kind !== "pdf") return;
    $("find-input").focus();
    $("find-input").select();
  },
  get kind() { return current?.kind || null; },
};
window.dispatchEvent(new Event("reader-ready"));
