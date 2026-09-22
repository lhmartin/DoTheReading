from datetime import datetime

from quiz_history import QuizHistory, question_key


def test_wrong_answers_enter_review_pile_until_answered_right(tmp_path):
    path = tmp_path / "quiz_history.json"
    h = QuizHistory(path)
    h.record("paperA", "Why?", correct=False, when=datetime(2026, 9, 1))
    h.record("paperB", "How?", correct=False, when=datetime(2026, 8, 1))
    h.record("paperA", "What?", correct=True)
    h.save()

    h = QuizHistory(path)  # survives a reload
    assert [e["question"] for e in h.review_pile()] == ["How?", "Why?"]  # longest-waiting first
    assert h.needs_review("paperA", "Why?") and not h.needs_review("paperA", "What?")

    h.record("paperA", "Why?", correct=True)
    assert [e["question"] for e in h.review_pile()] == ["How?"]
    assert len(h.questions[question_key("paperA", "Why?")]["attempts"]) == 2


def test_key_ignores_whitespace_and_punctuation():
    assert question_key("p", "Why  this method?") == question_key("p", "why this method")
    assert question_key("p", "Why?") != question_key("q", "Why?")


def test_missing_file_is_empty(tmp_path):
    assert QuizHistory(tmp_path / "nope.json").review_pile() == []
