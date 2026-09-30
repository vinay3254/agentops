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


def _raising_verify(raise_on, then=H):
    """verify_fn that raises RuntimeError on the given 1-based call numbers, else returns scripted results."""
    state = {"n": 0}

    def fn():
        state["n"] += 1
        if state["n"] in raise_on:
            raise RuntimeError("verifier broke")
        return then if not callable(then) else then(state["n"])
    return fn


def _watcher(tmp_path, verify_fn, handler=None):
    trace = TraceStore(connect(tmp_path / "v.db"))
    calls = []

    def default_handler(incident_id, symptom):
        calls.append((incident_id, symptom))
        return HandlerResult(steps=3, tokens=100, cost=0.01, stop_reason="finished")

    w = Watcher(handler or default_handler, trace, agent_name="agent", fault="crash",
                verify_fn=verify_fn, sleep=lambda s: None)
    return w, trace, calls


def test_verifier_exception_during_confirmation_leaves_incident_unresolved(tmp_path):
    # calls: 1=U, 2=U (opens), 3=raises during confirmation
    def seq(n):
        return U() if n <= 2 else H
    w, trace, _ = _watcher(tmp_path, _raising_verify({3}, then=seq))
    w.tick()
    inc = w.tick()
    row = trace.incident(inc)
    assert row["status"] == "unresolved" and row["status"] != "open"


def test_verifier_exception_in_tick_counts_as_failed_poll(tmp_path):
    w, trace, calls = _watcher(tmp_path, _raising_verify({1, 2}))
    assert w.tick() is None
    inc = w.tick()
    assert inc is not None and len(calls) == 1
    assert calls[0][1].startswith("verifier error")
    assert trace.incident(inc)["status"] == "resolved"


def test_keyboard_interrupt_closes_incident_interrupted(tmp_path):
    import pytest

    def interrupt(incident_id, symptom):
        raise KeyboardInterrupt

    w, trace, _ = _watcher(tmp_path, lambda: U(), handler=interrupt)
    w.tick()
    with pytest.raises(KeyboardInterrupt):
        w.tick()
    row = trace.incidents()[0]
    assert row["status"] == "unresolved" and row["stop_reason"] == "interrupted"
    assert row["steps"] == 0 and row["tokens"] == 0 and row["cost"] == 0
