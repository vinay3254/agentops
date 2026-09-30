from agentops.checkpoint import CheckpointStore
from agentops.db import connect


def test_latest_returns_highest_step(tmp_path):
    store = CheckpointStore(connect(tmp_path / "c.db"))
    store.save("r", 1, [{"role": "user", "content": "a"}], {"step": 1})
    store.save("r", 2, [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}], {"step": 2})
    cp = store.latest("r")
    assert cp["step"] == 2
    assert len(cp["messages"]) == 2
    assert cp["state"] == {"step": 2}


def test_latest_none_for_unknown_run(tmp_path):
    assert CheckpointStore(connect(tmp_path / "c.db")).latest("nope") is None


def test_checkpoint_messages_scrubbed(tmp_path, monkeypatch):
    key = "sk-or-secret-123456789"
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    conn = connect(tmp_path / "c.db")
    store = CheckpointStore(conn)
    messages = [{"role": "user", "content": f"use this key: {key}"}]
    state = {"api_key": key}
    store.save("r", 1, messages, state)

    # Raw database columns should not contain the key
    row = conn.execute("SELECT messages, state FROM checkpoints WHERE run_id=?", ("r",)).fetchone()
    raw_messages = row["messages"]
    raw_state = row["state"]
    assert key not in raw_messages
    assert key not in raw_state
    assert "***" in raw_messages
    assert "***" in raw_state

    # latest() should return scrubbed data
    cp = store.latest("r")
    assert key not in str(cp["messages"])
    assert key not in str(cp["state"])
    assert "***" in str(cp["messages"])
    assert "***" in str(cp["state"])
