from __future__ import annotations

import asyncio
import base64
import io
import json
import mimetypes
from datetime import datetime, timedelta, timezone
from email.header import Header
from email import policy
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx
try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None

from app.core.config import settings
from app.core.security import EncryptedPayload
from app.core.security import SecurityManager
from app.core.supabase_client import get_supabase_admin
from app.services.dev_store import dev_store

GOOGLE_API_BASE_URL = "https://www.googleapis.com"
GOOGLE_TIMEOUT = 20.0
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
GOOGLE_USERINFO_URL = "/oauth2/v2/userinfo"
DEFAULT_GOOGLE_CLIENT_ID = "513803184584-7sb5sp4qv68a534kvd0u3inp0ruf021r.apps.googleusercontent.com"
DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
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


def _decrypt_row(row: dict[str, Any] | None, user_id: str) -> str | None:
    if not row:
        return None
    encrypted = row.get("encrypted_value")
    nonce = row.get("nonce")
    if not encrypted or not nonce:
        return None
    try:
        return _security_manager.decrypt_text(
            EncryptedPayload(
                nonce=nonce,
                ciphertext=encrypted,
                key_version=row.get("key_version", 1),
            ),
            aad=user_id,
        )
    except Exception:
        return None


async def _store_encrypted_key(user_id: str, key_name: str, plaintext: str) -> None:
    encrypted = _security_manager.encrypt_text(plaintext, aad=user_id)
    payload = {
        "user_id": user_id,
        "key_name": key_name,
        "encrypted_value": encrypted.ciphertext,
        "nonce": encrypted.nonce,
        "key_version": encrypted.key_version,
    }

    if _is_supabase_enabled():
        def _upsert():
            client = get_supabase_admin()
            return client.table("user_keys").upsert(payload, on_conflict="user_id,key_name").execute()

        try:
            await asyncio.to_thread(_upsert)
        except Exception:
            pass

    await dev_store.upsert_user_key(
        user_id=user_id,
        key_name=key_name,
        encrypted_value=encrypted.ciphertext,
        nonce=encrypted.nonce,
        key_version=encrypted.key_version,
    )


def _parse_google_meta(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _parse_expires_at(meta: dict[str, Any]) -> datetime | None:
    expires_at_raw = meta.get("expires_at")
    if isinstance(expires_at_raw, str) and expires_at_raw.strip():
        try:
            return datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
        except Exception:
            pass
    issued_at_raw = meta.get("issued_at")
    expires_in = meta.get("expires_in")
    if isinstance(issued_at_raw, str) and isinstance(expires_in, (int, float)):
        try:
            issued = datetime.fromisoformat(issued_at_raw.replace("Z", "+00:00"))
            return issued + timedelta(seconds=int(expires_in))
        except Exception:
            return None
    return None


def _token_expired(meta: dict[str, Any], *, skew_seconds: int = 45) -> bool:
    expires_at = _parse_expires_at(meta)
    if not expires_at:
        return False
    now = datetime.now(timezone.utc)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= (now + timedelta(seconds=skew_seconds))


async def _load_google_bundle(user_id: str) -> tuple[str | None, str | None, dict[str, Any]]:
    access_token = _decrypt_row(
        await _fetch_encrypted_key(user_id, "google_oauth_access_token"),
        user_id,
    )
    refresh_token = _decrypt_row(
        await _fetch_encrypted_key(user_id, "google_oauth_refresh_token"),
        user_id,
    )
    meta_raw = _decrypt_row(
        await _fetch_encrypted_key(user_id, "google_oauth_token_meta"),
        user_id,
    )
    meta = _parse_google_meta(meta_raw)

    if not access_token and settings.google_oauth_access_token:
        access_token = settings.google_oauth_access_token

    return access_token, refresh_token, meta


async def _get_google_oauth_client_credentials(user_id: str) -> tuple[str | None, str | None]:
    key_client_id = _decrypt_row(
        await _fetch_encrypted_key(user_id, "google_client_id"),
        user_id,
    )
    key_client_secret = _decrypt_row(
        await _fetch_encrypted_key(user_id, "google_client_secret"),
        user_id,
    )
    client_id = (key_client_id or settings.google_client_id or DEFAULT_GOOGLE_CLIENT_ID or "").strip()
    client_secret = (key_client_secret or settings.google_client_secret or "").strip()
    return (client_id or None), (client_secret or None)


async def _refresh_google_access_token(user_id: str, refresh_token: str) -> tuple[str | None, str | None]:
    client_id, client_secret = await _get_google_oauth_client_credentials(user_id)
    if not client_id or not client_secret:
        return None, "google_client_credentials_missing"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    try:
        async with httpx.AsyncClient(timeout=GOOGLE_TIMEOUT) as client:
            response = await client.post(GOOGLE_OAUTH_TOKEN_URL, data=payload)
        if response.status_code >= 400:
            return None, f"refresh_failed:{response.status_code}:{response.text[:180]}"
        data = response.json()
        next_access_token = str(data.get("access_token") or "").strip()
        if not next_access_token:
            return None, "refresh_failed:empty_access_token"
        expires_in = int(data.get("expires_in") or 3600)
        scope = str(data.get("scope") or "")
        meta = {
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "expires_in": expires_in,
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat(),
            "scope": scope,
            "token_type": data.get("token_type"),
            "source": "refresh",
        }
        await _store_encrypted_key(user_id, "google_oauth_access_token", next_access_token)
        await _store_encrypted_key(user_id, "google_oauth_token_meta", json.dumps(meta, ensure_ascii=False))
        return next_access_token, None
    except Exception as exc:
        return None, f"refresh_exception:{str(exc)[:180]}"


async def _get_valid_google_access_token(user_id: str) -> tuple[str | None, str | None]:
    access_token, refresh_token, meta = await _load_google_bundle(user_id)
    if access_token and not _token_expired(meta):
        return access_token, None
    if refresh_token:
        refreshed, error = await _refresh_google_access_token(user_id, refresh_token)
        if refreshed:
            return refreshed, None
        return None, error
    if access_token and _token_expired(meta):
        return None, "access_token_expired_reauth_required"
    if access_token:
        # 메타 정보가 없으면 일단 시도 가능하게 허용.
        return access_token, None
    return None, "google_token_missing"


async def _get_google_token_scopes(token: str) -> set[str]:
    try:
        async with httpx.AsyncClient(timeout=GOOGLE_TIMEOUT) as client:
            response = await client.get(GOOGLE_TOKENINFO_URL, params={"access_token": token})
        if response.status_code >= 400:
            return set()
        data = response.json() if response.text else {}
        raw_scope = str(data.get("scope") or "")
        return {scope.strip() for scope in raw_scope.split(" ") if scope.strip()}
    except Exception:
        return set()


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


def _drive_folder_url(folder_id: str | None) -> str | None:
    if not folder_id:
        return None
    return f"https://drive.google.com/drive/folders/{folder_id}"


def _escape_drive_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


async def get_connected_google_oauth_identity(user_id: str) -> dict[str, Any]:
    token, token_error = await _get_valid_google_access_token(user_id)
    if not token:
        return {
            "status": "not_configured",
            "error": token_error or "google_oauth_access_token missing",
        }

    try:
        response = await _google_request(token, "GET", GOOGLE_USERINFO_URL)
        if response.status_code >= 400:
            return {
                "status": "error",
                "error": f"userinfo failed: {response.status_code} {response.text[:180]}",
            }
        payload = response.json() if response.text else {}
        return {
            "status": "live",
            "email": str(payload.get("email") or "").strip() or None,
            "name": str(payload.get("name") or "").strip() or None,
            "sub": str(payload.get("id") or payload.get("sub") or "").strip() or None,
            "verified_email": bool(payload.get("verified_email")),
        }
    except Exception as exc:
        return {"status": "error", "error": f"userinfo exception: {str(exc)[:200]}"}


async def _drive_find_folder(
    *,
    token: str,
    name: str,
    parent_id: str | None,
) -> dict[str, Any] | None:
    parent_condition = f"'{parent_id}' in parents" if parent_id else "'root' in parents"
    query = (
        f"mimeType='{DRIVE_FOLDER_MIME}' and trashed=false and "
        f"name='{_escape_drive_query_value(name)}' and {parent_condition}"
    )
    response = await _google_request(
        token,
        "GET",
        "/drive/v3/files",
        params={
            "q": query,
            "fields": "files(id,name,mimeType,webViewLink)",
            "pageSize": 5,
            "orderBy": "createdTime desc",
        },
    )
    if response.status_code >= 400:
        return None
    payload = response.json() if response.text else {}
    rows = payload.get("files")
    if isinstance(rows, list) and rows:
        row = rows[0]
        return row if isinstance(row, dict) else None
    return None


async def _drive_create_folder(
    *,
    token: str,
    name: str,
    parent_id: str | None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "name": name,
        "mimeType": DRIVE_FOLDER_MIME,
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    response = await _google_request(
        token,
        "POST",
        "/drive/v3/files",
        params={"fields": "id,name,mimeType,webViewLink"},
        json_body=metadata,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"drive folder create failed: {response.status_code} {response.text[:200]}")
    payload = response.json() if response.text else {}
    if not isinstance(payload, dict) or not payload.get("id"):
        raise RuntimeError("drive folder create failed: empty id")
    return payload


async def _drive_get_folder_by_id(token: str, folder_id: str) -> dict[str, Any] | None:
    response = await _google_request(
        token,
        "GET",
        f"/drive/v3/files/{folder_id}",
        params={"fields": "id,name,mimeType,trashed,webViewLink"},
    )
    if response.status_code >= 400:
        return None
    payload = response.json() if response.text else {}
    return payload if isinstance(payload, dict) else None


async def _drive_list_files_in_folder(
    *,
    token: str,
    folder_id: str,
    page_size: int = 20,
) -> list[dict[str, Any]]:
    query = (
        f"trashed=false and mimeType!='{DRIVE_FOLDER_MIME}' and "
        f"'{_escape_drive_query_value(folder_id)}' in parents"
    )
    response = await _google_request(
        token,
        "GET",
        "/drive/v3/files",
        params={
            "q": query,
            "fields": "files(id,name,mimeType,modifiedTime,size,webViewLink,webContentLink)",
            "pageSize": max(1, min(page_size, 100)),
            "orderBy": "modifiedTime desc",
        },
    )
    if response.status_code >= 400:
        return []
    payload = response.json() if response.text else {}
    rows = payload.get("files")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


async def _drive_read_text_content(
    *,
    token: str,
    file_id: str,
    mime_type: str,
) -> tuple[str, str | None]:
    def _extract_pdf_text(payload: bytes) -> str:
        if not payload:
            return ""
        if PdfReader is None:
            return ""
        try:
            reader = PdfReader(io.BytesIO(payload))
            chunks: list[str] = []
            max_pages = min(len(reader.pages), 40)
            for idx in range(max_pages):
                page = reader.pages[idx]
                text = (page.extract_text() or "").strip()
                if text:
                    chunks.append(text)
                if sum(len(item) for item in chunks) >= 50000:
                    break
            joined = "\n".join(chunks).strip()
            return joined[:50000] if len(joined) > 50000 else joined
        except Exception:
            return ""

    mt = (mime_type or "").strip()
    export_mime: str | None = None
    is_pdf = mt in {"application/pdf", "application/x-pdf"}
    textual_media_types = (
        mt.startswith("text/")
        or mt in {"application/json", "application/xml", "application/csv", "application/x-yaml", "application/yaml"}
    )
    if mt == "application/vnd.google-apps.document":
        export_mime = "text/plain"
    elif mt == "application/vnd.google-apps.spreadsheet":
        export_mime = "text/csv"
    elif mt == "application/vnd.google-apps.presentation":
        export_mime = "text/plain"

    try:
        if export_mime:
            response = await _google_request(
                token,
                "GET",
                f"/drive/v3/files/{file_id}/export",
                params={"mimeType": export_mime},
            )
        else:
            if not textual_media_types and not is_pdf:
                return "", f"unsupported_mime:{mt or 'unknown'}"
            response = await _google_request(
                token,
                "GET",
                f"/drive/v3/files/{file_id}",
                params={"alt": "media"},
            )
        if response.status_code >= 400:
            return "", f"read_failed:{response.status_code}"
        if not response.content:
            return "", "empty_content"
        if is_pdf:
            extracted_pdf = _extract_pdf_text(response.content)
            if extracted_pdf:
                return extracted_pdf, None
            return "", "pdf_text_not_found"
        raw = response.text
        normalized = str(raw or "").strip()
        if normalized:
            return normalized, None
        return "", "empty_text"
    except Exception:
        return "", "read_exception"


def _compact_text_for_summary(text: str, *, max_len: int = 1200) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_len:
        return compact
    return compact[: max(0, max_len - 3)] + "..."


async def collect_google_drive_input_summary(
    *,
    user_id: str,
    max_files: int = 8,
    max_chars_per_file: int = 1200,
) -> dict[str, Any]:
    token, token_error = await _get_valid_google_access_token(user_id)
    if not token:
        return {
            "status": "not_configured",
            "folder_id": None,
            "folder_name": None,
            "folder_url": None,
            "files": [],
            "summary_markdown": "",
            "error": token_error or "google_oauth_access_token missing",
        }

    stored_input_id = _decrypt_row(
        await _fetch_encrypted_key(user_id, "google_drive_input_root_folder_id"),
        user_id,
    ) or _decrypt_row(
        await _fetch_encrypted_key(user_id, "google_drive_input_folder_id"),
        user_id,
    )
    if not stored_input_id:
        stored_input_id = (settings.google_drive_input_root_folder_id or "").strip() or None
    folder: dict[str, Any] | None = None
    if stored_input_id:
        existing = await _drive_get_folder_by_id(token, stored_input_id)
        if existing and existing.get("mimeType") == DRIVE_FOLDER_MIME and not bool(existing.get("trashed")):
            folder = existing

    input_names = [
        (settings.google_drive_input_root_name or "input").strip() or "input",
        "input",
        "Input",
        "AgentGCS-input",
    ]
    if folder is None:
        seen: set[str] = set()
        for name in input_names:
            lowered = name.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            found = await _drive_find_folder(token=token, name=name, parent_id=None)
            if found:
                folder = found
                break

    if folder is None:
        return {
            "status": "missing_folder",
            "folder_id": None,
            "folder_name": input_names[0],
            "folder_url": None,
            "files": [],
            "summary_markdown": "",
            "error": "google_drive_input_folder_not_found",
        }

    folder_id = str(folder.get("id") or "").strip()
    folder_name = str(folder.get("name") or "").strip() or input_names[0]
    if folder_id:
        await _store_encrypted_key(user_id, "google_drive_input_root_folder_id", folder_id)
        await _store_encrypted_key(user_id, "google_drive_input_folder_id", folder_id)

    file_rows = await _drive_list_files_in_folder(token=token, folder_id=folder_id, page_size=max_files * 2)
    if not file_rows:
        return {
            "status": "empty",
            "folder_id": folder_id or None,
            "folder_name": folder_name,
            "folder_url": _drive_folder_url(folder_id or None),
            "files": [],
            "summary_markdown": "",
            "error": "google_drive_input_folder_empty",
        }

    summarized_files: list[dict[str, Any]] = []
    for row in file_rows[:max_files]:
        file_id = str(row.get("id") or "").strip()
        file_name = str(row.get("name") or "").strip() or "untitled"
        mime_type = str(row.get("mimeType") or "").strip()
        modified_time = str(row.get("modifiedTime") or "").strip() or None
        view_link = str(row.get("webViewLink") or row.get("webContentLink") or "").strip() or None
        extracted, extraction_error = await _drive_read_text_content(
            token=token,
            file_id=file_id,
            mime_type=mime_type,
        )
        snippet = _compact_text_for_summary(extracted, max_len=max_chars_per_file)
        summarized_files.append(
            {
                "file_id": file_id or None,
                "name": file_name,
                "mime_type": mime_type,
                "modified_time": modified_time,
                "drive_url": view_link,
                "snippet": snippet,
                "has_text": bool(snippet),
                "extraction_error": extraction_error,
            }
        )

    lines: list[str] = [
        "# Google Drive Input Folder Summary",
        "",
        f"- Folder: {folder_name}",
        f"- Folder URL: {_drive_folder_url(folder_id or None) or '-'}",
        f"- File Count: {len(summarized_files)}",
        "",
        "## File Summaries",
    ]
    for idx, item in enumerate(summarized_files, start=1):
        lines.append(f"- [D{idx}] {item['name']}")
        lines.append(f"  - mime: {item['mime_type'] or '-'}")
        if item.get("modified_time"):
            lines.append(f"  - modified: {item['modified_time']}")
        if item.get("drive_url"):
            lines.append(f"  - url: {item['drive_url']}")
        snippet = str(item.get("snippet") or "").strip()
        if snippet:
            lines.append(f"  - summary: {snippet}")
        else:
            extraction_error = str(item.get("extraction_error") or "").strip()
            if extraction_error:
                lines.append(f"  - summary: (텍스트 추출 불가: {extraction_error})")
            else:
                lines.append("  - summary: (텍스트 추출 불가)")
    lines.append("")

    return {
        "status": "live",
        "folder_id": folder_id or None,
        "folder_name": folder_name,
        "folder_url": _drive_folder_url(folder_id or None),
        "files": summarized_files,
        "summary_markdown": "\n".join(lines),
        "error": None,
    }


async def _drive_ensure_output_root_folder(*, token: str, user_id: str) -> dict[str, Any]:
    root_name = (settings.google_drive_output_root_name or "AgentGCS-output").strip() or "AgentGCS-output"
    stored_root_id = _decrypt_row(
        await _fetch_encrypted_key(user_id, "google_drive_output_root_folder_id"),
        user_id,
    ) or _decrypt_row(
        await _fetch_encrypted_key(user_id, "google_drive_output_folder_id"),
        user_id,
    )
    if not stored_root_id:
        stored_root_id = (settings.google_drive_output_root_folder_id or "").strip() or None
    if stored_root_id:
        existing = await _drive_get_folder_by_id(token, stored_root_id)
        if (
            existing
            and existing.get("mimeType") == DRIVE_FOLDER_MIME
            and not bool(existing.get("trashed"))
        ):
            return existing

    found = await _drive_find_folder(token=token, name=root_name, parent_id=None)
    if found:
        folder_id = str(found.get("id") or "")
        if folder_id:
            await _store_encrypted_key(user_id, "google_drive_output_root_folder_id", folder_id)
            await _store_encrypted_key(user_id, "google_drive_output_folder_id", folder_id)
        return found

    created = await _drive_create_folder(token=token, name=root_name, parent_id=None)
    folder_id = str(created.get("id") or "")
    if folder_id:
        await _store_encrypted_key(user_id, "google_drive_output_root_folder_id", folder_id)
        await _store_encrypted_key(user_id, "google_drive_output_folder_id", folder_id)
    return created


async def _drive_ensure_run_folder(
    *,
    token: str,
    output_root_folder_id: str,
    run_id: str,
) -> dict[str, Any]:
    folder_name = run_id.strip()[:120] or "run"
    found = await _drive_find_folder(token=token, name=folder_name, parent_id=output_root_folder_id)
    if found:
        return found
    return await _drive_create_folder(token=token, name=folder_name, parent_id=output_root_folder_id)


async def _drive_upload_file(
    *,
    token: str,
    path: Path,
    parent_id: str | None,
) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    metadata: dict[str, Any] = {"name": path.name}
    if parent_id:
        metadata["parents"] = [parent_id]

    file_bytes = path.read_bytes()
    response = await _google_request(
        token,
        "POST",
        "/upload/drive/v3/files",
        params={"uploadType": "multipart", "fields": "id,name,webViewLink,webContentLink,mimeType"},
        files={
            "metadata": (
                "metadata",
                json.dumps(metadata, ensure_ascii=False),
                "application/json; charset=UTF-8",
            ),
            "file": (path.name, file_bytes, mime_type),
        },
    )
    if response.status_code >= 400:
        return {
            "status": "error",
            "file_name": path.name,
            "local_path": str(path),
            "error": f"drive upload failed: {response.status_code} {response.text[:240]}",
        }

    payload = response.json() if response.text else {}
    return {
        "status": "live",
        "file_name": payload.get("name", path.name) if isinstance(payload, dict) else path.name,
        "local_path": str(path),
        "file_id": payload.get("id") if isinstance(payload, dict) else None,
        "mime_type": payload.get("mimeType", mime_type) if isinstance(payload, dict) else mime_type,
        "drive_url": (
            payload.get("webViewLink") or payload.get("webContentLink")
            if isinstance(payload, dict)
            else None
        ),
    }


async def upload_artifacts_to_google_drive(
    *,
    user_id: str,
    run_id: str,
    file_paths: list[str],
) -> dict[str, Any]:
    token, token_error = await _get_valid_google_access_token(user_id)
    if not token:
        return {
            "status": "not_configured",
            "run_id": run_id,
            "output_root_folder_id": None,
            "run_folder_id": None,
            "folder_url": None,
            "files": [],
            "error": token_error or "google_oauth_access_token missing",
        }

    unique_paths: list[Path] = []
    seen: set[str] = set()
    for raw_path in file_paths:
        normalized = str(raw_path or "").strip()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        path = Path(normalized)
        if path.exists() and path.is_file():
            unique_paths.append(path.resolve())

    if not unique_paths:
        return {
            "status": "error",
            "run_id": run_id,
            "output_root_folder_id": None,
            "run_folder_id": None,
            "folder_url": None,
            "files": [],
            "error": "no_uploadable_files",
        }

    try:
        output_root = await _drive_ensure_output_root_folder(token=token, user_id=user_id)
        output_root_id = str(output_root.get("id") or "")
        run_folder = await _drive_ensure_run_folder(
            token=token,
            output_root_folder_id=output_root_id,
            run_id=run_id,
        )
        run_folder_id = str(run_folder.get("id") or "")

        files: list[dict[str, Any]] = []
        for path in unique_paths:
            item = await _drive_upload_file(token=token, path=path, parent_id=run_folder_id)
            files.append(item)

        has_live_file = any(item.get("status") == "live" for item in files)
        return {
            "status": "live" if has_live_file else "error",
            "run_id": run_id,
            "output_root_folder_id": output_root_id or None,
            "run_folder_id": run_folder_id or None,
            "folder_url": _drive_folder_url(run_folder_id or None),
            "files": files,
            "error": None if has_live_file else "all_uploads_failed",
        }
    except Exception as exc:
        return {
            "status": "error",
            "run_id": run_id,
            "output_root_folder_id": None,
            "run_folder_id": None,
            "folder_url": None,
            "files": [],
            "error": f"drive upload exception: {str(exc)[:240]}",
        }


async def upload_to_google_drive(*, file_path: str, user_id: str) -> dict[str, Any]:
    name = Path(file_path).name
    token, token_error = await _get_valid_google_access_token(user_id)
    if not token:
        return {
            "status": "not_configured",
            "file_name": name,
            "error": token_error or "google_oauth_access_token missing",
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
    token, token_error = await _get_valid_google_access_token(user_id)
    if not token:
        return {
            "status": "not_configured",
            "to": to_email,
            "subject": subject,
            "error": token_error or "google_oauth_access_token missing",
        }

    try:
        # Use SMTP policy + RFC2047 encoded subject for broad client compatibility.
        msg = EmailMessage(policy=policy.SMTP)
        msg["To"] = to_email
        msg["Subject"] = Header(subject or "", "utf-8").encode()
        msg.set_content(body or "", subtype="plain", charset="utf-8", cte="base64")
        raw = base64.urlsafe_b64encode(msg.as_bytes(policy=policy.SMTP)).decode("ascii")
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
    token, token_error = await _get_valid_google_access_token(user_id)
    if not token:
        return {
            "status": "not_configured",
            "summary": summary,
            "error": token_error or "google_oauth_access_token missing",
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


async def diagnose_google_workspace(user_id: str) -> dict[str, Any]:
    client_id, client_secret = await _get_google_oauth_client_credentials(user_id)
    oauth_configured = bool(client_id and client_secret)
    access_token, refresh_token, meta = await _load_google_bundle(user_id)
    token_saved = bool(access_token or refresh_token)
    if not token_saved:
        return {
            "token_saved": False,
            "reachable": False,
            "status": "not_configured",
            "reason": "google_oauth_access_token/google_oauth_refresh_token not found",
            "oauth_configured": oauth_configured,
            "connected_account": None,
            "services": {
                "drive": {"status": "not_configured"},
                "gmail": {"status": "not_configured"},
                "calendar": {"status": "not_configured"},
            },
            "refresh_available": False,
        }

    valid_token, token_error = await _get_valid_google_access_token(user_id)
    if not valid_token:
        return {
            "token_saved": True,
            "reachable": False,
            "status": "auth_invalid",
            "reason": token_error or "invalid_google_token",
            "oauth_configured": oauth_configured,
            "connected_account": None,
            "services": {
                "drive": {"status": "auth_invalid"},
                "gmail": {"status": "auth_invalid"},
                "calendar": {"status": "auth_invalid"},
            },
            "refresh_available": bool(refresh_token),
            "token_expired": _token_expired(meta),
        }

    probes = [
        ("drive", "GET", "/drive/v3/about", {"fields": "user(emailAddress,displayName)"}),
        ("calendar", "GET", "/calendar/v3/users/me/calendarList", {"maxResults": 1}),
    ]
    services: dict[str, dict[str, Any]] = {}
    ok_count = 0
    for name, method, path, params in probes:
        try:
            response = await _google_request(valid_token, method, path, params=params)
            if response.status_code < 400:
                services[name] = {"status": "ok", "http_status": response.status_code}
                ok_count += 1
                continue
            body = response.text[:220]
            status = "auth_invalid" if response.status_code in {401, 403} else "error"
            services[name] = {
                "status": status,
                "http_status": response.status_code,
                "reason": body,
            }
        except Exception as exc:
            services[name] = {"status": "error", "reason": str(exc)[:220]}

    scopes = await _get_google_token_scopes(valid_token)
    gmail_send_scope = "https://www.googleapis.com/auth/gmail.send"
    if gmail_send_scope in scopes:
        services["gmail"] = {"status": "ok", "scope": gmail_send_scope}
        ok_count += 1
    else:
        services["gmail"] = {
            "status": "auth_invalid",
            "reason": "gmail.send scope missing",
            "required_scope": gmail_send_scope,
        }

    total_checks = len(probes) + 1
    if ok_count == total_checks:
        overall_status = "ok"
    elif ok_count > 0:
        overall_status = "partial"
    else:
        overall_status = "error"

    connected_account: dict[str, Any] | None = None
    try:
        userinfo_response = await _google_request(valid_token, "GET", GOOGLE_USERINFO_URL)
        if userinfo_response.status_code < 400:
            userinfo_payload = userinfo_response.json() if userinfo_response.text else {}
            if isinstance(userinfo_payload, dict):
                connected_account = {
                    "email": str(userinfo_payload.get("email") or "").strip() or None,
                    "name": str(userinfo_payload.get("name") or "").strip() or None,
                    "sub": str(userinfo_payload.get("id") or userinfo_payload.get("sub") or "").strip() or None,
                    "verified_email": bool(userinfo_payload.get("verified_email")),
                }
    except Exception:
        connected_account = None

    return {
        "token_saved": True,
        "reachable": ok_count > 0,
        "status": overall_status,
        "reason": None if ok_count > 0 else "all_google_service_probes_failed",
        "oauth_configured": oauth_configured,
        "connected_account": connected_account,
        "services": services,
        "refresh_available": bool(refresh_token),
        "token_expired": _token_expired(meta),
    }
