import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Renderer:
    url: str
    name: str
    running: int = 0
    capacity: int = 0
    up: bool = False
    fails: int = 0
    last_seen: float | None = None

    @property
    def load(self) -> float:
        return self.running / self.capacity if self.capacity else 1.0


class RendererRegistry:
    def __init__(self, urls: list[str], interval: float, max_fails: int):
        self.renderers = [Renderer(url=url, name=url) for url in urls]
        self.interval = interval
        self.max_fails = max_fails

    async def run(self) -> None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(1.5)) as client:
            while True:
                await asyncio.gather(*(self._check(client, r) for r in self.renderers))
                await asyncio.sleep(self.interval)

    async def _check(self, client: httpx.AsyncClient, renderer: Renderer) -> None:
        try:
            response = await client.get(f"{renderer.url}/health")
            response.raise_for_status()
            data = response.json()
            renderer.name = data["name"]
            renderer.running = data["running"]
            renderer.capacity = data["capacity"]
        except (httpx.HTTPError, ValueError, KeyError) as error:
            renderer.fails += 1
            if renderer.up and renderer.fails >= self.max_fails:
                renderer.up = False
                logger.warning("Renderer %s is down: %r", renderer.name, error)
            return

        if not renderer.up:
            logger.info("Renderer %s is up (%s)", renderer.name, renderer.url)
        renderer.up = True
        renderer.fails = 0
        renderer.last_seen = time.time()


registry = RendererRegistry(settings.renderer_urls, settings.health_interval, settings.health_fails)
