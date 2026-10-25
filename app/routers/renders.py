import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.balancer import NoRendererAvailable, RendererRejected, registry
from app.db import get_session
from app.limits import check_active_tasks, check_render_size, trim_history, user_locks
from app.models import FINAL_STATUSES, Task, User
from app.render_queue import QueueInfo, dispatch_lock, has_queued, mark_running, queue_info, worker_payload
from app.schemas import RenderCreated, RenderRequest, RenderStatus

router = APIRouter(prefix="/renders", tags=["renders"])


def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RenderCreated)
async def create_render(
    body: RenderRequest,
    http: httpx.AsyncClient = Depends(get_http),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> RenderCreated:
    check_render_size(body)

    async with user_locks[user.id]:
        await check_active_tasks(session, user.id)
        task = Task(
            user_id=user.id,
            scene=body.scene,
            width=body.width,
            height=body.height,
            spp=body.spp,
            max_depth=body.max_depth,
            seed=body.seed,
            status="queued",
        )

        async with dispatch_lock:
            if not await has_queued(session):
                try:
                    renderer, renderer_task_id = await registry.dispatch(http, worker_payload(task))
                except NoRendererAvailable:
                    pass
                except RendererRejected as error:
                    raise HTTPException(error.status_code, error.detail)
                else:
                    mark_running(task, renderer, renderer_task_id)

            session.add(task)
            await trim_history(session, user.id)
            await session.commit()

    info = await queue_info(session)
    return RenderCreated(
        id=task.id,
        status=task.status,
        renderer=task.renderer,
        queue_position=info.positions.get(task.id),
        eta_seconds=info.eta_seconds(task.id),
    )


@router.get("", response_model=list[RenderStatus])
async def list_renders(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[RenderStatus]:
    query = select(Task).where(Task.user_id == user.id).order_by(Task.created_at.desc()).limit(50)
    tasks = (await session.scalars(query)).all()
    info = await queue_info(session)
    return [_to_status(task, info) for task in tasks]


@router.get("/{render_id}", response_model=RenderStatus)
async def get_render(
    render_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> RenderStatus:
    task = await _get_task(session, render_id, user)
    return _to_status(task, await queue_info(session))


@router.get("/{render_id}/image", response_class=Response)
async def get_render_image(
    render_id: uuid.UUID,
    http: httpx.AsyncClient = Depends(get_http),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> Response:
    task = await _get_task(session, render_id, user)

    if task.image_path and Path(task.image_path).exists():
        return FileResponse(task.image_path, media_type="image/png")

    if task.status == "running":
        response = await _call_renderer(http, task, "GET", "/image")
        return Response(content=response.content, media_type="image/png")

    raise HTTPException(status.HTTP_404_NOT_FOUND, "Image is not available.")


@router.delete("/{render_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_render(
    render_id: uuid.UUID,
    http: httpx.AsyncClient = Depends(get_http),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
) -> None:
    task = await _get_task(session, render_id, user)
    if task.status in FINAL_STATUSES:
        return

    if task.status == "running":
        await _call_renderer(http, task, "DELETE", "")
    task.status = "cancelled"
    task.finished_at = datetime.now(timezone.utc)
    await session.commit()


async def _get_task(session: AsyncSession, render_id: uuid.UUID, user: User) -> Task:
    task = await session.get(Task, render_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Render not found.")
    return task


def _to_status(task: Task, info: QueueInfo) -> RenderStatus:
    return RenderStatus(
        id=task.id,
        status=task.status,
        progress=task.progress,
        error=task.error,
        renderer=task.renderer,
        scene=task.scene,
        width=task.width,
        height=task.height,
        spp=task.spp,
        created_at=task.created_at,
        queue_position=info.positions.get(task.id),
        eta_seconds=info.eta_seconds(task.id),
    )


async def _call_renderer(http: httpx.AsyncClient, task: Task, method: str, suffix: str) -> httpx.Response:
    url = f"{task.renderer_url}/tasks/{task.renderer_task_id}{suffix}"

    try:
        response = await http.request(method, url)
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Renderer {task.renderer} is unreachable.")

    if response.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(status.HTTP_410_GONE, f"Renderer {task.renderer} no longer has this task.")
    if response.is_error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Renderer {task.renderer} returned {response.status_code}.")

    return response
