import time

import httpx
import pytest

from agentops import config
from agentops.compose import compose


def _wait_ports(timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    pending = set(config.APP_SERVICES)
    while pending and time.monotonic() < deadline:
        for svc in list(pending):
            try:
                if httpx.get(f"{config.service_url(svc)}/health", timeout=2).status_code == 200:
                    pending.discard(svc)
            except httpx.HTTPError:
                pass
        time.sleep(1)
    if pending:
        raise RuntimeError(f"services not healthy: {sorted(pending)}")


@pytest.fixture(scope="session")
def stack():
    compose("up", "-d", "--build")
    _wait_ports()
    yield
