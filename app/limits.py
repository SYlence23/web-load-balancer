import asyncio
from collections import defaultdict
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import FINAL_STATUSES, Task
from app.schemas import RenderRequest

ACTIVE_STATUSES = {"queued", "running"}

user_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def check_render_size(body: RenderRequest) -> None:
    problems = []
    if body.width > settings.max_width or body.height > settings.max_height:
        problems.append(f"resolution is limited to {settings.max_width}x{settings.max_height}")
    if body.spp > settings.max_spp:
        problems.append(f"samples per pixel are limited to {settings.max_spp}")

    complexity = body.width * body.height * body.spp
    if complexity > settings.max_complexity:
        problems.append(f"width x height x spp = {complexity:,} is over the limit of {settings.max_complexity:,}")

    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Render is too heavy: " + "; ".join(problems) + ".")


async def check_active_tasks(session: AsyncSession, user_id: int) -> None:
    active = await session.scalar(
        select(func.count()).select_from(Task).where(Task.user_id == user_id, Task.status.in_(ACTIVE_STATUSES))
    )
    if active >= settings.max_active_per_user:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You already have {active} active renders. Wait for one to finish or cancel it "
            f"(limit is {settings.max_active_per_user}).",
        )


async def trim_history(session: AsyncSession, user_id: int) -> None:
    keep = settings.max_history_per_user
    beyond_limit = (
        await session.scalars(
            select(Task)
            .where(Task.user_id == user_id)
            .order_by(Task.created_at.desc())
            .offset(keep)
        )
    ).all()
    old_tasks = [task for task in beyond_limit if task.status in FINAL_STATUSES]
    if not old_tasks:
        return

    for task in old_tasks:
        if task.image_path:
            await asyncio.to_thread(Path(task.image_path).unlink, missing_ok=True)
    await session.execute(delete(Task).where(Task.id.in_([task.id for task in old_tasks])))
