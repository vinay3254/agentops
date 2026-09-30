from __future__ import annotations

import subprocess

from agentops import config


def ensure_env_file() -> None:
    config.ENV_FILE.touch(exist_ok=True)


def compose(*args: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run `docker compose` for the agentops project. argv list, never a shell."""
    ensure_env_file()
    cmd = [
        "docker", "compose",
        "-p", config.PROJECT,
        "-f", str(config.COMPOSE_FILE),
        "--env-file", str(config.ENV_FILE),
        *args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)


def set_env_var(name: str, value: str) -> None:
    ensure_env_file()
    lines = [
        line for line in config.ENV_FILE.read_text().splitlines()
        if not line.startswith(f"{name}=")
    ]
    lines.append(f"{name}={value}")
    config.ENV_FILE.write_text("\n".join(lines) + "\n")


def clear_env() -> None:
    config.ENV_FILE.write_text("")
