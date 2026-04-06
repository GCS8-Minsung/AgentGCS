from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import UUID

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import settings
from app.core.security import EncryptedPayload
from app.dependencies import security_manager
from app.core.supabase_client import get_supabase_admin
from app.services.dev_store import dev_store

router = APIRouter(prefix="/google/oauth", tags=["google_oauth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_CLIENT_ID = "513803184584-7sb5sp4qv68a534kvd0u3inp0ruf021r.apps.googleusercontent.com"
GOOGLE_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.file",
]


def _state_secret() -> bytes:
    base = (
        settings.encryption_master_key
        or settings.supabase_service_role_key
        or settings.google_client_secret
        or "agentgcs-dev-state-secret"
    )
    return base.encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("utf-8"))


def _build_state(user_id: str, return_to: str) -> str:
    payload = {
        "user_id": user_id,
        "return_to": return_to,
        "ts": int(time.time()),
    }
    payload_raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64url_encode(payload_raw)
    sig = hmac.new(_state_secret(), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def _parse_state(state_token: str) -> dict[str, Any]:
    try:
        payload_b64, signature = state_token.split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid_state_format") from exc
    expected_sig = hmac.new(_state_secret(), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_sig):
        raise ValueError("invalid_state_signature")
    payload = json.loads(_b64url_decode(payload_b64))
    if not isinstance(payload, dict):
        raise ValueError("invalid_state_payload")
    ts = int(payload.get("ts") or 0)
    if ts <= 0 or (time.time() - ts) > 900:
        raise ValueError("state_expired")
    return payload


def _allowed_origins() -> set[str]:
    origins = {origin.strip() for origin in settings.cors_origins if origin.strip()}
    origins.add("http://localhost:3000")
    origins.add("http://127.0.0.1:3000")
    return origins


def _sanitize_return_to(return_to: str | None) -> str:
    default_url = "http://localhost:3000"
    if not return_to:
        return default_url
    try:
        parsed = urlparse(return_to)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return default_url
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in _allowed_origins():
            return default_url
        return return_to
    except Exception:
        return default_url


def _append_query(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    current = dict(parse_qsl(parsed.query, keep_blank_values=True))
    current.update(params)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(current),
            parsed.fragment,
        )
    )


def _is_private_host(host: str) -> bool:
    candidate = (host or "").strip().lower()
    if not candidate:
        return False
    if candidate in {"localhost", "127.0.0.1"}:
        return False
    try:
        ip = ipaddress.ip_address(candidate)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def _resolve_google_redirect_uri(request: Request) -> str:
    configured = (settings.google_oauth_redirect_uri or "").strip()
    if configured:
        return configured

    generated = str(request.url_for("google_oauth_callback"))
    try:
        parsed = urlparse(generated)
        host = parsed.hostname or ""
        port = parsed.port
        if _is_private_host(host):
            fallback_netloc = f"localhost:{port}" if port else "localhost"
            return urlunparse(
                (
                    parsed.scheme or "http",
                    fallback_netloc,
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment,
                )
            )
        return generated
    except Exception:
        return generated


def _popup_html(*, status: str, message: str, return_to: str) -> str:
    target_origin = f"{urlparse(return_to).scheme}://{urlparse(return_to).netloc}"
    redirect_url = _append_query(return_to, {"google_oauth": status, "google_oauth_message": message[:200]})
    payload = {"type": "agentgcs_google_oauth_result", "status": status, "message": message}
    payload_json = json.dumps(payload, ensure_ascii=False)
    return f"""
<!doctype html>
<html>
  <head><meta charset="utf-8"><title>Google OAuth</title></head>
  <body>
    <p>{message}</p>
    <script>
      (function() {{
        const payload = {payload_json};
        const targetOrigin = {json.dumps(target_origin)};
        const redirectUrl = {json.dumps(redirect_url)};
        try {{
          if (window.opener && !window.opener.closed) {{
            window.opener.postMessage(payload, targetOrigin);
            window.close();
            return;
          }}
        }} catch (e) {{}}
        window.location.replace(redirectUrl);
      }})();
    </script>
  </body>
</html>
"""


async def _store_encrypted_key(user_id: str, key_name: str, plaintext: str) -> None:
    encrypted = security_manager.encrypt_text(plaintext, aad=user_id)
    payload = {
        "user_id": user_id,
        "key_name": key_name,
        "encrypted_value": encrypted.ciphertext,
        "nonce": encrypted.nonce,
        "key_version": encrypted.key_version,
    }
    if settings.supabase_url and settings.supabase_service_role_key:
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


async def _fetch_encrypted_key(user_id: str, key_name: str) -> dict[str, Any] | None:
    if settings.supabase_url and settings.supabase_service_role_key:
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


def _decrypt_row(user_id: str, row: dict[str, Any] | None) -> str | None:
    if not row:
        return None
    encrypted = row.get("encrypted_value")
    nonce = row.get("nonce")
    if not encrypted or not nonce:
        return None
    try:
        return security_manager.decrypt_text(
            EncryptedPayload(
                nonce=nonce,
                ciphertext=encrypted,
                key_version=row.get("key_version", 1),
            ),
            aad=user_id,
        )
    except Exception:
        return None


async def _get_google_client_credentials(user_id: str) -> tuple[str, str | None]:
    key_client_id = _decrypt_row(user_id, await _fetch_encrypted_key(user_id, "google_client_id"))
    key_client_secret = _decrypt_row(
        user_id, await _fetch_encrypted_key(user_id, "google_client_secret")
    )
    client_id = (key_client_id or settings.google_client_id or DEFAULT_CLIENT_ID).strip()
    client_secret = (key_client_secret or settings.google_client_secret or "").strip() or None
    return client_id, client_secret


def _validate_user_id(user_id: str) -> str:
    try:
        UUID(user_id)
        return user_id
    except Exception as exc:
        raise ValueError("invalid_user_id") from exc


@router.get("/start")
async def google_oauth_start(
    request: Request,
    user_id: str = Query(..., min_length=3, max_length=120),
    return_to: str | None = Query(default=None),
):
    try:
        user_id = _validate_user_id(user_id)
    except ValueError:
        return HTMLResponse("Invalid user_id", status_code=400)

    client_id, _ = await _get_google_client_credentials(user_id)
    if not client_id:
        return HTMLResponse("Google OAuth client_id is not configured.", status_code=500)

    safe_return_to = _sanitize_return_to(return_to)
    state = _build_state(user_id=user_id, return_to=safe_return_to)
    redirect_uri = _resolve_google_redirect_uri(request)
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
    )
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{query}")


@router.get("/callback", name="google_oauth_callback")
async def google_oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    fallback_return = _sanitize_return_to(None)
    if not state:
        return HTMLResponse(_popup_html(status="error", message="OAuth state missing", return_to=fallback_return), status_code=400)

    try:
        state_payload = _parse_state(state)
        user_id = _validate_user_id(str(state_payload.get("user_id") or ""))
        return_to = _sanitize_return_to(str(state_payload.get("return_to") or ""))
    except Exception as exc:
        return HTMLResponse(
            _popup_html(status="error", message=f"OAuth state validation failed: {str(exc)}", return_to=fallback_return),
            status_code=400,
        )

    if error:
        return HTMLResponse(
            _popup_html(status="error", message=f"Google OAuth error: {error}", return_to=return_to),
            status_code=400,
        )
    if not code:
        return HTMLResponse(
            _popup_html(status="error", message="Google OAuth code is missing.", return_to=return_to),
            status_code=400,
        )

    client_id, client_secret = await _get_google_client_credentials(user_id)
    if not client_secret:
        return HTMLResponse(
            _popup_html(
                status="error",
                message="google_client_secret not configured on backend/user keys.",
                return_to=return_to,
            ),
            status_code=500,
        )

    redirect_uri = _resolve_google_redirect_uri(request)
    token_payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            token_response = await client.post(GOOGLE_TOKEN_URL, data=token_payload)
    except Exception as exc:
        return HTMLResponse(
            _popup_html(status="error", message=f"Token exchange failed: {str(exc)[:180]}", return_to=return_to),
            status_code=500,
        )

    if token_response.status_code >= 400:
        return HTMLResponse(
            _popup_html(
                status="error",
                message=f"Token exchange failed: {token_response.status_code} {token_response.text[:180]}",
                return_to=return_to,
            ),
            status_code=400,
        )

    token_data = token_response.json() if token_response.text else {}
    access_token = str(token_data.get("access_token") or "").strip()
    refresh_token = str(token_data.get("refresh_token") or "").strip()
    expires_in = int(token_data.get("expires_in") or 3600)
    scope = str(token_data.get("scope") or "")
    if not access_token:
        return HTMLResponse(
            _popup_html(status="error", message="Google OAuth access_token missing.", return_to=return_to),
            status_code=400,
        )

    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=expires_in)
    token_meta = {
        "issued_at": issued_at.isoformat(),
        "expires_in": expires_in,
        "expires_at": expires_at.isoformat(),
        "scope": scope,
        "scopes": [item for item in scope.split(" ") if item],
        "token_type": token_data.get("token_type"),
        "source": "authorization_code",
        "refresh_token_present": bool(refresh_token),
    }

    await _store_encrypted_key(user_id, "google_oauth_access_token", access_token)
    if refresh_token:
        await _store_encrypted_key(user_id, "google_oauth_refresh_token", refresh_token)
    await _store_encrypted_key(user_id, "google_oauth_token_meta", json.dumps(token_meta, ensure_ascii=False))

    detail = "Google Workspace OAuth 연결 완료"
    if not refresh_token:
        detail += " (refresh token 미수신)"
    return HTMLResponse(_popup_html(status="success", message=detail, return_to=return_to))
