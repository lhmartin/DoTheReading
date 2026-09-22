"""User settings, shared by the nightly job, the CLI and the desktop app.

Stored as JSON in ~/PaperStudy/settings.json, so changing the model in the
app also changes what the 02:00 task uses. Stdlib only.

    model         which Ollama model generates and verifies questions
    num_questions how many questions to keep per paper
    guidance      free text appended to the generation prompt, e.g.
                  "I'm a wet-lab biologist; go easy on the maths."
"""

import json
import os
from pathlib import Path

# The 32B models are the default because verification is only as good as the
# model's reading: in testing, 14b kept an answer that stated the opposite of
# the paper, while both 32b quantizations rejected it every time.
DEFAULTS = {
    "model": "qwen2.5:32b-instruct-q3_K_S",
    "num_questions": 12,
    "guidance": "",
}

FIELDS = {"model": str, "num_questions": int, "guidance": str}


def base_dir() -> Path:
    return Path(os.environ.get("PAPERSTUDY_DIR") or (Path.home() / "PaperStudy"))


def settings_path() -> Path:
    return base_dir() / "settings.json"


def load() -> dict:
    """Current settings, with defaults filled in. A missing or broken file
    is not fatal — the nightly job should still run."""
    settings = dict(DEFAULTS)
    path = settings_path()
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return settings
        for key, kind in FIELDS.items():
            if key in stored and isinstance(stored[key], kind) and not isinstance(stored[key], bool):
                settings[key] = stored[key]
    settings["num_questions"] = max(1, min(50, settings["num_questions"]))
    return settings


def save(changes: dict) -> dict:
    """Merge `changes` into the stored settings and write them back."""
    settings = load()
    for key, kind in FIELDS.items():
        if key in changes:
            value = changes[key]
            if kind is int:
                value = int(value)
            elif not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            settings[key] = value
    settings["num_questions"] = max(1, min(50, settings["num_questions"]))

    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return settings
