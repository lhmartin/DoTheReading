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

## Install

**Easiest: download the installer.** Grab `DoTheReading-Setup-*.exe` from
[Releases](https://github.com/lhmartin/DoTheReading/releases). It contains
the app and the whole Python pipeline (frozen with PyInstaller), so no
Python install is needed. On first run, the app's **Settings** tab checks
what's missing and offers to fix it:

- **Ollama** — install it once from [ollama.com](https://ollama.com); the
  app tells you if it isn't running.
- **A model** — pick one in Settings and press Download (the default is
  ~14 GB; smaller ones are offered, with their trade-offs spelled out).
- **Nightly run** — one button registers the 02:00 task (and another
  removes it).
- **Tesseract** — optional, only for scanned PDFs.

**Windows will warn you.** The installer isn't code-signed, so SmartScreen
says "Windows protected your PC": choose *More info → Run anyway*. Every
release carries a build attestation and checksums if you'd rather verify
what you downloaded:

```powershell
gh attestation verify .\DoTheReading-Setup-0.1.2.exe -R lhmartin/DoTheReading
Get-FileHash .\DoTheReading-Setup-0.1.2.exe -Algorithm SHA256   # compare with SHA256SUMS.txt
```

Signing it properly needs a certificate; `release.yml` picks up
`WINDOWS_CSC_LINK` / `WINDOWS_CSC_KEY_PASSWORD` repo secrets and signs
automatically when they exist.

The sections below are the from-source route, which you also want if you
like the CLI or plan to change the code.

## Setup from source (one command)

Clone the repo onto the **Windows** drive (not inside WSL — Task Scheduler
and Windows Python can't reliably run from a `\\wsl$` path), then from
PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

This will, skipping anything already done:

1. Install Python 3.12, Node.js, Ollama and Tesseract OCR via `winget` if missing,
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

Check it works end to end before waiting on the scheduler:

```powershell
copy some-paper.pdf $HOME\PaperStudy\inbox\
.venv\Scripts\python.exe process_inbox.py   # ~13 min for a 20-page paper
.venv\Scripts\python.exe quiz_me.py
```

Daily use:

```powershell
DoTheReading.cmd                               # the app
.venv\Scripts\python.exe quiz_me.py            # or the CLI quiz
.venv\Scripts\python.exe quiz_me.py --review   # straight to questions you've missed
.venv\Scripts\python.exe process_inbox.py      # process the inbox right now
```

## The app

Installed, it's in the Start menu. From a checkout, `DoTheReading.cmd` (or
`npm start --prefix app`) opens it — the paper on the left, its questions
on the right.

- **Today** suggests a paper: something with questions you haven't
  studied, then anything you kept only for reading, otherwise whatever you
  looked at longest ago. "Suggest another" reshuffles, and the review pile
  is one click from the same screen.
- **Study** shows the paper beside the current question, with the sidebar
  out of the way; drag the divider to resize (double-click resets it).
  Reveal the answer and you get the supporting quote; "Show in the paper"
  scrolls to that passage and highlights it, in a PDF or a saved web
  article. The reader has its own page, zoom and find controls (`Ctrl+F`,
  `Ctrl`+scroll to zoom) and remembers where you left each PDF. Mark
  yourself with the buttons or the `y`/`n` keys (`space` reveals, `Esc`
  leaves).
- **Grade me with the model** (a checkbox on each question) turns it into
  a written exercise: type your answer, the local model marks it
  correct/partly/incorrect with a sentence of feedback, then you still
  mark yourself. ~10s per answer once the model is loaded.
- **Library** lists every processed paper with its score and review count,
  and can show all questions without quizzing. Search by title, or filter
  to unread papers or ones with questions to review.
- **Inbox** lists PDFs waiting, and "Process now" runs the nightly job
  immediately with its log streamed into the window. Drag PDFs anywhere
  onto the window to queue them (or use "Add PDFs…"); they're copied, so
  the original stays where it was, and anything already queued or already
  processed is skipped.
- **Progress** shows papers studied, questions answered, accuracy and the
  review pile over recent days.
- **Settings** holds the setup checklist, the model picker, how many
  questions per paper, a free-text steer added to every generation prompt
  ("I'm a wet-lab biologist: favour experimental design over the maths"),
  and the nightly-run toggle. These live in `~/PaperStudy/settings.json`,
  so the 02:00 job uses the same choices. The light/dark theme is set there
  too; it follows the system unless you pick one.

**On picking a smaller model:** the app lists each model's size and a
plain-language quality note, because the trade-off is real. Verification
depends on the model actually reading the passage: on a question whose
answer stated the opposite of the paper, both 32b quantizations rejected
it 3/3, while `qwen2.5:14b` kept it 3/3 — it misread the text itself.
Smaller models also write shallower questions. Faster is genuinely worse
here; the app says so where you choose.

The app is a thin UI: it shells out to `study_api.py`, so question files,
history and prompts have one implementation shared with the CLI. Both can
be used interchangeably — the app and `quiz_me.py` read and write the same
`quiz_history.json`.

## Example: a day in the life

**During the day** — drag PDFs onto the app window, or drop them into
`~/PaperStudy/inbox`. Nothing else to do.

**02:00, the scheduled task runs.** From `process_log.txt` (real run,
22-page paper, RTX 5000 Ada):

```
[2026-09-22 02:00:02] Found 1 paper(s) to process.
[2026-09-22 02:00:02] Processing 2026.06.07.729267v1.full.pdf...
[2026-09-22 02:00:07]     title: Promera: a unified model for biomolecular structure prediction, filtering, and design
[2026-09-22 02:00:07]     long paper — processing in 8 sections...
[2026-09-22 02:00:07]       section 1/8...
...
[2026-09-22 02:07:31]     ranking 24 candidate questions...
[2026-09-22 02:08:02]     verifying 12 question(s)...
[2026-09-22 02:12:40]       dropped: Why is the Promera nanobody design process considered challenging comp... (answer not supported: disagree: Answer A does not specify...)
[2026-09-22 02:12:49]     12 of 13 questions checked passed verification (24 generated).
[2026-09-22 02:12:49]   Done. Questions saved, PDF moved to library.
```

That leaves `questions/2026.06.07.729267v1.full_questions.md`, and the PDF
moves to `library/`. Roughly 13 minutes per paper.

**In the morning** — `quiz_me.py`:

```
Papers ready to review:

  1. Promera: a unified model for biomolecular structure prediction, filtering, and design  (12 questions)

  r. Review session: 3 question(s) you missed

Pick a number: 1

12 questions. Think through each one, hit Enter to see the answer and the
supporting quote, then mark yourself right or wrong.

Q2/12: Why does Promera incorporate masking as an integral part of its training pipeline?
(Enter when ready to see the answer, q to quit)

Answer: Promera incorporates masking to allow the model to be used for design by
leveraging the observation that co-folding models generate structured backbones
for masked residues.
Paper (p. 2): "Leveraging the observation that co-folding models generate structured
backbones for masked residues [11], we incorporate masking as an integral part of
our training pipeline to allow the model to be used for design."
Did you get it? (y/n): n

...

Score: 9/12

To review:
  - What limitation is noted in the comparison between Promera and BoltzGen designs?
  ...

3 question(s) in your review pile. Run `quiz_me.py --review` to go through them.
```

Press `q` at any prompt to stop; answers so far are saved. Questions you
miss come back with `quiz_me.py --review`, and drop out of the pile once
you get them right.

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
- `study_api.py` — JSON commands (`library`, `record`, `grade`,
  `process-inbox`) that the desktop app calls; the app never parses
  question files itself.
- `app/` — the Electron app: `main.js` (window, spawns `study_api.py`),
  `preload.js` (the only bridge), `renderer/` (UI).
- `DoTheReading.cmd` — double-click launcher for the app.
- `settings.py` — reads/writes `~/PaperStudy/settings.json` (model,
  question count, prompt guidance). Standard library only.
- `pipeline.spec` — PyInstaller recipe that freezes the pipeline for the
  installer.
- `.github/workflows/release.yml` — tag `v*` and a Windows runner builds
  the installer and attaches it to the release.
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

**Adding papers.** Drag a PDF onto the window, or paste a URL: web full
text is a much better input than a PDF (reading order, real headings, no
ligature or column damage, no parser crashes). bioRxiv/medRxiv and arXiv
links also fetch the PDF, to read alongside. Any article-shaped page works —
blogs, Substack — and articles chunk on their own headings.

On the same bioRxiv paper: the PDF yielded 8 verified questions of 43
checked; the URL yielded 12 of 12.

**Question ladder.** A study set walks from the whole paper down to its
details, rather than wherever the sections happened to lead:

| Level | Asks about | Share |
|---|---|---|
| `overview` | the problem, the contribution, the headline result | ~20% |
| `approach` | how it works, why built that way | ~35% |
| `evidence` | what was measured, what it shows | ~25% |
| `critique` | limitations the paper itself states | ~20% |

**Question pipeline** (`generate_questions` in `paper_qa_lib.py`):

0. *Read the paper as a whole*: a pass over its framing sections (abstract,
   introduction, discussion) asks what problem it solves, what it
   contributes and what's unresolved — the questions section-by-section
   generation never produces.
1. *Generate* ~1.5× `NUM_QUESTIONS` candidates across the sections, each
   with a verbatim `evidence` sentence from the text.
2. *Rank*: the model returns candidate IDs best-first, dropping
   near-duplicates and pushing trivia down (cheap: it doesn't rewrite them).
   Each level then gets its quota, and the set is ordered big picture first.
3. *Verify*, in rank order until `NUM_QUESTIONS` pass, a section at a time
   (its text dominates the prompt, so its questions are checked together).
   The model re-answers each **blind** from its source section (without
   seeing the original answer) and must quote the supporting sentence. The code
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
