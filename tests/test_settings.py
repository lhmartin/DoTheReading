import json

import pytest

import settings
from paper_qa_lib import build_prompt


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPERSTUDY_DIR", str(tmp_path))
    return tmp_path


def test_defaults_when_no_file(base):
    assert settings.load() == settings.DEFAULTS


def test_save_then_load_round_trips(base):
    settings.save({"model": "qwen2.5:7b", "num_questions": 5, "guidance": "focus on controls"})
    assert settings.load() == {"model": "qwen2.5:7b", "num_questions": 5, "guidance": "focus on controls"}
    assert json.loads((base / "settings.json").read_text())["model"] == "qwen2.5:7b"


def test_save_merges_and_clamps(base):
    settings.save({"num_questions": 999})
    assert settings.load()["num_questions"] == 50
    settings.save({"guidance": "only methods"})
    assert settings.load()["num_questions"] == 50, "untouched fields survive"


def test_broken_file_falls_back_to_defaults(base):
    (base / "settings.json").write_text("{not json")
    assert settings.load() == settings.DEFAULTS


def test_wrong_types_are_ignored(base):
    (base / "settings.json").write_text(json.dumps({"model": 42, "num_questions": "lots", "guidance": "keep me"}))
    loaded = settings.load()
    assert loaded["model"] == settings.DEFAULTS["model"]
    assert loaded["num_questions"] == settings.DEFAULTS["num_questions"]
    assert loaded["guidance"] == "keep me"


def test_guidance_reaches_the_prompt():
    prompt = build_prompt("TEXT", 5, "the full text", guidance="  I'm a wet-lab biologist.  ")
    assert "I'm a wet-lab biologist." in prompt
    assert prompt.index("I'm a wet-lab biologist.") < prompt.index("Include a mix of")
    assert "What this reader wants" not in build_prompt("TEXT", 5, "the full text", guidance="   ")


def test_memory_settings_state_off_windows(monkeypatch):
    import study_api

    monkeypatch.setattr(study_api.platform, "system", lambda: "Linux")
    assert study_api.memory_settings_state() == {"supported": False, "ok": True, "values": {}}


@pytest.mark.parametrize("stored, ok", [
    ({"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"}, True),
    ({"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": None}, False),
    ({"OLLAMA_FLASH_ATTENTION": None, "OLLAMA_KV_CACHE_TYPE": None}, False),
    ({"OLLAMA_FLASH_ATTENTION": "0", "OLLAMA_KV_CACHE_TYPE": "q8_0"}, False),
])
def test_memory_settings_state_reads_stored_values(monkeypatch, stored, ok):
    import study_api

    monkeypatch.setattr(study_api.platform, "system", lambda: "Windows")
    state = study_api.memory_settings_state(read=stored.get)
    assert state["ok"] is ok and state["supported"] is True
