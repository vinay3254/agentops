from agentops.db import connect
from agentops.trace import TraceStore


def make(tmp_path):
    conn = connect(tmp_path / "t.db")
    return conn, TraceStore(conn)


def test_event_roundtrip(tmp_path):
    _, store = make(tmp_path)
    store.event("r1", "tool_call", {"name": "x"}, step=1, incident_id="i1", tokens=10, cost=0.01)
    store.event("r2", "tool_call", {"name": "y"})
    evs = store.events(run_id="r1")
    assert len(evs) == 1
    assert evs[0]["payload"] == {"name": "x"}
    assert evs[0]["tokens"] == 10 and evs[0]["incident_id"] == "i1"
    assert len(store.events(incident_id="i1")) == 1


def test_api_key_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-123456789")
    conn, store = make(tmp_path)
    store.event("r", "llm_call", {"text": "the key is sk-or-secret-123456789"})
    raw = conn.execute("SELECT payload FROM events").fetchone()[0]
    assert "sk-or-secret" not in raw
    assert "***" in raw


def test_incident_lifecycle(tmp_path):
    _, store = make(tmp_path)
    store.open_incident("i1", "api down", "agent", fault="crash")
    row = store.incident("i1")
    assert row["status"] == "open" and row["closed"] is None and row["fault"] == "crash"
    store.close_incident("i1", "resolved", "finished", steps=4, tokens=900, cost=0.02)
    row = store.incident("i1")
    assert row["status"] == "resolved" and row["steps"] == 4 and row["closed"] >= row["opened"]
    assert store.incidents()[0]["id"] == "i1"
    assert store.incident("missing") is None
