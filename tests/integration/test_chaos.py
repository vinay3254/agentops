import time

import pytest

from agentops import chaos
from agentops.verifier import verify, wait_healthy

pytestmark = pytest.mark.docker


def _unhealthy_within(timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not verify().healthy:
            return True
        time.sleep(1)
    return False


def test_reset_gives_healthy_stack(stack):
    chaos.reset()
    assert verify().healthy


@pytest.mark.parametrize("fault", chaos.FAULTS)
def test_fault_breaks_stack_and_reset_heals(stack, fault):
    chaos.reset()
    chaos.inject(fault)
    assert _unhealthy_within(), f"{fault} did not make the verifier unhealthy"
    chaos.reset()
    assert wait_healthy(timeout=30).healthy
