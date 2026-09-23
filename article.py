"""Fetching a web article and reducing it to readable text.

Web text beats a PDF for this pipeline: it's already in reading order, with
real headings and no ligature or column damage. Works for any article-shaped
page (blogs, Substack, docs); bioRxiv and arXiv also expose a full-text page
and a PDF, so those are recognised and both are used.

Standard library only, so the frozen app stays small.
"""

import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

# Wrappers whose text is never the article.
SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript",
             "figure", "figcaption", "button", "svg", "select", "textarea"}
BLOCK_TAGS = {"p", "div", "section", "article", "li", "br", "tr", "blockquote", "pre"}
HEADING_TAGS = {"h1", "h2", "h3", "h4"}
# Sections that follow the paper itself; keeping them buries the content.
TAIL_HEADINGS = re.compile(r"^(references|bibliography|acknowledge?ments|supplementary|footnotes|"
                           r"competing interests|author contributions|data availability)\b", re.I)


class ArticleParser(HTMLParser):
    """Collects text, keeping headings marked so chunking can use them."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._heading: str | None = None
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in HEADING_TAGS and not self._skip_depth:
            self._heading = tag
            self.parts.append("\n\n## ")
        elif tag in BLOCK_TAGS and not self._skip_depth:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in HEADING_TAGS and self._heading == tag:
            self._heading = None
            self.parts.append("\n\n")

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = " ".join(data.split()) or None
        if self._skip_depth or not data.strip():
            return
        self.parts.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        joined = "".join(self.parts)
        joined = re.sub(r"[ \t]+", " ", joined)
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        return joined.strip()


def drop_tail_sections(text: str) -> str:
    """Cut references and similar trailing sections."""
    kept = []
    for block in text.split("\n\n"):
        heading = block.strip()
        if heading.startswith("## ") and TAIL_HEADINGS.match(heading[3:].strip()):
            break
        kept.append(block)
    return "\n\n".join(kept)


def extract_article(page_html: str) -> dict:
    """Title and readable text from a page's HTML."""
    parser = ArticleParser()
    parser.feed(page_html)
    text = drop_tail_sections(parser.text())
    # Drop leftover navigation crumbs: very short lines that aren't headings.
    blocks = [b.strip() for b in text.split("\n\n")]
    blocks = [b for b in blocks if b.startswith("## ") or len(b) > 40]
    return {"title": parser.title, "text": "\n\n".join(blocks)}


def title_from(extracted: dict, url: str) -> str:
    """The article's own title: its first heading if it has one, otherwise the
    page title with the site's name trimmed off ("... | bioRxiv")."""
    for block in extracted["text"].split("\n\n")[:3]:
        if block.startswith("## "):
            return block[3:].strip()
    title = (extracted.get("title") or url).strip()
    # " | Site" and friends are always a site name; after a dash, only a
    # single word is (arXiv, Substack) — real titles use dashes mid-sentence.
    trimmed = re.sub(r"\s*[|\u00b7\u2022]\s*[\w .'&-]{2,24}$", "", title)
    trimmed = re.sub(r"\s+[-\u2013\u2014]\s+[\w.'&]{2,20}$", "", trimmed)
    return trimmed or title


def canonical_sources(url: str) -> dict:
    """Where to find the full text and (if there is one) the PDF.

    bioRxiv/medRxiv and arXiv publish both; anything else is read as-is.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path

    if host in {"biorxiv.org", "medrxiv.org"}:
        base = re.sub(r"\.(full(-text)?|abstract|full\.pdf)$", "", path)
        return {"text_url": urljoin(url, base + ".full"), "pdf_url": urljoin(url, base + ".full.pdf")}

    if host == "arxiv.org":
        paper_id = re.sub(r"^/(abs|pdf|html)/", "", path).removesuffix(".pdf")
        return {"text_url": f"https://arxiv.org/abs/{paper_id}",
                "pdf_url": f"https://arxiv.org/pdf/{paper_id}"}

    return {"text_url": url, "pdf_url": None}


def slug_for(url: str, title: str | None) -> str:
    """A filename for this article: readable, unique enough, filesystem-safe."""
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.").split(".")[0]
    name = title or parsed.path.strip("/").split("/")[-1] or host
    name = html.unescape(name)
    name = re.sub(r"[^\w\s-]", "", name).strip()
    name = re.sub(r"[\s_]+", "-", name)[:70].strip("-")
    return f"{host}-{name}" if name else host or "article"


def as_reader_html(title: str, text: str, source_url: str) -> str:
    """The extracted text as a plain page, so the app has something to show
    for articles that have no PDF."""
    body = []
    for block in text.split("\n\n"):
        if block.startswith("## "):
            body.append(f"<h2>{html.escape(block[3:])}</h2>")
        else:
            body.append(f"<p>{html.escape(block)}</p>")
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{max-width:42rem;margin:3rem auto;padding:0 1.5rem;"
        "font:17px/1.65 Georgia,'Times New Roman',serif;color:#1c1a16;background:#f4efe4}"
        "h1{font-size:1.6rem;line-height:1.25}h2{font-size:1.15rem;margin-top:2rem}"
        "a{color:#7d2b22}</style>"
        f"<h1>{html.escape(title)}</h1>"
        f"<p><a href='{html.escape(source_url)}'>{html.escape(source_url)}</a></p>"
        + "".join(body)
    )
