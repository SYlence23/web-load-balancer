import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.balancer import NoRendererAvailable, RendererRejected, registry
from app.db import get_session
from app.models import FINAL_STATUSES, Task
from app.schemas import RenderCreated, RenderRequest, RenderStatus

router = APIRouter(prefix="/renders", tags=["renders"])


def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RenderCreated)
async def create_render(
    body: RenderRequest,
    http: httpx.AsyncClient = Depends(get_http),
    session: AsyncSession = Depends(get_session),
) -> RenderCreated:
    payload = {
        "scene": body.scene,
        "width": body.width,
        "height": body.height,
        "samplesPerPixel": body.spp,
        "maxDepth": body.max_depth,
        "seed": body.seed,
    }
    payload = {key: value for key, value in payload.items() if value is not None}

    try:
        renderer, renderer_task_id = await registry.dispatch(http, payload)
    except NoRendererAvailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "All renderers are busy or down.")
    except RendererRejected as error:
        raise HTTPException(error.status_code, error.detail)

    task = Task(
        scene=body.scene,
        width=body.width,
        height=body.height,
        spp=body.spp,
        max_depth=body.max_depth,
        seed=body.seed,
        renderer=renderer.name,
        renderer_url=renderer.url,
        renderer_task_id=renderer_task_id,
    )
    session.add(task)
    await session.commit()
    return RenderCreated(id=task.id, renderer=renderer.name)


@router.get("", response_model=list[RenderStatus])
async def list_renders(session: AsyncSession = Depends(get_session)) -> list[RenderStatus]:
    result = await session.scalars(select(Task).order_by(Task.created_at.desc()).limit(50))
    return [_to_status(task) for task in result]


@router.get("/{render_id}", response_model=RenderStatus)
async def get_render(
    render_id: uuid.UUID,
    http: httpx.AsyncClient = Depends(get_http),
    session: AsyncSession = Depends(get_session),
) -> RenderStatus:
    task = await _get_task(session, render_id)

    if task.status not in FINAL_STATUSES:
        response = await _call_renderer(http, task, "GET", "")
        data = response.json()
        task.status = data["status"]
        task.progress = data["progress"]
        task.error = data.get("error")
        if task.status in FINAL_STATUSES:
            task.finished_at = datetime.now(timezone.utc)
        await session.commit()

    return _to_status(task)


@router.get("/{render_id}/image", response_class=Response)
async def get_render_image(
    render_id: uuid.UUID,
    http: httpx.AsyncClient = Depends(get_http),
    session: AsyncSession = Depends(get_session),
) -> Response:
    task = await _get_task(session, render_id)
    response = await _call_renderer(http, task, "GET", "/image")
    return Response(content=response.content, media_type="image/png")


@router.delete("/{render_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_render(
    render_id: uuid.UUID,
    http: httpx.AsyncClient = Depends(get_http),
    session: AsyncSession = Depends(get_session),
) -> None:
    task = await _get_task(session, render_id)
    if task.status in FINAL_STATUSES:
        return

    await _call_renderer(http, task, "DELETE", "")
    task.status = "cancelled"
    task.finished_at = datetime.now(timezone.utc)
    await session.commit()


async def _get_task(session: AsyncSession, render_id: uuid.UUID) -> Task:
    task = await session.get(Task, render_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Render not found.")
    return task


def _to_status(task: Task) -> RenderStatus:
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
