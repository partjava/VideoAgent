from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from agents.pipeline import run_pipeline
from core.database import mongodb
from models.response_model import ApiResponse
from models.task_model import CreateVideoTaskRequest, VideoTaskStatus
from services.task_service import task_service


router = APIRouter(prefix="/api/video", tags=["video"])


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "message": message,
        },
    )


@router.get("/status")
async def video_route_status() -> dict[str, str]:
    return {
        "status": "ok",
        "message": "video routes are ready for real generation",
    }


@router.post("/create", response_model=ApiResponse)
async def create_video_task(payload: CreateVideoTaskRequest) -> ApiResponse | JSONResponse:
    try:
        task = await task_service.create_task(payload)
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)
    return ApiResponse(status="success", data=task.model_dump())


@router.get("/list", response_model=ApiResponse)
async def list_video_tasks() -> ApiResponse | JSONResponse:
    try:
        tasks = await task_service.list_tasks()
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)
    return ApiResponse(status="success", data=tasks)


async def run_pipeline_wrapper(task_id: str) -> None:
    print(f"[Pipeline Wrapper] Starting pipeline for {task_id}")
    try:
        result = await run_pipeline(task_id)
        print(f"[Pipeline Wrapper] Pipeline finished: {result.get('status')}")
    except Exception as exc:
        print(f"[Pipeline Wrapper] Pipeline failed for task {task_id}: {exc}")
        try:
            await task_service.update_task_fields(
                task_id,
                {
                    "status": VideoTaskStatus.FAILED.value,
                    "metadata.last_error": str(exc),
                },
            )
        except Exception as inner_exc:
            print(f"[BackgroundTasks] Failed to update task error status: {inner_exc}")


@router.post("/{task_id}/run-pipeline", response_model=ApiResponse)
async def run_video_pipeline(
    task_id: str,
    background_tasks: BackgroundTasks,
    run_async: bool = True,
    resume: bool = True,
) -> ApiResponse | JSONResponse:
    try:
        task = await task_service.get_task(task_id)
        if task is None:
            return error_response("Task not found", 404)

        if not resume:
            # 清理历史资产文件和数据库，准备重头开始生成
            await task_service.clear_task_assets(task_id)
            await task_service.update_task_fields(
                task_id,
                {
                    "status": VideoTaskStatus.CREATED.value,
                    "progress": 0,
                    "current_agent": None,
                },
            )

        if run_async:
            background_tasks.add_task(run_pipeline_wrapper, task_id)
            return ApiResponse(status="success", data={"message": "Pipeline execution started."})

        result = await run_pipeline(task_id)
        return ApiResponse(status="success", data=result)
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)
    except ValueError as exc:
        return error_response(str(exc), 400)


@router.get("/{task_id}/script", response_model=ApiResponse)
async def get_video_task_script(task_id: str) -> ApiResponse | JSONResponse:
    try:
        script = await mongodb.find_one("scripts", {"task_id": task_id})
        if script is None:
            script = await mongodb.find_one("scripts", {"_id": f"script_{task_id}"})
        if script is None:
            return error_response("Script not found", 404)
        if "_id" in script:
            script["script_id"] = str(script.pop("_id"))
        return ApiResponse(status="success", data=script)
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)


@router.get("/{task_id}/storyboard", response_model=ApiResponse)
async def get_video_task_storyboard(task_id: str) -> ApiResponse | JSONResponse:
    try:
        scenes = await mongodb.find_many("scenes", {"task_id": task_id}, limit=100)
        task_scenes = [scene for scene in scenes]
        task_scenes.sort(key=lambda scene: int(scene.get("scene_index", 0)))
        for scene in task_scenes:
            if "_id" in scene:
                scene["_id"] = str(scene["_id"])
        return ApiResponse(status="success", data=task_scenes)
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)


@router.get("/{task_id}", response_model=ApiResponse)
async def get_video_task(task_id: str) -> ApiResponse | JSONResponse:
    try:
        task = await task_service.get_task(task_id)
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)
    if task is None:
        return error_response("Task not found", 404)
    return ApiResponse(status="success", data=task)


@router.get("/{task_id}/progress", response_model=ApiResponse)
async def get_video_task_progress(task_id: str) -> ApiResponse | JSONResponse:
    try:
        progress = await task_service.get_task_progress(task_id)
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)
    if progress is None:
        return error_response("Task not found", 404)
    return ApiResponse(status="success", data=progress.model_dump())


@router.delete("/{task_id}", response_model=ApiResponse)
async def delete_video_task(task_id: str) -> ApiResponse | JSONResponse:
    try:
        task = await task_service.get_task(task_id)
        if task is None:
            return error_response("Task not found", 404)
        deleted = await task_service.delete_task(task_id)
        return ApiResponse(status="success", data={"deleted": deleted})
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)
