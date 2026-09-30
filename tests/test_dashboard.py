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
