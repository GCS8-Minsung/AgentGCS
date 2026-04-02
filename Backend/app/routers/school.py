from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user_id
from app.models.schemas import MeetingReservationCreateRequest, SnippetWriteRequest
from app.services.school_api_client import SchoolApiError, get_school_client_for_user

router = APIRouter(prefix="/school", tags=["school-api"])


@router.get("/meeting-rooms")
async def list_meeting_rooms(user_id: str = Depends(get_current_user_id)) -> dict:
    try:
        client = await get_school_client_for_user(user_id)
        rooms = await client.list_meeting_rooms()
        return {"items": rooms}
    except SchoolApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/meeting-rooms/{room_id}/reservations")
async def list_meeting_room_reservations(
    room_id: int,
    date: str | None = Query(default=None, description="YYYY-MM-DD"),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    try:
        client = await get_school_client_for_user(user_id)
        items = await client.list_room_reservations(room_id=room_id, date=date)
        return {"items": items}
    except SchoolApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/meeting-rooms/reservations")
async def create_meeting_room_reservation(
    body: MeetingReservationCreateRequest, user_id: str = Depends(get_current_user_id)
) -> dict:
    try:
        client = await get_school_client_for_user(user_id)
        item = await client.create_room_reservation(
            room_id=body.room_id,
            start_at=body.start_at,
            end_at=body.end_at,
            purpose=body.purpose,
        )
        return {"item": item}
    except SchoolApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/meeting-rooms/reservations/{reservation_id}")
async def cancel_meeting_room_reservation(
    reservation_id: int, user_id: str = Depends(get_current_user_id)
) -> dict:
    try:
        client = await get_school_client_for_user(user_id)
        data = await client.cancel_room_reservation(reservation_id)
        return {"result": data}
    except SchoolApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/daily-snippets")
async def list_daily_snippets(
    user_id: str = Depends(get_current_user_id),
    limit: int = 20,
    offset: int = 0,
    q: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    try:
        client = await get_school_client_for_user(user_id)
        data = await client.list_daily_snippets(
            limit=limit, offset=offset, q=q, from_date=from_date, to_date=to_date
        )
        return {"result": data}
    except SchoolApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/daily-snippets")
async def create_daily_snippet(
    body: SnippetWriteRequest, user_id: str = Depends(get_current_user_id)
) -> dict:
    try:
        client = await get_school_client_for_user(user_id)
        item = await client.create_daily_snippet(body.content)
        return {"item": item}
    except SchoolApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/daily-snippets/{snippet_id}")
async def update_daily_snippet(
    snippet_id: int,
    body: SnippetWriteRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    try:
        client = await get_school_client_for_user(user_id)
        item = await client.update_daily_snippet(snippet_id, body.content)
        return {"item": item}
    except SchoolApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/weekly-snippets")
async def list_weekly_snippets(
    user_id: str = Depends(get_current_user_id),
    limit: int = 20,
    offset: int = 0,
    q: str | None = None,
    from_week: str | None = None,
    to_week: str | None = None,
) -> dict:
    try:
        client = await get_school_client_for_user(user_id)
        data = await client.list_weekly_snippets(
            limit=limit, offset=offset, q=q, from_week=from_week, to_week=to_week
        )
        return {"result": data}
    except SchoolApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/weekly-snippets")
async def create_weekly_snippet(
    body: SnippetWriteRequest, user_id: str = Depends(get_current_user_id)
) -> dict:
    try:
        client = await get_school_client_for_user(user_id)
        item = await client.create_weekly_snippet(body.content)
        return {"item": item}
    except SchoolApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/weekly-snippets/{snippet_id}")
async def update_weekly_snippet(
    snippet_id: int,
    body: SnippetWriteRequest,
    user_id: str = Depends(get_current_user_id),
) -> dict:
    try:
        client = await get_school_client_for_user(user_id)
        item = await client.update_weekly_snippet(snippet_id, body.content)
        return {"item": item}
    except SchoolApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

