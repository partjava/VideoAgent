from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class VideoTaskStatus(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    SCRIPT_DONE = "script_done"
    STORYBOARD_DONE = "storyboard_done"
    IMAGE_GENERATING = "image_generating"
    VIDEO_GENERATING = "video_generating"
    VOICE_GENERATING = "voice_generating"
    SUBTITLE_GENERATING = "subtitle_generating"
    RENDERING = "rendering"
    EDITING = "editing"
    CHECKING = "checking"
    SUCCESS = "success"
    FAILED = "failed"


class VideoTask(BaseModel):
    task_id: str = Field(..., description="Unique video task id")
    user_input: str = Field(..., description="Original user prompt")
    topic: str | None = Field(default=None, description="Parsed video topic")
    duration: int | None = Field(default=None, description="Target duration in seconds")
    style: str | None = Field(default=None, description="Video style")
    platform: str | None = Field(default=None, description="Target publishing platform")
    ratio: str | None = Field(default="9:16", description="Video aspect ratio")
    generation_mode: str = Field(default="full_dynamic", description="Video generation mode")
    status: VideoTaskStatus = Field(default=VideoTaskStatus.CREATED)
    progress: int = Field(default=0, ge=0, le=100)
    current_agent: str | None = Field(default=None)
    provider_mode: str = Field(default="real")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CreateVideoTaskRequest(BaseModel):
    user_input: str = Field(..., min_length=1)
    duration: int | None = Field(default=None, ge=1)
    style: str | None = None
    platform: str | None = None
    ratio: str = "9:16"
    generation_mode: str = "full_dynamic"


class CreateVideoTaskResponse(BaseModel):
    task_id: str
    status: str
    progress: int


class VideoTaskProgressResponse(BaseModel):
    task_id: str
    task_status: str
    progress: int
    current_agent: str | None
    message: str
