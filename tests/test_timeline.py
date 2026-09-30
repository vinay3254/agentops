from agentops.timeline import format_event


def ev(type, payload, step=1):
    return {"type": type, "payload": payload, "step": step, "ts": 0.0}


def test_llm_call_with_text_becomes_thought():
    out = format_event(ev("llm_call", {"text": "api looks down", "tool_calls": []}))
    assert out["kind"] == "thought" and "api looks down" in out["body"]


def test_llm_call_without_text_is_skipped():
    assert format_event(ev("llm_call", {"text": "", "tool_calls": [{"name": "x", "arguments": "{}"}]})) is None


def test_tool_call_and_result():
    call = format_event(ev("tool_call", {"name": "restart_service", "args": {"service": "api"}, "permission": "mutate"}))
    assert call["kind"] == "action" and "restart_service" in call["label"] and "api" in call["body"]
    res = format_event(ev("tool_result", {"name": "read_logs", "ok": False, "result": "denied: nope"}))
    assert res["kind"] == "result" and "denied: nope" in res["body"]


def test_other_kinds():
    assert format_event(ev("policy_denied", {"name": "run_diagnostic", "reason": "bad"}))["kind"] == "denied"
    assert format_event(ev("verify", {"healthy": True, "reason": ""}))["kind"] == "verify"
    end = format_event(ev("run_end", {"stop_reason": "finished", "success": True, "steps": 3, "summary": "fixed"}))
    assert end["kind"] == "end" and "fixed" in end["body"]
    assert format_event(ev("llm_error", {"error": "boom"}))["kind"] == "error"
    assert format_event(ev("mystery", {})) is None
