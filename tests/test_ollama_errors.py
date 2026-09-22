import argparse

import pytest
import requests

import study_api


def test_connection_error_becomes_plain_english():
    message = study_api.ollama_message(requests.exceptions.ConnectionError("[WinError 10061] refused"))
    assert message == study_api.OLLAMA_NOT_RUNNING
    assert "WinError" not in message and "HTTPConnectionPool" not in message


def test_timeout_has_its_own_message():
    assert "stopped responding" in study_api.ollama_message(requests.exceptions.Timeout())


def test_pull_reports_a_stopped_ollama_instead_of_raising(monkeypatch):
    monkeypatch.setattr(study_api, "ollama_is_up", lambda timeout=2.0: False)
    result = study_api.cmd_pull_model(argparse.Namespace(model="qwen2.5:14b", base=None))
    assert result == {"ok": False, "error": study_api.OLLAMA_NOT_RUNNING}


def test_pull_maps_a_connection_failure_mid_request(monkeypatch):
    monkeypatch.setattr(study_api, "ollama_is_up", lambda timeout=2.0: True)

    def refuse(*args, **kwargs):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", refuse)
    result = study_api.cmd_pull_model(argparse.Namespace(model="m", base=None))
    assert result["ok"] is False and result["error"] == study_api.OLLAMA_NOT_RUNNING


def test_start_ollama_is_a_no_op_when_already_up(monkeypatch):
    monkeypatch.setattr(study_api, "ollama_is_up", lambda timeout=2.0: True)
    monkeypatch.setattr(study_api, "ollama_paths", lambda: pytest.fail("shouldn't launch anything"))
    assert study_api.start_ollama() is True
