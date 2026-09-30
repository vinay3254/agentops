from agentops import baseline
from agentops.db import connect
from agentops.trace import TraceStore
from agentops.verifier import VerifyResult
from tests.fakes import FakeExecutor

H = VerifyResult(True)


def U(reason):
    return VerifyResult(False, reason)


def run(tmp_path, executor, results, **kw):
    it = iter(results)
    trace = TraceStore(connect(tmp_path / "b.db"))
    out = baseline.run(executor, trace, "inc1", verify_fn=lambda: next(it),
                       settle_s=0, sleep=lambda s: None, **kw)
    return out, trace


def test_starts_exited_container_and_succeeds(tmp_path):
    ex = FakeExecutor(states={"gateway": "running", "api": "exited", "worker": "running", "redis": "running"})
    out, trace = run(tmp_path, ex, [H])
    assert out.success and out.steps == 1 and out.stop_reason == "healthy"
    assert ex.actions == [("start", "api")]
    ev = trace.events(run_id="inc1")[0]
    assert ev["type"] == "tool_call" and ev["payload"]["name"] == "start_service"


def test_restarts_named_unhealthy_service(tmp_path):
    ex = FakeExecutor()
    out, _ = run(tmp_path, ex, [U("api /health 503: redis down"), H])
    assert out.success and ex.actions == [("restart", "api")]


def test_restarts_all_app_services_when_reason_names_none(tmp_path):
    ex = FakeExecutor()
    out, _ = run(tmp_path, ex, [U("synthetic order failed: 502"), H])
    assert out.success
    assert ex.actions == [("restart", "gateway"), ("restart", "api"), ("restart", "worker")]


def test_gives_up_after_max_attempts(tmp_path):
    ex = FakeExecutor()
    out, _ = run(tmp_path, ex, [U("api /health 503")] * 6, max_attempts=3)
    assert not out.success and out.stop_reason == "max_attempts"
    assert ex.actions == [("restart", "api")] * 3


def test_settle_polls_after_start_until_healthy(tmp_path):
    """After start, settle polls verify until healthy or timeout. No restart on transient failure."""
    ex = FakeExecutor(states={"gateway": "running", "api": "exited", "worker": "running", "redis": "running"})
    sleep_calls = []
    trace = TraceStore(connect(tmp_path / "b.db"))
    results = [U("api /health 503"), U("api /health 503"), H]
    it = iter(results)
    out = baseline.run(executor=ex, trace=trace, incident_id="inc1",
                       verify_fn=lambda: next(it),
                       settle_s=10, sleep=lambda s: sleep_calls.append(s), max_attempts=3)
    assert out.success and out.steps == 1 and out.stop_reason == "healthy"
    assert ex.actions == [("start", "api")]
    assert sleep_calls == [1.0, 1.0]


def test_verify_once_when_nothing_needs_start_and_healthy(tmp_path):
    """If no containers need starting and verify is healthy, return immediately without settling."""
    ex = FakeExecutor()
    verify_count = [0]
    def count_verify():
        verify_count[0] += 1
        return H
    trace = TraceStore(connect(tmp_path / "b.db"))
    out = baseline.run(executor=ex, trace=trace, incident_id="inc1",
                       verify_fn=count_verify,
                       settle_s=10, sleep=lambda s: None, max_attempts=3)
    assert out.success and out.steps == 0 and out.stop_reason == "healthy"
    assert ex.actions == []
    assert verify_count[0] == 1


def test_settle_polls_after_restart_until_healthy(tmp_path):
    """After restart, settle polls verify until healthy or timeout."""
    ex = FakeExecutor()
    sleep_calls = []
    trace = TraceStore(connect(tmp_path / "b.db"))
    results = [U("api /health 503"), U("api /health 503"), H]
    it = iter(results)
    out = baseline.run(executor=ex, trace=trace, incident_id="inc1",
                       verify_fn=lambda: next(it),
                       settle_s=10, sleep=lambda s: sleep_calls.append(s), max_attempts=3)
    assert out.success and out.steps == 1 and out.stop_reason == "healthy"
    assert ex.actions == [("restart", "api")]
    assert sleep_calls == [1.0]
