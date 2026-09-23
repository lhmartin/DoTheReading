import study_api

AC_AND_DC = """
    Power Setting GUID: bd3b718a-0680-4d9d-8ab2-e1d2b4ac806d  (Allow wake timers)
      Possible Setting Index: 000
      Current AC Power Setting Index: 0x00000001
      Current DC Power Setting Index: 0x00000000
"""


def test_parses_the_ac_and_dc_indexes():
    assert study_api.parse_powercfg_value(AC_AND_DC) == 1
    assert study_api.parse_powercfg_value(AC_AND_DC, on_battery=True) == 0


def test_missing_or_odd_output_is_not_a_crash():
    assert study_api.parse_powercfg_value("") is None
    assert study_api.parse_powercfg_value("Current AC Power Setting Index: nonsense") is None


def test_power_state_is_quiet_off_windows(monkeypatch):
    monkeypatch.setattr(study_api.platform, "system", lambda: "Linux")
    assert study_api.power_state() == {"supported": False, "ok": True, "checks": []}


def test_power_state_flags_a_hibernating_lid(monkeypatch):
    monkeypatch.setattr(study_api.platform, "system", lambda: "Windows")
    values = {("SUB_SLEEP", "RTCWAKE"): 0, ("SUB_BUTTONS", "LIDACTION"): 2, ("SUB_SLEEP", "HIBERNATEIDLE"): 3600}
    monkeypatch.setattr(study_api, "query_power_setting", lambda sub, name: values[(sub, name)])
    state = study_api.power_state()
    assert state["ok"] is False
    by_name = {c["name"]: c for c in state["checks"]}
    assert by_name["Wake timers"]["value"] == "off" and not by_name["Wake timers"]["ok"]
    assert by_name["Closing the lid"]["value"] == "hibernates" and not by_name["Closing the lid"]["ok"]
    assert by_name["Hibernates after sleeping"]["value"] == "1 h" and not by_name["Hibernates after sleeping"]["ok"]


def test_power_state_is_happy_when_set_up(monkeypatch):
    monkeypatch.setattr(study_api.platform, "system", lambda: "Windows")
    values = {("SUB_SLEEP", "RTCWAKE"): 1, ("SUB_BUTTONS", "LIDACTION"): 1, ("SUB_SLEEP", "HIBERNATEIDLE"): 0}
    monkeypatch.setattr(study_api, "query_power_setting", lambda sub, name: values[(sub, name)])
    state = study_api.power_state()
    assert state["ok"] is True
    assert [c["name"] for c in state["checks"]] == ["Wake timers", "Closing the lid", "On battery"]
