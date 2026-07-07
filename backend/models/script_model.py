from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Script(BaseModel):
    script_id: str = Field(..., description="Unique script id")
    task_id: str = Field(..., description="Related video task id")
    title: str | None = None
    hook: str | None = None
    content: str = ""
    ending: str | None = None
    publish_copy: str | None = None
    source: str = "deepseek"
    status: str = "created"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
