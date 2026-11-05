from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Scene(BaseModel):
    scene_id: str = Field(..., description="Unique scene id")
    task_id: str = Field(..., description="Related video task id")
    scene_index: int = Field(..., ge=1)
    duration: int = Field(..., ge=1)
    speaker: str | None = None
    voiceover: str | None = None
    dialogue_lines: list[dict] = Field(default_factory=list)
    subtitle: str | None = None
    visual_description: str | None = None
    shot_type: str | None = None
    character_state: str | None = None
    scene_continuity: str | None = None
    transition_note: str | None = None
    voice_cue: str | None = None
    voice_start: float = Field(default=0, ge=0)
    sound_cues: list[dict] = Field(default_factory=list)
    audio_hint: str | None = None
    image_prompt: str | None = None
    video_prompt: str | None = None
    negative_prompt: str | None = None
    motion_beats: list[dict] | None = None
    transition_type: str = "cut"
    start_state: str | None = None
    end_state: str | None = None
    reaction_focus: str | None = None
    continuity_input_path: str | None = None
    last_frame_path: str | None = None
    first_frame_focus: str | None = None
    last_frame_for_transition: str | None = None
    continuity_note: str | None = None
    camera_motion: str | None = None
    need_dynamic_video: bool = False
    image_path: str | None = None
    video_path: str | None = None
    audio_path: str | None = None
    audio_duration: float | None = None
    status: str = "created"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
