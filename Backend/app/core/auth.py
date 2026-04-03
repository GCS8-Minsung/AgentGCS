from typing import Annotated
from uuid import UUID

from fastapi import Header, HTTPException, status


async def get_current_user_id(
    x_user_id: Annotated[str | None, Header(alias="x-user-id")] = None,
) -> str:
    """
    Demo auth dependency.
    In production, replace this with Supabase JWT verification middleware.
    """
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing x-user-id header.",
        )
    try:
        UUID(x_user_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid x-user-id. UUID format is required.",
        ) from exc
    return x_user_id
