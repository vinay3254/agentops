from __future__ import annotations

from agentops.agent import Agent
from agentops.responder_tools import build_registry
from agentops.verifier import verify

SYSTEM_PROMPT = """You are an on-call SRE agent operating a Docker Compose stack.
Topology: gateway (port 8000) calls api (port 8001), api uses redis, worker (port 8002) consumes the redis queue.
Every app service exposes /health. A separate verifier decides whether the stack is healthy.

Method:
1. Call get_service_status and verify_health to see what is broken.
2. Use read_logs and run_diagnostic on the suspect service to find the root cause.
3. Apply the smallest fix with a mutating tool.
4. Call verify_health.
5. Call finish with one paragraph: root cause and fix.

Rules:
- Do not restart services blindly. A restart does not fix configuration, disk or process faults.
- Only call finish after verify_health reports healthy. The verifier has the final word.
- You have at most 15 steps. Prefer few, well-chosen actions.
- run_diagnostic takes one plain command. No pipes or shell syntax."""

ALLOWED_TOOLS = [
    "get_service_status", "read_logs", "get_metrics", "run_diagnostic", "restart_service",
    "start_service", "set_env", "cleanup_files", "kill_process", "verify_health", "finish",
]


def incident_context(incident_id: str, symptom: str) -> str:
    return (f"Incident {incident_id}. Monitoring symptom: {symptom}. "
            "Diagnose the root cause, fix it, confirm with verify_health, then call finish.")


def build_responder(llm, executor, trace, checkpoints, verify_fn=verify, **agent_kwargs) -> Agent:
    registry = build_registry(executor, verify_fn)
    return Agent(llm, registry, ALLOWED_TOOLS, verify_fn, trace, checkpoints, SYSTEM_PROMPT, **agent_kwargs)
