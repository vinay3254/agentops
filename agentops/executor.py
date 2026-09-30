from __future__ import annotations

import docker
from docker.errors import NotFound

from agentops import config
from agentops.compose import compose, set_env_var
from agentops.policy import (
    PolicyError,
    check_data_path,
    check_env,
    check_pid,
    check_service,
    parse_diagnostic,
    redact_env,
)


class ExecutorError(Exception):
    """A Docker operation failed or is not possible in the current state."""


class Executor:
    def __init__(self, client=None):
        self.client = client or docker.from_env()

    def _container(self, service: str):
        check_service(service)
        try:
            return self.client.containers.get(config.container_name(service))
        except NotFound:
            raise ExecutorError(f"container for {service} does not exist")

    def status(self) -> dict[str, dict]:
        out = {}
        for svc in config.SERVICES:
            try:
                c = self.client.containers.get(config.container_name(svc))
                out[svc] = {"state": c.status, "exit_code": c.attrs["State"].get("ExitCode")}
            except NotFound:
                out[svc] = {"state": "missing", "exit_code": None}
        return out

    def logs(self, service: str, tail: int = 50) -> str:
        n = max(1, min(int(tail), 200))
        return self._container(service).logs(tail=n).decode("utf-8", "replace")

    def exec(self, service: str, argv: list[str]) -> tuple[int, str]:
        c = self._container(service)
        if c.status != "running":
            raise ExecutorError(f"{service} is {c.status}; cannot run commands in it")
        res = c.exec_run(argv)
        return res.exit_code, res.output.decode("utf-8", "replace")

    def diagnostic(self, service: str, command: str) -> str:
        argv = parse_diagnostic(command)
        code, out = self.exec(service, argv)
        if argv[0] == "env":
            out = redact_env(out)
        return out if code == 0 else f"[exit {code}]\n{out}"

    def restart(self, service: str) -> None:
        self._container(service).restart(timeout=5)

    def start(self, service: str) -> None:
        c = self._container(service)
        if c.status == "paused":
            c.unpause()
        elif c.status != "running":
            c.start()

    def set_env(self, service: str, key: str, value: str) -> str:
        check_service(service)
        var = check_env(service, key, value)
        set_env_var(var, value)
        compose("up", "-d", "--force-recreate", "--no-deps", service)
        return f"set {key}={value} for {service} and recreated the container"

    def cleanup_files(self, service: str, path: str) -> str:
        if service not in ("api", "worker"):
            raise PolicyError("cleanup_files only applies to api and worker")
        p = check_data_path(path)
        code, out = self.exec(service, ["rm", "-f", "--", p])
        if code != 0:
            raise ExecutorError(out.strip() or f"rm exited {code}")
        return f"removed {p}"

    def kill_process(self, service: str, pid) -> str:
        pid = check_pid(pid)
        code, out = self.exec(service, ["ps", "-eo", "pid=,ppid="])
        if code != 0:
            raise ExecutorError(out.strip())
        children: dict[int, list[int]] = {}
        pids: set[int] = set()
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and all(p.isdigit() for p in parts):
                p, pp = int(parts[0]), int(parts[1])
                pids.add(p)
                children.setdefault(pp, []).append(p)
        if pid not in pids:
            raise PolicyError(f"pid {pid} is not running in {service}")
        tree, stack = [], [pid]
        while stack:
            cur = stack.pop()
            tree.append(cur)
            stack.extend(children.get(cur, []))
        if 1 in tree:
            raise PolicyError("refusing to kill PID 1")
        self.exec(service, ["kill", "-9", *map(str, tree)])
        return f"killed {len(tree)} process(es): {sorted(tree)}"
