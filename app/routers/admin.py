from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin
from app.balancer import registry
from app.db import get_session
from app.limits import ACTIVE_STATUSES
from app.models import Task, User
from app.render_queue import queue_info
from app.schemas import AdminRenderer, AdminTask

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/renderers", response_model=list[AdminRenderer])
async def list_renderers() -> list[AdminRenderer]:
    return [
        AdminRenderer(
            name=r.name,
            url=r.url,
            up=r.up,
            running=r.running,
            capacity=r.capacity,
            load=round(r.load, 2),
            last_seen=datetime.fromtimestamp(r.last_seen, timezone.utc) if r.last_seen else None,
        )
        for r in registry.renderers
    ]


@router.get("/tasks", response_model=list[AdminTask])
async def list_active_tasks(session: AsyncSession = Depends(get_session)) -> list[AdminTask]:
    rows = (
        await session.execute(
            select(Task, User.username)
            .outerjoin(User, Task.user_id == User.id)
            .where(Task.status.in_(ACTIVE_STATUSES))
            .order_by(Task.created_at)
        )
    ).all()
    info = await queue_info(session)

    return [
        AdminTask(
            id=task.id,
            username=username,
            status=task.status,
            progress=task.progress,
            renderer=task.renderer,
            scene=task.scene,
            width=task.width,
            height=task.height,
            spp=task.spp,
            created_at=task.created_at,
            started_at=task.started_at,
            queue_position=info.positions.get(task.id),
            eta_seconds=info.eta_seconds(task.id),
        )
        for task, username in rows
    ]
