import anyio
import re
from datetime import UTC, datetime

from core.database import mongodb
from models.scene_model import Scene
from models.task_model import VideoTaskStatus
from services.provider_factory import get_llm_service

from agents.agent_utils import (
    SCENES_COLLECTION,
    SCRIPTS_COLLECTION,
    get_required_task,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    with_document_id,
)


VAGUE_STORYBOARD_TERMS = ("震撼", "充满希望", "引发思考", "电影感", "高质量", "氛围感")


def _spoken_char_count(text: str) -> int:
    return len(re.findall(r"[\w\u4e00-\u9fff]", text))


def _minimum_voiceover_chars(duration: float) -> int:
    return max(6, int(duration * 2.5))


def adjust_durations_to_target(scenes: list[dict[str, object]], target_duration: int) -> None:
    if not scenes or not target_duration:
        return
    total = sum(int(s.get("duration") or 0) for s in scenes)
    if total == target_duration:
        return
        
    diff = target_duration - total
    
    # 仅在误差合理（<= 12秒）的情况下自动微调，过大则直接报错
    if abs(diff) > 12:
        return

    if diff > 0:
        # 需要加时间，依次加给前几个分镜
        for i in range(diff):
            idx = i % len(scenes)
            scenes[idx]["duration"] = int(scenes[idx].get("duration") or 0) + 1
    elif diff < 0:
        # 需要减时间，依次扣减时长大于3秒的分镜，保持每个分镜至少3秒
        to_reduce = abs(diff)
        reduced = 0
        for _ in range(100):  # 避免死循环
            for scene in scenes:
                curr = int(scene.get("duration") or 0)
                if curr > 4:
                    scene["duration"] = curr - 1
                    reduced += 1
                    if reduced == to_reduce:
                        return


def validate_storyboard_scenes(
    scenes: list[dict[str, object]],
    target_duration: int | None = None,
    enforce_voiceover_density: bool = True,
) -> None:
    if not scenes:
        raise ValueError("Storyboard is empty")

    # 如果总时长超出范围，再尝试微调
    if target_duration is not None:
        total = sum(int(s.get("duration") or 0) for s in scenes)
        if total < target_duration or total > target_duration + 30:
            adjust_durations_to_target(scenes, target_duration)

    total_duration = 0
    for scene in scenes:
        scene_id = str(scene.get("scene_id") or "")
        voiceover = str(scene.get("voiceover") or "").strip()
        visual_description = str(scene.get("visual_description") or "").strip()
        character_state = str(scene.get("character_state") or "").strip()
        scene_continuity = str(scene.get("scene_continuity") or "").strip()
        transition_note = str(scene.get("transition_note") or "").strip()
        if not scene_id:
            raise ValueError("Storyboard scene_id is empty")
        duration = int(scene.get("duration") or 0)
        if duration < 4:
            raise ValueError(f"Storyboard duration too short: {scene_id} has {duration}s, minimum is 4s")
        if len(voiceover) < 2:
            raise ValueError(f"Storyboard voiceover is too short: {scene_id}")
        if enforce_voiceover_density:
            count = _spoken_char_count(voiceover)
            min_voiceover_chars = _minimum_voiceover_chars(float(duration))
            if count < min_voiceover_chars:
                raise ValueError(
                    f"Storyboard voiceover is too short for duration: "
                    f"{scene_id} needs at least {min_voiceover_chars} spoken chars"
                )
        if len(visual_description) < 6:
            raise ValueError(f"Storyboard visual_description is too short: {scene_id}")
        if any(term in visual_description for term in VAGUE_STORYBOARD_TERMS):
            raise ValueError(f"Storyboard visual_description is vague: {scene_id}")
        if len(character_state) < 2:
            raise ValueError(f"Storyboard character_state is too short: {scene_id}")
        if len(scene_continuity) < 2:
            raise ValueError(f"Storyboard scene_continuity is too short: {scene_id}")
        if len(transition_note) < 2:
            raise ValueError(f"Storyboard transition_note is too short: {scene_id}")
        total_duration += int(scene.get("duration") or 0)

    if target_duration is not None and (total_duration < target_duration or total_duration > target_duration + 30):
        raise ValueError(
            f"Storyboard duration mismatch: expected {target_duration}~{target_duration + 30}s, got {total_duration}s"
        )


class StoryboardAgent:
    agent_name = "StoryboardAgent"

    async def run(self, task_id: str) -> list[dict[str, object]]:
        try:
            task = await get_required_task(task_id)
            
            # 检查分镜是否已生成
            scenes = await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=100)
            existing_scenes = [scene for scene in scenes]
            if existing_scenes:
                existing_scenes.sort(key=lambda s: int(s.get("scene_index", 0)))
                # 清除 MongoDB 的 _id 字段保证返回纯字典
                for scene in existing_scenes:
                    if "_id" in scene:
                        scene.pop("_id")
                await mark_agent_success(
                    task_id,
                    self.agent_name,
                    progress=50,
                    status=VideoTaskStatus.STORYBOARD_DONE,
                    message="Skipped StoryboardAgent (scenes already exist)",
                    extra_fields={"metadata.scene_count": len(existing_scenes)},
                )
                return existing_scenes

            await mark_agent_start(task_id, self.agent_name, progress=40)

            script = await mongodb.find_one(SCRIPTS_COLLECTION, {"task_id": task_id})
            if script is None:
                raise ValueError(f"Script not found for task: {task_id}")

            llm_service = get_llm_service()
            _target_duration = task.get("duration") if isinstance(task.get("duration"), int) else None
            _generation_mode = str(task.get("generation_mode", "full_dynamic"))
            scenes_data = await anyio.to_thread.run_sync(
                lambda _script=script, _td=_target_duration, _gm=_generation_mode:
                    llm_service.generate_storyboard(
                        _script,
                        target_duration=_td,
                        generation_mode=_gm,
                    )
            )
            target_duration = _target_duration
            validate_storyboard_scenes(
                scenes_data,
                target_duration=target_duration,
                enforce_voiceover_density=False,
            )

            saved_scenes: list[dict[str, object]] = []
            for item in scenes_data:
                now = datetime.now(UTC)
                scene = Scene(
                    scene_id=str(item["scene_id"]),
                    task_id=task_id,
                    scene_index=int(item["scene_index"]),
                    duration=int(item["duration"]),
                    speaker=item.get("speaker") if isinstance(item.get("speaker"), str) else None,
                    voiceover=item.get("voiceover") if isinstance(item.get("voiceover"), str) else None,
                    subtitle=item.get("subtitle") if isinstance(item.get("subtitle"), str) else None,
                    visual_description=item.get("visual_description")
                    if isinstance(item.get("visual_description"), str)
                    else None,
                    shot_type=item.get("shot_type") if isinstance(item.get("shot_type"), str) else None,
                    character_state=item.get("character_state")
                    if isinstance(item.get("character_state"), str)
                    else None,
                    scene_continuity=item.get("scene_continuity")
                    if isinstance(item.get("scene_continuity"), str)
                    else None,
                    transition_note=item.get("transition_note")
                    if isinstance(item.get("transition_note"), str)
                    else None,
                    audio_hint=item.get("audio_hint") if isinstance(item.get("audio_hint"), str) else None,
                    camera_motion=item.get("camera_motion") if isinstance(item.get("camera_motion"), str) else None,
                    need_dynamic_video=bool(item.get("need_dynamic_video", False)),
                    status="storyboard_done",
                    created_at=now,
                    updated_at=now,
                )
                document_id = f"{task_id}_{scene.scene_id}"
                await mongodb.insert_one(SCENES_COLLECTION, with_document_id(scene.model_dump(), document_id))
                saved_scenes.append(scene.model_dump())

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=50,
                status=VideoTaskStatus.STORYBOARD_DONE,
                message="Real storyboard saved",
                extra_fields={"metadata.scene_count": len(saved_scenes)},
            )
            return saved_scenes
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
