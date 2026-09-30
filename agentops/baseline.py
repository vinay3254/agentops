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


def run(executor, trace, incident_id, verify_fn=verify, max_attempts=3, settle_s=4.0,
        sleep=time.sleep) -> BaselineResult:
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
        for svc, info in executor.status().items():
            if info["state"] != "running":
                act("start_service", svc)
        sleep(settle_s)
        result = verify_fn()
        if result.healthy:
            return BaselineResult(True, steps, "healthy")

        first = result.reason.split(" ", 1)[0]
        targets = [first] if first in config.SERVICES else list(config.APP_SERVICES)
        for svc in targets:
            act("restart_service", svc)
        sleep(settle_s)
        result = verify_fn()
        if result.healthy:
            return BaselineResult(True, steps, "healthy")
    return BaselineResult(False, steps, "max_attempts")
