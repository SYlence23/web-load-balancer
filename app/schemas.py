from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class RenderRequest(ApiModel):
    scene: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    spp: int = Field(gt=0)
    max_depth: int | None = Field(default=None, gt=0)
    seed: int | None = None


class RenderCreated(ApiModel):
    id: str
    renderer: str
