import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from app.auth import decode_token
from app.db import SessionLocal
from app.models import FINAL_STATUSES, Task
from app.render_queue import queue_info
from app.routers.renders import to_status

router = APIRouter(tags=["progress"])

POLL_SECONDS = 0.5


@router.websocket("/ws/renders/{render_id}")
async def render_progress(websocket: WebSocket, render_id: uuid.UUID, token: str = Query()) -> None:
    try:
        user_id = decode_token(token)
    except HTTPException:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token.")
        return

    async with SessionLocal() as session:
        task = await session.get(Task, render_id)
    if task is None or task.user_id != user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Render not found.")
        return

    await websocket.accept()
    last_message = None

    try:
        while True:
            async with SessionLocal() as session:
                task = await session.get(Task, render_id)
                if task is None:
                    await websocket.close(reason="Render was deleted.")
                    return
                message = to_status(task, await queue_info(session)).model_dump(mode="json", by_alias=True)

            if message != last_message:
                await websocket.send_json(message)
                last_message = message

            if task.status in FINAL_STATUSES:
                await websocket.close()
                return

            await asyncio.sleep(POLL_SECONDS)
    except WebSocketDisconnect:
        return
