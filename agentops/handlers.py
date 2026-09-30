from __future__ import annotations

from dataclasses import dataclass

from agentops import baseline
from agentops.responder import build_responder, incident_context
from agentops.verifier import verify


@dataclass
class HandlerResult:
    steps: int
    tokens: int
    cost: float
    stop_reason: str


def make_handler(mode: str, *, llm=None, executor=None, trace=None, checkpoints=None, verify_fn=verify):
    if mode not in ("agent", "baseline"):
        raise ValueError(f"unknown mode {mode!r}; use 'agent' or 'baseline'")

    def handler(incident_id: str, symptom: str) -> HandlerResult:
        ex = executor
        if ex is None:
            from agentops.executor import Executor
            ex = Executor()
        if mode == "agent":
            agent = build_responder(llm, ex, trace, checkpoints, verify_fn=verify_fn)
            res = agent.run(incident_id, incident_context(incident_id, symptom), run_id=incident_id)
            return HandlerResult(res.steps, res.tokens, res.cost, res.stop_reason)
        out = baseline.run(ex, trace, incident_id, verify_fn=verify_fn)
        return HandlerResult(out.steps, 0, 0.0, out.stop_reason)

    return handler
