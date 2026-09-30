import pytest

from agentops import chaos
from agentops.db import connect
from agentops.handlers import HandlerResult
from agentops.trace import TraceStore
from agentops.trial import run_trial, wait_unhealthy
from agentops.verifier import VerifyResult

H = VerifyResult(True)


def U(reason="api /health 503"):
    return VerifyResult(False, reason)


def test_wait_unhealthy_returns_reason():
    it = iter([H, H, U("api unreachable")])
    assert wait_unhealthy(timeout=5, verify_fn=lambda: next(it), sleep=lambda s: None) == "api unreachable"


def test_wait_unhealthy_raises_when_fault_has_no_effect():
    with pytest.raises(chaos.ChaosError, match="no effect"):
        wait_unhealthy(timeout=0.2, verify_fn=lambda: H, sleep=lambda s: __import__("time").sleep(0.05))


def test_run_trial_records_result(tmp_path):
    trace = TraceStore(connect(tmp_path / "t.db"))
    calls = []
    results = iter([U(), H, H])  # wait_unhealthy, then two confirm polls

    def handler(incident_id, symptom):
        calls.append(symptom)
        return HandlerResult(steps=2, tokens=300, cost=0.01, stop_reason="finished")

    res = run_trial(
        "crash", "agent", 1, handler, trace,
        reset_fn=lambda: calls.append("reset"), inject_fn=lambda f: calls.append(f"inject:{f}"),
        verify_fn=lambda: next(results), sleep=lambda s: None)
    assert calls == ["reset", "inject:crash", "api /health 503"]
    assert res.success and res.steps == 2 and res.tokens == 300 and res.stop_reason == "finished"
    assert res.mttr_s >= 0 and res.fault == "crash" and res.mode == "agent" and res.trial == 1
    assert trace.incident(res.incident_id)["fault"] == "crash"
