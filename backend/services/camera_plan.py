"""Shared camera direction for the two clips in one continuous section."""
from math import isfinite


def default_camera_plan(duration=10):
    return {
        "purpose": "主体动作展示与场景呈现",
        "focus": "主行动角色与关键互动点",
        "framing": "中景 (MS)",
        "viewpoint": "平视主体正面略侧微仰",
        "opening": "主体居中偏中下，环境背景清晰",
        "closing": "主体动作定格，留出后续衔接空间",
        "handoff": "主体动作进行中，保持视线与动势连续",
        "movement": "fixed",
        "direction": "保持机位稳定",
        "move_start": 0.0,
        "move_end": 0.0,
    }


def normalize_camera_plan(raw, duration=10):
    defaults = default_camera_plan(duration)
    if not isinstance(raw, dict):
        return defaults
    plan = {}
    for field in ("purpose", "focus", "framing", "viewpoint", "opening", "closing", "handoff"):
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            plan[field] = defaults[field]
        else:
            plan[field] = value.strip()
    move = raw.get("movement")
    if move not in {"fixed", "track", "pan", "tilt", "push", "pull"}:
        move = "fixed"
    try:
        start = float(raw.get("move_start", 0))
    except (ValueError, TypeError):
        start = 0.0
    try:
        end = float(raw.get("move_end", 0))
    except (ValueError, TypeError):
        end = 0.0
    if not isfinite(start) or not isfinite(end) or start < 0 or end > duration or start > end:
        start, end = 0.0, 0.0
        move = "fixed"
    direction = str(raw.get("direction") or "").strip()
    if move != "fixed" and (not direction or end <= start):
        move = "fixed"
        direction = "保持机位稳定"
        start, end = 0.0, 0.0
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
