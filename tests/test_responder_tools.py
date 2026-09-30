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
