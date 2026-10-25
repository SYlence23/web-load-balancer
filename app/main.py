from fastapi import FastAPI

from app.config import settings

app = FastAPI(title="web-load-balancer")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "renderers": settings.renderer_urls}
