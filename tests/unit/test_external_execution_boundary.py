import pytest

from services.safety import ExternalExecutionDisabledError, ExternalExecutionGate


def test_external_execution_gate_is_fail_closed() -> None:
    gate = ExternalExecutionGate()

    assert gate.enabled is False
    with pytest.raises(ExternalExecutionDisabledError):
        gate.submit({"action": "hypothetical"})
    with pytest.raises(ExternalExecutionDisabledError):
        gate.deposit(1)
    with pytest.raises(ExternalExecutionDisabledError):
        gate.withdraw(1)
    with pytest.raises(ExternalExecutionDisabledError):
        gate.credentials()
