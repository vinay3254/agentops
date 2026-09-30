import subprocess
from types import SimpleNamespace

import docker.errors
import pytest

from agentops import config
from agentops.compose import ComposeError
from agentops.executor import Executor, ExecutorError
from agentops.policy import PolicyError


class FakeContainer:
    def __init__(self, status="running", exec_results=None, raise_on=None):
        self.status = status
        self.exec_results = list(exec_results or [])
        self.exec_calls = []
        self.raise_on = raise_on

    def _maybe_raise(self, name):
        if self.raise_on == name:
            raise docker.errors.APIError("boom /var/run/docker.sock")

    def restart(self, timeout=None):
        self._maybe_raise("restart")

    def start(self):
        self._maybe_raise("start")

    def unpause(self):
        self._maybe_raise("unpause")

    def exec_run(self, argv):
        self.exec_calls.append(list(argv))
        code, out = self.exec_results.pop(0)
        return SimpleNamespace(exit_code=code, output=out)


class FakeClient:
    def __init__(self, container):
        self.containers = SimpleNamespace(get=lambda name: container)


def make(container):
    return Executor(client=FakeClient(container))


# ---- set_env rollback ----

@pytest.fixture
def envfile(tmp_path, monkeypatch):
    f = tmp_path / ".agentops.env"
    monkeypatch.setattr(config, "ENV_FILE", f)
    return f


def _failing(exc):
    def fake_compose(*args, **kwargs):
        raise exc
    return fake_compose


@pytest.mark.parametrize("exc", [
    ComposeError("docker compose -f /host/path/docker-compose.yml up failed"),
    subprocess.TimeoutExpired(cmd=["docker", "compose", "up"], timeout=300),
])
def test_set_env_restores_file_on_compose_failure(envfile, monkeypatch, exc):
    envfile.write_text("OTHER=1\n")
    monkeypatch.setattr("agentops.executor.compose", _failing(exc))
    with pytest.raises(ExecutorError) as ei:
        make(FakeContainer()).set_env("api", "DEBUG_SPIN", "0")
    assert envfile.read_text() == "OTHER=1\n"
    msg = str(ei.value)
    assert "docker compose" not in msg
    assert str(envfile) not in msg
    assert "configuration restored" in msg
    assert ei.value.__cause__ is exc


def test_set_env_restores_missing_file_as_empty(envfile, monkeypatch):
    assert not envfile.exists()
    monkeypatch.setattr("agentops.executor.compose", _failing(ComposeError("x")))
    with pytest.raises(ExecutorError):
        make(FakeContainer()).set_env("api", "DEBUG_SPIN", "0")
    assert envfile.read_text() == ""


def test_set_env_success_keeps_new_value(envfile, monkeypatch):
    envfile.write_text("OTHER=1\n")
    calls = []
    monkeypatch.setattr("agentops.executor.compose", lambda *a, **k: calls.append(a))
    msg = make(FakeContainer()).set_env("api", "DEBUG_SPIN", "0")
    assert "recreated" in msg
    assert "DEBUG_SPIN=0" in envfile.read_text()
    assert "OTHER=1" in envfile.read_text()
    assert calls


# ---- restart / start docker errors ----

def test_restart_api_error_becomes_executor_error():
    with pytest.raises(ExecutorError) as ei:
        make(FakeContainer(raise_on="restart")).restart("api")
    assert "restarting api" in str(ei.value)
    assert "docker.sock" not in str(ei.value)
    assert isinstance(ei.value.__cause__, docker.errors.APIError)


@pytest.mark.parametrize("status,raise_on", [("exited", "start"), ("paused", "unpause")])
def test_start_api_error_becomes_executor_error(status, raise_on):
    with pytest.raises(ExecutorError) as ei:
        make(FakeContainer(status=status, raise_on=raise_on)).start("api")
    assert "starting api" in str(ei.value)
    assert "docker.sock" not in str(ei.value)
    assert isinstance(ei.value.__cause__, docker.errors.APIError)


# ---- kill_process verification ----

PS_BEFORE = b"    1     0\n   20     1\n   30    20\n   40     1\n"
PS_AFTER_GONE = b"    1     0\n   40     1\n"
PS_AFTER_ALIVE = b"    1     0\n   20     1\n   40     1\n"


def test_kill_success_when_tree_gone():
    c = FakeContainer(exec_results=[(0, PS_BEFORE), (0, b""), (0, PS_AFTER_GONE)])
    msg = make(c).kill_process("api", 20)
    assert msg.startswith("killed 2 process(es)")
    assert c.exec_calls[1][:2] == ["kill", "-9"]
    assert set(c.exec_calls[1][2:]) == {"20", "30"}


def test_kill_fails_when_process_survives():
    c = FakeContainer(exec_results=[(0, PS_BEFORE), (1, b"denied"), (0, PS_AFTER_ALIVE)])
    with pytest.raises(ExecutorError) as ei:
        make(c).kill_process("api", 20)
    assert "pids still running after kill: [20]" in str(ei.value)


def test_kill_unknown_pid_policy_error():
    c = FakeContainer(exec_results=[(0, PS_AFTER_GONE)])
    with pytest.raises(PolicyError):
        make(c).kill_process("api", 20)


def test_kill_pid1_policy_error():
    c = FakeContainer(exec_results=[])
    with pytest.raises(PolicyError):
        make(c).kill_process("api", 1)
