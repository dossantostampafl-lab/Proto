# ruff: noqa: I001
from __future__ import annotations

from apps.api.app.settings import settings
from services.events import EventRuntime


event_runtime = EventRuntime(
    backend=settings.event_bus_backend,
    redis_url=settings.redis_url,
)

__all__ = ["event_runtime"]
