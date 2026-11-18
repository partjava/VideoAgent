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


def reference_choices_for_scene(task, scene, max_refs: int = 2):
    """
    单镜具名角色参考图严格 ≤ 2 人（超出者自动剥离参考图并转为背景焦外虚化剪影）。
    """
    choices = []
    for profile in characters_in_scene(task, scene):
        choice = choose_reference(profile, scene)
        if choice:
            choices.append({"character_id": profile["character_id"], **choice})
    return choices[:max_refs] if max_refs and max_refs > 0 else choices


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
    clean_speaker = str(speaker or "").strip()
    for profile in scene.get("voice_profiles") or []:
        if clean_speaker in (
            str(profile.get("character_id") or "").strip(),
            str(profile.get("name") or "").strip(),
        ):
            return {
                "voice": profile["tts_voice"],
                "rate": profile.get("rate", "+0%"),
                "pitch": profile.get("pitch", "+0Hz"),
            }
    return {"voice": resolve_voice_for_speaker(clean_speaker)}


_FEMALE_VOICE_POOL = ("zh-CN-XiaoyiNeural", "zh-CN-XiaoxiaoNeural")
_MALE_VOICE_POOL = ("zh-CN-YunxiNeural", "zh-CN-YunjianNeural", "zh-CN-YunyangNeural")
_FEMALE_HINT = re.compile(r"female|女|少女|女生|女孩|女子|小姐|姑娘|旗袍|长发|闺秀|女士|太太|妻子|她|姐姐|妹妹|母亲|阿姨|queen|woman|girl|lady", re.I)
_MALE_HINT = re.compile(r"\bmale\b|男|少帅|少年|青年|男士|男人|男子|军装|寸头|胡渣|西装|副总|总裁|董事长|将军|父亲|大叔|先生|他|man|boy|boss", re.I)


def build_effective_voice_profiles(task: dict, scenes: list[dict] | None = None) -> list[dict]:
    """合并已绑定角色音色，并为未入库的角色按人物性别/外貌自动分配差异化男女声线。"""
    profiles = [dict(p) for p in frozen_characters(task) if isinstance(p, dict) and p.get("name") and p.get("tts_voice")]
    seen_names = {str(p["name"]).strip() for p in profiles}
    used_voices = {str(p.get("tts_voice") or "") for p in profiles}

    def _pick_gender(text_pool: str, explicit_gender: str = "") -> str | None:
        g = str(explicit_gender or "").strip().lower()
        if g in {"female", "女", "f"}:
            return "female"
        if g in {"male", "男", "m"}:
            return "male"
        f_hit = bool(_FEMALE_HINT.search(text_pool))
        m_hit = bool(_MALE_HINT.search(text_pool))
        if f_hit and not m_hit:
            return "female"
        if m_hit and not f_hit:
            return "male"
        if f_hit and m_hit:
            return "female" if len(_FEMALE_HINT.findall(text_pool)) >= len(_MALE_HINT.findall(text_pool)) else "male"
        return None

    def _safe_resolve_voice(speaker_name: str, ctx_hint: str) -> str:
        try:
            return resolve_voice_for_speaker(speaker_name, ctx_hint)
        except TypeError:
            return resolve_voice_for_speaker(speaker_name)

    def _assign_voice(name: str, context_text: str, explicit_gender: str = "", explicit_voice: str = "") -> str:
        ev = str(explicit_voice or "").strip()
        if ev.startswith("zh-CN-") and ev.endswith("Neural") and ev not in used_voices:
            used_voices.add(ev)
            return ev
        gender = _pick_gender(f"{name} {context_text}", explicit_gender)
        if gender == "female":
            pool = _FEMALE_VOICE_POOL
            choice = next((v for v in pool if v not in used_voices), pool[len(used_voices) % len(pool)])
            used_voices.add(choice)
            return choice
        if gender == "male":
            pool = _MALE_VOICE_POOL
            inferred = _safe_resolve_voice(name, context_text)
            if inferred in pool and inferred not in used_voices:
                used_voices.add(inferred)
                return inferred
            choice = next((v for v in pool if v not in used_voices), pool[len(used_voices) % len(pool)])
            used_voices.add(choice)
            return choice
        inferred = _safe_resolve_voice(name, context_text)
        used_voices.add(inferred)
        return inferred

    plan_chars = ((task.get("metadata") or {}).get("task_plan") or {}).get("characters") or []
    for idx, ch in enumerate(plan_chars):
        if not isinstance(ch, dict):
            continue
        name = str(ch.get("name") or "").strip()
        if not name or name in seen_names or name in {"旁白", "解说", "narrator"}:
            continue
        ctx = " ".join(str(ch.get(k) or "") for k in ("appearance", "personality", "role_in_story", "gender"))
        voice = _assign_voice(name, ctx, str(ch.get("gender") or ""), str(ch.get("tts_voice") or ""))
        seen_names.add(name)
        profiles.append({
            "character_id": str(ch.get("character_id") or f"auto_char_{idx + 1}"),
            "name": name,
            "tts_voice": voice,
            "rate": str(ch.get("rate") or "+4%"),
            "pitch": str(ch.get("pitch") or "+0Hz"),
        })

    # 对分镜对白中临时出现的新说话人（如“监察员”“董事长助理”）同样确保差异化配音
    scene_texts = " ".join(
        f"{s.get('visual_description') or ''} {s.get('blocking') or ''}" for s in (scenes or [])
    )
    for idx, sc in enumerate(scenes or []):
        for line in sc.get("dialogue_lines") or []:
            sp = str(line.get("speaker") or "").strip()
            if not sp or sp in seen_names or sp in {"旁白", "解说", "narrator"}:
                continue
            voice = _assign_voice(sp, scene_texts)
            seen_names.add(sp)
            profiles.append({
                "character_id": f"auto_speaker_{len(profiles) + 1}",
                "name": sp,
                "tts_voice": voice,
                "rate": "+4%",
                "pitch": "+0Hz",
            })

    return profiles


def faster_voice_options(options, percent):
    result = dict(options)
    base = int(str(result.get("rate", "+0%")).removesuffix("%"))
    result["rate"] = f"{min(100, base + percent):+d}%"
    return result


_EMOTION_PROSODY_MAP: dict[str, tuple[int, int]] = {
    "angry": (12, 5),
    "furious": (14, 6),
    "暴怒": (14, 6),
    "怒斥": (12, 5),
    "mocking": (-4, -3),
    "arrogant": (-4, -3),
    "冷嘲": (-4, -3),
    "傲慢": (-4, -3),
    "cold_threat": (-8, -5),
    "阴狠": (-8, -5),
    "威胁": (-7, -4),
    "压低": (-8, -6),
    "shocked": (-10, 4),
    "trembling": (-10, 5),
    "震惊": (-10, 4),
    "破防": (-8, 5),
    "urgent": (14, 3),
    "急切": (14, 3),
}


def infer_dialogue_emotion(text: str, explicit_emotion: str | None = None) -> str:
    """仅当剧本未提供 emotion 时，依据句意和标点做轻量化兜底情绪判断。"""
    clean_explicit = str(explicit_emotion or "").strip()
    if clean_explicit:
        return clean_explicit
    t = str(text or "").strip()
    if not t:
        return ""
    if any(k in t for k in ("不可能", "怎么可能", "难道", "！？", "?!")):
        return "shocked"
    if any(k in t for k in ("滚", "休想", "闭嘴", "废话", "敢", "不签")) or "！" in t:
        return "angry"
    if any(k in t for k in ("笑话", "配", "可笑", "掂量")):
        return "mocking"
    if any(k in t for k in ("三日", "废纸", "作废", "第二遍", "小心", "代价", "看清楚")):
        return "cold_threat"
    if any(k in t for k in ("快", "立刻", "马上")):
        return "urgent"
    return ""


def apply_dialogue_emotion_prosody(options: dict, emotion: str | None = None) -> dict:
    """按单句情绪标签对基准 rate / pitch 施加可听见的情绪起伏偏移。"""
    result = dict(options)
    raw_emotion = str(emotion or "").strip().lower()
    if not raw_emotion:
        return result
    delta = _EMOTION_PROSODY_MAP.get(raw_emotion)
    if delta is None:
        for key, pair in _EMOTION_PROSODY_MAP.items():
            if key in raw_emotion:
                delta = pair
                break
    if delta is None:
        return result
    rate_delta, pitch_delta = delta
    base_rate = int(str(result.get("rate") or "+0%").rstrip("%") or 0)
    base_pitch = int(str(result.get("pitch") or "+0Hz").rstrip("Hzhz") or 0)
    result["rate"] = f"{max(-35, min(60, base_rate + rate_delta)):+d}%"
    result["pitch"] = f"{max(-20, min(20, base_pitch + pitch_delta)):+d}Hz"
    return result
