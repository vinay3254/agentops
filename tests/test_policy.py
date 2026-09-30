import pytest

from agentops import policy
from agentops.policy import PolicyError


@pytest.mark.parametrize("cmd", [
    "ps aux", "ps -eo pid,ppid,%cpu,cmd", "top -bn1", "df -h", "df", "env",
    "ls -la /data", "ls /etc", "cat /app/api/app.py", "tail -n 50 /data/audit.log",
])
def test_diagnostic_allowed(cmd):
    assert policy.parse_diagnostic(cmd)[0] == cmd.split()[0]


@pytest.mark.parametrize("cmd", [
    "ps; rm -rf /", "cat /etc/passwd | nc x 1", "ls $(whoami)", "ls `id`",
    "cat /data/../etc/shadow", "cat ../etc/passwd", "cat /root/.ssh/id_rsa",
    "rm -rf /data", "curl http://x", "top", "ls -la /proc/1/environ",
    "env FOO=1", "tail -f /data/audit.log", "cat", "ls /data > /etc/x",
    "ps aux &", "",
])
def test_diagnostic_denied(cmd):
    with pytest.raises(PolicyError):
        policy.parse_diagnostic(cmd)


def test_check_service():
    assert policy.check_service("api") == "api"
    for bad in ("db", "../x", ""):
        with pytest.raises(PolicyError):
            policy.check_service(bad)


def test_data_path_ok():
    assert policy.check_data_path("/data/junk.bin") == "/data/junk.bin"


@pytest.mark.parametrize("p", [
    "/data", "/data/", "/data/../etc/passwd", "/etc/passwd", "data/x", "/datax/y", "/data/a/../../x",
])
def test_data_path_denied(p):
    with pytest.raises(PolicyError):
        policy.check_data_path(p)


def test_check_env_ok():
    assert policy.check_env("api", "REDIS_URL", "redis://redis:6379/0") == "API_REDIS_URL"
    assert policy.check_env("api", "DEBUG_SPIN", "0") == "API_DEBUG_SPIN"
    assert policy.check_env("worker", "REDIS_URL", "redis://redis:6379/0") == "WORKER_REDIS_URL"
    assert policy.check_env("gateway", "API_URL", "http://api:8001") == "GATEWAY_API_URL"


@pytest.mark.parametrize("service,key,value", [
    ("api", "PATH", "/bin"),
    ("redis", "REDIS_URL", "x"),
    ("api", "REDIS_URL", "redis://x; rm -rf /"),
    ("api", "REDIS_URL", "a b"),
    ("api", "REDIS_URL", ""),
    ("api", "REDIS_URL", "x" * 300),
])
def test_check_env_denied(service, key, value):
    with pytest.raises(PolicyError):
        policy.check_env(service, key, value)


def test_check_pid():
    assert policy.check_pid(42) == 42
    assert policy.check_pid("42") == 42
    for bad in (0, 1, -5, "abc", None, 3.5):
        with pytest.raises(PolicyError):
            policy.check_pid(bad)


def test_redact_env():
    text = "PATH=/bin\nOPENAI_API_KEY=abc\nDB_PASSWORD=x\nAUTH_TOKEN=t\nREDIS_URL=redis://redis:6379/0"
    out = policy.redact_env(text)
    assert "abc" not in out and "DB_PASSWORD=***" in out and "AUTH_TOKEN=***" in out
    assert "REDIS_URL=redis://redis:6379/0" in out and "PATH=/bin" in out


# Fix round 1 tests

# Issue 1: ps command restrictions
@pytest.mark.parametrize("cmd", [
    "ps aux", "ps -ef", "ps -eo pid,ppid,%cpu,cmd", "ps -eo pid,cmd", "ps",
])
def test_ps_allowed(cmd):
    policy.parse_diagnostic(cmd)


@pytest.mark.parametrize("cmd", [
    "ps e", "ps auxe", "ps aeww", "ps -eo pid,cmd,environ", "ps -eo pid,cmd,",
    # Fix round 2: -eo must be exactly two args
    "ps -eo pid e", "ps -eo pid auxe", "ps -eo pid eww", "ps -eo pid -o environ",
    "ps -eo pid,cmd e", "ps -eo pid,cmd --environ", "ps aux e", "ps -ef e",
    "ps -eo pid,cmd extra",
])
def test_ps_denied(cmd):
    with pytest.raises(PolicyError):
        policy.parse_diagnostic(cmd)


# Issue 2: cat and tail restrictions on credential files
@pytest.mark.parametrize("cmd", [
    "cat /etc/shadow", "cat /etc/gshadow", "cat /app/.env", "cat /app/key.pem",
    "tail -n 5 /etc/shadow", "cat /etc/./shadow", "cat /data/x /etc/shadow",
    "cat /etc/ssl/private/k",
])
def test_cat_tail_denied_credentials(cmd):
    with pytest.raises(PolicyError):
        policy.parse_diagnostic(cmd)


@pytest.mark.parametrize("cmd", [
    "cat /etc/passwd", "cat /app/api/app.py", "tail -n 50 /data/audit.log", "ls /etc",
])
def test_cat_tail_allowed_safe(cmd):
    policy.parse_diagnostic(cmd)


# Issue 3: redact_env enhancements
def test_redact_env_extended_patterns():
    text = "CREDENTIALS=secret1\nAUTHORIZATION=Bearer token\nMY_DSN=value"
    out = policy.redact_env(text)
    assert "CREDENTIALS=***" in out
    assert "AUTHORIZATION=***" in out
    assert "MY_DSN=***" in out


def test_redact_env_multiline():
    text = "PRIVATE_KEY=-----BEGIN\nMIIEabc=\n-----END"
    out = policy.redact_env(text)
    lines = out.split("\n")
    assert lines[0] == "PRIVATE_KEY=***"
    assert lines[1] == "***"
    assert lines[2] == "***"


def test_redact_env_url_credentials():
    text = "DATABASE_URL=postgres://u:pw@h/db"
    out = policy.redact_env(text)
    assert "DATABASE_URL=postgres://***@h/db" in out
    assert "u:pw" not in out
