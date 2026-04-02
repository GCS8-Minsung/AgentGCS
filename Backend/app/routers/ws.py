from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.dependencies import ws_manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/agents")
async def agents_websocket(websocket: WebSocket, user_id: str) -> None:
    await ws_manager.connect(user_id, websocket)
    try:
        await ws_manager.emit(
            user_id,
            "socket.connected",
            {"message": "에이전트 실시간 스트림 채널이 연결되었습니다."},
        )
        while True:
            _ = await websocket.receive_text()
            await websocket.send_json({"event_type": "socket.pong"})
    except WebSocketDisconnect:
        await ws_manager.disconnect(user_id, websocket)
    except Exception:
        await ws_manager.disconnect(user_id, websocket)

