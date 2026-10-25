import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.balancer import registry
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    health_task = asyncio.create_task(registry.run())
    yield
    health_task.cancel()
    with suppress(asyncio.CancelledError):
        await health_task


app = FastAPI(title="web-load-balancer", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "renderers": settings.renderer_urls}


@app.get("/admin/renderers")
async def list_renderers() -> list[dict]:
    return [
        {
            "name": r.name,
            "url": r.url,
            "up": r.up,
            "running": r.running,
            "capacity": r.capacity,
            "load": round(r.load, 2),
            "lastSeen": r.last_seen,
        }
        for r in registry.renderers
    ]
