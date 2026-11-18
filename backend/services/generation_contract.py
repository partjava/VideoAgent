"""Offline contracts shared by planning, generation and composition."""
import math

MAX_VOICE_SPEED = 1.25


def build_timeline(scenes):
    rows, cursor = [], 0.0
    seen = set()
    for scene in scenes:
        sid = str(scene["scene_id"])
        duration = float(scene["duration"])
        if sid in seen or not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"Invalid timeline scene: {sid}")
        seen.add(sid)
        rows.append({"scene_id": sid, "start": cursor, "end": cursor + duration, "duration": duration})
        cursor += duration
    if not rows:
        raise ValueError("Timeline is empty")
    return rows


def validate_scene_durations(scenes, target=None, *, whole=False):
    if not scenes:
        raise ValueError("Storyboard is empty")
    if whole and (len(scenes) != 1 or scenes[0].get("need_dynamic_video") is not True):
        raise ValueError("Whole-video mode requires a single dynamic clip")
    maximum = 30 if whole else 12
    for scene in scenes:
        duration = float(scene.get("duration", 0))
        if not math.isfinite(duration) or not 4 <= duration <= maximum or not duration.is_integer():
            raise ValueError(f"Scene duration must be an integer between 4 and {maximum} seconds")
    total = sum(float(s["duration"]) for s in scenes)
    if target is not None and abs(total - target) > 0.01:
        raise ValueError(f"Storyboard duration mismatch: expected {target}s, got {total}s")


WHOLE_GENERATION_MODE = "wan3_whole"
PAIRED_GENERATION_MODE = "paired_5s"
NATIVE_GENERATION_MODE = "native_10s"
AUTO_DIRECTOR_MODE = "auto_director"


def provider_duration_bounds(provider_name: str | None = None, model_name: str | None = None) -> tuple[int, int]:
    """返回当前视频生成模型支持的单段合理整数秒数区间（下限, 上限）。"""
    combined = f"{provider_name or ''} {model_name or ''}".lower()
    if "wan3" in combined or "wan2.6" in combined or "wan2.7" in combined:
        # 弹性导演建议单镜控制在 4-12 秒保证画面连贯与动作可执行性
        return (4, 12)
    if "vidu" in combined:
        return (4, 10)
    if "doubao" in combined or "seedance" in combined or "comfyui" in combined:
        return (4, 12)
    return (4, 12)


def validate_auto_director_scenes(scenes, target_duration=None, bounds: tuple[int, int] = (4, 12)):
    if not scenes:
        raise ValueError("Storyboard is empty")
    min_sec, max_sec = bounds
    for index, scene in enumerate(scenes):
        duration = float(scene.get("duration", 0))
        if (
            not math.isfinite(duration)
            or not min_sec <= duration <= max_sec
            or not duration.is_integer()
        ):
            raise ValueError(
                f"Auto-director clip {index + 1} duration must be an integer between {min_sec} and {max_sec} seconds"
            )
        if scene.get("need_dynamic_video") is not True:
            raise ValueError(f"Auto-director clip {index + 1} must be dynamic")
        if scene.get("transition_type") not in {None, "cut", "continue_action", "scene_change"}:
            raise ValueError(f"Auto-director clip {index + 1} has invalid transition_type")
    total = sum(float(s["duration"]) for s in scenes)
    if target_duration is not None and abs(total - target_duration) > 0.01:
        raise ValueError(f"Storyboard duration mismatch: expected {target_duration}s, got {total}s")


def rebalance_motion_beats_duration(beats: list[dict], old_duration: int, new_duration: int) -> list[dict]:
    """当配音反推调整了单镜秒数时，无损重整逐秒 motion_beats 至 [0..new_duration]。"""
    if new_duration <= 0:
        raise ValueError("new_duration must be positive")
    working = [dict(b) for b in (beats or []) if isinstance(b, dict)]
    if not working:
        return [
            {
                "start": i,
                "end": i + 1,
                "subject": "人物",
                "camera": "保持当前景别与轴线",
                "start_state": "保持当前站位姿态",
                "action": "顺畅延续表演、口型及视线交互",
                "end_state": "落定在稳定可读终态",
            }
            for i in range(new_duration)
        ]

    # 先转为逐秒槽位列表
    if len(working) == old_duration and all(
        b.get("start") == idx and b.get("end") == idx + 1 for idx, b in enumerate(working)
    ):
        per_sec = working
    else:
        per_sec = []
        for s in range(old_duration):
            matched = next(
                (b for b in working if float(b.get("start", 0)) <= s < float(b.get("end", 0))),
                working[-1],
            )
            per_sec.append({
                "start": s,
                "end": s + 1,
                "subject": matched.get("subject") or "人物",
                "camera": matched.get("camera") or "保持当前景别",
                "start_state": matched.get("start_state") or "承接上一秒动作状态",
                "action": matched.get("action") or "保持动作及情绪推进",
                "end_state": matched.get("end_state") or "保持自然衔接姿态",
            })

    if new_duration == len(per_sec):
        return per_sec

    if new_duration > len(per_sec):
        # 在倒数第一秒定格之前插入自然持续延展帧，保留最终结尾态与收尾稳态
        tail = dict(per_sec[-1])
        prefix = [dict(b) for b in per_sec[:-1]]
        carry_state = (prefix[-1].get("end_state") if prefix else tail.get("start_state")) or "保持当前姿态与视线"
        carry_cam = tail.get("camera") or (prefix[-1].get("camera") if prefix else "保持当前景别")
        carry_subj = tail.get("subject") or "人物"
        extra_count = new_duration - len(per_sec)
        for _ in range(extra_count):
            prefix.append({
                "subject": carry_subj,
                "camera": carry_cam,
                "start_state": carry_state,
                "action": "保持当前站位与视线焦点，自然配合发声与微表情衔接",
                "end_state": carry_state,
            })
        tail["start_state"] = carry_state
        per_sec = prefix + [tail]
    else:
        # 轻微缩短时保留开头起手与末尾收束，压缩中间静止维持段
        head = per_sec[: max(1, new_duration - 1)]
        tail = dict(per_sec[-1])
        if head:
            tail["start_state"] = head[-1].get("end_state") or tail.get("start_state")
        per_sec = head + [tail]

    for idx, beat in enumerate(per_sec):
        beat["start"] = idx
        beat["end"] = idx + 1
    return per_sec


def validate_native_scenes(scenes, target_duration):
    validate_scene_durations(scenes, target_duration)
    for index, scene in enumerate(scenes):
        if (scene["duration"] != 10 or scene.get("transition_type", "cut") != "cut"
                or scene.get("need_dynamic_video") is not True):
            raise ValueError(f"Native clip {index + 1} must be one dynamic 10-second clip with its own first frame")


def validate_paired_scenes(scenes, target_duration):
    validate_scene_durations(scenes, target_duration)
    if len(scenes) % 2:
        raise ValueError("Paired mode needs two clips per section")
    for index, scene in enumerate(scenes):
        expected = "cut" if index % 2 == 0 else "continue_action"
        if (scene["duration"] != 5 or scene.get("transition_type") != expected
                or scene.get("need_dynamic_video") is not True):
            raise ValueError(f"Paired clip {index + 1} must be dynamic, 5 seconds, and use {expected}")


def _motion_seconds(value):
    if isinstance(value, str):
        value = value.strip().lower()
        if value.endswith(("s", "秒")):
            value = value[:-1].strip()
    return float(value)


def validate_motion_beats(beats, duration, *, detailed=False, per_second=False):
    if not isinstance(beats, list) or not beats:
        raise ValueError("Motion beats must cover entire scene")
    if per_second and len(beats) != duration:
        raise ValueError("Motion beats must have exactly one entry per second")
    cursor = 0.0
    normalized = []
    for index, beat in enumerate(beats):
        if not isinstance(beat, dict):
            raise ValueError("Motion beat must be structured with start/end/action")
        try:
            start, end = _motion_seconds(beat["start"]), _motion_seconds(beat["end"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Motion beat {index + 1} must have valid start/end seconds") from exc
        if not all(math.isfinite(t) for t in (start, end)) or abs(start - cursor) > 0.01 or end <= start or end > duration + 0.01:
            raise ValueError("Motion beats must cover scene without gaps or overlaps")
        if per_second and (start != index or end != index + 1):
            raise ValueError("Motion beats must use consecutive one-second intervals")
        if not isinstance(beat.get("action"), str) or not beat["action"].strip():
            raise ValueError("Motion beat action is empty")
        if detailed:
            for field in ("subject", "start_state", "end_state", "camera"):
                if not isinstance(beat.get(field), str) or not beat[field].strip():
                    raise ValueError(f"Motion beat is missing {field}")
        cursor = end
        normalized.append((start, end))
    if abs(cursor - duration) > 0.01:
        raise ValueError("Motion beats must cover entire scene")
    for beat, (start, end) in zip(beats, normalized):
        for field, value in (("start", start), ("end", end)):
            if isinstance(beat[field], str):
                beat[field] = int(value) if value.is_integer() else value


def needs_video(scene):
    return scene.get("need_dynamic_video") is True or scene.get("status") == "image_fallback"


def scene_event_offset(scene, offset):
    value = float(offset)
    if not math.isfinite(value) or not 0 <= value < float(scene["duration"]):
        raise ValueError(f"Event outside scene {scene['scene_id']}: {offset}")
    return value


def voice_window(scene):
    offset = scene_event_offset(scene, scene.get("voice_start", 0))
    return offset, float(scene["duration"]) - offset


def voice_speed(scene_id, audio_duration, slot_duration):
    if not math.isfinite(audio_duration) or audio_duration <= 0:
        raise ValueError(f"Invalid audio duration for scene {scene_id}")
    speed = max(1.0, audio_duration / slot_duration)
    if speed > MAX_VOICE_SPEED:
        raise ValueError(f"Voice too long for scene {scene_id}; shorten dialogue (required speed {speed:.2f}x)")
    return speed


def continuity_input(scene, previous_tail, own_image):
    if scene.get("transition_type") == "continue_action":
        if not previous_tail:
            raise ValueError("Continuous action requires previous scene's actual last frame")
        return previous_tail
    return own_image


def format_motion_beats(beats):
    return "\n".join(
        f"{b['start']}-{b['end']}秒：{b.get('subject', '')} {b['action']}；起始：{b.get('start_state', '')}；结束：{b.get('end_state', '')}；镜头：{b.get('camera', '固定')}"
        for b in beats
    )


def format_srt_time(seconds):
    whole, millis = divmod(round(seconds * 1000), 1000)
    minutes, seconds = divmod(whole, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _wrap_cjk_subtitle(text: str, max_chars: int = 15) -> str:
    """对过长的单行中文字幕按标点或视觉中点温柔折成双行，避免竖屏左右贴边。"""
    clean = str(text or "").strip()
    if not clean or "\n" in clean or max_chars <= 0 or len(clean) <= max_chars:
        return clean
    mid = len(clean) // 2
    best_idx = None
    best_dist = len(clean)
    for idx, ch in enumerate(clean[:-1]):
        if ch in "，。！？；：、…" and 4 <= idx + 1 <= max_chars + 2:
            dist = abs((idx + 1) - mid)
            if dist < best_dist:
                best_dist = dist
                best_idx = idx + 1
    if best_idx is not None:
        left = clean[:best_idx].rstrip("，、 ")
        right = clean[best_idx:].strip()
        if right:
            return f"{left}\n{right}"
    split_at = min(max_chars, max(6, mid))
    return f"{clean[:split_at]}\n{clean[split_at:].strip()}"


def render_srt(scenes, *, wrap_width: int = 15):
    cues = []
    for scene, row in zip(scenes, build_timeline(scenes)):
        if scene.get("dialogue_audio"):
            for line in scene["dialogue_audio"]:
                start, end = (format_srt_time(row["start"] + line[field]) for field in ("start", "end"))
                cues.append(f"{len(cues) + 1}\n{start} --> {end}\n{_wrap_cjk_subtitle(line['text'], wrap_width)}\n")
            continue
        text = str(scene.get("voiceover") or scene.get("subtitle") or "").strip()
        if not text:
            continue
        offset, slot = voice_window(scene)
        start = row["start"] + offset
        audio_duration = scene.get("audio_duration")
        spoken = slot
        if audio_duration and audio_duration > 0:
            from services.audio_timing import narration_speed
            spoken = float(audio_duration) / narration_speed(scene, float(audio_duration), slot)
        cues.append(f"{len(cues) + 1}\n{format_srt_time(start)} --> {format_srt_time(start + spoken)}\n{_wrap_cjk_subtitle(text, wrap_width)}\n")
    return "\n".join(cues)
