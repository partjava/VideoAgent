from datetime import UTC, datetime
from typing import Any

from core.database import mongodb
from models.task_model import VideoTaskStatus
from services.provider_factory import get_llm_service

from agents.agent_utils import (
    SCENES_COLLECTION,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    get_required_task,
)


class PromptAgent:
    agent_name = "PromptAgent"

    async def run(self, task_id: str) -> list[dict[str, Any]]:
        try:
            task = await get_required_task(task_id)
            scenes = await mongodb.find_many(SCENES_COLLECTION, limit=100)
            task_scenes = [scene for scene in scenes if scene.get("task_id") == task_id]
            task_scenes.sort(key=lambda s: int(s.get("scene_index", 0)))
            if not task_scenes:
                raise ValueError(f"Scenes not found for task: {task_id}")

            # 检查提示词是否已全部生成
            if all(scene.get("image_prompt") for scene in task_scenes):
                await mark_agent_success(
                    task_id,
                    self.agent_name,
                    progress=70,
                    status=VideoTaskStatus.IMAGE_GENERATING,
                    message="Skipped PromptAgent (prompts already exist)",
                    extra_fields={"metadata.prompt_count": len(task_scenes)},
                )
                return task_scenes

            await mark_agent_start(task_id, self.agent_name, progress=60)
            ratio = str(task.get("ratio", "9:16"))
            style = str(task.get("style") or task.get("metadata", {}).get("style") or "")
            prompts = get_llm_service().generate_image_prompts(
                task_scenes,
                ratio=ratio,
                style=style or None,
            )
            now = datetime.now(UTC)
            for prompt in prompts:
                scene_id = str(prompt["scene_id"])
                await mongodb.update_one(
                    SCENES_COLLECTION,
                    {"_id": f"{task_id}_{scene_id}"},
                    {
                        "image_prompt": prompt.get("image_prompt"),
                        "negative_prompt": prompt.get("negative_prompt"),
                        "video_prompt": prompt.get("video_prompt"),
                        "motion_beats": prompt.get("motion_beats"),
                        "first_frame_focus": prompt.get("first_frame_focus"),
                        "last_frame_for_transition": prompt.get("last_frame_for_transition"),
                        "continuity_note": prompt.get("continuity_note"),
                        "status": "prompt_done",
                        "updated_at": now,
                    },
                )

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=70,
                status=VideoTaskStatus.IMAGE_GENERATING,
                message="Real prompts saved",
                extra_fields={"metadata.prompt_count": len(prompts)},
            )
            return prompts
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
