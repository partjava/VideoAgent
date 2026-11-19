import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from copy import deepcopy
import re

import anyio

from agents.agent_utils import (
    ASSETS_COLLECTION,
    SCENES_COLLECTION,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    with_document_id,
)
from core.database import mongodb
from models.asset_model import Asset
from models.task_model import VideoTaskStatus
from services.generation_contract import (
    AUTO_DIRECTOR_MODE,
    STORYBOARD_FLOW_MODE,
    active_provider_duration_bounds,
    continuity_input,
    format_motion_beats,
    needs_video,
    provider_duration_bounds,
    validate_auto_director_scenes,
    validate_motion_beats,
    validate_scene_durations,
    validate_storyboard_flow_scenes,
    PAIRED_GENERATION_MODE,
    NATIVE_GENERATION_MODE,
    validate_native_scenes,
    WHOLE_GENERATION_MODE,
    validate_paired_scenes,
    voice_speed,
    voice_window,
)
from services.camera_plan import camera_description
from services.media_quality import compare_join_frames, extract_tail, resolve_media
from services.provider_factory import get_video_service
from services.video.base import VideoRenderRejected
from services.video_vision import replan_from_tail, review_enabled, review_opening_frame
from services.audio_timing import narration_speed
from services.visual_style import lock_visual_style, PHOTO_EXCLUSIONS
from services.generation_dispatch import dispatch_generation
from services.generation_usage import record_reuse
from services.drama_craft_service import sanitize_video_prompt_mcsla
from services.shot_continuity import adapt_video_submission


OVERLAY_DIRECTION = re.compile(
    r"(?:切黑|黑底|黑屏)(?=[^。；;｜\n]{0,24}(?:白字|字幕|字卡))[^。；;｜\n]*"
    r"|(?:浮出|浮现|弹出|出现|显示|叠加|打出)\s*"
    r"(?:[一行段条短句白色中文英文巨大醒目居中]{0,12})"
    r"(?:白字|字幕|字卡|片名|大字)[^。；;｜\n]*"
)

OVERLAY_CLAUSE = re.compile(
    r"(?:屏幕|画面正中|画面中央|黑底|黑屏)[^。；;｜\n]{0,80}"
    r"(?:文字|字样|英文字母|字幕|白字|大字)[^。；;｜\n]*"
)


def sanitize_motion_text(text):
    props = []

    def protect(match):
        props.append(match.group())
        return f"__PROP_TEXT_{len(props) - 1}__"

    text = re.sub(
        r"(?:鞋底|鞋面|招牌|信纸|信封|桌面)(?:上)?(?:刻有|印有|写有)"
        r"[^，,。；;｜\n]*", protect, text,
    )
    text = OVERLAY_DIRECTION.sub("", OVERLAY_CLAUSE.sub("", text))
    for index, prop in enumerate(props):
        text = text.replace(f"__PROP_TEXT_{index}__", prop)
    return text


def align_measured_motion(scene):
    scene = deepcopy(scene)
    audio = scene.get("dialogue_audio") or []
    if audio:
        start, end = float(audio[-1]["start"]), float(audio[-1]["end"])
    elif scene.get("audio_duration") and scene.get("voiceover"):
        start, slot = voice_window(scene)
        duration = float(scene["audio_duration"])
        end = start + duration / narration_speed(scene, duration, slot)
    else:
        return scene
    risky = re.compile(r"转身|背对|离场|离开画面|出画|切黑|拉门|卷帘门")
    beats = scene.get("motion_beats") or []
    index = next((i for i, beat in enumerate(beats)
                  if float(beat["end"]) > start and float(beat["start"]) < end
                  and risky.search(str(beat.get("action", "")))), None)
    if index is None:
        return scene
    state = (beats[index - 1].get("end_state") if index else scene.get("start_state")) or beats[index].get("start_state") or "保持当前人物位置和道具接触"
    camera = beats[index - 1].get("camera") if index else "保持首帧构图与拍摄侧"
    focus = scene.get("reaction_focus") or {
        "product": "当前产品和已完成的演示结果",
        "explainer": "当前可见现象和演示结果",
    }.get(scene.get("video_type"), "当前人物可见的表情和反应")
    for beat in beats[index:]:
        beat.update(camera=camera, start_state=state, end_state=state,
                    action=f"维持当前人物位置、朝向和道具接触，保留{focus}。")
    scene.update(video_prompt="从首帧按逐秒动作推进，保持人物和道具一致。",
                 camera_motion="保持原拍摄侧，按逐秒构图平稳拍摄。",
                 end_state=state, last_frame_for_transition=state,
                 continuity_note="保持首帧人物外貌、服装、场景和光线。",
                 scene_continuity="保持首帧场景。")
    scene.pop("camera_plan", None)
    return scene


def actual_dialogue_direction(scene):
    audio = scene.get("dialogue_audio") or []
    if not audio:
        return ""
    last = audio[-1]
    return f"\n[配音对齐] 最后一句台词在 {last['start']:.2f}–{last['end']:.2f} 秒，保持说话人正面表情和反应完整可见。"


def speaker_windows(scene):
    windows = []
    visible_ids = scene.get("character_ids") or []
    single_cast_mode = len(visible_ids) == 1
    visible_speaker = str(scene.get("speaker") or "").strip()
    for line in scene.get("dialogue_audio") or []:
        speaker = str(line.get("speaker") or "当前说话者").strip()
        if speaker in {"旁白", "解说", "narrator"}:
            windows.append(f"{line['start']:.2f}–{line['end']:.2f}秒为画外旁白，画面内人物按剧情反应。")
        elif single_cast_mode and visible_speaker and speaker != visible_speaker:
            # 单人镜头画外角色发声时不写入画外人名，避免生出多余人物或双胞胎
            windows.append(f"{line['start']:.2f}–{line['end']:.2f}秒为画外对白，画面内人物自然闭口并保持倾听反应。")
        else:
            windows.append(
                f"{line['start']:.2f}–{line['end']:.2f}秒：{speaker}若在画面中，"
                "双唇自然轻微开合，下颌微动；同场倾听者自然闭口。"
            )
    return "\n[实测发声窗口]\n" + "\n".join(windows) if windows else ""


def video_prompt_options(service, scene):
    negative = scene.get("video_negative_prompt")
    if scene.get("style") == "短剧":
        negative = PHOTO_EXCLUSIONS + "，" + (negative or "")
    if scene.get("dialogue_audio"):
        negative = (negative or "") + "，嘴部撕裂，牙齿畸形，嘴唇粘连，夸张扭曲张嘴"
    if isinstance(negative, str) and negative.strip() and getattr(service, "supports_negative_prompt", False) is True:
        return {"negative_prompt": negative.strip()}
    return {}


def render_signature(service, first_digest, prompt, duration, ratio, *, negative_prompt=None, **extra):
    provider = f"{type(service).__module__}.{type(service).__qualname__}:{getattr(service, 'model', '')}:{getattr(service, 'api_base', '')}"
    parts = (provider, first_digest, prompt, duration, ratio)
    if negative_prompt:
        parts += (negative_prompt,)
    for k in sorted(extra):
        if extra[k] is not None:
            parts += (f"{k}={extra[k]}",)
    return sha256(repr(parts).encode()).hexdigest()


async def prepare_motion_prompt(scene, previous, first, first_digest):
    saved = scene.get("observed_handoff") or {}
    if scene.get("transition_type") != "continue_action" or not previous or not previous.get("video_path"):
        return _build_motion_prompt(scene), None
    previous_beats = previous.get("effective_motion_beats") or previous.get("motion_beats") or []
    previous_state = previous_beats[-1].get("end_state") if previous_beats else previous.get("end_state")
    scene = deepcopy(scene)
    scene["previous_action_state"] = previous_state
    if previous.get("effective_end_state") and scene.get("motion_beats"):
        scene["start_state"] = previous["effective_end_state"]
        scene["motion_beats"][0]["start_state"] = previous["effective_end_state"]
    revision = sha256(_build_motion_prompt(scene).encode()).hexdigest()
    plan = None
    if saved.get("digest") == first_digest and saved.get("revision") == revision:
        plan = saved["plan"]
    elif review_enabled() and previous.get("video_path"):
        try:
            plan = await anyio.to_thread.run_sync(replan_from_tail, previous, scene, first)
        except Exception as exc:
            print(f"[VideoAgent] Tail replan skipped safely for {scene.get('scene_id')}: {exc}", flush=True)
            plan = None
    if plan is None:
        base_note = str(scene.get("continuity_note") or "").strip()
        seam_lock = "首0.3秒严格顺承首帧落地姿态、持物手与运动惯性，不复位重起"
        if seam_lock not in base_note:
            scene["continuity_note"] = f"{seam_lock}。{base_note}".strip("。")
        return _build_motion_prompt(scene), None
    return _build_motion_prompt({**scene, **plan}), {"digest": first_digest, "revision": revision, "plan": plan}


def _compact_timeline_slices(beats, *, keep_intermediate_states: bool = True, merge_identical: bool = False):
    working = list(beats)
    if merge_identical and len(working) > 1:
        merged = [dict(working[0])]
        for item in working[1:]:
            prev = merged[-1]
            same_action = str(prev.get("action") or "").strip() == str(item.get("action") or "").strip()
            same_subj = str(prev.get("subject") or "").strip() == str(item.get("subject") or "").strip()
            same_cam = str(prev.get("camera") or "").strip() == str(item.get("camera") or "").strip()
            if same_action and same_subj and same_cam:
                prev["end"] = item["end"]
                prev["end_state"] = item.get("end_state")
            else:
                merged.append(dict(item))
        working = merged

    slices = []
    prev_end_state = None
    for index, b in enumerate(working):
        parts = []
        cam = b.get("camera")
        if index == 0 and cam and cam != "沿用分组运镜计划":
            parts.append(f"机位:{cam}")
        start_state = b.get("start_state")
        if start_state and (index == 0 or (keep_intermediate_states and start_state != prev_end_state)):
            parts.append(f"起:{start_state}")
        action_text = f"{b.get('subject', '')} {b['action']}".strip()
        if action_text:
            parts.append(action_text)
        end_state = b.get("end_state")
        if end_state and (index == len(working) - 1 or (keep_intermediate_states and end_state != start_state)):
            parts.append(f"止:{end_state}")
        prev_end_state = end_state
        slices.append(f"[{b['start']}-{b['end']}s]" + "｜".join(parts))
    return "\n".join(slices)


def _build_motion_prompt(scene):
    scene = align_measured_motion(scene)
    beats = scene.get("motion_beats")
    validate_motion_beats(beats, float(scene["duration"]), detailed=True)

    shot_type = scene.get("shot_type") or "中景"
    camera_motion = scene.get("camera_motion") or "固定机位"

    # 1. 镜头配方（景别与单一主运镜）
    plan = scene.get("camera_plan")
    if plan:
        offset = scene.get("camera_section_offset") or 0
        direction = camera_description(plan, offset, float(scene["duration"]))
        framing_goal = plan["closing"] if offset else plan["handoff"]
        recipe_header = (
            f"[统一运镜] {direction} 目标构图：{framing_goal}。镜头任务：{plan['purpose']}。"
        )
    else:
        recipe_header = f"[运镜指令] 景别:{shot_type}，单一主运镜:{camera_motion}，从首帧自然推进。"

    # 2. 角色与场景物理锁定 + 结尾定格
    continuity = scene.get("continuity_note") or scene.get("scene_continuity") or "保持首帧五官、服装与主光一致"
    opening_pose = beats[0].get("start_state") if beats else scene.get("start_state")
    settle = scene.get("end_state") or (beats[-1].get("end_state") if beats else None) or scene.get("last_frame_for_transition") or "动作自然收止稳定"

    pose_block = f"[起止] 起:{opening_pose}；定格:{settle}。\n" if opening_pose else ""
    consistency_block = (
        f"{pose_block}"
        f"[锚定与微锁] 严格继承首帧外观与物理材质（{continuity}），一镜一视线落点，身后固定背景与主光色温不变，最终停在：{settle}。"
    )

    # The section camera plan and structured beats are authoritative in paired mode.
    base_prompt = "" if plan else str(scene.get("video_prompt") or "").strip()
    blocking_header = f"[站位轴线] {scene['blocking']}\n" if scene.get("blocking") else ""
    handoff = ""
    if scene.get("transition_type") == "continue_action":
        handoff = (
            "[尾帧续接] 输入图即为上段尾帧，第一帧沿用其中真实姿态、机位与道具位置；已完成动作不重演，已离场物体不复位。\n"
        )
        previous_state = scene.get("previous_action_state")
        if previous_state:
            handoff += f"[动作交接] 从上一拍继续：{previous_state}。\n"

    tail_notes = actual_dialogue_direction(scene) + speaker_windows(scene)

    cast_names = scene.get("cast_names") or [
        str(line.get("speaker") or "").strip()
        for line in (scene.get("dialogue_audio") or [])
        if str(line.get("speaker") or "").strip() not in {"", "旁白", "解说", "narrator"}
    ]

    def _assemble_level(keep_inter: bool, merge_same: bool, include_overview: bool) -> str:
        tblock = _compact_timeline_slices(beats, keep_intermediate_states=keep_inter, merge_identical=merge_same)
        prefix = f"{base_prompt}\n" if (include_overview and base_prompt) else ""
        raw_motion = f"{prefix}[节奏动作链]\n{tblock}".strip()
        sanitized_motion = sanitize_video_prompt_mcsla(
            raw_motion,
            scene=scene,
            all_character_names=cast_names,
            max_chars=1800,
        )["prompt"]
        body = f"{handoff}{blocking_header}{recipe_header}\n{sanitized_motion}\n{consistency_block}"
        return lock_visual_style(sanitize_motion_text(body), scene.get("style")) + tail_notes

    # 无损逐级压缩：先保留完整明细；若超 1400 字则依次省略中间重复状态、合并静止连续拍、省略概括句，绝不截断第 8-10 秒结尾动作
    for keep_inter, merge_same, include_overview in (
        (True, False, True),
        (False, False, True),
        (False, True, True),
        (False, True, False),
    ):
        assembled = _assemble_level(keep_inter, merge_same, include_overview)
        if len(assembled) <= 1400:
            return assembled
    return assembled


def video_service_for_task(task):
    if task.get("generation_mode") == WHOLE_GENERATION_MODE:
        from services.video.wan_video_service import WanVideoService
        return WanVideoService(model="wan3.0-video")
    return get_video_service()


def _bounds_for_video_service(service, generation_mode: str, cfg) -> tuple[int, int]:
    """优先依据本任务实际使用的视频服务推导合法片段时长范围，未命中时再回退至全局生效配置。"""
    if service is not None:
        cls_name = type(service).__name__.lower()
        raw_model = getattr(service, "model", "")
        model_text = raw_model.lower() if isinstance(raw_model, str) else ""
        if any(tok in f"{cls_name} {model_text}" for tok in ("wan", "vidu", "doubao", "seedance", "siliconflow", "minimax", "comfyui")):
            return provider_duration_bounds(cls_name, model_text, generation_mode=generation_mode)
    return active_provider_duration_bounds(generation_mode=generation_mode, cfg=cfg)


class VideoAgent:
    agent_name = "VideoAgent"

    async def run(self, task_id: str, *, trial_scene_id: str | None = None) -> list[dict[str, object]]:
        try:
            if trial_scene_id is None:
                await mark_agent_start(task_id, self.agent_name, progress=88, status=VideoTaskStatus.VIDEO_GENERATING)
            query = {"task_id": task_id}
            scenes = await mongodb.find_many(SCENES_COLLECTION, query, limit=100)
            scenes.sort(key=lambda s: int(s.get("scene_index", 0)))
            task = await mongodb.find_one("video_tasks", {"_id": task_id}) or {}
            from services.character_binding import frozen_characters
            task_plan_chars = (task.get("metadata", {}).get("task_plan") or {}).get("characters") or []
            all_cast_names = [
                c.get("name") for c in (frozen_characters(task) or task_plan_chars) if isinstance(c, dict) and c.get("name")
            ]
            video_type = (task.get("metadata", {}).get("task_plan") or {}).get("video_type", "story")
            for scene in scenes:
                scene["video_type"] = video_type
                scene["style"] = task.get("style")
                scene["cast_names"] = all_cast_names
            ratio = str(task.get("ratio", "9:16"))
            paired = task.get("generation_mode") == PAIRED_GENERATION_MODE
            native = task.get("generation_mode") == NATIVE_GENERATION_MODE
            auto_director = task.get("generation_mode") == AUTO_DIRECTOR_MODE
            storyboard_flow = task.get("generation_mode") == STORYBOARD_FLOW_MODE
            service = None
            if paired:
                validate_paired_scenes(scenes, task.get("duration"))
            elif native:
                validate_native_scenes(scenes, task.get("duration"))
                for scene in scenes:
                    validate_motion_beats(scene.get("motion_beats"), 10, detailed=True, per_second=True)
            elif auto_director or storyboard_flow:
                from config import settings as _cfg
                mode_key = AUTO_DIRECTOR_MODE if auto_director else STORYBOARD_FLOW_MODE
                try:
                    service = video_service_for_task(task)
                except Exception:
                    service = None
                bounds = _bounds_for_video_service(service, mode_key, _cfg)
                if auto_director:
                    validate_auto_director_scenes(scenes, target_duration=task.get("duration"), bounds=bounds)
                else:
                    validate_storyboard_flow_scenes(scenes, target_duration=task.get("duration"), bounds=bounds)
                for scene in scenes:
                    validate_motion_beats(scene.get("motion_beats"), int(scene["duration"]), detailed=True, per_second=auto_director)
            else:
                validate_scene_durations(scenes, task.get("duration"), whole=task.get("generation_mode") == WHOLE_GENERATION_MODE)
            selected = list(enumerate(scenes))
            if trial_scene_id is not None:
                selected = [(i, scene) for i, scene in selected if scene["scene_id"] == trial_scene_id]
                if not selected or not needs_video(selected[0][1]):
                    raise ValueError("请选择需要生成动态片段的镜头试拍")
            # Fail before submitting paid clips if the soundtrack cannot fit the timeline.
            for _, scene in selected:
                if scene.get("dialogue_lines"):
                    audio = scene.get("dialogue_audio") or []
                    if len(audio) != len(scene["dialogue_lines"]) or not all(resolve_media(line["path"]).is_file() for line in audio):
                        raise ValueError(f"Dialogue tracks not ready for scene {scene['scene_id']}")
                    continue
                if str(scene.get("voiceover") or "").strip():
                    audio_path = scene.get("audio_path")
                    if not audio_path or not resolve_media(audio_path).is_file():
                        raise ValueError(f"Voice track not ready for scene {scene['scene_id']}")
                    narration_speed(scene, float(scene.get("audio_duration") or 0), voice_window(scene)[1])
            stored = await mongodb.find_many(ASSETS_COLLECTION, query, limit=200)
            images = {a["scene_id"]: a for a in stored if a.get("asset_type") == "image"}
            videos = {}
            for asset in stored:  # Database orders newest first.
                if asset.get("asset_type") == "video_clip":
                    videos.setdefault(asset["scene_id"], asset)
            if service is None:
                service = video_service_for_task(task)
            sem = getattr(anyio, "Semaphore", asyncio.Semaphore)(3)
            chains: list[list[tuple[int, dict]]] = []
            for pair in selected:
                _, sc = pair
                if trial_scene_id is None and chains and sc.get("transition_type") == "continue_action":
                    chains[-1].append(pair)
                else:
                    chains.append([pair])

            outcomes: dict[int, dict] = {}

            async def _run_chain(chain: list[tuple[int, dict]]) -> None:
                previous_tail = None
                for index, scene in chain:
                    sid = str(scene["scene_id"])
                    key = {"_id": f"{task_id}_{sid}"}
                    image = scene.get("image_path") or images.get(sid, {}).get("path")
                    if not needs_video(scene):
                        if not image or not resolve_media(image).is_file():
                            raise ValueError(f"Missing scene image: {sid}")
                        previous_tail = str(image)
                        continue

                    previous = scenes[index - 1] if index else None
                    if trial_scene_id is not None and scene.get("transition_type") == "continue_action":
                        if previous and not previous.get("stale_tail"):
                            if needs_video(previous):
                                previous_tail = previous.get("last_frame_path") if previous.get("video_path") else None
                            else:
                                previous_tail = previous.get("image_path") or images.get(previous["scene_id"], {}).get("path")
                        if not previous_tail or not resolve_media(previous_tail).is_file():
                            raise ValueError("这个镜头需要上一镜头的实际尾帧，请先试拍上一镜头")
                    first = continuity_input(scene, previous_tail, image)
                    if not first or not resolve_media(first).is_file():
                        raise ValueError(f"Missing first frame for scene: {sid}")
                    duration = int(scene["duration"])
                    asset = videos.get(sid)
                    cached_path = asset.get("path") if asset else None
                    first_digest = sha256(resolve_media(first).read_bytes()).hexdigest()
                    prompt, observed = await prepare_motion_prompt(scene, previous, first, first_digest)
                    prompt_scene = {**scene, "_is_trial": True} if trial_scene_id is not None else scene
                    submitted_prompt, adapted_opts = adapt_video_submission(service, prompt_scene, prompt)
                    prompt_options = {**video_prompt_options(service, prompt_scene), **adapted_opts}
                    signature = render_signature(service, first_digest, submitted_prompt, duration, ratio, **prompt_options)
                    reusable = asset and asset.get("render_signature") == signature
                    asset_needs_replace = False
                    scene_fields: dict[str, object] = {}

                    if not cached_path or not resolve_media(cached_path).is_file() or not reusable:
                        if scene.get("camera_plan") and scene.get("transition_type") != "continue_action" and review_enabled():
                            saved_check = scene.get("opening_frame_review") or {}
                            if saved_check.get("digest") != first_digest:
                                try:
                                    check = await anyio.to_thread.run_sync(review_opening_frame, scene, first)
                                    saved_check = {"digest": first_digest, **check}
                                    await mongodb.update_one(SCENES_COLLECTION, key, {"opening_frame_review": saved_check})
                                except Exception as exc:
                                    print(f"[VideoAgent] Opening frame review skipped safely for {sid}: {exc}", flush=True)
                                    saved_check = {}
                            if saved_check.get("verdict") == "blocked":
                                raise ValueError(f"首帧构图不符合分组运镜计划（{sid}）：{saved_check.get('reason') or '关键接触位置不可见'}")
                        if observed:
                            await mongodb.update_one(SCENES_COLLECTION, key, {"observed_handoff": observed})
                        task_doc = {"task_id": task_id, **(task or {})}
                        try:
                            async with sem:
                                has_pending = getattr(service, "has_pending_render", lambda *a, **kw: False)(
                                    task_id, sid, first, video_prompt=submitted_prompt, duration=duration, ratio=ratio, **prompt_options
                                ) is True
                                def _call_video(_sid=sid, _first=first, _prompt=submitted_prompt, _dur=duration, _opts=prompt_options):
                                    return service.generate_video(
                                        task_id=task_id, scene_id=_sid, image_path=_first,
                                        video_prompt=_prompt, duration=_dur, ratio=ratio, **_opts
                                    )
                                if has_pending:
                                    result = await anyio.to_thread.run_sync(_call_video)
                                else:
                                    result = await dispatch_generation(task_doc, "video", sid, _call_video)
                        except Exception as exc:
                            if trial_scene_id is not None or paired or native or task.get("generation_mode") == WHOLE_GENERATION_MODE or (not isinstance(exc, VideoRenderRejected) and not isinstance(exc.__cause__, VideoRenderRejected)):
                                raise
                            outcomes[index] = {
                                "kind": "fallback",
                                "key": key,
                                "first": first,
                                "reason": str(exc),
                            }
                            previous_tail = first
                            continue
                        if result.get("status") != "success" or not result.get("asset_path"):
                            raise RuntimeError(f"Video generation failed for {sid}")
                        now = datetime.now(UTC)
                        asset_id = f"asset_{task_id}_{sid}_video_clip"
                        asset = Asset(asset_id=asset_id, task_id=task_id, scene_id=sid,
                                      asset_type="video_clip", path=str(result["asset_path"]),
                                      source=str(result.get("source", "video_provider")), status="success",
                                      created_at=now, updated_at=now).model_dump()
                        asset["provider_task_id"] = result.get("provider_task_id")
                        asset["first_frame_digest"] = first_digest
                        asset["render_signature"] = signature
                        asset["submitted_video_prompt"] = submitted_prompt
                        asset_needs_replace = True
                        scene_fields.update({
                            "video_path": asset["path"],
                            "submitted_video_prompt": submitted_prompt,
                            "continuity_input_path": first,
                            "need_dynamic_video": True,
                            "stale_tail": False,
                            "status": "video_done",
                            "updated_at": now,
                        })
                    else:
                        if scene.get("stale_tail") or scene.get("video_path") != cached_path or (
                            scene.get("transition_type") == "continue_action" and scene.get("continuity_input_path") != first
                        ):
                            scene_fields.update({
                                "video_path": cached_path,
                                "continuity_input_path": first,
                                "need_dynamic_video": True,
                                "stale_tail": False,
                                "status": "video_done",
                            })
                        await record_reuse({"task_id": task_id, **(task or {})}, "video", sid)

                    scene["video_path"] = asset["path"]
                    effective = align_measured_motion(scene)
                    scene["effective_motion_beats"] = effective.get("motion_beats")
                    scene["effective_end_state"] = effective.get("end_state")
                    scene_fields.update({
                        "effective_motion_beats": effective.get("motion_beats"),
                        "effective_end_state": effective.get("end_state"),
                    })
                    if scene.get("transition_type") == "continue_action":
                        actual_first = f"backend/assets/{task_id}/frames/{sid}_first.png"
                        await anyio.to_thread.run_sync(extract_tail, asset["path"], resolve_media(actual_first), 0)
                        join_review = await anyio.to_thread.run_sync(compare_join_frames, first, actual_first)
                        scene_fields.update({
                            "actual_first_frame_path": actual_first,
                            "join_review": join_review,
                        })

                    previous_tail = None
                    if index + 1 < len(scenes) and scenes[index + 1].get("transition_type") == "continue_action":
                        destination = f"backend/assets/{task_id}/frames/{sid}_tail.png"
                        await anyio.to_thread.run_sync(extract_tail, asset["path"], resolve_media(destination), duration)
                        previous_tail = destination
                        scene_fields["last_frame_path"] = previous_tail

                    outcomes[index] = {
                        "kind": "ok",
                        "key": key,
                        "asset": asset,
                        "asset_needs_replace": asset_needs_replace,
                        "scene_fields": scene_fields,
                    }

            chain_results = await asyncio.gather(*(_run_chain(c) for c in chains), return_exceptions=True)

            assets = []
            for index in sorted(outcomes):
                item = outcomes[index]
                if item["kind"] == "fallback":
                    await mongodb.update_one(SCENES_COLLECTION, item["key"], {
                        "video_path": None, "image_path": item["first"], "need_dynamic_video": False,
                        "status": "static_fallback", "fallback_reason": item["reason"],
                    })
                    continue
                asset = item["asset"]
                if item["asset_needs_replace"]:
                    asset_id = str(asset["asset_id"])
                    await mongodb.replace_one(ASSETS_COLLECTION, {"_id": asset_id},
                                              with_document_id(asset, asset_id), upsert=True)
                if item["scene_fields"]:
                    await mongodb.update_one(SCENES_COLLECTION, item["key"], item["scene_fields"])
                assets.append(asset)

            for res in chain_results:
                if isinstance(res, BaseException):
                    raise res

            if trial_scene_id is None:
                await mark_agent_success(task_id, self.agent_name, progress=92, status=VideoTaskStatus.VIDEO_GENERATING,
                                         message="Required video clips are ready",
                                         extra_fields={"metadata.dynamic_video_count": len(assets)})
            return assets
        except Exception as exc:
            if trial_scene_id is None:
                await mark_agent_failed(task_id, self.agent_name, exc)
            raise
