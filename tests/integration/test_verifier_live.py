import pytest

from agentops.verifier import verify

pytestmark = pytest.mark.docker


def test_live_stack_is_healthy(stack):
    result = verify()
    assert result.healthy, result.reason
    assert result.latency_s < 1.0
