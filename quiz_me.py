#!/usr/bin/env python3
"""
quiz_me.py

Run this in the morning. It lists papers that have been processed
overnight and lets you pick one to be quizzed on, one question at a time.
After each answer you see the supporting quote from the paper and mark
yourself right or wrong. Questions you get wrong go into a review pile.

Usage:
    python quiz_me.py            # pick a paper (or the review session)
    python quiz_me.py --review   # go straight to the questions you missed
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from qa_format import parse_markdown, parse_title
from quiz_history import QuizHistory, question_key

BASE_DIR = Path.home() / "PaperStudy"
QUESTIONS_DIR = BASE_DIR / "questions"
HISTORY_FILE = BASE_DIR / "quiz_history.json"


class Quit(Exception):
    pass


def ask(prompt: str) -> str:
    reply = input(prompt).strip().lower()
    if reply == "q":
        raise Quit
    return reply


def paper_name(md_path: Path) -> str:
    return md_path.stem.removesuffix("_questions")


def load_paper(md_path: Path) -> tuple[str, list[dict]]:
    md_text = md_path.read_text(encoding="utf-8")
    return parse_title(md_text) or paper_name(md_path), parse_markdown(md_text)


def ask_question(label: str, item: dict) -> bool:
    print(f"\n{label}: {item['question']}")
    ask("(Enter when ready to see the answer, q to quit) ")
    print(f"\nAnswer: {item['answer']}")
    if item.get("evidence"):
        page = f" (p. {item['page']})" if item.get("page") else ""
        print(f"Paper{page}: \"{item['evidence']}\"")
    while True:
        got_it = ask("Did you get it? (y/n): ")
        if got_it in ("y", "n"):
            return got_it == "y"


def run_session(items: list[tuple[str, str, dict]], history: QuizHistory):
    """items: (paper, display title, question dict). Records every answer."""
    correct, missed = 0, []
    for i, (paper, title, item) in enumerate(items, 1):
        label = f"Q{i}/{len(items)}" + (f" [{title}]" if len({p for p, _, _ in items}) > 1 else "")
        got_it = ask_question(label, item)
        history.record(paper, item["question"], got_it)
        history.save()
        if got_it:
            correct += 1
        else:
            missed.append(item["question"])

    print(f"\nScore: {correct}/{len(items)}")
    if missed:
        print("\nTo review:")
        for q in missed:
            print(f"  - {q}")
    pile = len(history.review_pile())
    if pile:
        print(f"\n{pile} question(s) in your review pile. Run `quiz_me.py --review` to go through them.")


def review_items(history: QuizHistory) -> list[tuple[str, str, dict]]:
    """Missed questions, looked up in their current question files."""
    items, papers = [], {}
    for entry in history.review_pile():
        paper = entry["paper"]
        if paper not in papers:
            md_path = QUESTIONS_DIR / f"{paper}_questions.md"
            papers[paper] = load_paper(md_path) if md_path.exists() else (paper, [])
        title, questions = papers[paper]
        wanted = question_key(paper, entry["question"])
        match = next((q for q in questions if question_key(paper, q["question"]) == wanted), None)
        if match:
            items.append((paper, title, match))
    return items


def choose(history: QuizHistory):
    """Returns a list of session items, or None."""
    files = sorted(QUESTIONS_DIR.glob("*_questions.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        print("No processed papers found yet. Run process_inbox.py first (or wait for the overnight job).")
        return None

    papers = [(f, *load_paper(f)) for f in files]
    print("\nPapers ready to review:\n")
    for i, (f, title, questions) in enumerate(papers, 1):
        missed = sum(history.needs_review(paper_name(f), q["question"]) for q in questions)
        extra = f", {missed} to review" if missed else ""
        print(f"  {i}. {title}  ({len(questions)} questions{extra})")
    pile = len(history.review_pile())
    if pile:
        print(f"\n  r. Review session: {pile} question(s) you missed")

    choice = ask("\nPick a number: ")
    if choice == "r" and pile:
        return review_items(history)
    try:
        f, title, questions = papers[int(choice) - 1]
    except (ValueError, IndexError):
        print("Not a valid choice.")
        return None
    return [(paper_name(f), title, q) for q in questions]


def main():
    history = QuizHistory(HISTORY_FILE)
    try:
        items = review_items(history) if "--review" in sys.argv[1:] else choose(history)
        if items == []:
            print("Nothing to review. Nice.")
        elif items:
            print(f"\n{len(items)} questions. Think through each one, hit Enter to see the answer "
                  f"and the supporting quote, then mark yourself right or wrong.")
            run_session(items, history)
    except (Quit, KeyboardInterrupt, EOFError):
        print("\nStopped. Answers so far are saved.")


if __name__ == "__main__":
    main()
