#!/usr/bin/env python3
"""
study_api.py

JSON API over the study library, for the desktop app (app/) to call. The app
stays a thin UI: question files, quiz history and model prompts stay here, so
there is only one implementation of each.

Every command prints one JSON object on stdout.

    python study_api.py library
    python study_api.py record --paper <stem> --question <text> --correct 1
    python study_api.py grade  --paper <stem> --question <text> --answer <yours>
    python study_api.py process-inbox        # streams log lines, then a summary

`--base` (or PAPERSTUDY_DIR) overrides ~/PaperStudy, for testing.
"""

import argparse
import json
import os
import random
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from qa_format import parse_markdown, parse_title
from quiz_history import QuizHistory, question_key


def base_dir(args) -> Path:
    return Path(args.base or os.environ.get("PAPERSTUDY_DIR") or (Path.home() / "PaperStudy"))


def paper_stem(md_path: Path) -> str:
    return md_path.stem.removesuffix("_questions")


def load_papers(base: Path, history: QuizHistory) -> list[dict]:
    papers = []
    for md_path in sorted((base / "questions").glob("*_questions.md"), key=lambda f: f.stat().st_mtime, reverse=True):
        stem = paper_stem(md_path)
        md_text = md_path.read_text(encoding="utf-8")
        pdf_path = base / "library" / f"{stem}.pdf"
        questions = []
        for q in parse_markdown(md_text):
            entry = history.questions.get(question_key(stem, q["question"]), {})
            attempts = entry.get("attempts", [])
            questions.append({
                **q,
                "key": question_key(stem, q["question"]),
                "attempts": attempts,
                "needs_review": bool(attempts) and not attempts[-1]["correct"],
                "last_seen": attempts[-1]["at"] if attempts else None,
            })
        seen = [q for q in questions if q["attempts"]]
        papers.append({
            "stem": stem,
            "title": parse_title(md_text) or stem,
            "pdf": str(pdf_path) if pdf_path.exists() else None,
            "added": datetime.fromtimestamp(md_path.stat().st_mtime).isoformat(timespec="seconds"),
            "questions": questions,
            "counts": {
                "total": len(questions),
                "seen": len(seen),
                "correct": sum(1 for q in seen if q["attempts"][-1]["correct"]),
                "review": sum(1 for q in questions if q["needs_review"]),
            },
            "last_studied": max((q["last_seen"] for q in seen), default=None),
        })
    return papers


def suggest(papers: list[dict]) -> str | None:
    """Which paper to open today: something never studied (at random), else
    whatever was studied longest ago."""
    if not papers:
        return None
    fresh = [p for p in papers if not p["counts"]["seen"]]
    if fresh:
        return random.choice(fresh)["stem"]
    return min(papers, key=lambda p: p["last_studied"] or "")["stem"]


def overall_stats(papers: list[dict], history: QuizHistory) -> dict:
    attempts = [(a, e) for e in history.questions.values() for a in e["attempts"]]
    by_day = {}
    for attempt, _ in attempts:
        day = by_day.setdefault(attempt["at"][:10], {"date": attempt["at"][:10], "answered": 0, "correct": 0})
        day["answered"] += 1
        day["correct"] += bool(attempt["correct"])
    return {
        "papers": len(papers),
        "papers_studied": sum(1 for p in papers if p["counts"]["seen"]),
        "questions": sum(p["counts"]["total"] for p in papers),
        "answered": len(attempts),
        "correct": sum(1 for a, _ in attempts if a["correct"]),
        "review_pile": len(history.review_pile()),
        "by_day": sorted(by_day.values(), key=lambda d: d["date"]),
        "today": date.today().isoformat(),
    }


def cmd_library(args) -> dict:
    base = base_dir(args)
    history = QuizHistory(base / "quiz_history.json")
    papers = load_papers(base, history)
    inbox = sorted(p.name for p in (base / "inbox").glob("*.pdf")) if (base / "inbox").is_dir() else []
    review = [{"paper": e["paper"], "question": e["question"], "key": question_key(e["paper"], e["question"])}
              for e in history.review_pile()]
    return {
        "base": str(base),
        "papers": papers,
        "inbox": inbox,
        "review": review,
        "suggested": suggest(papers),
        "stats": overall_stats(papers, history),
    }


def cmd_record(args) -> dict:
    base = base_dir(args)
    history = QuizHistory(base / "quiz_history.json")
    history.record(args.paper, args.question, bool(int(args.correct)))
    history.save()
    return {"ok": True, "key": question_key(args.paper, args.question),
            "review_pile": len(history.review_pile())}


GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partly", "incorrect"]},
        "feedback": {"type": "string"},
    },
    "required": ["verdict", "feedback"],
}


def cmd_grade(args) -> dict:
    # Imported here: only grading needs the pipeline's heavier dependencies.
    import paper_qa_lib
    import process_inbox
    import textwrap

    prompt = textwrap.dedent(f"""\
        You are marking a researcher's recall of a paper they studied.

        QUESTION: {args.question}
        REFERENCE ANSWER: {args.reference}
        SUPPORTING QUOTE FROM THE PAPER: {args.evidence}

        THEIR ANSWER: {args.answer}

        Mark their answer against the reference:
        - "correct": the same core claim, even if worded differently or briefer.
        - "partly": right as far as it goes, but missing something important.
        - "incorrect": contradicts the reference, or misses the point.

        Then write one short sentence of feedback, addressed to them ("you"),
        saying what they got right or what they missed. Don't repeat the
        reference answer verbatim.

        Respond with JSON only:
        {{"verdict": "correct" | "partly" | "incorrect", "feedback": "<one sentence>"}}
    """)
    graded = paper_qa_lib.ask_json(prompt, args.model or process_inbox.MODEL, GRADE_SCHEMA, log=lambda m: None)
    return {"verdict": graded.get("verdict", "partly"), "feedback": graded.get("feedback", "")}


def cmd_process_inbox(args) -> dict:
    """Run the nightly job now, streaming its log lines as JSON objects."""
    import process_inbox

    base = base_dir(args)
    process_inbox.BASE_DIR = base
    process_inbox.INBOX_DIR = base / "inbox"
    process_inbox.LIBRARY_DIR = base / "library"
    process_inbox.QUESTIONS_DIR = base / "questions"
    process_inbox.LOG_FILE = base / "process_log.txt"

    original_log = process_inbox.log

    def streaming_log(message: str):
        original_log(message)
        print(json.dumps({"log": message}), flush=True)

    process_inbox.log = streaming_log
    try:
        process_inbox.main()
        return {"ok": True}
    except SystemExit as e:  # e.g. Ollama not running
        return {"ok": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="PaperStudy folder (default: ~/PaperStudy)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("library")

    record = sub.add_parser("record")
    record.add_argument("--paper", required=True)
    record.add_argument("--question", required=True)
    record.add_argument("--correct", required=True, choices=["0", "1"])

    grade = sub.add_parser("grade")
    for name in ("paper", "question", "reference", "answer"):
        grade.add_argument(f"--{name}", required=True)
    grade.add_argument("--evidence", default="")
    grade.add_argument("--model", default="")

    sub.add_parser("process-inbox")

    args = parser.parse_args()
    commands = {"library": cmd_library, "record": cmd_record, "grade": cmd_grade,
                "process-inbox": cmd_process_inbox}
    try:
        result = commands[args.command](args)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), flush=True)
        raise SystemExit(1)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
