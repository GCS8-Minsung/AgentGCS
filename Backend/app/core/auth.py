from typing import Annotated

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
    return x_user_id

