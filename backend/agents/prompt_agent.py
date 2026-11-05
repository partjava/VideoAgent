import json
import anyio
from datetime import UTC, datetime
from typing import Any

from core.database import mongodb
from models.task_model import VideoTaskStatus
from services.provider_factory import get_llm_service
from services.task_service import task_service
from services.camera_plan import normalize_camera_plan
from services.generation_contract import NATIVE_GENERATION_MODE, PAIRED_GENERATION_MODE
from services.visual_style import PHOTO_RULES, PHOTO_EXCLUSIONS, lock_visual_style

DEFAULT_IMAGE_NEGATIVE_PROMPT = "多余肢体，手指结构混乱，五官错位，无关文字，水印，主体边缘异常断裂"
DEFAULT_VIDEO_NEGATIVE_PROMPT = "道具瞬移，非计划抖动，突然变脸，肢体穿模，多余肢体，服装颜色突变，画面撕裂，非计划倒放"

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
            native = task.get("generation_mode") == NATIVE_GENERATION_MODE
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
            print(f"[PromptAgent] Task {task_id}: generating visual prompts for {len(task_scenes)} scenes via DeepSeek...", flush=True)
            ratio = str(task.get("ratio", "9:16"))
            llm_service = get_llm_service()
            _style = str(task.get("style") or task.get("metadata", {}).get("style") or "") or None
            world = task.get("metadata", {}).get("task_plan") or {}
            bible = {"characters": world.get("characters", []), "visual_world": world.get("visual_world", "")}
            fixed = json.dumps(bible, ensure_ascii=False)

            def generate(batch):
                return llm_service.generate_image_prompts(batch, ratio=ratio, style=_style, visual_bible=bible)

            def prepare(prompt, scene):
                beats = prompt.get("motion_beats")
                validate_motion_beats(beats, float(scene["duration"]), detailed=True, per_second=True)
                if not native and scene.get("start_state"):
                    beats[0]["start_state"] = scene["start_state"]
                if not native and scene.get("end_state"):
                    beats[-1]["end_state"] = scene["end_state"]
                for prior, current in zip(beats, beats[1:]):
                    current["start_state"] = prior["end_state"]
                if not prompt.get("image_prompt") or not prompt.get("video_prompt"):
                    raise ValueError("Missing image/video prompt")
                image_negative = prompt.get("image_negative_prompt") or prompt.get("negative_prompt")
                video_negative = prompt.get("video_negative_prompt")
                prompt["image_negative_prompt"] = (
                    image_negative.strip() if isinstance(image_negative, str) else ""
                ) or DEFAULT_IMAGE_NEGATIVE_PROMPT
                prompt["video_negative_prompt"] = (
                    video_negative.strip() if isinstance(video_negative, str) else ""
                ) or DEFAULT_VIDEO_NEGATIVE_PROMPT
                prompt["image_prompt"] = f"固定角色与世界设定（只绘制本镜实际出场角色）：{fixed}\n{prompt['image_prompt']}"
                if scene.get("start_state"):
                    prompt["image_prompt"] = f"首帧时刻的画面状态：{scene['start_state']}\n{prompt['image_prompt']}"
                if _style == "短剧":
                    style_lock = PHOTO_RULES
                    prompt["image_prompt"] = lock_visual_style(prompt["image_prompt"], _style)
                    prompt["video_prompt"] = lock_visual_style(prompt["video_prompt"], _style)
                    prompt["continuity_note"] = f"{style_lock}{prompt.get('continuity_note') or ''}"
                    prompt["image_negative_prompt"] = PHOTO_EXCLUSIONS + "，" + str(prompt.get("image_negative_prompt") or "")
                    prompt["video_negative_prompt"] = PHOTO_EXCLUSIONS + "，" + str(prompt.get("video_negative_prompt") or "")

            if native:
                prompts = []
                previous_context = None
                rebuilding = False
                for index, scene in enumerate(task_scenes, start=1):
                    if not rebuilding and complete(scene):
                        prompt = scene
                    else:
                        rebuilding = True
                        await task_service.update_task_fields(task_id, {
                            "metadata.prompt_count": index - 1,
                            "metadata.prompt_active_segment": index,
                        })
                        current = {**scene, "previous_segment_context": previous_context}
                        batch = await anyio.to_thread.run_sync(generate, [current], abandon_on_cancel=True)
                        if len(batch) != 1 or str(batch[0].get("scene_id")) != str(scene["scene_id"]):
                            raise ValueError("Prompt result must match the requested segment")
                        prompt = batch[0]
                        prompt["previous_segment_context"] = previous_context
                        prepare(prompt, scene)
                        await self._save_prompt(task_id, prompt)
                    previous_context = {
                        "scene_index": scene["scene_index"],
                        "visual_description": scene.get("visual_description"),
                        "voiceover": scene.get("voiceover"),
                        "key_action": prompt.get("video_prompt"),
                        "final_second": prompt["motion_beats"][-1],
                        "closing_composition": prompt.get("last_frame_for_transition"),
                    }
                    prompts.append(prompt)
            else:
                prompts = await anyio.to_thread.run_sync(generate, task_scenes, abandon_on_cancel=True)
            print(f"[PromptAgent] Task {task_id}: prompts successfully received ({len(prompts)} scenes)", flush=True)
            expected = {str(s["scene_id"]): s for s in task_scenes}
            if len(prompts) != len(expected) or {str(p.get("scene_id")) for p in prompts} != set(expected):
                raise ValueError("Prompt results must match every scene exactly once")
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
            if not native:
                for prompt in prompts:
                    prepare(prompt, expected[str(prompt["scene_id"])])
                for prompt in prompts:
                    await self._save_prompt(task_id, prompt)

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=70,
                status=VideoTaskStatus.IMAGE_GENERATING,
                message="Real prompts saved",
                extra_fields={"metadata.prompt_count": len(prompts), "metadata.prompt_active_segment": None},
            )
            return prompts
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise

    async def _save_prompt(self, task_id, prompt):
        fields = (
            "image_prompt", "image_negative_prompt", "video_negative_prompt", "video_prompt", "motion_beats", "camera_plan",
            "camera_section_offset", "camera_section_number", "first_frame_focus",
            "last_frame_for_transition", "continuity_note", "previous_segment_context",
        )
        await mongodb.update_one(
            SCENES_COLLECTION,
            {"_id": f"{task_id}_{prompt['scene_id']}"},
            {**{field: prompt.get(field) for field in fields},
             "negative_prompt": prompt.get("image_negative_prompt") or prompt.get("negative_prompt"),
             "status": "prompt_done", "updated_at": datetime.now(UTC)},
        )
