from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agents.pipeline import run_pipeline
from agents.video_agent import VideoAgent
from core.database import mongodb
from models.response_model import ApiResponse
from models.task_model import VideoTaskStatus
from services.generation_contract import needs_video
from services.task_service import task_service


router = APIRouter(prefix="/api/video", tags=["shoot-review"])
WAITING = VideoTaskStatus.AWAITING_SHOOT_REVIEW.value
TRIAL_FIELD = "metadata.shoot_review_trial"


class TrialRequest(BaseModel):
    scene_id: str = Field(min_length=1)


def review_error(exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=404 if isinstance(exc, LookupError) else 409,
                        content={"status": "error", "message": str(exc)})


async def claim_shoot_review_approval(task_id: str, *, allow_series: bool = False) -> None:
    task = await task_service.get_task(task_id)
    if not task:
        raise LookupError("任务不存在")
    if (task.get("series_id") or (task.get("metadata") or {}).get("series_context")) and not allow_series:
        raise ValueError("请在连载工作台确认本集开拍")
    updated = await mongodb.update_one("video_tasks", {"_id": task_id, "status": WAITING, TRIAL_FIELD: None}, {
        "status": VideoTaskStatus.VIDEO_GENERATING.value,
        "metadata.shoot_review_approved": True,
        "current_agent": "VideoAgent",
        "updated_at": datetime.now(UTC),
    })
    if not updated:
        raise ValueError("任务已开拍、未进入预审，或仍在试拍")


async def run_trial(task_id: str, scene_id: str, started_at: str) -> None:
    result = {"scene_id": scene_id, "status": "success"}
    try:
        await VideoAgent().run(task_id, trial_scene_id=scene_id)
    except Exception as exc:
        result.update(status="failed", error=str(exc))
    finally:
        await mongodb.update_one("video_tasks", {"_id": task_id, f"{TRIAL_FIELD}.started_at": started_at}, {
            TRIAL_FIELD: None,
            "metadata.last_shoot_trial": result,
            "updated_at": datetime.now(UTC),
        })


@router.post("/{task_id}/shoot-review/trial", response_model=ApiResponse)
async def trial_shot(task_id: str, payload: TrialRequest, background_tasks: BackgroundTasks):
    task = await task_service.get_task(task_id)
    if not task:
        return review_error(LookupError("任务不存在"))
    scene = await mongodb.find_one("scenes", {"task_id": task_id, "scene_id": payload.scene_id})
    if not scene or not needs_video(scene):
        return review_error(ValueError("请选择本任务中需要生成动态片段的镜头试拍"))
    started_at = datetime.now(UTC).isoformat()
    trial = {"scene_id": payload.scene_id, "started_at": started_at}
    updated = await mongodb.update_one("video_tasks", {"_id": task_id, "status": WAITING, TRIAL_FIELD: None}, {
        TRIAL_FIELD: trial,
        "metadata.last_shoot_trial": None,
        "updated_at": datetime.now(UTC),
    })
    if not updated:
        return review_error(ValueError("任务未等待预审，或已有镜头正在试拍"))
    background_tasks.add_task(run_trial, task_id, payload.scene_id, started_at)
    return ApiResponse(status="success", data=trial)


@router.post("/{task_id}/shoot-review/approve", response_model=ApiResponse)
async def approve_shoot(task_id: str, background_tasks: BackgroundTasks):
    try:
        await claim_shoot_review_approval(task_id)
    except (LookupError, ValueError) as exc:
        return review_error(exc)
    background_tasks.add_task(run_pipeline, task_id, resume_shoot=True)
    return ApiResponse(status="success", data={"status": VideoTaskStatus.VIDEO_GENERATING.value})
