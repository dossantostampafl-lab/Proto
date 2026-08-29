"""Safety boundaries that permanently disable external financial execution."""

from .external_execution import ExternalExecutionDisabledError, ExternalExecutionGate

__all__ = ["ExternalExecutionDisabledError", "ExternalExecutionGate"]
