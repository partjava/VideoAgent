"""
全自动质检自愈导演引擎（Director Self-Heal Service）

当 QualityAgent 产出 visual_review / continuity_audit 缺陷或静态降级记录时，
在严格封顶的自愈预算（默认每片最多 2 镜、每镜最多 1 次、绝不突破用户配额上限）内，
自动诊断病灶、重写局部稳态提示词、单镜重拍并回流字幕/剪辑/质检闭环。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Sequence
from uuid import uuid4

import anyio

from agents.agent_utils import ASSETS_COLLECTION, SCENES_COLLECTION, with_document_id
from config import settings
from core.database import mongodb
from models.asset_model import Asset
from services.drama_craft_service import sanitize_video_prompt_mcsla
from services.generation_contract import (
    STORYBOARD_FLOW_MODE,
    build_timeline,
    continuity_input,
    needs_video,
    validate_motion_beats,
)
from services.generation_dispatch import dispatch_generation
from services.generation_usage import GenerationLimitExceeded
from services.media_quality import extract_tail, inspect_video, resolve_media
from services.provider_factory import get_llm_service
from services.shot_continuity import adapt_video_submission


DEFAULT_HEAL_PROMPT_LOCK = "保持主体位置与视线稳定、主光色温与曝光恒定，严禁闪白跳变与急剧甩头。"


def _map_time_to_scene_id(time_sec: float, timeline: Sequence[dict[str, Any]]) -> str | None:
    for row in timeline:
        if float(row.get("start", 0)) <= time_sec <= float(row.get("end", 0)):
            return str(row["scene_id"])
    return str(timeline[-1]["scene_id"]) if timeline else None


def diagnose_heal_candidates(
    scenes: Sequence[dict[str, Any]],
    timeline: Sequence[dict[str, Any]],
    quality_report: dict[str, Any] | None,
    *,
    max_repairs: int = 2,
) -> list[dict[str, Any]]:
    """从质检报告中聚合可局部重拍修复的缺陷分镜，按严重度排序并受上限截断。"""
    if max_repairs <= 0 or not quality_report or not scenes:
        return []

    scene_map = {str(s["scene_id"]): s for s in scenes}
    buckets: dict[str, dict[str, Any]] = {}

    def _add_reason(sid: str | None, reason: str, weight: int) -> None:
        if not sid or sid not in scene_map:
            return
        sc = scene_map[sid]
        if int(sc.get("auto_healed_count") or 0) >= 1:
            return
        if not needs_video(sc) and sc.get("status") != "static_fallback":
            return
        entry = buckets.setdefault(sid, {"scene_id": sid, "score": 0, "reasons": []})
        entry["score"] += weight
        if reason not in entry["reasons"]:
            entry["reasons"].append(reason)

    # 1. 视觉模型抽检缺陷
    for issue in ((quality_report.get("visual_review") or {}).get("issues") or []):
        if isinstance(issue, dict):
            sid = issue.get("scene_id")
            if not sid and isinstance(issue.get("time"), (int, float)):
                sid = _map_time_to_scene_id(float(issue["time"]), timeline)
            _add_reason(str(sid) if sid else None, f"视觉抽检：{issue.get('issue') or '动作/一致性偏差'}", 4)

    # 2. 本地连续性审计缺陷（仅针对严重闪烁坏帧或同场高亮差跳变触发重拍，轻微提示不乱烧额度）
    cont = quality_report.get("continuity_audit") or {}
    for glitch in (cont.get("suspect_frames") or []):
        if isinstance(glitch, dict) and isinstance(glitch.get("time"), (int, float)):
            sid = _map_time_to_scene_id(float(glitch["time"]), timeline)
            _add_reason(sid, f"画面 {glitch['time']:.2f}s 疑似单帧闪烁或坏帧突变", 4)

    for issue in (cont.get("issues") or []):
        if isinstance(issue, dict) and issue.get("severity") == "warning":
            sid = issue.get("scene_id")
            if not sid and isinstance(issue.get("time"), (int, float)):
                sid = _map_time_to_scene_id(float(issue["time"]), timeline)
            _add_reason(str(sid) if sid else None, f"衔接检测：{issue.get('issue') or '切点明显跳变'}", 2)

    # 3. 意外静态降级的镜头
    for sid in (quality_report.get("static_fallbacks") or []):
        _add_reason(str(sid), "视频生成曾发生静态降级，尝试以稳态提示词补拍", 3)

    # 4. 连续动作接缝首帧明显错位
    for sc in scenes:
        jr = sc.get("join_review") or {}
        if sc.get("transition_type") == "continue_action" and jr.get("review_suggested") and float(jr.get("difference") or 0) >= 0.22:
            _add_reason(str(sc["scene_id"]), f"接力首帧像素偏移较大（{jr.get('difference')}），需稳住起手衔接", 2)

    # 仅保留真正需要修复的问题镜（score >= 3 或明确视觉/闪烁/降级问题）
    ranked = [item for item in buckets.values() if item["score"] >= 3]
    ranked.sort(key=lambda item: (-item["score"], item["scene_id"]))
    return ranked[:max_repairs]


def plan_scene_self_heal(
    scene: dict[str, Any],
    diagnosis_reasons: list[str],
    *,
    allow_llm: bool = True,
    llm_service: Any = None,
    generation_mode: str | None = None,
) -> dict[str, Any]:
    """生成针对病灶的自愈改写方案；若模型不可用则回退确定性稳态增强。"""
    duration = int(scene.get("duration") or 5)
    healed = deepcopy(scene)
    rewrite_source = "deterministic_fallback"
    heal_summary = "；".join(diagnosis_reasons[:2])
    mode = str(generation_mode or scene.get("generation_mode") or "")
    per_second = mode != STORYBOARD_FLOW_MODE

    if allow_llm and (llm_service is not None or settings.enable_paid_api):
        try:
            svc = llm_service or get_llm_service()
            if hasattr(svc, "doctor_scene_prompt"):
                candidate = svc.doctor_scene_prompt(scene, diagnosis_reasons)
                if isinstance(candidate, dict):
                    beats = candidate.get("motion_beats")
                    validate_motion_beats(beats, duration, detailed=True, per_second=per_second)
                    vp = str(candidate.get("video_prompt") or "").strip()
                    if vp:
                        healed["video_prompt"] = vp
                        healed["motion_beats"] = beats
                        if candidate.get("transition_type") in {"cut", "continue_action", "scene_change"}:
                            if scene.get("transition_type") != "continue_action" or candidate["transition_type"] == "continue_action":
                                healed["transition_type"] = candidate["transition_type"]
                        heal_summary = str(candidate.get("heal_summary") or heal_summary).strip()
                        rewrite_source = "llm_doctor"
        except Exception:
            pass

    if rewrite_source == "deterministic_fallback":
        base_vp = str(healed.get("video_prompt") or healed.get("visual_description") or "").strip()
        if DEFAULT_HEAL_PROMPT_LOCK not in base_vp:
            healed["video_prompt"] = f"{base_vp}。{DEFAULT_HEAL_PROMPT_LOCK}".strip("。")
        beats = healed.get("motion_beats") or []
        for idx, b in enumerate(beats):
            if idx == len(beats) - 1:
                b["end_state"] = f"{str(b.get('end_state') or '').strip('。')}，画面光照稳定收束"
            action_txt = str(b.get("action") or "")
            for sharp in ("猛然甩头", "急剧晃动", "快速闪烁"):
                action_txt = action_txt.replace(sharp, "平稳转向")
            b["action"] = action_txt

    sanitized = sanitize_video_prompt_mcsla(
        str(healed.get("video_prompt") or ""),
        scene=healed,
        all_character_names=healed.get("cast_names") or [],
    )
    healed["video_prompt"] = sanitized["prompt"]
    return {
        "video_prompt": healed["video_prompt"],
        "motion_beats": healed.get("motion_beats"),
        "transition_type": healed.get("transition_type", scene.get("transition_type", "cut")),
        "heal_summary": heal_summary,
        "rewrite_source": rewrite_source,
    }


async def execute_director_self_heal(
    task_id: str,
    quality_report: dict[str, Any] | None,
    *,
    video_service: Any = None,
    llm_service: Any = None,
) -> dict[str, Any]:
    """在配额与次数安全上限内执行质检触发的单镜局部自愈重拍。"""
    from core.database import mongodb as _db
    from services.task_service import task_service
    from agents.video_agent import (
        _build_motion_prompt,
        render_signature,
        video_prompt_options,
        video_service_for_task,
    )

    task = await task_service.get_task(task_id)
    if not task:
        return {"healed": False, "reason": "task_not_found", "repaired_scenes": []}
    if not task.get("auto_heal", True):
        return {"healed": False, "reason": "auto_heal_disabled", "repaired_scenes": []}

    meta = task.get("metadata") or {}
    heal_used = int(meta.get("heal_attempts_used") or 0)
    max_heal = int(task.get("max_heal_attempts") if task.get("max_heal_attempts") is not None else 2)
    remaining_budget = max(0, max_heal - heal_used)
    if remaining_budget <= 0:
        return {"healed": False, "reason": "heal_budget_exhausted", "repaired_scenes": []}

    scenes = await _db.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=100)
    scenes.sort(key=lambda s: int(s.get("scene_index", 0)))
    if not scenes:
        return {"healed": False, "reason": "no_scenes", "repaired_scenes": []}

    timeline = build_timeline(scenes)
    candidates = diagnose_heal_candidates(scenes, timeline, quality_report, max_repairs=remaining_budget)
    if not candidates:
        return {"healed": False, "reason": "quality_clean", "repaired_scenes": []}

    service = video_service or video_service_for_task(task)
    ratio = str(task.get("ratio") or "9:16")
    now = datetime.now(UTC)
    repaired_records: list[dict[str, Any]] = []
    quota_exhausted = False

    by_index = {i: s for i, s in enumerate(scenes)}
    index_by_id = {str(s["scene_id"]): i for i, s in enumerate(scenes)}

    for cand in candidates:
        sid = cand["scene_id"]
        idx = index_by_id[sid]
        scene = by_index[idx]
        prior = by_index.get(idx - 1) if idx > 0 else None
        prev_tail = (prior or {}).get("last_frame_path") or (prior or {}).get("image_path")
        try:
            first = continuity_input(scene, prev_tail, scene.get("image_path"))
        except ValueError:
            continue
        if not first or not resolve_media(first).is_file():
            continue

        patch_plan = await anyio.to_thread.run_sync(
            lambda sc=scene, rs=cand["reasons"], _gm=task.get("generation_mode"): plan_scene_self_heal(
                sc, rs, allow_llm=True, llm_service=llm_service, generation_mode=_gm
            )
        )
        patched_scene = {**scene, **patch_plan, "style": task.get("style")}
        raw_motion = _build_motion_prompt(patched_scene)
        submitted_prompt, adapted_opts = adapt_video_submission(service, patched_scene, raw_motion)
        prompt_options = {**video_prompt_options(service, patched_scene), **adapted_opts}
        duration = int(scene["duration"])
        first_digest = sha256(resolve_media(first).read_bytes()).hexdigest()
        render_sig = render_signature(service, first_digest, submitted_prompt, duration, ratio, **prompt_options)
        heal_tag = f"{sid}_heal_{int(scene.get('auto_healed_count') or 0) + 1}_{uuid4().hex[:6]}"

        try:
            res = await dispatch_generation(
                {"task_id": task_id, **(task or {})},
                "video",
                heal_tag,
                lambda _tag=heal_tag, _first=first, _p=submitted_prompt, _d=duration, _opts=prompt_options: service.generate_video(
                    task_id=task_id,
                    scene_id=_tag,
                    image_path=_first,
                    video_prompt=_p,
                    duration=_d,
                    ratio=ratio,
                    **_opts,
                ),
            )
        except GenerationLimitExceeded:
            quota_exhausted = True
            break
        except Exception:
            continue

        if not isinstance(res, dict) or res.get("status") != "success" or not res.get("asset_path"):
            continue

        new_video_path = str(res["asset_path"])
        try:
            await anyio.to_thread.run_sync(inspect_video, resolve_media(new_video_path))
        except Exception:
            continue

        tail_path = scene.get("last_frame_path")
        if idx + 1 < len(scenes) and scenes[idx + 1].get("transition_type") == "continue_action":
            tail_path = f"backend/assets/{task_id}/frames/{heal_tag}_tail.png"
            await anyio.to_thread.run_sync(extract_tail, new_video_path, resolve_media(tail_path), duration)

        asset_id = f"asset_{task_id}_{sid}_video_clip"
        asset_doc = Asset(
            asset_id=asset_id,
            task_id=task_id,
            scene_id=sid,
            asset_type="video_clip",
            path=new_video_path,
            source=str(res.get("source", "video_provider")),
            status="success",
            created_at=now,
            updated_at=now,
        ).model_dump()
        asset_doc["first_frame_digest"] = first_digest
        asset_doc["render_signature"] = render_sig
        asset_doc["submitted_video_prompt"] = submitted_prompt
        await _db.replace_one(ASSETS_COLLECTION, {"_id": asset_id}, with_document_id(asset_doc, asset_id), upsert=True)

        history = list(scene.get("heal_history") or [])
        record = {
            "scene_id": sid,
            "reasons": cand["reasons"],
            "heal_summary": patch_plan["heal_summary"],
            "rewrite_source": patch_plan["rewrite_source"],
            "asset_path": new_video_path,
            "healed_at": now.isoformat(),
        }
        history.append(record)
        await _db.update_one(
            SCENES_COLLECTION,
            {"_id": f"{task_id}_{sid}"},
            {
                "video_path": new_video_path,
                "last_frame_path": tail_path,
                "video_prompt": patch_plan["video_prompt"],
                "motion_beats": patch_plan["motion_beats"],
                "transition_type": patch_plan["transition_type"],
                "submitted_video_prompt": submitted_prompt,
                "continuity_input_path": first,
                "need_dynamic_video": True,
                "stale_tail": False,
                "status": "video_done",
                "auto_healed_count": int(scene.get("auto_healed_count") or 0) + 1,
                "heal_history": history,
                "updated_at": now,
            },
        )
        repaired_records.append(record)

    if repaired_records:
        total_used = heal_used + len(repaired_records)
        all_history = list(meta.get("heal_history") or []) + repaired_records
        await task_service.update_task_fields(
            task_id,
            {
                "metadata.heal_attempts_used": total_used,
                "metadata.heal_history": all_history,
            },
        )

    return {
        "healed": len(repaired_records) > 0,
        "reason": "quota_exhausted" if quota_exhausted and not repaired_records else "repaired" if repaired_records else "no_effective_repair",
        "repaired_scenes": repaired_records,
    }
