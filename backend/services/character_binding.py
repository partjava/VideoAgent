import json
import re

from services.tts_service import resolve_voice_for_speaker


def frozen_characters(task):
    meta = task.get("metadata") or {}
    return (meta.get("series_context") or {}).get("characters") or meta.get("bound_characters") or []


def series_prompt_context(task):
    meta = task.get("metadata") or {}
    context = meta.get("series_context")
    if context:
        return (
            "\n连载约束：以下为已确认的本集大纲、角色版本与上一集状态。"
            "沿用角色ID、身份与当前剧情事实，保持人物已知信息和道具归属。"
            "角色发型与五官以冻结身份为准，服装变化以已确认剧情状态为准。"
            "剧情跳时或换场可以重新建立构图，但须交代转换原因。"
            "角色ID是结构化标识，不得读入台词或画入画面。\n"
            + json.dumps(context, ensure_ascii=False, default=str)
        )
    bound = meta.get("bound_characters")
    if bound:
        return (
            "\n已绑定角色资产：以下为用户从角色库中选定的出镜角色（包含角色ID、姓名、外貌特征与标志服装）。"
            "请围绕这些已绑定角色编写剧情与分镜，保持角色姓名、五官发型与标志服装一致，并在分镜 character_ids 中填写出镜角色ID。"
            "角色ID是结构化标识，不得读入台词或画入画面。\n"
            + json.dumps({"characters": bound}, ensure_ascii=False, default=str)
        )
    return ""


def characters_in_scene(task, scene):
    profiles = frozen_characters(task)
    is_series = bool((task.get("metadata") or {}).get("series_context"))
    ids = scene.get("character_ids") or []
    by_id = {profile["character_id"]: profile for profile in profiles}
    if ids:
        if is_series and any(character_id not in by_id for character_id in ids):
            raise ValueError("分镜引用了未绑定的角色ID，请检查角色与分镜")
        matched = [by_id[character_id] for character_id in dict.fromkeys(ids) if character_id in by_id]
        if matched or is_series:
            return matched
    # Compatibility for scenes made before explicit visible-cast IDs existed.
    visible = " ".join(str(scene.get(field) or "") for field in
                       ("visual_description", "start_state", "character_state"))
    return [profile for profile in profiles if profile["name"] in visible]


_WHOLE_SHOT = re.compile(
    r"全景|远景|全身|背面|背影|背对|背向|\b(?:wide shot|wide|long shot|full shot|"
    r"full[- ]body|from behind|back view|rear view|back facing|whole body)\b"
)
_SIDE_SHOT = re.compile(
    r"侧面|侧脸|侧身|侧视|侧拍|侧向|侧后方|\b(?:profile|side view|side shot|"
    r"from the side|side[- ]on|side angle)\b"
)


def choose_reference(profile, scene):
    """Select one frozen image for a visible character and report the actual view."""
    images = profile.get("reference_images") or {}
    shot = " ".join(str(scene.get(field) or "") for field in (
        "shot_type", "visual_description", "camera_motion", "start_state",
    )).lower().replace("_", " ")
    if _WHOLE_SHOT.search(shot):
        if images.get("whole"):
            return {"view": "full", "path": images["whole"]}
    elif _SIDE_SHOT.search(shot) and images.get("profile"):
        return {"view": "side", "path": images["profile"]}
    front = images.get("front") or profile.get("reference_path")
    return {"view": "front", "path": front} if front else None


def reference_choices_for_scene(task, scene):
    choices = []
    for profile in characters_in_scene(task, scene):
        choice = choose_reference(profile, scene)
        if choice:
            choices.append({"character_id": profile["character_id"], **choice})
    return choices


def reference_paths_for_scene(task, scene):
    return [choice["path"] for choice in reference_choices_for_scene(task, scene)]


def validate_series_scene(task, scene):
    if not (task.get("metadata") or {}).get("series_context"):
        return
    profiles = frozen_characters(task)
    if not profiles:
        return
    characters_in_scene(task, scene)
    allowed = {profile[key] for profile in profiles for key in ("character_id", "name")}
    for line in scene.get("dialogue_lines") or []:
        if line["speaker"] not in allowed and line["speaker"] not in {"旁白", "解说", "narrator"}:
            raise ValueError(f"说话角色“{line['speaker']}”未绑定角色卡，请补充角色或调整大纲")


def scene_voice_options(scene, speaker):
    for profile in scene.get("voice_profiles") or []:
        if speaker in (profile["character_id"], profile["name"]):
            return {"voice": profile["tts_voice"], "rate": profile.get("rate", "+0%"),
                    "pitch": profile.get("pitch", "+0Hz")}
    return {"voice": resolve_voice_for_speaker(speaker)}


def faster_voice_options(options, percent):
    result = dict(options)
    base = int(str(result.get("rate", "+0%")).removesuffix("%"))
    result["rate"] = f"{min(100, base + percent):+d}%"
    return result
