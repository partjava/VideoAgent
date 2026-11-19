from datetime import UTC, datetime

from pydantic import BaseModel, Field, model_validator


class ScriptLine(BaseModel):
    speaker: str = Field(min_length=1)
    text: str = Field(min_length=1)
    start: float = Field(ge=0, lt=30)
    emotion: str = ""


class ScriptSegment(BaseModel):
    duration: int = Field(default=10, ge=3, le=12)
    plot: str = Field(min_length=1)
    voice_start: float = Field(ge=0, lt=30)
    voiceover: str = ""
    dialogue_lines: list[ScriptLine] = Field(default_factory=list)
    allow_silent: bool = False

    @model_validator(mode="after")
    def validate_speech(self):
        if self.voice_start >= self.duration:
            raise ValueError("旁白或对白切入时间必须小于本段时长")
        if self.dialogue_lines:
            if any(line.start >= self.duration for line in self.dialogue_lines):
                raise ValueError("对白出声时间必须小于本段时长")
            if any(right.start <= left.start for left, right in zip(self.dialogue_lines, self.dialogue_lines[1:])):
                raise ValueError("对白出声时间必须依次递增")
        elif not self.voiceover.strip() and not self.allow_silent:
            raise ValueError("旁白段落必须提供旁白文本")
        return self


class Script(BaseModel):
    script_id: str = Field(..., description="Unique script id")
    task_id: str = Field(..., description="Related video task id")
    title: str | None = None
    hook: str | None = None
    content: str = ""
    segments: list[ScriptSegment] = Field(default_factory=list)
    ending: str | None = None
    publish_copy: str | None = None
    source: str = "deepseek"
    status: str = "created"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
