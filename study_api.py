#!/usr/bin/env python3
"""
study_api.py

JSON API over the study library, for the desktop app (app/) to call. The app
stays a thin UI: question files, quiz history and model prompts stay here, so
there is only one implementation of each.

Every command prints one JSON object on stdout.

    python study_api.py library
    python study_api.py record --paper <stem> --question <text> --correct 1
    python study_api.py grade  --paper <stem> --question <text> --answer <yours>
    python study_api.py add-papers --files a.pdf b.pdf
    python study_api.py process-inbox        # streams log lines, then a summary

`--base` (or PAPERSTUDY_DIR) overrides ~/PaperStudy, for testing.
"""

import argparse
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import settings
from qa_format import parse_markdown, parse_title
from quiz_history import QuizHistory, question_key

INBOX_SUFFIXES = {".pdf", ".html", ".htm"}
# Below this, a "full text" page is really just an abstract.
MIN_FULL_TEXT = 6000
OLLAMA_HOST = "http://localhost:11434"
TASK_NAME = "PaperStudyNightly"

# Models worth offering, largest first. Verification quality tracks the
# model's reading: see `quality` — surfaced in the app so a smaller pick is
# an informed one.
SUGGESTED_MODELS = [
    {"name": "qwen2.5:32b-instruct-q4_K_M", "size": "19 GB", "quality": "best",
     "note": "Sharpest questions. Too big for 16GB VRAM, so ~3x slower (~35 min/paper)."},
    {"name": "qwen2.5:32b-instruct-q3_K_S", "size": "14 GB", "quality": "best",
     "note": "Default. Fits in 16GB VRAM; same verification accuracy as q4_K_M in testing (~13 min/paper)."},
    {"name": "qwen2.5:14b", "size": "9 GB", "quality": "weaker",
     "note": "~10x faster, but it misread a passage and kept an answer that contradicted the paper in 3/3 trials."},
    {"name": "qwen2.5:7b", "size": "4.7 GB", "quality": "weakest",
     "note": "For low-VRAM machines. Expect shallower questions and less reliable checking."},
]


def base_dir(args) -> Path:
    if args.base:
        os.environ["PAPERSTUDY_DIR"] = args.base  # so settings.py agrees
    return settings.base_dir()


def cache_dir() -> Path:
    """Thumbnails and page counts: derived data, so it lives in the OS cache
    rather than cluttering ~/PaperStudy."""
    if platform.system() == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "DoTheReading"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dothereading"
    path = root / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cached_info(stem: str) -> dict | None:
    """Page count and cover image for a paper, if we've looked before."""
    info_path = cache_dir() / f"{stem}.json"
    if not info_path.exists():
        return None
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    cover = cache_dir() / f"{stem}.png"
    info["cover"] = str(cover) if cover.exists() else None
    return info


def build_info(stem: str, pdf_path: Path) -> dict:
    """Render the first page as a cover image and count the pages."""
    import pdfplumber

    info = {"pages": None}
    cover = cache_dir() / f"{stem}.png"
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            info["pages"] = len(pdf.pages)
            image = pdf.pages[0].to_image(resolution=55).original
            image.thumbnail((420, 560))
            image.save(cover)
    except Exception as e:  # a PDF that won't render shouldn't break the library
        info["error"] = f"{type(e).__name__}: {e}"
    (cache_dir() / f"{stem}.json").write_text(json.dumps(info), encoding="utf-8")
    info["cover"] = str(cover) if cover.exists() else None
    return info


def cmd_paper_info(args) -> dict:
    base = base_dir(args)
    pdf_path = next((p for p in (base / "library" / f"{args.paper}.pdf", base / "inbox" / f"{args.paper}.pdf")
                     if p.exists()), None)
    if pdf_path is None:
        return {"paper": args.paper, "info": {"pages": None, "cover": None}}
    return {"paper": args.paper, "info": cached_info(args.paper) or build_info(args.paper, pdf_path)}


def paper_stem(md_path: Path) -> str:
    return md_path.stem.removesuffix("_questions")


def load_papers(base: Path, history: QuizHistory) -> list[dict]:
    papers = []
    for md_path in sorted((base / "questions").glob("*_questions.md"), key=lambda f: f.stat().st_mtime, reverse=True):
        stem = paper_stem(md_path)
        md_text = md_path.read_text(encoding="utf-8")
        pdf_path = base / "library" / f"{stem}.pdf"
        questions = []
        for q in parse_markdown(md_text):
            entry = history.questions.get(question_key(stem, q["question"]), {})
            attempts = entry.get("attempts", [])
            questions.append({
                **q,
                "key": question_key(stem, q["question"]),
                "attempts": attempts,
                "needs_review": bool(attempts) and not attempts[-1]["correct"],
                "last_seen": attempts[-1]["at"] if attempts else None,
            })
        seen = [q for q in questions if q["attempts"]]
        info = cached_info(stem) or {}
        from qa_format import read_notes
        papers.append({
            "stem": stem,
            "title": parse_title(md_text) or stem,
            "pdf": str(pdf_path) if pdf_path.exists() else None,
            "pages": info.get("pages"),
            "cover": info.get("cover"),
            "added": datetime.fromtimestamp(md_path.stat().st_mtime).isoformat(timespec="seconds"),
            "questions": questions,
            "counts": {
                "total": len(questions),
                "seen": len(seen),
                "correct": sum(1 for q in seen if q["attempts"][-1]["correct"]),
                "review": sum(1 for q in questions if q["needs_review"]),
            },
            "last_studied": max((q["last_seen"] for q in seen), default=None),
            "notes": read_notes(md_text),
        })
    return papers


def suggest(papers: list[dict]) -> str | None:
    """Which paper to open today: something never studied (at random), else
    whatever was studied longest ago."""
    if not papers:
        return None
    fresh = [p for p in papers if not p["counts"]["seen"]]
    if fresh:
        return random.choice(fresh)["stem"]
    return min(papers, key=lambda p: p["last_studied"] or "")["stem"]


def overall_stats(papers: list[dict], history: QuizHistory) -> dict:
    attempts = [(a, e) for e in history.questions.values() for a in e["attempts"]]
    by_day = {}
    for attempt, _ in attempts:
        day = by_day.setdefault(attempt["at"][:10], {"date": attempt["at"][:10], "answered": 0, "correct": 0})
        day["answered"] += 1
        day["correct"] += bool(attempt["correct"])
    return {
        "papers": len(papers),
        "papers_studied": sum(1 for p in papers if p["counts"]["seen"]),
        "questions": sum(p["counts"]["total"] for p in papers),
        "answered": len(attempts),
        "correct": sum(1 for a, _ in attempts if a["correct"]),
        "review_pile": len(history.review_pile()),
        "by_day": sorted(by_day.values(), key=lambda d: d["date"]),
        "today": date.today().isoformat(),
    }


def queued_papers(base: Path) -> list[dict]:
    """What's waiting in the inbox, with enough detail to show it in the
    library beneath the processed papers."""
    inbox = base / "inbox"
    if not inbox.is_dir():
        return []
    queued = []
    for path in sorted(inbox.iterdir()):
        if path.suffix.lower() not in INBOX_SUFFIXES:
            continue
        info = cached_info(path.stem) or {}
        queued.append({
            "name": path.name,
            "stem": path.stem,
            "kind": "pdf" if path.suffix.lower() == ".pdf" else "article",
            "title": readable_title(path),
            "size_bytes": path.stat().st_size,
            "cover": info.get("cover"),
        })
    return queued


def readable_title(path: Path) -> str:
    """A title for a queued paper: the saved article's own, else its filename."""
    if path.suffix.lower() in (".html", ".htm"):
        try:
            import article

            got = article.extract_article(path.read_text(encoding="utf-8", errors="replace"))
            return article.title_from(got, path.stem)
        except Exception:
            pass
    return path.stem.replace("_", " ").replace("-", " ")


def cmd_library(args) -> dict:
    base = base_dir(args)
    history = QuizHistory(base / "quiz_history.json")
    papers = load_papers(base, history)
    inbox = queued_papers(base)
    review = [{"paper": e["paper"], "question": e["question"], "key": question_key(e["paper"], e["question"])}
              for e in history.review_pile()]
    return {
        "base": str(base),
        "papers": papers,
        "inbox": inbox,
        "review": review,
        "suggested": suggest(papers),
        "stats": overall_stats(papers, history),
    }


def cmd_record(args) -> dict:
    base = base_dir(args)
    history = QuizHistory(base / "quiz_history.json")
    history.record(args.paper, args.question, bool(int(args.correct)))
    history.save()
    return {"ok": True, "key": question_key(args.paper, args.question),
            "review_pile": len(history.review_pile())}


GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partly", "incorrect"]},
        "feedback": {"type": "string"},
    },
    "required": ["verdict", "feedback"],
}


def cmd_grade(args) -> dict:
    # Imported here: only grading needs the pipeline's heavier dependencies.
    import paper_qa_lib
    import textwrap

    prompt = textwrap.dedent(f"""\
        You are marking a researcher's recall of a paper they studied.

        QUESTION: {args.question}
        REFERENCE ANSWER: {args.reference}
        SUPPORTING QUOTE FROM THE PAPER: {args.evidence}

        THEIR ANSWER: {args.answer}

        Mark their answer against the reference:
        - "correct": the same core claim, even if worded differently or briefer.
        - "partly": right as far as it goes, but missing something important.
        - "incorrect": contradicts the reference, or misses the point.

        Then write one short sentence of feedback, addressed to them ("you"),
        saying what they got right or what they missed. Don't repeat the
        reference answer verbatim.

        Respond with JSON only:
        {{"verdict": "correct" | "partly" | "incorrect", "feedback": "<one sentence>"}}
    """)
    graded = paper_qa_lib.ask_json(prompt, args.model or settings.load()["model"], GRADE_SCHEMA, log=lambda m: None)
    return {"verdict": graded.get("verdict", "partly"), "feedback": graded.get("feedback", "")}


def cmd_process_inbox(args) -> dict:
    """Run the nightly job now, streaming its log lines as JSON objects."""
    base_dir(args)
    import process_inbox

    original_log = process_inbox.log

    def streaming_log(message: str):
        original_log(message)
        print(json.dumps({"log": message}), flush=True)

    process_inbox.log = streaming_log
    try:
        problem = process_inbox.main(only=args.only)
        return {"ok": not problem, "error": problem or ""}
    except SystemExit as e:  # e.g. Ollama stopped mid-run
        return {"ok": False, "error": str(e)}


def add_papers(base: Path, paths: list[str]) -> dict:
    """Copy PDFs into the inbox, skipping ones already known.

    Returns what happened to each file, so the app can say so.
    """
    inbox = base / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    added, skipped = [], []
    for raw in paths:
        source = Path(raw)
        if source.suffix.lower() != ".pdf" or not source.is_file():
            skipped.append({"name": source.name, "why": "not a PDF"})
            continue
        if (inbox / source.name).exists():
            skipped.append({"name": source.name, "why": "already in the inbox"})
            continue
        if (base / "library" / source.name).exists():
            skipped.append({"name": source.name, "why": "already processed"})
            continue
        try:
            shutil.copy2(source, inbox / source.name)
        except OSError as e:
            skipped.append({"name": source.name, "why": str(e)})
            continue
        added.append(source.name)
    return {"added": added, "skipped": skipped}


USER_AGENT = "Mozilla/5.0 (compatible; DoTheReading/1.0; +https://github.com/lhmartin/DoTheReading)"


RETRY_STATUSES = {429, 503}


def fetch_with_retry(url: str, attempts: int = 3, timeout: int = 60, log=lambda m: None):
    """GET a URL, waiting out rate limits. Returns the response, or raises
    the last error."""
    import requests

    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
            if response.status_code not in RETRY_STATUSES:
                response.raise_for_status()
                return response
            # Respect Retry-After when the server sends one.
            wait = min(int(response.headers.get("Retry-After", 0) or 0) or 5 * attempt, 30)
            last_error = requests.exceptions.HTTPError(f"{response.status_code} from {url}", response=response)
        except requests.exceptions.RequestException as e:
            last_error, wait = e, 3 * attempt
        if attempt < attempts:
            log(f"the site is busy — waiting {wait}s and trying again ({attempt}/{attempts})")
            time.sleep(wait)
    raise last_error


def save_pdf_to_inbox(base: Path, pdf_url: str, slug: str, log=lambda m: None) -> bool:
    """Last resort when a site won't serve us its text: process the PDF."""
    try:
        response = fetch_with_retry(pdf_url, attempts=2, timeout=120, log=log)
    except Exception:
        return False
    if not response.content[:4] == b"%PDF":
        return False
    inbox = base / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{slug}.pdf").write_bytes(response.content)
    return True


def preview_of(text: str, limit: int = 1500) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def add_text(base: Path, title: str, text: str) -> dict:
    """Save text the reader supplied themselves (pasted, or rendered by the
    app) as an article to process."""
    import article

    title = title.strip() or "Pasted article"
    slug = article.slug_for("https://pasted.local/" + title, title)
    inbox = base / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{slug}.html").write_text(article.as_reader_html(title, text, ""), encoding="utf-8")
    return {"ok": True, "title": title, "slug": slug, "characters": len(text), "pdf": False}


def add_url(base: Path, url: str, log=lambda m: None, html: str | None = None,
            dry_run: bool = False) -> dict:
    """Fetch an article, save it for processing, and keep the PDF to read
    when the source has one (bioRxiv, arXiv)."""
    import article
    import requests

    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return {"ok": False, "error": "That doesn't look like a web address."}

    sources = article.canonical_sources(url)
    page_html = html
    if page_html is None:
        log("fetching the page…")
        try:
            page_html = fetch_with_retry(sources["text_url"], log=log).text
        except requests.exceptions.RequestException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            # A rate-limited site often still serves its PDF, which we can process.
            if sources["pdf_url"] and not dry_run:
                log("the site wouldn't serve its text — trying the PDF instead…")
                slug = article.slug_for(url, None)
                if save_pdf_to_inbox(base, sources["pdf_url"], slug, log=log):
                    return {"ok": True, "title": slug, "slug": slug, "characters": 0, "pdf": True,
                            "note": "That site is rate-limiting us, so the PDF was added instead. "
                                    "Adding the link again later gets the cleaner web text."}
            if status in RETRY_STATUSES:
                return {"ok": False, "error": "That site is rate-limiting us. Wait a minute and try again."}
            return {"ok": False, "error": f"Couldn't fetch that page: {e}"}

    got = article.extract_article(page_html)
    if len(got["text"]) < 1000:
        # Most likely a JavaScript-rendered page: nothing to read in the HTML
        # itself. The app can render it and try again.
        return {"ok": False, "needs_render": html is None,
                "error": "That page needs a browser to render it — nothing readable in the HTML."}

    # bioRxiv serves the abstract page when a paper has no full text; the PDF
    # is then the only way to read the whole thing.
    if len(got["text"]) < MIN_FULL_TEXT and sources["pdf_url"]:
        slug = article.slug_for(url, article.title_from(got, url))
        if dry_run:
            return {"ok": True, "dry_run": True, "title": article.title_from(got, url), "slug": slug,
                    "characters": len(got["text"]), "source": "pdf", "headings": [],
                    "preview": preview_of(got["text"]),
                    "note": "Only the abstract is published as web text, so the PDF will be used."}
        log("that page only has the abstract — fetching the PDF instead…")
        if save_pdf_to_inbox(base, sources["pdf_url"], slug, log=log):
            return {"ok": True, "title": article.title_from(got, url), "slug": slug,
                    "characters": len(got["text"]), "pdf": True,
                    "note": "Only the abstract is published as web text, so the PDF was queued instead."}

    title = article.title_from(got, url)
    slug = article.slug_for(url, title)
    if dry_run:
        return {"ok": True, "dry_run": True, "title": title, "slug": slug, "source": "web",
                "characters": len(got["text"]), "preview": preview_of(got["text"]),
                "headings": [b[3:] for b in got["text"].split("\n\n") if b.startswith("## ")][:12]}
    inbox = base / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{slug}.html").write_text(article.as_reader_html(title, got["text"], url), encoding="utf-8")

    # The typeset PDF is nicer to read than our stripped-down page.
    pdf_saved = False
    if sources["pdf_url"]:
        log("fetching the PDF…")
        try:
            pdf = requests.get(sources["pdf_url"], timeout=120, headers={"User-Agent": USER_AGENT})
            if pdf.ok and pdf.content[:4] == b"%PDF":
                library = base / "library"
                library.mkdir(parents=True, exist_ok=True)
                (library / f"{slug}.pdf").write_bytes(pdf.content)
                pdf_saved = True
        except requests.exceptions.RequestException:
            pass  # the article text is what matters; the PDF is a bonus

    return {"ok": True, "title": title, "slug": slug, "characters": len(got["text"]), "pdf": pdf_saved}


def cmd_add_url(args) -> dict:
    html = Path(args.html_file).read_text(encoding="utf-8", errors="replace") if args.html_file else None
    return add_url(base_dir(args), args.url, log=lambda m: print(json.dumps({"log": m}), flush=True),
                   html=html, dry_run=bool(args.dry_run))


def cmd_add_text(args) -> dict:
    text = Path(args.text_file).read_text(encoding="utf-8", errors="replace")
    if len(text.strip()) < 500:
        return {"ok": False, "error": "That's too short to make questions from — paste the whole article."}
    return add_text(base_dir(args), args.title, text.strip())


def remove_from_inbox(base: Path, name: str) -> dict:
    """Drop a queued paper. Only touches files inside the inbox."""
    target = (base / "inbox" / name).resolve()
    inbox = (base / "inbox").resolve()
    if inbox not in target.parents or target.suffix.lower() not in INBOX_SUFFIXES:
        return {"ok": False, "error": "That isn't a queued paper."}
    if not target.exists():
        return {"ok": False, "error": "That paper isn't in the queue any more."}
    target.unlink()
    return {"ok": True, "removed": name}


def shelve_without_questions(base: Path, name: str) -> dict:
    """Move a queued paper straight to the library to read, no questions.

    The library is built from the question files, so it gets one — with a
    title and no questions — which is also where its notes will live.
    """
    import article
    from qa_format import render_markdown

    source = (base / "inbox" / name).resolve()
    inbox = (base / "inbox").resolve()
    if inbox not in source.parents or source.suffix.lower() not in INBOX_SUFFIXES or not source.exists():
        return {"ok": False, "error": "That isn't a queued paper."}

    title = readable_title(source)
    library = base / "library"
    questions_dir = base / "questions"
    library.mkdir(parents=True, exist_ok=True)
    questions_dir.mkdir(parents=True, exist_ok=True)
    questions_path = questions_dir / f"{source.stem}_questions.md"
    if not questions_path.exists():
        questions_path.write_text(
            render_markdown(title, [], note=f"{name} · added for reading; no questions generated."),
            encoding="utf-8")
    # (an existing file is left alone, notes and all)
    shutil.move(str(source), str(library / source.name))
    return {"ok": True, "title": title, "stem": source.stem}


def cmd_shelve(args) -> dict:
    return shelve_without_questions(base_dir(args), args.name)


def cmd_notes(args) -> dict:
    from qa_format import read_notes

    path = base_dir(args) / "questions" / f"{args.paper}_questions.md"
    if not path.exists():
        return {"ok": False, "error": "No question file for that paper."}
    return {"ok": True, "paper": args.paper, "notes": read_notes(path.read_text(encoding="utf-8"))}


def cmd_save_notes(args) -> dict:
    from qa_format import write_notes

    path = base_dir(args) / "questions" / f"{args.paper}_questions.md"
    if not path.exists():
        return {"ok": False, "error": "No question file for that paper."}
    text = Path(args.text_file).read_text(encoding="utf-8", errors="replace") if args.text_file else ""
    path.write_text(write_notes(path.read_text(encoding="utf-8"), text), encoding="utf-8")
    return {"ok": True, "paper": args.paper, "characters": len(text.strip())}


def cmd_remove_paper(args) -> dict:
    return remove_from_inbox(base_dir(args), args.name)


def cmd_add_papers(args) -> dict:
    return add_papers(base_dir(args), args.files)


def ollama_get(path: str, timeout: float = 3.0):
    import requests

    return requests.get(f"{OLLAMA_HOST}{path}", timeout=timeout).json()


def cmd_settings(args) -> dict:
    base_dir(args)
    return {"settings": settings.load(), "defaults": settings.DEFAULTS, "path": str(settings.settings_path())}


def cmd_save_settings(args) -> dict:
    base_dir(args)
    changes = {}
    if args.model:
        changes["model"] = args.model
    if args.num_questions:
        changes["num_questions"] = int(args.num_questions)
    if args.guidance is not None:
        changes["guidance"] = args.guidance
    return {"settings": settings.save(changes)}


def cmd_environment(args) -> dict:
    """What the pipeline needs, and whether it's there: for the app's setup
    checklist."""
    base_dir(args)
    current = settings.load()
    installed, running, version = [], False, None
    try:
        version = ollama_get("/api/version").get("version")
        running = True
        installed = [{"name": m["name"], "size_bytes": m.get("size")} for m in ollama_get("/api/tags").get("models", [])]
    except Exception:
        pass

    tesseract = None
    try:
        import paper_qa_lib

        tesseract = paper_qa_lib.ocr_available()
    except Exception:
        pass

    return {
        "ollama": {"running": running, "version": version},
        "models": {
            "installed": installed,
            "suggested": SUGGESTED_MODELS,
            "selected": current["model"],
            "selected_installed": any(m["name"] == current["model"] for m in installed),
        },
        "tesseract": tesseract,
        "memory_settings": memory_settings_state(),
        "scheduled_task": scheduled_task_state(),
        "power": power_state(),
        "settings": current,
        "base": str(settings.base_dir()),
    }


def cmd_pull_model(args) -> dict:
    """Pull a model, streaming Ollama's progress as {"log": ...} lines."""
    import requests

    if not ollama_is_up():
        return {"ok": False, "error": OLLAMA_NOT_RUNNING}
    seen = None
    try:
        response_context = requests.post(f"{OLLAMA_HOST}/api/pull", json={"model": args.model}, stream=True, timeout=None)
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": ollama_message(e)}
    with response_context as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            update = json.loads(line)
            if update.get("error"):
                return {"ok": False, "error": update["error"]}
            status = update.get("status", "")
            total, completed = update.get("total"), update.get("completed")
            if total and completed:
                percent = int(completed / total * 100)
                status = f"{status} — {percent}% of {total / 1e9:.1f} GB"
            if status != seen:
                seen = status
                print(json.dumps({"log": status}), flush=True)
    return {"ok": True, "model": args.model}


# ---- Ollama memory settings (Windows) ------------------------------------
# Without these the KV cache is twice the size, the 32B model spills onto the
# CPU and runs ~3x slower. Ollama reads them at startup. setup.ps1 calls this
# too, so there's one implementation.

OLLAMA_MEMORY_ENV = {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"}


def read_user_env(name: str) -> str | None:
    """A user-scope environment variable as Windows has it stored (which is
    what Ollama will see next time it starts), or None."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except (FileNotFoundError, OSError):
        return None


def memory_settings_state(read=None) -> dict:
    if platform.system() != "Windows":
        return {"supported": False, "ok": True, "values": {}}
    read = read or read_user_env
    values = {name: read(name) for name in OLLAMA_MEMORY_ENV}
    ok = all(values[name] == wanted for name, wanted in OLLAMA_MEMORY_ENV.items())
    return {"supported": True, "ok": ok, "values": values, "wanted": OLLAMA_MEMORY_ENV}


OLLAMA_NOT_RUNNING = "Ollama isn't running. Start it from the Start menu (or the button in Settings), then try again."


LLAMA_CRASH_HINT = (
    "Ollama's model server crashed while loading or running the model. Try, in order: "
    "turn off the speed settings in Settings and retry; re-download the model; update Ollama."
)


def ollama_message(error: Exception) -> str:
    """Ollama's failures in words rather than stack traces."""
    import requests

    if isinstance(error, requests.exceptions.ConnectionError):
        return OLLAMA_NOT_RUNNING
    if isinstance(error, requests.exceptions.Timeout):
        return "Ollama stopped responding. Check it's still running, then try again."
    return f"Ollama error: {error}"


def ollama_is_up(timeout: float = 2.0) -> bool:
    try:
        ollama_get("/api/version", timeout=timeout)
        return True
    except Exception:
        return False


def ollama_paths() -> list[list[str]]:
    """Ways to launch Ollama, best first: the tray app keeps the server alive
    the way a normal install does."""
    candidates = []
    for root in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", "")):
        if root:
            candidates.append([str(Path(root) / "Programs" / "Ollama" / "ollama app.exe")])
            candidates.append([str(Path(root) / "Ollama" / "ollama app.exe")])
    found = shutil.which("ollama")
    if found:
        candidates.append([found, "serve"])
    return [c for c in candidates if c[0].endswith("serve") or Path(c[0]).is_file()]


def start_ollama(wait_seconds: int = 30) -> bool:
    """Launch Ollama and wait until it answers. True if it's up."""
    if ollama_is_up():
        return True
    for command in ollama_paths():
        try:
            subprocess.Popen(command, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            continue
        for _ in range(wait_seconds):
            time.sleep(1)
            if ollama_is_up():
                return True
    return False


def restart_ollama() -> bool:
    """Stop Ollama and start it again so it picks up new settings."""
    for image in ("ollama app.exe", "ollama.exe"):
        subprocess.run(["taskkill", "/f", "/im", image], capture_output=True)
    time.sleep(2)
    return start_ollama()


def cmd_start_ollama(args) -> dict:
    started = start_ollama()
    return {"ok": started, "error": "" if started else "Couldn't start Ollama — launch it from the Start menu."}


def cmd_ollama_memory(args) -> dict:
    if platform.system() != "Windows":
        return {"ok": False, "error": "Windows only.", "state": memory_settings_state()}
    if args.action == "check":
        return {"ok": True, "state": memory_settings_state()}

    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        for name, value in OLLAMA_MEMORY_ENV.items():
            if args.action == "clear":
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
                os.environ.pop(name, None)
            else:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
                os.environ[name] = value
    restarted = restart_ollama()
    return {"ok": True, "restarted": restarted, "running": ollama_is_up(),
            "error": "" if restarted else "Settings saved, but Ollama didn't come back up — start it from Settings.",
            "state": memory_settings_state()}


# ---- power plan (Windows) ------------------------------------------------
# A 02:00 task can only wake a sleeping laptop if wake timers are allowed and
# the lid puts it to sleep rather than hibernating it. This only reports:
# changing someone's power plan behind their back is not our business.

LID_ACTIONS = {0: "stays awake", 1: "sleeps", 2: "hibernates", 3: "shuts down"}
WAKE_TIMER_VALUES = {0: "off", 1: "on", 2: "important events only"}


def parse_powercfg_value(output: str, on_battery: bool = False) -> int | None:
    """Pull the AC (or DC) index out of `powercfg /q` output."""
    wanted = "Current DC Power Setting Index:" if on_battery else "Current AC Power Setting Index:"
    for line in output.splitlines():
        if wanted in line:
            try:
                return int(line.split(":")[1].strip(), 16)
            except ValueError:
                return None
    return None


def query_power_setting(subgroup: str, setting: str) -> int | None:
    try:
        done = subprocess.run(["powercfg", "/q", "SCHEME_CURRENT", subgroup, setting],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_powercfg_value(done.stdout) if done.returncode == 0 else None


def power_state() -> dict:
    """Whether a sleeping machine would actually wake for the nightly run."""
    if platform.system() != "Windows":
        return {"supported": False, "ok": True, "checks": []}

    wake = query_power_setting("SUB_SLEEP", "RTCWAKE")
    lid = query_power_setting("SUB_BUTTONS", "LIDACTION")
    hibernate_after = query_power_setting("SUB_SLEEP", "HIBERNATEIDLE")

    checks = [{
        "name": "Wake timers",
        "value": WAKE_TIMER_VALUES.get(wake, "unknown"),
        "ok": wake in (1, 2),
        "detail": "The nightly task can't wake the machine without these.",
        "fix": "powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1",
    }, {
        "name": "Closing the lid",
        "value": LID_ACTIONS.get(lid, "unknown"),
        "ok": lid in (0, 1),
        "detail": "A wake timer can wake a sleeping machine, but not a hibernated or shut down one.",
        "fix": "powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 1",
    }]
    if hibernate_after:
        hours = hibernate_after / 3600
        checks.append({
            "name": "Hibernates after sleeping",
            "value": f"{hours:.0f} h",
            "ok": hours >= 8,
            "detail": "If it hibernates before 02:00, the run is missed.",
            "fix": "powercfg /change hibernate-timeout-ac 0",
        })
    checks.append({
        "name": "On battery",
        "value": "the run is skipped",
        "ok": True,
        "detail": "Windows doesn't start scheduled tasks on battery, so leave it plugged in.",
        "fix": "",
    })
    return {"supported": True, "ok": all(c["ok"] for c in checks), "checks": checks}


def cmd_power(args) -> dict:
    return {"ok": True, "power": power_state()}


# ---- the nightly scheduled task (Windows) --------------------------------

def scheduled_task_state() -> dict:
    if platform.system() != "Windows":
        return {"supported": False, "registered": False}
    try:
        found = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME], capture_output=True, text=True)
        return {"supported": True, "registered": found.returncode == 0, "name": TASK_NAME}
    except OSError:
        return {"supported": False, "registered": False}


def cmd_schedule(args) -> dict:
    """Register or remove the nightly task, from the app's settings."""
    if platform.system() != "Windows":
        return {"ok": False, "error": "Scheduling is Windows-only."}

    if args.action == "remove":
        done = subprocess.run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"], capture_output=True, text=True)
        return {"ok": done.returncode == 0, "error": done.stderr.strip(), "state": scheduled_task_state()}

    # Run this same executable (frozen study_api.exe, or python study_api.py).
    if getattr(sys, "frozen", False):
        command = f'"{sys.executable}" process-inbox'
    else:
        command = f'"{sys.executable}" "{Path(__file__).resolve()}" process-inbox'
    script = (
        f"$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c {command}';"
        f"$trigger = New-ScheduledTaskTrigger -Daily -At {args.time};"
        "$settings = New-ScheduledTaskSettingsSet -WakeToRun;"
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $trigger "
        "-Settings $settings -Description 'Generate study questions for new PDFs' -Force | Out-Null"
    )
    done = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True)
    return {"ok": done.returncode == 0, "error": done.stderr.strip(), "state": scheduled_task_state()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="PaperStudy folder (default: ~/PaperStudy)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("library")

    record = sub.add_parser("record")
    record.add_argument("--paper", required=True)
    record.add_argument("--question", required=True)
    record.add_argument("--correct", required=True, choices=["0", "1"])

    grade = sub.add_parser("grade")
    for name in ("paper", "question", "reference", "answer"):
        grade.add_argument(f"--{name}", required=True)
    grade.add_argument("--evidence", default="")
    grade.add_argument("--model", default="")

    run_now = sub.add_parser("process-inbox")
    run_now.add_argument("--only", nargs="*", help="file names to process; default is everything queued")
    sub.add_parser("settings")
    sub.add_parser("environment")

    save_settings = sub.add_parser("save-settings")
    save_settings.add_argument("--model")
    save_settings.add_argument("--num-questions", dest="num_questions")
    save_settings.add_argument("--guidance")

    add_link = sub.add_parser("add-url")
    add_link.add_argument("--url", required=True)
    add_link.add_argument("--html-file", dest="html_file", help="use this rendered HTML instead of fetching")
    add_link.add_argument("--dry-run", dest="dry_run", action="store_true", help="extract but don't save")

    shelve = sub.add_parser("shelve")
    shelve.add_argument("--name", required=True)

    notes = sub.add_parser("notes")
    notes.add_argument("--paper", required=True)

    save_notes_cmd = sub.add_parser("save-notes")
    save_notes_cmd.add_argument("--paper", required=True)
    save_notes_cmd.add_argument("--text-file", dest="text_file", default="")

    remove = sub.add_parser("remove-paper")
    remove.add_argument("--name", required=True)

    add_pasted = sub.add_parser("add-text")
    add_pasted.add_argument("--title", default="")
    add_pasted.add_argument("--text-file", dest="text_file", required=True)

    add = sub.add_parser("add-papers")
    add.add_argument("--files", nargs="+", required=True)

    pull = sub.add_parser("pull-model")
    pull.add_argument("--model", required=True)

    sub.add_parser("start-ollama")
    sub.add_parser("power")

    info = sub.add_parser("paper-info")
    info.add_argument("--paper", required=True)

    memory = sub.add_parser("ollama-memory")
    memory.add_argument("--action", required=True, choices=["check", "set", "clear"])

    schedule = sub.add_parser("schedule")
    schedule.add_argument("--action", required=True, choices=["add", "remove"])
    schedule.add_argument("--time", default="02:00")

    args = parser.parse_args()
    commands = {"library": cmd_library, "record": cmd_record, "grade": cmd_grade,
                "process-inbox": cmd_process_inbox, "settings": cmd_settings,
                "save-settings": cmd_save_settings, "environment": cmd_environment,
                "pull-model": cmd_pull_model, "schedule": cmd_schedule,
                "add-papers": cmd_add_papers, "add-url": cmd_add_url, "add-text": cmd_add_text, "remove-paper": cmd_remove_paper, "shelve": cmd_shelve,
                "notes": cmd_notes, "save-notes": cmd_save_notes, "ollama-memory": cmd_ollama_memory,
                "start-ollama": cmd_start_ollama, "power": cmd_power, "paper-info": cmd_paper_info}
    try:
        result = commands[args.command](args)
    except Exception as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}), flush=True)
        raise SystemExit(1)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
