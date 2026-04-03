from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.core.security import EncryptedPayload
from app.core.security import SecurityManager
from app.core.supabase_client import get_supabase_admin
from app.services.dev_store import dev_store


SCHOOL_API_BASE_URL = "https://api.1000.school"
DEFAULT_TIMEOUT = 20.0
_security_manager = SecurityManager(settings.encryption_master_key)


class SchoolApiError(RuntimeError):
    pass


@dataclass(slots=True)
class SchoolApiClient:
    bearer_token: str
    timeout: float = DEFAULT_TIMEOUT

    def _auth_headers(self) -> list[tuple[str, dict[str, str]]]:
        token = self.bearer_token.strip()
        base = {
            "Accept": "application/json",
            "User-Agent": "AgentGCS/1.0 (+https://localhost)",
        }
        return [
            ("bearer", {**base, "Authorization": f"Bearer {token}"}),
            ("x-api-key", {**base, "x-api-key": token}),
            ("raw-auth", {**base, "Authorization": token}),
        ]

    async def _request_once(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(base_url=SCHOOL_API_BASE_URL, timeout=self.timeout) as client:
            return await client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers=headers,
            )

    def _parse_response_body(self, response: httpx.Response) -> Any:
        if not response.text:
            return {}
        content_type = response.headers.get("content-type", "").lower()
        if "application/json" in content_type:
            return response.json()
        try:
            return response.json()
        except Exception:
            return {"raw_text": response.text[:800]}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        attempts: list[str] = []
        for auth_style, headers in self._auth_headers():
            response = await self._request_once(
                method,
                path,
                headers=headers,
                params=params,
                json_body=json_body,
            )
            if response.status_code < 400:
                return self._parse_response_body(response)

            body_preview = (response.text or "")[:260]
            attempts.append(f"{auth_style}:{response.status_code}:{body_preview}")

            # 권한 오류는 다른 인증 헤더 포맷으로 재시도한다.
            if response.status_code in {401, 403}:
                continue

            raise SchoolApiError(
                f"{method} {path} failed({response.status_code}) with {auth_style}. "
                f"body={body_preview}"
            )

        raise SchoolApiError(
            f"{method} {path} auth failed across header variants. attempts={' | '.join(attempts)}"
        )

    async def _request_any(
        self,
        method: str,
        paths: list[str],
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        errors: list[str] = []
        for path in paths:
            try:
                return await self._request(
                    method,
                    path,
                    params=params,
                    json_body=json_body,
                )
            except SchoolApiError as exc:
                errors.append(f"{path}: {str(exc)}")
                continue
        raise SchoolApiError(
            f"{method} endpoint candidates exhausted. {' || '.join(errors[:4])}"
        )

    async def request_known_path(
        self,
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        normalized = path.strip()
        if not normalized.startswith("/"):
            normalized = f"/{normalized}"

        allowed_exact = {
            "/auth/me",
            "/teams/me",
            "/users/me/league",
            "/snippet_date",
            "/leaderboards",
            "/meeting-rooms",
            "/daily-snippets",
            "/weekly-snippets",
            "/openapi.json",
        }
        allowed_prefixes = ("/meeting-rooms/", "/daily-snippets/", "/weekly-snippets/")
        if normalized not in allowed_exact and not any(
            normalized.startswith(prefix) for prefix in allowed_prefixes
        ):
            raise SchoolApiError(
                f"Path not allowed for safety: {normalized}. "
                "Use one of the documented school API endpoints."
            )

        return await self._request(
            method=method,
            path=normalized,
            params=params,
            json_body=json_body,
        )

    async def list_meeting_rooms(self) -> list[dict]:
        data = await self._request("GET", "/meeting-rooms")
        return data if isinstance(data, list) else []

    async def get_auth_me(self) -> dict:
        data = await self._request_any("GET", ["/auth/me", "/me", "/users/me"])
        return data if isinstance(data, dict) else {"raw": data}

    async def get_my_team(self) -> dict:
        data = await self._request_any("GET", ["/teams/me", "/team/me"])
        return data if isinstance(data, dict) else {"raw": data}

    async def get_my_league(self) -> dict:
        data = await self._request_any("GET", ["/users/me/league", "/league/me"])
        return data if isinstance(data, dict) else {"raw": data}

    async def get_snippet_date(self) -> dict:
        data = await self._request_any("GET", ["/snippet_date", "/snippet-date"])
        return data if isinstance(data, dict) else {"raw": data}

    async def get_openapi_summary(self) -> dict:
        data = await self._request("GET", "/openapi.json")
        if not isinstance(data, dict):
            return {"raw": data}
        paths = data.get("paths")
        if not isinstance(paths, dict):
            return {"title": data.get("info", {}).get("title"), "paths": []}
        summary_paths: list[dict[str, Any]] = []
        for path, methods in sorted(paths.items())[:120]:
            if not isinstance(methods, dict):
                continue
            method_names = sorted(str(method).upper() for method in methods.keys())
            summary_paths.append({"path": path, "methods": method_names})
        return {
            "title": data.get("info", {}).get("title"),
            "version": data.get("info", {}).get("version"),
            "path_count": len(paths),
            "paths": summary_paths,
        }

    async def get_leaderboard(
        self,
        *,
        period: str = "daily",
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        params = {"period": period, "limit": limit, "offset": offset}
        data = await self._request_any("GET", ["/leaderboards", "/leaderboard"], params=params)
        return data if isinstance(data, dict) else {"raw": data}

    async def list_room_reservations(self, room_id: int, date: str | None = None) -> list[dict]:
        params = {"date": date} if date else None
        data = await self._request("GET", f"/meeting-rooms/{room_id}/reservations", params=params)
        return data if isinstance(data, list) else []

    async def create_room_reservation(
        self, *, room_id: int, start_at: str, end_at: str, purpose: str | None = None
    ) -> dict:
        payload = {"start_at": start_at, "end_at": end_at, "purpose": purpose}
        return await self._request(
            "POST", f"/meeting-rooms/{room_id}/reservations", json_body=payload
        )

    async def cancel_room_reservation(self, reservation_id: int) -> dict:
        return await self._request("DELETE", f"/meeting-rooms/reservations/{reservation_id}")

    async def create_daily_snippet(self, content: str) -> dict:
        return await self._request("POST", "/daily-snippets", json_body={"content": content})

    async def update_daily_snippet(self, snippet_id: int, content: str) -> dict:
        return await self._request(
            "PUT", f"/daily-snippets/{snippet_id}", json_body={"content": content}
        )

    async def list_daily_snippets(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        q: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict:
        params = {
            "limit": limit,
            "offset": offset,
            "q": q,
            "from_date": from_date,
            "to_date": to_date,
        }
        filtered = {k: v for k, v in params.items() if v is not None}
        return await self._request("GET", "/daily-snippets", params=filtered)

    async def create_weekly_snippet(self, content: str) -> dict:
        return await self._request("POST", "/weekly-snippets", json_body={"content": content})

    async def update_weekly_snippet(self, snippet_id: int, content: str) -> dict:
        return await self._request(
            "PUT", f"/weekly-snippets/{snippet_id}", json_body={"content": content}
        )

    async def list_weekly_snippets(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        q: str | None = None,
        from_week: str | None = None,
        to_week: str | None = None,
    ) -> dict:
        params = {
            "limit": limit,
            "offset": offset,
            "q": q,
            "from_week": from_week,
            "to_week": to_week,
        }
        filtered = {k: v for k, v in params.items() if v is not None}
        return await self._request("GET", "/weekly-snippets", params=filtered)


async def get_school_client_for_user(user_id: str) -> SchoolApiClient:
    fallback_token = settings.school_api_token or settings.anthropic_auth_token

    if not settings.supabase_url or not settings.supabase_service_role_key:
        local_row = await dev_store.get_user_key(user_id, "school_api_token")
        if local_row:
            decrypted = _security_manager.decrypt_text(
                EncryptedPayload(
                    nonce=local_row["nonce"],
                    ciphertext=local_row["encrypted_value"],
                    key_version=local_row.get("key_version", 1),
                ),
                aad=user_id,
            )
            return SchoolApiClient(bearer_token=decrypted)
        if fallback_token:
            return SchoolApiClient(bearer_token=fallback_token)
        raise SchoolApiError(
            "Supabase key-store is disabled and no SCHOOL_API_TOKEN/ANTHROPIC_AUTH_TOKEN is set."
        )

    def _fetch_key():
        client = get_supabase_admin()
        return (
            client.table("user_keys")
            .select("encrypted_value,nonce,key_version")
            .eq("user_id", user_id)
            .eq("key_name", "school_api_token")
            .limit(1)
            .execute()
        )

    try:
        result = await asyncio.to_thread(_fetch_key)
        row = (result.data or [None])[0]
    except Exception:
        row = None

    if not row:
        local_row = await dev_store.get_user_key(user_id, "school_api_token")
        if local_row:
            decrypted = _security_manager.decrypt_text(
                EncryptedPayload(
                    nonce=local_row["nonce"],
                    ciphertext=local_row["encrypted_value"],
                    key_version=local_row.get("key_version", 1),
                ),
                aad=user_id,
            )
            return SchoolApiClient(bearer_token=decrypted)
        if fallback_token:
            return SchoolApiClient(bearer_token=fallback_token)
        raise SchoolApiError(
            "school_api_token not found. Save it first via POST /api/keys with key_name=school_api_token."
        )

    decrypted = _security_manager.decrypt_text(
        EncryptedPayload(
            nonce=row["nonce"],
            ciphertext=row["encrypted_value"],
            key_version=row.get("key_version", 1),
        ),
        aad=user_id,
    )
    return SchoolApiClient(bearer_token=decrypted)
