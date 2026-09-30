import httpx

from agentops import config
from agentops.verifier import VerifyResult, verify, wait_healthy


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def healthy_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(200, json={"status": "ok"})
    if request.url.path == "/orders" and request.method == "POST":
        return httpx.Response(200, json={"id": "abc"})
    if request.url.path == "/orders/abc":
        return httpx.Response(200, json={"status": "done"})
    return httpx.Response(404)


def test_healthy():
    result = verify(make_client(healthy_handler))
    assert result.healthy
    assert result.latency_s is not None


def test_service_health_failure_names_service():
    def handler(request):
        if request.url.port == 8001 and request.url.path == "/health":
            return httpx.Response(503, json={"status": "fail", "reason": "redis: down"})
        return healthy_handler(request)

    result = verify(make_client(handler))
    assert not result.healthy
    assert result.reason.startswith("api /health 503")


def test_unreachable_service():
    def handler(request):
        if request.url.port == 8000:
            raise httpx.ConnectError("refused")
        return healthy_handler(request)

    result = verify(make_client(handler))
    assert not result.healthy
    assert result.reason.startswith("gateway unreachable")


def test_slow_synthetic_order(monkeypatch):
    monkeypatch.setattr(config, "LATENCY_THRESHOLD_S", -1.0)
    result = verify(make_client(healthy_handler))
    assert not result.healthy
    assert "slow" in result.reason


def test_order_never_processed(monkeypatch):
    monkeypatch.setattr(config, "ORDER_DONE_TIMEOUT_S", 0.3)

    def handler(request):
        if request.url.path == "/orders/abc":
            return httpx.Response(200, json={"status": "pending"})
        return healthy_handler(request)

    result = verify(make_client(handler))
    assert not result.healthy
    assert result.reason.startswith("order not processed")


def test_wait_healthy_returns_when_healthy():
    seq = iter([VerifyResult(False, "x"), VerifyResult(False, "x"), VerifyResult(True)])
    result = wait_healthy(timeout=5, interval=0, verify_fn=lambda: next(seq))
    assert result.healthy


def test_wait_healthy_times_out():
    result = wait_healthy(timeout=0.3, interval=0.05, verify_fn=lambda: VerifyResult(False, "down"))
    assert not result.healthy
    assert result.reason == "down"
