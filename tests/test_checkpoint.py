import json

import pytest

import paper_qa_lib
from checkpoint import Checkpoint


@pytest.fixture(autouse=True)
def cache_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


def test_remembers_sections_across_runs():
    first = Checkpoint("paper", "the text")
    assert (first.sections_done, first.candidates) == (0, [])
    first.save(3, [{"question": "Q1?"}])

    resumed = Checkpoint("paper", "the text")
    assert resumed.sections_done == 3
    assert resumed.candidates == [{"question": "Q1?"}]


def test_changed_paper_starts_over():
    Checkpoint("paper", "the text").save(3, [{"question": "Q1?"}])
    assert Checkpoint("paper", "different text").sections_done == 0


def test_clear_removes_the_file():
    done = Checkpoint("paper", "t")
    done.save(1, [])
    assert done.path.exists()
    done.clear()
    assert not done.path.exists()
    done.clear()  # twice is fine


def test_corrupt_checkpoint_is_ignored(tmp_path):
    broken = Checkpoint("paper", "t")
    broken.path.write_text("{not json")
    assert Checkpoint("paper", "t").sections_done == 0


def test_generate_resumes_from_the_checkpoint(monkeypatch):
    """A paper stopped after section 1 shouldn't rewrite section 1."""
    chunks = ["section one text", "section two text"]
    monkeypatch.setattr(paper_qa_lib, "chunk_text", lambda text: chunks)
    asked = []

    def fake_ask(prompt, model, log=print):
        asked.append(prompt)
        return [{"type": "comprehension", "question": "New?", "answer": "a", "evidence": "second evidence"}]

    monkeypatch.setattr(paper_qa_lib, "ask_for_questions", fake_ask)
    monkeypatch.setattr(paper_qa_lib, "rank_candidates", lambda c, n, m, log: c)
    monkeypatch.setattr(paper_qa_lib, "verify_questions", lambda qs, chunk, model, log: [True] * len(qs))
    monkeypatch.setattr(paper_qa_lib, "page_index", lambda text: [])

    progress = Checkpoint("paper", "whole text")
    progress.save(1, [{"type": "critical", "question": "Old?", "answer": "a", "evidence": "first evidence", "chunk": 0}])

    questions, _ = generate = paper_qa_lib.generate_questions(
        "whole text", "m", 2, log=lambda m: None, checkpoint=progress)
    assert len(asked) == 1, "only the unfinished section should be generated"
    assert "section two text" in asked[0]
    assert [q["question"] for q in questions] == ["Old?", "New?"]
    assert progress.sections_done == 2
