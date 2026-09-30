from __future__ import annotations

import posixpath
import re
import shlex

from agentops import config


class PolicyError(Exception):
    """An action was refused by the sandbox policy."""


FORBIDDEN_CHARS = set(";&|`$<>\\\n()*?[]{}!")
READ_PREFIXES = ("/app", "/data", "/etc")

ENV_ALLOWLIST = {
    ("api", "REDIS_URL"): "API_REDIS_URL",
    ("api", "DEBUG_SPIN"): "API_DEBUG_SPIN",
    ("worker", "REDIS_URL"): "WORKER_REDIS_URL",
    ("gateway", "API_URL"): "GATEWAY_API_URL",
}
ENV_VALUE_RE = re.compile(r"^[A-Za-z0-9:/._-]{1,200}$")
SECRET_NAME_RE = re.compile(r"(SECRET|PASSWORD|TOKEN|KEY|PASS)")


def check_service(name: str) -> str:
    if name not in config.SERVICES:
        raise PolicyError(f"unknown service {name!r}. Valid: {', '.join(config.SERVICES)}")
    return name


def _check_read_path(path: str) -> None:
    if not path.startswith("/") or ".." in path.split("/"):
        raise PolicyError(f"path not allowed: {path}")
    norm = posixpath.normpath(path)
    if not any(norm == p or norm.startswith(p + "/") for p in READ_PREFIXES):
        raise PolicyError(f"path outside allowed directories {READ_PREFIXES}: {path}")


def _check_file_args(args: list[str], flag_re: str, need_path: bool) -> None:
    paths = 0
    for a in args:
        if a.startswith("-"):
            if not re.fullmatch(flag_re, a):
                raise PolicyError(f"flag not allowed: {a}")
        elif a.isdigit():
            continue
        else:
            _check_read_path(a)
            paths += 1
    if need_path and paths == 0:
        raise PolicyError("a file path is required")


def _ps(args):
    for a in args:
        if not re.fullmatch(r"-?[A-Za-z0-9,=%]+", a):
            raise PolicyError(f"ps argument not allowed: {a}")


def _exact(allowed: list[list[str]]):
    def check(args):
        if args not in allowed:
            raise PolicyError(f"arguments not allowed: {' '.join(args)}")
    return check


_VALIDATORS = {
    "ps": _ps,
    "top": _exact([["-bn1"]]),
    "df": _exact([[], ["-h"]]),
    "env": _exact([[]]),
    "ls": lambda a: _check_file_args(a, r"-[alhtrRS1]+", need_path=False),
    "cat": lambda a: _check_file_args(a, r"(?!)", need_path=True),
    "tail": lambda a: _check_file_args(a, r"-n|-\d+", need_path=True),
}


def parse_diagnostic(command: str) -> list[str]:
    if any(c in FORBIDDEN_CHARS for c in command):
        raise PolicyError("shell metacharacters are not allowed in diagnostic commands")
    try:
        argv = shlex.split(command)
    except ValueError as e:
        raise PolicyError(f"cannot parse command: {e}")
    if not argv:
        raise PolicyError("empty command")
    cmd, args = argv[0], argv[1:]
    if cmd not in _VALIDATORS:
        raise PolicyError(f"command not allowed: {cmd}. Allowed: {', '.join(sorted(_VALIDATORS))}")
    _VALIDATORS[cmd](args)
    return argv


def check_data_path(path: str) -> str:
    if not path.startswith("/") or ".." in path.split("/"):
        raise PolicyError(f"path not allowed: {path}")
    norm = posixpath.normpath(path)
    if not norm.startswith("/data/"):
        raise PolicyError(f"path must be inside /data/: {path}")
    return norm


def check_env(service: str, key: str, value: str) -> str:
    var = ENV_ALLOWLIST.get((service, key))
    if var is None:
        allowed = ", ".join(f"{s}.{k}" for s, k in ENV_ALLOWLIST)
        raise PolicyError(f"env var {service}.{key} is not settable. Allowed: {allowed}")
    if not ENV_VALUE_RE.fullmatch(value or ""):
        raise PolicyError("env value must match [A-Za-z0-9:/._-]{1,200}")
    return var


def check_pid(pid) -> int:
    if isinstance(pid, bool) or pid is None:
        raise PolicyError(f"invalid pid: {pid!r}")
    try:
        n = int(pid) if not isinstance(pid, float) else None
    except (TypeError, ValueError):
        raise PolicyError(f"invalid pid: {pid!r}")
    if n is None or n <= 1:
        raise PolicyError(f"pid not allowed: {pid!r} (PID 1 and invalid pids are protected)")
    return n


def redact_env(text: str) -> str:
    out = []
    for line in text.splitlines():
        name, sep, _ = line.partition("=")
        if sep and SECRET_NAME_RE.search(name.upper()):
            out.append(f"{name}=***")
        else:
            out.append(line)
    return "\n".join(out)
