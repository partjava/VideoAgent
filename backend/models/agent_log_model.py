from datetime import UTC, datetime

from pydantic import BaseModel, Field


class AgentLog(BaseModel):
    log_id: str = Field(..., description="Unique agent log id")
    task_id: str = Field(..., description="Related video task id")
    agent_name: str = Field(..., description="Agent or service name")
    status: str = Field(default="success", description="Log status")
    input_summary: str | None = Field(default=None)
    output_summary: str | None = Field(default=None)
    message: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CreateAgentLogRequest(BaseModel):
    task_id: str
    agent_name: str
    status: str = "success"
    input_summary: str | None = None
    output_summary: str | None = None
    message: str | None = None
