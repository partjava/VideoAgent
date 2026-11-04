"""Shared camera direction for the two clips in one continuous section."""
from math import isfinite


def normalize_camera_plan(raw, duration):
    if not isinstance(raw, dict):
        raise ValueError("Missing section camera plan")
    plan = {}
    for field in ("purpose", "focus", "framing", "viewpoint", "opening", "closing", "handoff"):
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Camera plan requires {field}")
        plan[field] = value.strip()
    move = raw.get("movement")
    if move not in {"fixed", "track", "pan", "tilt", "push", "pull"}:
        raise ValueError("Unsupported camera movement")
    start = float(raw.get("move_start", 0))
    end = float(raw.get("move_end", 0))
    if not isfinite(start) or not isfinite(end) or not 0 <= start <= end <= duration:
        raise ValueError("Camera movement must stay inside the section")
    direction = str(raw.get("direction") or "").strip()
    if move != "fixed" and (not direction or end <= start):
        raise ValueError("Moving camera requires direction and a positive time window")
    plan.update(movement=move, direction=direction, move_start=start, move_end=end)
    return plan


def camera_description(plan, offset, duration):
    label = {"fixed": "固定", "track": "跟拍", "pan": "水平摇摄", "tilt": "垂直摇摄",
             "push": "推进", "pull": "拉远"}[plan["movement"]]
    text = f"{plan['framing']}，{plan['viewpoint']}。观众须看清：{plan['focus']}。"
    if plan["movement"] == "fixed":
        return text + "摄影机固定，保持同一构图和镜头轴线。"
    start = max(0, plan["move_start"] - offset)
    end = min(duration, plan["move_end"] - offset)
    if end <= start:
        return text + "本段保持同一机位、景别和轴线。"
    return text + f"本段 {start:g}–{end:g} 秒{label}，{plan['direction']}。其余时间稳住构图，不切换轴线。"
