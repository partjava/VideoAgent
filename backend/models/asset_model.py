from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Asset(BaseModel):
    asset_id: str = Field(..., description="Unique asset id")
    task_id: str = Field(..., description="Related video task id")
    scene_id: str | None = None
    asset_type: str = Field(..., description="image, video, audio, subtitle, or other")
    path: str
    source: str = "real"
    status: str = "created"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
