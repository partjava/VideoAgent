from datetime import UTC, datetime

from core.database import mongodb
from models.script_model import Script
from models.task_model import VideoTaskStatus
from services.provider_factory import get_llm_service

from agents.agent_utils import (
    SCRIPTS_COLLECTION,
    get_required_task,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    with_document_id,
)


class ScriptAgent:
    agent_name = "ScriptAgent"

    async def run(self, task_id: str) -> dict[str, object]:
        try:
            task = await get_required_task(task_id)
            
            # 检查脚本是否已生成
            existing_script = await mongodb.find_one(SCRIPTS_COLLECTION, {"task_id": task_id})
            if not existing_script:
                existing_script = await mongodb.find_one(SCRIPTS_COLLECTION, {"_id": f"script_{task_id}"})
            
            if existing_script:
                await mark_agent_success(
                    task_id,
                    self.agent_name,
                    progress=30,
                    status=VideoTaskStatus.SCRIPT_DONE,
                    message="Skipped ScriptAgent (script already exists)",
                    extra_fields={"metadata.script_id": existing_script.get("_id") or f"script_{task_id}"},
                )
                return existing_script

            await mark_agent_start(task_id, self.agent_name, progress=20)

            task_plan = task.get("metadata", {}).get("task_plan") if isinstance(task.get("metadata"), dict) else None
            if not isinstance(task_plan, dict):
                task_plan = {
                    "source": "deepseek",
                    "topic": task.get("topic") or task.get("user_input"),
                    "duration": task.get("duration") or 60,
                    "style": task.get("style") or "real",
                    "platform": task.get("platform") or "抖音",
                    "ratio": task.get("ratio") or "9:16",
                    "generation_mode": task.get("generation_mode") or "full_dynamic",
                }

            script_data = get_llm_service().generate_script(task_plan)
            now = datetime.now(UTC)
            script = Script(
                script_id=f"script_{task_id}",
                task_id=task_id,
                title=script_data.get("title") if isinstance(script_data.get("title"), str) else None,
                hook=script_data.get("hook") if isinstance(script_data.get("hook"), str) else None,
                content=str(script_data.get("content", "")),
                ending=script_data.get("ending") if isinstance(script_data.get("ending"), str) else None,
                publish_copy=script_data.get("publish_copy") if isinstance(script_data.get("publish_copy"), str) else None,
                source=str(script_data.get("source", "deepseek")),
                status="created",
                created_at=now,
                updated_at=now,
            )
            await mongodb.insert_one(
                SCRIPTS_COLLECTION,
                with_document_id(script.model_dump(), script.script_id),
            )

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=30,
                status=VideoTaskStatus.SCRIPT_DONE,
                message="Real script saved",
                extra_fields={"metadata.script_id": script.script_id},
            )
            return script.model_dump()
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
