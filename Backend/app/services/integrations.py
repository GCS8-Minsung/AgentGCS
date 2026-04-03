from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.core.security import EncryptedPayload
from app.core.security import SecurityManager
from app.core.supabase_client import get_supabase_admin
from app.services.dev_store import dev_store

GOOGLE_API_BASE_URL = "https://www.googleapis.com"
GOOGLE_TIMEOUT = 20.0
_security_manager = SecurityManager(settings.encryption_master_key)


def _is_supabase_enabled() -> bool:
    return bool(settings.supabase_url and settings.supabase_service_role_key)


async def _fetch_encrypted_key(user_id: str, key_name: str) -> dict[str, Any] | None:
    if _is_supabase_enabled():
        def _select():
            client = get_supabase_admin()
            return (
                client.table("user_keys")
                .select("encrypted_value,nonce,key_version")
                .eq("user_id", user_id)
                .eq("key_name", key_name)
                .limit(1)
                .execute()
            )

        try:
            result = await asyncio.to_thread(_select)
            row = (result.data or [None])[0]
            if row:
                return row
        except Exception:
            pass

    return await dev_store.get_user_key(user_id, key_name)


async def _get_google_access_token(user_id: str) -> str | None:
    row = await _fetch_encrypted_key(user_id, "google_oauth_access_token")
    if row:
        return _security_manager.decrypt_text(
            EncryptedPayload(
                nonce=row["nonce"],
                ciphertext=row["encrypted_value"],
                key_version=row.get("key_version", 1),
            ),
            aad=user_id,
        )

    if settings.google_oauth_access_token:
        return settings.google_oauth_access_token
    return None


async def _google_request(
    token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    files: Any | None = None,
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=GOOGLE_API_BASE_URL, timeout=GOOGLE_TIMEOUT) as client:
        return await client.request(
            method,
            path,
            params=params,
            json=json_body,
            files=files,
            headers=headers,
        )


async def upload_to_google_drive(*, file_path: str, user_id: str) -> dict[str, Any]:
    name = Path(file_path).name
    token = await _get_google_access_token(user_id)
    if not token:
        return {
            "status": "mocked_missing_token",
            "file_name": name,
            "drive_url": f"https://drive.google.com/file/d/mock-{name}/view",
        }

    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return {"status": "error", "error": f"file not found: {file_path}"}

    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        file_bytes = path.read_bytes()
        response = await _google_request(
            token,
            "POST",
            "/upload/drive/v3/files",
            params={
                "uploadType": "multipart",
                "fields": "id,name,webViewLink,webContentLink",
            },
            files={
                "metadata": (
                    "metadata",
                    json.dumps({"name": path.name}),
                    "application/json; charset=UTF-8",
                ),
                "file": (path.name, file_bytes, mime_type),
            },
        )
        if response.status_code >= 400:
            return {
                "status": "error",
                "error": f"drive upload failed: {response.status_code} {response.text[:240]}",
            }
        data = response.json()
        return {
            "status": "live",
            "file_name": data.get("name", path.name),
            "file_id": data.get("id"),
            "drive_url": data.get("webViewLink") or data.get("webContentLink"),
        }
    except Exception as exc:
        return {"status": "error", "error": f"drive upload exception: {exc}"}


async def send_gmail_notification(
    *, user_id: str, to_email: str, subject: str, body: str
) -> dict[str, Any]:
    token = await _get_google_access_token(user_id)
    if not token:
        return {
            "status": "mocked_missing_token",
            "to": to_email,
            "subject": subject,
            "preview": body[:140],
        }

    try:
        msg = EmailMessage()
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8").rstrip("=")
        response = await _google_request(
            token,
            "POST",
            "/gmail/v1/users/me/messages/send",
            json_body={"raw": raw},
        )
        if response.status_code >= 400:
            return {
                "status": "error",
                "error": f"gmail send failed: {response.status_code} {response.text[:240]}",
            }
        payload = response.json()
        return {
            "status": "live",
            "to": to_email,
            "subject": subject,
            "message_id": payload.get("id"),
        }
    except Exception as exc:
        return {"status": "error", "error": f"gmail send exception: {exc}"}


async def create_google_calendar_event(
    *,
    user_id: str,
    summary: str,
    start_at: str,
    end_at: str,
    description: str | None = None,
    calendar_id: str = "primary",
) -> dict[str, Any]:
    token = await _get_google_access_token(user_id)
    if not token:
        return {
            "status": "mocked_missing_token",
            "summary": summary,
            "html_link": "https://calendar.google.com",
        }

    body = {
        "summary": summary,
        "description": description or "",
        "start": {"dateTime": start_at},
        "end": {"dateTime": end_at},
    }
    encoded_calendar_id = calendar_id.replace("/", "%2F")
    try:
        response = await _google_request(
            token,
            "POST",
            f"/calendar/v3/calendars/{encoded_calendar_id}/events",
            json_body=body,
        )
        if response.status_code >= 400:
            return {
                "status": "error",
                "error": f"calendar create failed: {response.status_code} {response.text[:240]}",
            }
        data = response.json()
        return {
            "status": "live",
            "event_id": data.get("id"),
            "html_link": data.get("htmlLink"),
            "summary": data.get("summary", summary),
        }
    except Exception as exc:
        return {"status": "error", "error": f"calendar create exception: {exc}"}
