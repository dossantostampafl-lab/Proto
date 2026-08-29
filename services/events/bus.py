from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis


@dataclass(frozen=True, slots=True)
class BusMessage:
    message_id: str
    stream: str
    payload: dict[str, str]


class EventBus(Protocol):
    async def publish(self, stream: str, payload: Mapping[str, str]) -> str: ...

    async def subscribe(self, stream: str, *, after: str = "0-0") -> AsyncIterator[BusMessage]: ...

    async def ack(self, stream: str, group: str, message_id: str) -> int: ...

    async def retry(self, stream: str, payload: Mapping[str, str]) -> str: ...

    async def dead_letter(self, stream: str, payload: Mapping[str, str]) -> str: ...


class InMemoryEventBus:
    def __init__(self) -> None:
        self._messages: dict[str, list[BusMessage]] = defaultdict(list)
        self._sequence = 0

    async def publish(self, stream: str, payload: Mapping[str, str]) -> str:
        self._sequence += 1
        message_id = f"{self._sequence}-0"
        self._messages[stream].append(
            BusMessage(message_id=message_id, stream=stream, payload=dict(payload))
        )
        return message_id

    async def subscribe(self, stream: str, *, after: str = "0-0") -> AsyncIterator[BusMessage]:
        after_seq = int(after.split("-", maxsplit=1)[0])
        for message in self._messages.get(stream, []):
            seq = int(message.message_id.split("-", maxsplit=1)[0])
            if seq > after_seq:
                yield message

    async def ack(self, stream: str, group: str, message_id: str) -> int:
        _ = (stream, group, message_id)
        return 1

    async def retry(self, stream: str, payload: Mapping[str, str]) -> str:
        return await self.publish(f"{stream}.retry", payload)

    async def dead_letter(self, stream: str, payload: Mapping[str, str]) -> str:
        return await self.publish(f"{stream}.dead_letter", payload)


class RedisStreamsEventBus:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, stream: str, payload: Mapping[str, str]) -> str:
        message_id = await self._redis.xadd(stream, dict(payload))
        return message_id.decode() if isinstance(message_id, bytes) else str(message_id)

    async def subscribe(self, stream: str, *, after: str = "0-0") -> AsyncIterator[BusMessage]:
        cursor = after
        while True:
            batches = await self._redis.xread({stream: cursor}, count=100, block=1_000)
            if not batches:
                return
            for stream_name, entries in batches:
                resolved_stream = (
                    stream_name.decode() if isinstance(stream_name, bytes) else str(stream_name)
                )
                for message_id, payload in entries:
                    resolved_id = (
                        message_id.decode() if isinstance(message_id, bytes) else str(message_id)
                    )
                    resolved_payload = {
                        (key.decode() if isinstance(key, bytes) else str(key)): (
                            value.decode() if isinstance(value, bytes) else str(value)
                        )
                        for key, value in payload.items()
                    }
                    cursor = resolved_id
                    yield BusMessage(resolved_id, resolved_stream, resolved_payload)

    async def ack(self, stream: str, group: str, message_id: str) -> int:
        return int(await self._redis.xack(stream, group, message_id))

    async def retry(self, stream: str, payload: Mapping[str, str]) -> str:
        return await self.publish(f"{stream}.retry", payload)

    async def dead_letter(self, stream: str, payload: Mapping[str, str]) -> str:
        return await self.publish(f"{stream}.dead_letter", payload)
