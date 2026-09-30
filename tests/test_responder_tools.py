import json

import pytest

from agentops import config
from agentops.responder_tools import build_registry
from agentops.tools import ToolError, truncate
from agentops.verifier import VerifyResult
from tests.fakes import FakeExecutor

ALL = ["get_service_status", "read_logs", "get_metrics", "run_diagnostic", "restart_service",
       "start_service", "set_env", "cleanup_files", "kill_process", "verify_health", "finish"]


def make():
    ex = FakeExecutor()
    reg = build_registry(ex, lambda: VerifyResult(False, "api /health 503"))
    return ex, reg


def test_all_tools_registered_with_permissions():
    _, reg = make()
    assert len(reg.schemas(ALL)) == len(ALL)
    assert reg.permission("read_logs") == "read"
    assert reg.permission("restart_service") == "mutate"
    assert reg.permission("finish") == "control"


def test_tools_delegate_to_executor():
    ex, reg = make()
    reg.call("restart_service", {"service": "api"}, ALL)
    reg.call("start_service", {"service": "redis"}, ALL)
    reg.call("set_env", {"service": "api", "key": "DEBUG_SPIN", "value": "0"}, ALL)
    reg.call("cleanup_files", {"service": "api", "path": "/data/junk.bin"}, ALL)
    reg.call("kill_process", {"service": "api", "pid": "77"}, ALL)
    assert ex.actions == [
        ("restart", "api"), ("start", "redis"), ("set_env", "api", "DEBUG_SPIN", "0"),
        ("cleanup_files", "api", "/data/junk.bin"), ("kill_process", "api", 77),
    ]


def test_verify_health_returns_json():
    _, reg = make()
    data = json.loads(reg.call("verify_health", {}, ALL))
    assert data == {"healthy": False, "reason": "api /health 503"}


def test_status_is_json():
    _, reg = make()
    assert "gateway" in json.loads(reg.call("get_service_status", {}, ALL))


def test_get_metrics_only_for_api():
    _, reg = make()
    with pytest.raises(ToolError):
        reg.call("get_metrics", {"service": "worker"}, ALL)


def test_large_logs_truncate_at_limit():
    _, reg = make()
    out = truncate(reg.call("read_logs", {"service": "api", "tail": 200}, ALL))
    assert len(out) <= config.TOOL_RESULT_MAX_CHARS + 60
    assert "truncated" in out


def test_run_diagnostic_description_matches_hardened_policy():
    """Verify description accurately reflects what's actually allowed."""
    _, reg = make()
    schema = reg.schemas(["run_diagnostic"])[0]
    desc = schema["function"]["description"]
    # Key substrings from the hardened policy implementation
    assert "ps -eo" in desc, f"Description should mention ps -eo fields: {desc}"
    assert "top -bn1" in desc, f"Description should mention top -bn1: {desc}"
    assert "df -h" in desc, f"Description should mention df -h: {desc}"
    assert "tail -n" in desc, f"Description should mention tail -n: {desc}"
    assert "/app, /data or /etc" in desc, f"Description should specify allowed paths: {desc}"
    assert "refused" in desc, f"Description should mention credential files are refused: {desc}"


def test_get_metrics_handles_httpx_errors(monkeypatch):
    """Test that httpx errors are caught and converted to ToolError."""
    import httpx

    _, reg = make()

    # Test ConnectError
    def raise_connect_error(*args, **kwargs):
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr("agentops.responder_tools.httpx.get", raise_connect_error)
    with pytest.raises(ToolError) as exc_info:
        reg.call("get_metrics", {"service": "api"}, ALL)
    assert "unreachable" in str(exc_info.value)
    assert "ConnectError" in str(exc_info.value)

    # Test TimeoutException
    def raise_timeout(*args, **kwargs):
        raise httpx.TimeoutException("timeout")

    monkeypatch.setattr("agentops.responder_tools.httpx.get", raise_timeout)
    with pytest.raises(ToolError) as exc_info:
        reg.call("get_metrics", {"service": "api"}, ALL)
    assert "unreachable" in str(exc_info.value)
    assert "TimeoutException" in str(exc_info.value)


def test_get_metrics_handles_non_200_status(monkeypatch):
    """Test that non-200 status codes raise ToolError."""
    import httpx

    _, reg = make()

    # Mock response with 503 status
    class MockResponse:
        status_code = 503
        text = "Service Unavailable"

    monkeypatch.setattr("agentops.responder_tools.httpx.get", lambda *args, **kwargs: MockResponse())
    with pytest.raises(ToolError) as exc_info:
        reg.call("get_metrics", {"service": "api"}, ALL)
    assert "503" in str(exc_info.value)


def test_get_metrics_returns_text_on_success(monkeypatch):
    """Test that successful 200 response returns the body text."""
    _, reg = make()

    # Mock successful response
    class MockResponse:
        status_code = 200
        text = "requests_total 42\np95_latency_ms 150\n"

    monkeypatch.setattr("agentops.responder_tools.httpx.get", lambda *args, **kwargs: MockResponse())
    result = reg.call("get_metrics", {"service": "api"}, ALL)
    assert result == "requests_total 42\np95_latency_ms 150\n"
