import json
import anyio
from datetime import UTC, datetime
from typing import Any

from core.database import mongodb
from models.task_model import VideoTaskStatus
from services.provider_factory import get_llm_service
from services.task_service import task_service
from services.camera_plan import normalize_camera_plan
from services.generation_contract import (
    NATIVE_GENERATION_MODE,
    PAIRED_GENERATION_MODE,
    STORYBOARD_FLOW_MODE,
    rebalance_motion_beats_duration,
)
from services.visual_style import PHOTO_RULES, PHOTO_EXCLUSIONS, lock_visual_style
from services.character_binding import (
    characters_in_scene,
    frozen_characters,
    reference_choices_for_scene,
    series_prompt_context,
)
from services.drama_craft_service import (
    build_eight_segment_image_prompt,
    build_mcsla_video_overview,
    ensure_scene_blocking,
    infer_transition_mode,
    sanitize_video_prompt_mcsla,
    strip_redundant_face_dna_when_referenced,
)
from services.shot_continuity import build_scene_continuity_handoff

DEFAULT_IMAGE_NEGATIVE_PROMPT = "多余肢体，手指结构混乱，五官错位，无关文字，水印，主体边缘异常断裂，纯白影棚背景，Q版大头比例"
DEFAULT_VIDEO_NEGATIVE_PROMPT = "道具瞬移，非计划抖动，突然变脸，肢体穿模，多余肢体，多余手臂，断臂嫁接，双胞胎重影，画外角色闯入，服装颜色突变，画面撕裂，非计划倒放，画面内自带字幕，文字气泡，英文乱码，水印"
PROMPT_BATCH_SIZE_PER_SECOND = 3
PROMPT_BATCH_SIZE_FLOW = 4


def _extract_scene_number(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def _match_batch_prompts(raw_batch: Any, chunk_scenes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """将小批次 LLM 返回的提示词条目对齐到 chunk_scenes，兼容模型偶发写错 scene_id（如 '1' -> 'scene_01'）。"""
    if not isinstance(raw_batch, list):
        return {}
    valid_items = [dict(item) for item in raw_batch if isinstance(item, dict)]
    if not valid_items:
        return {}

    matched: dict[str, dict[str, Any]] = {}
    used_indices: set[int] = set()

    # Pass 1: 精确匹配 scene_id
    for scene in chunk_scenes:
        sid = str(scene["scene_id"])
        for idx, item in enumerate(valid_items):
            if idx in used_indices:
                continue
            if str(item.get("scene_id") or "").strip() == sid:
                item["scene_id"] = sid
                matched[sid] = item
                used_indices.add(idx)
                break

    # Pass 2: 按 scene_index 或纯数字后缀匹配
    for scene in chunk_scenes:
        sid = str(scene["scene_id"])
        if sid in matched:
            continue
        target_idx = _extract_scene_number(scene.get("scene_index")) or _extract_scene_number(sid)
        if target_idx is None:
            continue
        for idx, item in enumerate(valid_items):
            if idx in used_indices:
                continue
            item_num = _extract_scene_number(item.get("scene_id")) or _extract_scene_number(item.get("scene_index"))
            if item_num == target_idx:
                item["scene_id"] = sid
                matched[sid] = item
                used_indices.add(idx)
                break

    # Pass 3: 若返回条数与本批剩余待匹配数一致，按顺序兜底对齐
    remaining_scenes = [s for s in chunk_scenes if str(s["scene_id"]) not in matched]
    remaining_items = [item for idx, item in enumerate(valid_items) if idx not in used_indices]
    if remaining_scenes and len(remaining_scenes) == len(remaining_items):
        for scene, item in zip(remaining_scenes, remaining_items):
            sid = str(scene["scene_id"])
            item["scene_id"] = sid
            matched[sid] = item

    return matched


def _build_previous_context(scene: dict[str, Any], prompt: dict[str, Any]) -> dict[str, Any]:
    handoff = scene.get("continuity_handoff") or {}
    return {
        "scene_index": scene["scene_index"],
        "visual_description": scene.get("visual_description"),
        "voiceover": scene.get("voiceover"),
        "blocking": prompt.get("blocking"),
        "next_transition_type": scene.get("transition_type") or handoff.get("mode"),
        "key_action": prompt.get("video_prompt"),
        "final_second": prompt["motion_beats"][-1],
        "closing_composition": prompt.get("last_frame_for_transition"),
    }


def _format_visual_bible_brief(bible: dict[str, Any], scene: dict[str, Any]) -> str:
    """将世界观与本镜出场角色外观转为精炼自然语言，严禁把 JSON / tts_voice 等元数据塞进生图提示词。"""
    parts: list[str] = []
    world_txt = str(bible.get("visual_world") or "").strip()
    if world_txt:
        parts.append(f"场景世界观：{world_txt[:180]}")
    scene_blob = " ".join(
        [
            str(scene.get("visual_description") or ""),
            str(scene.get("speaker") or ""),
            str(scene.get("start_state") or ""),
            " ".join(str(line.get("speaker") or "") for line in (scene.get("dialogue_lines") or []) if isinstance(line, dict)),
        ]
    )
    chars = [c for c in (bible.get("characters") or []) if isinstance(c, dict) and str(c.get("name") or "").strip()]
    matched = [c for c in chars if str(c.get("name")).strip() in scene_blob]
    picked = matched[:2] if matched else chars[:2]
    char_descs: list[str] = []
    for ch in picked:
        name = str(ch.get("name") or "").strip()
        look = str(ch.get("appearance") or ch.get("face_dna") or "").strip()
        cloth = str(ch.get("signature_clothing") or "").strip()
        summary = "；".join(x for x in (look[:90], f"服装：{cloth[:60]}" if cloth and cloth not in look else "") if x)
        if name and summary:
            char_descs.append(f"{name}（{summary}）")
    if char_descs:
        parts.append("出场角色造型：" + "、".join(char_descs))
    return "。".join(parts)


def _normalize_motion_beats(
    raw_beats: Any,
    duration: int,
    scene: dict[str, Any],
    *,
    per_second: bool,
) -> list[dict[str, Any]]:
    """清洗并对齐大模型输出的 motion_beats（修正字符串秒数、缺失字段、尾秒偏差及逐秒切分）。"""
    dur = max(1, int(duration))
    default_subject = str(scene.get("speaker") or "画面主体").strip() or "画面主体"
    if default_subject in {"旁白", "无", "解说"}:
        default_subject = "画面主体"
    default_cam = f"{scene.get('shot_type') or '中景'}，{scene.get('camera_motion') or '稳定机位'}"
    default_start = str(scene.get("start_state") or scene.get("visual_description") or "首帧起始站位姿态").strip()[:120]
    default_end = str(scene.get("end_state") or scene.get("visual_description") or "动作自然落定稳态").strip()[:120]
    default_action = str(scene.get("visual_description") or "主体顺着首帧势能自然推进动作").strip()[:120]

    cleaned: list[dict[str, Any]] = []
    if isinstance(raw_beats, list):
        for item in raw_beats:
            if not isinstance(item, dict):
                continue
            try:
                st = float(item.get("start", 0))
                en = float(item.get("end", 0))
            except (TypeError, ValueError):
                st, en = 0.0, 0.0
            cleaned.append(
                {
                    "start": st,
                    "end": en,
                    "subject": str(item.get("subject") or default_subject).strip() or default_subject,
                    "camera": str(item.get("camera") or default_cam).strip() or default_cam,
                    "start_state": str(item.get("start_state") or default_start).strip() or default_start,
                    "action": str(item.get("action") or default_action).strip() or default_action,
                    "end_state": str(item.get("end_state") or default_end).strip() or default_end,
                }
            )

    if not cleaned:
        cleaned = [
            {
                "start": 0,
                "end": dur,
                "subject": default_subject,
                "camera": default_cam,
                "start_state": default_start,
                "action": default_action,
                "end_state": default_end,
            }
        ]

    if per_second:
        if len(cleaned) == dur:
            result = []
            for sec, item in enumerate(cleaned):
                entry = {**item, "start": sec, "end": sec + 1}
                if result:
                    entry["start_state"] = result[-1]["end_state"]
                result.append(entry)
            return result
        # 将连续时间块均匀展开为严格 1 秒整数区间，保持首尾状态与运镜连贯
        expanded: list[dict[str, Any]] = []
        src_count = len(cleaned)
        for sec in range(dur):
            src_idx = min(src_count - 1, int(sec * src_count / dur))
            src = cleaned[src_idx]
            entry = {
                "start": sec,
                "end": sec + 1,
                "subject": src["subject"],
                "camera": src["camera"],
                "start_state": expanded[-1]["end_state"] if expanded else src["start_state"],
                "action": src["action"],
                "end_state": src["end_state"],
            }
            expanded.append(entry)
        return expanded

    # 非逐秒模式（storyboard_flow）：重排连续时间块严格覆盖 [0, dur]
    old_end = int(round(cleaned[-1]["end"])) if cleaned[-1]["end"] > 0 else dur
    if old_end != dur or cleaned[0]["start"] != 0 or any(
        b["end"] <= b["start"] or (i > 0 and b["start"] != cleaned[i - 1]["end"])
        for i, b in enumerate(cleaned)
    ):
        if len(cleaned) == 1:
            cleaned[0]["start"] = 0
            cleaned[0]["end"] = dur
        else:
            step = dur / len(cleaned)
            cursor = 0
            rebuilt = []
            for idx, item in enumerate(cleaned):
                nxt = dur if idx == len(cleaned) - 1 else max(cursor + 1, min(dur - (len(cleaned) - 1 - idx), int(round((idx + 1) * step))))
                if nxt <= cursor:
                    continue
                rebuilt.append({**item, "start": cursor, "end": nxt})
                cursor = nxt
            if rebuilt:
                rebuilt[-1]["end"] = dur
                cleaned = rebuilt
            else:
                cleaned = [{**cleaned[0], "start": 0, "end": dur}]
    for prior, current in zip(cleaned, cleaned[1:]):
        current["start_state"] = prior["end_state"]
    return cleaned

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
            storyboard_flow = task.get("generation_mode") == STORYBOARD_FLOW_MODE
            per_second = not storyboard_flow
            for index, scene in enumerate(task_scenes):
                if paired:
                    scene["camera_section_offset"] = 5 * (index % 2)
                    scene["camera_section_number"] = index // 2 + 1
                scene["continuity_handoff"] = build_scene_continuity_handoff(
                    task_scenes, index, ratio=str(task.get("ratio", "9:16")), bound_cast=frozen_characters(task) or None
                )
            # 旧字符串节拍必须重新生成，不能仅凭图片提示词复用。
            def complete(scene):
                try:
                    validate_motion_beats(scene.get("motion_beats"), float(scene["duration"]), detailed=True, per_second=per_second)
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
            bible = {"characters": world.get("characters", []), "visual_world": world.get("visual_world", ""),
                     "series_context": series_prompt_context(task)}
            fixed = json.dumps(bible, ensure_ascii=False)

            bound_cast = frozen_characters(task) or world.get("characters", [])
            all_cast_names = [c.get("name") for c in bound_cast if isinstance(c, dict) and c.get("name")]
            try:
                from services.provider_factory import get_image_service

                image_supports_refs = bool(getattr(get_image_service(), "supports_reference_images", True))
            except Exception:
                image_supports_refs = True
            prompt_bible = {**bible, "characters": bound_cast} if not image_supports_refs and bound_cast else bible

            def generate(batch):
                kwargs = {"ratio": ratio, "style": _style, "visual_bible": bible}
                if not per_second:
                    kwargs["per_second"] = False
                return llm_service.generate_image_prompts(batch, **kwargs)

            def prepare(prompt, scene):
                beats = _normalize_motion_beats(
                    prompt.get("motion_beats"),
                    int(scene["duration"]),
                    scene,
                    per_second=per_second,
                )
                prompt["motion_beats"] = beats
                validate_motion_beats(beats, float(scene["duration"]), detailed=True, per_second=per_second)
                if not native and scene.get("start_state"):
                    beats[0]["start_state"] = scene["start_state"]
                if not native and scene.get("end_state"):
                    beats[-1]["end_state"] = scene["end_state"]
                for prior, current in zip(beats, beats[1:]):
                    current["start_state"] = prior["end_state"]
                if not str(prompt.get("image_prompt") or "").strip():
                    prompt["image_prompt"] = str(scene.get("visual_description") or scene.get("start_state") or "").strip()
                if not str(prompt.get("video_prompt") or "").strip():
                    prompt["video_prompt"] = str(scene.get("visual_description") or prompt.get("image_prompt") or "").strip()
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

                # ── 空间站位声明（Blocking）与段缝衔接模式 ──
                prompt["blocking"] = prompt.get("blocking") or scene.get("blocking") or ensure_scene_blocking(scene, bound_cast)
                prompt["transition_mode"] = prompt.get("transition_mode") or scene.get("transition_mode") or infer_transition_mode(scene)
                handoff = scene.get("continuity_handoff") or {}
                if handoff.get("next_is_continue") and beats:
                    last_end_state = str(beats[-1].get("end_state") or "").strip("。 ")
                    if last_end_state and "稳态" not in last_end_state:
                        beats[-1]["end_state"] = f"{last_end_state}，收束至清晰可截稳态"
                prompt["continuity_handoff"] = handoff

                # ── 分镜首帧三层工艺分离（绑定参考图时自动剥离五官重描 + 单镜具名角色 ≤2 人） ──
                scene_refs = reference_choices_for_scene(task, scene) if image_supports_refs else []
                visible_cast = characters_in_scene(task, scene)
                if image_supports_refs and scene_refs and visible_cast:
                    primary_cast = visible_cast[:2]
                    overflow_cast = visible_cast[2:]
                    stripped_img = strip_redundant_face_dna_when_referenced(prompt["image_prompt"], primary_cast)
                    prompt["image_prompt"] = build_eight_segment_image_prompt(
                        {**scene, **prompt},
                        stripped_img,
                        bound_profiles=primary_cast,
                        overflow_profiles=overflow_cast,
                        style=_style or "短剧",
                        ratio=ratio,
                    )
                    prompt["three_layer_stripped"] = True
                else:
                    bible_brief = _format_visual_bible_brief(prompt_bible, scene)
                    prefix_lines = []
                    if bible_brief:
                        prefix_lines.append(bible_brief)
                    if scene.get("start_state"):
                        prefix_lines.append(f"首帧起始状态：{scene['start_state']}")
                    if prefix_lines:
                        prompt["image_prompt"] = "\n".join(prefix_lines) + "\n" + str(prompt["image_prompt"])
                    prompt["three_layer_stripped"] = False

                # ── 视频提示词 MCSLA 五层结构 + 动作物理化清洗 + 敏感词转译（≤1400字） ──
                sanitized_video = sanitize_video_prompt_mcsla(
                    prompt["video_prompt"],
                    scene={**scene, **prompt},
                    all_character_names=all_cast_names,
                )
                prompt["video_prompt"] = sanitized_video["prompt"]
                prompt["mcsla_summary"] = build_mcsla_video_overview(
                    {**scene, **prompt},
                    all_character_names=all_cast_names,
                )

                if _style == "短剧":
                    style_lock = PHOTO_RULES
                    prompt["image_prompt"] = lock_visual_style(prompt["image_prompt"], _style)
                    prompt["video_prompt"] = lock_visual_style(prompt["video_prompt"], _style)
                    prompt["continuity_note"] = f"{style_lock}{prompt.get('continuity_note') or ''}"
                    prompt["image_negative_prompt"] = PHOTO_EXCLUSIONS + "，" + str(prompt.get("image_negative_prompt") or "")
                    prompt["video_negative_prompt"] = PHOTO_EXCLUSIONS + "，" + str(prompt.get("video_negative_prompt") or "")

            sequential = native or not paired
            if sequential:
                prompts = []
                previous_context = None
                rebuilding = False
                chunk_size = PROMPT_BATCH_SIZE_PER_SECOND if per_second else PROMPT_BATCH_SIZE_FLOW
                cursor = 0
                total_scenes = len(task_scenes)

                async def _generate_single_scene(scene_obj: dict[str, Any], prev_ctx: dict[str, Any] | None) -> dict[str, Any]:
                    current = {**scene_obj, "previous_segment_context": prev_ctx}
                    batch_out = None
                    last_exc = None
                    for _attempt in range(2):
                        try:
                            batch_out = await anyio.to_thread.run_sync(generate, [current], abandon_on_cancel=True)
                            if isinstance(batch_out, list) and len(batch_out) == 1 and isinstance(batch_out[0], dict):
                                batch_out[0]["scene_id"] = str(scene_obj["scene_id"])
                                break
                            last_exc = ValueError("Prompt result must match the requested segment")
                        except Exception as exc:
                            last_exc = exc
                            batch_out = None
                    if not batch_out or len(batch_out) != 1:
                        raise last_exc or ValueError("Prompt result must match the requested segment")
                    item = batch_out[0]
                    item["previous_segment_context"] = prev_ctx
                    prepare(item, scene_obj)
                    await self._save_prompt(task_id, item)
                    return item

                while cursor < total_scenes:
                    scene = task_scenes[cursor]
                    if not rebuilding and complete(scene):
                        previous_context = _build_previous_context(scene, scene)
                        prompts.append(scene)
                        cursor += 1
                        continue

                    rebuilding = True
                    chunk = task_scenes[cursor:cursor + chunk_size]
                    await task_service.update_task_fields(task_id, {
                        "metadata.prompt_count": len(prompts),
                        "metadata.prompt_active_segment": cursor + 1,
                    })

                    matched_map: dict[str, dict[str, Any]] = {}
                    if len(chunk) > 1:
                        chunk_inputs = [
                            {**sc, "previous_segment_context": previous_context if i == 0 else None}
                            for i, sc in enumerate(chunk)
                        ]
                        try:
                            raw_chunk = await anyio.to_thread.run_sync(generate, chunk_inputs, abandon_on_cancel=True)
                            matched_map = _match_batch_prompts(raw_chunk, chunk)
                        except Exception as exc:
                            print(
                                f"[PromptAgent] Batch ({len(chunk)} scenes from #{cursor + 1}) failed ({exc}); falling back to single-scene generation.",
                                flush=True,
                            )
                            matched_map = {}

                    for offset, sc in enumerate(chunk):
                        sid = str(sc["scene_id"])
                        candidate = matched_map.get(sid)
                        prompt = None
                        if candidate is not None:
                            try:
                                candidate["previous_segment_context"] = previous_context
                                prepare(candidate, sc)
                                await self._save_prompt(task_id, candidate)
                                prompt = candidate
                            except Exception as exc:
                                print(
                                    f"[PromptAgent] Batch item {sid} failed prepare ({exc}); retrying single-scene.",
                                    flush=True,
                                )
                                prompt = None
                        if prompt is None:
                            await task_service.update_task_fields(task_id, {
                                "metadata.prompt_count": len(prompts),
                                "metadata.prompt_active_segment": cursor + offset + 1,
                            })
                            prompt = await _generate_single_scene(sc, previous_context)
                        previous_context = _build_previous_context(sc, prompt)
                        prompts.append(prompt)

                    cursor += len(chunk)
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
            if not sequential:
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
            "blocking", "transition_mode", "mcsla_summary", "three_layer_stripped", "continuity_handoff",
        )
        await mongodb.update_one(
            SCENES_COLLECTION,
            {"_id": f"{task_id}_{prompt['scene_id']}"},
            {**{field: prompt.get(field) for field in fields},
             "negative_prompt": prompt.get("image_negative_prompt") or prompt.get("negative_prompt"),
             "status": "prompt_done", "updated_at": datetime.now(UTC)},
        )
