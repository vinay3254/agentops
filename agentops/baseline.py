from __future__ import annotations

import time
from dataclasses import dataclass

from agentops import config
from agentops.verifier import verify


@dataclass
class BaselineResult:
    success: bool
    steps: int
    stop_reason: str


def _settle(verify_fn, settle_s, sleep):
    """Poll verify_fn until healthy or settle_s seconds elapsed. Always verifies at least once."""
    deadline = time.monotonic() + settle_s
    while True:
        result = verify_fn()
        if result.healthy or time.monotonic() >= deadline:
            return result
        sleep(1.0)


def run(executor, trace, incident_id, verify_fn=verify, max_attempts=3, settle_s=12.0,
        sleep=time.sleep) -> BaselineResult:
    """Run rule-based recovery actions until system is healthy or max_attempts exceeded.

    Args:
        executor: Service executor with status(), start(), restart() methods
        trace: TraceStore for recording actions
        incident_id: Incident ID for trace events
        verify_fn: Function that returns VerifyResult; defaults to verify()
        max_attempts: Maximum number of recovery attempts
        settle_s: Maximum seconds to poll after start/restart (polling window, not fixed sleep)
        sleep: Sleep function (default time.sleep)

    Returns:
        BaselineResult with success status, step count, and stop reason
    """
    steps = 0

    def act(name: str, service: str) -> None:
        nonlocal steps
        steps += 1
        trace.event(incident_id, "tool_call", {"name": name, "args": {"service": service}},
                    step=steps, incident_id=incident_id)
        if name == "start_service":
            executor.start(service)
        else:
            executor.restart(service)

    for _ in range(max_attempts):
        started = False
        for svc, info in executor.status().items():
            if info["state"] != "running":
                act("start_service", svc)
                started = True
        result = _settle(verify_fn, settle_s, sleep) if started else verify_fn()
        if result.healthy:
            return BaselineResult(True, steps, "healthy")

        first = result.reason.split(" ", 1)[0]
        targets = [first] if first in config.SERVICES else list(config.APP_SERVICES)
        for svc in targets:
            act("restart_service", svc)
        result = _settle(verify_fn, settle_s, sleep)
        if result.healthy:
            return BaselineResult(True, steps, "healthy")
    return BaselineResult(False, steps, "max_attempts")
