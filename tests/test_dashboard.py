from pathlib import Path

from streamlit.testing.v1 import AppTest

from agentops import config
from agentops.db import connect
from agentops.trace import TraceStore

APP = str(Path(__file__).resolve().parent.parent / "agentops" / "dashboard.py")


def test_renders_with_empty_database(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOPS_DASHBOARD_OFFLINE", "1")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "empty.db")
    monkeypatch.setattr(config, "ROOT", tmp_path)  # no eval/ files here
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Live", "Traces", "Eval"]


def test_renders_seeded_incident(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTOPS_DASHBOARD_OFFLINE", "1")
    db = tmp_path / "seed.db"
    monkeypatch.setattr(config, "DB_PATH", db)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    trace = TraceStore(connect(db))
    trace.open_incident("agent-crash-1", "api unreachable", "agent", "crash")
    trace.event("agent-crash-1", "tool_call", {"name": "restart_service", "args": {"service": "api"}},
                step=1, incident_id="agent-crash-1")
    trace.close_incident("agent-crash-1", "resolved", "finished", 2, 300, 0.01)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception


# ---- non-offline (live) mode -------------------------------------------------

import pytest  # noqa: E402

from agentops import chaos, executor, verifier  # noqa: E402
from agentops.verifier import VerifyResult  # noqa: E402


class FakeExecutor:
    def status(self):
        return {s: {"state": "running"} for s in ("gateway", "api", "worker", "redis")}


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTOPS_DASHBOARD_OFFLINE", raising=False)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "live.db")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(executor, "Executor", FakeExecutor)
    calls = {"verify": 0}

    def fake_verify(*a, **k):
        calls["verify"] += 1
        return VerifyResult(True)

    monkeypatch.setattr(verifier, "verify", fake_verify)
    return calls


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def test_health_checks_run_once_and_only_on_demand(live):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert live["verify"] == 1
    at.run()  # unrelated rerun: cached health is reused
    assert not at.exception
    assert live["verify"] == 1
    _button(at, "Check health now").click().run()
    assert live["verify"] == 2
    assert any("Stack healthy" in s.value for s in at.success)


def test_unrelated_widget_change_does_not_reverify(tmp_path, live):
    db = tmp_path / "live.db"
    trace = TraceStore(connect(db))
    trace.open_incident("i-1", "x", "agent", "crash")
    trace.open_incident("i-2", "x", "agent", "crash")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert live["verify"] == 1
    next(s for s in at.selectbox if s.label == "Incident").select("i-2").run()
    assert not at.exception
    assert live["verify"] == 1


def test_inject_failure_is_shown_not_raised(live, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(chaos, "inject", boom)
    at = AppTest.from_file(APP, default_timeout=30).run()
    _button(at, "Inject fault").click().run()
    assert not at.exception
    assert any("Inject failed" in e.value and "boom" in e.value for e in at.error)
    assert [t.label for t in at.tabs] == ["Live", "Traces", "Eval"]


def test_reset_failure_is_shown_not_raised(live, monkeypatch):
    def boom(*a, **k):
        raise chaos.ChaosError("nope")

    monkeypatch.setattr(chaos, "reset", boom)
    at = AppTest.from_file(APP, default_timeout=30).run()
    _button(at, "Reset stack").click().run()
    assert not at.exception
    assert any("Reset failed" in e.value and "ChaosError" in e.value for e in at.error)


def test_verifier_exception_becomes_unhealthy_banner(live, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(verifier, "verify", boom)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("verifier error" in e.value for e in at.error)
