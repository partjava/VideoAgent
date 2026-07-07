from datetime import UTC, datetime

from pydantic import BaseModel, Field


class RenderOutput(BaseModel):
    output_id: str = Field(..., description="Unique render output id")
    task_id: str = Field(..., description="Related video task id")
    video_path: str | None = None
    cover_path: str | None = None
    subtitle_path: str | None = None
    script_path: str | None = None
    storyboard_path: str | None = None
    publish_path: str | None = None
    duration: float | None = None
    status: str = "created"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
