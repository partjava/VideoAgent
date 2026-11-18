"""
全链路视听衔接与模型适配引擎（Shot Continuity & Provider Dialect Engine）

职责：
1. 视频模型方言自适应编译与动作幅度推荐（Wan / Seedance / Vidu / ComfyUI）
2. 跨镜头上下文状态链校验与进出场/景别连续性约束生成
3. 同场相邻镜头自动接镜调色（RGB 通道均值与对比离散度向组内中位数收敛）
4. 动态避让 BGM 音量包络与 EBU R128 两遍响度标准化辅助
5. 导出成片全自动切点连续性审计（亮度跳变、单帧闪烁坏帧、音量阶跃检测）
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
from PIL import Image, ImageOps

from services.media_quality import resolve_media


# ============================================================================
# 1. 模型方言识别与视频提示词自适应编译
# ============================================================================

_BRACKET_SECTION_RE = re.compile(
    r"\[(?:站位与轴线|站位轴线|衔接模式[:：]?[^\]]*|主运镜与焦段|统一运镜|运镜指令|起止|锚定与微锁|"
    r"尾帧续接|动作交接|节奏动作链|实测配音时间优先|实测发声窗口|人物与光影一致锁|交付)\]\s*"
)
_AXIS_LOCK_PREFIX_RE = re.compile(r"【(?:双人轴线锁|单人入画锁|单人主视角|环境/特写机位锁)[:：]?[^】]*】")
_BEAT_PREFIX_RE = re.compile(r"^\[(\d+(?:\.\d+)?-\d+(?:\.\d+)?s)\](.*)$", re.MULTILINE)


def resolve_provider_dialect(service: Any) -> str:
    """根据当前视频服务实例及型号识别目标模型方言。"""
    cls_name = type(service).__name__.lower()
    model_name = str(getattr(service, "model", "") or "").lower()
    if "wan" in cls_name or "wan" in model_name:
        return "wan"
    if "doubao" in cls_name or "seedance" in model_name:
        return "doubao"
    if "vidu" in cls_name or "vidu" in model_name:
        return "vidu"
    if "comfyui" in cls_name:
        return "comfyui"
    return "generic"


def resolve_vidu_movement_amplitude(scene: dict[str, Any]) -> str:
    """按分镜景别、对白密度与转场模式自动匹配 Vidu 动作幅度。"""
    transition_mode = str(scene.get("transition_mode") or "").strip()
    shot_type = str(scene.get("shot_type") or "")
    text_pool = " ".join(
        str(scene.get(k) or "")
        for k in ("video_prompt", "visual_description", "camera_motion", "transition_note")
    )
    if transition_mode == "whip_morph" or any(
        k in text_pool for k in ("甩镜", "急转", "奔跑", "追逐", "砸", "摔", "冲刺", "大幅")
    ):
        return "large"
    has_dialogue = bool(scene.get("dialogue_audio") or scene.get("dialogue_lines"))
    is_tight = any(k in shot_type for k in ("特写", "大特写", "近景"))
    if is_tight and (has_dialogue or any(k in text_pool for k in ("凝视", "微动", "屏息", "闭口", "低语"))):
        return "small"
    return "medium"


def compile_prompt_for_dialect(prompt: str, dialect: str) -> str:
    """将统一结构化运动提示词按目标模型习惯编译为干净、自然的执行文本。"""
    text = str(prompt or "").strip()
    if not text or dialect == "generic":
        return text

    # 剥离内部流程方括号与工程锁前缀，避免模型把工程标签误读为画内字幕或分屏
    cleaned = _BRACKET_SECTION_RE.sub("", text)
    cleaned = _AXIS_LOCK_PREFIX_RE.sub("", cleaned)

    if dialect == "wan":
        # Wan3 将多时段 + 机位切换视为段内硬切脚本；锁定单一连续长镜头并用自然连续语句表达
        cleaned = cleaned.replace("@", "")
        cleaned = _BEAT_PREFIX_RE.sub(lambda m: f"({m.group(1)}) {m.group(2).strip()}", cleaned)
        cleaned = cleaned.replace("｜", "，")
        # 保留首个时段前的景别/机位定调，剥离后续时段中途跳变机位的词（防段内鬼影硬切与叠化）
        lines = []
        seen_cam = False
        for raw_line in cleaned.splitlines():
            s_line = raw_line.strip()
            if s_line.startswith("(") and "机位:" in s_line:
                if seen_cam:
                    s_line = re.sub(r"机位:[^，,\n]+[，,]?", "", s_line).strip()
                seen_cam = True
            if s_line:
                lines.append(s_line)
        cleaned = "\n".join(lines)
        single_shot_lock = "单一连续一镜到底实拍，全程保持连续机位运动，严禁镜内硬切、禁止切换第二个景别或画面叠化。"
        if "单一连续" not in cleaned and "单连续镜头" not in cleaned:
            cleaned = f"{single_shot_lock}\n{cleaned}"
    elif dialect == "doubao":
        # Seedance 系列适合保留清晰时间段标与因果推进句
        cleaned = _BEAT_PREFIX_RE.sub(lambda m: f"{m.group(1)}：{m.group(2).strip()}", cleaned)
        cleaned = cleaned.replace("｜", "，")
    elif dialect in {"vidu", "comfyui"}:
        cleaned = cleaned.replace("@", "")
        cleaned = _BEAT_PREFIX_RE.sub(lambda m: f"{m.group(1)} {m.group(2).strip()}", cleaned)
        cleaned = cleaned.replace("｜", "，")

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return "\n".join(line.strip() for line in cleaned.splitlines() if line.strip())


def adapt_video_submission(service: Any, scene: dict[str, Any], prompt: str) -> tuple[str, dict[str, Any]]:
    """
    为具体视频模型编译最终提交提示词与扩展参数，
    并严格兼容无特定扩展字段的普通实现或离线测试替身。
    """
    dialect = resolve_provider_dialect(service)
    compiled_prompt = compile_prompt_for_dialect(prompt, dialect)
    extra_options: dict[str, Any] = {}

    if dialect == "vidu" and getattr(service, "supports_movement_amplitude", False) is True:
        extra_options["movement_amplitude"] = resolve_vidu_movement_amplitude(scene)

    if dialect == "vidu" and getattr(service, "supports_resolution", False) is True:
        from config import settings as _cfg
        if scene.get("vidu_resolution"):
            extra_options["resolution"] = str(scene["vidu_resolution"])
        elif scene.get("_is_trial"):
            extra_options["resolution"] = str(getattr(_cfg, "vidu_trial_resolution", "720p") or "720p")
        else:
            extra_options["resolution"] = str(getattr(_cfg, "vidu_resolution", "1080p") or "1080p")

    last_frame = scene.get("last_frame_target_path")
    if (
        isinstance(last_frame, str)
        and last_frame.strip()
        and getattr(service, "supports_last_frame", False) is True
    ):
        resolved_last = resolve_media(last_frame)
        if resolved_last.is_file():
            extra_options["last_frame_path"] = last_frame

    return compiled_prompt, extra_options


# ============================================================================
# 2. 景别阶梯与跨镜连续性交接状态构建
# ============================================================================

SHOT_SCALE_ORDER: tuple[tuple[str, int], ...] = (
    ("大远景", 0),
    ("远景", 1),
    ("全景", 2),
    ("中全景", 3),
    ("中景", 4),
    ("中近景", 5),
    ("近景", 6),
    ("特写", 7),
    ("大特写", 8),
)


def classify_shot_scale(shot_type: str | None) -> int | None:
    text = str(shot_type or "").strip()
    if not text:
        return None
    for label, rank in SHOT_SCALE_ORDER:
        if label in text:
            return rank
    lower = text.lower()
    if "extreme wide" in lower:
        return 0
    if "wide" in lower or "long shot" in lower:
        return 2
    if "medium close" in lower or "mcu" in lower:
        return 5
    if "medium" in lower or "ms" in lower:
        return 4
    if "extreme close" in lower or "ecu" in lower:
        return 8
    if "close" in lower or "cu" in lower:
        return 7
    return None


def _cast_signature(scene: dict[str, Any], bound_cast: Sequence[dict[str, Any]] | None = None) -> list[str]:
    by_id = {
        str(c.get("character_id")): str(c.get("name") or c.get("character_id"))
        for c in (bound_cast or [])
        if isinstance(c, dict) and c.get("character_id")
    }
    raw_ids = [str(cid) for cid in (scene.get("character_ids") or []) if cid]
    if raw_ids:
        return [by_id.get(cid, cid) for cid in dict.fromkeys(raw_ids)]
    speaker = str(scene.get("speaker") or "").strip()
    if speaker and speaker not in {"旁白", "解说", "narrator"}:
        return [speaker]
    return []


def build_scene_continuity_handoff(
    scenes: Sequence[dict[str, Any]],
    index: int,
    *,
    ratio: str = "9:16",
    bound_cast: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """为分镜生成跨镜交接契约（人数增减交代、景别阶梯防跳、画幅节奏与同场光影锁）。"""
    current = scenes[index]
    is_vertical = ratio == "9:16"
    ratio_pacing = (
        "竖屏9:16快节奏：禁用无信息长空镜，跨镜主要靠台词抢白、动作打断或视线对撞咬合，每3-5秒保持强推进。"
        if is_vertical
        else "横屏16:9精品节奏：保留空间纵深留白与情绪落地反应拍，长推拉优先沿斜轴线推进。"
    )

    handoff: dict[str, Any] = {
        "ratio": ratio,
        "ratio_pacing": ratio_pacing,
        "is_first_shot": index == 0,
        "next_is_continue": (
            index + 1 < len(scenes)
            and str(scenes[index + 1].get("transition_type") or "") == "continue_action"
        ),
    }

    if index == 0:
        handoff["cast_delta"] = {"entering": _cast_signature(current, bound_cast), "exiting": []}
        handoff["scale_advice"] = "首镜确立核心关系与主光方位，第0.1秒处于动作刚启动的势能起手态，忌僵硬站桩。"
        return handoff

    prev = scenes[index - 1]
    prev_cast = _cast_signature(prev, bound_cast)
    curr_cast = _cast_signature(current, bound_cast)
    entering = [name for name in curr_cast if name not in prev_cast]
    exiting = [name for name in prev_cast if name not in curr_cast]

    prev_scale = classify_shot_scale(prev.get("shot_type"))
    curr_scale = classify_shot_scale(current.get("shot_type"))
    ttype = str(current.get("transition_type") or "cut")

    if ttype == "continue_action":
        scale_advice = "连续动作接缝：本镜起点必须与上镜终点同档景别（0级落差）、同拍摄轴线与同光影色温，禁止复位重起。"
    elif ttype == "scene_change":
        scale_advice = "换场衔接：用新空间环境锚点或先声后人过渡，明确主光来源与时空对比。"
    elif prev_scale is not None and curr_scale is not None:
        diff = abs(curr_scale - prev_scale)
        if diff == 0 and index >= 2 and classify_shot_scale(scenes[index - 2].get("shot_type")) == curr_scale:
            scale_advice = "景别防呆提醒：已连续两镜相同景别，本镜在不翻转180°轴线前提下微调机位角度或景别1级。"
        elif diff >= 2:
            scale_advice = "大跨度景别切换：通过明确视线落点、动作动势或前景遮挡引导观众视线无缝着陆。"
        else:
            scale_advice = "常规顺畅切换：保持180°轴线与视线左右方向对应（A看画右则B看画左）。"
    else:
        scale_advice = "常规顺畅切换：保持180°轴线与同场光照一致。"

    entry_instruction = ""
    if entering and ttype != "scene_change":
        entry_instruction = (
            f"跨镜新增角色（{'、'.join(entering)}）：须在前1.5秒内交代进画来源"
            "（画内自某侧推门走入 / 画外先声再转镜），禁止无过渡凭空闪现同框。"
        )
    if handoff["next_is_continue"]:
        scale_advice += "｜下一镜为连续动作接力：本镜最后0.6秒须完成动作收束至清晰可截稳态（禁极大远景或急甩离场）。"

    handoff.update(
        {
            "prev_scene_id": prev.get("scene_id"),
            "prev_shot_type": prev.get("shot_type"),
            "prev_end_state": prev.get("end_state"),
            "prev_blocking": prev.get("blocking"),
            "prev_continuity_note": prev.get("continuity_note"),
            "cast_delta": {"entering": entering, "exiting": exiting},
            "entry_instruction": entry_instruction,
            "scale_advice": scale_advice,
        }
    )
    return handoff


# ============================================================================
# 3. 同场自动接镜调色（RGB 通道均值与离散度向组内中位数收敛）
# ============================================================================

SHOT_MATCH_STRENGTH = 0.68
SHOT_MATCH_GAIN_LIMITS = (0.78, 1.28)


@dataclass(frozen=True)
class ChannelStats:
    mean: tuple[float, float, float]
    spread: tuple[float, float, float]


@dataclass(frozen=True)
class ShotColorMatch:
    gains: tuple[float, float, float]
    offsets: tuple[float, float, float]

    def build_uint8_lut(self) -> np.ndarray:
        """预计算 [256, 3] 的 uint8 查找表，逐帧查表零浮点乘法开销。"""
        lut = np.empty((256, 3), dtype=np.uint8)
        base = np.arange(256, dtype=np.float32)
        for ch in range(3):
            mapped = np.clip(base * self.gains[ch] + self.offsets[ch], 0.0, 255.0)
            lut[:, ch] = np.rint(mapped).astype(np.uint8)
        return lut


def build_scene_runs(scenes: Sequence[dict[str, Any]]) -> list[list[int]]:
    """按连续同场切分分镜索引组；遇到明确换场或纯空镜过渡时断开。"""
    runs: list[list[int]] = []
    for idx, scene in enumerate(scenes):
        ttype = str(scene.get("transition_type") or "cut")
        tmode = str(scene.get("transition_mode") or "")
        is_break = idx == 0 or ttype == "scene_change" or tmode == "empty_shot"
        if is_break or not runs:
            runs.append([idx])
        else:
            runs[-1].append(idx)
    return [run for run in runs if len(run) >= 2]


def compute_rgb_stats_from_frames(frames: Sequence[np.ndarray]) -> ChannelStats | None:
    """对一组采样帧计算 R/G/B 三通道均值与标准差。"""
    if not frames:
        return None
    stacked = np.concatenate(
        [np.asarray(f, dtype=np.float32).reshape(-1, 3) for f in frames],
        axis=0,
    )
    if stacked.size == 0:
        return None
    means = tuple(float(x) for x in stacked.mean(axis=0))
    stds = tuple(float(max(1.0, s)) for s in stacked.std(axis=0))
    return ChannelStats(mean=means, spread=stds)  # type: ignore[arg-type]


def measure_clip_rgb_stats(clip: Any) -> ChannelStats | None:
    """从 MoviePy VideoClip 或 ImageClip 中均匀抽样 3 帧进行轻量统计（缩至 64px 宽）。"""
    try:
        dur = float(getattr(clip, "duration", 0.0) or 0.0)
        if dur <= 0.0:
            return None
        sample_times = [min(dur * ratio, max(0.0, dur - 0.04)) for ratio in (0.12, 0.50, 0.88)]
        frames = []
        for t in sample_times:
            arr = clip.get_frame(t)
            img = Image.fromarray(np.asarray(arr, dtype=np.uint8)).convert("RGB")
            img.thumbnail((64, 64))
            frames.append(np.asarray(img, dtype=np.uint8))
        return compute_rgb_stats_from_frames(frames)
    except Exception:
        return None


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else 0.5 * (ordered[mid - 1] + ordered[mid])


def match_toward_reference(
    stats: ChannelStats,
    reference: ChannelStats,
    strength: float = SHOT_MATCH_STRENGTH,
) -> ShotColorMatch:
    low, high = SHOT_MATCH_GAIN_LIMITS
    gains: list[float] = []
    offsets: list[float] = []
    for mean, spread, ref_mean, ref_spread in zip(
        stats.mean, stats.spread, reference.mean, reference.spread
    ):
        target_mean = mean + strength * (ref_mean - mean)
        target_spread = spread + strength * (ref_spread - spread)
        gain = min(high, max(low, target_spread / spread)) if spread > 1e-6 else 1.0
        # 限制单通道全局偏移在合理范围，防止局部极端画面整体发灰
        offset = max(-38.0, min(38.0, target_mean - gain * mean))
        gains.append(round(gain, 4))
        offsets.append(round(offset, 2))
    return ShotColorMatch(tuple(gains), tuple(offsets))  # type: ignore[arg-type]


def plan_shot_color_match(
    scenes: Sequence[dict[str, Any]],
    measure: Callable[[int], ChannelStats | None],
    *,
    strength: float = SHOT_MATCH_STRENGTH,
) -> dict[str, ShotColorMatch]:
    """为同一连续场景组内的相邻分镜计算 68% 中位数收敛色彩/曝光匹配方案。"""
    plan: dict[str, ShotColorMatch] = {}
    for run in build_scene_runs(scenes):
        known: list[tuple[str, ChannelStats]] = []
        for idx in run:
            sid = str(scenes[idx].get("scene_id") or f"scene_{idx + 1}")
            st = measure(idx)
            if st is not None:
                known.append((sid, st))
        if len(known) < 2:
            continue
        reference = ChannelStats(
            tuple(_median([st.mean[c] for _, st in known]) for c in range(3)),  # type: ignore[arg-type]
            tuple(_median([st.spread[c] for _, st in known]) for c in range(3)),  # type: ignore[arg-type]
        )
        for sid, st in known:
            match = match_toward_reference(st, reference, strength=strength)
            # 若基本与参考一致则跳过无谓运算
            if any(abs(g - 1.0) > 0.015 for g in match.gains) or any(abs(o) > 1.5 for o in match.offsets):
                plan[sid] = match
    return plan


def select_bgm_file(
    task: dict[str, Any] | None,
    scenes: Sequence[dict[str, Any]],
    bgm_dir: Path,
) -> str | None:
    """按任务设定或剧情关键词挑选最贴合情绪基调的 BGM 文件，支持 none 静音与显式 bgm_path。"""
    explicit = str((task or {}).get("bgm_path") or "").strip()
    if explicit and Path(explicit).is_file():
        return explicit

    preset = str((task or {}).get("bgm_preset") or "auto").strip().lower()
    if preset == "none":
        return None
    if not bgm_dir.exists():
        return None
    candidates = sorted(list(bgm_dir.glob("*.mp3")) + list(bgm_dir.glob("*.wav")))
    if not candidates:
        return None

    def _find(substr: str) -> str | None:
        for path in candidates:
            if substr in path.name.lower():
                return str(path)
        return None

    if preset == "warm_campus":
        return _find("warm") or str(candidates[0])
    if preset == "heroic":
        return _find("heroic") or str(candidates[0])
    if preset == "suspense":
        return _find("suspense") or str(candidates[0])

    # auto：结合用户输入、主题与前几镜剧情判定情绪底色
    text_pool = " ".join([
        str((task or {}).get("user_input") or ""),
        str((task or {}).get("topic") or ""),
        str((task or {}).get("style") or ""),
        " ".join(str(s.get("visual_description") or "") for s in list(scenes)[:6]),
        " ".join(str(s.get("voiceover") or "") for s in list(scenes)[:6]),
    ])
    heroic_kw = ("革命", "暴动", "英雄", "烈士", "方志敏", "军", "起义", "热血", "史诗", "抗争", "红色")
    warm_kw = ("校园", "早恋", "青春", "暗恋", "初恋", "教室", "晚自习", "虐恋", "地铁", "温暖", "成长", "告别")
    if any(k in text_pool for k in heroic_kw):
        return _find("heroic") or str(candidates[0])
    if any(k in text_pool for k in warm_kw):
        return _find("warm") or str(candidates[0])
    return _find("suspense") or str(candidates[0])


def apply_color_match_to_clip(clip: Any, match: ShotColorMatch) -> Any:
    """用预计算 uint8 LUT 快速统一帧色调与亮度。"""
    lut = match.build_uint8_lut()
    r_lut, g_lut, b_lut = lut[:, 0], lut[:, 1], lut[:, 2]

    def _transform(frame: np.ndarray) -> np.ndarray:
        out = np.empty_like(frame)
        out[..., 0] = r_lut[frame[..., 0]]
        out[..., 1] = g_lut[frame[..., 1]]
        out[..., 2] = b_lut[frame[..., 2]]
        return out

    return clip.fl_image(_transform)


def _apply_whip_blur_frame(frame: np.ndarray, ratio: float) -> np.ndarray:
    arr = frame.astype(np.float32)
    shift = max(2, int(round(ratio * 10)))
    padded = np.pad(arr, ((0, 0), (shift, shift), (0, 0)), mode="edge")
    blended = (
        0.4 * arr
        + 0.3 * padded[:, : arr.shape[1], :]
        + 0.3 * padded[:, 2 * shift :, :]
    )
    return np.clip(blended, 0, 255).astype(np.uint8)


def _scale_luma_frame(frame: np.ndarray, ratio: float, min_factor: float) -> np.ndarray:
    factor = max(min_factor, 1.0 - (1.0 - min_factor) * ratio)
    return np.clip(frame.astype(np.float32) * factor, 0, 255).astype(np.uint8)


def _flash_white_frame(frame: np.ndarray, ratio: float) -> np.ndarray:
    arr = frame.astype(np.float32)
    alpha = min(0.9, max(0.0, ratio * 0.9))
    return np.clip(arr + (255.0 - arr) * alpha, 0, 255).astype(np.uint8)


def _apply_edge_transition(clip: Any, *, edge: str, width: float, fn: Callable[[np.ndarray, float], np.ndarray]) -> Any:
    dur = float(getattr(clip, "duration", 0.0) or 0.0)
    if edge == "tail":
        return clip.fl(
            lambda gf, t, d=dur, w=width, f=fn: gf(t) if t < d - w else f(gf(t), (t - (d - w)) / w)
        )
    return clip.fl(
        lambda gf, t, w=width, f=fn: gf(t) if t > w else f(gf(t), 1.0 - (t / w))
    )


def apply_cut_transitions(clips: list[Any], scenes: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """按相邻分镜的转场意图施加严格等长边界微过渡，绝不改变任何片段起止时长。"""
    applied: list[dict[str, Any]] = []
    for idx in range(1, len(clips)):
        next_scene = scenes[idx]
        ttype = str(next_scene.get("transition_type") or "cut")
        tmode = str(next_scene.get("transition_mode") or "")
        out_dur = float(getattr(clips[idx - 1], "duration", 0.0) or 0.0)
        in_dur = float(getattr(clips[idx], "duration", 0.0) or 0.0)
        if out_dur < 0.8 or in_dur < 0.8:
            continue

        transition_hint = " ".join(
            str(x or "")
            for x in (
                scenes[idx - 1].get("transition_note"),
                scenes[idx - 1].get("visual_description"),
                next_scene.get("transition_note"),
                next_scene.get("visual_description"),
                tmode,
            )
        )
        if tmode == "flash_white" or (ttype == "scene_change" and any(k in transition_hint for k in ("闪白", "入梦", "梦醒"))):
            mode, width, fn = "flash_white", 0.24, _flash_white_frame
        elif ttype == "scene_change":
            mode, width, fn = "scene_fade", 0.22, lambda frame, r: _scale_luma_frame(frame, r, 0.18)
        elif tmode == "whip_morph":
            mode, width, fn = "whip_morph", 0.16, _apply_whip_blur_frame
        elif tmode == "empty_shot":
            mode, width, fn = "empty_ease", 0.16, lambda frame, r: _scale_luma_frame(frame, r, 0.55)
        else:
            continue

        clips[idx - 1] = _apply_edge_transition(clips[idx - 1], edge="tail", width=width, fn=fn)
        clips[idx] = _apply_edge_transition(clips[idx], edge="head", width=width, fn=fn)
        applied.append({
            "from": scenes[idx - 1].get("scene_id"),
            "to": next_scene.get("scene_id"),
            "mode": mode,
        })
    return applied


# ============================================================================
# 4. 音频微包络、动态闪避（Ducking）与 FFmpeg 两遍响度统一
# ============================================================================


def speech_ducking_gain(
    times: np.ndarray,
    speech_windows: Sequence[tuple[float, float]],
    *,
    base_gain: float = 1.0,
    duck_gain: float = 0.42,
    ramp: float = 0.22,
) -> np.ndarray:
    """生成平滑对白避让曲线：说话窗前后 `ramp` 秒渐变降至 `duck_gain`，空白间隙恢复至 `base_gain`。"""
    t = np.asarray(times, dtype=np.float32)
    gains = np.full(t.shape, float(base_gain), dtype=np.float32)
    if not speech_windows:
        return gains
    for start, end in speech_windows:
        if end <= start:
            continue
        pre = start - ramp
        post = end + ramp
        # 说话中保持压低
        inside = (t >= start) & (t <= end)
        gains = np.minimum(gains, np.where(inside, duck_gain, base_gain))
        # 进入前平滑下降
        fade_in = (t >= pre) & (t < start)
        if np.any(fade_in):
            progress = np.clip((t - pre) / max(1e-4, ramp), 0.0, 1.0)
            curve = base_gain - (base_gain - duck_gain) * progress
            gains = np.minimum(gains, np.where(fade_in, curve, base_gain))
        # 结束后平滑回升
        fade_out = (t > end) & (t <= post)
        if np.any(fade_out):
            progress = np.clip((t - end) / max(1e-4, ramp), 0.0, 1.0)
            curve = duck_gain + (base_gain - duck_gain) * progress
            gains = np.minimum(gains, np.where(fade_out, curve, base_gain))
    return gains


def wrap_ducked_bgm(bgm_clip: Any, speech_windows: Sequence[tuple[float, float]], *, base_volume: float = 0.17, duck_volume: float = 0.075) -> Any:
    """对已设定好基准音量的 BGM 按人声时间窗自动执行平滑闪避。"""
    if not speech_windows:
        return bgm_clip.volumex(base_volume)
    ratio = duck_volume / max(1e-4, base_volume)
    scaled = bgm_clip.volumex(base_volume)

    def _duck_filter(get_frame, t):
        frame = get_frame(t)
        times = np.asarray(t, dtype=np.float32)
        gains = speech_ducking_gain(times, speech_windows, base_gain=1.0, duck_gain=ratio)
        arr = np.asarray(frame, dtype=np.float32)
        if times.ndim == 0:
            return arr * float(gains)
        if arr.ndim == 2:
            return arr * gains[:, None]
        return arr * gains

    return scaled.fl(_duck_filter)


def apply_micro_audio_fade(audio_clip: Any, fade_in: float = 0.02, fade_out: float = 0.03) -> Any:
    """为人声片段添加 20-30ms 极短微包络，消除切点爆音而不改动主体音量。"""
    dur = float(getattr(audio_clip, "duration", 0.0) or 0.0)
    if dur < 0.14:
        return audio_clip
    fi = min(fade_in, dur * 0.18)
    fo = min(fade_out, dur * 0.22)
    clip = audio_clip
    if fi > 0.004:
        clip = clip.audio_fadein(fi)
    if fo > 0.004:
        clip = clip.audio_fadeout(fo)
    return clip


def _extract_last_json(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    last: dict[str, Any] | None = None
    idx = 0
    while True:
        pos = text.find("{", idx)
        if pos < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[pos:])
            if isinstance(obj, dict):
                last = obj
            idx = pos + max(1, end)
        except json.JSONDecodeError:
            idx = pos + 1
    return last


def measure_loudnorm_filter(
    ffmpeg_exe: str,
    media_path: Path,
    *,
    target_i: float = -16.0,
    true_peak: float = -1.5,
) -> str | None:
    """运行 FFmpeg EBU R128 测量；遇静音、无音轨或极短稀疏测试音频返回 None 安全跳过。"""
    try:
        common = f"I={target_i:g}:TP={true_peak:g}:LRA=11"
        res = subprocess.run(
            [
                ffmpeg_exe, "-hide_banner", "-nostats", "-i", str(media_path),
                "-af", f"loudnorm={common}:print_format=json", "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        measured = _extract_last_json(res.stderr or "")
        if not measured:
            return None
        required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
        if not all(k in measured for k in required):
            return None
        parsed = {k: float(measured[k]) for k in required}
        if not all(math.isfinite(v) for v in parsed.values()):
            return None
        # 若输入近乎静音（< -45 LUFS）或与目标差值在 1.5 LU 内，无需大幅动态改写
        if parsed["input_i"] < -45.0:
            return None
        if abs(parsed["input_i"] - target_i) <= 1.2 and parsed["input_tp"] <= true_peak:
            return None
        return (
            f"loudnorm={common}:measured_I={measured['input_i']}"
            f":measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}"
            f":measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}:linear=true"
        )
    except Exception:
        return None


# ============================================================================
# 5. 成片本地免付费切点连续性体检（Cut Luma / Suspect Frame / Audio Step）
# ============================================================================


def _frame_luma_and_rgb(frame: np.ndarray) -> tuple[float, tuple[float, float, float]]:
    arr = np.asarray(frame, dtype=np.float32)
    r, g, b = float(arr[..., 0].mean()), float(arr[..., 1].mean()), float(arr[..., 2].mean())
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    return luma, (r, g, b)


def audit_final_video_continuity(
    video_path: Path,
    scenes: Sequence[dict[str, Any]],
    timeline: Sequence[dict[str, Any]],
    *,
    shot_match_plan: dict[str, ShotColorMatch] | None = None,
    transitions_applied: Sequence[dict[str, Any]] | None = None,
    luma_jump_threshold: float = 24.0,
    suspect_glitch_threshold: float = 42.0,
    audio_jump_threshold_db: float = 12.0,
) -> dict[str, Any]:
    """
    对渲染完成后的成片运行本地零成本物理连续性审计：
    1) 检查相邻切点前后帧的亮度与RGB色彩跳变；
    2) 在高频采样点排查孤立坏帧/单帧闪白；
    3) 检查切点前后 0.4s 音频响度阶跃；
    输出可直达精确时间点的质检结构。
    """
    from moviepy.editor import VideoFileClip

    cut_checks: list[dict[str, Any]] = []
    suspect_frames: list[dict[str, Any]] = []
    audio_jumps: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    with VideoFileClip(str(video_path)) as clip:
        dur = float(clip.duration or 0.0)
        fps = float(clip.fps or 24.0)
        frame_step = 1.0 / max(1.0, fps)

        # 1. 切点画质跳变检查
        for idx in range(len(timeline) - 1):
            curr_row, next_row = timeline[idx], timeline[idx + 1]
            curr_scene, next_scene = scenes[idx], scenes[idx + 1]
            cut_t = float(curr_row["end"])
            if cut_t <= frame_step or cut_t >= dur - frame_step:
                continue
            ttype = str(next_scene.get("transition_type") or "cut")
            tmode = str(next_scene.get("transition_mode") or "")
            is_same_scene = ttype != "scene_change" and tmode != "empty_shot"
            t_before = max(0.0, cut_t - frame_step)
            t_after = min(max(0.0, dur - frame_step), cut_t + frame_step)
            luma_a, rgb_a = _frame_luma_and_rgb(clip.get_frame(t_before))
            luma_b, rgb_b = _frame_luma_and_rgb(clip.get_frame(t_after))
            delta_luma = round(abs(luma_b - luma_a), 2)
            delta_rgb = round(max(abs(a - b) for a, b in zip(rgb_a, rgb_b)), 2)
            entry = {
                "cut_time": round(cut_t, 2),
                "from_scene": str(curr_scene["scene_id"]),
                "to_scene": str(next_scene["scene_id"]),
                "same_scene": is_same_scene,
                "delta_luma": delta_luma,
                "delta_rgb": delta_rgb,
            }
            cut_checks.append(entry)
            if is_same_scene and delta_luma >= luma_jump_threshold and tmode != "whip_morph":
                issues.append({
                    "scene_id": str(next_scene["scene_id"]),
                    "scene_index": int(next_scene.get("scene_index") or (idx + 2)),
                    "time": round(cut_t, 2),
                    "scene_offset": 0.0,
                    "severity": "warning",
                    "issue": f"同场切点明暗变化偏大（Δ亮度 {delta_luma:.1f}），建议核对 {curr_scene['scene_id']}→{next_scene['scene_id']} 衔接帧。",
                })

        # 2. 孤立单帧异常闪烁检查（在非切点处按 ~0.35s 间隔抽检前后 1 帧邻域）
        cut_times = {round(float(row["end"]), 2) for row in timeline[:-1]}
        sample_ts = []
        cursor = 0.35
        while cursor < dur - 0.35:
            if all(abs(cursor - ct) > 0.16 for ct in cut_times):
                sample_ts.append(cursor)
            cursor += 0.45
        for st in sample_ts[:60]:
            f_prev = Image.fromarray(np.asarray(clip.get_frame(max(0.0, st - frame_step)), dtype=np.uint8)).resize((48, 48))
            f_curr = Image.fromarray(np.asarray(clip.get_frame(st), dtype=np.uint8)).resize((48, 48))
            f_next = Image.fromarray(np.asarray(clip.get_frame(min(dur - frame_step, st + frame_step)), dtype=np.uint8)).resize((48, 48))
            a_p = np.asarray(f_prev, dtype=np.float32)
            a_c = np.asarray(f_curr, dtype=np.float32)
            a_n = np.asarray(f_next, dtype=np.float32)
            diff_neighbors = float(np.mean(np.abs(a_p - a_n)))
            diff_spike = min(float(np.mean(np.abs(a_c - a_p))), float(np.mean(np.abs(a_c - a_n))))
            # 若前后邻帧很接近但中间帧剧烈偏离，说明存在单帧闪白/花屏/黑帧坏帧
            if diff_spike >= suspect_glitch_threshold and diff_neighbors < diff_spike * 0.45:
                suspect_frames.append({"time": round(st, 2), "spike": round(diff_spike, 1)})
                issues.append({
                    "time": round(st, 2),
                    "severity": "warning",
                    "issue": f"成片 {st:.2f}s 附近疑似存在单帧闪烁或坏帧突变（偏离度 {diff_spike:.1f}）。",
                })

        # 3. 切点前后音频 RMS 阶跃检测
        if clip.audio is not None:
            for idx in range(len(timeline) - 1):
                curr_row, next_row = timeline[idx], timeline[idx + 1]
                cut_t = float(curr_row["end"])
                if cut_t < 0.45 or cut_t > dur - 0.45:
                    continue
                t_a = np.linspace(max(0.0, cut_t - 0.40), max(0.0, cut_t - 0.03), 1200)
                t_b = np.linspace(min(dur, cut_t + 0.03), min(dur, cut_t + 0.40), 1200)
                rms_a = float(np.sqrt(np.mean(np.square(clip.audio.get_frame(t_a)))))
                rms_b = float(np.sqrt(np.mean(np.square(clip.audio.get_frame(t_b)))))
                # 仅当两侧均存在有效发声时检测突发音量跳变
                if rms_a > 0.015 and rms_b > 0.015:
                    db_diff = abs(20.0 * math.log10(rms_b / rms_a))
                    if db_diff >= audio_jump_threshold_db:
                        audio_jumps.append({
                            "cut_time": round(cut_t, 2),
                            "delta_db": round(db_diff, 1),
                        })
                        issues.append({
                            "scene_id": str(scenes[idx + 1]["scene_id"]),
                            "scene_index": int(scenes[idx + 1].get("scene_index") or (idx + 2)),
                            "time": round(cut_t, 2),
                            "scene_offset": 0.0,
                            "severity": "info",
                            "issue": f"切点 {cut_t:.2f}s 前后人声/音量阶跃约 {db_diff:.1f} dB，建议回听。",
                        })

    penalty = len([i for i in issues if i.get("severity") == "warning"]) * 8 + len(
        [i for i in issues if i.get("severity") != "warning"]
    ) * 3
    score = max(55, min(100, 100 - penalty))

    return {
        "smoothness_score": score,
        "shot_color_match_count": len(shot_match_plan or {}),
        "shot_color_match": {
            sid: {"gains": list(m.gains), "offsets": list(m.offsets)}
            for sid, m in (shot_match_plan or {}).items()
        },
        "transitions_applied": list(transitions_applied or []),
        "cut_checks": cut_checks,
        "suspect_frames": suspect_frames,
        "audio_jumps": audio_jumps,
        "issues": issues,
    }
