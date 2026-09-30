from types import SimpleNamespace

from agentops.checkpoint import CheckpointStore
from agentops.db import connect
from agentops.run_one import _resume
from agentops.trace import TraceStore
from agentops.verifier import VerifyResult


class FakeAgent:
    def __init__(self):
        self.resumed = []

    def resume(self, run_id):
        self.resumed.append(run_id)
        return SimpleNamespace(success=True, stop_reason="finished", steps=4, tokens=100, cost=0.5,
                               summary="fixed it")


def stores(tmp_path):
    conn = connect(tmp_path / "t.db")
    return TraceStore(conn), CheckpointStore(conn)


def healthy(ok):
    return lambda: VerifyResult(ok, "" if ok else "still down")


def test_resume_closes_open_incident(tmp_path, capsys):
    trace, cps = stores(tmp_path)
    trace.open_incident("inc1", "api down", "agent", "crash")
    agent = FakeAgent()
    _resume("inc1", trace, cps, agent, healthy(True))
    row = trace.incident("inc1")
    assert agent.resumed == ["inc1"]
    assert row["status"] == "resolved" and row["stop_reason"] == "finished"
    assert row["steps"] == 4 and row["tokens"] == 100 and row["cost"] == 0.5 and row["closed"] is not None
    assert "fixed it" in capsys.readouterr().out


def test_resume_marks_unresolved_when_unhealthy(tmp_path):
    trace, cps = stores(tmp_path)
    trace.open_incident("inc1", "api down", "agent", "crash")
    _resume("inc1", trace, cps, FakeAgent(), healthy(False))
    assert trace.incident("inc1")["status"] == "unresolved"


def test_resume_leaves_closed_incident_untouched(tmp_path):
    trace, cps = stores(tmp_path)
    trace.open_incident("inc1", "api down", "agent", "crash")
    trace.close_incident("inc1", "unresolved", "max_steps", 15, 999, 1.0)
    before = trace.incident("inc1")
    _resume("inc1", trace, cps, FakeAgent(), healthy(True))
    assert trace.incident("inc1") == before


def test_resume_without_checkpoint_or_incident_row_does_not_raise(tmp_path, capsys):
    trace, cps = stores(tmp_path)
    _resume("ghost", trace, cps, FakeAgent(), healthy(True))
    assert trace.incident("ghost") is None
    assert "resumed ghost" in capsys.readouterr().out
