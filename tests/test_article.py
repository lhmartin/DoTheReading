import pytest

from article import as_reader_html, canonical_sources, extract_article, slug_for

PAGE = """
<html><head><title>How Transformers Work | Example Blog</title></head>
<body>
  <nav><a href="/">Home</a><a href="/archive">Archive</a></nav>
  <header>Subscribe now for more posts about machine learning and other things</header>
  <article>
    <h1>How Transformers Work</h1>
    <p>Attention lets every token look at every other token, which is the whole trick
       and the reason the architecture scales so well on modern hardware.</p>
    <h2>Self-attention</h2>
    <p>Queries, keys and values are projections of the same input sequence, and the
       dot product between a query and a key decides how much each value contributes.</p>
    <figure><figcaption>Figure 1: an attention matrix, shown as a heatmap</figcaption></figure>
    <script>analytics.track("pageview", {page: "transformers"});</script>
    <h2>References</h2>
    <p>Vaswani et al. Attention is all you need. NeurIPS 2017, pages 5998 to 6008.</p>
  </article>
  <footer>Copyright 2026, all rights reserved by the author of this weblog</footer>
</body></html>
"""


def test_extracts_the_article_and_its_headings():
    got = extract_article(PAGE)
    assert got["title"] == "How Transformers Work | Example Blog"
    assert "Attention lets every token" in got["text"]
    assert "## Self-attention" in got["text"]


@pytest.mark.parametrize("unwanted", [
    "analytics.track",          # script
    "Figure 1:",                # figcaption
    "Copyright 2026",           # footer
    "Subscribe now",            # header
    "Attention is all you need",  # after the References heading
])
def test_drops_what_isnt_the_article(unwanted):
    assert unwanted not in extract_article(PAGE)["text"]


def test_biorxiv_gives_full_text_and_pdf():
    sources = canonical_sources("https://www.biorxiv.org/content/10.64898/2026.09.08.749745v1")
    assert sources["text_url"].endswith("v1.full")
    assert sources["pdf_url"].endswith("v1.full.pdf")


@pytest.mark.parametrize("url", [
    "https://arxiv.org/abs/2511.09216",
    "https://arxiv.org/pdf/2511.09216",
    "https://arxiv.org/pdf/2511.09216.pdf",
])
def test_arxiv_urls_all_resolve_to_the_same_paper(url):
    sources = canonical_sources(url)
    assert sources["text_url"] == "https://arxiv.org/abs/2511.09216"
    assert sources["pdf_url"] == "https://arxiv.org/pdf/2511.09216"


def test_other_sites_are_read_as_given():
    sources = canonical_sources("https://simonwillison.net/2026/Jan/1/some-post/")
    assert sources["text_url"] == "https://simonwillison.net/2026/Jan/1/some-post/"
    assert sources["pdf_url"] is None


def test_slug_is_readable_and_safe():
    assert slug_for("https://example.com/p/1", "How Transformers Work: part 2") == "example-How-Transformers-Work-part-2"
    assert "/" not in slug_for("https://ex.com/a/b", "a/b:c*d")
    assert slug_for("https://ex.com/final-thoughts/", None) == "ex-final-thoughts"


def test_reader_html_keeps_headings_and_escapes_text():
    page = as_reader_html("Title <hack>", "## Heading\n\nBody & more", "https://ex.com")
    assert "<h2>Heading</h2>" in page and "<p>Body &amp; more</p>" in page
    assert "<hack>" not in page


@pytest.mark.parametrize("page_title, text, expected", [
    ("Specificity-driven binder design | bioRxiv", "## Specificity-driven binder design\n\nbody",
     "Specificity-driven binder design"),                      # the heading wins
    ("How Transformers Work | Example Blog", "Some lead-in text\n\nmore text",
     "How Transformers Work"),                                  # site name trimmed
    ("Attention Is All You Need", "body text", "Attention Is All You Need"),
    ("A Study of X - Y Interactions in Cells", "body text",
     "A Study of X - Y Interactions in Cells"),                 # a real dash mid-title is kept
    ("Scaling laws for neural models - Substack", "body text",
     "Scaling laws for neural models"),                         # a one-word site after a dash goes
])
def test_title_from(page_title, text, expected):
    from article import title_from

    assert title_from({"title": page_title, "text": text}, "https://ex.com") == expected


ARXIV_ABS = """<html><head><title>[2609.19770v1] TorchCraft: Unified binder design</title>
<meta name="citation_title" content="TorchCraft: Unified binder design" /></head>
<body><h1>Computer Science &gt; Artificial Intelligence</h1>
<h1 class="title"><span class="descriptor">Title:</span>TorchCraft: Unified binder design</h1>
<blockquote>""" + "An abstract long enough to count as text. " * 5 + """</blockquote></body></html>"""


def test_title_prefers_the_citation_title_over_the_first_heading():
    from article import title_from

    # arXiv's first <h1> is the subject area, not the paper.
    assert title_from(extract_article(ARXIV_ABS), "https://arxiv.org/abs/2609.19770v1") == \
        "TorchCraft: Unified binder design"


def test_title_trims_the_site_name_from_a_declared_title():
    from article import title_from

    page = '<html><head><meta property="og:title" content="Why proteins fold | Quanta Magazine"></head>' \
           "<body><h1>Menu</h1><p>Text.</p></body></html>"
    assert title_from(extract_article(page), "https://example.org/a") == "Why proteins fold"


def test_title_drops_the_arxiv_id_from_the_page_title():
    from article import title_from

    assert title_from({"title": "[2609.19770v1] TorchCraft", "text": "body"}, "u") == "TorchCraft"
