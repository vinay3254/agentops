import json

import pytest

from agentops.db import connect
from agentops.trace import TraceStore, scrub


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


def test_api_key_with_quotes_and_backslash(tmp_path, monkeypatch):
    key = 'sk-or-"quoted"-back\\slash-99999'
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    conn, store = make(tmp_path)
    store.event("r", "llm_call", {"text": f"the key is {key}"})
    raw = conn.execute("SELECT payload FROM events").fetchone()[0]
    assert key not in raw
    assert "***" in raw
    evs = store.events(run_id="r")
    assert len(evs) == 1
    assert "***" in evs[0]["payload"]["text"]


def test_api_key_with_non_ascii(tmp_path, monkeypatch):
    key = "sk-or-é-ü-123456789"
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    conn, store = make(tmp_path)
    store.event("r", "llm_call", {"text": f"the key is {key}"})
    raw = conn.execute("SELECT payload FROM events").fetchone()[0]
    assert key not in raw
    assert "***" in raw
    evs = store.events(run_id="r")
    assert len(evs) == 1
    assert "***" in evs[0]["payload"]["text"]


def test_scrub_none_returns_none(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret-123456789")
    assert scrub(None) is None


def test_scrub_short_key_not_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "short")
    conn, store = make(tmp_path)
    store.event("r", "llm_call", {"text": "the key is short"})
    raw = conn.execute("SELECT payload FROM events").fetchone()[0]
    assert "short" in raw
    assert "***" not in raw


@pytest.mark.parametrize("key", [
    '"abcdefgh',  # starts with quote
    'abcdefgh\\',  # ends with backslash
    'sk-or-"quoted"-back\\slash-99999',  # mixed special chars
    'sk-or-é-ü-123456789',  # non-ASCII
    'plain-key-123456789',  # plain ASCII
])
def test_scrubbing_maintains_json_validity(tmp_path, monkeypatch, key):
    """Test that scrubbing produces valid JSON and removes all key forms."""
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    conn, store = make(tmp_path)

    # Test with key at start, middle, and end of value
    text_variants = [
        f"{key} rest of text",
        f"start of text {key} middle",
        f"start {key}",
    ]

    for idx, text in enumerate(text_variants):
        payload = {"text": text}
        store.event("r", "llm_call", payload)

    # Get raw stored JSON from database
    rows = conn.execute("SELECT payload FROM events WHERE run_id=?", ("r",)).fetchall()

    for raw_json in rows:
        raw_payload_str = raw_json[0]

        # (1) Raw stored payload must decode as valid JSON
        decoded = json.loads(raw_payload_str)
        assert isinstance(decoded, dict)
        assert "text" in decoded

        # (2) Must not contain any form of the key
        assert key not in raw_payload_str  # raw key

        # Also check JSON-escaped forms
        json_escaped = json.dumps(key)[1:-1]
        if json_escaped:
            assert json_escaped not in raw_payload_str

        json_escaped_no_ascii = json.dumps(key, ensure_ascii=False)[1:-1]
        if json_escaped_no_ascii and json_escaped_no_ascii != json_escaped:
            assert json_escaped_no_ascii not in raw_payload_str

    # (3) store.events() must successfully decode and return payload
    events = store.events(run_id="r")
    assert len(events) == 3
    for event in events:
        assert "text" in event["payload"]
        assert "***" in event["payload"]["text"]
