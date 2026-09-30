from __future__ import annotations

import json

import httpx

from agentops import config
from agentops.tools import ToolError, ToolRegistry, obj

SERVICE = {"type": "string", "description": "Service name: gateway, api, worker or redis"}


def build_registry(executor, verify_fn) -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool("get_service_status", "List every container and its Docker state.", obj({}, []), "read")
    def get_service_status():
        return json.dumps(executor.status())

    @reg.tool("read_logs", "Read the last N log lines of a service (default 50, max 200).",
              obj({"service": SERVICE, "tail": {"type": "integer"}}, ["service"]), "read")
    def read_logs(service, tail=50):
        return executor.logs(service, tail)

    @reg.tool("get_metrics", "Read /metrics of the api service (request count, p95 latency, cpu probe).",
              obj({"service": SERVICE}, ["service"]), "read")
    def get_metrics(service):
        if service != "api":
            raise ToolError("only the api service exposes /metrics")
        return httpx.get(f"{config.service_url('api')}/metrics", timeout=3).text

    @reg.tool("run_diagnostic",
              "Run a read-only command inside a service container. Allowed: ps, top -bn1, df -h, ls, cat, env, tail. "
              "No pipes or shell syntax. Paths must be under /app, /data or /etc.",
              obj({"service": SERVICE, "command": {"type": "string"}}, ["service", "command"]), "read")
    def run_diagnostic(service, command):
        return executor.diagnostic(service, command)

    @reg.tool("restart_service", "Restart a container. Does not change its configuration or data.",
              obj({"service": SERVICE}, ["service"]), "mutate")
    def restart_service(service):
        executor.restart(service)
        return f"restarted {service}"

    @reg.tool("start_service", "Start a stopped or paused container.",
              obj({"service": SERVICE}, ["service"]), "mutate")
    def start_service(service):
        executor.start(service)
        return f"started {service}"

    @reg.tool("set_env",
              "Change an environment variable and recreate the service. Settable: api.REDIS_URL, api.DEBUG_SPIN, "
              "worker.REDIS_URL, gateway.API_URL. Value characters: letters, digits and : / . _ -",
              obj({"service": SERVICE, "key": {"type": "string"}, "value": {"type": "string"}},
                  ["service", "key", "value"]), "mutate")
    def set_env(service, key, value):
        return executor.set_env(service, key, value)

    @reg.tool("cleanup_files", "Delete one file under /data/ inside api or worker.",
              obj({"service": SERVICE, "path": {"type": "string"}}, ["service", "path"]), "mutate")
    def cleanup_files(service, path):
        return executor.cleanup_files(service, path)

    @reg.tool("kill_process", "Kill a process and all its children inside a service. PID 1 is protected.",
              obj({"service": SERVICE, "pid": {"type": "integer"}}, ["service", "pid"]), "mutate")
    def kill_process(service, pid):
        return executor.kill_process(service, pid)

    @reg.tool("verify_health", "Run the independent health check. Returns healthy true or false and the reason.",
              obj({}, []), "read")
    def verify_health():
        r = verify_fn()
        return json.dumps({"healthy": r.healthy, "reason": r.reason})

    @reg.tool("finish", "End the incident. Call only after verify_health reports healthy.",
              obj({"summary": {"type": "string", "description": "Root cause and fix in one paragraph"}},
                  ["summary"]), "control")
    def finish(summary):
        return summary

    return reg
