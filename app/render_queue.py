import asyncio
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.balancer import NoRendererAvailable, Renderer, RendererRejected, registry
from app.db import SessionLocal
from app.models import Task

logger = logging.getLogger(__name__)

DEFAULT_RENDER_SECONDS = 30.0

dispatch_lock = asyncio.Lock()


@dataclass
class QueueInfo:
    positions: dict[uuid.UUID, int]
    average_seconds: float
    capacity: int

    def eta_seconds(self, task_id: uuid.UUID) -> float | None:
        position = self.positions.get(task_id)
        if position is None:
            return None
        return round(math.ceil(position / self.capacity) * self.average_seconds, 1)


async def has_queued(session: AsyncSession) -> bool:
    return await session.scalar(select(Task.id).where(Task.status == "queued").limit(1)) is not None


async def try_dispatch(http: httpx.AsyncClient, task: Task) -> bool:
    try:
        renderer, renderer_task_id = await registry.dispatch(http, worker_payload(task))
    except NoRendererAvailable:
        return False
    except RendererRejected as error:
        task.status = "failed"
        task.error = error.detail
        task.finished_at = datetime.now(timezone.utc)
        return True

    mark_running(task, renderer, renderer_task_id)
    return True


def mark_running(task: Task, renderer: Renderer, renderer_task_id: str) -> None:
    task.status = "running"
    task.renderer = renderer.name
    task.renderer_url = renderer.url
    task.renderer_task_id = renderer_task_id
    task.started_at = datetime.now(timezone.utc)


async def dispatch_queued(http: httpx.AsyncClient) -> None:
    async with dispatch_lock, SessionLocal() as session:
        queued = (
            await session.scalars(select(Task).where(Task.status == "queued").order_by(Task.created_at))
        ).all()

        for task in queued:
            if not await try_dispatch(http, task):
                break
            logger.info("Queued task %s left the queue: %s", task.id, task.status)

        await session.commit()


async def queue_info(session: AsyncSession) -> QueueInfo:
    queued_ids = (
        await session.scalars(select(Task.id).where(Task.status == "queued").order_by(Task.created_at))
    ).all()
    positions = {task_id: index + 1 for index, task_id in enumerate(queued_ids)}

    recent = (
        await session.execute(
            select(Task.started_at, Task.finished_at)
            .where(Task.status == "done", Task.started_at.is_not(None))
            .order_by(Task.finished_at.desc())
            .limit(20)
        )
    ).all()
    durations = [(finished - started).total_seconds() for started, finished in recent]
    average = sum(durations) / len(durations) if durations else DEFAULT_RENDER_SECONDS

    capacity = sum(r.capacity for r in registry.renderers if r.up) or 1
    return QueueInfo(positions=positions, average_seconds=average, capacity=capacity)


def worker_payload(task: Task) -> dict:
    payload = {
        "scene": task.scene,
        "width": task.width,
        "height": task.height,
        "samplesPerPixel": task.spp,
        "maxDepth": task.max_depth,
        "seed": task.seed,
    }
    return {key: value for key, value in payload.items() if value is not None}
