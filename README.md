# DoTheReading

A tool to help make sure you've done the reading.

Local, no-API-cost pipeline for studying research papers: drop PDFs in a
folder, an overnight job generates study questions via a local Ollama
model, and a morning script quizzes you on whichever paper you pick.

## Environment

- Windows laptop with WSL available; running Ollama natively on Windows
  (not inside WSL) to keep Task Scheduler simple.
- GPU: NVIDIA RTX 5000 Ada Generation Laptop GPU, 16GB VRAM.
- Target model: `qwen2.5:32b-instruct-q3_K_S` — the 32B model at a
  quantization that fits in 16GB VRAM (see Performance below).

## Setup (one command)

Clone the repo onto the **Windows** drive (not inside WSL — Task Scheduler
and Windows Python can't reliably run from a `\\wsl$` path), then from
PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

This will, skipping anything already done:

1. Install Python 3.12, Ollama and Tesseract OCR via `winget` if missing,
   and set `OLLAMA_FLASH_ATTENTION=1` / `OLLAMA_KV_CACHE_TYPE=q8_0` so the
   model's context cache fits in VRAM alongside the model.
2. Create `.venv\` in the repo and `pip install -r requirements.txt` into it.
3. Start Ollama if it isn't running and `ollama pull` the model (read from
   `MODEL` in `process_inbox.py`).
4. Create `~/PaperStudy/{inbox,library,questions}`.
5. Register the `PaperStudyNightly` scheduled task (daily at 02:00, using
   the venv's Python) **with "Wake the computer to run this task" enabled**,
   so the Task Scheduler GUI step is no longer needed. An existing task with
   that name is replaced.

Options: `-Model qwen2.5:14b`, `-Time 03:30`, `-TaskName ...`,
`-SkipModelPull`, `-SkipTask`. Re-running is safe.

Daily use:

```powershell
.venv\Scripts\python.exe quiz_me.py            # morning quiz: pick a paper or the review session
.venv\Scripts\python.exe quiz_me.py --review   # straight to questions you've missed
.venv\Scripts\python.exe process_inbox.py      # process the inbox right now
```

## Folder layout (created automatically on first run)

```
~/PaperStudy/
  inbox/      <- drop new PDFs here during the day
  library/    <- processed PDFs get moved here
  questions/  <- generated *_questions.md files land here
  process_log.txt
  quiz_history.json  <- your quiz results; drives the review pile
```

## Files

- `paper_qa_lib.py` — PDF text extraction (pdfplumber, with Tesseract OCR
  for pages that have no text layer), chunking long papers with overlap,
  calling the local Ollama HTTP API (`localhost:11434/api/generate`), and
  the question-generation prompts.
- `qa_format.py` — the question JSON schema, and rendering/parsing the
  `*_questions.md` files. Standard library only.
- `quiz_history.py` — reads/writes `quiz_history.json`. Standard library only.
- `process_inbox.py` — batch job run nightly by Task Scheduler. Processes
  every new PDF in `inbox/`, skips anything already processed, and is
  resilient to individual-paper failures.
- `quiz_me.py` — interactive CLI: lists processed papers (by title)
  newest-first, walks through the questions one at a time showing the answer
  plus the supporting quote and page, and records right/wrong. Questions you
  get wrong go into a review pile you can work through later.
- `setup.ps1` — the one-command Windows install described above.
- `tests/` — pytest tests for extraction, chunking, the question format,
  the verification logic (with a scripted fake model) and quiz history.

## How it works

**Text extraction.** Two-column pages are detected (an empty vertical
strip near the middle that almost no text row crosses) and read left
column then right; rows that cross the gutter, such as titles and wide
figures, are read as full-width bands in place. Without this, pdfplumber
interleaves the two columns line by line. Words are split with a tighter
tolerance than pdfplumber's default, which glues words together in many
LaTeX PDFs, and rotated margin text (e.g. arXiv's stamp) is dropped.

**OCR fallback.** Each page is extracted with pdfplumber; any page with
fewer than 25 non-whitespace characters is rendered at 300 DPI and run
through Tesseract. Mixed PDFs only OCR the pages that need it. Pages are
rendered with pdfplumber's built-in renderer (pypdfium2) rather than
pdf2image, so Poppler doesn't need to be installed. If Tesseract is
missing, a warning is logged and a fully scanned PDF is skipped (left in
the inbox).

**Chunking.** Papers are split on paragraph boundaries into ~12,000-char
chunks. Each chunk after the first starts with the last ~1,500 chars of
the previous one (cut at a paragraph or word boundary), and the prompt
tells the model that part is context only. Paragraphs longer than a chunk
are split at line/word boundaries. Tune `CHUNK_SIZE` / `CHUNK_OVERLAP` in
`paper_qa_lib.py`.

**Structured output.** Every model call is constrained with Ollama's
`format` JSON schema (needs Ollama 0.5+), and malformed replies are retried
once. The context window is set explicitly (`NUM_CTX = 8192`) because
Ollama's default is too small for a full chunk and it silently truncates.

**Question pipeline** (`generate_questions` in `paper_qa_lib.py`):

1. *Generate* ~1.5× `NUM_QUESTIONS` candidates across the sections, each
   with a verbatim `evidence` sentence from the text.
2. *Rank*: the model returns candidate IDs best-first, dropping
   near-duplicates and pushing trivia down (cheap: it doesn't rewrite them).
3. *Verify*, in rank order until `NUM_QUESTIONS` pass. For each question
   the model re-answers it **blind** from its source section (without seeing
   the original answer) and must quote the supporting sentence. The code
   checks that a quote really appears in the section (ignoring
   case/spacing/punctuation/hyphenation), then the model judges whether the
   two answers agree. Questions that fail are dropped and replaced by the
   next candidate; the log says why each was dropped.
4. The verified quote and its page number are saved with the answer.

The paper's title comes from the PDF metadata or, failing that, from the
model reading page 1. Either way it must actually appear on page 1, or the
file name is used instead.

Question files use a fixed layout:

```markdown
# Study Questions: <paper title>

_paper.pdf · 12 of 15 questions checked passed verification (18 generated)._

## Q1 (methodology)
Why did they use a within-subjects design?

**Answer:** ...

**Evidence (p. 4):** "exact sentence from the paper"
```

`quiz_me.py` still reads older question files (without evidence, or in the
`Q: ... A: ...` format).

## Development

Tests run anywhere (including WSL) and don't need Ollama or Tesseract; the
one real-OCR test is skipped if Tesseract isn't installed.

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

## Performance (RTX 5000 Ada, 16GB)

Measured on the same paper (22 pages, 8 sections) and the same hardware:

| Model | VRAM | On CPU | Generation | One paper |
|---|---|---|---|---|
| `32b-instruct-q3_K_S` (default) | 15GB | 8% | 10.4 tok/s | 12.7 min |
| `32b-instruct-q4_K_M` | 22GB | 35% | 4.3 tok/s | 37.5 min |
| `qwen2.5:14b` | 10GB | 0% | ~40 tok/s | ~3 min |

Model choice is mostly about verification. On a question whose answer
stated the opposite of the paper, both 32b quantizations rejected it in
3/3 trials (and kept correct answers in 3/3), while the 14b kept the wrong
answer every time — it misread the passage itself. Verification is only as
good as the model's reading, so a 32b is the default. q4_K_M writes
slightly sharper questions than q3_K_S (fewer trivial or thinly supported
ones), at 3x the runtime; switch by changing `MODEL` in `process_inbox.py`
and running `ollama pull` for it.

The q3_K_S numbers need `OLLAMA_FLASH_ATTENTION=1` and
`OLLAMA_KV_CACHE_TYPE=q8_0` (set by `setup.ps1`): without them the context
cache is twice as big and part of the model spills back onto the CPU.

## Known rough edges / open items

- If no question passes verification, the PDF stays in the inbox and is
  retried every night.
