import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI

from app.balancer import registry
from app.config import settings
from app.db import engine, init_db
from app.routers import admin, auth, progress, renders
from app.tracker import tracker

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http:
        app.state.http = http
        background = [
            asyncio.create_task(registry.run(http)),
            asyncio.create_task(tracker.run(http)),
        ]
        yield
        for task in background:
            task.cancel()
        for task in background:
            with suppress(asyncio.CancelledError):
                await task

    await engine.dispose()


app = FastAPI(title="web-load-balancer", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(renders.router)
app.include_router(progress.router)
app.include_router(admin.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "renderers": settings.renderer_urls}
