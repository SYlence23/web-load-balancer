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


class NoRendererAvailable(Exception):
    pass


class RendererRejected(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class RendererRegistry:
    def __init__(self, urls: list[str], interval: float, max_fails: int):
        self.renderers = [Renderer(url=url, name=url) for url in urls]
        self.interval = interval
        self.max_fails = max_fails

    async def run(self, client: httpx.AsyncClient) -> None:
        while True:
            await asyncio.gather(*(self._check(client, r) for r in self.renderers))
            await asyncio.sleep(self.interval)

    def get(self, url: str) -> Renderer | None:
        return next((r for r in self.renderers if r.url == url), None)

    def add(self, url: str) -> Renderer:
        renderer = self.get(url)
        if renderer is None:
            renderer = Renderer(url=url, name=url)
            self.renderers.append(renderer)
        return renderer

    def remove(self, url: str) -> None:
        self.renderers = [r for r in self.renderers if r.url != url]

    def pick(self, exclude: set[str]) -> Renderer | None:
        candidates = [
            r for r in self.renderers
            if r.up and r.running < r.capacity and r.url not in exclude
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda r: (r.load, r.name))

    async def dispatch(self, client: httpx.AsyncClient, payload: dict) -> tuple[Renderer, str]:
        tried: set[str] = set()

        while (renderer := self.pick(exclude=tried)) is not None:
            tried.add(renderer.url)
            renderer.running += 1

            try:
                response = await client.post(f"{renderer.url}/tasks", json=payload)
            except httpx.HTTPError as error:
                renderer.running -= 1
                logger.warning("Renderer %s did not accept the task: %r", renderer.name, error)
                continue

            if response.status_code == 503:
                renderer.running = renderer.capacity
                continue

            if response.status_code != 201:
                renderer.running -= 1
                raise RendererRejected(response.status_code, _error_detail(response))

            task_id = response.json()["id"]
            logger.info("Task %s sent to %s (load %.2f)", task_id, renderer.name, renderer.load)
            return renderer, task_id

        raise NoRendererAvailable()

    async def _check(self, client: httpx.AsyncClient, renderer: Renderer) -> None:
        try:
            response = await client.get(f"{renderer.url}/health", timeout=1.5)
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


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict):
        return body.get("detail") or body.get("title") or str(body)
    return str(body)


registry = RendererRegistry(settings.renderer_urls, settings.health_interval, settings.health_fails)
