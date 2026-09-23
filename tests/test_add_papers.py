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


def test_remove_takes_a_paper_out_of_the_queue(base, tmp_path):
    from study_api import remove_from_inbox

    add_papers(base, [make_pdf_file(tmp_path, "unwanted.pdf")])
    assert remove_from_inbox(base, "unwanted.pdf") == {"ok": True, "removed": "unwanted.pdf"}
    assert not (base / "inbox" / "unwanted.pdf").exists()


def test_remove_works_for_saved_articles(base):
    from study_api import remove_from_inbox

    (base / "inbox").mkdir(exist_ok=True)
    (base / "inbox" / "post.html").write_text("<html></html>")
    assert remove_from_inbox(base, "post.html")["ok"]


@pytest.mark.parametrize("name", ["../library/precious.pdf", "/etc/passwd", "notes.txt", "gone.pdf"])
def test_remove_refuses_anything_that_isnt_a_queued_paper(base, name):
    from study_api import remove_from_inbox

    (base / "inbox").mkdir(exist_ok=True)
    (base / "library" / "precious.pdf").write_bytes(b"%PDF")
    (base / "inbox" / "notes.txt").write_text("keep")
    assert remove_from_inbox(base, name)["ok"] is False
    assert (base / "library" / "precious.pdf").exists()
    assert (base / "inbox" / "notes.txt").exists()
