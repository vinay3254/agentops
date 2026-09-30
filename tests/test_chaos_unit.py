import pytest

from agentops import chaos


def test_faults_are_the_five_spec_faults():
    assert chaos.FAULTS == ("crash", "bad_config", "dependency_down", "disk_full", "cpu_hog")


def test_unknown_fault_rejected_before_docker():
    with pytest.raises(ValueError):
        chaos.inject("meteor_strike", executor=object())
