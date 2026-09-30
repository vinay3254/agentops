import time

import httpx
import pytest

from agentops import config

pytestmark = pytest.mark.docker


def test_health_endpoints(stack):
    for svc in config.APP_SERVICES:
        r = httpx.get(f"{config.service_url(svc)}/health", timeout=3)
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_order_flows_through_worker(stack):
    gw = config.service_url("gateway")
    r = httpx.post(f"{gw}/orders", json={"item": "book", "qty": 2}, timeout=5)
    assert r.status_code == 200
    oid = r.json()["id"]
    deadline = time.monotonic() + 10
    data = {}
    while time.monotonic() < deadline:
        data = httpx.get(f"{gw}/orders/{oid}", timeout=3).json()
        if data.get("status") == "done":
            break
        time.sleep(0.3)
    assert data["status"] == "done"
    assert data["item"] == "book"


def test_api_metrics(stack):
    m = httpx.get(f"{config.service_url('api')}/metrics", timeout=3).json()
    assert {"requests", "p95_ms", "last_cpu_probe_ms"} <= set(m)
