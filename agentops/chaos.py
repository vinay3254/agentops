from __future__ import annotations

import sys
import time

import docker

from agentops import config
from agentops.compose import clear_env, compose, set_env_var
from agentops.executor import Executor, ExecutorError
from agentops.verifier import wait_healthy

FAULTS = ("crash", "bad_config", "dependency_down", "disk_full", "cpu_hog")


class ChaosError(Exception):
    pass


def inject(fault: str, executor: Executor | None = None) -> None:
    if fault not in FAULTS:
        raise ValueError(f"unknown fault {fault!r}. Valid: {', '.join(FAULTS)}")
    ex = executor or Executor()
    if fault == "crash":
        ex.client.containers.get(config.container_name("api")).kill()
    elif fault == "bad_config":
        set_env_var("API_REDIS_URL", "redis://redis-wrong:6379/0")
        compose("up", "-d", "--force-recreate", "--no-deps", "api")
    elif fault == "dependency_down":
        ex.client.containers.get(config.container_name("redis")).stop(timeout=2)
    elif fault == "disk_full":
        ex.exec("api", ["dd", "if=/dev/zero", "of=/data/junk.bin", "bs=1M", "count=10"])
    elif fault == "cpu_hog":
        set_env_var("API_DEBUG_SPIN", "8")
        compose("up", "-d", "--force-recreate", "--no-deps", "api")


def _wipe_data(ex: Executor, service: str, tries: int = 15) -> None:
    for _ in range(tries):
        try:
            ex.exec(service, ["sh", "-c", "rm -rf /data/* /data/.[!.]*"])
            return
        except (ExecutorError, docker.errors.APIError):
            time.sleep(1)
    raise ChaosError(f"could not wipe /data in {service}")


def reset(timeout: float = 90.0) -> None:
    clear_env()
    compose("up", "-d", "--force-recreate")
    ex = Executor()
    for svc in ("api", "worker"):
        _wipe_data(ex, svc)
    result = wait_healthy(timeout=timeout)
    if not result.healthy:
        raise ChaosError(f"reset failed: {result.reason}")


def main(argv: list[str]) -> int:
    if argv[:1] == ["reset"]:
        reset()
        print("stack reset and healthy")
        return 0
    if len(argv) == 2 and argv[0] == "inject":
        inject(argv[1])
        print(f"injected {argv[1]}")
        return 0
    print("usage: python -m agentops.chaos reset | inject <fault>")
    print("faults:", ", ".join(FAULTS))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
