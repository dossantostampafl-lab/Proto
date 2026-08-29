"""Hawkes-process research engine for simulated event intensity."""

from .core import HawkesEstimate, ExponentialHawkesEngine

__all__ = ["ExponentialHawkesEngine", "HawkesEstimate"]
