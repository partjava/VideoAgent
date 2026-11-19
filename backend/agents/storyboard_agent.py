import anyio
import re
from datetime import UTC, datetime
from typing import Any
from services.character_binding import frozen_characters, series_prompt_context, validate_series_scene
from services.drama_craft_service import ensure_scene_blocking, infer_transition_mode

from config import settings
from core.database import mongodb
from models.scene_model import Scene
from models.task_model import VideoTaskStatus
from services.provider_factory import get_llm_service
from services.task_service import task_service
from services.generation_contract import (
    AUTO_DIRECTOR_MODE,
    NATIVE_GENERATION_MODE,
    PAIRED_GENERATION_MODE,
    STORYBOARD_FLOW_MODE,
    active_provider_duration_bounds,
    build_timeline,
    validate_auto_director_scenes,
    validate_native_scenes,
    validate_paired_scenes,
    validate_scene_durations,
    validate_storyboard_flow_scenes,
    voice_window,
    scene_event_offset,
)

from agents.agent_utils import (
    SCENES_COLLECTION,
    SCRIPTS_COLLECTION,
    deduplicate_scenes_by_index,
    get_required_task,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    with_document_id,
)


VAGUE_STORYBOARD_TERMS = ("震撼", "充满希望", "引发思考", "电影感", "高质量", "氛围感")


def _sanitize_storyboard_draft(
    draft: list[dict[str, Any]],
    *,
    segments: list[dict[str, Any]] | None = None,
    allow_silent: bool = False,
    bound_cast: list[dict[str, Any]] | None = None,
) -> None:
    """清洗大模型分镜草稿中常见的轻度格式瑕疵（空泛词、缺失辅助字段、越界音效时间、非法转场名及角色ID纠偏），避免无意义硬崩溃。"""
    valid_transitions = {"cut", "continue_action", "scene_change"}
    valid_sfx = {"footstep", "impact", "water", "paper", "wind"}
    cast_ids = {str(c.get("character_id")).strip() for c in (bound_cast or []) if isinstance(c, dict) and c.get("character_id")}
    name_to_id = {
        str(c.get("name")).strip(): str(c.get("character_id")).strip()
        for c in (bound_cast or [])
        if isinstance(c, dict) and c.get("name") and c.get("character_id")
    }
    for idx, scene in enumerate(draft):
        if cast_ids and isinstance(scene.get("character_ids"), list):
            cleaned_ids: list[str] = []
            for raw_cid in scene["character_ids"]:
                cid = str(raw_cid or "").strip()
                mapped = cid if cid in cast_ids else name_to_id.get(cid)
                if mapped and mapped not in cleaned_ids:
                    cleaned_ids.append(mapped)
            scene["character_ids"] = cleaned_ids
        seg = segments[idx] if segments and idx < len(segments) and isinstance(segments[idx], dict) else {}
        if not str(scene.get("scene_id") or "").strip():
            scene["scene_id"] = f"scene_{idx + 1:02d}"
        scene["scene_index"] = idx + 1
        dur = max(3 if allow_silent else 4, min(12, int(scene.get("duration") or seg.get("duration") or 5)))
        scene["duration"] = dur

        trans = str(scene.get("transition_type") or "cut").strip()
        if trans not in valid_transitions:
            trans = "scene_change" if any(k in trans.lower() for k in ("flash", "fade", "dissolve", "change", "dream")) else "cut"
        if trans == "continue_action":
            if idx == 0:
                trans = "cut"
            elif not str(draft[idx - 1].get("end_state") or "").strip():
                prev_vis = str(draft[idx - 1].get("visual_description") or "").strip()
                if prev_vis:
                    draft[idx - 1]["end_state"] = prev_vis[:60]
                else:
                    trans = "cut"
        scene["transition_type"] = trans

        vis = str(scene.get("visual_description") or seg.get("plot") or "").strip()
        for vague in VAGUE_STORYBOARD_TERMS:
            vis = vis.replace(vague, "")
        vis = re.sub(r"[，,、]{2,}", "，", vis).strip("，, ")
        if len(vis) < 6:
            fallback_vis = str(seg.get("plot") or scene.get("voiceover") or "镜头推进呈现当前人物动作与空间反应").strip()
            vis = f"{vis} {fallback_vis}".strip()
            if len(vis) < 6:
                vis = "中景实拍人物动作与神情变化过程"
        scene["visual_description"] = vis

        if len(str(scene.get("character_state") or "").strip()) < 2:
            scene["character_state"] = "情绪紧绷转为警觉反应"
        if len(str(scene.get("scene_continuity") or "").strip()) < 2:
            scene["scene_continuity"] = "承接上一镜空间位置与人物状态" if idx > 0 else "建立主场景空间与人物初始方位"
        if len(str(scene.get("transition_note") or "").strip()) < 2:
            scene["transition_note"] = "动作收束后硬切下一镜"

        v_start = float(scene.get("voice_start") or 0.0)
        scene["voice_start"] = max(0.0, min(round(dur - 0.5, 2), v_start))

        raw_cues = scene.get("sound_cues")
        if isinstance(raw_cues, list):
            clean_cues = []
            for cue in raw_cues:
                if not isinstance(cue, dict):
                    continue
                cname = str(cue.get("name") or "").strip().lower()
                if cname not in valid_sfx:
                    continue
                try:
                    cstart = float(cue.get("start") or 0.0)
                except (TypeError, ValueError):
                    cstart = 0.0
                clean_cues.append({"name": cname, "start": round(max(0.0, min(dur - 0.1, cstart)), 2)})
            scene["sound_cues"] = clean_cues
        else:
            scene["sound_cues"] = []

        if not allow_silent and not scene.get("dialogue_lines") and len(str(scene.get("voiceover") or "").strip()) < 2:
            fallback_vo = str(seg.get("voiceover") or "").strip()
            if len(fallback_vo) >= 2:
                scene["voiceover"] = fallback_vo
                scene["subtitle"] = fallback_vo


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
    allow_silent: bool = False,
) -> None:
    if not scenes:
        raise ValueError("Storyboard is empty")

    validate_scene_durations(scenes, target_duration, whole=whole, min_duration=3 if allow_silent else 4)
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
        if len(voiceover) < 2 and not allow_silent:
            raise ValueError(f"Storyboard voiceover is too short: {scene_id}")
        if enforce_voiceover_density and not scene.get("voiceover_repaired") and (voiceover or not allow_silent):
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
            
            # 检查分镜是否已生成；若存在并发或历史残余导致的重复 scene_index，先去重或清理重建
            scenes = await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=100)
            existing_scenes = [scene for scene in scenes]
            _is_flow = task.get("generation_mode") == STORYBOARD_FLOW_MODE
            if existing_scenes:
                deduped = await deduplicate_scenes_by_index(existing_scenes, mongodb, SCENES_COLLECTION)
                for scene in deduped:
                    scene.pop("_id", None)
                try:
                    validate_storyboard_scenes(
                        deduped,
                        target_duration=task.get("duration") if isinstance(task.get("duration"), int) else None,
                        enforce_voiceover_density=False,
                        whole=task.get("generation_mode") == "wan3_whole",
                        allow_silent=_is_flow,
                    )
                    await mark_agent_success(
                        task_id,
                        self.agent_name,
                        progress=50,
                        status=VideoTaskStatus.STORYBOARD_DONE,
                        message="Skipped StoryboardAgent (scenes already exist)",
                        extra_fields={"metadata.scene_count": len(deduped)},
                    )
                    return deduped
                except ValueError:
                    for sc in await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=200):
                        if sc.get("_id") is not None:
                            await mongodb.delete_one(SCENES_COLLECTION, {"_id": sc["_id"]})

            await mark_agent_start(task_id, self.agent_name, progress=40)

            script = await mongodb.find_one(SCRIPTS_COLLECTION, {"task_id": task_id})
            if script is None:
                raise ValueError(f"Script not found for task: {task_id}")

            llm_service = get_llm_service()
            _target_duration = task.get("duration") if isinstance(task.get("duration"), int) else 60
            plan = task.get("metadata", {}).get("task_plan") or {}
            storyboard_context = {**script, "video_type": plan.get("video_type", "story"),
                                  "visual_style": task.get("style"),
                                  "series_context": series_prompt_context(task),
                                  "content_beats": plan.get("plot_beats", []), "characters": plan.get("characters", []),
                                  "visual_world": plan.get("visual_world", "")}
            _generation_mode = str(task.get("generation_mode", "full_dynamic"))
            if _generation_mode == "wan3_whole":
                storyboard_context["segments"] = []
            segments = script.get("segments") or []

            async def _draft_and_validate(ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
                draft = await anyio.to_thread.run_sync(
                    lambda _script=ctx, _td=_target_duration, _gm=_generation_mode:
                        llm_service.generate_storyboard(
                            _script,
                            target_duration=_td,
                            generation_mode=_gm,
                        )
                )
                resolved_duration = _target_duration
                if _generation_mode == NATIVE_GENERATION_MODE and segments:
                    if len(draft) != len(segments):
                        raise ValueError("原生10秒分镜必须与剧本段落一一对应")
                    draft.sort(key=lambda scene: int(scene["scene_index"]))
                    for scene, segment in zip(draft, segments):
                        lines = segment.get("dialogue_lines") or []
                        voiceover = "\n".join(line["text"] for line in lines) if lines else segment.get("voiceover", "")
                        scene.update(
                            voiceover=voiceover, subtitle=voiceover,
                            voice_start=lines[0]["start"] if lines else segment["voice_start"],
                            dialogue_lines=lines,
                        )
                elif _generation_mode in {AUTO_DIRECTOR_MODE, STORYBOARD_FLOW_MODE}:
                    min_s, max_s = active_provider_duration_bounds(generation_mode=_generation_mode, cfg=settings)
                    default_dur = 4 if _generation_mode == STORYBOARD_FLOW_MODE else 8
                    draft.sort(key=lambda scene: int(scene.get("scene_index") or 1))
                    for idx, scene in enumerate(draft):
                        scene["scene_index"] = idx + 1
                        scene["need_dynamic_video"] = True
                        if idx == 0 and scene.get("transition_type") == "continue_action":
                            scene["transition_type"] = "cut"
                        if segments and idx < len(segments):
                            segment = segments[idx]
                            seg_dur = max(min_s, min(max_s, int(segment.get("duration") or scene.get("duration") or default_dur)))
                            lines = segment.get("dialogue_lines") or []
                            voiceover = "\n".join(line["text"] for line in lines) if lines else segment.get("voiceover", "")
                            if voiceover:
                                scene.update(
                                    duration=seg_dur,
                                    voiceover=voiceover,
                                    subtitle=voiceover,
                                    voice_start=lines[0]["start"] if lines else segment.get("voice_start", 0),
                                    dialogue_lines=lines,
                                )
                            else:
                                scene["duration"] = seg_dur
                                if _generation_mode == STORYBOARD_FLOW_MODE and segment.get("voiceover") == "":
                                    scene["voiceover"] = ""
                                    scene["subtitle"] = ""
                        else:
                            scene["duration"] = max(min_s, min(max_s, int(scene.get("duration") or default_dur)))
                    if not task.get("duration_locked"):
                        resolved_duration = sum(int(s["duration"]) for s in draft)
                        await task_service.update_task_fields(
                            task_id,
                            {"duration": resolved_duration, "metadata.task_plan.duration": resolved_duration},
                        )
                _sanitize_storyboard_draft(
                    draft,
                    segments=segments,
                    allow_silent=_generation_mode == STORYBOARD_FLOW_MODE,
                    bound_cast=frozen_characters(task),
                )
                if not task.get("duration_locked") and _generation_mode in {AUTO_DIRECTOR_MODE, STORYBOARD_FLOW_MODE}:
                    resolved_duration = sum(int(s["duration"]) for s in draft)
                    await task_service.update_task_fields(
                        task_id,
                        {"duration": resolved_duration, "metadata.task_plan.duration": resolved_duration},
                    )
                validate_storyboard_scenes(
                    draft,
                    target_duration=resolved_duration,
                    enforce_voiceover_density=False,
                    whole=_generation_mode == "wan3_whole",
                    allow_silent=_generation_mode == STORYBOARD_FLOW_MODE,
                )
                if _generation_mode == PAIRED_GENERATION_MODE:
                    validate_paired_scenes(draft, resolved_duration)
                elif _generation_mode == NATIVE_GENERATION_MODE:
                    validate_native_scenes(draft, resolved_duration)
                elif _generation_mode == AUTO_DIRECTOR_MODE:
                    validate_auto_director_scenes(
                        draft, target_duration=resolved_duration, bounds=active_provider_duration_bounds(generation_mode=AUTO_DIRECTOR_MODE, cfg=settings)
                    )
                elif _generation_mode == STORYBOARD_FLOW_MODE:
                    validate_storyboard_flow_scenes(
                        draft,
                        target_duration=resolved_duration,
                        bounds=active_provider_duration_bounds(generation_mode=STORYBOARD_FLOW_MODE, cfg=settings),
                    )
                return draft, resolved_duration

            scenes_data, target_duration = await _draft_and_validate(storyboard_context)

            # 出图/出视频前前置剧透与结尾契合核查：发现把末尾反转剧透到前中段时，免费在文字阶段重写一次
            if task.get("user_input") and settings.enable_paid_api and hasattr(llm_service, "review_story_coverage"):
                try:
                    pre_review_inputs = [
                        {
                            "scene_id": sc.get("scene_id"),
                            "scene_index": sc.get("scene_index"),
                            "duration": sc.get("duration"),
                            "picture": sc.get("visual_description"),
                            "narration": sc.get("voiceover"),
                            "media": "video" if sc.get("need_dynamic_video") else "still image",
                        }
                        for sc in scenes_data
                    ]
                    pre_review = await anyio.to_thread.run_sync(
                        llm_service.review_story_coverage,
                        task.get("user_input", ""),
                        plan.get("plot_beats", []),
                        pre_review_inputs,
                    )
                    raw_issues = [str(x).strip() for x in (pre_review.get("issues") or []) if str(x).strip()]
                    if pre_review.get("verdict") != "pass" and any(
                        kw in msg for msg in raw_issues for kw in ("剧透", "提前", "最后", "结尾", "未按", "不一致", "遗漏")
                    ):
                        print(f"[StoryboardAgent] Task {task_id}: pre-shoot semantic check flagged {len(raw_issues)} timing/spoiler issue(s); rewriting once before image/video generation...", flush=True)
                        revised_scenes, revised_dur = await _draft_and_validate({**storyboard_context, "review_feedback": raw_issues})
                        scenes_data, target_duration = revised_scenes, revised_dur
                except Exception as exc:
                    print(f"[StoryboardAgent] Pre-shoot semantic review skipped safely: {exc}", flush=True)

            for stale in await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=200):
                if stale.get("_id") is not None:
                    await mongodb.delete_one(SCENES_COLLECTION, {"_id": stale["_id"]})

            saved_scenes: list[dict[str, object]] = []
            bound_cast = frozen_characters(task) or plan.get("characters", [])
            for idx, item in enumerate(scenes_data):
                validate_series_scene(task, item)
                now = datetime.now(UTC)
                blocking_text = ensure_scene_blocking(item, bound_cast)
                t_mode = infer_transition_mode(item, idx)
                scene = Scene(
                    character_ids=item.get("character_ids") or [],
                    scene_id=str(item["scene_id"]),
                    task_id=task_id,
                    scene_index=int(item["scene_index"]),
                    duration=int(item["duration"]),
                    dialogue_lines=item.get("dialogue_lines") or [],
                    voice_cue=item.get("voice_cue"),
                    voice_start=item.get("voice_start", 0),
                    sound_cues=item.get("sound_cues") or [],
                    transition_type=str(item.get("transition_type") or "cut"),
                    transition_mode=t_mode,
                    blocking=blocking_text,
                    start_state=item.get("start_state"),
                    end_state=item.get("end_state"),
                    reaction_focus=item.get("reaction_focus"),
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
                await mongodb.replace_one(
                    SCENES_COLLECTION,
                    {"_id": document_id},
                    with_document_id(scene.model_dump(), document_id),
                    upsert=True,
                )
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
