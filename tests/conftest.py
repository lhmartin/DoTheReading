import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(pages: list[list | None]) -> bytes:
    """Build a minimal PDF. Each page is a list of text lines, or of
    (x, y, text) tuples to place text at a position (PDF points, y from the
    bottom), or None for a page with no text layer (what a scanned page
    looks like to pdfplumber)."""
    n = len(pages)
    font_id = 3
    page_ids = [4 + 2 * i for i in range(n)]
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {n} >>".encode(),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, lines in zip(page_ids, pages):
        if lines and isinstance(lines[0], tuple):
            ops = [f"BT /F1 10 Tf {x} {y} Td ({_escape(t)}) Tj ET" for x, y, t in lines]
        else:
            ops = ["BT", "/F1 14 Tf", "72 720 Td", "18 TL"]
            for line in lines or []:
                ops.append(f"({_escape(line)}) Tj T*")
            ops.append("ET")
        stream = "\n".join(ops).encode() if lines else b""
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {page_id + 1} 0 R >>"
        ).encode()
        objects[page_id + 1] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for obj_id in sorted(objects):
        offsets[obj_id] = len(out)
        out += b"%d 0 obj\n" % obj_id + objects[obj_id] + b"\nendobj\n"
    xref_pos = len(out)
    size = max(objects) + 1
    out += b"xref\n0 %d\n0000000000 65535 f \n" % size
    for obj_id in range(1, size):
        out += b"%010d 00000 n \n" % offsets[obj_id]
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref_pos)
    return bytes(out)


@pytest.fixture
def make_pdf(tmp_path):
    def _make(pages, name="paper.pdf"):
        path = tmp_path / name
        path.write_bytes(build_pdf(pages))
        return str(path)
    return _make
