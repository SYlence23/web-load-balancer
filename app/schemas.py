import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class Credentials(ApiModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=72)


class UserOut(ApiModel):
    id: int
    username: str
    is_admin: bool


class Token(ApiModel):
    access_token: str
    token_type: str = "bearer"


class RenderRequest(ApiModel):
    scene: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    spp: int = Field(gt=0)
    max_depth: int | None = Field(default=None, gt=0)
    seed: int | None = None


class AdminRenderer(ApiModel):
    name: str
    url: str
    up: bool
    running: int
    capacity: int
    load: float
    last_seen: datetime | None


class AdminTask(ApiModel):
    id: uuid.UUID
    username: str | None
    status: str
    progress: float
    renderer: str | None
    scene: str
    width: int
    height: int
    spp: int
    created_at: datetime
    started_at: datetime | None
    queue_position: int | None
    eta_seconds: float | None


class RenderCreated(ApiModel):
    id: uuid.UUID
    status: str
    renderer: str | None
    queue_position: int | None = None
    eta_seconds: float | None = None


class RenderStatus(ApiModel):
    id: uuid.UUID
    status: str
    progress: float
    error: str | None
    renderer: str | None
    scene: str
    width: int
    height: int
    spp: int
    created_at: datetime
    queue_position: int | None = None
    eta_seconds: float | None = None
