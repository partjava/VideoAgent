import anyio
from datetime import UTC, datetime
from typing import Any

from core.database import mongodb
from models.task_model import VideoTaskStatus
from services.provider_factory import get_llm_service
from services.camera_plan import normalize_camera_plan
from services.generation_contract import PAIRED_GENERATION_MODE

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
            scenes = await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=100)
            task_scenes = [scene for scene in scenes]
            task_scenes.sort(key=lambda s: int(s.get("scene_index", 0)))
            if not task_scenes:
                raise ValueError(f"Scenes not found for task: {task_id}")

            from services.generation_contract import validate_motion_beats
            paired = task.get("generation_mode") == PAIRED_GENERATION_MODE
            for index, scene in enumerate(task_scenes):
                if paired:
                    scene["camera_section_offset"] = 5 * (index % 2)
                    scene["camera_section_number"] = index // 2 + 1
            # 旧字符串节拍必须重新生成，不能仅凭图片提示词复用。
            def complete(scene):
                try:
                    validate_motion_beats(scene.get("motion_beats"), float(scene["duration"]), detailed=True, per_second=True)
                    return bool(scene.get("image_prompt") and scene.get("video_prompt"))
                except (ValueError, TypeError, KeyError):
                    return False

            # 检查提示词是否已全部生成
            if all(complete(scene) for scene in task_scenes):
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
            llm_service = get_llm_service()
            _style = str(task.get("style") or task.get("metadata", {}).get("style") or "") or None
            world = task.get("metadata", {}).get("task_plan") or {}
            bible = {"characters": world.get("characters", []), "visual_world": world.get("visual_world", "")}
            prompts = await anyio.to_thread.run_sync(
                lambda _scenes=task_scenes, _ratio=ratio, _style=_style:
                    llm_service.generate_image_prompts(_scenes, ratio=_ratio, style=_style, visual_bible=bible)
            )
            expected = {str(s["scene_id"]): s for s in task_scenes}
            if len(prompts) != len(expected) or {str(p.get("scene_id")) for p in prompts} != set(expected):
                raise ValueError("Prompt results must match every scene exactly once")
            import json
            fixed = json.dumps(bible, ensure_ascii=False)
            prompts_by_id = {str(p["scene_id"]): p for p in prompts}
            for index in range(0, len(task_scenes), 2) if paired else ():
                first, second = task_scenes[index:index + 2]
                first_prompt = prompts_by_id[str(first["scene_id"])]
                plan = normalize_camera_plan(first_prompt.get("camera_plan"), 10)
                for scene in (first, second):
                    result = prompts_by_id[str(scene["scene_id"])]
                    result["camera_plan"] = plan
                    result["camera_section_offset"] = scene["camera_section_offset"]
                    result["camera_section_number"] = scene["camera_section_number"]
                first_prompt["image_prompt"] = (
                    f"首帧构图：{plan['opening']}。景别：{plan['framing']}。机位：{plan['viewpoint']}。"
                    f"关键可见位置：{plan['focus']}。\n" + str(first_prompt.get("image_prompt") or "")
                )
            for prompt in prompts:
                scene = expected[str(prompt["scene_id"])]
                beats = prompt.get("motion_beats")
                validate_motion_beats(beats, float(scene["duration"]), detailed=True, per_second=True)
                if scene.get("start_state"):
                    beats[0]["start_state"] = scene["start_state"]
                if scene.get("end_state"):
                    beats[-1]["end_state"] = scene["end_state"]
                for prior, current in zip(beats, beats[1:]):
                    current["start_state"] = prior["end_state"]
                if not prompt.get("image_prompt") or not prompt.get("video_prompt"):
                    raise ValueError("Missing image/video prompt")
                prompt["image_prompt"] = f"固定角色与世界设定（只绘制本镜实际出场角色）：{fixed}\n{prompt["image_prompt"]}"
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
                        "camera_plan": prompt.get("camera_plan"),
                        "camera_section_offset": prompt.get("camera_section_offset"),
                        "camera_section_number": prompt.get("camera_section_number"),
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
