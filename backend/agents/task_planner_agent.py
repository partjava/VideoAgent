import anyio

from models.task_model import VideoTaskStatus
from services.provider_factory import get_llm_service

from agents.agent_utils import (
    get_required_task,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
)


class TaskPlannerAgent:
    agent_name = "TaskPlannerAgent"

    async def run(self, task_id: str) -> dict[str, object]:
        try:
            task = await get_required_task(task_id)
            
            # 检查任务是否已经有了规划
            existing_plan = task.get("metadata", {}).get("task_plan") if isinstance(task.get("metadata"), dict) else None
            if existing_plan and task.get("topic"):
                await mark_agent_success(
                    task_id,
                    self.agent_name,
                    progress=15,
                    status=VideoTaskStatus.PLANNING,
                    message="Skipped TaskPlannerAgent (plan already exists)",
                )
                return existing_plan

            await mark_agent_start(
                task_id,
                self.agent_name,
                progress=5,
                status=VideoTaskStatus.PLANNING,
            )

            llm_service = get_llm_service()
            _user_input = str(task["user_input"])
            _duration = task.get("duration") if isinstance(task.get("duration"), int) else None
            _style = task.get("style") if isinstance(task.get("style"), str) else None
            _platform = task.get("platform") if isinstance(task.get("platform"), str) else None
            _ratio = str(task.get("ratio") or "9:16")
            _generation_mode = str(task.get("generation_mode") or "full_dynamic")
            task_plan = await anyio.to_thread.run_sync(
                lambda _u=_user_input, _d=_duration, _s=_style, _p=_platform, _r=_ratio, _g=_generation_mode:
                    llm_service.plan_task(
                        user_input=_u,
                        duration=_d,
                        style=_s,
                        platform=_p,
                        ratio=_r,
                        generation_mode=_g,
                    )
            )

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=15,
                status=VideoTaskStatus.PLANNING,
                message="Real task plan generated",
                extra_fields={
                    "topic": task_plan.get("topic"),
                    "duration": task_plan.get("duration"),
                    "style": task_plan.get("style"),
                    "platform": task_plan.get("platform"),
                    "ratio": task_plan.get("ratio"),
                    "generation_mode": task_plan.get("generation_mode", "full_dynamic"),
                    "provider_mode": "real",
                    "metadata.task_plan": task_plan,
                },
            )
            return task_plan
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
