from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import anyio
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.database import mongodb
from models.response_model import ApiResponse
from services.drama_craft_service import (
    ai_decompose_novel_to_drama,
    ai_doctor_script,
    ai_expand_character,
    ai_generate_prop_sheet_prompt,
    ai_generate_scene_grid_prompt,
    ai_review_script,
    ai_upgrade_video_prompt,
    render_craft_asset_image,
)

router = APIRouter(prefix="/api/craft", tags=["drama-craft"])


class ExpandCharacterRequest(BaseModel):
    name: str = Field(default="", max_length=100)
    brief: str = Field(default="", max_length=2000)
    style: Literal["短剧", "漫剧"] = "短剧"
    market_mode: Literal["cn", "en"] = "cn"


class DecomposeNovelRequest(BaseModel):
    novel_text: str = Field(..., min_length=1, max_length=100000)
    title: str = Field(default="", max_length=120)
    total_episodes: int = Field(default=5, ge=1, le=10)
    episode_duration: Literal[10, 30, 60, 90] = 60
    style: Literal["短剧", "漫剧"] = "短剧"
    market_mode: Literal["cn", "en"] = "cn"


class ReviewScriptRequest(BaseModel):
    script_text: str = Field(default="", max_length=50000)
    task_id: str | None = None
    duration: int = Field(default=60, ge=5, le=600)


class DoctorScriptRequest(BaseModel):
    script_text: str = Field(default="", max_length=50000)
    title: str = Field(default="", max_length=120)
    duration: int = Field(default=60, ge=5, le=600)
    task_id: str | None = None
    save_to_task: bool = False


class SceneGridPromptRequest(BaseModel):
    scene_name: str = Field(..., min_length=1, max_length=120)
    space_type: Literal["indoor", "outdoor_building", "street_nature"] = "indoor"
    era_and_style: str = Field(default="", max_length=300)
    layout_and_materials: str = Field(default="", max_length=1000)
    lighting_and_kelvin: str = Field(default="", max_length=300)


class PropSheetPromptRequest(BaseModel):
    prop_name: str = Field(..., min_length=1, max_length=120)
    material_and_color: str = Field(default="", max_length=500)
    size_vs_palm: str = Field(default="", max_length=120)
    key_detail: str = Field(default="", max_length=500)
    style: str = Field(default="", max_length=200)


class SanitizeVideoPromptRequest(BaseModel):
    raw_prompt: str = Field(..., min_length=1, max_length=10000)
    speaker: str = Field(default="", max_length=100)
    character_ids: list[str] = Field(default_factory=list)
    all_character_names: list[str] = Field(default_factory=list)
    shot_type: str = Field(default="", max_length=100)
    camera_motion: str = Field(default="", max_length=200)


class RenderCraftAssetRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    negative_prompt: str = Field(default="", max_length=2000)
    category: Literal["scene_grids", "prop_sheets", "craft_assets"] = "craft_assets"
    ratio: Literal["16:9", "9:16"] = "16:9"


def _err(msg: str, code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=code, content={"status": "error", "message": msg})


@router.post("/expand-character", response_model=ApiResponse)
async def expand_character_endpoint(payload: ExpandCharacterRequest):
    try:
        result = await anyio.to_thread.run_sync(
            lambda: ai_expand_character(
                name=payload.name,
                brief=payload.brief,
                style=payload.style,
                market_mode=payload.market_mode,
            )
        )
        return ApiResponse(status="success", data=result)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _err(str(exc), 502)


@router.post("/decompose-novel", response_model=ApiResponse)
async def decompose_novel_endpoint(payload: DecomposeNovelRequest):
    try:
        result = await anyio.to_thread.run_sync(
            lambda: ai_decompose_novel_to_drama(
                novel_text=payload.novel_text,
                title=payload.title,
                total_episodes=payload.total_episodes,
                episode_duration=payload.episode_duration,
                style=payload.style,
                market_mode=payload.market_mode,
            )
        )
        return ApiResponse(status="success", data=result)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _err(str(exc), 502)


@router.post("/review-script", response_model=ApiResponse)
async def review_script_endpoint(payload: ReviewScriptRequest):
    try:
        script_text = payload.script_text.strip()
        scenes: list[dict[str, Any]] = []
        if payload.task_id:
            if not script_text:
                script_doc = await mongodb.find_one("scripts", {"task_id": payload.task_id}) or await mongodb.find_one(
                    "scripts", {"_id": f"script_{payload.task_id}"}
                )
                if script_doc:
                    script_text = str(script_doc.get("content") or "")
            raw_scenes = await mongodb.find_many("scenes", {"task_id": payload.task_id}, limit=100)
            scenes = list(raw_scenes or [])
        if not script_text:
            return _err("未提供可审查的剧本内容", 400)
        report = await anyio.to_thread.run_sync(
            lambda: ai_review_script(script_text, scenes=scenes, duration=payload.duration)
        )
        if payload.task_id:
            await mongodb.update_one(
                "video_tasks",
                {"_id": payload.task_id},
                {"metadata.script_review_38": report, "updated_at": datetime.now(UTC)},
            )
        return ApiResponse(status="success", data=report)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _err(str(exc), 502)


@router.post("/doctor-script", response_model=ApiResponse)
async def doctor_script_endpoint(payload: DoctorScriptRequest):
    try:
        script_text = payload.script_text.strip()
        title = payload.title.strip()
        if payload.task_id and not script_text:
            script_doc = await mongodb.find_one("scripts", {"task_id": payload.task_id}) or await mongodb.find_one(
                "scripts", {"_id": f"script_{payload.task_id}"}
            )
            if script_doc:
                script_text = str(script_doc.get("content") or "")
                title = title or str(script_doc.get("title") or "")
        if not script_text:
            return _err("未提供可精修的剧本内容", 400)
        result = await anyio.to_thread.run_sync(
            lambda: ai_doctor_script(script_text, title=title, duration=payload.duration)
        )
        if payload.task_id and payload.save_to_task and result.get("polished_script"):
            await mongodb.update_one(
                "scripts",
                {"task_id": payload.task_id},
                {"content": result["polished_script"], "updated_at": datetime.now(UTC)},
            )
            await mongodb.update_one(
                "video_tasks",
                {"_id": payload.task_id},
                {"metadata.script_review_38": result.get("after_review"), "updated_at": datetime.now(UTC)},
            )
        return ApiResponse(status="success", data=result)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _err(str(exc), 502)


@router.post("/scene-grid-prompt", response_model=ApiResponse)
async def scene_grid_prompt_endpoint(payload: SceneGridPromptRequest):
    try:
        data = await anyio.to_thread.run_sync(
            lambda: ai_generate_scene_grid_prompt(
                scene_name=payload.scene_name,
                space_type=payload.space_type,
                era_and_style=payload.era_and_style,
                layout_and_materials=payload.layout_and_materials,
                lighting_and_kelvin=payload.lighting_and_kelvin,
            )
        )
        return ApiResponse(status="success", data=data)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _err(str(exc), 502)


@router.post("/prop-sheet-prompt", response_model=ApiResponse)
async def prop_sheet_prompt_endpoint(payload: PropSheetPromptRequest):
    try:
        data = await anyio.to_thread.run_sync(
            lambda: ai_generate_prop_sheet_prompt(
                prop_name=payload.prop_name,
                material_and_color=payload.material_and_color,
                size_vs_palm=payload.size_vs_palm,
                key_detail=payload.key_detail,
                style=payload.style,
            )
        )
        return ApiResponse(status="success", data=data)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _err(str(exc), 502)


@router.post("/sanitize-video-prompt", response_model=ApiResponse)
async def sanitize_video_prompt_endpoint(payload: SanitizeVideoPromptRequest):
    try:
        data = await anyio.to_thread.run_sync(
            lambda: ai_upgrade_video_prompt(
                raw_prompt=payload.raw_prompt,
                speaker=payload.speaker,
                character_ids=payload.character_ids,
                all_character_names=payload.all_character_names,
                shot_type=payload.shot_type,
                camera_motion=payload.camera_motion,
            )
        )
        return ApiResponse(status="success", data=data)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _err(str(exc), 502)


@router.post("/render-asset-image", response_model=ApiResponse)
async def render_craft_asset_endpoint(payload: RenderCraftAssetRequest):
    try:
        data = await render_craft_asset_image(
            prompt=payload.prompt,
            negative_prompt=payload.negative_prompt,
            category=payload.category,
            ratio=payload.ratio,
        )
        return ApiResponse(status="success", data=data)
    except ValueError as exc:
        return _err(str(exc), 400)
    except Exception as exc:
        return _err(str(exc), 502)
