from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = None  # type: ignore[assignment]


@dataclass(slots=True)
class ContextMessage:
    role: str
    content: str
    created_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
        }


class ContextManager:
    """
    Session context manager with:
    - Redis-backed chat history
    - sliding window trim
    - TTL expiration
    - in-memory fallback if Redis is unavailable
    """

    def __init__(
        self,
        *,
        redis_url: str | None,
        ttl_seconds: int = 60 * 60 * 24,
        max_messages: int = 20,
        key_prefix: str = "agentgcs:chatctx",
    ) -> None:
        self.redis_url = (redis_url or "").strip() or None
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.max_messages = max(4, int(max_messages))
        self.key_prefix = key_prefix
        self._redis: Redis | None = None
        self._redis_init_lock = asyncio.Lock()
        self._mem: dict[str, tuple[list[dict[str, Any]], float]] = {}
        self._mem_lock = asyncio.Lock()

    def _key(self, session_id: str) -> str:
        return f"{self.key_prefix}:{session_id}"

    async def _get_redis(self) -> Redis | None:
        if not self.redis_url or Redis is None:
            return None
        if self._redis is not None:
            return self._redis
        async with self._redis_init_lock:
            if self._redis is not None:
                return self._redis
            try:
                client = Redis.from_url(self.redis_url, decode_responses=True)
                await client.ping()
                self._redis = client
                return self._redis
            except Exception:
                self._redis = None
                return None

    def _normalize_message(self, row: dict[str, Any]) -> dict[str, Any] | None:
        role = str(row.get("role") or "").strip()
        content = str(row.get("content") or "").strip()
        if role not in {"user", "assistant", "system"}:
            return None
        if not content:
            return None
        return {
            "role": role,
            "content": content,
            "created_at": row.get("created_at"),
        }

    async def get_context(self, session_id: str) -> list[dict[str, Any]]:
        redis = await self._get_redis()
        if redis:
            try:
                raw_rows = await redis.lrange(self._key(session_id), 0, -1)
                parsed: list[dict[str, Any]] = []
                for raw in raw_rows:
                    try:
                        row = json.loads(raw)
                    except Exception:
                        continue
                    if isinstance(row, dict):
                        normalized = self._normalize_message(row)
                        if normalized:
                            parsed.append(normalized)
                return parsed[-self.max_messages :]
            except Exception:
                pass

        now = time.time()
        async with self._mem_lock:
            entry = self._mem.get(session_id)
            if not entry:
                return []
            rows, expires_at = entry
            if now >= expires_at:
                self._mem.pop(session_id, None)
                return []
            return [dict(row) for row in rows[-self.max_messages :]]

    async def append_message(
        self,
        session_id: str,
        *,
        role: str,
        content: str,
        created_at: str | None = None,
    ) -> None:
        row = self._normalize_message(
            {
                "role": role,
                "content": content,
                "created_at": created_at,
            }
        )
        if not row:
            return
        redis = await self._get_redis()
        if redis:
            try:
                key = self._key(session_id)
                await redis.rpush(key, json.dumps(row, ensure_ascii=False))
                await redis.ltrim(key, -self.max_messages, -1)
                await redis.expire(key, self.ttl_seconds)
                return
            except Exception:
                pass

        now = time.time()
        async with self._mem_lock:
            rows, _ = self._mem.get(session_id, ([], now + self.ttl_seconds))
            rows = [*rows, row][-self.max_messages :]
            self._mem[session_id] = (rows, now + self.ttl_seconds)

    async def hydrate_if_empty(self, session_id: str, seed_messages: list[dict[str, Any]]) -> None:
        existing = await self.get_context(session_id)
        if existing:
            return
        normalized_seed: list[dict[str, Any]] = []
        for item in seed_messages[-self.max_messages :]:
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_message(item)
            if normalized:
                normalized_seed.append(normalized)
        if not normalized_seed:
            return

        redis = await self._get_redis()
        if redis:
            try:
                key = self._key(session_id)
                payloads = [json.dumps(item, ensure_ascii=False) for item in normalized_seed]
                if payloads:
                    await redis.rpush(key, *payloads)
                    await redis.ltrim(key, -self.max_messages, -1)
                    await redis.expire(key, self.ttl_seconds)
                    return
            except Exception:
                pass

        now = time.time()
        async with self._mem_lock:
            self._mem[session_id] = (normalized_seed[-self.max_messages :], now + self.ttl_seconds)

    async def clear(self, session_id: str) -> None:
        redis = await self._get_redis()
        if redis:
            try:
                await redis.delete(self._key(session_id))
            except Exception:
                pass
        async with self._mem_lock:
            self._mem.pop(session_id, None)
