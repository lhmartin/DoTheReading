import pytest

from qa_format import normalize_for_match, parse_markdown, parse_model_json, parse_title, render_markdown

QUESTIONS = [
    {"type": "comprehension", "question": "What did they find?", "answer": "X improves Y.\n\nSee Table 2.",
     "evidence": "X improves Y by 20%.", "page": 4},
    {"type": "critical", "question": "What's the Q: weakness?", "answer": "A: small n.", "evidence": "", "page": None},
]


def test_render_then_parse_round_trips():
    md = render_markdown("Some Paper", QUESTIONS, note="paper.pdf · 2 of 3 passed")
    assert md.startswith("# Study Questions: Some Paper")
    assert parse_title(md) == "Some Paper"
    parsed = parse_markdown(md)
    assert [(q["question"], q["answer"], q["evidence"], q["page"]) for q in parsed] == [
        ("What did they find?", "X improves Y.\n\nSee Table 2.", "X improves Y by 20%.", 4),
        ("What's the Q: weakness?", "A: small n.", "", None),
    ]


def test_evidence_without_page():
    md = render_markdown("T", [{**QUESTIONS[0], "page": None}])
    assert "**Evidence:**" in md
    assert parse_markdown(md)[0]["evidence"] == "X improves Y by 20%."


def test_parses_legacy_qa_files():
    md = "# Study Questions: Old\n\nQ: First?\nA: One.\n\nQ: Second?\nA: Two,\nover two lines.\n"
    assert [(q["question"], q["answer"]) for q in parse_markdown(md)] == [
        ("First?", "One."), ("Second?", "Two,\nover two lines.")]
    assert parse_title(md) == "Old"


def test_parse_model_json():
    raw = '{"questions": [{"type": "approach", "question": "Why?", "answer": "Because.", "evidence": "Quote."}]}'
    assert parse_model_json(raw) == [
        {"type": "approach", "question": "Why?", "answer": "Because.", "evidence": "Quote."}]


def test_parse_model_json_tolerates_drift():
    raw = '```json\n[{"question": "Q1", "answer": "A1", "type": "Other"}, {"question": "no answer"}]\n```'
    assert parse_model_json(raw) == [{"type": "", "question": "Q1", "answer": "A1", "evidence": ""}]


@pytest.mark.parametrize("raw", ["Q: plain text\nA: answer", '{"questions": []}', '{"foo": 1}'])
def test_parse_model_json_rejects_unusable(raw):
    with pytest.raises(ValueError):
        parse_model_json(raw)


def test_normalize_ignores_pdf_noise():
    assert normalize_for_match("an all-\natom  model, ﬁne") == normalize_for_match("An all-atom model fine")


def test_parse_markdown_keeps_question_type():
    md = render_markdown("T", QUESTIONS)
    assert [q["type"] for q in parse_markdown(md)] == ["comprehension", "critical"]
    assert parse_markdown("## Q1\nNo type?\n\n**Answer:** yes\n")[0]["type"] == ""
