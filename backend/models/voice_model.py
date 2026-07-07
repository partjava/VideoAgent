from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Voice(BaseModel):
    voice_id: str = Field(..., description="Unique voice id")
    task_id: str = Field(..., description="Related video task id")
    text: str
    voice_type: str = "edge_tts_default"
    path: str
    duration: float | None = None
    status: str = "created"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
