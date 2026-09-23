#!/usr/bin/env python3
"""
process_inbox.py

Run this nightly (via Windows Task Scheduler). It looks in INBOX_DIR for
any PDFs that don't have questions generated yet, processes each one with
a local Ollama model, saves the questions as markdown in QUESTIONS_DIR,
and moves the source PDF into LIBRARY_DIR so it isn't reprocessed.

Designed to be safe to run every night: already-processed papers are
skipped automatically, and one bad/corrupt PDF won't stop the rest.

Edit the CONFIG block below to match your folder layout, then point
Task Scheduler at this script.
"""

import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import settings
from checkpoint import Checkpoint
from paper_qa_lib import check_model, extract_any, find_title, generate_questions
from qa_format import read_notes, render_markdown, write_notes

# ---- CONFIG -----------------------------------------------------------
# Folders. The model, how many questions to keep, and any prompt guidance
# live in ~/PaperStudy/settings.json — change them in the app, or by hand
# (see settings.py for the defaults).
BASE_DIR = settings.base_dir()
INBOX_DIR = BASE_DIR / "inbox"          # drop new PDFs here during the day
LIBRARY_DIR = BASE_DIR / "library"      # processed PDFs get moved here
QUESTIONS_DIR = BASE_DIR / "questions"  # generated .md question sets land here
LOG_FILE = BASE_DIR / "process_log.txt"
# ------------------------------------------------------------------------


def render_keeping_notes(questions_path: Path, title: str, questions: list, note: str) -> str:
    """The question file's new contents, carrying over any notes the reader
    wrote: they live in the same file, and a rewrite must not eat them."""
    rendered = render_markdown(title, questions, note=note)
    if questions_path.exists():
        return write_notes(rendered, read_notes(questions_path.read_text(encoding="utf-8")))
    return rendered


def log(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_questions_for(source: Path, config: dict, log=print) -> str | None:
    """Read one paper and write its question file. Returns None on success,
    or a reason it couldn't be done. Used by the nightly run and by the app
    when questions are asked for after the fact."""
    QUESTIONS_DIR.mkdir(parents=True, exist_ok=True)
    questions_path = QUESTIONS_DIR / (source.stem + "_questions.md")

    text = extract_any(str(source), log=log)
    if not text.strip():
        return f"no extractable text in {source.name}, even after OCR"

    title = (text.split("\n\n")[0].removeprefix("## ").strip() if source.suffix.lower() != ".pdf"
             else find_title(str(source), text, config["model"], log=log)) or source.stem
    log(f"    title: {title}")

    progress = Checkpoint(source.stem, text)
    questions, summary = generate_questions(text, config["model"], config["num_questions"],
                                            log=log, guidance=config["guidance"], checkpoint=progress)
    if not questions:
        return f"no questions passed verification for {source.name}"

    questions_path.write_text(
        render_keeping_notes(questions_path, title, questions, f"{source.name} · {summary}"),
        encoding="utf-8")
    progress.clear()
    return None


def main(only: list[str] | None = None) -> str | None:
    """Process the inbox. `only` limits the run to those file names.

    Returns None when the run completed, or a reason it couldn't start.
    """
    config = settings.load()
    log_config = f"model {config['model']}, {config['num_questions']} questions"
    for d in (INBOX_DIR, LIBRARY_DIR, QUESTIONS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(p for p in INBOX_DIR.iterdir() if p.suffix.lower() in (".pdf", ".html", ".htm"))
    if only:
        pdfs = [p for p in pdfs if p.name in set(only)]
    if not pdfs:
        log("No new papers in inbox. Nothing to do.")
        return

    # Fail before touching any paper, rather than after minutes of work.
    problem = check_model(config["model"])
    if problem:
        log(f"Can't start: {problem}")
        return problem

    log(f"Found {len(pdfs)} paper(s) to process. Using {log_config}.")

    for pdf_path in pdfs:
        questions_path = QUESTIONS_DIR / (pdf_path.stem + "_questions.md")
        if questions_path.exists():
            log(f"Skipping {pdf_path.name} — questions already exist.")
            continue

        log(f"Processing {pdf_path.name}...")
        try:
            problem = write_questions_for(pdf_path, config, log=log)
            if problem:
                log(f"  WARNING: {problem}. Leaving it in the inbox.")
                continue

            shutil.move(str(pdf_path), str(LIBRARY_DIR / pdf_path.name))
            log(f"  Done. Questions saved, PDF moved to library.")
        except Exception:
            log(f"  ERROR processing {pdf_path.name}:\n{traceback.format_exc()}")
            # Leave the PDF in the inbox so it gets retried next run.
            continue

    log("Run complete.\n")


if __name__ == "__main__":
    main()
