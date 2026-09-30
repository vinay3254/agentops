from __future__ import annotations

import time
from dataclasses import dataclass

from agentops import chaos
from agentops.verifier import verify
from agentops.watcher import Watcher


@dataclass
class TrialResult:
    fault: str
    mode: str
    trial: int
    success: bool
    mttr_s: float
    steps: int
    tokens: int
    cost: float
    stop_reason: str
    incident_id: str


def wait_unhealthy(timeout: float = 40.0, verify_fn=verify, sleep=time.sleep) -> str:
    deadline = time.monotonic() + timeout
    while True:
        result = verify_fn()
        if not result.healthy:
            return result.reason
        if time.monotonic() >= deadline:
            raise chaos.ChaosError("fault had no effect: stack still healthy after injection")
        sleep(1.0)


def run_trial(fault, mode, trial_no, handler, trace, reset_fn=chaos.reset, inject_fn=chaos.inject,
              verify_fn=verify, sleep=time.sleep) -> TrialResult:
    reset_fn()
    inject_fn(fault)
    symptom = wait_unhealthy(verify_fn=verify_fn, sleep=sleep)
    watcher = Watcher(handler, trace, agent_name=mode, fault=fault, verify_fn=verify_fn, sleep=sleep)
    incident_id = watcher.handle(symptom)
    row = trace.incident(incident_id)
    return TrialResult(
        fault=fault, mode=mode, trial=trial_no, success=row["status"] == "resolved",
        mttr_s=row["closed"] - row["opened"], steps=row["steps"], tokens=row["tokens"],
        cost=row["cost"], stop_reason=row["stop_reason"], incident_id=incident_id)
