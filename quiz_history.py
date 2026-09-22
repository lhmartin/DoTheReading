"""Remembers how you did on each question, so missed ones can be reviewed.

Stored as JSON (quiz_history.json in ~/PaperStudy). A question is in the
review pile if the last attempt on it was wrong — in a normal quiz or a
review session. Stdlib only, like quiz_me.py.
"""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from qa_format import normalize_for_match


def question_key(paper: str, question: str) -> str:
    digest = hashlib.sha1(normalize_for_match(question).encode()).hexdigest()[:12]
    return f"{paper}::{digest}"


class QuizHistory:
    def __init__(self, path: Path):
        self.path = path
        self.questions = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.questions = data.get("questions", {})

    def record(self, paper: str, question: str, correct: bool, when: datetime | None = None):
        entry = self.questions.setdefault(question_key(paper, question),
                                          {"paper": paper, "question": question, "attempts": []})
        entry["attempts"].append({"at": (when or datetime.now()).isoformat(timespec="seconds"),
                                  "correct": correct})

    def needs_review(self, paper: str, question: str) -> bool:
        entry = self.questions.get(question_key(paper, question))
        return bool(entry and entry["attempts"]) and not entry["attempts"][-1]["correct"]

    def review_pile(self) -> list[dict]:
        """Entries you got wrong the last time you saw them, longest-waiting
        first."""
        pile = [e for e in self.questions.values() if e["attempts"] and not e["attempts"][-1]["correct"]]
        return sorted(pile, key=lambda e: e["attempts"][-1]["at"])

    def save(self):
        # Write to a temp file and swap it in, so a crash can't corrupt it.
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"version": 1, "questions": self.questions}, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
