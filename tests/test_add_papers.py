import pytest

from study_api import add_papers


@pytest.fixture
def base(tmp_path):
    (tmp_path / "library").mkdir()
    return tmp_path


def make_pdf_file(path, name="paper.pdf"):
    target = path / name
    target.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return str(target)


def test_adds_pdfs_to_the_inbox(base, tmp_path):
    source = make_pdf_file(tmp_path, "new.pdf")
    result = add_papers(base, [source])
    assert result == {"added": ["new.pdf"], "skipped": []}
    assert (base / "inbox" / "new.pdf").exists()


def test_skips_duplicates_and_processed_papers(base, tmp_path):
    already_queued = make_pdf_file(tmp_path, "queued.pdf")
    already_done = make_pdf_file(tmp_path, "done.pdf")
    add_papers(base, [already_queued])
    (base / "library" / "done.pdf").write_bytes(b"%PDF-1.4\n")

    result = add_papers(base, [already_queued, already_done])
    assert result["added"] == []
    assert [s["why"] for s in result["skipped"]] == ["already in the inbox", "already processed"]


def test_skips_non_pdfs_and_missing_files(base, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    result = add_papers(base, [str(notes), str(tmp_path / "ghost.pdf")])
    assert result["added"] == []
    assert all(s["why"] == "not a PDF" for s in result["skipped"])


def test_original_file_is_left_alone(base, tmp_path):
    source = make_pdf_file(tmp_path, "keep.pdf")
    add_papers(base, [source])
    assert (tmp_path / "keep.pdf").exists(), "we copy, never move: the PDF may live in Downloads"
