from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from agentops import config
from agentops.policy import PolicyError
from agentops.tools import ToolError, truncate


@dataclass
class RunResult:
    run_id: str
    success: bool
    stop_reason: str
    steps: int
    tokens: int
    cost: float
    summary: str


class Agent:
    def __init__(self, llm, registry, allowed_tools, verify_fn, trace, checkpoints, system_prompt,
                 max_steps=None, max_failed=None, timeout_s=None, token_budget=None, step_hook=None):
        self.llm = llm
        self.registry = registry
        self.allowed_tools = list(allowed_tools)
        self.verify_fn = verify_fn
        self.trace = trace
        self.checkpoints = checkpoints
        self.system_prompt = system_prompt
        self.max_steps = config.MAX_STEPS if max_steps is None else max_steps
        self.max_failed = config.MAX_FAILED_CALLS if max_failed is None else max_failed
        self.timeout_s = config.RUN_TIMEOUT_S if timeout_s is None else timeout_s
        self.token_budget = config.TOKEN_BUDGET if token_budget is None else token_budget
        self.step_hook = step_hook

    def run(self, incident_id: str, context: str, run_id: str | None = None) -> RunResult:
        run_id = run_id or uuid.uuid4().hex[:8]
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": context},
        ]
        state = {"incident_id": incident_id, "step": 0, "tokens": 0, "cost": 0.0, "failed": 0, "elapsed": 0.0}
        return self._loop(run_id, messages, state)

    def resume(self, run_id: str) -> RunResult:
        cp = self.checkpoints.latest(run_id)
        if cp is None:
            raise ValueError(f"no checkpoint for run {run_id}")
        return self._loop(run_id, cp["messages"], cp["state"])

    def _loop(self, run_id: str, messages: list, state: dict) -> RunResult:
        started = time.monotonic() - state["elapsed"]
        schemas = self.registry.schemas(self.allowed_tools)
        inc = state["incident_id"]
        while True:
            state["elapsed"] = time.monotonic() - started
            if state["step"] >= self.max_steps:
                return self._end(run_id, state, "step_limit")
            if state["elapsed"] >= self.timeout_s:
                return self._end(run_id, state, "timeout")
            if state["tokens"] >= self.token_budget:
                return self._end(run_id, state, "token_budget")
            try:
                resp = self.llm.complete(messages, schemas)
            except Exception as e:
                self.trace.event(run_id, "llm_error", {"error": f"{type(e).__name__}: {e}"},
                                 step=state["step"], incident_id=inc)
                return self._end(run_id, state, "llm_error")

            state["step"] += 1
            used = resp.prompt_tokens + resp.completion_tokens
            state["tokens"] += used
            state["cost"] += resp.cost
            self.trace.event(
                run_id, "llm_call",
                {"text": resp.text, "tool_calls": [{"name": c.name, "arguments": c.arguments} for c in resp.tool_calls]},
                step=state["step"], incident_id=inc, tokens=used, cost=resp.cost)
            messages.append(resp.raw_message)

            finished_summary = None
            if not resp.tool_calls:
                messages.append({"role": "user", "content":
                                 "No tool call received. Call a tool, or call finish once verify_health reports healthy."})
                state["failed"] += 1
            for call in resp.tool_calls:
                if finished_summary is not None:
                    messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": "skipped: run already finished"})
                    continue
                content, ok, summary = self._execute(run_id, state, call)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
                state["failed"] = 0 if ok else state["failed"] + 1
                if summary is not None:
                    finished_summary = summary

            self.checkpoints.save(run_id, state["step"], messages, dict(state))
            if self.step_hook:
                self.step_hook(state["step"])
            if finished_summary is not None:
                return self._end(run_id, state, "finished", success=True, summary=finished_summary)
            if state["failed"] >= self.max_failed:
                return self._end(run_id, state, "too_many_failures")

    def _execute(self, run_id: str, state: dict, call):
        inc, step = state["incident_id"], state["step"]

        def record(ok: bool, result: str):
            self.trace.event(run_id, "tool_result", {"name": call.name, "ok": ok, "result": result},
                             step=step, incident_id=inc)

        try:
            args = json.loads(call.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except ValueError as e:
            self.trace.event(run_id, "tool_call", {"name": call.name, "args": call.arguments},
                             step=step, incident_id=inc)
            msg = f"error: invalid JSON arguments: {e}"
            record(False, msg)
            return msg, False, None

        self.trace.event(
            run_id, "tool_call",
            {"name": call.name, "args": args, "permission": self.registry.permission(call.name)},
            step=step, incident_id=inc)

        if call.name == "finish" and "finish" in self.allowed_tools:
            verdict = self.verify_fn()
            self.trace.event(run_id, "verify", {"healthy": verdict.healthy, "reason": verdict.reason},
                             step=step, incident_id=inc)
            if not verdict.healthy:
                msg = f"Not resolved. Verifier reports: {verdict.reason}. Keep working."
                record(False, msg)
                return msg, False, None
            record(True, "Incident closed.")
            return "Incident closed.", True, str(args.get("summary", "")) or "(no summary)"

        try:
            result = truncate(self.registry.call(call.name, args, self.allowed_tools))
            ok = True
        except PolicyError as e:
            result, ok = f"denied: {e}", False
            self.trace.event(run_id, "policy_denied", {"name": call.name, "reason": str(e)},
                             step=step, incident_id=inc)
        except ToolError as e:
            result, ok = f"error: {e}", False
        except Exception as e:
            result, ok = f"error: {type(e).__name__}: {e}", False
        record(ok, result)
        return result, ok, None

    def _end(self, run_id, state, reason, success=False, summary="") -> RunResult:
        self.trace.event(
            run_id, "run_end",
            {"stop_reason": reason, "success": success, "steps": state["step"],
             "tokens": state["tokens"], "cost": state["cost"], "summary": summary},
            step=state["step"], incident_id=state["incident_id"])
        return RunResult(run_id, success, reason, state["step"], state["tokens"], state["cost"], summary)
