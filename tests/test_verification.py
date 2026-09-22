import json

import pytest

import paper_qa_lib
from paper_qa_lib import find_page, generate_questions, quote_in_text, rank_candidates, verify_question

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
    assert find_page("the wake group was tested in the evening", SECTION) == 4
    assert find_page("The sleep group recalled 23 percent", SECTION) == 3
    assert find_page("not in the paper at all", SECTION) is None


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


def blind(answer="About 23 percent more.", quote="the wake group was tested in the evening", answerable=True):
    return {"answerable": answerable, "answer": answer, "quote": quote}


@pytest.fixture
def fake(monkeypatch):
    def install(**replies):
        model = FakeModel(replies)
        monkeypatch.setattr(paper_qa_lib, "call_ollama", model)
        return model
    return install


def test_verify_passes_on_agreement_and_real_quote(fake):
    model = fake(blind=[blind()], judge=[{"verdict": "agree", "reason": "same"}])
    q = question()
    assert verify_question(q, SECTION, "m", log=lambda m: None)
    assert q["evidence"] == "The sleep group recalled 23 percent more word pairs"
    assert "How much better" not in model.prompts[0].split("QUESTION:")[0], "section text should come first"
    assert "23% more pairs." not in model.prompts[0], "blind answer must not see the original answer"


def test_verify_fails_on_disagreement(fake):
    fake(blind=[blind(answer="They did worse.")], judge=[{"verdict": "disagree", "reason": "opposite"}])
    q = question()
    assert not verify_question(q, SECTION, "m", log=lambda m: None)
    assert q["verification"].startswith("disagree")


def test_verify_uses_blind_quote_when_original_evidence_is_invented(fake):
    fake(blind=[blind()], judge=[{"verdict": "partial", "reason": "close"}])
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
    evidence = ["The sleep group recalled 23 percent more word pairs", "We train on 1,605 complexes released in 2025",
                "the wake group was tested in the evening", "A limitation is the small sample"]
    gen = [{"type": "comprehension", "question": f"Question {i}?", "answer": f"a{i}", "evidence": e}
           for i, e in enumerate(evidence)]
    fake(
        questions=[{"questions": gen}],
        rank=[{"ranked_ids": [3, 1, 0, 2]}],
        # First batch (q3, q1): q3 fails. Second batch (q0): passes.
        blind=[blind(), blind(), blind()],
        judge=[{"verdict": "disagree", "reason": ""}, {"verdict": "agree", "reason": ""},
               {"verdict": "agree", "reason": ""}],
    )
    questions, summary = generate_questions(SECTION, "m", num_questions=2, log=lambda m: None)
    assert [q["question"] for q in questions] == ["Question 1?", "Question 0?"]
    assert all(q["page"] == 3 for q in questions)
    assert summary.startswith("2 of 3 questions checked passed")


def test_clean_quote():
    from paper_qa_lib import clean_quote
    assert clean_quote("insufficient ability to discrimi-\nnate binders\n from  nonbinders") == \
        "insufficient ability to discriminate binders from nonbinders"


def test_balance_types_interleaves_keeping_rank_order():
    from paper_qa_lib import balance_types
    ranked = [{"type": t, "question": f"{t}{i}"} for i, t in enumerate(
        ["critical", "critical", "critical", "methodology", "comprehension", "critical", "methodology"])]
    assert [q["question"] for q in balance_types(ranked)] == [
        "critical0", "methodology3", "comprehension4", "critical1", "methodology6", "critical2", "critical5"]


def test_judge_rejects_false_premise_or_unsupported_answer(fake):
    fake(blind=[blind(), blind()],
         judge=[{"premise_ok": False, "a_supported": True, "verdict": "agree", "reason": "loaded"},
                {"premise_ok": True, "a_supported": False, "verdict": "partial", "reason": "made up"}])
    q1, q2 = question(), question()
    assert not verify_question(q1, SECTION, "m", log=lambda m: None)
    assert q1["verification"].startswith("false premise")
    assert not verify_question(q2, SECTION, "m", log=lambda m: None)
    assert q2["verification"].startswith("answer not supported")


def test_generate_skips_questions_with_duplicate_evidence(fake, monkeypatch):
    monkeypatch.setattr(paper_qa_lib, "chunk_text", lambda text: [SECTION])
    same = "The sleep group recalled 23 percent more word pairs"
    gen = [{"type": "comprehension", "question": f"Question {i}?", "answer": f"a{i}", "evidence": same}
           for i in range(2)]
    gen.append({"type": "critical", "question": "Question 2?", "answer": "a2",
                "evidence": "the wake group was tested in the evening"})
    agree = {"verdict": "agree", "reason": ""}
    fake(questions=[{"questions": gen}], rank=[{"ranked_ids": [0, 1, 2]}],
         blind=[blind()] * 3, judge=[agree] * 3)
    questions, _ = generate_questions(SECTION, "m", num_questions=2, log=lambda m: None)
    assert [q["question"] for q in questions] == ["Question 0?", "Question 2?"]
