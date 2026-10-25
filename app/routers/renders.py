import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.balancer import NoRendererAvailable, RendererRejected, registry
from app.schemas import RenderCreated, RenderRequest, RenderStatus

router = APIRouter(prefix="/renders", tags=["renders"])


@dataclass
class RenderJob:
    id: str
    request: RenderRequest
    renderer_url: str
    renderer_name: str
    renderer_task_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


jobs: dict[str, RenderJob] = {}


def get_http(request: Request) -> httpx.AsyncClient:
    return request.app.state.http


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RenderCreated)
async def create_render(body: RenderRequest, http: httpx.AsyncClient = Depends(get_http)) -> RenderCreated:
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

    job = RenderJob(
        id=uuid.uuid4().hex,
        request=body,
        renderer_url=renderer.url,
        renderer_name=renderer.name,
        renderer_task_id=renderer_task_id,
    )
    jobs[job.id] = job
    return RenderCreated(id=job.id, renderer=renderer.name)


@router.get("/{render_id}", response_model=RenderStatus)
async def get_render(render_id: str, http: httpx.AsyncClient = Depends(get_http)) -> RenderStatus:
    job = _get_job(render_id)
    response = await _call_renderer(http, job, "GET", "")
    data = response.json()

    return RenderStatus(
        id=job.id,
        status=data["status"],
        progress=data["progress"],
        error=data.get("error"),
        renderer=job.renderer_name,
        scene=job.request.scene,
        width=job.request.width,
        height=job.request.height,
        spp=job.request.spp,
        created_at=job.created_at,
    )


@router.get("/{render_id}/image", response_class=Response)
async def get_render_image(render_id: str, http: httpx.AsyncClient = Depends(get_http)) -> Response:
    job = _get_job(render_id)
    response = await _call_renderer(http, job, "GET", "/image")
    return Response(content=response.content, media_type="image/png")


@router.delete("/{render_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_render(render_id: str, http: httpx.AsyncClient = Depends(get_http)) -> None:
    job = _get_job(render_id)
    await _call_renderer(http, job, "DELETE", "")


def _get_job(render_id: str) -> RenderJob:
    job = jobs.get(render_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Render not found.")
    return job


async def _call_renderer(http: httpx.AsyncClient, job: RenderJob, method: str, suffix: str) -> httpx.Response:
    url = f"{job.renderer_url}/tasks/{job.renderer_task_id}{suffix}"

    try:
        response = await http.request(method, url)
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Renderer {job.renderer_name} is unreachable.")

    if response.status_code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(status.HTTP_410_GONE, f"Renderer {job.renderer_name} no longer has this task.")
    if response.is_error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Renderer {job.renderer_name} returned {response.status_code}.")

    return response
