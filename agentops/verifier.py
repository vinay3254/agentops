from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from agentops import config


@dataclass(frozen=True)
class VerifyResult:
    healthy: bool
    reason: str = ""
    latency_s: float | None = None


def verify(client: httpx.Client | None = None) -> VerifyResult:
    own = client is None
    client = client or httpx.Client(timeout=3.0)
    try:
        return _verify(client)
    finally:
        if own:
            client.close()


def _verify(client: httpx.Client) -> VerifyResult:
    for svc in config.APP_SERVICES:
        try:
            resp = client.get(f"{config.service_url(svc)}/health")
        except httpx.HTTPError as e:
            return VerifyResult(False, f"{svc} unreachable: {type(e).__name__}")
        if resp.status_code != 200:
            return VerifyResult(False, f"{svc} /health {resp.status_code}: {resp.text[:200]}")

    gateway = config.service_url("gateway")
    start = time.perf_counter()
    try:
        resp = client.post(f"{gateway}/orders", json={"item": "probe", "qty": 1})
    except httpx.HTTPError as e:
        return VerifyResult(False, f"synthetic order failed: {type(e).__name__}")
    latency = time.perf_counter() - start
    if resp.status_code != 200:
        return VerifyResult(False, f"synthetic order failed: {resp.status_code} {resp.text[:200]}", latency)
    if latency > config.LATENCY_THRESHOLD_S:
        return VerifyResult(False, f"synthetic order slow: {latency:.2f}s", latency)

    try:
        oid = resp.json()["id"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return VerifyResult(False, "synthetic order failed: malformed response", latency)
    deadline = time.monotonic() + config.ORDER_DONE_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            r2 = client.get(f"{gateway}/orders/{oid}")
            if r2.status_code == 200 and r2.json().get("status") == "done":
                return VerifyResult(True, "", latency)
        except (httpx.HTTPError, ValueError, AttributeError):
            pass
        time.sleep(0.2)
    return VerifyResult(False, "order not processed by worker within timeout", latency)


def wait_healthy(timeout: float = 60.0, interval: float = 1.0, verify_fn=None) -> VerifyResult:
    verify_fn = verify_fn or verify
    deadline = time.monotonic() + timeout
    while True:
        result = verify_fn()
        if result.healthy or time.monotonic() >= deadline:
            return result
        time.sleep(interval)
