import math
from copy import deepcopy

from services.generation_contract import voice_speed, voice_window


def narration_speed(scene, duration, slot):
    speed = scene.get("audio_speed")
    if speed is None:
        return voice_speed(scene["scene_id"], duration, slot)
    speed = float(speed)
    if not math.isfinite(speed) or not 0.9 <= speed <= 1.25:
        raise ValueError("语速必须在0.9至1.25倍之间")
    if duration / speed > slot + 0.001:
        raise ValueError("配音超出当前镜头，请提前起点或提高语速")
    return speed


def retime_audio(scene, edits):
    result = deepcopy(scene)
    audio = result.get("dialogue_audio") or []
    if audio:
        if len(edits) != len(audio):
            raise ValueError("请为每句对白提交一个时间设置")
        previous_end = 0.0
        for line, edit in zip(audio, edits):
            # Older records store playback duration and speed, not source duration.
            duration = float(line.get("source_duration") or
                             (line["end"] - line["start"]) * line["speed"])
            start, speed = float(edit["start"]), float(edit["speed"])
            if not all(math.isfinite(value) for value in (start, speed, duration)):
                raise ValueError("配音时间必须为有限数字")
            if not 0.9 <= speed <= 1.25 or duration <= 0 or start < previous_end - 0.001:
                raise ValueError("对白顺序不能重叠，语速须在0.9至1.25倍之间")
            end = start + duration / speed
            if end > float(scene["duration"]) + 0.001:
                raise ValueError("配音超出当前镜头，请提前起点或提高语速")
            line.update(start=start, end=end, speed=speed, source_duration=duration)
            previous_end = end
        return {"dialogue_audio": audio}
    if len(edits) != 1 or not scene.get("audio_duration"):
        raise ValueError("没有可调整的配音")
    result["voice_start"] = float(edits[0]["start"])
    result["audio_speed"] = float(edits[0]["speed"])
    _, slot = voice_window(result)
    narration_speed(result, float(result["audio_duration"]), slot)
    return {"voice_start": result["voice_start"], "audio_speed": result["audio_speed"],
            "voice_start_locked": True}
