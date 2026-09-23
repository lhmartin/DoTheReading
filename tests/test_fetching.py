import argparse

import pytest
import requests

import study_api


class Response:
    def __init__(self, status_code, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.text = content.decode("utf-8", "replace")
        self.headers = headers or {}

    @property
    def ok(self):
        return self.status_code < 400

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(study_api.time, "sleep", lambda seconds: None)


def test_waits_out_a_rate_limit_then_succeeds(monkeypatch):
    replies = [Response(429, headers={"Retry-After": "2"}), Response(200, b"<html>ok</html>")]
    monkeypatch.setattr(requests, "get", lambda *a, **k: replies.pop(0))
    assert study_api.fetch_with_retry("https://ex.com").status_code == 200


def test_gives_up_after_repeated_rate_limits(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: Response(429))
    with pytest.raises(requests.exceptions.HTTPError):
        study_api.fetch_with_retry("https://ex.com", attempts=2)


def test_a_rate_limited_site_falls_back_to_its_pdf(monkeypatch, tmp_path):
    def get(url, **kwargs):
        return Response(200, b"%PDF-1.7 ...") if url.endswith(".pdf") else Response(429)

    monkeypatch.setattr(requests, "get", get)
    result = study_api.add_url(tmp_path, "https://www.biorxiv.org/content/10.64898/2026.09.08.749745v1")
    assert result["ok"] and result["pdf"]
    assert "rate-limiting" in result["note"]
    assert list((tmp_path / "inbox").glob("*.pdf")), "the PDF was queued for processing"


def test_a_rate_limited_site_with_no_pdf_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "get", lambda *a, **k: Response(429))
    result = study_api.add_url(tmp_path, "https://someblog.example/post")
    assert result["ok"] is False
    assert "rate-limiting" in result["error"] and "Wait a minute" in result["error"]


ABSTRACT_ONLY = b"<html><title>A Paper</title><body><article><h2>Abstract</h2><p>" + b"x" * 2000 + b"</p></article></body></html>"
FULL_TEXT = b"<html><title>A Paper</title><body><article><h2>Introduction</h2><p>" + b"y" * 20000 + b"</p></article></body></html>"


def test_an_abstract_only_page_queues_the_pdf_instead(monkeypatch, tmp_path):
    """bioRxiv serves the abstract when a paper has no full text; a 3,000
    character 'paper' makes for a poor question set."""
    def get(url, **kwargs):
        return Response(200, b"%PDF-1.7 ...") if url.endswith(".pdf") else Response(200, ABSTRACT_ONLY)

    monkeypatch.setattr(requests, "get", get)
    result = study_api.add_url(tmp_path, "https://www.biorxiv.org/content/10.1101/2026.01.01.123456v1")
    assert result["ok"] and result["pdf"]
    assert "abstract" in result["note"]
    assert list((tmp_path / "inbox").glob("*.pdf")) and not list((tmp_path / "inbox").glob("*.html"))


def test_a_real_full_text_is_used_as_is(monkeypatch, tmp_path):
    def get(url, **kwargs):
        return Response(200, b"%PDF-1.7 ...") if url.endswith(".pdf") else Response(200, FULL_TEXT)

    monkeypatch.setattr(requests, "get", get)
    result = study_api.add_url(tmp_path, "https://www.biorxiv.org/content/10.1101/2026.01.01.123456v1")
    assert result["ok"] and result["characters"] > 6000
    assert list((tmp_path / "inbox").glob("*.html")), "the web text is what gets processed"


def test_inbox_listing_includes_articles(tmp_path):
    import argparse

    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "paper.pdf").write_bytes(b"%PDF")
    (tmp_path / "inbox" / "article.html").write_text("<html></html>")
    (tmp_path / "inbox" / "notes.txt").write_text("ignore me")
    listing = study_api.cmd_library(argparse.Namespace(base=str(tmp_path)))
    assert [q["name"] for q in listing["inbox"]] == ["article.html", "paper.pdf"]
    assert [q["kind"] for q in listing["inbox"]] == ["article", "pdf"]
