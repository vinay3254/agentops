import pytest

from agentops.checkpoint import CheckpointStore
from agentops.db import connect
from agentops.handlers import HandlerResult, make_handler
from agentops.responder import ALLOWED_TOOLS, SYSTEM_PROMPT, incident_context
from agentops.responder_tools import build_registry
from agentops.trace import TraceStore
from agentops.verifier import VerifyResult
from tests.fakes import FakeExecutor, ScriptedLLM, reply


def stores(tmp_path):
    conn = connect(tmp_path / "h.db")
    return TraceStore(conn), CheckpointStore(conn)


def test_allowed_tools_are_all_registered():
    reg = build_registry(FakeExecutor(), lambda: VerifyResult(True))
    assert len(reg.schemas(ALLOWED_TOOLS)) == len(ALLOWED_TOOLS)
    assert "finish" in ALLOWED_TOOLS


def test_prompt_and_context():
    assert "verify_health" in SYSTEM_PROMPT and "finish" in SYSTEM_PROMPT
    assert "api /health 503" in incident_context("inc1", "api /health 503")


def test_agent_handler(tmp_path):
    trace, cps = stores(tmp_path)
    llm = ScriptedLLM([
        reply(calls=[("restart_service", {"service": "api"})]),
        reply(calls=[("finish", {"summary": "restarted"})]),
    ])
    handler = make_handler("agent", llm=llm, executor=FakeExecutor(), trace=trace, checkpoints=cps,
                           verify_fn=lambda: VerifyResult(True))
    out = handler("inc1", "api down")
    assert out == HandlerResult(steps=2, tokens=240, cost=0.002, stop_reason="finished")


def test_baseline_handler(tmp_path):
    trace, cps = stores(tmp_path)
    ex = FakeExecutor(states={"gateway": "running", "api": "exited", "worker": "running", "redis": "running"})
    handler = make_handler("baseline", executor=ex, trace=trace, checkpoints=cps,
                           verify_fn=lambda: VerifyResult(True))
    out = handler("inc1", "api down")
    assert out.steps == 1 and out.tokens == 0 and out.stop_reason == "healthy"


def test_unknown_mode():
    with pytest.raises(ValueError):
        make_handler("magic")
