import anyio
from datetime import UTC, datetime
from typing import Any

from core.database import mongodb
from models.task_model import VideoTaskStatus
from services.provider_factory import get_llm_service

from agents.agent_utils import (
    SCENES_COLLECTION,
    get_required_task,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
)
from agents.storyboard_agent import validate_storyboard_scenes


class DialoguePolishAgent:
    agent_name = "DialoguePolishAgent"

    async def run(self, task_id: str) -> list[dict[str, Any]]:
        try:
            task = await get_required_task(task_id)
            await mark_agent_start(
                task_id,
                self.agent_name,
                progress=55,
                status=VideoTaskStatus.STORYBOARD_DONE,
            )

            scenes = await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=100)
            task_scenes = [scene for scene in scenes]
            task_scenes.sort(key=lambda scene: int(scene.get("scene_index", 0)))
            if not task_scenes:
                raise ValueError(f"Scenes not found for task: {task_id}")
            if all(
                scene.get("status") == "dialogue_done"
                and scene.get("speaker")
                and scene.get("voiceover")
                for scene in task_scenes
            ):
                validate_storyboard_scenes(
                    task_scenes,
                    target_duration=task.get("duration") if isinstance(task.get("duration"), int) else None,
                )
                await mark_agent_success(
                    task_id,
                    self.agent_name,
                    progress=60,
                    status=VideoTaskStatus.STORYBOARD_DONE,
                    message="Skipped DialoguePolishAgent (dialogue already polished)",
                    extra_fields={"metadata.dialogue_count": len(task_scenes)},
                )
                return task_scenes

            llm_service = get_llm_service()
            _target_duration = task.get("duration") if isinstance(task.get("duration"), int) else None
            dialogues = await anyio.to_thread.run_sync(
                lambda _scenes=task_scenes, _td=_target_duration:
                    llm_service.polish_dialogue(_scenes, target_duration=_td)
            )
            dialogue_by_scene_id = {str(item.get("scene_id")): item for item in dialogues}

            updated_scenes: list[dict[str, Any]] = []
            now = datetime.now(UTC)
            for scene in task_scenes:
                scene_id = str(scene.get("scene_id"))
                dialogue = dialogue_by_scene_id.get(scene_id)
                if dialogue is None:
                    raise ValueError(f"Dialogue polish result missing scene: {scene_id}")

                values = {
                    "speaker": str(dialogue.get("speaker") or "旁白").strip(),
                    "voiceover": str(dialogue.get("voiceover") or "").strip(),
                    "subtitle": str(dialogue.get("subtitle") or dialogue.get("voiceover") or "").strip(),
                    "status": "dialogue_done",
                    "updated_at": now,
                }
                scene.update(values)
                await mongodb.update_one(SCENES_COLLECTION, {"_id": f"{task_id}_{scene_id}"}, values)
                updated_scenes.append(scene)

            validate_storyboard_scenes(
                updated_scenes,
                target_duration=task.get("duration") if isinstance(task.get("duration"), int) else None,
            )

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=60,
                status=VideoTaskStatus.STORYBOARD_DONE,
                message="Dialogue polished successfully",
                extra_fields={"metadata.dialogue_count": len(updated_scenes)},
            )
            return updated_scenes
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
