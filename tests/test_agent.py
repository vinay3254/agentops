import pytest

from agentops.agent import Agent
from agentops.checkpoint import CheckpointStore
from agentops.db import connect
from agentops.policy import PolicyError
from agentops.responder_tools import build_registry
from agentops.trace import TraceStore
from agentops.verifier import VerifyResult
from tests.fakes import FakeExecutor, ScriptedLLM, reply

ALLOWED = ["get_service_status", "read_logs", "run_diagnostic", "restart_service", "verify_health", "finish"]
HEALTHY = VerifyResult(True)
UNHEALTHY = VerifyResult(False, "api /health 503")


class Crash(Exception):
    pass


def make_agent(tmp_path, replies, verify=lambda: HEALTHY, executor=None, llm=None, **kw):
    conn = connect(tmp_path / "a.db")
    trace, cps = TraceStore(conn), CheckpointStore(conn)
    ex = executor or FakeExecutor()
    llm = llm or ScriptedLLM(replies)
    agent = Agent(llm, build_registry(ex, verify), ALLOWED, verify, trace, cps, "You are an SRE.", **kw)
    return agent, trace, cps, llm


def seq(results):
    it = iter(results)
    return lambda: next(it)


def test_success_path(tmp_path):
    agent, trace, _, _ = make_agent(tmp_path, [
        reply(calls=[("get_service_status", {})]),
        reply(calls=[("restart_service", {"service": "api"})]),
        reply(calls=[("finish", {"summary": "restarted api"})]),
    ])
    res = agent.run("inc1", "api is down", run_id="r1")
    assert res.success and res.stop_reason == "finished"
    assert res.steps == 3 and res.tokens == 360
    assert res.summary == "restarted api"
    types = [e["type"] for e in trace.events(run_id="r1")]
    assert types.count("llm_call") == 3 and "run_end" in types


def test_finish_while_unhealthy_does_not_resolve(tmp_path):
    agent, trace, _, _ = make_agent(
        tmp_path,
        [reply(calls=[("finish", {"summary": "done"})]), reply(calls=[("finish", {"summary": "really done"})])],
        verify=seq([UNHEALTHY, HEALTHY]),
    )
    res = agent.run("inc1", "x", run_id="r1")
    assert res.success and res.steps == 2 and res.summary == "really done"
    results = [e["payload"] for e in trace.events(run_id="r1") if e["type"] == "tool_result"]
    assert results[0]["ok"] is False and "Not resolved" in results[0]["result"]


def test_step_limit(tmp_path):
    agent, _, _, _ = make_agent(tmp_path, [reply(calls=[("get_service_status", {})])] * 5, max_steps=3)
    res = agent.run("i", "x")
    assert not res.success and res.stop_reason == "step_limit" and res.steps == 3


def test_malformed_json_arguments_count_as_failed_calls(tmp_path):
    bad = reply(calls=[("get_service_status", "{not json")])
    agent, trace, _, _ = make_agent(tmp_path, [bad, bad, bad], max_failed=3)
    res = agent.run("i", "x", run_id="r1")
    assert res.stop_reason == "too_many_failures" and not res.success
    msgs = [e["payload"]["result"] for e in trace.events(run_id="r1") if e["type"] == "tool_result"]
    assert all("invalid JSON" in m for m in msgs)


def test_failed_call_counter_resets_after_success(tmp_path):
    bad = reply(calls=[("nope", {})])
    good = reply(calls=[("get_service_status", {})])
    agent, _, _, _ = make_agent(
        tmp_path, [bad, bad, good, bad, bad, reply(calls=[("finish", {"summary": "s"})])], max_failed=3)
    assert agent.run("i", "x").success


def test_unknown_and_disallowed_tools(tmp_path):
    agent, trace, _, _ = make_agent(tmp_path, [
        reply(calls=[("set_env", {"service": "api", "key": "DEBUG_SPIN", "value": "0"})]),
        reply(calls=[("finish", {"summary": "s"})]),
    ])
    res = agent.run("i", "x", run_id="r1")
    first = [e for e in trace.events(run_id="r1") if e["type"] == "tool_result"][0]["payload"]
    assert res.success and first["ok"] is False and "not available" in first["result"]


def test_policy_denial_is_traced_and_loop_continues(tmp_path):
    ex = FakeExecutor()
    ex.raise_on["diagnostic"] = PolicyError("command not allowed: rm")
    agent, trace, _, _ = make_agent(tmp_path, [
        reply(calls=[("run_diagnostic", {"service": "api", "command": "rm -rf /"})]),
        reply(calls=[("finish", {"summary": "s"})]),
    ], executor=ex)
    res = agent.run("i", "x", run_id="r1")
    assert res.success
    denied = [e for e in trace.events(run_id="r1") if e["type"] == "policy_denied"]
    assert len(denied) == 1 and "command not allowed" in denied[0]["payload"]["reason"]


def test_large_tool_output_is_truncated_in_messages(tmp_path):
    agent, _, cps, _ = make_agent(tmp_path, [
        reply(calls=[("read_logs", {"service": "api", "tail": 200})]),
        reply(calls=[("finish", {"summary": "s"})]),
    ])
    agent.run("i", "x", run_id="r1")
    tool_msgs = [m for m in cps.latest("r1")["messages"] if m["role"] == "tool"]
    assert "truncated" in tool_msgs[0]["content"] and len(tool_msgs[0]["content"]) < 3200


def test_text_only_replies_are_nudged_then_fail(tmp_path):
    agent, _, _, _ = make_agent(tmp_path, [reply("thinking")] * 3, max_failed=3)
    res = agent.run("i", "x")
    assert res.stop_reason == "too_many_failures"


def test_token_budget(tmp_path):
    agent, _, _, _ = make_agent(
        tmp_path, [reply(calls=[("get_service_status", {})])] * 5, token_budget=200)
    res = agent.run("i", "x")
    assert res.stop_reason == "token_budget" and res.steps == 2


def test_timeout(tmp_path):
    agent, _, _, llm = make_agent(tmp_path, [reply(calls=[("get_service_status", {})])], timeout_s=0)
    res = agent.run("i", "x")
    assert res.stop_reason == "timeout" and res.steps == 0 and llm.calls == []


def test_llm_error(tmp_path):
    agent, trace, _, _ = make_agent(tmp_path, [])  # script exhausted raises RuntimeError
    res = agent.run("i", "x", run_id="r1")
    assert res.stop_reason == "llm_error"
    assert any(e["type"] == "llm_error" for e in trace.events(run_id="r1"))


def test_resume_after_crash(tmp_path):
    def hook(step):
        if step == 2:
            raise Crash()

    replies = [
        reply(calls=[("get_service_status", {})]),
        reply(calls=[("restart_service", {"service": "api"})]),
        reply(calls=[("finish", {"summary": "recovered"})]),
    ]
    agent, trace, cps, _ = make_agent(tmp_path, replies, step_hook=hook)
    with pytest.raises(Crash):
        agent.run("inc1", "api is down", run_id="r1")
    assert cps.latest("r1")["step"] == 2

    llm2 = ScriptedLLM(replies[2:])
    agent2, _, _, _ = make_agent(tmp_path, [], llm=llm2)
    res = agent2.resume("r1")
    assert res.success and res.steps == 3 and res.summary == "recovered"
    assert llm2.calls[0] == 6  # system, user, 2 assistant, 2 tool messages restored


def test_resume_unknown_run(tmp_path):
    agent, _, _, _ = make_agent(tmp_path, [])
    with pytest.raises(ValueError):
        agent.resume("missing")
