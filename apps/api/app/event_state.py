from services.events import EventRuntime
from .settings import settings

event_runtime = EventRuntime(
    backend=settings.event_bus_backend,
    redis_url=settings.redis_url,
)

__all__ = ["event_runtime"]
