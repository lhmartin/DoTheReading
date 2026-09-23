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


def test_entry_without_attempts_is_ignored(tmp_path):
    path = tmp_path / "quiz_history.json"
    path.write_text('{"version": 1, "questions": {"p::abc": {"paper": "p", "question": "Q?", "attempts": []}}}')
    h = QuizHistory(path)
    assert h.review_pile() == []
    assert not h.needs_review("p", "Q?")


def test_marking_a_paper_read(tmp_path):
    from datetime import datetime

    path = tmp_path / "quiz_history.json"
    h = QuizHistory(path)
    assert h.read_at("paper") is None

    h.mark_read("paper", when=datetime(2026, 9, 23, 9, 30))
    h.save()
    assert QuizHistory(path).read_at("paper") == "2026-09-23T09:30:00"

    h.mark_read("paper", read=False)
    h.save()
    assert QuizHistory(path).read_at("paper") is None


def test_read_state_survives_alongside_question_history(tmp_path):
    path = tmp_path / "quiz_history.json"
    h = QuizHistory(path)
    h.record("paper", "Why?", correct=True)
    h.mark_read("paper")
    h.save()

    reloaded = QuizHistory(path)
    assert reloaded.read_at("paper") and not reloaded.review_pile()


def test_reading_days_groups_titles_and_answers(tmp_path):
    from datetime import datetime

    import study_api

    history = QuizHistory(tmp_path / "quiz_history.json")
    history.mark_read("paper-a", when=datetime(2026, 9, 20, 8, 0))
    history.mark_read("paper-b", when=datetime(2026, 9, 20, 21, 0))
    history.record("paper-a", "Why?", correct=True, when=datetime(2026, 9, 20, 9, 0))
    history.record("paper-a", "How?", correct=False, when=datetime(2026, 9, 21, 9, 0))

    papers = [{"stem": "paper-a", "title": "Paper A"}, {"stem": "paper-b", "title": "Paper B"}]
    days = {day["date"]: day for day in study_api.reading_days(papers, history, days=3650)}

    assert sorted(days["2026-09-20"]["read"]) == ["Paper A", "Paper B"]
    assert days["2026-09-20"]["answered"] == 1 and days["2026-09-20"]["correct"] == 1
    assert days["2026-09-21"]["read"] == [] and days["2026-09-21"]["answered"] == 1


def test_reading_days_only_covers_the_recent_window(tmp_path):
    from datetime import datetime

    import study_api

    history = QuizHistory(tmp_path / "quiz_history.json")
    history.mark_read("ancient", when=datetime(2020, 1, 1))
    assert study_api.reading_days([{"stem": "ancient", "title": "Old"}], history, days=30) == []
