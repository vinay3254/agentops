from __future__ import annotations

import fnmatch
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
SECRET_NAME_RE = re.compile(r"(SECRET|PASSWORD|TOKEN|KEY|PASS|CRED|AUTH|PRIVATE|DSN|CERT|COOKIE|SESSION)")
PS_FIELDS_ALLOWED = {"pid", "ppid", "%cpu", "%mem", "cmd", "comm", "args", "etime", "stat", "user", "rss", "vsz", "time"}


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


def _check_file_args(args: list[str], flag_re: str, need_path: bool, check_creds: bool = False) -> None:
    paths = 0
    for a in args:
        if a.startswith("-"):
            if not re.fullmatch(flag_re, a):
                raise PolicyError(f"flag not allowed: {a}")
        elif a.isdigit():
            continue
        else:
            _check_read_path(a)
            if check_creds:
                _check_credential_file(a)
            paths += 1
    if need_path and paths == 0:
        raise PolicyError("a file path is required")


def _check_credential_file(path: str) -> None:
    """Deny reading credential files for cat/tail."""
    norm = posixpath.normpath(path)
    basename = posixpath.basename(norm)

    # Check if path is under /etc/ssl/private
    if norm == "/etc/ssl/private" or norm.startswith("/etc/ssl/private/"):
        raise PolicyError(f"credential file not allowed: {path}")

    # Check basename patterns
    patterns = ["shadow*", "gshadow*", ".env*", "*.pem", "*.key", "id_rsa*", "sudoers*"]
    for pattern in patterns:
        if fnmatch.fnmatch(basename.lower(), pattern):
            raise PolicyError(f"credential file not allowed: {path}")


def _ps(args):
    """Allow: ps, ps aux, ps -ef, ps -eo pid,ppid,%cpu,..."""
    if args == []:
        return
    if args == ["aux"]:
        return
    if args == ["-ef"]:
        return
    if len(args) >= 2 and args[0] == "-eo":
        fields = args[1].split(",")
        for field in fields:
            if not field or field not in PS_FIELDS_ALLOWED:
                raise PolicyError(f"ps field not allowed: {field}")
        return
    raise PolicyError(f"ps arguments not allowed: {' '.join(args)}")


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
    "cat": lambda a: _check_file_args(a, r"(?!)", need_path=True, check_creds=True),
    "tail": lambda a: _check_file_args(a, r"-n|-\d+", need_path=True, check_creds=True),
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
    previous_redacted = False

    for line in text.splitlines():
        name, sep, value = line.partition("=")

        # A new variable must: have valid identifier name, have =, and have at least one char value
        is_new_var = sep and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name) and value

        if is_new_var:
            # This is a new variable definition
            if SECRET_NAME_RE.search(name.upper()):
                out.append(f"{name}=***")
                previous_redacted = True
            else:
                # Redact URL credentials in the value
                redacted_value = re.sub(r"(://)[^/@\s:]*:[^@\s]*@", r"\1***@", value)
                out.append(f"{name}={redacted_value}")
                previous_redacted = False
        else:
            # This is a continuation line
            if previous_redacted:
                out.append("***")
            else:
                out.append(line)

    return "\n".join(out)
