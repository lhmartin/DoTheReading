"""Shared functions for extracting text from PDFs and generating study
questions via a local Ollama model. Imported by process_inbox.py — not run
directly."""

import difflib
import functools
import json
import math
import os
import re
import shutil
import sys
import textwrap
from pathlib import Path

import pdfplumber
import pytesseract
import requests

from qa_format import QUESTIONS_SCHEMA, normalize_for_match, parse_legacy_qa, parse_model_json, strip_json_fence

OLLAMA_URL = "http://localhost:11434/api/generate"
# Context window in tokens. Set explicitly because Ollama's default (4096 on
# this machine) is smaller than a full chunk + instructions + answer, and
# Ollama silently drops the start of prompts that don't fit.
NUM_CTX = 8192
MAX_JSON_ATTEMPTS = 2  # per model call, before giving up on a malformed reply
CHUNK_SIZE = 12000    # chars; ~3,000 tokens per chunk
CHUNK_OVERLAP = 1500  # chars from the end of each chunk repeated at the start of the next
OCR_MIN_CHARS = 25    # pages with fewer non-whitespace chars than this get OCR'd
OCR_DPI = 300
# Max horizontal gap (pt) between chars in the same word. pdfplumber's default
# of 3 glues words together in tightly-set LaTeX PDFs ("wepresentPromera").
X_TOLERANCE = 1.5
GUTTER_HALF_WIDTH = 4  # pt; a column gutter must be an empty strip at least 8pt wide
CANDIDATE_FACTOR = 1.5  # generate this many times NUM_QUESTIONS, so failed checks can be replaced
MIN_QUOTE_CHARS = 25    # normalized length; shorter "quotes" are too generic to prove anything
QUOTE_MATCH_RATIO = 0.8 # share of a quote that must appear verbatim (after normalizing)


# ---- PDF text extraction ------------------------------------------------

@functools.cache
def ocr_available() -> bool:
    """True if the Tesseract binary can be found. On Windows the installer
    doesn't add it to PATH, so also check the default install locations."""
    cmd = shutil.which("tesseract")
    if not cmd and os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        ]
        cmd = next((str(c) for c in candidates if c.is_file()), None)
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    return cmd is not None


def needs_ocr(page_text: str) -> bool:
    return len("".join(page_text.split())) < OCR_MIN_CHARS


def ocr_page(page) -> str:
    # pdfplumber renders pages via pypdfium2, so no Poppler install is needed.
    image = page.to_image(resolution=OCR_DPI).original
    return pytesseract.image_to_string(image)


def find_gutter(words: list[dict], width: float) -> float | None:
    """x position of the gap between two text columns, or None if the page
    is single-column. A gutter is a vertical line near the middle that
    almost no text row crosses, with plenty of text on both sides."""
    rows = {}
    for w in words:
        rows.setdefault(round(w["top"]), []).append(w)
    if len(rows) < 10:
        return None
    # Score each x by the rows crossing anywhere within +/- GUTTER_HALF_WIDTH,
    # so a gutter must be a wide empty strip. Otherwise a gap between words
    # that happens to line up (e.g. in a table) could pass for one.
    best, best_crossing = None, None
    for x in range(int(width * 0.4), int(width * 0.6) + 1):
        lo, hi = x - GUTTER_HALF_WIDTH, x + GUTTER_HALF_WIDTH
        crossing = sum(any(w["x0"] < hi and w["x1"] > lo for w in row) for row in rows.values())
        if best_crossing is None or crossing < best_crossing:
            best, best_crossing = x, crossing
    left = sum(any(w["x1"] <= best for w in row) for row in rows.values())
    right = sum(any(w["x0"] >= best for w in row) for row in rows.values())
    if best_crossing > 0.25 * len(rows) or min(left, right) < 0.25 * len(rows):
        return None
    return best


def read_page(page) -> str:
    """Text of one page in reading order, handling two-column layouts.

    pdfplumber reads straight across the page, which interleaves the lines
    of two columns. Instead, rows that cross the gutter (titles, headers,
    wide figures/equations) are read as full-width bands, and between those
    the left column is read before the right one.
    """
    # Drop rotated text, e.g. arXiv's sideways stamp in the margin.
    page = page.filter(lambda obj: obj.get("object_type") != "char" or obj.get("upright", True))
    words = page.extract_words(x_tolerance=X_TOLERANCE)
    gutter = find_gutter(words, page.width)
    if gutter is None:
        text = page.extract_text(x_tolerance=X_TOLERANCE) or ""
    else:
        # The gutter is the line the fewest rows cross, so any word crossing
        # it belongs to a genuinely full-width row.
        bands = []
        for top, bottom in sorted((w["top"], w["bottom"]) for w in words if w["x0"] < gutter < w["x1"]):
            if bands and top <= bands[-1][1] + 4:
                bands[-1][1] = max(bands[-1][1], bottom)
            else:
                bands.append([top, bottom])

        def region(x0, top, x1, bottom):
            # Assign each character to exactly one region by its centre, so
            # nothing is read twice or lost on a boundary.
            def inside(obj):
                cx, cy = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
                return x0 <= cx < x1 and top <= cy < bottom
            return page.filter(inside).extract_text(x_tolerance=X_TOLERANCE) or ""

        left, right = page.bbox[0], page.bbox[2]
        parts, y = [], page.bbox[1]
        for top, bottom in bands + [[page.bbox[3], page.bbox[3]]]:
            parts += [region(left, y, gutter, top), region(gutter, y, right, top)]
            parts.append(region(left, top, right, bottom))
            y = bottom
        text = "\n".join(p for p in parts if p.strip())
    return re.sub(r"\(cid:\d+\)", "", text)  # glyphs with no text mapping (math fonts)


def extract_text(pdf_path: str, ocr: bool = True, log=print) -> str:
    """Extract text page by page, OCR-ing pages that have no text layer.

    Returns "" if no page yielded any text, so callers can skip the paper.
    """
    text_parts = []
    found_text = False
    ocr_count = skipped_count = 0
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            page_text = read_page(page)
            if ocr and needs_ocr(page_text):
                if not ocr_available():
                    skipped_count += 1
                else:
                    try:
                        page_text = ocr_page(page)
                        ocr_count += 1
                    except pytesseract.TesseractError as e:
                        log(f"    OCR failed on page {i + 1}: {e}")
            if page_text.strip():
                found_text = True
            text_parts.append(f"--- Page {i + 1} ---\n{page_text}")

        if ocr_count:
            log(f"    OCR'd {ocr_count}/{len(pdf.pages)} page(s) with no text layer.")
        if skipped_count:
            log(f"    WARNING: {skipped_count} page(s) have no text layer, but Tesseract "
                f"isn't installed so they couldn't be OCR'd.")

    return "\n\n".join(text_parts) if found_text else ""


# ---- Chunking -------------------------------------------------------------

def _split_long(para: str, limit: int) -> list[str]:
    """Split a paragraph longer than `limit` at line breaks or spaces."""
    pieces = []
    while len(para) > limit:
        cut = para.rfind("\n", 0, limit)
        if cut <= 0:
            cut = para.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        pieces.append(para[:cut].rstrip())
        para = para[cut:].lstrip()
    if para.strip():
        pieces.append(para)
    return pieces


def _tail(text: str, overlap: int) -> str:
    """The last ~`overlap` chars of `text`, starting on a paragraph break if
    there is one in range, otherwise on a word boundary."""
    if overlap <= 0:
        return ""
    tail = text[-overlap:]
    if len(tail) == len(text):
        return tail
    for sep in ("\n\n", "\n", " "):
        idx = tail.find(sep)
        if idx != -1:
            return tail[idx + len(sep):].lstrip()
    return tail


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into chunks of at most `chunk_size` chars on paragraph
    boundaries. Each chunk after the first starts with up to `overlap` chars
    copied from the end of the previous one, so content near a boundary is
    seen with its surrounding context."""
    if not 0 <= overlap < chunk_size // 2:
        raise ValueError("overlap must be non-negative and less than half of chunk_size")

    # Leave room for the overlap and a paragraph separator in every chunk.
    piece_limit = chunk_size - overlap - 2
    pieces = []
    for para in text.split("\n\n"):
        if para.strip():
            pieces.extend(_split_long(para, piece_limit))

    chunks, current = [], ""
    for piece in pieces:
        if current and len(current) + 2 + len(piece) > chunk_size:
            chunks.append(current)
            current = _tail(current, overlap)
        current += ("\n\n" if current else "") + piece
    if current:
        chunks.append(current)
    return chunks


# ---- Ollama -------------------------------------------------------------

def call_ollama(prompt: str, model: str, fmt=None) -> str:
    """Send a prompt to Ollama. `fmt` is passed as Ollama's `format` field
    (a JSON schema here) to constrain the output."""
    payload = {"model": model, "prompt": prompt, "stream": False, "options": {"num_ctx": NUM_CTX}}
    if fmt is not None:
        payload["format"] = fmt
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=1200)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        sys.exit("Could not reach Ollama at localhost:11434. Is it running?")
    except requests.exceptions.HTTPError:
        # A 404 here means Ollama is running but doesn't have this model —
        # worth saying plainly rather than showing a raw HTTP error.
        try:
            detail = resp.json().get("error", "")
        except ValueError:
            detail = resp.text.strip()[:200]
        if resp.status_code == 404:
            sys.exit(f"Ollama doesn't have the model '{model}'. Download it in the app's Settings, "
                     f"or run: ollama pull {model}")
        if "llama-server" in detail or "GGML_ASSERT" in detail:
            # Ollama's own model server died; nothing here can retry around it.
            sys.exit("Ollama's model server crashed while running the model. Try turning off the speed "
                     "settings in Settings and running again; if it keeps happening, re-download the "
                     "model or update Ollama.")
        sys.exit(f"Ollama returned HTTP {resp.status_code}: {detail}")
    return resp.json()["response"].strip()


def installed_models() -> list[str] | None:
    """Model names Ollama has, or None if it can't be reached."""
    try:
        resp = requests.get(OLLAMA_URL.replace("/api/generate", "/api/tags"), timeout=5)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except (requests.exceptions.RequestException, ValueError, KeyError):
        return None


def check_model(model: str) -> str | None:
    """A human-readable problem with using `model`, or None if it looks usable."""
    available = installed_models()
    if available is None:
        return "Could not reach Ollama at localhost:11434. Is it running?"
    if model in available or f"{model}:latest" in available:
        return None
    have = ", ".join(sorted(available)) if available else "none"
    return (f"Ollama doesn't have the model '{model}' (installed: {have}). "
            f"Download it in the app's Settings, or run: ollama pull {model}")


def _ask_with_retry(prompt: str, model: str, schema: dict, parse, log=print) -> tuple:
    """Call the model constrained to `schema` and parse the reply, retrying
    once if it doesn't parse. Returns (parsed value, last raw reply); the
    value is None if every attempt failed."""
    raw = ""
    for attempt in range(1, MAX_JSON_ATTEMPTS + 1):
        raw = call_ollama(prompt, model, fmt=schema)
        try:
            return parse(raw), raw
        except ValueError as e:
            log(f"      couldn't parse model output (attempt {attempt}/{MAX_JSON_ATTEMPTS}): {e}")
    return None, raw


def _parse_json_object(raw: str) -> dict:
    try:
        data = json.loads(strip_json_fence(raw))
    except json.JSONDecodeError as e:
        raise ValueError(str(e)) from e
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def ask_json(prompt: str, model: str, schema: dict, log=print) -> dict:
    data, _ = _ask_with_retry(prompt, model, schema, _parse_json_object, log)
    if data is None:
        raise ValueError(f"model did not return valid JSON after {MAX_JSON_ATTEMPTS} attempts")
    return data


def ask_for_questions(prompt: str, model: str, log=print) -> list[dict]:
    """Ask for structured questions, falling back to reading the reply as
    old-style Q:/A: text if it never parses as JSON."""
    questions, raw = _ask_with_retry(prompt, model, QUESTIONS_SCHEMA, parse_model_json, log)
    if questions is not None:
        return questions

    questions = parse_legacy_qa(raw)
    if questions:
        log("      recovered questions from plain-text Q:/A: output.")
        return questions
    raise ValueError(f"model did not return usable questions after {MAX_JSON_ATTEMPTS} attempts")


# ---- Quote checking -----------------------------------------------------

def quote_in_text(quote: str, text: str, min_chars: int = MIN_QUOTE_CHARS) -> bool:
    """True if `quote` really appears in `text`, ignoring case, spacing and
    punctuation. Allows small slips (e.g. a mangled symbol) as long as at
    least QUOTE_MATCH_RATIO of the quote appears as one unbroken run."""
    q = normalize_for_match(quote)
    if len(q) < min_chars:
        return False
    t = normalize_for_match(text)
    if q in t:
        return True
    match = difflib.SequenceMatcher(None, q, t, autojunk=False).find_longest_match(0, len(q), 0, len(t))
    return match.size >= QUOTE_MATCH_RATIO * len(q)


_PAGE_MARKER_RE = re.compile(r"^--- Page (\d+) ---$", flags=re.MULTILINE)


def page_index(text: str) -> list[tuple[int, str]]:
    """(page number, normalized page text) for each page of an extracted
    document, so many quotes can be located without re-normalizing it."""
    markers = list(_PAGE_MARKER_RE.finditer(text))
    return [(int(m.group(1)), normalize_for_match(text[m.end():(n.start() if n else len(text))]))
            for m, n in zip(markers, markers[1:] + [None])]


def find_page(quote: str, pages: list[tuple[int, str]]) -> int | None:
    """Page number where `quote` starts, using a page_index()."""
    head = normalize_for_match(quote)[:40]
    if not head:
        return None
    return next((number for number, page in pages if head in page), None)


def clean_quote(quote: str) -> str:
    """Undo PDF line wrapping in a quote for display ("discrimi-\nnate")."""
    return " ".join(re.sub(r"(\w)-\n(\w)", r"\1\2", quote).split())


def balance_types(ranked: list[dict]) -> list[dict]:
    """Interleave question types (keeping rank order within each type), so
    the first N questions are a mix even if the ranking favours one type."""
    by_type = {}
    for q in ranked:
        by_type.setdefault(q.get("type", ""), []).append(q)
    queues = sorted(by_type.values(), key=lambda qs: ranked.index(qs[0]))
    out = []
    while any(queues):
        for queue in queues:
            if queue:
                out.append(queue.pop(0))
    return out


# ---- Prompts ------------------------------------------------------------
# Verification prompts put the section text FIRST: Ollama reuses its cache
# for a shared prompt prefix, so several checks against the same section
# only pay to read the section once.

def build_prompt(text_chunk: str, num_questions: int, section_label: str, overlapping: bool = False,
                 guidance: str = "") -> str:
    # Dedent the template before appending the paper text: interpolating the
    # (unindented) text first would stop dedent from stripping anything.
    prompt = textwrap.dedent(f"""\
        You are helping a researcher study a scientific paper. Below is
        {section_label} of the paper. Generate {num_questions} study
        questions based ONLY on this text.
    """)
    if overlapping:
        prompt += textwrap.dedent("""\
            (Instruction, not part of the paper: the first few paragraphs of the
            text repeat the end of the previous section for context only. Don't
            ask questions about them or about this instruction.)
        """)
    if guidance.strip():
        # The reader's own steer, e.g. "focus on experimental design".
        prompt += "\nWhat this reader wants from the questions:\n" + guidance.strip() + "\n"
    prompt += textwrap.dedent("""
        Include a mix of:
        - comprehension questions (what did they do / find)
        - methodology questions (why this method, what are its limits)
        - critical questions (limitations, assumptions, or open problems that
          the text itself states or directly implies; not generic criticism)

        Every answer must be supported by the text. For each question, copy
        into "evidence" one sentence from the text, word for word, that
        supports the answer. Prefer questions about central ideas over
        incidental details such as default hyperparameters.

        Respond with JSON only, in this shape:
        {"questions": [{"type": "comprehension" | "methodology" | "critical",
                        "question": "<question>",
                        "answer": "<brief answer>",
                        "evidence": "<exact sentence copied from the text>"}]}

        TEXT:
    """)
    return prompt + text_chunk + "\n"


RANK_SCHEMA = {
    "type": "object",
    "properties": {"ranked_ids": {"type": "array", "items": {"type": "integer"}}},
    "required": ["ranked_ids"],
}


def build_rank_prompt(candidates: list[dict], num_questions: int) -> str:
    listing = "\n".join(f"[{i}] ({q['type'] or 'other'}) {q['question']}" for i, q in enumerate(candidates))
    return textwrap.dedent(f"""\
        Below are candidate study questions generated separately from
        different sections of the same scientific paper. Rank them for a
        researcher who wants to understand the paper deeply:

        - Drop near-duplicates (keep the better-worded one).
        - Put questions about central ideas, methods and findings first,
          trivia (exact hyperparameters, minor details) last.
        - Keep a mix of comprehension, methodology and critical questions
          near the top; the first {num_questions} will usually be used.

        Respond with JSON only: {{"ranked_ids": [<id>, <id>, ...]}}, listing
        every candidate you keep, best first.

        CANDIDATES:
    """) + listing + "\n"


BLIND_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answerable": {"type": "boolean"},
        "answer": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["answerable", "answer", "quote"],
}


def build_blind_answer_prompt(text_chunk: str, question: str) -> str:
    return "TEXT:\n" + text_chunk + "\n\n" + textwrap.dedent(f"""\
        Answer the question below using ONLY the text above. Copy into
        "quote" the single sentence from the text, word for word, that best
        supports your answer. If the text doesn't answer the question, set
        "answerable" to false.

        QUESTION: {question}

        Respond with JSON only:
        {{"answerable": true | false, "answer": "<brief answer>", "quote": "<exact sentence from the text>"}}
    """)


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "premise_ok": {"type": "boolean"},
        "a_supported": {"type": "boolean"},
        "verdict": {"type": "string", "enum": ["agree", "partial", "disagree"]},
        "reason": {"type": "string"},
    },
    "required": ["premise_ok", "a_supported", "verdict", "reason"],
}


def build_judge_prompt(text_chunk: str, question: str, answer_a: str, answer_b: str) -> str:
    # Agreement alone isn't enough: a question with a false premise can lead
    # both answers into the same mistake. So the judge also checks the
    # premise and answer A against the text directly.
    return "TEXT:\n" + text_chunk + "\n\n" + textwrap.dedent(f"""\
        Two answers were written independently to the same question about
        the text above. Check them carefully against the text.

        QUESTION: {question}
        ANSWER A: {answer_a}
        ANSWER B: {answer_b}

        - premise_ok: false if the question assumes something the text does
          not say or contradicts (e.g. asks for a limitation the text never
          states, or presupposes the opposite of what the text claims).
        - a_supported: true only if every claim in ANSWER A is supported by
          the text. False if any part is contradicted or made up.
        - verdict: "agree" if A and B make the same core claim; "partial" if
          compatible but one is missing something important; "disagree" if
          they contradict each other or make different claims.

        Respond with JSON only:
        {{"premise_ok": true | false, "a_supported": true | false,
          "verdict": "agree" | "partial" | "disagree", "reason": "<one sentence>"}}
    """)


TITLE_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}


# ---- Pipeline -------------------------------------------------------------

def verify_question(q: dict, text_chunk: str, model: str, log=print) -> bool:
    """Re-answer the question blind from its source section and keep it only
    if the answers agree and a verbatim supporting quote exists. On success,
    sets q["evidence"] to the verified quote."""
    evidence_ok = quote_in_text(q.get("evidence", ""), text_chunk)

    blind = ask_json(build_blind_answer_prompt(text_chunk, q["question"]), model, BLIND_ANSWER_SCHEMA, log)
    if not blind.get("answerable", True) or not str(blind.get("answer", "")).strip():
        q["verification"] = "not answerable from the text"
        return False
    blind_quote = str(blind.get("quote", "")).strip()
    blind_quote_ok = quote_in_text(blind_quote, text_chunk)
    if not (evidence_ok or blind_quote_ok):
        q["verification"] = "no quote found in the text"
        return False

    judged = ask_json(build_judge_prompt(text_chunk, q["question"], q["answer"], str(blind["answer"])),
                      model, JUDGE_SCHEMA, log)
    verdict = judged.get("verdict")
    q["verification"] = f"{verdict}: {judged.get('reason', '')}"
    if judged.get("premise_ok") is False:
        q["verification"] = "false premise: " + q["verification"]
        return False
    if judged.get("a_supported") is False:
        q["verification"] = "answer not supported: " + q["verification"]
        return False
    if verdict not in ("agree", "partial"):
        return False
    q["evidence"] = clean_quote(q["evidence"] if evidence_ok else blind_quote)
    return True


def rank_candidates(candidates: list[dict], num_questions: int, model: str, log=print) -> list[dict]:
    try:
        ranked_ids = ask_json(build_rank_prompt(candidates, num_questions), model, RANK_SCHEMA, log)["ranked_ids"]
    except (ValueError, KeyError) as e:
        log(f"    ranking failed ({e}); keeping generation order.")
        return candidates
    seen, ranked = set(), []
    for i in ranked_ids:
        if isinstance(i, int) and 0 <= i < len(candidates) and i not in seen:
            seen.add(i)
            ranked.append(candidates[i])
    return ranked or candidates


def generate_questions(text: str, model: str, num_questions: int, log=print,
                       guidance: str = "") -> tuple[list[dict], str]:
    """Generate, rank and verify questions. Returns (questions, summary)
    where each question dict has type, question, answer, evidence, page."""
    chunks = chunk_text(text)
    target = math.ceil(num_questions * CANDIDATE_FACTOR)
    per_chunk = max(3, math.ceil(target / len(chunks)))

    if len(chunks) > 1:
        log(f"    long paper — processing in {len(chunks)} sections...")
    candidates = []
    for i, chunk in enumerate(chunks):
        if len(chunks) > 1:
            log(f"      section {i + 1}/{len(chunks)}...")
        label = "the full text" if len(chunks) == 1 else f"section {i + 1} of {len(chunks)}"
        prompt = build_prompt(chunk, per_chunk, label, overlapping=i > 0, guidance=guidance)
        for q in ask_for_questions(prompt, model, log):
            q["chunk"] = i
            candidates.append(q)

    log(f"    ranking {len(candidates)} candidate questions...")
    ranked = balance_types(rank_candidates(candidates, num_questions, model, log))

    # Verify in rank order, a batch at a time. Within a batch, go section by
    # section so Ollama's prompt cache can reuse each section.
    verified, checked, remaining = [], 0, list(ranked)
    while remaining and len(verified) < num_questions:
        need = num_questions - len(verified)
        batch, remaining = remaining[:need], remaining[need:]
        log(f"    verifying {len(batch)} question(s)...")
        for q in sorted(batch, key=lambda q: q["chunk"]):
            checked += 1
            try:
                ok = verify_question(q, chunks[q["chunk"]], model, log)
            except ValueError as e:
                ok, q["verification"] = False, str(e)
            if ok and normalize_for_match(q["evidence"]) in {normalize_for_match(v["evidence"]) for v in verified}:
                ok, q["verification"] = False, "duplicate: same evidence as a question already kept"
            if ok:
                verified.append(q)
            else:
                log(f"      dropped: {q['question'][:70]}... ({q['verification'][:80]})")

    verified.sort(key=ranked.index)  # batches were verified section by section
    pages = page_index(text)
    for q in verified:
        q["page"] = find_page(q["evidence"], pages)
    summary = f"{len(verified)} of {checked} questions checked passed verification ({len(candidates)} generated)."
    log(f"    {summary}")
    return verified, summary


def find_title(pdf_path: str, text: str, model: str, log=print) -> str | None:
    """The paper's title: from PDF metadata if it looks real, else asked of
    the model. Either way it must appear on page 1, or we return None."""
    first_page = text.split("--- Page 2 ---")[0].replace("--- Page 1 ---", "")

    with pdfplumber.open(pdf_path) as pdf:
        meta_title = str((pdf.metadata or {}).get("Title") or "").strip()
    if meta_title and quote_in_text(meta_title, first_page) and not meta_title.lower().endswith((".pdf", ".dvi", ".tex")):
        return " ".join(meta_title.split())

    prompt = textwrap.dedent("""\
        Below is the first page of a scientific paper. What is the paper's
        title? Copy it exactly, without authors or journal names. Respond
        with JSON only: {"title": "<title>"}

        FIRST PAGE:
    """) + first_page[:3000]
    try:
        title = " ".join(str(ask_json(prompt, model, TITLE_SCHEMA, log).get("title", "")).split())
    except ValueError:
        return None
    # Titles are shorter than the usual evidence quote, so lower the minimum.
    return title if quote_in_text(title, first_page, min_chars=8) else None
