"""
情节审判与自动打回重写智能体（Story Judge Agent）

将 15 秒四挡节奏审计、静默发呆空窗检查、对白信息密度扫描与 AI 审稿打分
直接接入主创作流水线（ScriptAgent -> StoryboardAgent 之间）：
初稿不达标时自动调用结构化改写并校验，从根源拦截“无情节感/干瞪眼/车轱辘话”。
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from config import settings
from models.script_model import Script, ScriptSegment
from services.drama_craft_service import ai_review_script, scan_script_metrics
from services.generation_contract import (
    AUTO_DIRECTOR_MODE,
    NATIVE_GENERATION_MODE,
    STORYBOARD_FLOW_MODE,
    active_provider_duration_bounds,
)


MIN_PASS_SCORE = 82
_SPOKEN_CHAR_RE = re.compile(r"[\w\u4e00-\u9fff]")


def _spoken_len(text: str) -> int:
    return len(_SPOKEN_CHAR_RE.findall(str(text or "")))


def normalize_segments_for_mode(
    raw_segments: list[Any],
    *,
    mode: str,
    min_sec: int = 4,
    max_sec: int = 12,
) -> list[dict[str, Any]]:
    """统一清洗并对齐 segments 的时长、voice_start 与 dialogue_lines 递增时间戳。"""
    if not isinstance(raw_segments, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw_seg in raw_segments:
        if not isinstance(raw_seg, dict):
            continue
        seg = dict(raw_seg)
        seg_dur = 10 if mode == NATIVE_GENERATION_MODE else max(min_sec, min(max_sec, int(seg.get("duration") or 8)))
        seg["duration"] = seg_dur
        v_start = float(seg.get("voice_start") or 0.0)
        seg["voice_start"] = max(0.0, min(round(seg_dur - 0.5, 2), v_start))
        lines = seg.get("dialogue_lines")
        if isinstance(lines, list):
            fixed_lines: list[dict[str, Any]] = []
            cursor = 0.0
            for line in lines:
                if not isinstance(line, dict):
                    continue
                speaker = str(line.get("speaker") or "").strip()
                text = str(line.get("text") or "").strip()
                if not speaker or not text:
                    continue
                raw_start = float(line.get("start") or 0.0)
                safe_start = max(cursor, min(round(seg_dur - 0.4, 2), raw_start))
                safe_start = min(round(seg_dur - 0.1, 2), safe_start)
                line_obj: dict[str, Any] = {"speaker": speaker, "text": text, "start": round(safe_start, 2)}
                emotion = str(line.get("emotion") or "").strip()
                if emotion:
                    line_obj["emotion"] = emotion
                fixed_lines.append(line_obj)
                cursor = round(safe_start + 0.1, 2)
            seg["dialogue_lines"] = fixed_lines
            if fixed_lines:
                seg["voice_start"] = fixed_lines[0]["start"]
                if not str(seg.get("voiceover") or "").strip():
                    seg["voiceover"] = "\n".join(item["text"] for item in fixed_lines)
        normalized.append(seg)
    return normalized


def audit_script_pacing_and_drama(
    script_data: dict[str, Any],
    task_plan: dict[str, Any] | None = None,
    *,
    allow_llm: bool = True,
) -> dict[str, Any]:
    """结合客观分段静默检测 + LLM 深度审稿，产出情节诊断报告。"""
    content = str(script_data.get("content") or "").strip()
    segments = [s for s in (script_data.get("segments") or []) if isinstance(s, dict)]
    metrics = scan_script_metrics(content)

    total_dur = sum(int(s.get("duration") or 0) for s in segments) or int((task_plan or {}).get("duration") or 48)
    total_spoken = 0
    silence_seconds = 0.0
    flags: list[dict[str, str]] = []

    for idx, seg in enumerate(segments, start=1):
        dur = max(1, int(seg.get("duration") or 8))
        v_start = float(seg.get("voice_start") or 0.0)
        lines = seg.get("dialogue_lines") or []
        spoken_text = " ".join(str(line.get("text") or "") for line in lines) if lines else str(seg.get("voiceover") or "")
        chars = _spoken_len(spoken_text)
        total_spoken += chars
        est_speech = chars / 3.8
        dead_tail = max(0.0, dur - v_start - est_speech)
        silence_seconds += min(float(dur), v_start + dead_tail)

        allow_silent = str((task_plan or {}).get("generation_mode") or "") == STORYBOARD_FLOW_MODE
        if idx == 1 and v_start > 1.0 and not (allow_silent and chars == 0):
            flags.append({
                "rule_id": "SLOW_OPENING",
                "severity": "high",
                "diagnosis": f"第 1 段开场等待 {v_start:.1f} 秒才发声，前 3 秒钩子起势偏慢。",
                "prescription": "开场控制在 0.2~0.5 秒内以冲突台词或摔物证直接破题。",
            })
        elif v_start >= 1.5 and not (allow_silent and chars == 0):
            flags.append({
                "rule_id": "EXCESSIVE_SILENCE_GAP",
                "severity": "medium",
                "diagnosis": f"第 {idx} 段等待 {v_start:.1f} 秒才开口，镜头起手发呆时间偏长。",
                "prescription": "将起音前移至 0.2~0.6 秒内。",
            })

        if dead_tail > (2.8 if allow_silent else 2.3) and not (allow_silent and chars == 0):
            flags.append({
                "rule_id": "EXCESSIVE_SILENCE_GAP",
                "severity": "high" if dead_tail >= 3.2 else "medium",
                "diagnosis": f"第 {idx} 段总长 {dur} 秒但台词仅 {chars} 字，末尾有约 {dead_tail:.1f} 秒无声干瞪眼。",
                "prescription": f"补充攻防对白或关键利害结论至约 {int(dur * 3.0)}~{int(dur * 3.5)} 字，压缩静默留白。",
            })

    if segments:
        chars_per_sec = round(total_spoken / max(1, total_dur), 2)
        silence_ratio = round(min(1.0, silence_seconds / max(1, total_dur)), 2)
        is_flow_mode = str((task_plan or {}).get("generation_mode") or "") == STORYBOARD_FLOW_MODE
        if chars_per_sec < (1.4 if is_flow_mode else 2.2):
            flags.append({
                "rule_id": "LOW_STORY_DENSITY",
                "severity": "high",
                "diagnosis": f"全片 {total_dur} 秒仅 {total_spoken} 字对白/旁白（{chars_per_sec} 字/秒），叙事信息偏稀、容易像慢动作默剧。",
                "prescription": "补足两轮以上筹码加码与大白话利害交锋，保持每秒 3.0~3.8 字紧凑节奏。",
            })
        if total_dur >= 35 and len(segments) >= 4:
            dynamic_action_re = re.compile(r"起身|站起|拍|摔|砸|逼近|步步|退|拦|挡|推门|踹|抢|按|扯|撕|翻|掀|跪|指向|亮出|反杀")
            emotion_shift_re = re.compile(r"冷笑|嗤笑|暴怒|怒|僵|剧变|骤变|煞白|震撼|崩溃|发颤|咬牙|低吼|厉声|撕破脸")
            dynamic_count = sum(1 for s in segments if dynamic_action_re.search(str(s.get("plot") or "")))
            emotion_count = sum(1 for s in segments if emotion_shift_re.search(str(s.get("plot") or "") + " " + str(s.get("voiceover") or "")))
            if dynamic_count < 2 or emotion_count < 2:
                flags.append({
                    "rule_id": "FLAT_PLOT_AND_EMOTION",
                    "severity": "high",
                    "diagnosis": f"全片长达 {total_dur} 秒，但段落动作调度与情绪反差转折过少（仅 {dynamic_count} 镜明显动作调度、{emotion_count} 镜情绪明显变化），容易演成坐着干聊。",
                    "prescription": "重写加入拍案起身/逼近拦门/甩物反扑等大幅调度，并拉满从冷嘲→暴怒→破防反转的情绪跨度。",
                })
    else:
        chars_per_sec = 0.0
        silence_ratio = 0.0

    if metrics.get("found_ai_cliches"):
        flags.append({
            "rule_id": "AI_CLICHE_WORDING",
            "severity": "medium",
            "diagnosis": f"台词夹带 AI 套话：{', '.join(metrics['found_ai_cliches'])}",
            "prescription": "全部替换为口语短刀句与具体算账结论。",
        })
    if metrics.get("found_invalid_micro_actions"):
        flags.append({
            "rule_id": "UNSHOOTABLE_MICRO_ACTION",
            "severity": "medium",
            "diagnosis": f"包含难拍抽象心理词：{', '.join(metrics['found_invalid_micro_actions'])}",
            "prescription": "改写为镜头可拍的面部肌肉、手部动作或道具结果。",
        })

    review: dict[str, Any] = {}
    if allow_llm and settings.enable_paid_api and len(content) >= 5:
        try:
            scene_briefs = [
                {
                    "index": i + 1,
                    "duration": s.get("duration"),
                    "plot": s.get("plot"),
                    "voiceover": s.get("voiceover"),
                    "dialogue_lines": s.get("dialogue_lines") or [],
                }
                for i, s in enumerate(segments)
            ]
            review = ai_review_script(content, scenes=scene_briefs, duration=total_dur)
        except Exception:
            review = {}

    if not review:
        penalty = sum(12 if f["severity"] == "high" else 6 for f in flags)
        fallback_score = max(45, 92 - penalty)
        review = {
            "overall_score": fallback_score,
            "grade": "S" if fallback_score >= 90 else "A" if fallback_score >= 82 else "B" if fallback_score >= 70 else "C",
            "verdict": "pass" if fallback_score >= MIN_PASS_SCORE and not any(f["severity"] == "high" for f in flags) else "needs_polish",
            "dimensions": {
                "pacing": {"label": "15秒换挡与紧凑节奏", "score": max(40, 90 - penalty)},
                "payoff": {"label": "筹码升级与当集结算", "score": fallback_score},
                "dialogue": {"label": "台词去AI味与声线区分", "score": fallback_score},
                "shootability": {"label": "空间可拍性", "score": 88},
                "continuity": {"label": "反转逻辑闭环", "score": fallback_score},
            },
            "four_gear_timeline": [],
            "issues": [],
            "metrics": metrics,
            "source": "deterministic_audit",
        }

    existing_issues = list(review.get("issues") or [])
    merged_issues = existing_issues + [
        {
            "rule_id": f["rule_id"],
            "category": "节奏与可拍性",
            "severity": f["severity"],
            "evidence": "",
            "diagnosis": f["diagnosis"],
            "prescription": f["prescription"],
        }
        for f in flags
    ]
    base_score = int(review.get("overall_score") or 78)
    high_flag_penalty = 8 * sum(1 for f in flags if f["severity"] == "high")
    adjusted_score = max(0, min(100, base_score - high_flag_penalty))
    needs_rewrite = (
        adjusted_score < MIN_PASS_SCORE
        or review.get("verdict") != "pass"
        or any(str(iss.get("severity") or "").lower() == "high" for iss in merged_issues)
        or any(f["rule_id"] in {"SLOW_OPENING", "EXCESSIVE_SILENCE_GAP", "LOW_STORY_DENSITY"} and f["severity"] == "high" for f in flags)
    )
    grade = "S" if adjusted_score >= 90 else "A" if adjusted_score >= 82 else "B" if adjusted_score >= 70 else "C"

    review.update({
        "overall_score": adjusted_score,
        "grade": grade,
        "verdict": "needs_polish" if needs_rewrite else "pass",
        "issues": merged_issues,
        "issue_count": len(merged_issues),
        "deterministic_flags": [f["rule_id"] for f in flags],
        "pacing_stats": {
            "total_duration": total_dur,
            "total_spoken_chars": total_spoken,
            "chars_per_second": chars_per_sec,
            "silence_ratio": silence_ratio,
        },
        "needs_rewrite": needs_rewrite,
    })
    return review


class StoryJudgeAgent:
    """情节审判官：审查剧本初稿戏剧冲突与静默空窗，不达标时自动重写并校验结构。"""

    agent_name = "StoryJudgeAgent"

    @classmethod
    def judge_and_polish(
        cls,
        task: dict[str, Any],
        script_data: dict[str, Any],
        task_plan: dict[str, Any],
        *,
        llm_service: Any = None,
        allow_llm: bool = True,
    ) -> dict[str, Any]:
        mode = str(task.get("generation_mode") or AUTO_DIRECTOR_MODE)
        min_s, max_s = active_provider_duration_bounds(generation_mode=mode, cfg=settings)

        working_script = deepcopy(script_data)
        if mode in {AUTO_DIRECTOR_MODE, NATIVE_GENERATION_MODE, STORYBOARD_FLOW_MODE}:
            working_script["segments"] = normalize_segments_for_mode(
                working_script.get("segments") or [],
                mode=mode,
                min_sec=min_s,
                max_sec=max_s,
            )

        before_review = audit_script_pacing_and_drama(working_script, task_plan, allow_llm=allow_llm)
        final_script = working_script
        final_review = before_review
        rewritten = False
        changes_made: list[str] = []

        should_rewrite = (
            bool(before_review.get("needs_rewrite"))
            and mode in {AUTO_DIRECTOR_MODE, NATIVE_GENERATION_MODE, STORYBOARD_FLOW_MODE}
            and allow_llm
            and llm_service is not None
            and hasattr(llm_service, "rewrite_script_with_judgment")
        )

        if should_rewrite:
            try:
                rewritten_raw = llm_service.rewrite_script_with_judgment(
                    task_plan, working_script, before_review
                )
                if isinstance(rewritten_raw, dict):
                    candidate = deepcopy(working_script)
                    for key in ("title", "hook", "content", "ending", "publish_copy"):
                        val = rewritten_raw.get(key)
                        if isinstance(val, str) and val.strip():
                            candidate[key] = val.strip()

                    cand_segs = normalize_segments_for_mode(
                        rewritten_raw.get("segments") or [],
                        mode=mode,
                        min_sec=min_s,
                        max_sec=max_s,
                    )
                    if mode == NATIVE_GENERATION_MODE and task.get("duration"):
                        if len(cand_segs) != len(working_script.get("segments") or []) or sum(
                            s["duration"] for s in cand_segs
                        ) != int(task["duration"]):
                            cand_segs = []
                    elif task.get("duration_locked") and task.get("duration") and cand_segs:
                        if sum(s["duration"] for s in cand_segs) != int(task["duration"]):
                            cand_segs = []

                    if cand_segs:
                        # 校验重写后的结构满足 ScriptSegment 模型规则
                        validated = [ScriptSegment(**s).model_dump() for s in cand_segs]
                        candidate["segments"] = validated
                        if not candidate.get("content"):
                            candidate["content"] = "\n".join(s.get("voiceover") or "" for s in validated)
                        Script(
                            script_id=f"judge_check_{task.get('task_id', 'x')}",
                            task_id=str(task.get("task_id") or "x"),
                            content=candidate["content"],
                            segments=validated,
                        )
                        after_review = audit_script_pacing_and_drama(
                            candidate, task_plan, allow_llm=allow_llm
                        )
                        final_script = candidate
                        final_review = after_review
                        rewritten = True
                        changes_made = [
                            str(c).strip()
                            for c in (rewritten_raw.get("changes_made") or [])
                            if str(c).strip()
                        ]
            except Exception as exc:
                print(f"[StoryJudgeAgent] Auto-rewrite skipped safely: {exc}", flush=True)

        summary = {
            "before_score": int(before_review.get("overall_score") or 0),
            "before_grade": before_review.get("grade"),
            "after_score": int(final_review.get("overall_score") or 0),
            "after_grade": final_review.get("grade"),
            "rewritten": rewritten,
            "changes_made": changes_made,
            "deterministic_flags": final_review.get("deterministic_flags") or [],
            "pacing_stats": final_review.get("pacing_stats") or {},
        }
        return {
            "script_data": final_script,
            "script_review_38": final_review,
            "story_judge": summary,
        }
