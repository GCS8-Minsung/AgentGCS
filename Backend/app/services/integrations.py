from __future__ import annotations

from pathlib import Path


async def upload_to_google_drive(file_path: str) -> dict[str, str]:
    """
    Mock uploader.
    Replace with Google Drive API upload in production.
    """
    name = Path(file_path).name
    return {
        "status": "mocked",
        "file_name": name,
        "drive_url": f"https://drive.google.com/file/d/mock-{name}/view",
    }


async def send_gmail_notification(to_email: str, subject: str, body: str) -> dict[str, str]:
    """
    Mock Gmail sender.
    Replace with Gmail API call in production.
    """
    return {
        "status": "mocked",
        "to": to_email,
        "subject": subject,
        "preview": body[:140],
    }

