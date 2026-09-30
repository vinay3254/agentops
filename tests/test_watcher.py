from agentops.db import connect
from agentops.handlers import HandlerResult
from agentops.trace import TraceStore
from agentops.verifier import VerifyResult
from agentops.watcher import Watcher

H = VerifyResult(True)


def U(reason="api down"):
    return VerifyResult(False, reason)


def make(tmp_path, results, handler=None):
    it = iter(results)
    trace = TraceStore(connect(tmp_path / "w.db"))
    calls = []

    def default_handler(incident_id, symptom):
        calls.append((incident_id, symptom))
        return HandlerResult(steps=3, tokens=100, cost=0.01, stop_reason="finished")

    w = Watcher(handler or default_handler, trace, agent_name="agent", fault="crash",
                verify_fn=lambda: next(it), sleep=lambda s: None)
    return w, trace, calls


def test_opens_incident_after_two_failed_polls_and_resolves(tmp_path):
    w, trace, calls = make(tmp_path, [H, U(), U(), H, H])
    assert w.tick() is None      # healthy
    assert w.tick() is None      # first failure, no incident yet
    inc = w.tick()               # second failure opens the incident
    assert inc is not None and len(calls) == 1 and calls[0][1] == "api down"
    row = trace.incident(inc)
    assert row["status"] == "resolved" and row["steps"] == 3 and row["fault"] == "crash"
    assert inc.startswith("agent-crash-")


def test_single_blip_opens_nothing(tmp_path):
    w, trace, calls = make(tmp_path, [U(), H, U(), H])
    for _ in range(4):
        assert w.tick() is None
    assert calls == [] and trace.incidents() == []


def test_unresolved_when_not_healthy_after_handler(tmp_path):
    w, trace, _ = make(tmp_path, [U(), U(), H, U()])
    w.tick()
    inc = w.tick()
    assert trace.incident(inc)["status"] == "unresolved"


def test_handler_exception_is_recorded_as_stop_reason(tmp_path):
    def boom(incident_id, symptom):
        raise RuntimeError("kaput")

    w, trace, _ = make(tmp_path, [U(), U(), H, H], handler=boom)
    w.tick()
    inc = w.tick()
    row = trace.incident(inc)
    assert row["status"] == "resolved" and row["stop_reason"] == "error:RuntimeError"


def test_run_forever_stops_on_event(tmp_path):
    import threading
    w, _, _ = make(tmp_path, [H] * 50)
    stop = threading.Event()
    stop.set()
    w.run_forever(stop)  # returns immediately
