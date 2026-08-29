from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from redis.asyncio import Redis

from .bus import EventBus, InMemoryEventBus, RedisStreamsEventBus


@dataclass(frozen=True, slots=True)
class EventRuntimeSnapshot:
    backend: str
    started: bool
    ready: bool
    publish_count: int
    publish_failures: int
    last_error: str | None


class EventRuntime:
    """Lifecycle wrapper for the event bus used by monitoring and audit surfaces."""

    def __init__(self, *, backend: str = "memory", redis_url: str = "redis://localhost:6379/0") -> None:
        normalized = backend.strip().lower()
        if normalized not in {"memory", "redis"}:
            raise ValueError("event bus backend must be 'memory' or 'redis'")
        self.backend = normalized
        self.redis_url = redis_url
        self._bus: EventBus | None = None
        self._redis: Redis | None = None
        self._started = False
        self._ready = False
        self._publish_count = 0
        self._publish_failures = 0
        self._last_error: str | None = None

    @property
    def bus(self) -> EventBus:
        if self._bus is None:
            raise RuntimeError("event runtime has not been started")
        return self._bus

    async def start(self) -> None:
        if self._started:
            return
        self._last_error = None
        if self.backend == "memory":
            self._bus = InMemoryEventBus()
            self._started = True
            self._ready = True
            return

        redis = Redis.from_url(self.redis_url)
        self._redis = redis
        try:
            await redis.ping()
        except Exception as error:
            self._started = True
            self._ready = False
            self._last_error = type(error).__name__
            return
        self._bus = RedisStreamsEventBus(redis)
        self._started = True
        self._ready = True

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
        self._redis = None
        self._bus = None
        self._started = False
        self._ready = False

    async def publish(self, stream: str, payload: Mapping[str, str]) -> str:
        if not self._ready or self._bus is None:
            raise RuntimeError("event runtime is not ready")
        try:
            message_id = await self._bus.publish(stream, payload)
        except Exception as error:
            self._publish_failures += 1
            self._last_error = type(error).__name__
            raise
        self._publish_count += 1
        return message_id

    async def safe_publish(self, stream: str, payload: Mapping[str, str]) -> str | None:
        try:
            return await self.publish(stream, payload)
        except Exception:
            return None

    def snapshot(self) -> EventRuntimeSnapshot:
        return EventRuntimeSnapshot(
            backend=self.backend,
            started=self._started,
            ready=self._ready,
            publish_count=self._publish_count,
            publish_failures=self._publish_failures,
            last_error=self._last_error,
        )
