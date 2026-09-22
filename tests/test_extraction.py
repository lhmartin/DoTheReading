import pytest

import paper_qa_lib
from paper_qa_lib import extract_text, needs_ocr

LOREM = "Transformer models attend over every token in the input sequence."


@pytest.fixture
def fake_ocr(monkeypatch):
    """Pretend Tesseract is installed and returns a fixed string."""
    calls = []

    def ocr_page(page):
        calls.append(page.page_number)
        return f"OCR text for page {page.page_number}"

    monkeypatch.setattr(paper_qa_lib, "ocr_available", lambda: True)
    monkeypatch.setattr(paper_qa_lib, "ocr_page", ocr_page)
    return calls


def test_extracts_text_with_page_markers(make_pdf):
    text = extract_text(make_pdf([[LOREM], ["Second page results."]]), log=lambda m: None)
    assert "--- Page 1 ---" in text and "--- Page 2 ---" in text
    assert LOREM in text
    assert text.index(LOREM) < text.index("Second page results.")


def test_text_pdf_does_not_trigger_ocr(make_pdf, fake_ocr):
    extract_text(make_pdf([[LOREM], [LOREM]]), log=lambda m: None)
    assert fake_ocr == []


def test_scanned_pages_are_ocrd(make_pdf, fake_ocr):
    logs = []
    text = extract_text(make_pdf([None, None]), log=logs.append)
    assert fake_ocr == [1, 2]
    assert "OCR text for page 1" in text and "OCR text for page 2" in text
    assert any("OCR'd 2/2" in m for m in logs)


def test_mixed_pdf_only_ocrs_pages_without_text(make_pdf, fake_ocr):
    text = extract_text(make_pdf([[LOREM], None, [LOREM]]), log=lambda m: None)
    assert fake_ocr == [2]
    assert LOREM in text and "OCR text for page 2" in text


def test_scanned_pdf_without_tesseract_returns_empty(make_pdf, monkeypatch):
    # Previously the page markers made this non-empty, so scanned PDFs were
    # sent to the model instead of being skipped.
    monkeypatch.setattr(paper_qa_lib, "ocr_available", lambda: False)
    logs = []
    assert extract_text(make_pdf([None, None]), log=logs.append) == ""
    assert any("Tesseract isn't installed" in m for m in logs)


def test_ocr_disabled(make_pdf, fake_ocr):
    assert extract_text(make_pdf([None]), ocr=False, log=lambda m: None) == ""
    assert fake_ocr == []


@pytest.mark.parametrize("text, expected", [("", True), ("  \n 12 \n", True), (LOREM, False)])
def test_needs_ocr(text, expected):
    assert needs_ocr(text) is expected


@pytest.mark.skipif(not paper_qa_lib.ocr_available(), reason="Tesseract not installed")
def test_real_ocr_reads_rendered_page(make_pdf):
    import pdfplumber

    with pdfplumber.open(make_pdf([["Attention is all you need"]])) as pdf:
        assert "Attention" in paper_qa_lib.ocr_page(pdf.pages[0])


def two_column_page():
    title = [(150, 740, "A Very Long Title That Spans Both Of The Columns On This Page")]
    left = [(50, 700 - 14 * i, f"left column sentence number {i:02d} goes here") for i in range(20)]
    right = [(320, 700 - 14 * i, f"right column sentence number {i:02d} goes here") for i in range(20)]
    return title + left + right


def test_two_column_page_reads_left_column_then_right(make_pdf):
    text = extract_text(make_pdf([two_column_page()]), log=lambda m: None)
    lines = [l for l in text.splitlines() if l.strip() and not l.startswith("---")]
    assert lines[0] == "A Very Long Title That Spans Both Of The Columns On This Page"
    assert lines[1:21] == [f"left column sentence number {i:02d} goes here" for i in range(20)]
    assert lines[21:41] == [f"right column sentence number {i:02d} goes here" for i in range(20)]


def test_single_column_page_is_not_split(make_pdf):
    rows = [(72, 700 - 14 * i, f"a full width line of ordinary body text number {i:02d} runs across the middle")
            for i in range(20)]
    text = extract_text(make_pdf([rows]), log=lambda m: None)
    assert [l for l in text.splitlines() if l.startswith("a full")] == [r[2] for r in rows]
