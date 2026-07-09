from datetime import UTC, datetime
from uuid import uuid4

from core.database import mongodb
from models.agent_log_model import CreateAgentLogRequest
from models.task_model import (
    CreateVideoTaskRequest,
    CreateVideoTaskResponse,
    VideoTask,
    VideoTaskProgressResponse,
    VideoTaskStatus,
)
from services.agent_log_service import agent_log_service


VIDEO_TASKS_COLLECTION = "video_tasks"


def _build_task_id() -> str:
    # 生成当前阶段使用的唯一任务 ID，后续接口都通过它查询任务。
    return f"task_{uuid4().hex[:12]}"


def _task_to_document(task: VideoTask) -> dict[str, object]:
    # MongoDB 使用 _id 作为主键，这里把业务 task_id 同步写入 _id。
    document = task.model_dump()
    document["_id"] = task.task_id
    return document


def _document_to_task(document: dict[str, object]) -> dict[str, object]:
    # 对外接口统一返回 task_id，不暴露 MongoDB 内部字段 _id。
    task = document.copy()
    if "_id" in task:
        task["task_id"] = str(task.pop("_id"))
    if isinstance(task.get("status"), VideoTaskStatus):
        task["status"] = task["status"].value
    return task


class TaskService:
    async def create_task(self, payload: CreateVideoTaskRequest) -> CreateVideoTaskResponse:
        # 本阶段只创建任务记录和基础日志，不启动 Agent，也不调用真实付费模型。
        now = datetime.now(UTC)
        task = VideoTask(
            task_id=_build_task_id(),
            user_input=payload.user_input,
            duration=payload.duration,
            style=payload.style,
            platform=payload.platform,
            ratio=payload.ratio,
            generation_mode=payload.generation_mode,
            provider_mode="real",
            status=VideoTaskStatus.CREATED,
            progress=0,
            current_agent=None,
            created_at=now,
            updated_at=now,
        )

        await mongodb.insert_one(VIDEO_TASKS_COLLECTION, _task_to_document(task))
        await agent_log_service.write_log(
            CreateAgentLogRequest(
                task_id=task.task_id,
                agent_name="TaskService",
                status="success",
                input_summary=payload.user_input,
                output_summary="任务记录已创建",
                message="基础任务已写入 video_tasks 集合",
            )
        )

        return CreateVideoTaskResponse(
            task_id=task.task_id,
            status=task.status.value,
            progress=task.progress,
        )

    async def get_task(self, task_id: str) -> dict[str, object] | None:
        document = await mongodb.find_one(VIDEO_TASKS_COLLECTION, {"_id": task_id})
        if document is None:
            return None
        return _document_to_task(document)

    async def list_tasks(self, limit: int = 50) -> list[dict[str, object]]:
        documents = await mongodb.find_many(VIDEO_TASKS_COLLECTION, limit=limit)
        return [_document_to_task(document) for document in documents]

    async def update_task_fields(self, task_id: str, values: dict[str, object]) -> bool:
        # Agent Pipeline 只通过这个方法更新任务状态、进度和当前 Agent。
        values["updated_at"] = datetime.now(UTC)
        modified_count = await mongodb.update_one(VIDEO_TASKS_COLLECTION, {"_id": task_id}, values)
        return modified_count > 0

    async def clear_task_assets(self, task_id: str) -> None:
        import shutil
        from pathlib import Path

        # 1. 清理数据库记录（保留 video_tasks 核心记录）
        await mongodb.delete_one("scripts", {"task_id": task_id})
        await mongodb.delete_one("scripts", {"_id": f"script_{task_id}"})
        await mongodb.delete_one("voices", {"task_id": task_id})
        await mongodb.delete_one("voices", {"_id": f"voice_{task_id}"})
        
        # 删除所有资产和分镜
        # 使用 find_many 获取并批量删除，因为 mongo 没有 delete_many，但我们有 delete_one 循环或直接在底层处理
        assets = await mongodb.find_many("assets", {"task_id": task_id}, limit=500)
        for a in assets:
            await mongodb.delete_one("assets", {"_id": a["_id"]})

        scenes = await mongodb.find_many("scenes", {"task_id": task_id}, limit=200)
        for s in scenes:
            await mongodb.delete_one("scenes", {"_id": s["_id"]})
                
        await mongodb.delete_one("render_outputs", {"task_id": task_id})

        # 2. 物理清理资产文件夹
        backend_dir = Path(__file__).resolve().parents[1]
        assets_dir = backend_dir / "assets" / task_id
        outputs_dir = backend_dir / "outputs" / task_id
        if assets_dir.exists():
            shutil.rmtree(assets_dir, ignore_errors=True)
        if outputs_dir.exists():
            shutil.rmtree(outputs_dir, ignore_errors=True)

    async def delete_task(self, task_id: str) -> bool:
        # 先清理相关资产和文件
        await self.clear_task_assets(task_id)
        # 删除 video_tasks 中的任务记录
        deleted_count = await mongodb.delete_one(VIDEO_TASKS_COLLECTION, {"_id": task_id})
        return deleted_count > 0

    async def get_task_progress(self, task_id: str) -> VideoTaskProgressResponse | None:
        # 当前进度来自 video_tasks 基础字段，后续 Agent 接入后再更新 current_agent。
        task = await self.get_task(task_id)
        if task is None:
            return None

        task_status = str(task.get("status", VideoTaskStatus.CREATED.value))
        progress = int(task.get("progress", 0))
        current_agent = task.get("current_agent")

        return VideoTaskProgressResponse(
            task_id=str(task["task_id"]),
            task_status=task_status,
            progress=progress,
            current_agent=current_agent if isinstance(current_agent, str) else None,
            message=_progress_message(task_status),
        )


def _progress_message(task_status: str) -> str:
    # 给前端展示用的基础中文状态说明。
    if task_status == VideoTaskStatus.CREATED.value:
        return "任务已创建，等待执行"
    if task_status == VideoTaskStatus.FAILED.value:
        return "任务执行失败"
    if task_status == VideoTaskStatus.SUCCESS.value:
        return "任务已完成"
    if task_status == VideoTaskStatus.PLANNING.value:
        return "任务规划中"
    if task_status == VideoTaskStatus.SCRIPT_DONE.value:
        return "脚本已生成"
    if task_status == VideoTaskStatus.STORYBOARD_DONE.value:
        return "分镜已生成"
    if task_status == VideoTaskStatus.IMAGE_GENERATING.value:
        return "图片处理中"
    if task_status == VideoTaskStatus.VOICE_GENERATING.value:
        return "配音模块预留中"
    if task_status == VideoTaskStatus.SUBTITLE_GENERATING.value:
        return "字幕模块预留中"
    if task_status == VideoTaskStatus.EDITING.value:
        return "剪辑模块预留中"
    if task_status == VideoTaskStatus.CHECKING.value:
        return "质检模块预留中"
    return "任务处理中"


task_service = TaskService()
