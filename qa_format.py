"""Reading and writing the question sets.

The model is asked for JSON (see QUESTIONS_SCHEMA); we then render that into
a fixed markdown layout that we control, so quiz_me.py never has to parse
free-form model output. Stdlib only, so quiz_me.py stays dependency-free.

Rendered layout:

    # Study Questions: <paper title>

    ## Q1 (methodology)
    <question>

    **Answer:** <answer>

    **Evidence (p. 4):** "<verbatim quote from the paper>"
"""

import json
import re
import unicodedata

# A study set should walk from the whole paper down to its details, so a
# question's "type" is the rung it sits on rather than a flavour.
QUESTION_LEVELS = ["overview", "approach", "evidence", "critique"]
QUESTION_TYPES = QUESTION_LEVELS  # the field is still called "type" on disk

# Passed to Ollama's `format` field to constrain generation to this shape.
QUESTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": QUESTION_TYPES},
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["type", "question", "answer", "evidence"],
            },
        }
    },
    "required": ["questions"],
}

TITLE_PREFIX = "Study Questions: "
ANSWER_MARKER = "**Answer:**"
_EVIDENCE_RE = re.compile(r"^\*\*Evidence(?: \(p\. (\d+)\))?:\*\*\s*(.*)$", flags=re.MULTILINE | re.DOTALL)
_HEADING_RE = re.compile(r"^## Q\d+\b[ \t]*(?:\(([^)]*)\))?[ \t]*$", flags=re.MULTILINE)
_LEGACY_QA_RE = re.compile(r"Q:\s*(.+?)\s*A:\s*(.+?)(?=\nQ:|\Z)", flags=re.DOTALL)


def strip_json_fence(raw: str) -> str:
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    return fence.group(1) if fence else text


def parse_model_json(raw: str) -> list[dict]:
    """Turn the model's JSON reply into a list of question dicts with keys
    type, question, answer, evidence.

    Raises ValueError if the reply isn't usable JSON. Tolerates a few common
    drifts: a bare list instead of {"questions": [...]}, a ```json fence, and
    missing/unknown "type" or "evidence" values.
    """
    try:
        data = json.loads(strip_json_fence(raw))
    except json.JSONDecodeError as e:
        raise ValueError(f"model reply is not valid JSON: {e}") from e

    items = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("model reply has no 'questions' list")

    questions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question", "")).strip()
        a = str(item.get("answer", "")).strip()
        if not q or not a:
            continue
        qtype = str(item.get("type", "")).strip().lower()
        questions.append({
            "type": qtype if qtype in QUESTION_TYPES else "",
            "question": q,
            "answer": a,
            "evidence": str(item.get("evidence", "")).strip(),
        })

    if not questions:
        raise ValueError("model reply contained no complete question/answer pairs")
    return questions


def parse_legacy_qa(text: str) -> list[dict]:
    """Parse the old free-text `Q: ... A: ...` format."""
    return [
        {"type": "", "question": q.strip(), "answer": a.strip(), "evidence": ""}
        for q, a in _LEGACY_QA_RE.findall(text)
    ]


def normalize_for_match(text: str) -> str:
    """Lowercase alphanumerics only. Makes quote matching immune to PDF
    extraction noise: line breaks, hyphenation ("all- atom"), ligatures,
    and punctuation/spacing differences."""
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text if ch.isalnum())


def render_markdown(title: str, questions: list[dict], note: str = "") -> str:
    parts = [f"# {TITLE_PREFIX}{title}\n"]
    if note:
        parts.append(f"_{note}_\n")
    for i, item in enumerate(questions, 1):
        heading = f"## Q{i}" + (f" ({item['type']})" if item.get("type") else "")
        block = f"{heading}\n{item['question']}\n\n{ANSWER_MARKER} {item['answer']}\n"
        if item.get("evidence"):
            page = f" (p. {item['page']})" if item.get("page") else ""
            block += f"\n**Evidence{page}:** \"{item['evidence']}\"\n"
        parts.append(block)
    return "\n".join(parts)


def parse_title(md_text: str) -> str | None:
    """The paper title from a questions file's `# Study Questions: ...` line."""
    for line in md_text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            return title[len(TITLE_PREFIX):].strip() if title.startswith(TITLE_PREFIX) else title
    return None


def parse_markdown(md_text: str) -> list[dict]:
    """Pull questions out of a questions .md file, as dicts with keys
    type, question, answer, evidence ("" if none) and page (int or None).

    Understands the current rendered layout (with or without evidence), and
    falls back to the old `Q: ... A: ...` format so question files from
    before the switch still work.
    """
    # split() with one capture group alternates: type, section, type, ...
    parts = _HEADING_RE.split(md_text)[1:]  # [0] is the title block
    questions = []
    for qtype, section in zip(parts[::2], parts[1::2]):
        question, sep, rest = section.partition(ANSWER_MARKER)
        if not sep or not question.strip():
            continue
        answer, evidence, page = rest, "", None
        match = _EVIDENCE_RE.search(rest)
        if match:
            answer = rest[:match.start()]
            page = int(match.group(1)) if match.group(1) else None
            evidence = match.group(2).strip().strip('"').strip()
        if answer.strip():
            questions.append({"type": (qtype or "").strip(), "question": question.strip(),
                              "answer": answer.strip(), "evidence": evidence, "page": page})
    if questions:
        return questions
    return [
        {"type": "", "question": item["question"], "answer": item["answer"], "evidence": "", "page": None}
        for item in parse_legacy_qa(md_text)
    ]
