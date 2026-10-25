import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.balancer import NoRendererAvailable, RendererRejected, registry
from app.schemas import RenderCreated, RenderRequest

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
