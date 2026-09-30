from __future__ import annotations

import time

from agentops import config
from agentops.handlers import HandlerResult
from agentops.verifier import verify


class Watcher:
    def __init__(self, handler, trace, agent_name="agent", fault=None, verify_fn=verify, sleep=time.sleep):
        self.handler = handler
        self.trace = trace
        self.agent_name = agent_name
        self.fault = fault
        self.verify_fn = verify_fn
        self.sleep = sleep
        self._fail = 0

    def tick(self) -> str | None:
        result = self.verify_fn()
        if result.healthy:
            self._fail = 0
            return None
        self._fail += 1
        if self._fail < config.POLLS_TO_OPEN:
            return None
        self._fail = 0
        return self.handle(result.reason)

    def handle(self, symptom: str) -> str:
        incident_id = f"{self.agent_name}-{self.fault or 'live'}-{int(time.time() * 1000)}"
        self.trace.open_incident(incident_id, symptom, self.agent_name, self.fault)
        try:
            out = self.handler(incident_id, symptom)
        except Exception as e:
            out = HandlerResult(0, 0, 0.0, f"error:{type(e).__name__}")
        resolved = self._confirm_healthy()
        self.trace.close_incident(incident_id, "resolved" if resolved else "unresolved",
                                  out.stop_reason, out.steps, out.tokens, out.cost)
        return incident_id

    def _confirm_healthy(self) -> bool:
        for i in range(config.POLLS_TO_CLOSE):
            if not self.verify_fn().healthy:
                return False
            if i < config.POLLS_TO_CLOSE - 1:
                self.sleep(1.0)
        return True

    def run_forever(self, stop_event=None) -> None:
        while not (stop_event and stop_event.is_set()):
            self.tick()
            self.sleep(config.POLL_INTERVAL_S)
