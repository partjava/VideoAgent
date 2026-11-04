"""Offline contracts shared by planning, generation and composition."""
import math

MAX_VOICE_SPEED = 1.2


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


def validate_paired_scenes(scenes, target_duration):
    validate_scene_durations(scenes, target_duration)
    if len(scenes) % 2:
        raise ValueError("Paired mode needs two clips per section")
    for index, scene in enumerate(scenes):
        expected = "cut" if index % 2 == 0 else "continue_action"
        if (scene["duration"] != 5 or scene.get("transition_type") != expected
                or scene.get("need_dynamic_video") is not True):
            raise ValueError(f"Paired clip {index + 1} must be dynamic, 5 seconds, and use {expected}")


def validate_motion_beats(beats, duration, *, detailed=False, per_second=False):
    if not isinstance(beats, list) or not beats:
        raise ValueError("Motion beats must cover entire scene")
    if per_second and len(beats) != duration:
        raise ValueError("Motion beats must have exactly one entry per second")
    cursor = 0.0
    for index, beat in enumerate(beats):
        if not isinstance(beat, dict):
            raise ValueError("Motion beat must be structured with start/end/action")
        start, end = float(beat.get("start", -1)), float(beat.get("end", -1))
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
    if abs(cursor - duration) > 0.01:
        raise ValueError("Motion beats must cover entire scene")


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


def render_srt(scenes):
    cues = []
    for scene, row in zip(scenes, build_timeline(scenes)):
        text = str(scene.get("voiceover") or scene.get("subtitle") or "").strip()
        if not text:
            continue
        offset, slot = voice_window(scene)
        start = row["start"] + offset
        audio_duration = scene.get("audio_duration")
        spoken = slot
        if audio_duration and audio_duration > 0:
            spoken = float(audio_duration) / voice_speed(scene["scene_id"], float(audio_duration), slot)
        cues.append(f"{len(cues) + 1}\n{format_srt_time(start)} --> {format_srt_time(start + spoken)}\n{text}\n")
    return "\n".join(cues)
