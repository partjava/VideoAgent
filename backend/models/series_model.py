from typing import Any, Literal
from pydantic import BaseModel, Field


class CreateSeriesRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    brief: str = Field(min_length=1, max_length=20000)
    total_episodes: int = Field(ge=1, le=10)
    episode_duration: Literal[10, 30, 60, 90] = 30
    style: Literal['短剧', '漫剧'] = '短剧'
    ratio: Literal['9:16', '16:9'] = '9:16'
    character_ids: list[str] = Field(default_factory=list)
    auto_continue: bool = False
    pre_shoot_review: bool = False
    generation_limit: int | None = Field(default=None, ge=1, le=10000)


class OutlineRequest(BaseModel):
    episodes: list[dict[str, Any]]


class ConfirmSnapshotRequest(BaseModel):
    state: dict[str, Any]


class PublishNotesRequest(BaseModel):
    platform: Literal['通用', '抖音'] = '通用'


class CoverRequest(BaseModel):
    second: float | None = Field(default=None, ge=0, le=3, allow_inf_nan=False)
    title: str = Field(default='', max_length=60)
