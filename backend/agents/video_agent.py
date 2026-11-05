from datetime import UTC, datetime
from hashlib import sha256

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
    continuity_input,
    format_motion_beats,
    needs_video,
    validate_motion_beats,
    validate_scene_durations,
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


def video_prompt_options(service, scene):
    negative = scene.get("video_negative_prompt")
    if isinstance(negative, str) and negative.strip() and getattr(service, "supports_negative_prompt", False) is True:
        return {"negative_prompt": negative.strip()}
    return {}


def render_signature(service, first_digest, prompt, duration, ratio, *, negative_prompt=None):
    provider = f"{type(service).__module__}.{type(service).__qualname__}:{getattr(service, 'model', '')}:{getattr(service, 'api_base', '')}"
    parts = (provider, first_digest, prompt, duration, ratio)
    if negative_prompt:
        parts += (negative_prompt,)
    return sha256(repr(parts).encode()).hexdigest()


async def prepare_motion_prompt(scene, previous, first, first_digest):
    saved = scene.get("observed_handoff") or {}
    if scene.get("transition_type") != "continue_action" or not previous or not previous.get("video_path"):
        return _build_motion_prompt(scene), None
    previous_beats = previous.get("motion_beats") or []
    previous_state = previous_beats[-1].get("end_state") if previous_beats else previous.get("end_state")
    scene = {**scene, "previous_action_state": previous_state}
    revision = sha256(_build_motion_prompt(scene).encode()).hexdigest()
    if saved.get("digest") == first_digest and saved.get("revision") == revision:
        plan = saved["plan"]
    elif review_enabled():
        plan = await anyio.to_thread.run_sync(replan_from_tail, previous, scene, first)
    else:
        return _build_motion_prompt(scene), None
    return _build_motion_prompt({**scene, **plan}), {"digest": first_digest, "revision": revision, "plan": plan}


def _build_motion_prompt(scene):
    beats = scene.get("motion_beats")
    validate_motion_beats(beats, float(scene["duration"]), detailed=True)

    shot_type = scene.get("shot_type") or "中景"
    camera_motion = scene.get("camera_motion") or "固定机位"

    # 1. 镜头配方 (video-shotcraft 语法: 景别与运镜模式)
    plan = scene.get("camera_plan")
    if plan:
        offset = scene.get("camera_section_offset") or 0
        direction = camera_description(plan, offset, float(scene["duration"]))
        framing_goal = plan["closing"] if offset else plan["handoff"]
        recipe_header = (
            f"[统一运镜] {direction} 目标构图：{framing_goal}。镜头任务：{plan['purpose']}。"
        )
    else:
        recipe_header = f"[运镜指令] 景别: {shot_type}，运镜: {camera_motion}。从首帧画面自然推进。"

    # 2. 秒级时间切片进度 (seedance2-skill 语法: 结构化动作演进与动力学向量)
    timeline_slices = []
    for b in beats:
        fields = []
        if b.get("camera") and b["camera"] != "沿用分组运镜计划":
            fields.append(f"构图与机位：{b['camera']}")
        if b.get("start_state"):
            fields.append(f"起始画面与位置：{b['start_state']}")
        fields.append(f"可见动作：{b.get('subject', '')} {b['action']}".strip())
        if b.get("end_state"):
            fields.append(f"秒末画面：{b['end_state']}")
        timeline_slices.append(f"【{b['start']}-{b['end']}秒】" + "｜".join(fields))
    timeline_block = "\n".join(timeline_slices)

    # 3. 角色一致性与物理结算 (Seedance 角色锁定与尾帧锚定)
    continuity = scene.get("continuity_note") or scene.get("scene_continuity") or "保持首帧主体面孔、发型服装与光影一致"
    opening_pose = beats[0].get("start_state") if beats else scene.get("start_state")
    settle = scene.get("end_state") or (beats[-1].get("end_state") if beats else None) or scene.get("last_frame_for_transition") or "动作自然收止稳定"

    pose_block = f"[动作起止状态] 起手姿态: {opening_pose}；末尾定格姿态: {settle}。\n" if opening_pose else ""
    consistency_block = (
        f"{pose_block}"
        f"[角色锁定] 严格锚定首帧画面，{continuity}，严禁角色突变或衣服变色。\n"
        f"[结尾帧锚定] 最终帧停留在: {settle}。"
    )

    # The section camera plan and structured beats are authoritative in paired mode.
    base_prompt = "" if plan else str(scene.get("video_prompt") or "").strip()
    handoff = ""
    if scene.get("transition_type") == "continue_action":
        handoff = (
            "[实际尾帧续接] 输入图就是上一段的实际结尾，第一帧必须沿用其中真实出现的人物、姿态、"
            "机位、光线和道具位置。若文字起始状态和图像冲突，以图像为准。"
            "已经完成的动作不要重演，已落下或离开画面的道具不得突然回到原处。\n"
        )
        previous_state = scene.get("previous_action_state")
        if previous_state:
            handoff += f"[动作交接] 第1秒从上一拍状态继续：{previous_state}。保留接触点、视线和运动方向。\n"
    return f"{handoff}{recipe_header}\n{base_prompt}\n[秒级动作演进]\n{timeline_block}\n{consistency_block}"


def video_service_for_task(task):
    if task.get("generation_mode") == WHOLE_GENERATION_MODE:
        from services.video.wan_video_service import WanVideoService
        return WanVideoService(model="wan3.0-video")
    return get_video_service()


class VideoAgent:
    agent_name = "VideoAgent"

    async def run(self, task_id: str) -> list[dict[str, object]]:
        try:
            await mark_agent_start(task_id, self.agent_name, progress=88, status=VideoTaskStatus.VIDEO_GENERATING)
            query = {"task_id": task_id}
            scenes = await mongodb.find_many(SCENES_COLLECTION, query, limit=100)
            scenes.sort(key=lambda s: int(s.get("scene_index", 0)))
            task = await mongodb.find_one("video_tasks", {"_id": task_id}) or {}
            ratio = str(task.get("ratio", "9:16"))
            paired = task.get("generation_mode") == PAIRED_GENERATION_MODE
            native = task.get("generation_mode") == NATIVE_GENERATION_MODE
            if paired:
                validate_paired_scenes(scenes, task.get("duration"))
            elif native:
                validate_native_scenes(scenes, task.get("duration"))
                for scene in scenes:
                    validate_motion_beats(scene.get("motion_beats"), 10, detailed=True, per_second=True)
            else:
                validate_scene_durations(scenes, task.get("duration"), whole=task.get("generation_mode") == WHOLE_GENERATION_MODE)
            # Fail before submitting paid clips if the soundtrack cannot fit the timeline.
            for scene in scenes:
                if scene.get("dialogue_lines"):
                    audio = scene.get("dialogue_audio") or []
                    if len(audio) != len(scene["dialogue_lines"]) or not all(resolve_media(line["path"]).is_file() for line in audio):
                        raise ValueError(f"Dialogue tracks not ready for scene {scene['scene_id']}")
                    continue
                if str(scene.get("voiceover") or "").strip():
                    audio_path = scene.get("audio_path")
                    if not audio_path or not resolve_media(audio_path).is_file():
                        raise ValueError(f"Voice track not ready for scene {scene['scene_id']}")
                    voice_speed(scene["scene_id"], float(scene.get("audio_duration") or 0), voice_window(scene)[1])
            stored = await mongodb.find_many(ASSETS_COLLECTION, query, limit=200)
            images = {a["scene_id"]: a for a in stored if a.get("asset_type") == "image"}
            videos = {}
            for asset in stored:  # Database orders newest first.
                if asset.get("asset_type") == "video_clip":
                    videos.setdefault(asset["scene_id"], asset)
            service = video_service_for_task(task)
            assets = []
            previous_tail = None
            for index, scene in enumerate(scenes):
                sid = str(scene["scene_id"])
                key = {"_id": f"{task_id}_{sid}"}
                image = scene.get("image_path") or images.get(sid, {}).get("path")
                if not needs_video(scene):
                    if not image or not resolve_media(image).is_file():
                        raise ValueError(f"Missing scene image: {sid}")
                    previous_tail = str(image)
                    continue

                first = continuity_input(scene, previous_tail, image)
                if not first or not resolve_media(first).is_file():
                    raise ValueError(f"Missing first frame for scene: {sid}")
                duration = int(scene["duration"])
                asset = videos.get(sid)
                cached_path = asset.get("path") if asset else None
                first_digest = sha256(resolve_media(first).read_bytes()).hexdigest()
                previous = scenes[index - 1] if index else None
                prompt, observed = await prepare_motion_prompt(scene, previous, first, first_digest)
                prompt_options = video_prompt_options(service, scene)
                signature = render_signature(service, first_digest, prompt, duration, ratio, **prompt_options)
                reusable = asset and asset.get("render_signature") == signature
                if not cached_path or not resolve_media(cached_path).is_file() or not reusable:
                    if scene.get("camera_plan") and scene.get("transition_type") != "continue_action" and review_enabled():
                        saved_check = scene.get("opening_frame_review") or {}
                        if saved_check.get("digest") != first_digest:
                            check = await anyio.to_thread.run_sync(review_opening_frame, scene, first)
                            saved_check = {"digest": first_digest, **check}
                            await mongodb.update_one(SCENES_COLLECTION, key, {"opening_frame_review": saved_check})
                        if saved_check.get("verdict") == "blocked":
                            raise ValueError(f"首帧构图不符合分组运镜计划（{sid}）：{saved_check.get('reason') or '关键接触位置不可见'}")
                    if observed:
                        await mongodb.update_one(SCENES_COLLECTION, key, {"observed_handoff": observed})
                    try:
                        result = await anyio.to_thread.run_sync(
                            lambda: service.generate_video(task_id=task_id, scene_id=sid, image_path=first,
                                                           video_prompt=prompt, duration=duration, ratio=ratio,
                                                           **prompt_options)
                        )
                    except Exception as exc:
                        if paired or native or task.get("generation_mode") == WHOLE_GENERATION_MODE or (not isinstance(exc, VideoRenderRejected) and not isinstance(exc.__cause__, VideoRenderRejected)):
                            raise
                        await mongodb.update_one(SCENES_COLLECTION, key, {
                            "video_path": None, "image_path": first, "need_dynamic_video": False,
                            "status": "static_fallback", "fallback_reason": str(exc),
                        })
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
                    asset["submitted_video_prompt"] = prompt
                    await mongodb.replace_one(ASSETS_COLLECTION, {"_id": asset_id},
                                              with_document_id(asset, asset_id), upsert=True)
                    await mongodb.update_one(SCENES_COLLECTION, key, {"video_path": asset["path"],
                                              "submitted_video_prompt": prompt,
                                              "continuity_input_path": first, "need_dynamic_video": True, "stale_tail": False,
                                              "status": "video_done", "updated_at": now})
                elif scene.get("stale_tail") or scene.get("video_path") != cached_path or (
                    scene.get("transition_type") == "continue_action" and scene.get("continuity_input_path") != first
                ):
                    await mongodb.update_one(SCENES_COLLECTION, key, {"video_path": cached_path,
                                              "continuity_input_path": first,
                                              "need_dynamic_video": True, "stale_tail": False, "status": "video_done"})
                scene["video_path"] = asset["path"]
                assets.append(asset)
                if scene.get("transition_type") == "continue_action":
                    actual_first = f"backend/assets/{task_id}/frames/{sid}_first.png"
                    await anyio.to_thread.run_sync(extract_tail, asset["path"], resolve_media(actual_first), 0)
                    join_review = await anyio.to_thread.run_sync(compare_join_frames, first, actual_first)
                    await mongodb.update_one(SCENES_COLLECTION, key, {
                        "actual_first_frame_path": actual_first, "join_review": join_review,
                    })

                # Only decode tails that are required by the next shot. Use the displayed end,
                # since the editor may trim excess frames returned by the provider.
                previous_tail = None
                if index + 1 < len(scenes) and scenes[index + 1].get("transition_type") == "continue_action":
                    destination = f"backend/assets/{task_id}/frames/{sid}_tail.png"
                    await anyio.to_thread.run_sync(extract_tail, asset["path"], resolve_media(destination), duration)
                    previous_tail = destination
                    await mongodb.update_one(SCENES_COLLECTION, key, {"last_frame_path": previous_tail})

            await mark_agent_success(task_id, self.agent_name, progress=92, status=VideoTaskStatus.VIDEO_GENERATING,
                                     message="Required video clips are ready",
                                     extra_fields={"metadata.dynamic_video_count": len(assets)})
            return assets
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
