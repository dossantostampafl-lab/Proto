"""Bounded private conversations on PROTO's existing SQL engine; no trading authority."""

from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

metadata = MetaData()
conversations = Table(
    "agent_conversations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("owner", String(64), nullable=False, index=True),
    Column("agent_id", String(160), nullable=False),
    Column("version", Integer, nullable=False),
    Column("messages", Text, nullable=False),
)


class ConversationStore:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def create(self, owner: str, agent_id: str) -> str:
        identifier = str(uuid4())
        async with self.engine.begin() as conn:
            await conn.execute(
                conversations.insert().values(
                    id=identifier, owner=owner, agent_id=agent_id, version=0, messages="[]"
                )
            )
        return identifier

    async def read(self, owner: str, identifier: str) -> dict:
        async with self.engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(conversations).where(
                            conversations.c.id == identifier, conversations.c.owner == owner
                        )
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise KeyError("Conversation not found")
        return {**row, "messages": json.loads(row["messages"])}

    async def append(self, owner: str, identifier: str, version: int, messages: list) -> None:
        if len(messages) > 40 or len(json.dumps(messages)) > 64000:
            raise ValueError("Conversation full; start a new conversation")
        async with self.engine.begin() as conn:
            result = await conn.execute(
                update(conversations)
                .where(
                    conversations.c.id == identifier,
                    conversations.c.owner == owner,
                    conversations.c.version == version,
                )
                .values(messages=json.dumps(messages), version=version + 1)
            )
            if result.rowcount != 1:
                raise ValueError("Conversation changed; reload before retrying")
