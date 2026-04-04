import json
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from google.oauth2 import credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.core.config import settings
from app.core.supabase_client import get_supabase_admin
from app.core.security import EncryptedPayload

router = APIRouter(prefix="/auth/google", tags=["google"])


# NOTE: This is a simplified OAuth handler. In production you should validate state,
# use PKCE or server-side session, and store refresh tokens encrypted.

@router.get("/start")
async def google_oauth_start(request: Request):
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=["https://www.googleapis.com/auth/drive.file"],
        redirect_uri=settings.google_oauth_redirect_uri,
    )
    auth_url, _ = flow.authorization_url(access_type="offline", include_granted_scopes="true")
    return RedirectResponse(auth_url)


@router.get("/callback")
async def google_oauth_callback(request: Request):
    code = request.query_params.get("code")
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=["https://www.googleapis.com/auth/drive.file"],
        redirect_uri=settings.google_oauth_redirect_uri,
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    # In production: encrypt and store refresh token in user_keys table
    # For now return a simple JSON with token info
    return {"access_token": creds.token, "refresh_token": creds.refresh_token}


async def upload_file_to_drive(creds: credentials.Credentials, file_path: str, mime_type: str = "application/vnd.openxmlformats-officedocument.presentationml.presentation") -> dict:
    service = build("drive", "v3", credentials=creds)
    file_metadata = {"name": file_path.split("/")[-1]}
    media = None
    try:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(file_path, mimetype=mime_type)
    except Exception:
        media = None
    file = service.files().create(body=file_metadata, media_body=media, fields="id,webViewLink").execute()
    return file
