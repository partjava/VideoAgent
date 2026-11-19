from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


class CreateSeriesRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    brief: str = Field(min_length=1, max_length=20000)
    total_episodes: int = Field(ge=1, le=10)
    episode_duration: int = Field(default=30, ge=10, le=180)
    episode_duration_min: int | None = Field(default=None, ge=10, le=180)
    episode_duration_max: int | None = Field(default=None, ge=10, le=180)
    generation_mode: Literal['auto_director', 'storyboard_flow', 'native_10s'] = 'native_10s'
    style: Literal['短剧', '漫剧'] = '短剧'
    ratio: Literal['9:16', '16:9'] = '9:16'
    market_mode: Literal['cn', 'en'] = 'cn'
    character_ids: list[str] = Field(default_factory=list)
    auto_continue: bool = False
    pre_shoot_review: bool = False
    generation_limit: int | None = Field(default=None, ge=1, le=10000)
    initial_episodes: list[dict[str, Any]] | None = None
    continuity_ledgers: dict[str, Any] | None = None
    first_episode_script: str | None = Field(default=None, max_length=20000)
    first_episode_task_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode='after')
    def normalize_duration_range(self):
        lo = self.episode_duration_min
        hi = self.episode_duration_max
        if lo is not None or hi is not None:
            resolved_lo = lo if lo is not None else min(self.episode_duration, hi or self.episode_duration)
            resolved_hi = hi if hi is not None else max(self.episode_duration, lo or self.episode_duration)
            if resolved_lo > resolved_hi:
                raise ValueError('单集最小时长不能大于最大时长')
            self.episode_duration_min = resolved_lo
            self.episode_duration_max = resolved_hi
            self.episode_duration = int(round((resolved_lo + resolved_hi) / 2))
        return self


class ContinuityLedgersRequest(BaseModel):
    continuity_ledgers: dict[str, Any]


class OutlineRequest(BaseModel):
    episodes: list[dict[str, Any]]


class ConfirmSnapshotRequest(BaseModel):
    state: dict[str, Any]


class BindEpisodeTaskRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)


class PublishNotesRequest(BaseModel):
    platform: Literal['通用', '抖音'] = '通用'


class CoverRequest(BaseModel):
    second: float | None = Field(default=None, ge=0, le=3, allow_inf_nan=False)
    title: str = Field(default='', max_length=60)
