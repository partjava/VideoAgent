from pathlib import Path
import json
import re

import anyio
from models.task_model import VideoTaskStatus

from agents.agent_utils import mark_agent_failed, mark_agent_start, mark_agent_success


def locate_quality_issues(issues: list[dict], scenes: list[dict], timeline: list[dict]) -> list[dict]:
    entries = []
    for idx, (scene, row) in enumerate(zip(scenes, timeline)):
        entries.append({
            "scene_id": str(scene.get("scene_id") or f"scene_{idx + 1}"),
            "scene_index": int(scene.get("scene_index") or (idx + 1)),
            "start": float(row.get("start") or 0.0),
            "end": float(row.get("end") or 0.0),
            "duration": float(row.get("duration") or scene.get("duration") or 0.0),
        })
    by_id = {entry["scene_id"]: entry for entry in entries}
    located = []
    for raw in issues or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        time_val = item.get("time")
        has_time = isinstance(time_val, (int, float)) and not isinstance(time_val, bool)
        entry = by_id.get(str(item.get("scene_id") or ""))
        if entry is None and has_time and entries:
            t = float(time_val)
            entry = next((e for e in entries if e["start"] <= t <= e["end"]), None)
            if entry is None:
                entry = entries[-1] if t > entries[-1]["end"] else entries[0]
        if entry is not None:
            item["scene_id"] = entry["scene_id"]
            item["scene_index"] = entry["scene_index"]
            t = float(time_val) if has_time else entry["start"]
            item["time"] = round(t, 2)
            item["scene_offset"] = round(max(0.0, min(entry["duration"], t - entry["start"])), 2)
        located.append(item)
    return located


def locate_semantic_issues(issues: list, scenes: list[dict], timeline: list[dict]) -> list[dict]:
    entries = {}
    by_index = {}
    for idx, (scene, row) in enumerate(zip(scenes, timeline)):
        sid = str(scene.get("scene_id") or f"scene_{idx + 1}")
        sidx = int(scene.get("scene_index") or (idx + 1))
        info = {"scene_id": sid, "scene_index": sidx, "time": round(float(row.get("start") or 0.0), 2), "scene_offset": 0.0}
        entries[sid] = info
        by_index[sidx] = info
    located = []
    for raw in issues or []:
        text = raw.get("issue") if isinstance(raw, dict) else str(raw or "")
        if not text:
            continue
        match_info = None
        for sid, info in entries.items():
            if sid in text:
                match_info = info
                break
        if match_info is None:
            m = re.search(r"(?:scene_|分镜\s*|镜头\s*)(\d+)", text, re.IGNORECASE)
            if m:
                match_info = by_index.get(int(m.group(1)))
        located.append({"issue": text, **(match_info or {})})
    return located


class QualityAgent:
    agent_name = "QualityAgent"

    async def run(self, task_id: str) -> dict[str, object]:
        try:
            await mark_agent_start(
                task_id,
                self.agent_name,
                progress=97,
                status=VideoTaskStatus.CHECKING,
            )

            backend_dir = Path(__file__).resolve().parents[1]
            video_file = backend_dir / "outputs" / task_id / "final.mp4"
            if not video_file.exists():
                raise FileNotFoundError(f"Video file final.mp4 was not generated for task: {task_id}")

            from core.database import mongodb
            from services.generation_contract import build_timeline, needs_video, render_srt, validate_motion_beats
            from services.media_quality import inspect_video, resolve_media
            scenes = await mongodb.find_many("scenes", {"task_id": task_id}, limit=100)
            scenes.sort(key=lambda s: int(s.get("scene_index", 0)))
            task = await mongodb.find_one("video_tasks", {"_id": task_id}) or {}
            timeline = build_timeline(scenes)
            for index, scene in enumerate(scenes):
                if scene.get("stale_tail"):
                    raise ValueError(f"Scene {scene['scene_id']} must be regenerated from the updated tail")
                if needs_video(scene):
                    if not scene.get("video_path"):
                        raise ValueError(f"Missing dynamic scene: {scene['scene_id']}")
                    validate_motion_beats(scene.get("motion_beats"), float(scene["duration"]), detailed=True)
                    await anyio.to_thread.run_sync(inspect_video, resolve_media(scene["video_path"]))
                    if scene.get("transition_type") == "continue_action":
                        prior = scenes[index - 1] if index else {}
                        expected_input = prior.get("last_frame_path") if needs_video(prior) else prior.get("image_path")
                        if not expected_input or scene.get("continuity_input_path") != expected_input or not resolve_media(expected_input).is_file():
                            raise ValueError(f"Missing continuation evidence: {scene['scene_id']}")
                elif not scene.get("image_path") or not resolve_media(scene["image_path"]).is_file():
                    raise ValueError(f"Missing scene image: {scene['scene_id']}")
                dialogue = scene.get("dialogue_audio") or []
                audio_ready = (
                    len(dialogue) == len(scene["dialogue_lines"]) and all(resolve_media(line["path"]).is_file() for line in dialogue)
                    if scene.get("dialogue_lines")
                    else scene.get("audio_path") and resolve_media(scene["audio_path"]).is_file()
                )
                if scene.get("voiceover") and not audio_ready:
                    raise ValueError(f"Missing scene audio: {scene['scene_id']}")
            media = await anyio.to_thread.run_sync(
                inspect_video, video_file, timeline[-1]["end"], task.get("ratio", "9:16"),
                any(s.get("voiceover") for s in scenes),
            )
            subtitle = video_file.with_name("subtitle.srt")
            if not subtitle.is_file() or subtitle.read_text(encoding="utf-8") != render_srt(scenes):
                raise ValueError("Subtitle cues do not match the scene timeline")
            result = {"source": "local_media_validation", "status": "success", "media": media,
                      "message": "Decoding, duration, aspect ratio, audio and scene completeness passed",
                      "semantic_review": "not_run", "limitations": "Character identity and action execution require visual review"}
            result["static_fallbacks"] = [scene["scene_id"] for scene in scenes
                                           if scene.get("status") == "static_fallback"]
            plan = (task.get("metadata") or {}).get("task_plan") or {}
            if plan.get("video_type"):
                from services.provider_factory import get_llm_service
                review_inputs = [{
                    "scene_id": scene["scene_id"],
                    "picture": scene.get("visual_description"),
                    "narration": scene.get("voiceover"),
                    "media": "video" if needs_video(scene) else "still image",
                } for scene in scenes]
                try:
                    review = await anyio.to_thread.run_sync(
                        get_llm_service().review_story_coverage, task.get("user_input", ""),
                        plan.get("plot_beats", []), review_inputs,
                    )
                    raw_sem_issues = review.get("issues", [])
                    result["semantic_review"] = {
                        "scope": "script_and_planned_visuals",
                        "verdict": "pass" if review.get("verdict") == "pass" else "needs_review",
                        "issues": raw_sem_issues,
                        "located_issues": locate_semantic_issues(raw_sem_issues, scenes, timeline),
                    }
                except Exception:
                    result["semantic_review"] = {"scope": "script_and_planned_visuals", "verdict": "unavailable"}
            from services.video_vision import review_enabled, review_scene
            if review_enabled():
                issues, unavailable = [], []
                for scene, row in zip(scenes, timeline):
                    try:
                        observed = (scene.get("observed_handoff") or {}).get("plan") or {}
                        issues.extend(await anyio.to_thread.run_sync(
                            review_scene, video_file, {**scene, **observed}, row["start"],
                        ))
                    except Exception:
                        unavailable.append(scene["scene_id"])
                issues = locate_quality_issues(issues, scenes, timeline)
                result["visual_review"] = {
                    "scope": "sampled_actual_frames", "issues": issues, "unavailable_scenes": unavailable,
                    "verdict": "needs_review" if issues else "incomplete" if unavailable else "pass",
                }
            else:
                result["visual_review"] = {"scope": "sampled_actual_frames", "verdict": "disabled"}
            report_path = video_file.with_name("quality_report.json")
            report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=98,
                status=VideoTaskStatus.CHECKING,
                message="Quality check passed",
                extra_fields={"metadata.quality_status": "success", "metadata.quality_report": result},
            )
            return result
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
