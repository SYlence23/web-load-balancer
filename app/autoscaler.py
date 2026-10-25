import asyncio
import logging
import socket
import time
from urllib.parse import urlparse

import docker
from docker.errors import DockerException, NotFound

from app.balancer import registry
from app.config import settings
from app.db import SessionLocal
from app.render_queue import dispatch_lock, has_queued

logger = logging.getLogger(__name__)

LABEL = "web-raytracer.autoscaled"


class Autoscaler:
    def __init__(self):
        self.client: docker.DockerClient | None = None
        self.network = settings.autoscale_network
        self.containers: dict[str, str] = {}
        self.started_at: dict[str, float] = {}
        self.idle_since: dict[str, float] = {}

    async def run(self) -> None:
        try:
            await asyncio.to_thread(self._connect)
        except DockerException as error:
            logger.warning("Autoscaling is off, Docker is not reachable: %r", error)
            return

        logger.info(
            "Autoscaling %d-%d workers on network %s",
            settings.autoscale_min, settings.autoscale_max, self.network,
        )
        try:
            while True:
                try:
                    await self._tick()
                except Exception:
                    logger.exception("Autoscaling failed")
                await asyncio.sleep(settings.autoscale_interval)
        finally:
            await asyncio.to_thread(self._remove_all)

    def _connect(self) -> None:
        self.client = docker.from_env()
        if not self.network:
            me = self.client.containers.get(socket.gethostname())
            self.network = next(iter(me.attrs["NetworkSettings"]["Networks"]))

        now = time.time()
        self.started_at = {r.url: now for r in registry.renderers}

        leftovers = self.client.containers.list(all=True, filters={"label": f"{LABEL}={self.network}"})
        for container in leftovers:
            if container.status == "running":
                self._track(container.name)
                logger.info("Adopted worker %s", container.name)
            else:
                container.remove(force=True)

    async def _tick(self) -> None:
        async with SessionLocal() as session:
            queued = await has_queued(session)

        now = time.time()
        self._update_idle(now)
        await self._drop_broken(now)

        count = len(registry.renderers)
        busy = queued or _load() >= settings.autoscale_up_load

        if count < settings.autoscale_min or (count < settings.autoscale_max and busy and not self._starting(now)):
            name = await asyncio.to_thread(self._start)
            self._track(name)
            logger.info("Started worker %s (%d/%d, queued: %s, load %.2f)",
                        name, count + 1, settings.autoscale_max, queued, _load())
            return

        if queued or count <= settings.autoscale_min:
            return

        for url, since in self.idle_since.items():
            if now - since >= settings.autoscale_idle_seconds and _load(without=url) < settings.autoscale_up_load:
                await self._stop(url, "idle")
                return

    def _update_idle(self, now: float) -> None:
        for url in self.containers:
            renderer = registry.get(url)
            if renderer and renderer.up and renderer.running == 0:
                self.idle_since.setdefault(url, now)
            else:
                self.idle_since.pop(url, None)

    def _starting(self, now: float) -> bool:
        return any(
            not r.up and r.last_seen is None and now - self.started_at.get(r.url, 0) < settings.autoscale_start_timeout
            for r in registry.renderers
        )

    async def _drop_broken(self, now: float) -> None:
        for url in list(self.containers):
            renderer = registry.get(url)
            if renderer is None or renderer.up:
                continue
            last_alive = max(self.started_at.get(url, 0), renderer.last_seen or 0)
            if now - last_alive >= settings.autoscale_start_timeout:
                await self._stop(url, "not responding")

    def _track(self, name: str) -> None:
        url = f"http://{name}:{settings.autoscale_worker_port}"
        registry.add(url)
        self.containers[url] = name
        self.started_at[url] = time.time()

    def _start(self) -> str:
        name = self._free_name()
        self.client.containers.run(
            settings.autoscale_image,
            name=name,
            detach=True,
            network=self.network,
            environment={"Worker__Name": name, "Worker__Capacity": str(settings.autoscale_worker_capacity)},
            labels={LABEL: self.network},
            restart_policy={"Name": "unless-stopped"},
        )
        return name

    async def _stop(self, url: str, reason: str) -> None:
        async with dispatch_lock:
            renderer = registry.get(url)
            if renderer and renderer.up and renderer.running:
                return
            registry.remove(url)

        name = self.containers.pop(url)
        self.started_at.pop(url, None)
        self.idle_since.pop(url, None)
        await asyncio.to_thread(self._remove_container, name)
        logger.info("Stopped worker %s (%s, %d left)", name, reason, len(registry.renderers))

    def _free_name(self) -> str:
        taken = {c.name for c in self.client.containers.list(all=True)}
        taken |= {urlparse(r.url).hostname for r in registry.renderers}
        number = 1
        while f"{settings.autoscale_name_prefix}-{number}" in taken:
            number += 1
        return f"{settings.autoscale_name_prefix}-{number}"

    def _remove_container(self, name: str) -> None:
        try:
            self.client.containers.get(name).remove(force=True)
        except NotFound:
            pass

    def _remove_all(self) -> None:
        for url, name in list(self.containers.items()):
            self._remove_container(name)
            registry.remove(url)
            logger.info("Removed worker %s on shutdown", name)
        self.containers.clear()


def _load(without: str | None = None) -> float:
    up = [r for r in registry.renderers if r.up and r.url != without]
    capacity = sum(r.capacity for r in up)
    return sum(r.running for r in up) / capacity if capacity else 0.0


autoscaler = Autoscaler()
