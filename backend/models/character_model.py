from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReferenceView = Literal["front", "profile", "whole"]
PortraitView = Literal["front", "profile", "whole", "all"]
PortraitStyle = Literal["realistic", "anime", "3d"]


class CharacterFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    face_dna: str = Field(default="", max_length=4000)
    signature_clothing: str = Field(default="", max_length=2000)
    tts_voice: str = Field(default="zh-CN-YunyangNeural", pattern=r"^[A-Za-z]{2,3}-[A-Za-z]{2,4}-[A-Za-z0-9]+Neural$", max_length=100)
    rate: str = Field(default="+0%", pattern=r"^[+-](?:[0-9]|[1-9][0-9]|100)%$")
    pitch: str = Field(default="+0Hz", pattern=r"^[+-](?:[0-9]|[1-9][0-9]|100)Hz$")


class CharacterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    face_dna: str | None = None
    signature_clothing: str | None = None
    tts_voice: str | None = None
    rate: str | None = None
    pitch: str | None = None


class ReferenceUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_base64: str = Field(min_length=1, max_length=14000000)
    view: ReferenceView = "front"


class PortraitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(default="", max_length=4000)
    view: PortraitView = "all"
    style: PortraitStyle = "realistic"
    include_turntable: bool = False


class TurntableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration: int = Field(default=5, ge=2, le=10)


class VoicePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(default="你好，这是我的角色声音试听。", min_length=1, max_length=500)
