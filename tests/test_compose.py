import subprocess

import pytest

from agentops import compose as compose_mod
from agentops import config


def _fake_run(returncode: int, stderr: str = ""):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

    return run


def test_failure_raises_compose_error_with_stderr(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".agentops.env")
    monkeypatch.setattr(compose_mod.subprocess, "run", _fake_run(1, "boom: no such service"))
    with pytest.raises(compose_mod.ComposeError) as ei:
        compose_mod.compose("up", "-d")
    msg = str(ei.value)
    assert "boom: no such service" in msg
    assert "docker compose" in msg and "up -d" in msg
    assert "1" in msg


def test_stderr_is_truncated_to_tail(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".agentops.env")
    monkeypatch.setattr(compose_mod.subprocess, "run", _fake_run(2, "HEAD" + "x" * 5000 + "TAIL"))
    with pytest.raises(compose_mod.ComposeError) as ei:
        compose_mod.compose("ps")
    assert "TAIL" in str(ei.value)
    assert "HEAD" not in str(ei.value)


def test_check_false_returns_result(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".agentops.env")
    monkeypatch.setattr(compose_mod.subprocess, "run", _fake_run(1, "err"))
    res = compose_mod.compose("ps", check=False)
    assert res.returncode == 1


def test_success_returns_result_and_creates_env_file(monkeypatch, tmp_path):
    env = tmp_path / ".agentops.env"
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.setattr(compose_mod.subprocess, "run", _fake_run(0))
    assert not env.exists()
    res = compose_mod.compose("ps")
    assert res.returncode == 0
    assert env.exists()
