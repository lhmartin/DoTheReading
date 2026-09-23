import json

import pytest

import paper_qa_lib
from paper_qa_lib import (find_page, generate_questions, page_index, quote_in_text, rank_candidates,
                          verify_question)

SECTION = (
    "--- Page 3 ---\nWe train on 1,605 complexes released in 2025. The sleep group re-\n"
    "called 23 percent more word pairs after twelve hours than the wake group.\n\n"
    "--- Page 4 ---\nA limitation is the small sample, and the wake group was tested in the evening."
)


@pytest.mark.parametrize("quote, expected", [
    ("The sleep group recalled 23 percent more word pairs after twelve hours", True),   # hyphenation + line break
    ("the SLEEP group recalled 23 percent more word-pairs, after twelve hours", True),  # case/punctuation
    ("The sleep group recalled 23 percent more word pairs after twelve days", True),    # small slip still >80%
    ("The sleep group recalled 40 percent fewer word pairs than anyone expected", False),
    ("small sample", False),                                                             # too short to prove anything
    ("", False),
])
def test_quote_in_text(quote, expected):
    assert quote_in_text(quote, SECTION) is expected


def test_find_page():
    pages = page_index(SECTION)
    assert find_page("the wake group was tested in the evening", pages) == 4
    assert find_page("The sleep group recalled 23 percent", pages) == 3
    assert find_page("not in the paper at all", pages) is None


class FakeModel:
    """Stands in for Ollama: replies are chosen by which schema was requested."""

    def __init__(self, replies):
        self.replies = {k: list(v) for k, v in replies.items()}
        self.prompts = []

    def __call__(self, prompt, model, fmt=None):
        self.prompts.append(prompt)
        kind = next(k for k, schema in SCHEMAS.items() if schema is fmt)
        return json.dumps(self.replies[kind].pop(0))


SCHEMAS = {
    "questions": paper_qa_lib.QUESTIONS_SCHEMA,
    "rank": paper_qa_lib.RANK_SCHEMA,
    "blind": paper_qa_lib.BLIND_ANSWER_SCHEMA,
    "judge": paper_qa_lib.JUDGE_SCHEMA,
}


def question(answer="23% more pairs.", evidence="The sleep group recalled 23 percent more word pairs"):
    return {"type": "comprehension", "question": "How much better did the sleep group do?",
            "answer": answer, "evidence": evidence, "chunk": 0}


def blind(answer="About 23 percent more.", quote="the wake group was tested in the evening", answerable=True, ids=(0,)):
    return {"answers": [{"id": i, "answerable": answerable, "answer": answer, "quote": quote} for i in ids]}


def judged(verdict="agree", reason="same", ids=(0,), **fields):
    return {"judgements": [{"id": i, "verdict": verdict, "reason": reason, **fields} for i in ids]}


@pytest.fixture
def fake(monkeypatch):
    def install(**replies):
        model = FakeModel(replies)
        monkeypatch.setattr(paper_qa_lib, "call_ollama", model)
        return model
    return install


def test_verify_passes_on_agreement_and_real_quote(fake):
    model = fake(blind=[blind()], judge=[judged()])
    q = question()
    assert verify_question(q, SECTION, "m", log=lambda m: None)
    assert q["evidence"] == "The sleep group recalled 23 percent more word pairs"
    prompt = model.prompts[0]
    assert prompt.index("TEXT:") < prompt.index("QUESTIONS:") < prompt.index("How much better"), \
        "the section text has to come first, so Ollama can reuse it across calls"
    assert "23% more pairs." not in prompt, "blind answer must not see the original answer"


def test_verify_fails_on_disagreement(fake):
    fake(blind=[blind(answer="They did worse.")], judge=[judged("disagree", "opposite")])
    q = question()
    assert not verify_question(q, SECTION, "m", log=lambda m: None)
    assert q["verification"].startswith("disagree")


def test_verify_uses_blind_quote_when_original_evidence_is_invented(fake):
    fake(blind=[blind()], judge=[judged("partial", "close")])
    q = question(evidence="Sleep is known to double memory in all adults.")
    assert verify_question(q, SECTION, "m", log=lambda m: None)
    assert q["evidence"] == "the wake group was tested in the evening"


def test_verify_fails_without_any_real_quote(fake):
    model = fake(blind=[blind(quote="Something the paper never says at all.")])
    q = question(evidence="Also invented, nowhere in the paper text.")
    assert not verify_question(q, SECTION, "m", log=lambda m: None)
    assert len(model.prompts) == 1, "shouldn't bother judging"


def test_verify_fails_when_not_answerable(fake):
    fake(blind=[blind(answerable=False)])
    assert not verify_question(question(), SECTION, "m", log=lambda m: None)


def test_rank_drops_duplicates_and_bad_ids(fake):
    fake(rank=[{"ranked_ids": [2, 0, 2, 99]}])
    cands = [{"type": "", "question": f"q{i}"} for i in range(3)]
    assert [c["question"] for c in rank_candidates(cands, 2, "m", log=lambda m: None)] == ["q2", "q0"]


def test_generate_replaces_failed_questions_and_keeps_rank_order(fake, monkeypatch):
    monkeypatch.setattr(paper_qa_lib, "chunk_text", lambda text: [SECTION])
    monkeypatch.setattr(paper_qa_lib, "framing_text", lambda chunks, limit=None: "")
    evidence = ["The sleep group recalled 23 percent more word pairs", "We train on 1,605 complexes released in 2025",
                "the wake group was tested in the evening", "A limitation is the small sample"]
    gen = [{"type": "comprehension", "question": f"Question {i}?", "answer": f"a{i}", "evidence": e}
           for i, e in enumerate(evidence)]
    fake(
        questions=[{"questions": gen}],
        rank=[{"ranked_ids": [3, 1, 0, 2]}],
        # First batch (q3, q1): q3 fails. Second batch (q0): passes.
        # batch 1: questions 3 and 1 from the same chunk; batch 2: question 0
        blind=[blind(ids=(0, 1)), blind(ids=(0,))],
        judge=[{"judgements": [{"id": 0, "verdict": "disagree", "reason": ""},
                               {"id": 1, "verdict": "agree", "reason": ""}]},
               judged()],
    )
    questions, summary = generate_questions(SECTION, "m", num_questions=2, log=lambda m: None)
    assert [q["question"] for q in questions] == ["Question 1?", "Question 0?"]
    assert all(q["page"] == 3 for q in questions)
    assert summary.startswith("2 of 3 questions checked passed")


def test_clean_quote():
    from paper_qa_lib import clean_quote
    assert clean_quote("insufficient ability to discrimi-\nnate binders\n from  nonbinders") == \
        "insufficient ability to discriminate binders from nonbinders"


def test_level_quota_adds_up_and_covers_every_rung():
    from paper_qa_lib import level_quota

    for total in (4, 8, 12, 20):
        quota = level_quota(total)
        assert sum(quota.values()) == total
        assert all(count >= 1 for count in quota.values()), "every rung gets at least one question"
    assert level_quota(12)["approach"] >= level_quota(12)["overview"]


def test_selection_fills_each_level_then_orders_big_picture_first():
    from paper_qa_lib import select_by_level

    ranked = ([{"type": "evidence", "question": f"e{i}"} for i in range(6)]
              + [{"type": "overview", "question": "o1"}, {"type": "approach", "question": "a1"},
                 {"type": "critique", "question": "c1"}])
    chosen = select_by_level(ranked, 4)[:4]
    assert [q["type"] for q in chosen] == ["overview", "approach", "evidence", "critique"], \
        "a set of four should walk from the whole paper down to a criticism"


def test_selection_falls_back_when_a_level_is_missing():
    from paper_qa_lib import select_by_level

    ranked = [{"type": "evidence", "question": f"e{i}"} for i in range(5)]
    chosen = select_by_level(ranked, 3)[:3]
    assert len(chosen) == 3 and all(q["type"] == "evidence" for q in chosen)


def test_judge_rejects_false_premise_or_unsupported_answer(fake):
    fake(blind=[blind(), blind()],
         judge=[judged("agree", "loaded", premise_ok=False, a_supported=True),
                judged("partial", "made up", premise_ok=True, a_supported=False)])
    q1, q2 = question(), question()
    assert not verify_question(q1, SECTION, "m", log=lambda m: None)
    assert q1["verification"].startswith("false premise")
    assert not verify_question(q2, SECTION, "m", log=lambda m: None)
    assert q2["verification"].startswith("answer not supported")


def test_generate_skips_questions_with_duplicate_evidence(fake, monkeypatch):
    monkeypatch.setattr(paper_qa_lib, "chunk_text", lambda text: [SECTION])
    monkeypatch.setattr(paper_qa_lib, "framing_text", lambda chunks, limit=None: "")
    same = "The sleep group recalled 23 percent more word pairs"
    gen = [{"type": "evidence", "question": f"Question {i}?", "answer": f"a{i}", "evidence": same}
           for i in range(2)]
    gen.append({"type": "critique", "question": "Question 2?", "answer": "a2",
                "evidence": "the wake group was tested in the evening"})
    # Q1 is dropped as a duplicate, so a second batch checks Q2 as its replacement.
    fake(questions=[{"questions": gen}], rank=[{"ranked_ids": [0, 1, 2]}],
         blind=[blind(ids=(0, 1)), blind(ids=(0,))], judge=[judged(ids=(0, 1)), judged()])
    questions, _ = generate_questions(SECTION, "m", num_questions=2, log=lambda m: None)
    assert [q["question"] for q in questions] == ["Question 0?", "Question 2?"]


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.exceptions.HTTPError(response=self)


def test_missing_model_gives_a_plain_message(monkeypatch):
    monkeypatch.setattr(paper_qa_lib.requests, "post",
                        lambda *a, **k: FakeResponse(404, {"error": "model 'x' not found"}))
    with pytest.raises(SystemExit) as exit_info:
        paper_qa_lib.call_ollama("prompt", "qwen2.5:32b-instruct-q3_K_S")
    message = str(exit_info.value)
    assert "doesn't have the model 'qwen2.5:32b-instruct-q3_K_S'" in message
    assert "ollama pull qwen2.5:32b-instruct-q3_K_S" in message
    assert "404" not in message, "the HTTP status is noise for the reader"


def test_other_http_errors_still_surface(monkeypatch):
    monkeypatch.setattr(paper_qa_lib.requests, "post",
                        lambda *a, **k: FakeResponse(500, {"error": "out of memory"}))
    with pytest.raises(SystemExit) as exit_info:
        paper_qa_lib.call_ollama("prompt", "m")
    assert "HTTP 500" in str(exit_info.value) and "out of memory" in str(exit_info.value)


@pytest.mark.parametrize("installed, model, expected", [
    (["qwen2.5:14b"], "qwen2.5:14b", None),
    (["qwen2.5:14b:latest"], "qwen2.5:14b", None),
    ([], "qwen2.5:14b", "doesn't have the model"),
    (["other:7b"], "qwen2.5:14b", "installed: other:7b"),
])
def test_check_model(monkeypatch, installed, model, expected):
    monkeypatch.setattr(paper_qa_lib, "installed_models", lambda: installed)
    problem = paper_qa_lib.check_model(model)
    assert problem is None if expected is None else expected in problem


def test_check_model_when_ollama_is_down(monkeypatch):
    monkeypatch.setattr(paper_qa_lib, "installed_models", lambda: None)
    assert "Could not reach Ollama" in paper_qa_lib.check_model("any")


def test_a_fallback_quote_brings_its_own_answer(fake):
    """If the question's own evidence isn't in the text, the quote shown comes
    from the blind answer — so the answer shown must too, or the pair can
    contradict each other."""
    fake(blind=[blind(answer="The second receptor was monitored but not optimised.",
                      quote="the wake group was tested in the evening")],
         judge=[judged("agree", "same claim")])
    q = question(answer="Single-target campaigns had lower pass rates.",
                 evidence="A sentence that appears nowhere in the paper at all.")
    assert verify_question(q, SECTION, "m", log=lambda m: None)
    assert q["evidence"] == "the wake group was tested in the evening"
    assert q["answer"] == "The second receptor was monitored but not optimised.", \
        "answer and quote must come from the same attempt"


def test_a_verified_own_quote_keeps_the_original_answer(fake):
    fake(blind=[blind(answer="Something else entirely.")], judge=[judged()])
    q = question(answer="23% more pairs.", evidence="The sleep group recalled 23 percent more word pairs")
    assert verify_question(q, SECTION, "m", log=lambda m: None)
    assert q["answer"] == "23% more pairs."


def test_generate_opens_with_whole_paper_questions(fake, monkeypatch):
    """The first pass asks about the paper as a whole, drawn from its framing
    sections — the questions section-by-section generation never produces."""
    discussion = ("## Discussion\n\nTaken together, sleep consolidates memory of word pairs "
                  "learned earlier in the day.")
    monkeypatch.setattr(paper_qa_lib, "chunk_text", lambda text: [SECTION, discussion])
    overview = {"type": "overview", "question": "What problem does this solve?",
                "answer": "Whether sleep consolidates memory.",
                "evidence": "Taken together, sleep consolidates memory of word pairs learned earlier in the day."}
    section = {"type": "evidence", "question": "How much better did they do?", "answer": "23 percent.",
               "evidence": "The sleep group recalled 23 percent more word pairs"}
    model = fake(
        questions=[{"questions": [overview]}, {"questions": [section]}, {"questions": [section]}],
        rank=[{"ranked_ids": [0, 1]}],
        # the overview batch is checked first, then the section batch
        blind=[blind(answer="Whether sleep consolidates memory.",
                     quote="Taken together, sleep consolidates memory of word pairs learned earlier in the day."),
               blind()],
        judge=[judged(), judged()],
    )
    questions, _ = paper_qa_lib.generate_questions(SECTION, "m", 2, log=lambda m: None)
    assert "AS A WHOLE" in model.prompts[0], "the first call asks about the paper, not a section"
    assert [q["type"] for q in questions] == ["overview", "evidence"], "big picture comes first"
