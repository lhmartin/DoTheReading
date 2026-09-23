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
from qa_format import render_markdown

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


def log(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> str | None:
    """Returns None when the run completed, or a reason it couldn't start."""
    config = settings.load()
    log_config = f"model {config['model']}, {config['num_questions']} questions"
    for d in (INBOX_DIR, LIBRARY_DIR, QUESTIONS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(p for p in INBOX_DIR.iterdir() if p.suffix.lower() in (".pdf", ".html", ".htm"))
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
            text = extract_any(str(pdf_path), log=log)
            if not text.strip():
                log(f"  WARNING: no extractable text in {pdf_path.name}, even after OCR. Skipping.")
                continue

            # A saved article already carries its title as the first heading.
            title = (text.split("\n\n")[0].removeprefix("## ").strip() if pdf_path.suffix.lower() != ".pdf"
                     else find_title(str(pdf_path), text, config["model"], log=log)) or pdf_path.stem
            log(f"    title: {title}")
            progress = Checkpoint(pdf_path.stem, text)
            questions, summary = generate_questions(text, config["model"], config["num_questions"],
                                                    log=log, guidance=config["guidance"],
                                                    checkpoint=progress)
            if not questions:
                log(f"  WARNING: no questions passed verification for {pdf_path.name}. Leaving it in the inbox.")
                continue
            note = f"{pdf_path.name} · {summary}"
            questions_path.write_text(render_markdown(title, questions, note=note), encoding="utf-8")

            progress.clear()
            shutil.move(str(pdf_path), str(LIBRARY_DIR / pdf_path.name))
            log(f"  Done. Questions saved, PDF moved to library.")
        except Exception:
            log(f"  ERROR processing {pdf_path.name}:\n{traceback.format_exc()}")
            # Leave the PDF in the inbox so it gets retried next run.
            continue

    log("Run complete.\n")


if __name__ == "__main__":
    main()
