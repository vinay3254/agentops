from __future__ import annotations

import argparse
import os

from agentops import chaos, config
from agentops.checkpoint import CheckpointStore
from agentops.db import connect
from agentops.executor import Executor
from agentops.handlers import make_handler
from agentops.responder import build_responder
from agentops.trace import TraceStore
from agentops.trial import run_trial
from agentops.verifier import verify, wait_healthy


def _resume(run_id: str, trace, cps) -> None:
    from agentops.llm import OpenRouterClient
    agent = build_responder(OpenRouterClient(), Executor(), trace, cps)
    res = agent.resume(run_id)
    healthy = wait_healthy(timeout=30).healthy
    incident_id = cps.latest(run_id)["state"]["incident_id"]
    trace.close_incident(incident_id, "resolved" if healthy else "unresolved",
                         res.stop_reason, res.steps, res.tokens, res.cost)
    print(f"resumed {run_id}: success={res.success} steps={res.steps} healthy={healthy}")
    print(res.summary)


def main() -> None:
    p = argparse.ArgumentParser(description="Run one incident end to end")
    p.add_argument("--fault", choices=chaos.FAULTS)
    p.add_argument("--mode", choices=["agent", "baseline"], default="agent")
    p.add_argument("--crash-after-step", type=int, help="kill this process after step N (resume demo)")
    p.add_argument("--resume", metavar="RUN_ID")
    args = p.parse_args()

    conn = connect(config.DB_PATH)
    trace, cps = TraceStore(conn), CheckpointStore(conn)

    if args.resume:
        _resume(args.resume, trace, cps)
        return
    if not args.fault:
        p.error("--fault is required unless --resume is given")

    llm = None
    if args.mode == "agent":
        from agentops.llm import OpenRouterClient
        llm = OpenRouterClient()

    step_hook = None
    if args.crash_after_step:
        def step_hook(step):
            if step >= args.crash_after_step:
                print(f"simulated crash after step {step}; resume with --resume <incident id shown in dashboard>")
                os._exit(1)

    handler = make_handler(args.mode, llm=llm, trace=trace, checkpoints=cps)
    if step_hook and args.mode == "agent":
        inner = handler

        def handler(incident_id, symptom, _inner=inner):
            from agentops.responder import incident_context
            agent = build_responder(llm, Executor(), trace, cps, step_hook=step_hook)
            res = agent.run(incident_id, incident_context(incident_id, symptom), run_id=incident_id)
            from agentops.handlers import HandlerResult
            return HandlerResult(res.steps, res.tokens, res.cost, res.stop_reason)

    result = run_trial(args.fault, args.mode, 1, handler, trace)
    print(result)
    print("final health:", verify())


if __name__ == "__main__":
    main()
