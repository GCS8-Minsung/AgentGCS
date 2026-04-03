from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from app.core.default_guideline import DEFAULT_AGENTGCS_GUIDELINE


DEFAULT_PERSONA = {
    "id": "default-balanced",
    "name": "기본 균형형",
    "stats": {
        "creativity": 82,
        "logic": 76,
        "critical_thinking": 79,
        "data_dependency": 71,
        "empathy": 48,
        "drive": 84,
    },
}

DEFAULT_SETTINGS = {
    "theme": "system",
    "dev_mode": False,
    "debug_raw_mode": False,
    "claude_base_url": "https://claude.1000.school",
    "preferred_model": "gpt-5.2",
    "knowledge_base_prompt": DEFAULT_AGENTGCS_GUIDELINE,
    "chat_mode_personas": {
        "cautious": {
            "creativity": 42,
            "logic": 92,
            "critical_thinking": 95,
            "data_dependency": 88,
            "empathy": 52,
            "drive": 58,
        },
        "balanced": {
            "creativity": 74,
            "logic": 78,
            "critical_thinking": 79,
            "data_dependency": 72,
            "empathy": 64,
            "drive": 72,
        },
        "creative": {
            "creativity": 96,
            "logic": 62,
            "critical_thinking": 58,
            "data_dependency": 46,
            "empathy": 68,
            "drive": 86,
        },
        "autonomous": {
            "creativity": 72,
            "logic": 82,
            "critical_thinking": 78,
            "data_dependency": 66,
            "empathy": 54,
            "drive": 93,
        },
    },
    "approval_policy": {
        "cautious_requires_approval": True,
        "balanced_requires_approval": True,
        "creative_requires_approval": True,
        "autonomous_needs_first_warning": True,
        "autonomous_warning_accepted": False,
    },
    "active_persona_id": DEFAULT_PERSONA["id"],
    "personas": [DEFAULT_PERSONA],
}


class DevStore:
    """
    In-memory fallback store for local/dev mode when Supabase is unavailable.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._keys: dict[str, dict[str, dict]] = {}
        self._tasks: dict[str, dict[str, dict]] = {}
        self._settings: dict[str, dict] = {}
        self._threads: dict[str, dict[str, dict]] = {}
        self._messages: dict[str, list[dict]] = {}

    def _now(self) -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    async def upsert_user_key(
        self,
        *,
        user_id: str,
        key_name: str,
        encrypted_value: str,
        nonce: str,
        key_version: int,
    ) -> dict:
        async with self._lock:
            key_map = self._keys.setdefault(user_id, {})
            current = key_map.get(key_name)
            now = self._now()
            row = {
                "id": current["id"] if current else str(uuid4()),
                "user_id": user_id,
                "key_name": key_name,
                "encrypted_value": encrypted_value,
                "nonce": nonce,
                "key_version": key_version,
                "created_at": current["created_at"] if current else now,
                "updated_at": now,
            }
            key_map[key_name] = row
            return deepcopy(row)

    async def list_user_keys(self, user_id: str) -> list[dict]:
        async with self._lock:
            rows = list(self._keys.get(user_id, {}).values())
            rows.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
            return deepcopy(rows)

    async def get_user_key(self, user_id: str, key_name: str) -> dict | None:
        async with self._lock:
            row = self._keys.get(user_id, {}).get(key_name)
            return deepcopy(row) if row else None

    async def list_tasks(self, user_id: str) -> list[dict]:
        async with self._lock:
            rows = list(self._tasks.get(user_id, {}).values())
            rows.sort(key=lambda row: row.get("created_at", ""))
            return deepcopy(rows)

    async def create_task(self, user_id: str, payload: dict) -> dict:
        async with self._lock:
            task_map = self._tasks.setdefault(user_id, {})
            task_id = payload.get("id") or str(uuid4())
            now = self._now()
            row = {
                "id": task_id,
                "user_id": user_id,
                "title": payload.get("title", ""),
                "description": payload.get("description"),
                "status": payload.get("status", "todo"),
                "due_date": payload.get("due_date"),
                "created_at": payload.get("created_at", now),
                "updated_at": now,
            }
            task_map[task_id] = row
            return deepcopy(row)

    async def update_task(self, user_id: str, task_id: str, updates: dict) -> dict | None:
        async with self._lock:
            row = self._tasks.setdefault(user_id, {}).get(task_id)
            if not row:
                return None
            row.update(updates)
            row["updated_at"] = self._now()
            return deepcopy(row)

    async def delete_task(self, user_id: str, task_id: str) -> None:
        async with self._lock:
            self._tasks.setdefault(user_id, {}).pop(task_id, None)

    async def get_settings(self, user_id: str) -> dict:
        async with self._lock:
            current = self._settings.get(user_id)
            if current is None:
                current = deepcopy(DEFAULT_SETTINGS)
                self._settings[user_id] = current
            return deepcopy(current)

    async def upsert_settings(self, user_id: str, settings: dict) -> dict:
        async with self._lock:
            merged = deepcopy(DEFAULT_SETTINGS)
            merged.update(settings)
            self._settings[user_id] = merged
            return deepcopy(merged)

    async def list_threads(self, user_id: str, limit: int = 20) -> list[dict]:
        async with self._lock:
            rows = list(self._threads.get(user_id, {}).values())
            rows.sort(key=lambda row: row.get("updated_at", ""), reverse=True)
            return deepcopy(rows[:limit])

    async def delete_thread(self, user_id: str, thread_id: str) -> bool:
        async with self._lock:
            removed = self._threads.setdefault(user_id, {}).pop(thread_id, None)
            self._messages.pop(thread_id, None)
            return removed is not None

    async def delete_threads_except(self, user_id: str, keep_thread_id: str | None = None) -> int:
        async with self._lock:
            thread_map = self._threads.setdefault(user_id, {})
            targets = [thread_id for thread_id in thread_map if thread_id != keep_thread_id]
            for thread_id in targets:
                thread_map.pop(thread_id, None)
                self._messages.pop(thread_id, None)
            return len(targets)

    async def ensure_thread(self, user_id: str, thread_id: str | None, title: str | None) -> dict:
        async with self._lock:
            thread_map = self._threads.setdefault(user_id, {})
            now = self._now()
            if thread_id and thread_id in thread_map:
                row = thread_map[thread_id]
                if title:
                    row["title"] = title
                row["updated_at"] = now
                return deepcopy(row)

            final_id = thread_id or str(uuid4())
            row = {
                "id": final_id,
                "user_id": user_id,
                "title": title or "새 대화",
                "created_at": now,
                "updated_at": now,
            }
            thread_map[final_id] = row
            self._messages.setdefault(final_id, [])
            return deepcopy(row)

    async def append_message(
        self,
        *,
        user_id: str,
        thread_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> dict:
        async with self._lock:
            thread_map = self._threads.setdefault(user_id, {})
            if thread_id not in thread_map:
                now = self._now()
                thread_map[thread_id] = {
                    "id": thread_id,
                    "user_id": user_id,
                    "title": "새 대화",
                    "created_at": now,
                    "updated_at": now,
                }
            now = self._now()
            row = {
                "id": str(uuid4()),
                "thread_id": thread_id,
                "user_id": user_id,
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "created_at": now,
            }
            self._messages.setdefault(thread_id, []).append(row)
            thread_map[thread_id]["updated_at"] = now
            if thread_map[thread_id]["title"] == "새 대화" and role == "user":
                thread_map[thread_id]["title"] = content[:30] or "새 대화"
            return deepcopy(row)

    async def list_messages(self, user_id: str, thread_id: str, limit: int = 100) -> list[dict]:
        async with self._lock:
            thread = self._threads.get(user_id, {}).get(thread_id)
            if not thread:
                return []
            rows = self._messages.get(thread_id, [])
            return deepcopy(rows[-limit:])


dev_store = DevStore()
