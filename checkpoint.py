"""Partial work for a paper being processed.

A long paper is dozens of model calls over half an hour; if the run stops
(the app quits, the machine sleeps), the sections already written should not
have to be written again. Kept in the OS cache, not ~/PaperStudy, because
it's scratch data. Stdlib only.
"""

import hashlib
import json
import os
import platform
from pathlib import Path


def cache_dir() -> Path:
    if platform.system() == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "DoTheReading"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "dothereading"
    path = root / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Checkpoint:
    """Sections generated so far for one paper.

    The text's fingerprint is part of the file, so re-processing a changed
    paper starts fresh rather than resuming onto different text.
    """

    def __init__(self, name: str, text: str):
        self.path = cache_dir() / f"{name}.progress.json"
        self.fingerprint = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]
        self.sections_done = 0
        self.candidates: list[dict] = []
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if saved.get("fingerprint") != self.fingerprint:
            return  # the paper changed; the old progress doesn't apply
        self.sections_done = int(saved.get("sections_done", 0))
        self.candidates = saved.get("candidates", [])

    def save(self, sections_done: int, candidates: list[dict]):
        self.sections_done = sections_done
        self.candidates = candidates
        payload = {"fingerprint": self.fingerprint, "sections_done": sections_done, "candidates": candidates}
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # a checkpoint is an optimisation, never a reason to fail

    def clear(self):
        self.path.unlink(missing_ok=True)
