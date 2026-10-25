import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

FINAL_STATUSES = {"done", "cancelled", "failed"}


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    scene: Mapped[str] = mapped_column(String(50))
    width: Mapped[int]
    height: Mapped[int]
    spp: Mapped[int]
    max_depth: Mapped[int | None]
    seed: Mapped[int | None]

    status: Mapped[str] = mapped_column(String(20), default="running")
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None]

    renderer: Mapped[str | None] = mapped_column(String(100))
    renderer_url: Mapped[str | None] = mapped_column(String(200))
    renderer_task_id: Mapped[str | None] = mapped_column(String(50))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
