import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

FINAL_STATUSES = {"done", "cancelled", "failed"}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
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
    image_path: Mapped[str | None] = mapped_column(String(300))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
