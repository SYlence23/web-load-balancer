import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select

from app.balancer import registry
from app.config import settings
from app.db import SessionLocal
from app.models import FINAL_STATUSES, Task

logger = logging.getLogger(__name__)


class TaskTracker:
    def __init__(self, interval: float, images_dir: str):
        self.interval = interval
        self.images_dir = Path(images_dir)

    async def run(self, http: httpx.AsyncClient) -> None:
        self.images_dir.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                await self._poll_once(http)
            except Exception:
                logger.exception("Task tracking failed")
            await asyncio.sleep(self.interval)

    async def _poll_once(self, http: httpx.AsyncClient) -> None:
        async with SessionLocal() as session:
            tasks = (await session.scalars(select(Task).where(Task.status == "running"))).all()
            if not tasks:
                return
            await asyncio.gather(*(self._update(http, task) for task in tasks))
            await session.commit()

    async def _update(self, http: httpx.AsyncClient, task: Task) -> None:
        url = f"{task.renderer_url}/tasks/{task.renderer_task_id}"

        try:
            response = await http.get(url, timeout=2.0)
        except httpx.HTTPError:
            if not self._renderer_up(task.renderer_url):
                self._finish(task, "failed", f"Renderer {task.renderer} went down.")
            return

        if response.status_code == 404:
            self._finish(task, "failed", f"Renderer {task.renderer} no longer has this task.")
            return
        if response.is_error:
            return

        data = response.json()
        task.progress = data["progress"]
        worker_status = data["status"]

        if worker_status == "done":
            try:
                await self._save_image(http, task, url)
            except httpx.HTTPError as error:
                logger.warning("Could not download image for task %s: %r", task.id, error)
                return

        if worker_status in FINAL_STATUSES:
            self._finish(task, worker_status, data.get("error"))

    async def _save_image(self, http: httpx.AsyncClient, task: Task, url: str) -> None:
        response = await http.get(f"{url}/image")
        response.raise_for_status()
        path = self.images_dir / f"{task.id}.png"
        await asyncio.to_thread(path.write_bytes, response.content)
        task.image_path = str(path)

    def _finish(self, task: Task, status: str, error: str | None) -> None:
        task.status = status
        task.error = error
        task.finished_at = datetime.now(timezone.utc)
        logger.info("Task %s finished: %s", task.id, status)

    @staticmethod
    def _renderer_up(url: str | None) -> bool:
        return any(r.up for r in registry.renderers if r.url == url)


tracker = TaskTracker(settings.tracker_interval, settings.images_dir)
