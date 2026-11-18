"""
短剧导演创作引擎 (Drama Craft Service)
所有创意拆解、审稿诊断、剧本精修、场景与道具设计、动作提示词扩写均由用户配置的真实 AI 模型实时生成；
确定性规则层仅负责参考图三层剥离、站位轴线校验、敏感词安全转译、鞋履补全与物理约束护栏。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any
from uuid import uuid4


# ============================================================================
# 1. 角色定妆照：质感风格词块 + 鞋履防截断 + 职业服装约束 + 16:9 四格设定板
# ============================================================================

STYLE_QUALITY_BLOCKS: dict[str, str] = {
    "realistic": (
        "电影级写实人像摄影，ARRI Alexa 实拍质感，85mm 定焦镜头，"
        "真实亚洲人皮肤微肌理（可见细腻毛孔、面部极细微绒毛、T区自然哑光质感，严禁塑料磨皮假脸），"
        "发丝根根分明带自然碎发，真实织物经纬线肌理"
    ),
    "3d": (
        "高精度 3D CG 角色资产，Unreal Engine 5 渲染品质，"
        "皮肤具柔和次表面散射（SSS）通透感，XGen 级独立发丝束，"
        "PBR 物理服装材质（丝绸/皮革/金属高光与粗糙度对比鲜明），五官立体精致"
    ),
    "anime": (
        "高品质 2D 动画角色设定稿，精准粗细变化勾线，赛璐珞结合轻厚涂上色，"
        "明确二分受光与暗部冷暖交界线，高饱和通透瞳孔高光层次，标准番剧色指定卡质感"
    ),
}

OCCUPATION_ANTI_DRIFT_RULES: list[tuple[tuple[str, ...], str]] = [
    (("保安", "门卫", "物业"), "严禁画成警察制服、严禁警用肩章、严禁警徽与警用对讲机"),
    (("外卖", "骑手", "快递", "跑腿"), "严禁画成协警或交警制服、严禁肩章与警用反光背心"),
    (("医生", "外科", "大夫", "护士"), "严禁画成实验室防化服、严禁手持带血手术器械"),
    (("霸总", "总裁", "董事长", "高管"), "严禁画成酒店迎宾礼服或夸张暴发户大金链"),
    (("剑客", "侠客", "仙尊", "古风", "世子"), "严禁混入现代拉链、塑料纽扣、现代手表或运动鞋"),
    (("学生", "校花", "少年", "高中"), "严禁过度成熟浓妆、严禁夸张成人礼服"),
]

SHOE_KEYWORDS = ("鞋", "靴", "履", "高跟", "皮鞋", "运动鞋", "球鞋", "布鞋", "凉鞋", "拖鞋", "长靴", "短靴", "shoe", "boot", "heel")

ALLOWED_TTS_VOICES = {
    "zh-CN-YunjianNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YunyangNeural",
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-XiaoyiNeural",
    "en-US-AndrewNeural",
    "en-US-GuyNeural",
    "en-US-AriaNeural",
    "en-US-JennyNeural",
}


def normalize_voice_params(tts_voice: str = "", rate: str = "", pitch: str = "", market_mode: str = "cn") -> tuple[str, str, str]:
    voice = (tts_voice or "").strip()
    if voice not in ALLOWED_TTS_VOICES or not re.fullmatch(r"[A-Za-z]{2,3}-[A-Za-z]{2,4}-[A-Za-z0-9]+Neural", voice):
        voice = "en-US-AndrewNeural" if market_mode == "en" else "zh-CN-YunyangNeural"
    r = (rate or "+0%").strip()
    if not r.startswith(("+", "-")):
        r = "+" + r
    if not r.endswith("%"):
        r = r.rstrip("% ") + "%"
    if not re.fullmatch(r"^[+-](?:[0-9]|[1-9][0-9]|100)%$", r):
        r = "+0%"
    p = (pitch or "+0Hz").strip()
    if not p.startswith(("+", "-")):
        p = "+" + p
    if not p.endswith("Hz"):
        p = re.sub(r"[hzHZ]+$", "", p).strip() + "Hz"
    if not re.fullmatch(r"^[+-](?:[0-9]|[1-9][0-9]|100)Hz$", p):
        p = "+0Hz"
    return voice, r, p


def infer_anti_drift_negatives(face_dna: str = "", clothing: str = "", name: str = "") -> str:
    combined = f"{name} {face_dna} {clothing}"
    matched = [neg for keywords, neg in OCCUPATION_ANTI_DRIFT_RULES if any(k in combined for k in keywords)]
    base = "严禁夸张表情、严禁手部比划姿势、严禁手持任何独立道具（no props in hand, no pose）、严禁Q版大头比例、严禁背景杂物、严禁文字与UI水印"
    if matched:
        return base + "、" + "、".join(matched)
    return base


def ensure_shoes_in_clothing(clothing: str, face_dna: str = "") -> str:
    text = (clothing or "").strip()
    if any(k in text.lower() for k in SHOE_KEYWORDS):
        return text
    combined = f"{text} {face_dna}"
    if any(k in combined for k in ("古风", "锦袍", "汉服", "长剑", "玉冠", "道袍")):
        shoe = "脚穿同色系云纹皂靴（鞋底完整可见）"
    elif any(k in combined for k in ("西装", "风衣", "衬衫", "礼服", "总裁")):
        shoe = "脚穿哑光黑色修身皮鞋（鞋底完整可见）"
    elif any(k in combined for k in ("短裙", "针织", "丝质", "耳环", "女")):
        shoe = "脚穿简约低跟单鞋（鞋头与鞋跟完整可见）"
    else:
        shoe = "脚穿干净利落的深色休闲鞋（鞋底完整可见）"
    return f"{text}，{shoe}" if text else shoe


def build_character_sheet_4grid_prompt(profile: dict[str, Any], style: str = "realistic", custom_prompt: str = "") -> str:
    """生成单张 16:9 横图四格角色设定板提示词"""
    style_block = STYLE_QUALITY_BLOCKS.get(style, STYLE_QUALITY_BLOCKS["realistic"])
    name = (profile.get("name") or "角色").strip()
    face_dna = (profile.get("face_dna") or "").strip("。 ")
    clothing_with_shoes = ensure_shoes_in_clothing(profile.get("signature_clothing") or "", face_dna)
    voice_tag = profile.get("tts_voice") or "zh-CN-YunyangNeural"
    anti_drift = infer_anti_drift_negatives(face_dna, clothing_with_shoes, name)
    extra = f"补充要求：{custom_prompt.strip()}。" if custom_prompt and custom_prompt.strip() else ""
    return (
        f"角色设定图，整板 16:9 横版构图，纯白无接缝影棚背景（#FFFFFF）。"
        f"画面风格：{style_block}。"
        f"人物主体：{name}，真人正常 7 头身比例（严禁Q版或大头娃娃比例）。"
        f"全图共 4 格，从左到右横向并排，仅此 4 格，不设独立大主图："
        f"① 左一格：头部正面特写（占格高70%以上，全图唯一清晰完整展示五官区域，{face_dna or '五官立体清晰'}，平静中性表情 neutral expression，底部仅配简短英文音色标签 \"{voice_tag}\" 及极简黑白声纹波形线）；"
        f"② 左二格：正面全身站立图（头顶至鞋底完整入画，上下各留5%白边，双手自然垂放身体两侧，空手不持物，展示正面服装：{clothing_with_shoes or '统一常服与完整鞋履'}）；"
        f"③ 左三格：90度正侧面全身站立图（头顶至鞋底完整入画，展示鼻梁下颌侧面轮廓、后脑发型层次与服装侧面剪裁）；"
        f"④ 右一格：背面全身站立图（头顶至鞋底完整入画，展示后背发型收束与衣摆鞋跟背面结构）。"
        f"{extra}"
        f"强制禁止项：严禁出现第5个独立主图人物、严禁中文乱码标注、{anti_drift}。"
    )


# ============================================================================
# 2. 分镜首帧图：三层工艺分离与八段式结构
# ============================================================================

STUDIO_LIGHT_POLLUTION_RE = re.compile(
    r"干净纯色摄影棚背景|纯白无接缝影棚背景|纯白背景|摄影棚柔和恒定布光|flat studio lighting|pure white background",
    re.IGNORECASE,
)


def cap_scene_reference_choices(choices: list[dict[str, Any]], max_refs: int = 2) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """单镜具名角色参考图严格 ≤ 2 人；超出者自动剥离参考图并转为背景焦外虚化剪影。"""
    if not choices:
        return [], []
    return list(choices[:max_refs]), list(choices[max_refs:])


def strip_redundant_face_dna_when_referenced(prompt: str, bound_profiles: list[dict[str, Any]]) -> str:
    """三层分离规则：传入角色参考图时，剥离文字中重复的五官重描，保持参考图身份冻结。"""
    cleaned = STUDIO_LIGHT_POLLUTION_RE.sub("自然叙事环境光影", str(prompt or ""))
    for profile in bound_profiles:
        face_dna = (profile.get("face_dna") or "").strip("。 ")
        if face_dna and len(face_dna) >= 8 and face_dna in cleaned:
            cleaned = cleaned.replace(face_dna, "保持参考图冻结五官与发型（same identity as reference）")
    return cleaned


def build_eight_segment_image_prompt(
    scene: dict[str, Any],
    base_image_prompt: str,
    bound_profiles: list[dict[str, Any]] | None = None,
    overflow_profiles: list[dict[str, Any]] | None = None,
    style: str = "短剧",
    ratio: str = "9:16",
) -> str:
    """将分镜首帧提示词结构化为八段式：[CHARACTER]/[BACKGROUND]/[ACTION]/[SCENE]/[CAMERA]/[LIGHT]/[TEXT]/[STYLE]"""
    bound_profiles = bound_profiles or []
    overflow_profiles = overflow_profiles or []
    cleaned_base = strip_redundant_face_dna_when_referenced(base_image_prompt, bound_profiles)

    if "[CHARACTER]" in cleaned_base and "[CAMERA]" in cleaned_base:
        return cleaned_base

    blocking = str(scene.get("blocking") or "").strip()
    if bound_profiles:
        char_parts = []
        positions = ["位于画面左侧，身体朝向右侧", "位于画面右侧，身体朝向左侧"]
        for idx, prof in enumerate(bound_profiles[:2]):
            cname = prof.get("name") or f"角色{idx+1}"
            pos = positions[idx] if idx < len(positions) else "位于画面中景"
            char_parts.append(f"{cname}(@{cname}参考图) — same identity, {pos}")
        char_seg = "；".join(char_parts) + "（参考图身份冻结，严禁重画不同五官）。"
        if blocking:
            char_seg += f" 站位约束：{blocking}"
    else:
        char_seg = str(scene.get("character_state") or "画面核心主体姿态鲜明，位置关系明确").strip()
        if blocking:
            char_seg += f"（{blocking}）"

    if overflow_profiles:
        overflow_names = "、".join(p.get("name", "群演") for p in overflow_profiles)
        bg_seg = f"其余次要人物（{overflow_names}）仅以焦外虚化剪影（blurred silhouette in shallow DoF bokeh）呈现于远景，单镜具名清晰人脸严格 ≤ 2 人。"
    else:
        bg_seg = "背景干净无多余抢戏路人，远景保持适度浅景深虚化（shallow DoF bokeh）。"

    start_state = str(scene.get("start_state") or "").strip()
    vis_desc = str(scene.get("visual_description") or "").strip()
    if bound_profiles:
        vis_desc = strip_redundant_face_dna_when_referenced(vis_desc, bound_profiles)
    is_dynamic = bool(scene.get("need_dynamic_video")) and str(scene.get("transition_type") or "cut") != "continue_action"
    anticipation = "（主体处于动作刚启动0.1秒的势能起手态，眼神与重心已向目标蓄势，避免木桩站立）" if is_dynamic else ""
    action_seg = f"首帧定格状态：{start_state}{anticipation}。{vis_desc}".strip(" 。")

    continuity = str(scene.get("scene_continuity") or "").strip()
    scene_seg = f"前景适度构图遮挡 + 中景主体交互区 + 远景空间纵深锚点。{continuity}".strip(" 。")

    shot_type = str(scene.get("shot_type") or "中近景（MCU）").strip()
    camera_seg = f"{shot_type}，电影叙事机位，50mm 标准镜头透视，焦点锁死在主体眼部与关键接触道具上。"

    light_seg = "明确物理主光源方向与冷暖色温对比 + 侧逆轮廓边缘光（rim light）勾勒立体体积感，拒绝影棚纯白平光。"
    text_seg = "画面中无任何对白字幕、无气泡、无UI控件、无水印。"

    aspect = "9:16 竖屏构图" if ratio == "9:16" else "16:9 横屏构图"
    style_desc = (
        "电影级真人实拍短剧质感，真实皮肤纹理与实物服装材质"
        if style == "短剧"
        else "高品质精品漫剧/CG厚涂质感，线条与光影层次统一"
    )
    style_seg = f"{style_desc}，{aspect}。"

    return (
        f"[CHARACTER] {char_seg}\n"
        f"[BACKGROUND] {bg_seg}\n"
        f"[ACTION] {action_seg}\n"
        f"[SCENE] {scene_seg}\n"
        f"[CAMERA] {camera_seg}\n"
        f"[LIGHT] {light_seg}\n"
        f"[TEXT] {text_seg}\n"
        f"[STYLE] {style_seg}\n"
        f"补充画面细节：{cleaned_base}"
    )


# ============================================================================
# 3. 空间站位声明（Blocking）与段缝衔接模式（Transition Mode）
# ============================================================================

TRANSITION_MODES: dict[str, dict[str, str]] = {
    "freeze_handoff": {
        "code": "freeze_handoff",
        "label": "末帧定格承接",
        "description": "上一镜头末尾动作定格蓄势，本镜从上一镜实际尾帧或同构图紧接爆发（continue_action）。",
    },
    "angle_cut": {
        "code": "angle_cut",
        "label": "换机位硬切",
        "description": "保持 180° 轴线不变（左A右B严禁翻转），切换景别或正反打机位干脆硬切。",
    },
    "empty_shot": {
        "code": "empty_shot",
        "label": "空镜/道具过渡",
        "description": "以关键物证特写或环境空镜承上启下，完成时空或情绪换挡。",
    },
    "whip_morph": {
        "code": "whip_morph",
        "label": "动势甩镜/遮挡转场",
        "description": "借人物急速转身、前景黑影擦过或动作动势完成高能转场。",
    },
}


def infer_transition_mode(scene: dict[str, Any], index: int = 0) -> str:
    existing = str(scene.get("transition_mode") or "").strip()
    if existing in TRANSITION_MODES:
        return existing
    ttype = str(scene.get("transition_type") or "cut")
    note = str(scene.get("transition_note") or "") + " " + str(scene.get("visual_description") or "")
    if ttype == "continue_action" or (index > 0 and any(k in note for k in ("延续", "尾帧", "定格", "紧接"))):
        return "freeze_handoff"
    if any(k in note for k in ("甩镜", "遮挡转场")):
        return "whip_morph"
    if ttype == "scene_change" or any(k in note for k in ("空镜", "道具特写", "桌面特写")):
        return "empty_shot"
    return "angle_cut"


def ensure_scene_blocking(scene: dict[str, Any], task_characters: list[dict[str, Any]] | None = None) -> str:
    """为每个分镜规范化【空间站位声明（Blocking）】（含画左/画右、依托物、朝向、180°轴线锁）。"""
    existing = str(scene.get("blocking") or "").strip()
    if existing and ("轴线" in existing or "画左" in existing or "画右" in existing or "单人" in existing):
        return existing

    task_characters = task_characters or []
    by_id = {c.get("character_id"): c.get("name") for c in task_characters if c.get("character_id")}
    char_ids = scene.get("character_ids") or []
    names = [by_id.get(cid) or cid for cid in char_ids if cid]

    text_pool = " ".join(
        str(scene.get(k) or "") for k in ("visual_description", "start_state", "character_state", "speaker")
    )
    if not names and task_characters:
        names = [c.get("name") for c in task_characters if c.get("name") and c.get("name") in text_pool]

    start_state = str(scene.get("start_state") or "依托当前场景核心支点").strip("。 ")
    if len(names) >= 2:
        left_c, right_c = names[0], names[1]
        return (
            f"【双人轴线锁：左{left_c}·右{right_c}】"
            f"{left_c}位于画面左侧（面向画右），{right_c}位于画面右侧（面向画左），双方身后锁定固定建筑/家具参照物防漂移；"
            f"起始依托状态：{start_state}。严禁跨镜头翻转 180° 轴线。"
        )
    if len(names) == 1:
        only_c = names[0]
        return (
            f"【单人入画锁：{only_c}】"
            f"{only_c}位于画面视觉中心偏黄金分割点，身后锁定固定门窗/桌墙参照物，视线朝向画外对峙方向；"
            f"起始依托状态：{start_state}。严禁画外角色闯入单人镜。"
        )
    speaker = str(scene.get("speaker") or "").strip()
    if speaker and speaker not in {"旁白", "解说", "narrator"}:
        return f"【单人主视角：{speaker}】主体位于画面中景核心位并绑定背景物理参照物，{start_state}，保持拍摄轴线恒定。"
    return f"【环境/特写机位锁】主体位于画面中心焦点区并保持背景参照稳定，{start_state}，机位轴线恒定。"


# ============================================================================
# 4. 视频提示词安全清洗与 MCSLA 五层结构组装
# ============================================================================

LITERARY_SIMILE_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"端坐如钟|坐如洪钟"), "端坐原地，脊背挺直，双肩沉稳不动"),
    (re.compile(r"目光如炬|眼神如刀|目光似剑|眼神如冰"), "目光锐利冷冽，瞳孔微缩直视前方"),
    (re.compile(r"心如刀绞|肝肠寸断|痛彻心扉"), "右手用力按住胸口，指节泛白，眉头紧锁"),
    (re.compile(r"呆若木鸡|石化当场|如遭雷击|五雷轰顶"), "身体骤然僵直停顿，双目圆睁，呼吸屏住"),
    (re.compile(r"暴跳如雷|怒火中烧|火冒三丈"), "下颌线猛然绷紧，胸口剧烈起伏，猛地一拍桌面站起"),
    (re.compile(r"泪如雨下|嚎啕大哭"), "眼眶泛红，泪珠顺着脸颊滑落，双肩轻微抽动"),
]

ABSTRACT_EMOTION_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"非常震惊|极其震惊|一脸震惊|大吃一惊"), "瞳孔骤然微缩，双唇微张，身体轻微后仰"),
    (re.compile(r"非常愤怒|极其愤怒|十分生气|勃然大怒"), "眉心紧锁，下颌线绷紧，双拳在身侧攥紧"),
    (re.compile(r"心里一紧|心中五味杂陈|心头一震|暗自心惊"), "呼吸骤然停顿半秒，手指下意识收紧"),
    (re.compile(r"眼神微动|眸光微闪|微微一怔"), "目光停顿聚焦，眼睑轻微抬起"),
]

PROP_GLOW_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(玉佩|玉镯|令牌|戒指|匕首|长剑|刀刃)[^，。；\n]{0,6}(泛着幽光|闪着寒光|散发微光|映出火苗|光芒流转)"), r"\1呈现哑光实心物理材质原色与自然漫反射"),
]

SENSITIVE_VISUAL_TRANSLATIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"鲜血淋漓|血肉模糊|血流成河|喷血|吐血"), "深色衣襟染上暗红痕迹，嘴角残留一丝暗红"),
    (re.compile(r"割喉|刺穿胸膛|砍头|爆头|碎尸|虐杀|砍杀|刺杀"), "寒光一闪掠过，对方身体僵住缓缓后退倒地"),
    (re.compile(r"掐死|勒死|窒息而死"), "手掌逼近对方肩颈施压，对方呼吸急促后退"),
    (re.compile(r"自杀|跳楼|割腕|上吊"), "站在高处边缘身形摇晃，情绪濒临极限"),
    (re.compile(r"毒药|砒霜|迷药|毒品"), "不明深色药剂液体"),
]

EXTREME_REFRAME_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?:镜头|机位)(?:从[^，。；\n]{1,16})?(?:猛地|快速|迅速|缓缓)?(?:上摇|下摇|拉远至全身|拉到全身|切到人脸特写|推进到极近人脸)"),
        "保持当前统一景别与肩臂自然连接结构，平稳微推",
    ),
]

SCREEN_TEXT_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?:手机|屏幕)[^，。；\n]{0,12}(?:清晰显示|弹出|展示|特写)[^，。；\n]{0,20}(?:文字|消息|短信|内容|字样|回信)"),
        "手机屏幕亮起柔光映照指尖（画面不渲染可读文字）",
    ),
]

MAX_VIDEO_PROMPT_CHARS = 1400


def sanitize_video_prompt_mcsla(
    raw_prompt: str,
    scene: dict[str, Any] | None = None,
    all_character_names: list[str] | None = None,
    max_chars: int = MAX_VIDEO_PROMPT_CHARS,
) -> dict[str, Any]:
    """视频提示词动作物理化护栏：比喻替换、面部微动作拆解、反光去光效、单人镜去画外人名、敏感词转译、≤1400字。"""
    scene = scene or {}
    text = str(raw_prompt or "").strip()
    applied: list[str] = []

    for pattern, repl in LITERARY_SIMILE_REPLACEMENTS:
        if pattern.search(text):
            text = pattern.sub(repl, text)
            applied.append("文学比喻转物理动作")

    for pattern, repl in ABSTRACT_EMOTION_REPLACEMENTS:
        if pattern.search(text):
            text = pattern.sub(repl, text)
            applied.append("抽象情绪转面部微动作")

    for pattern, repl in PROP_GLOW_REPLACEMENTS:
        if pattern.search(text):
            text = pattern.sub(repl, text)
            applied.append("反光道具去光效描写")

    visible_ids = scene.get("character_ids") or []
    all_names = [n for n in (all_character_names or []) if n]
    if len(visible_ids) == 1 and len(all_names) >= 2:
        speaker = str(scene.get("speaker") or "")
        for name in all_names:
            if name and name != speaker and name in text:
                text = re.sub(
                    rf"(看向|望向|盯着|凝视|对着|朝){re.escape(name)}",
                    r"\1画外右前方方向",
                    text,
                )
                applied.append(f"单人镜剥离画外角色名「{name}」")

    for pattern, repl in SENSITIVE_VISUAL_TRANSLATIONS:
        if pattern.search(text):
            text = pattern.sub(repl, text)
            applied.append("敏感词转译为影视动作暗示")

    for pattern, repl in EXTREME_REFRAME_REPLACEMENTS:
        if pattern.search(text):
            text = pattern.sub(repl, text)
            applied.append("单镜禁跨档大变焦（防肢体嫁接畸形）")

    for pattern, repl in SCREEN_TEXT_REPLACEMENTS:
        if pattern.search(text):
            text = pattern.sub(repl, text)
            applied.append("屏蔽屏幕渲染可读文字（防英文乱码）")

    continuity_tail = "；身后固定背景结构与主光色温保持不变，严禁画面内自行生成任何字幕、气泡文字或水印。"
    if "主光色温保持不变" not in text and "背景与主光不变" not in text:
        text = text.rstrip("。 ") + continuity_tail
        applied.append("句尾追加环境、主光与无字锁定")

    if len(text) > max_chars:
        text = text[: max_chars - 12].rstrip("，、； ") + "。动作自然收止稳定。"
        applied.append(f"长度限制:自动精简至≤{max_chars}字")

    return {
        "prompt": text,
        "applied_rules": list(dict.fromkeys(applied)),
        "char_count": len(text),
    }


def build_mcsla_video_overview(scene: dict[str, Any], all_character_names: list[str] | None = None) -> str:
    """组装 MCSLA 五层结构视频提示词。"""
    blocking = ensure_scene_blocking(scene)
    tmode = TRANSITION_MODES.get(infer_transition_mode(scene), TRANSITION_MODES["angle_cut"])
    shot_type = str(scene.get("shot_type") or "中近景").strip()
    camera_motion = str(scene.get("camera_motion") or "平稳固定机位（或单向微推）").strip()
    start_state = str(scene.get("start_state") or "继承首帧起始姿态").strip()
    end_state = str(scene.get("end_state") or "定格在动作结果与情绪反应").strip()
    raw_video = str(scene.get("video_prompt") or scene.get("visual_description") or "").strip()

    mcsla_raw = (
        f"[Subject & Blocking 站位轴线] {blocking}｜段缝衔接：{tmode['label']}。\n"
        f"[Motion 物理动作因果链] 起手状态（{start_state}） -> 动作推进（{raw_video}） -> 秒末终态（{end_state}）。\n"
        f"[Camera 单一主运镜] 景别：{shot_type}，运镜：{camera_motion}（一镜一主运镜，一镜一视线落点）。\n"
        f"[Lighting & Lock 环境微锁] 保持首帧主光方向与人物五官服装恒定"
    )
    sanitized = sanitize_video_prompt_mcsla(mcsla_raw, scene=scene, all_character_names=all_character_names)
    return sanitized["prompt"]


# ============================================================================
# 5. 场景 3×3 九宫格 & 道具四视图结构封装
# ============================================================================

SCENE_GRID_ANGLES: dict[str, list[str]] = {
    "indoor": [
        "第1格（左上）：入口进门主视角全景，正对空间核心区，建立整体纵深",
        "第2格（中上）：主活动区正面中全景，展示核心家具组合与背景主墙关系",
        "第3格（右上）：窗侧/主光源侧逆光中景，展示体积光束与地面投影",
        "第4格（左中）：从空间内部向入口门框方向的反打全景（Reverse Angle）",
        "第5格（正中）：45度高机位俯视全景（High-angle overview），清晰交代全场动线与平面拓扑",
        "第6格（右中）：对角线纵深长焦压缩视角，展示前景遮挡、中景主体与远景层次",
        "第7格（左下）：核心叙事台面/书桌/茶几近景特写，展示台面陈设细节",
        "第8格（中下）：墙面装饰、门窗结构或建筑材质肌理近景",
        "第9格（右下）：仰视顶部灯具/横梁结构或窗外延伸环境空镜",
    ],
    "outdoor_building": [
        "第1格（左上）：建筑正立面大全景（Establishing Shot），交代建筑全貌与周边环境",
        "第2格（中上）：大门/入口台阶中景仰拍，凸显建筑压迫感与门头细节",
        "第3格（右上）：建筑45度斜侧全景，展示建筑体量纵深与侧翼结构",
        "第4格（左中）：从大门内向外反打街道/庭院全景，锁定进出动线",
        "第5格（正中）：高空鸟瞰俯视全景（Bird's-eye overview），交代建筑与道路方位",
        "第6格（右中）：建筑连廊/侧巷纵深透视中景，形成光影引导线",
        "第7格（左下）：门牌、石狮、门环或标志性建筑构件特写",
        "第8格（中下）：外墙砖石/玻璃幕墙材质与光影斑驳细节近景",
        "第9格（右下）：建筑屋檐/天际线与天空云层氛围空镜",
    ],
    "street_nature": [
        "第1格（左上）：街巷/场地主入口远景，建立环境尺度与延伸方向",
        "第2格（中上）：核心交锋场地正面中全景，交代地面材质与两侧参照物",
        "第3格（右上）：侧逆光环境氛围中景，强化空气颗粒感与明暗层次",
        "第4格（左中）：反打来路方向全景，锁死退路与空间轴线",
        "第5格（正中）：45度高处俯瞰全景，清晰呈现地形全貌与掩体分布",
        "第6格（右中）：利用前景树木/招牌遮挡的纵深窥视视角",
        "第7格（左下）：地面标志物（路标、石阶、积水倒影、火盆）近景特写",
        "第8格（中下）：两侧建筑边缘、围栏或植被肌理近景",
        "第9格（右下）：远方天际线、霓虹灯牌或山峦环境空镜",
    ],
}


def format_scene_grid_prompt(
    scene_name: str,
    space_type: str = "indoor",
    era_and_style: str = "",
    layout_and_materials: str = "",
    lighting_and_kelvin: str = "",
    custom_angles: list[str] | None = None,
) -> dict[str, Any]:
    """将 AI 设计或用户输入的场景空间参数封装为标准 16:9 场景 3×3 九宫格生图提示词。"""
    angles = custom_angles if custom_angles and len(custom_angles) == 9 else SCENE_GRID_ANGLES.get(space_type, SCENE_GRID_ANGLES["indoor"])
    angles_text = "；\n".join(f"- {a}" for a in angles)
    prompt = (
        f"场景概念设定图，整板 16:9 横版构图，内部为标准 3×3 九宫格分格布局（共 9 个独立机位格，格间细白线分隔）。\n"
        f"【场景母版总设定】\n"
        f"空间名称与美术风格：{scene_name}，{era_and_style or '电影级实景质感'}。\n"
        f"空间几何与陈设坐标：{layout_and_materials}。\n"
        f"统一光影与色温：{lighting_and_kelvin or '明确方向性物理主光与统一曝光'}，全图 9 格保持完全一致的曝光亮度、色温与材质陈旧度，统一使用 50mm 标准镜头透视，明亮通透，严禁死黑暗角。\n\n"
        f"【3×3 九宫格九机位明细（纯场景无人空镜）】\n"
        f"{angles_text}。\n\n"
        f"强制禁止项：纯场景无人空镜（严禁出现任何人物、人影、残影或动物）、严禁任何文字水印与UI标签、严禁鱼眼畸变。"
    )
    return {
        "scene_name": scene_name,
        "space_type": space_type,
        "era_and_style": era_and_style,
        "layout_and_materials": layout_and_materials,
        "lighting_and_kelvin": lighting_and_kelvin,
        "angles": angles,
        "prompt": prompt,
        "negative_prompt": "人物，行人，人影，手指，文字，水印，UI边框，死黑暗角，鱼眼镜头畸变，各格色调不一致",
    }


def format_prop_sheet_prompt(
    prop_name: str,
    material_and_color: str,
    size_vs_palm: str,
    key_detail: str,
    style: str = "写实影视道具设定风",
) -> dict[str, Any]:
    """将道具参数封装为 16:9 关键道具四格设定图提示词（零人手/零发光特效）。"""
    clean_material = PROP_GLOW_REPLACEMENTS[0][0].sub(r"\1呈现哑光实心物理材质原色", material_and_color)
    prompt = (
        f"道具设定图，整板 16:9 横版构图，纯白无接缝背景（#FFFFFF），全图共 4 格横向并排：\n"
        f"道具主体：{prop_name}，尺寸比例：{size_vs_palm}，材质与色彩：{clean_material}，细节特征：{key_detail}。画面风格：{style}。\n"
        f"- 第1格：正面标准平视图，完整展示道具正立面造型与轮廓比例；\n"
        f"- 第2格：45度斜侧立体视图，展示道具厚度、转折结构与立体体积感；\n"
        f"- 第3格：顶部俯视图（或背面结构图），展示顶面/背面接口与完整几何结构；\n"
        f"- 第4格：局部微距特写，放大展示最关键的叙事细节（{key_detail}）。\n"
        f"强制禁止项：严禁出现任何人物、人手、手指或手持展示画面（no hands, no human body）；"
        f"反光金属或玉石道具仅体现自然物理漫反射，严禁夸张发光特效；严禁任何文字说明与标注线。"
    )
    return {
        "prop_name": prop_name,
        "material_and_color": clean_material,
        "size_vs_palm": size_vs_palm,
        "key_detail": key_detail,
        "style": style,
        "prompt": prompt,
        "negative_prompt": "人手，手指，手持道具，人物，发光特效，光晕，霓虹灯感，文字标注，尺寸线，水印，杂乱背景",
    }


# ============================================================================
# 6. 剧本客观静态指标扫描（只做事实统计，绝不伪造固定打分或假建议）
# ============================================================================

INVALID_MICRO_ACTIONS = (
    "眼神微动", "眸光微闪", "微微一怔", "心里一紧", "心中五味杂陈",
    "嘴角勾起一抹", "三分薄凉", "四分漫不经心", "暗自心惊", "思绪万千",
    "脑海中浮现", "不由得想起", "百感交集", "心头一震",
)

AI_CLICHE_PHRASES = (
    "然而", "不仅如此", "显而易见", "毫无疑问", "从某种意义上",
    "命运的齿轮", "我深刻意识到", "众所周知", "总而言之", "换句话说",
    "不可否认", "值得一提的是", "与此同时", "在这场博弈中",
)


def scan_script_metrics(script_text: str, scenes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """对输入剧本执行纯粹客观的文本特征扫描，将实测事实提供给 AI 进行深度审稿与改写。"""
    text = str(script_text or "").strip()
    scenes = scenes or []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    consecutive_dialogue = 0
    max_dialogue_streak = 0
    action_line_count = 0
    dialogue_line_count = 0
    long_clauses: list[str] = []

    for ln in lines:
        is_action = ln.startswith("△") or ln.startswith("▲") or ln.startswith("【画面") or ln.startswith("[画面")
        if is_action:
            action_line_count += 1
            consecutive_dialogue = 0
        elif "：" in ln or ":" in ln or ln.startswith("“") or ln.startswith("\""):
            dialogue_line_count += 1
            consecutive_dialogue += 1
            max_dialogue_streak = max(max_dialogue_streak, consecutive_dialogue)
            spoken = re.split(r"[：:]", ln, maxsplit=1)[-1].strip("“”\"' ")
            for cl in re.split(r"[。！？!?；;]", spoken):
                if len(cl.strip()) > 22:
                    long_clauses.append(cl.strip())

    found_invalid_micro = [w for w in INVALID_MICRO_ACTIONS if w in text]
    for s in scenes:
        pool = str(s.get("visual_description") or "") + " " + str(s.get("character_state") or "")
        for w in INVALID_MICRO_ACTIONS:
            if w in pool and w not in found_invalid_micro:
                found_invalid_micro.append(w)

    found_ai_cliches = [w for w in AI_CLICHE_PHRASES if w in text]

    return {
        "total_chars": len(text),
        "total_lines": len(lines),
        "action_line_count": action_line_count,
        "dialogue_line_count": dialogue_line_count,
        "max_consecutive_dialogue": max_dialogue_streak,
        "long_clauses_over_22chars": long_clauses[:6],
        "found_invalid_micro_actions": found_invalid_micro,
        "found_ai_cliches": found_ai_cliches,
    }


# ============================================================================
# 7. 真实 AI 引擎调用层（全部通过 DeepSeekService / ImageService 实时生成）
# ============================================================================

def _get_real_llm():
    from services.provider_factory import get_llm_service
    llm = get_llm_service()
    if not hasattr(llm, "_call_api"):
        raise RuntimeError("当前语言模型服务不支持结构化导演接口")
    return llm


def ai_expand_character(
    name: str = "",
    brief: str = "",
    style: str = "短剧",
    market_mode: str = "cn",
) -> dict[str, Any]:
    """根据用户输入的简短角色想法，调用真实 AI 生成高精度 10 项外貌锚点、含鞋履服装与音色参数。"""
    if not (name.strip() or brief.strip()):
        raise ValueError("请至少填写角色名称或一句话角色设定")
    llm = _get_real_llm()
    system_prompt = (
        "你是顶级短剧造型与人物设定总监。请根据用户的角色灵感，生成可用于 AI 绘画与配音的完整角色卡。"
        "必须遵循：\n"
        "1) face_dna 必须具体写清：年龄段、骨相脸型、肤色肤质、眉形、眼型与瞳色、鼻唇特点、发型与发色、1处标志辨识点（泪痣/伤疤/眼镜等）、神情气质，严禁空泛形容词；\n"
        "2) signature_clothing 必须写清上装、下装、关键饰品，且必须明确写出具体鞋履款式与颜色（防全身图被截断成半身）；匹配年代与职业，严禁混入容易混淆职业的服饰；\n"
        "3) tts_voice 必须从以下合法 Edge-TTS 音色中选择最贴合角色声线的一个："
        + ", ".join(sorted(ALLOWED_TTS_VOICES))
        + "；rate 取值如 '+0%' 或 '-5%'，pitch 取值如 '+0Hz' 或 '-2Hz'。\n"
        "必须输出严格 JSON：{\"name\":..., \"face_dna\":..., \"signature_clothing\":..., \"tts_voice\":..., \"rate\":..., \"pitch\":...}"
    )
    user_prompt = (
        f"角色名参考：{name.strip() or '由你原创一个高度贴合人设的姓名'}\n"
        f"一句话设定与背景：{brief.strip() or name.strip()}\n"
        f"视觉风格：{style}\n"
        f"目标语言市场：{'出海英文短剧' if market_mode == 'en' else '国内中文短剧'}"
    )
    res = llm._call_api(system_prompt, user_prompt, max_tokens=1500)
    char_name = str(res.get("name") or name or "角色").strip()[:80]
    face_dna = str(res.get("face_dna") or "").strip()
    clothing = ensure_shoes_in_clothing(str(res.get("signature_clothing") or "").strip(), face_dna)
    voice, rate, pitch = normalize_voice_params(
        str(res.get("tts_voice") or ""),
        str(res.get("rate") or "+0%"),
        str(res.get("pitch") or "+0Hz"),
        market_mode=market_mode,
    )
    return {
        "name": char_name,
        "face_dna": face_dna,
        "signature_clothing": clothing,
        "tts_voice": voice,
        "rate": rate,
        "pitch": pitch,
    }


def ai_decompose_novel_to_drama(
    novel_text: str,
    title: str = "",
    total_episodes: int = 5,
    episode_duration: int = 60,
    style: str = "短剧",
    market_mode: str = "cn",
) -> dict[str, Any]:
    """
    调用真实 AI 将用户的小说/故事/长创意全量拆解为：
    可拍性五维真实评估、真实人物资产卡、真实场景九宫格设计、关键道具四视图设计、
    分集付费卡点大纲、第1集四挡商业剧本、跨集四本连续性初始台账。
    """
    clean_text = str(novel_text or "").strip()
    if len(clean_text) < 5:
        raise ValueError("请输入或粘贴需要拆解的小说/故事内容（至少 5 个字）")

    llm = _get_real_llm()
    total_episodes = max(1, min(10, int(total_episodes or 5)))
    lang_label = "出海英文短剧（使用地道美式短剧对白与英文编剧格式，但分析说明字段可用中文或双语）" if market_mode == "en" else "国内爆款中文短剧"

    system_prompt = (
        "你是拥有多部破亿爆款作品的短剧总编剧、总制片人与视听美术总监。你必须严格依据用户提供的【原著/创意原文】进行原创改编与拆解，"
        "严禁套用无关模板或照抄他人名字。\n"
        "请严格执行以下创作法则：\n"
        "1. 【单集 15 秒四挡推进】：0~15s 抓人立事（强视觉动作+具体数字/死线/压迫处境，严禁空镜寒暄）→ 15~45s 正面交锋（至少2轮筹码动作博弈）→ 45~85s 亮牌翻转（物证与大白话利害结论同步直给，当集反派必付代价）→ 85~110s 余波断章钩子（动作做到一半或新危机撞门而入瞬间定格）；\n"
        "2. 【可拍性动作锚点】：首集剧本 `first_episode_script` 中每 2~3 句对白必插一条可拍摄物理动作 `△` 行（写四肢发力、物证撞击或面部肌肉反应），彻底封杀“眼神微动、微微一怔、心里一紧”等心理废话与“然而、不仅如此、显而易见”等书面腔，对白使用 3~6 字短句与 8~16 字大白话算账句变速；\n"
        "3. 【角色资产可生成性】：每个角色 `face_dna` 含五官/发型/辨识特征；`signature_clothing` 必须写出具体鞋履款式与颜色；`tts_voice` 必须选自合法音色："
        + ", ".join(sorted(ALLOWED_TTS_VOICES))
        + "；\n"
        "4. 【场景与道具母版】：提取最核心高频场景（2~3个）设计布局方位（东西南北）、材质纹理与主光色温 Kelvin；提取推动反转的核心道具（1~2个），严禁给反光道具写“泛着幽光/发光特效”；\n"
        "5. 【跨集四本连续性台账】：结合原著人物与秘密差，生成 `physical_ledger`、`knowledge_ledger`、`prop_ledger`、`relationship_ledger`。\n\n"
        "必须输出严格 JSON 对象，结构如下：\n"
        "{\n"
        '  "title": "本剧剧名",\n'
        '  "shootability_report": {\n'
        '    "overall_feasibility": "S级/A级/B级及一句话真实评语",\n'
        '    "metrics": [{"name": "前3秒视觉抓力", "score": 0-100整数, "comment": "针对原文的具体分析"}, ... 共4-5项]\n'
        "  },\n"
        '  "characters": [{"name": "...", "role": "...", "face_dna": "...", "signature_clothing": "含具体鞋履", "tts_voice": "...", "rate": "+0%", "pitch": "+0Hz"}],\n'
        '  "scenes_spec": [{"scene_name": "...", "space_type": "indoor|outdoor_building|street_nature", "era_and_style": "...", "layout_and_materials": "...", "lighting_and_kelvin": "...", "angles": ["第1格...", "...共9格针对本场景的具体机位描述"]}],\n'
        '  "props_spec": [{"prop_name": "...", "material_and_color": "哑光实心材质描述", "size_vs_palm": "...", "key_detail": "...", "style": "..."}],\n'
        '  "episodes_outline": [{"episode_num": 1, "title": "...", "opening_hook": "...", "core_event": "...", "ending_hook": "...", "paywall_card": true/false, "paywall_reason": "..."}],\n'
        '  "first_episode_script": "完整第1集商业剧本正文（使用 \\n 换行，含 △ 动作行与角色对白）",\n'
        '  "continuity_ledgers": {\n'
        '    "physical_ledger": [{"character": "...", "location": "...", "costume_state": "...", "injury_or_mark": "...", "stamina_posture": "..."}],\n'
        '    "knowledge_ledger": [{"character": "...", "known_facts": "...", "hidden_blindspots": "..."}],\n'
        '    "prop_ledger": [{"prop_name": "...", "holder": "...", "location": "...", "condition": "...", "narrative_role": "..."}],\n'
        '    "relationship_ledger": [{"pair": "...", "tension_level": "...", "unsettled_debt": "..."}]\n'
        "  }\n"
        "}"
    )

    user_prompt = (
        f"剧集名称提示：{title.strip() or '根据故事自动提炼一个极具爆款吸引力的剧名'}\n"
        f"规划总集数：{total_episodes} 集\n"
        f"单集目标时长：{episode_duration} 秒\n"
        f"视觉风格：{style}\n"
        f"目标市场模式：{lang_label}\n\n"
        f"【待改编原著/故事正文】：\n{clean_text[:18000]}"
    )

    result = llm._call_api(system_prompt, user_prompt, max_tokens=6000)

    # 规范化角色资产与 TTS 参数
    normalized_chars = []
    for raw_c in result.get("characters") or []:
        if not isinstance(raw_c, dict) or not raw_c.get("name"):
            continue
        fdna = str(raw_c.get("face_dna") or "").strip()
        cloth = ensure_shoes_in_clothing(str(raw_c.get("signature_clothing") or "").strip(), fdna)
        v, r, p = normalize_voice_params(
            str(raw_c.get("tts_voice") or ""),
            str(raw_c.get("rate") or "+0%"),
            str(raw_c.get("pitch") or "+0Hz"),
            market_mode=market_mode,
        )
        normalized_chars.append({
            "name": str(raw_c["name"]).strip()[:80],
            "role": str(raw_c.get("role") or "主要角色").strip(),
            "face_dna": fdna,
            "signature_clothing": cloth,
            "tts_voice": v,
            "rate": r,
            "pitch": p,
        })

    # 将 AI 生成的场景 spec 与道具 spec 封装为完整的 3×3 / 4-grid 提示词
    scenes_sheet = []
    for sc in result.get("scenes_spec") or []:
        if not isinstance(sc, dict) or not sc.get("scene_name"):
            continue
        stype = sc.get("space_type") if sc.get("space_type") in SCENE_GRID_ANGLES else "indoor"
        custom_angles = sc.get("angles") if isinstance(sc.get("angles"), list) and len(sc["angles"]) == 9 else None
        scenes_sheet.append(
            format_scene_grid_prompt(
                scene_name=str(sc["scene_name"]).strip(),
                space_type=stype,
                era_and_style=str(sc.get("era_and_style") or style).strip(),
                layout_and_materials=str(sc.get("layout_and_materials") or "").strip(),
                lighting_and_kelvin=str(sc.get("lighting_and_kelvin") or "").strip(),
                custom_angles=custom_angles,
            )
        )

    props_sheet = []
    for pr in result.get("props_spec") or []:
        if not isinstance(pr, dict) or not pr.get("prop_name"):
            continue
        props_sheet.append(
            format_prop_sheet_prompt(
                prop_name=str(pr["prop_name"]).strip(),
                material_and_color=str(pr.get("material_and_color") or "哑光实心物理材质").strip(),
                size_vs_palm=str(pr.get("size_vs_palm") or "约成人手掌 1.0 倍大小").strip(),
                key_detail=str(pr.get("key_detail") or "关键叙事细节清晰可见").strip(),
                style=str(pr.get("style") or ("精品漫剧道具设定风" if style == "漫剧" else "写实影视道具设定风")).strip(),
            )
        )

    # 规范化分集大纲（确保集号连续 1..N）
    raw_eps = result.get("episodes_outline") or []
    episodes_outline = []
    for idx in range(1, total_episodes + 1):
        src = raw_eps[idx - 1] if idx - 1 < len(raw_eps) and isinstance(raw_eps[idx - 1], dict) else {}
        episodes_outline.append({
            "episode_num": idx,
            "title": str(src.get("title") or f"第{idx}集").strip(),
            "opening_hook": str(src.get("opening_hook") or "").strip(),
            "core_event": str(src.get("core_event") or "").strip(),
            "ending_hook": str(src.get("ending_hook") or "").strip(),
            "paywall_card": bool(src.get("paywall_card", False)),
            "paywall_reason": str(src.get("paywall_reason") or "").strip(),
        })

    first_script = str(result.get("first_episode_script") or "").strip()
    report = dict(result.get("shootability_report") or {})
    report["word_count"] = len(clean_text)
    report["market_mode"] = "出海英文短剧" if market_mode == "en" else "国内中文短剧"
    if first_script:
        report["script_metrics"] = scan_script_metrics(first_script)

    return {
        "title": str(result.get("title") or title or "未命名短剧").strip(),
        "market_mode": market_mode,
        "style": style,
        "total_episodes": total_episodes,
        "episode_duration": episode_duration,
        "shootability_report": report,
        "characters": normalized_chars,
        "scenes_sheet": scenes_sheet,
        "props_sheet": props_sheet,
        "episodes_outline": episodes_outline,
        "first_episode_script": first_script,
        "continuity_ledgers": result.get("continuity_ledgers") or {},
        "source": "deepseek",
    }


def ai_review_script(
    script_text: str,
    scenes: list[dict[str, Any]] | None = None,
    duration: int = 60,
) -> dict[str, Any]:
    """
    结合客观静态扫描 + 真实 AI 导演深度审稿，返回基于用户输入原文的五维打分、四挡检验与精准改写建议。
    """
    text = str(script_text or "").strip()
    if len(text) < 5:
        raise ValueError("请提供需要审查的剧本正文")

    metrics = scan_script_metrics(text, scenes)
    llm = _get_real_llm()

    system_prompt = (
        "你是顶级爆款短剧总审稿导演。请结合客观扫描统计数据和你对剧本原文的深度阅读，对用户提交的剧本进行严苛、精准、可落地的专业审稿。\n"
        "审查标准（严禁敷衍客套，必须言之有物并原句引用 evidence）：\n"
        "1. `pacing`（15秒换挡节奏）：前3~15秒是否有强视觉动作/数字死线立事？15~45s是否有两轮以上筹码对抗？结尾85~110s是否在最高悬念处断章而非平淡大结局？\n"
        "2. `payoff`（爽点与当集结算）：是否做到物证与大白话利害结论同轮直给？反派是否当集付出可见代价？\n"
        "3. `dialogue`（台词去AI味辨识度）：是否有“然而/不仅如此/显而易见/命运的齿轮”等书面套话或超过20字的憋气长句？是否缺少3~6字短句与8~16字算账句变速？\n"
        "4. `shootability`（空间可拍性与△密度）：是否出现连续3~4句纯对白站桩？是否出现“眼神微动/微微一怔/心里一紧/思绪万千”等镜头拍不出的抽象心理词？\n"
        "5. `continuity`（人物动机与连续性）：人物行为是否符合利益动机、空间与道具前后逻辑是否自洽？\n\n"
        "必须输出严格 JSON：\n"
        "{\n"
        '  "overall_score": 0-100整数,\n'
        '  "grade": "S"|"A"|"B"|"C",\n'
        '  "verdict": "pass"|"needs_polish",\n'
        '  "dimensions": {\n'
        '    "pacing": {"label": "15秒换挡节奏", "score": 0-100},\n'
        '    "payoff": {"label": "爽点与当集结算", "score": 0-100},\n'
        '    "dialogue": {"label": "台词去AI味辨识度", "score": 0-100},\n'
        '    "shootability": {"label": "空间可拍性与△密度", "score": 0-100},\n'
        '    "continuity": {"label": "人物动机与连续性", "score": 0-100}\n'
        "  },\n"
        '  "four_gear_timeline": [\n'
        '    {"gear": "第一挡 · 0~15s 抓人立事", "target": "结合本剧开场评价", "status": "pass"|"warn"},\n'
        '    {"gear": "第二挡 · 15~45s 正面交锋", "target": "结合本剧中段评价", "status": "pass"|"warn"},\n'
        '    {"gear": "第三挡 · 45~85s 亮牌翻转", "target": "结合本集结算评价", "status": "pass"|"warn"},\n'
        '    {"gear": "第四挡 · 85~110s 断章钩子", "target": "结合本集结尾评价", "status": "pass"|"warn"}\n'
        "  ],\n"
        '  "issues": [{"rule_id": "问题规则名称", "category": "所属维度", "severity": "high"|"medium", "evidence": "精确摘录剧本原句", "diagnosis": "具体问题原因", "prescription": "直接可用的改写建议或示例"}]\n'
        "}"
    )

    user_prompt = (
        f"目标时长：{duration} 秒\n"
        f"客观静态扫描事实：{metrics}\n\n"
        f"【待审剧本正文】：\n{text[:15000]}"
    )
    if scenes:
        user_prompt += f"\n\n【关联分镜概要】：\n{scenes[:12]}"

    result = llm._call_api(system_prompt, user_prompt, max_tokens=3200)
    issues = result.get("issues") or []
    result["issue_count"] = len(issues)
    result["metrics"] = metrics
    result["source"] = "deepseek"
    return result


def ai_doctor_script(
    script_text: str,
    title: str = "",
    duration: int = 60,
) -> dict[str, Any]:
    """
    调用真实 AI 根据审稿标准对用户剧本进行深度重写与去 AI 味精修，并自动复审返回优化前后对比。
    """
    raw = str(script_text or "").strip()
    if len(raw) < 5:
        raise ValueError("请提供需要精修的剧本内容")

    metrics = scan_script_metrics(raw)
    llm = _get_real_llm()

    system_prompt = (
        "你是金牌短剧改稿总编剧。你的任务是在【完整保留原剧本核心人物、核心事件与关键反转事实】的前提下，"
        "将用户提供的初稿真正重写润色为 S 级可拍摄爆款短剧剧本。\n"
        "你必须亲自动手完成以下 5 项实质性改写（严禁只做微调应付）：\n"
        "1. 【前3秒重锤立事】：删掉慢热铺垫和寒暄，第一镜直接用『△』切入强冲突物理动作与具体数字/时间死线；\n"
        "2. 【每2~3句必插 △ 强动作】：彻底消除站桩对白，为人物设计符合情境的动作、表情肌肉变化与物证交互（以 `△` 开头成行）；\n"
        "3. 【清除全部心理废话】：严禁“眼神微动/微微一怔/心里一紧/五味杂陈”，全部改写为镜头能拍出的动作或道具变化；\n"
        "4. 【台词七维去 AI 味】：剔除“然而/不仅如此/显而易见/命运的齿轮”等书面腔，把长句拆成 3~6 字短句 + 8~16 字大白话利害算账句，物证与结论当场直给；\n"
        "5. 【结尾强断章钩子】：把平淡大结局改写成停在动作半途或突发新危机瞬间的追更悬念。\n\n"
        "必须输出严格 JSON：\n"
        "{\n"
        '  "polished_script": "完整精修后的剧本正文（使用 \\n 换行）",\n'
        '  "changes_made": ["具体修改说明1（写清改了哪场戏/哪句台词）", "..."],\n'
        '  "after_review": {\n'
        '    "overall_score": 0-100整数,\n'
        '    "grade": "S"|"A"|"B"|"C",\n'
        '    "verdict": "pass"|"needs_polish",\n'
        '    "dimensions": {\n'
        '      "pacing": {"label": "15秒换挡节奏", "score": 0-100},\n'
        '      "payoff": {"label": "爽点与当集结算", "score": 0-100},\n'
        '      "dialogue": {"label": "台词去AI味辨识度", "score": 0-100},\n'
        '      "shootability": {"label": "空间可拍性与△密度", "score": 0-100},\n'
        '      "continuity": {"label": "人物动机与连续性", "score": 0-100}\n'
        "    },\n"
        '    "four_gear_timeline": [\n'
        '      {"gear": "第一挡 · 0~15s 抓人立事", "target": "精修后效果", "status": "pass"|"warn"},\n'
        '      {"gear": "第二挡 · 15~45s 正面交锋", "target": "精修后效果", "status": "pass"|"warn"},\n'
        '      {"gear": "第三挡 · 45~85s 亮牌翻转", "target": "精修后效果", "status": "pass"|"warn"},\n'
        '      {"gear": "第四挡 · 85~110s 断章钩子", "target": "精修后效果", "status": "pass"|"warn"}\n'
        "    ],\n"
        '    "issues": []\n'
        "  }\n"
        "}"
    )

    user_prompt = (
        f"剧本标题：{title.strip() or '未命名剧本'}\n"
        f"目标时长：{duration} 秒\n"
        f"初稿客观扫描发现的问题：{metrics}\n\n"
        f"【待精修剧本初稿】：\n{raw[:15000]}"
    )

    res = llm._call_api(system_prompt, user_prompt, max_tokens=5000)
    polished = str(res.get("polished_script") or "").strip()
    if not polished:
        raise RuntimeError("AI 未能返回有效的精修剧本内容")

    after_review = res.get("after_review") or {}
    after_review["metrics"] = scan_script_metrics(polished)
    after_review["issue_count"] = len(after_review.get("issues") or [])
    after_review["source"] = "deepseek"

    return {
        "title": title,
        "polished_script": polished,
        "changes_made": res.get("changes_made") or [],
        "after_review": after_review,
        "source": "deepseek",
    }


def ai_generate_scene_grid_prompt(
    scene_name: str,
    space_type: str = "indoor",
    era_and_style: str = "",
    layout_and_materials: str = "",
    lighting_and_kelvin: str = "",
) -> dict[str, Any]:
    """调用真实 AI 根据场景名称与简述，原创设计完整的空间拓扑、材质陈设、主光色温与 9 机位纯空镜提示词。"""
    if not scene_name.strip():
        raise ValueError("请输入场景名称")

    llm = _get_real_llm()
    system_prompt = (
        "你是顶级影视美术指导与概念场景设计师。请根据用户给出的场景名称与简要想法，为 AI 生图模型设计一套完整、立体、可跨镜头锁死空间拓扑的【16:9 3×3 九宫格设定图】方案。\n"
        "要求：\n"
        "1) 必须详细写出东西南北具体结构、核心家具与道具坐标位置、真实物理材质纹理；\n"
        "2) 必须明确物理主光源方向与精确 Kelvin 色温；\n"
        "3) `angles` 必须严格输出 9 条数组，分别对应九宫格第1~9格（涵盖入口全景、主活动区、窗侧逆光、门框反打、45°高机位俯视平面、对角长焦、台面特写、墙体肌理、顶部/窗外空镜），每一条都必须融入【本场景】真实物体而非空话，且保持纯场景无人空镜！\n"
        "必须输出严格 JSON：{\"era_and_style\": \"...\", \"layout_and_materials\": \"...\", \"lighting_and_kelvin\": \"...\", \"angles\": [9条字符串]}"
    )
    user_prompt = (
        f"场景名称：{scene_name.strip()}\n"
        f"空间类型：{space_type}\n"
        f"年代与风格参考：{era_and_style.strip() or '由你根据场景气质自由定调'}\n"
        f"空间与陈设补充：{layout_and_materials.strip() or '由你根据该场所功能真实推演补全家具、建筑构件与材质磨损细节'}\n"
        f"光影色温参考：{lighting_and_kelvin.strip() or '由你根据戏剧氛围设计具体主光方向与Kelvin色温'}"
    )
    res = llm._call_api(system_prompt, user_prompt, max_tokens=2200)
    custom_angles = res.get("angles") if isinstance(res.get("angles"), list) and len(res["angles"]) == 9 else None
    data = format_scene_grid_prompt(
        scene_name=scene_name.strip(),
        space_type=space_type,
        era_and_style=str(res.get("era_and_style") or era_and_style or "电影级实景质感").strip(),
        layout_and_materials=str(res.get("layout_and_materials") or layout_and_materials).strip(),
        lighting_and_kelvin=str(res.get("lighting_and_kelvin") or lighting_and_kelvin).strip(),
        custom_angles=custom_angles,
    )
    data["source"] = "deepseek"
    return data


def ai_generate_prop_sheet_prompt(
    prop_name: str,
    material_and_color: str = "",
    size_vs_palm: str = "",
    key_detail: str = "",
    style: str = "",
) -> dict[str, Any]:
    """调用真实 AI 根据道具名称与简述，设计高精度物理材质、手掌比例与微距叙事细节，并组装为 16:9 四格提示词。"""
    if not prop_name.strip():
        raise ValueError("请输入道具名称")

    llm = _get_real_llm()
    system_prompt = (
        "你是顶级影视道具美术总监。请根据用户提供的道具名称，补全极具真实感与戏剧叙事张力的【16:9 道具四格设定图】物理设计规范。\n"
        "铁则：\n"
        "1) 严禁描写“发光、泛着幽光、散发灵光、光芒流转”等虚幻光效词（否则生图会变成灯泡），金属/玉石/玻璃只写哑光/半哑光实心物理材质本色与自然漫反射；\n"
        "2) 必须明确相对于成人手掌的真实物理尺寸比例；\n"
        "3) 必须设计出可在第4格微距特写中呈现的独家破案/叙事细节（如铭文、磕痕、防伪暗纹、磨损氧化痕迹）；\n"
        "4) 画面中绝不能出现人手或人体肢体。\n"
        "必须输出严格 JSON：{\"material_and_color\": \"...\", \"size_vs_palm\": \"...\", \"key_detail\": \"...\", \"style\": \"...\"}"
    )
    user_prompt = (
        f"道具名称：{prop_name.strip()}\n"
        f"材质补充：{material_and_color.strip() or '由你设计最真实契合的物理材质与配色'}\n"
        f"尺寸参考：{size_vs_palm.strip() or '由你给出相对成人手掌的精准比例'}\n"
        f"视觉风格：{style.strip() or '写实影视道具设定风'}"
    )
    res = llm._call_api(system_prompt, user_prompt, max_tokens=1200)
    data = format_prop_sheet_prompt(
        prop_name=prop_name.strip(),
        material_and_color=str(res.get("material_and_color") or material_and_color or "哑光实心物理材质原色").strip(),
        size_vs_palm=str(res.get("size_vs_palm") or size_vs_palm or "约成人手掌 1.0 倍大小").strip(),
        key_detail=str(res.get("key_detail") or key_detail or "清晰可辨的表面材质与独特叙事纹理").strip(),
        style=str(res.get("style") or style or "写实影视道具设定风").strip(),
    )
    data["source"] = "deepseek"
    return data


def ai_upgrade_video_prompt(
    raw_prompt: str,
    speaker: str = "",
    character_ids: list[str] | None = None,
    all_character_names: list[str] | None = None,
    shot_type: str = "",
    camera_motion: str = "",
) -> dict[str, Any]:
    """
    先调用真实 AI 动作导演将用户粗糙画面想法扩写升级为 MCSLA 完整物理因果链提示词，
    再通过确定性清洗护栏确保绝无文学比喻、无画外人名、无反光灯泡、无高危敏感词、≤1400字。
    """
    if not str(raw_prompt or "").strip():
        raise ValueError("请输入需要优化或生成的视频镜头描述")

    llm = _get_real_llm()
    char_ids = character_ids or []
    all_names = [n for n in (all_character_names or []) if n]
    single_shot_hint = (
        f"本镜头为单人镜头（仅 {speaker or '主角'} 在场），严禁在文字中出现其他画外角色名（{all_names}），"
        "视线只能写『望向画右前方画外』等方向词！"
        if len(char_ids) == 1
        else f"本镜头角色上下文：{all_names or '按输入画面主角设定'}"
    )

    system_prompt = (
        "你是 AI 图生视频动作导演。请把用户的简短镜头想法或草稿，扩写升级为高稳定度的【MCSLA 物理动作因果链视频提示词】。\n"
        "必须严格遵守：\n"
        "1. 只描述运动增量与物理因果链（起手动作起因 → 受力接触点与运动幅度 → 身体与衣料物理反应 → 动作终态），不重复啰嗦静态五官；\n"
        "2. 严禁任何文学比喻（如“端坐如钟/目光如炬/心如刀绞”），全部写成纯物理动作；\n"
        "3. 情绪必须拆解为面部肌肉与呼吸动作（如瞳孔骤缩、下颌绷紧、胸口急促起伏）；\n"
        "4. 反光金属或玉石禁止写“泛着幽光/闪烁光芒”，只写实心哑光材质物理反馈；\n"
        "5. 高危暴力/血腥动作转译为克制的影视冲击动作；\n"
        "6. 单镜头只指定唯一主运镜与唯一视线落点，并在结尾附上身后固定背景物与主光色温不变的微锁；\n"
        f"7. {single_shot_hint}\n"
        "8. 总体长度控制在 120~500 字之间，精炼精准。\n"
        "必须输出严格 JSON：{\"upgraded_prompt\": \"...\"}"
    )
    user_prompt = (
        f"用户原始镜头描述：{raw_prompt.strip()}\n"
        f"景别偏好：{shot_type or '中近景（MCU）'}\n"
        f"运镜偏好：{camera_motion or '缓慢单向推镜或稳定固定机位'}\n"
        f"当前主体：{speaker or '主角'}"
    )
    res = llm._call_api(system_prompt, user_prompt, max_tokens=1500)
    ai_text = str(res.get("upgraded_prompt") or raw_prompt).strip()

    sanitized = sanitize_video_prompt_mcsla(
        raw_prompt=ai_text,
        scene={"speaker": speaker, "character_ids": char_ids},
        all_character_names=all_names,
    )
    sanitized["source"] = "deepseek"
    return sanitized


async def render_craft_asset_image(
    prompt: str,
    negative_prompt: str = "",
    category: str = "craft_assets",
    ratio: str = "16:9",
) -> dict[str, Any]:
    """调用系统已配置的真实生图模型绘制 16:9 场景九宫格或道具四视图。"""
    from services.provider_factory import get_image_service

    if not str(prompt or "").strip():
        raise ValueError("提示词不能为空")

    image_service = get_image_service()
    scene_tag = uuid4().hex[:12]

    def _run():
        try:
            return image_service.generate_image(
                task_id=category,
                scene_id=scene_tag,
                image_prompt=prompt,
                negative_prompt=negative_prompt or None,
                ratio=ratio,
            )
        except TypeError:
            return image_service.generate_image(
                category,
                scene_tag,
                prompt,
            )

    res = await asyncio.to_thread(_run)
    if not isinstance(res, dict) or res.get("status") != "success" or not res.get("asset_path"):
        raise RuntimeError(str((res or {}).get("message") or "图片生成服务未返回有效图片"))
    return {
        "asset_path": res["asset_path"],
        "source": res.get("source", image_service.__class__.__name__),
    }
