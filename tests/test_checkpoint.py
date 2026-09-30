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
