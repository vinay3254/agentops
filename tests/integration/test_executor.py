import re
import time

import pytest

from agentops import config
from agentops.compose import clear_env, compose
from agentops.executor import Executor
from agentops.policy import PolicyError
from agentops.verifier import wait_healthy

pytestmark = pytest.mark.docker


@pytest.fixture
def ex(stack):
    e = Executor()
    yield e
    clear_env()
    compose("up", "-d", "--no-deps", "api")
    assert wait_healthy(timeout=90).healthy


def test_status_lists_all_services(ex):
    status = ex.status()
    assert set(status) == set(config.SERVICES)
    assert all(v["state"] == "running" for v in status.values())


def test_logs_and_tail_clamp(ex):
    assert "Uvicorn running" in ex.logs("api", tail=500)
    with pytest.raises(PolicyError):
        ex.logs("nope")


def test_diagnostic_allowed_and_denied(ex):
    assert "uvicorn" in ex.diagnostic("api", "ps aux")
    assert "REDIS_URL=" in ex.diagnostic("api", "env")
    for bad in ("ps; rm -rf /", "cat /data/../etc/shadow", "curl http://x"):
        with pytest.raises(PolicyError):
            ex.diagnostic("api", bad)


def test_restart_and_start(ex):
    ex.restart("api")
    assert wait_healthy(timeout=90).healthy
    ex.client.containers.get(config.container_name("api")).stop(timeout=2)
    ex.start("api")
    assert wait_healthy(timeout=90).healthy


def test_set_env_recreates_service(ex):
    msg = ex.set_env("api", "DEBUG_SPIN", "0")
    assert "recreated" in msg
    assert wait_healthy(timeout=90).healthy
    with pytest.raises(PolicyError):
        ex.set_env("api", "REDIS_URL", "x; rm -rf /")
    with pytest.raises(PolicyError):
        ex.set_env("api", "PATH", "/bin")


def test_cleanup_files(ex):
    ex.exec("api", ["sh", "-c", "echo hi > /data/tmp.txt"])
    assert "tmp.txt" in ex.diagnostic("api", "ls /data")
    ex.cleanup_files("api", "/data/tmp.txt")
    assert "tmp.txt" not in ex.diagnostic("api", "ls /data")
    for bad in ("/data/../etc/passwd", "/etc/passwd", "/data"):
        with pytest.raises(PolicyError):
            ex.cleanup_files("api", bad)
    with pytest.raises(PolicyError):
        ex.cleanup_files("redis", "/data/x")


def test_kill_process_tree_and_pid1_denied(ex):
    api = ex.client.containers.get(config.container_name("api"))
    api.exec_run(["sleep", "300"], detach=True)
    time.sleep(0.5)
    ps = ex.diagnostic("api", "ps -eo pid,ppid,cmd")
    pid = int(re.search(r"^\s*(\d+)\s+\d+\s+sleep 300", ps, re.M).group(1))
    assert "killed" in ex.kill_process("api", pid)
    assert "sleep 300" not in ex.diagnostic("api", "ps -eo pid,ppid,cmd")
    with pytest.raises(PolicyError):
        ex.kill_process("api", 1)
    with pytest.raises(PolicyError):
        ex.kill_process("api", 999999)
