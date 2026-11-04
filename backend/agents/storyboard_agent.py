import anyio
import re
from datetime import UTC, datetime

from core.database import mongodb
from models.scene_model import Scene
from models.task_model import VideoTaskStatus
from services.provider_factory import get_llm_service
from services.generation_contract import PAIRED_GENERATION_MODE, build_timeline, validate_paired_scenes, validate_scene_durations, voice_window, scene_event_offset

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


def _maximum_voiceover_chars(duration: float) -> int:
    return max(18, int(duration * 4.2))


def validate_storyboard_scenes(
    scenes: list[dict[str, object]],
    target_duration: int | None = None,
    enforce_voiceover_density: bool = True,
    whole: bool = False,
) -> None:
    if not scenes:
        raise ValueError("Storyboard is empty")

    validate_scene_durations(scenes, target_duration, whole=whole)
    build_timeline(scenes)
    if [int(scene.get("scene_index", 0)) for scene in scenes] != list(range(1, len(scenes) + 1)):
        raise ValueError("Storyboard indexes must be consecutive from 1")
    for i, scene in enumerate(scenes):
        transition = scene.get("transition_type") or "cut"
        if transition not in {"cut", "continue_action", "scene_change"}:
            raise ValueError(f"Invalid scene transition: {transition}")
        if transition == "continue_action":
            if i == 0 or not scenes[i - 1].get("end_state"):
                raise ValueError("Continuous action requires a previous scene end state")
            # Reuse one written state rather than separately inventing two sides of a cut.
            scene["start_state"] = scenes[i - 1]["end_state"]
        scene_id = str(scene.get("scene_id") or "")
        voiceover = str(scene.get("voiceover") or "").strip()
        visual_description = str(scene.get("visual_description") or "").strip()
        character_state = str(scene.get("character_state") or "").strip()
        scene_continuity = str(scene.get("scene_continuity") or "").strip()
        transition_note = str(scene.get("transition_note") or "").strip()
        if not scene_id:
            raise ValueError("Storyboard scene_id is empty")
        voice_window(scene)
        for cue in scene.get("sound_cues") or []:
            scene_event_offset(scene, cue["start"])
        duration = int(scene.get("duration") or 0)
        if len(voiceover) < 2:
            raise ValueError(f"Storyboard voiceover is too short: {scene_id}")
        if enforce_voiceover_density and not scene.get("voiceover_repaired"):
            count = _spoken_char_count(voiceover)
            min_voiceover_chars = _minimum_voiceover_chars(float(duration))
            max_voiceover_chars = _maximum_voiceover_chars(float(duration))
            if count < min_voiceover_chars:
                raise ValueError(
                    f"Storyboard voiceover is too short for duration: "
                    f"{scene_id} needs at least {min_voiceover_chars} spoken chars"
                )
            if count > max_voiceover_chars:
                raise ValueError(
                    f"Storyboard voiceover is too long for duration: "
                    f"{scene_id} exceeds {max_voiceover_chars} spoken chars (got {count})"
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
            _target_duration = task.get("duration") if isinstance(task.get("duration"), int) else 60
            plan = task.get("metadata", {}).get("task_plan") or {}
            storyboard_context = {**script, "video_type": plan.get("video_type", "story"),
                                  "content_beats": plan.get("plot_beats", []), "characters": plan.get("characters", []),
                                  "visual_world": plan.get("visual_world", "")}
            _generation_mode = str(task.get("generation_mode", "full_dynamic"))
            scenes_data = await anyio.to_thread.run_sync(
                lambda _script=storyboard_context, _td=_target_duration, _gm=_generation_mode:
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
                whole=_generation_mode == "wan3_whole",
            )

            if _generation_mode == PAIRED_GENERATION_MODE:
                validate_paired_scenes(scenes_data, target_duration)

            saved_scenes: list[dict[str, object]] = []
            for item in scenes_data:
                now = datetime.now(UTC)
                scene = Scene(
                    scene_id=str(item["scene_id"]),
                    task_id=task_id,
                    scene_index=int(item["scene_index"]),
                    duration=int(item["duration"]),
                    voice_cue=item.get("voice_cue"),
                    voice_start=item.get("voice_start", 0),
                    sound_cues=item.get("sound_cues") or [],
                    transition_type=str(item.get("transition_type") or "cut"),
                    start_state=item.get("start_state"),
                    end_state=item.get("end_state"),
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
