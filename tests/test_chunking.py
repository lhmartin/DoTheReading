import pytest

from paper_qa_lib import chunk_text


def paragraphs(n, size=100):
    # Distinct, recognisable paragraphs: "P007 xxxx..."
    return [f"P{i:03d} " + "x" * (size - 5) for i in range(n)]


def test_short_text_is_one_chunk():
    text = "\n\n".join(paragraphs(3))
    assert chunk_text(text, chunk_size=1000, overlap=100) == [text]


def test_chunks_respect_size_limit():
    text = "\n\n".join(paragraphs(100))
    chunks = chunk_text(text, chunk_size=1000, overlap=200)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 for c in chunks)


def test_every_paragraph_is_kept_in_order():
    paras = paragraphs(100)
    chunks = chunk_text("\n\n".join(paras), chunk_size=1000, overlap=200)
    seen = []
    for chunk in chunks:
        for para in chunk.split("\n\n"):
            if para not in seen:
                seen.append(para)
    assert seen == paras


def test_consecutive_chunks_overlap():
    chunks = chunk_text("\n\n".join(paragraphs(100)), chunk_size=1000, overlap=250)
    for prev, nxt in zip(chunks, chunks[1:]):
        prev_paras = prev.split("\n\n")
        shared = []
        for para in nxt.split("\n\n"):
            if para not in prev_paras:
                break
            shared.append(para)
        overlap_text = "\n\n".join(shared)
        assert shared, "next chunk should start with context from the previous one"
        assert prev.endswith(overlap_text)
        assert len(overlap_text) <= 250


def test_zero_overlap_matches_plain_split():
    paras = paragraphs(30)
    chunks = chunk_text("\n\n".join(paras), chunk_size=1000, overlap=0)
    assert "\n\n".join(chunks) == "\n\n".join(paras)


def test_overlap_starts_on_word_boundary_inside_long_paragraph():
    words = " ".join(f"w{i:04d}" for i in range(400))  # one ~2,400-char paragraph
    chunks = chunk_text(words + "\n\n" + words, chunk_size=3000, overlap=200)
    assert len(chunks) == 2
    assert chunks[1].split()[0].startswith("w") and len(chunks[1].split()[0]) == 5
    assert chunks[1].startswith(chunks[0][-200:].split(" ", 1)[1])


def test_oversized_paragraph_is_split():
    huge = " ".join(["word"] * 3000)  # ~15,000 chars with no blank lines
    chunks = chunk_text(huge, chunk_size=4000, overlap=500)
    assert len(chunks) > 1
    assert all(len(c) <= 4000 for c in chunks)
    assert all(w == "word" for c in chunks for w in c.split())


def test_blank_paragraphs_are_dropped():
    assert chunk_text("a\n\n\n\n   \n\nb", chunk_size=1000, overlap=10) == ["a\n\nb"]


def test_empty_text():
    assert chunk_text("") == []


@pytest.mark.parametrize("overlap", [-1, 500, 900])
def test_rejects_bad_overlap(overlap):
    with pytest.raises(ValueError):
        chunk_text("text", chunk_size=1000, overlap=overlap)
