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
    python study_api.py add-papers --files a.pdf b.pdf
    python study_api.py process-inbox        # streams log lines, then a summary

`--base` (or PAPERSTUDY_DIR) overrides ~/PaperStudy, for testing.
"""

import argparse
import json
import os
import platform
import random
import shutil
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import settings
from qa_format import parse_markdown, parse_title
from quiz_history import QuizHistory, question_key

OLLAMA_HOST = "http://localhost:11434"
TASK_NAME = "PaperStudyNightly"

# Models worth offering, largest first. Verification quality tracks the
# model's reading: see `quality` — surfaced in the app so a smaller pick is
# an informed one.
SUGGESTED_MODELS = [
    {"name": "qwen2.5:32b-instruct-q4_K_M", "size": "19 GB", "quality": "best",
     "note": "Sharpest questions. Too big for 16GB VRAM, so ~3x slower (~35 min/paper)."},
    {"name": "qwen2.5:32b-instruct-q3_K_S", "size": "14 GB", "quality": "best",
     "note": "Default. Fits in 16GB VRAM; same verification accuracy as q4_K_M in testing (~13 min/paper)."},
    {"name": "qwen2.5:14b", "size": "9 GB", "quality": "weaker",
     "note": "~10x faster, but it misread a passage and kept an answer that contradicted the paper in 3/3 trials."},
    {"name": "qwen2.5:7b", "size": "4.7 GB", "quality": "weakest",
     "note": "For low-VRAM machines. Expect shallower questions and less reliable checking."},
]


def base_dir(args) -> Path:
    if args.base:
        os.environ["PAPERSTUDY_DIR"] = args.base  # so settings.py agrees
    return settings.base_dir()


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
    graded = paper_qa_lib.ask_json(prompt, args.model or settings.load()["model"], GRADE_SCHEMA, log=lambda m: None)
    return {"verdict": graded.get("verdict", "partly"), "feedback": graded.get("feedback", "")}


def cmd_process_inbox(args) -> dict:
    """Run the nightly job now, streaming its log lines as JSON objects."""
    base_dir(args)
    import process_inbox

    original_log = process_inbox.log

    def streaming_log(message: str):
        original_log(message)
        print(json.dumps({"log": message}), flush=True)

    process_inbox.log = streaming_log
    try:
        problem = process_inbox.main()
        return {"ok": not problem, "error": problem or ""}
    except SystemExit as e:  # e.g. Ollama stopped mid-run
        return {"ok": False, "error": str(e)}


def add_papers(base: Path, paths: list[str]) -> dict:
    """Copy PDFs into the inbox, skipping ones already known.

    Returns what happened to each file, so the app can say so.
    """
    inbox = base / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    added, skipped = [], []
    for raw in paths:
        source = Path(raw)
        if source.suffix.lower() != ".pdf" or not source.is_file():
            skipped.append({"name": source.name, "why": "not a PDF"})
            continue
        if (inbox / source.name).exists():
            skipped.append({"name": source.name, "why": "already in the inbox"})
            continue
        if (base / "library" / source.name).exists():
            skipped.append({"name": source.name, "why": "already processed"})
            continue
        try:
            shutil.copy2(source, inbox / source.name)
        except OSError as e:
            skipped.append({"name": source.name, "why": str(e)})
            continue
        added.append(source.name)
    return {"added": added, "skipped": skipped}


def cmd_add_papers(args) -> dict:
    return add_papers(base_dir(args), args.files)


def ollama_get(path: str, timeout: float = 3.0):
    import requests

    return requests.get(f"{OLLAMA_HOST}{path}", timeout=timeout).json()


def cmd_settings(args) -> dict:
    base_dir(args)
    return {"settings": settings.load(), "defaults": settings.DEFAULTS, "path": str(settings.settings_path())}


def cmd_save_settings(args) -> dict:
    base_dir(args)
    changes = {}
    if args.model:
        changes["model"] = args.model
    if args.num_questions:
        changes["num_questions"] = int(args.num_questions)
    if args.guidance is not None:
        changes["guidance"] = args.guidance
    return {"settings": settings.save(changes)}


def cmd_environment(args) -> dict:
    """What the pipeline needs, and whether it's there: for the app's setup
    checklist."""
    base_dir(args)
    current = settings.load()
    installed, running, version = [], False, None
    try:
        version = ollama_get("/api/version").get("version")
        running = True
        installed = [{"name": m["name"], "size_bytes": m.get("size")} for m in ollama_get("/api/tags").get("models", [])]
    except Exception:
        pass

    tesseract = None
    try:
        import paper_qa_lib

        tesseract = paper_qa_lib.ocr_available()
    except Exception:
        pass

    return {
        "ollama": {"running": running, "version": version},
        "models": {
            "installed": installed,
            "suggested": SUGGESTED_MODELS,
            "selected": current["model"],
            "selected_installed": any(m["name"] == current["model"] for m in installed),
        },
        "tesseract": tesseract,
        "scheduled_task": scheduled_task_state(),
        "settings": current,
        "base": str(settings.base_dir()),
    }


def cmd_pull_model(args) -> dict:
    """Pull a model, streaming Ollama's progress as {"log": ...} lines."""
    import requests

    seen = None
    with requests.post(f"{OLLAMA_HOST}/api/pull", json={"model": args.model}, stream=True, timeout=None) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            update = json.loads(line)
            if update.get("error"):
                return {"ok": False, "error": update["error"]}
            status = update.get("status", "")
            total, completed = update.get("total"), update.get("completed")
            if total and completed:
                percent = int(completed / total * 100)
                status = f"{status} — {percent}% of {total / 1e9:.1f} GB"
            if status != seen:
                seen = status
                print(json.dumps({"log": status}), flush=True)
    return {"ok": True, "model": args.model}


# ---- the nightly scheduled task (Windows) --------------------------------

def scheduled_task_state() -> dict:
    if platform.system() != "Windows":
        return {"supported": False, "registered": False}
    try:
        found = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME], capture_output=True, text=True)
        return {"supported": True, "registered": found.returncode == 0, "name": TASK_NAME}
    except OSError:
        return {"supported": False, "registered": False}


def cmd_schedule(args) -> dict:
    """Register or remove the nightly task, from the app's settings."""
    if platform.system() != "Windows":
        return {"ok": False, "error": "Scheduling is Windows-only."}

    if args.action == "remove":
        done = subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], capture_output=True, text=True)
        return {"ok": done.returncode == 0, "error": done.stderr.strip(), "state": scheduled_task_state()}

    # Run this same executable (frozen study_api.exe, or python study_api.py).
    if getattr(sys, "frozen", False):
        command = f'"{sys.executable}" process-inbox'
    else:
        command = f'"{sys.executable}" "{Path(__file__).resolve()}" process-inbox'
    script = (
        f"$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c {command}';"
        f"$trigger = New-ScheduledTaskTrigger -Daily -At {args.time};"
        "$settings = New-ScheduledTaskSettingsSet -WakeToRun;"
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $trigger "
        "-Settings $settings -Description 'Generate study questions for new PDFs' -Force | Out-Null"
    )
    done = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    return {"ok": done.returncode == 0, "error": done.stderr.strip(), "state": scheduled_task_state()}


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
    sub.add_parser("settings")
    sub.add_parser("environment")

    save_settings = sub.add_parser("save-settings")
    save_settings.add_argument("--model")
    save_settings.add_argument("--num-questions", dest="num_questions")
    save_settings.add_argument("--guidance")

    add = sub.add_parser("add-papers")
    add.add_argument("--files", nargs="+", required=True)

    pull = sub.add_parser("pull-model")
    pull.add_argument("--model", required=True)

    schedule = sub.add_parser("schedule")
    schedule.add_argument("--action", required=True, choices=["add", "remove"])
    schedule.add_argument("--time", default="02:00")

    args = parser.parse_args()
    commands = {"library": cmd_library, "record": cmd_record, "grade": cmd_grade,
                "process-inbox": cmd_process_inbox, "settings": cmd_settings,
                "save-settings": cmd_save_settings, "environment": cmd_environment,
                "pull-model": cmd_pull_model, "schedule": cmd_schedule,
                "add-papers": cmd_add_papers}
    try:
        result = commands[args.command](args)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), flush=True)
        raise SystemExit(1)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
