from datetime import UTC, datetime
from pathlib import Path
from models.task_model import VideoTaskStatus
from models.asset_model import Asset
from core.database import mongodb
from services.audio_timing import narration_speed
from services.shot_continuity import (
    apply_color_match_to_clip,
    apply_cut_transitions,
    apply_micro_audio_fade,
    measure_clip_rgb_stats,
    measure_loudnorm_filter,
    plan_shot_color_match,
    wrap_ducked_bgm,
)

from agents.agent_utils import (
    ASSETS_COLLECTION,
    assert_no_mock_assets,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    with_document_id,
)


def _fit_dialogue_audio(source, speed, scheduled_duration):
    import numpy as np

    duration = min(scheduled_duration, source.duration / speed)

    def read_frame(get_frame, time):
        # MoviePy mixes whole sample batches before masking inactive clips.
        source_time = np.asarray(time) * speed
        active = (np.asarray(time) >= 0) & (np.asarray(time) < duration) & (source_time < source.duration)
        if source_time.ndim == 0:
            return get_frame(float(source_time)) if active else np.zeros(source.nchannels)
        return get_frame(np.where(active, source_time, 0.0)) * active[:, None]

    return source.fl(read_frame).set_duration(duration)


class EditorAgent:
    agent_name = "EditorAgent"

    async def run(self, task_id: str) -> dict[str, object]:
        try:
            await mark_agent_start(
                task_id,
                self.agent_name,
                progress=94,
                status=VideoTaskStatus.EDITING,
            )

            # 1. 查询分镜和素材，用于后续合成
            from agents.agent_utils import SCENES_COLLECTION
            scenes = await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=100)
            task_scenes = [scene for scene in scenes]
            # 按 scene_index 排序，保证最终视频场景顺序正确
            task_scenes.sort(key=lambda s: int(s.get("scene_index", 0)))

            assets_in_db = await mongodb.find_many(ASSETS_COLLECTION, {"task_id": task_id}, limit=200)
            task_assets = [a for a in assets_in_db]
            assert_no_mock_assets(task_assets)

            # 建立分镜素材映射
            video_clips = {}
            for asset in task_assets:
                if asset.get("asset_type") == "video_clip":
                    video_clips.setdefault(asset["scene_id"], asset["path"])
            image_assets = {a["scene_id"]: a["path"] for a in task_assets if a.get("asset_type") == "image"}
            # 建立分镜独立配音映射 (每个分镜有自己独立的配音文件)
            scene_audio_map = {}
            for a in task_assets:
                if a.get("asset_type") == "audio" and a.get("scene_id"):
                    scene_audio_map[a["scene_id"]] = a["path"]

            mixed_inputs = []
            for scene in task_scenes:
                scene_id = str(scene["scene_id"])
                if scene.get("status") == "static_fallback":
                    mixed_inputs.append({"scene_id": scene_id, "type": "static_image", "path": scene["image_path"]})
                elif scene_id in video_clips:
                    mixed_inputs.append({
                        "scene_id": scene_id,
                        "type": "dynamic_video",
                        "path": video_clips[scene_id]
                    })
                elif scene_id in image_assets:
                    mixed_inputs.append({
                        "scene_id": scene_id,
                        "type": "static_image",
                        "path": image_assets[scene_id]
                    })

            print(f"[EditorAgent] Mixed compilation inputs: {mixed_inputs}")

            # 2. 确定输出路径
            backend_dir = Path(__file__).resolve().parents[1]
            relative_path = f"backend/outputs/{task_id}/final.mp4"
            output_file = backend_dir / "outputs" / task_id / "final.mp4"

            # All streams use the same scene timeline. No silent omissions.
            from services.generation_contract import build_timeline, format_srt_time, needs_video, scene_event_offset, voice_speed, voice_window
            from services.media_quality import resolve_media
            from contextlib import ExitStack
            import json
            import subprocess
            import imageio_ffmpeg
            from PIL import Image, ImageOps
            import numpy as np
            from moviepy.editor import (ImageClip, VideoFileClip, AudioFileClip,
                                        CompositeAudioClip, concatenate_videoclips, vfx)
            timeline = build_timeline(task_scenes)
            if len(mixed_inputs) != len(task_scenes):
                raise ValueError("Missing scene media; refusing incomplete export")
            task = await mongodb.find_one("video_tasks", {"_id": task_id}) or {}
            aspect = (16, 9) if task.get("ratio") == "16:9" else (9, 16)
            with ExitStack() as resources:
                clips, audio_segments = [], []
                speech_windows: list[tuple[float, float]] = []
                canvas = None
                for idx, (scene, item, row) in enumerate(zip(task_scenes, mixed_inputs, timeline)):
                    path = resolve_media(item["path"])
                    if not path.is_file():
                        raise FileNotFoundError(f"Missing scene media: {path}")
                    duration = row["duration"]
                    next_is_continue = (
                        idx + 1 < len(task_scenes)
                        and str(task_scenes[idx + 1].get("transition_type") or "") == "continue_action"
                    )
                    if item["type"] == "dynamic_video":
                        source = resources.enter_context(VideoFileClip(str(path)))
                        clip = source.without_audio()
                        if source.duration < duration:
                            if duration - source.duration > 0.5:
                                raise ValueError(f"Scene {scene['scene_id']} video is too short: {source.duration}s for {duration}s")
                            ratio_speed = source.duration / max(0.1, duration)
                            if ratio_speed >= 0.88:
                                # 无感平滑微调时长，避免末尾突然定格卡死
                                clip = clip.fx(vfx.speedx, ratio_speed).set_duration(duration)
                            else:
                                still = ImageClip(source.get_frame(max(0, source.duration - 1 / (source.fps or 24)))).set_duration(duration - source.duration)
                                clip = concatenate_videoclips([clip, still], method="compose")
                        else:
                            extra = source.duration - duration
                            has_opening_speech = bool(scene.get("dialogue_lines")) or (
                                bool(str(scene.get("voiceover") or "").strip()) and float(scene.get("voice_start") or 0) < 0.4
                            )
                            if (
                                extra >= 0.24
                                and scene.get("transition_type") != "continue_action"
                                and not next_is_continue
                                and not has_opening_speech
                            ):
                                # 跳过 AI 视频前几帧常见起步呆立，切入即处于运动惯性中
                                lead = min(0.16, extra - 0.04)
                                clip = clip.subclip(lead, lead + duration)
                            else:
                                clip = clip.subclip(0, duration)
                    else:
                        if needs_video(scene):
                            raise ValueError(f"Dynamic scene has no video: {scene['scene_id']}")
                        with Image.open(path) as image:
                            clip = ImageClip(np.asarray(ImageOps.exif_transpose(image).convert("RGB"))).set_duration(duration)
                            # 静帧段交替推拉呼吸动效，增强连续镜质感
                            if idx % 2 == 0:
                                clip = clip.resize(lambda t, d=duration: 1.0 + 0.05 * (t / max(0.1, d)))
                            else:
                                clip = clip.resize(lambda t, d=duration: 1.045 - 0.045 * (t / max(0.1, d)))
                    if canvas is None:
                        units = max(2, int(min(clip.w / aspect[0], clip.h / aspect[1], 120) // 2) * 2)
                        canvas = (aspect[0] * units, aspect[1] * units)
                    if tuple(clip.size) != canvas:
                        clip = clip.fl_image(lambda frame, c=canvas: np.asarray(ImageOps.fit(
                            Image.fromarray(frame), c, method=Image.Resampling.LANCZOS)))
                    clips.append(clip)
                    audio_path = scene_audio_map.get(scene["scene_id"])
                    if scene.get("dialogue_lines"):
                        lines = scene.get("dialogue_audio") or []
                        if len(lines) != len(scene["dialogue_lines"]):
                            raise ValueError(f"Missing dialogue audio: {scene['scene_id']}")
                        for line in lines:
                            audio = resources.enter_context(AudioFileClip(str(resolve_media(line["path"]))))
                            audio = _fit_dialogue_audio(audio, line["speed"], line["end"] - line["start"])
                            audio = apply_micro_audio_fade(audio)
                            abs_start = row["start"] + line["start"]
                            abs_end = row["start"] + line["end"]
                            speech_windows.append((abs_start, abs_end))
                            audio_segments.append(audio.set_start(abs_start))
                        audio_path = None
                    elif str(scene.get("voiceover") or "").strip() and not audio_path:
                        raise ValueError(f"Missing scene audio: {scene['scene_id']}")
                    if audio_path:
                        audio = resources.enter_context(AudioFileClip(str(resolve_media(audio_path))))
                        offset, slot = voice_window(scene)
                        speed = narration_speed(scene, float(scene.get("audio_duration") or audio.duration), slot)
                        if speed != 1:
                            audio = audio.fx(vfx.speedx, speed)
                        audio = apply_micro_audio_fade(audio)
                        abs_start = row["start"] + offset
                        speech_windows.append((abs_start, abs_start + float(audio.duration or slot)))
                        # Absolute placement preserves silence and missing-dialogue gaps.
                        audio_segments.append(audio.set_start(abs_start))
                    for cue in scene.get("sound_cues") or []:
                        name = cue["name"]
                        if name not in {"footstep", "impact", "water", "paper", "wind"}:
                            continue
                        offset = scene_event_offset(scene, cue["start"])
                        cue_path = backend_dir / "data" / "sfx" / f"{name}.wav"
                        if not cue_path.is_file():
                            continue
                        effect = resources.enter_context(AudioFileClip(str(cue_path)))
                        effect = effect.subclip(0, min(effect.duration, duration - offset)).volumex(0.28)
                        effect = apply_micro_audio_fade(effect, fade_in=0.015, fade_out=0.04)
                        audio_segments.append(effect.set_start(row["start"] + offset))

                # 同场连续镜头自动接镜调色（RGB 均值与离散度向组内中位数收敛）
                shot_match_plan = plan_shot_color_match(task_scenes, lambda i: measure_clip_rgb_stats(clips[i]))
                for idx, scene in enumerate(task_scenes):
                    sid = str(scene["scene_id"])
                    if sid in shot_match_plan:
                        clips[idx] = apply_color_match_to_clip(clips[idx], shot_match_plan[sid])

                # 执行切点边界微过渡
                transitions_applied = apply_cut_transitions(clips, task_scenes)

                final_video = concatenate_videoclips(clips, method="compose")
                resources.callback(final_video.close)
                if audio_segments:
                    # BGM 自动铺底与人声动态闪避（对白期平滑压低，空白/转场回升）
                    bgm_dir = backend_dir / "data" / "bgm"
                    bgm_candidates = sorted(list(bgm_dir.glob("*.mp3")) + list(bgm_dir.glob("*.wav"))) if bgm_dir.exists() else []
                    bgm_file = task.get("bgm_path") or (str(bgm_candidates[0]) if bgm_candidates else None)
                    if bgm_file and Path(bgm_file).is_file():
                        from moviepy.audio.fx.all import audio_loop
                        bgm = resources.enter_context(AudioFileClip(str(bgm_file)))
                        bgm = audio_loop(bgm, duration=timeline[-1]["end"])
                        bgm = wrap_ducked_bgm(bgm, speech_windows, base_volume=0.17, duck_volume=0.075)
                        audio_segments.insert(0, bgm.set_start(0))
                    final_audio = CompositeAudioClip(audio_segments).set_duration(timeline[-1]["end"])
                    resources.callback(final_audio.close)
                    final_video = final_video.set_audio(final_audio)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                # Separate render from published final to avoid stale-file success.
                raw_file = output_file.with_name("render.mp4")
                final_video.write_videofile(str(raw_file), fps=24, codec="libx264", audio_codec="aac",
                                            temp_audiofile=str(output_file.with_name("render_audio.m4a")), logger=None)
                subtitle_file = output_file.with_name("subtitle.srt")
                if not subtitle_file.is_file():
                    raise FileNotFoundError("Subtitle file missing")
                staged = output_file.with_name("final_staged.mp4")
                if task.get("subtitle_style") == "poetry":
                    subtitle_style = "Alignment=2,FontName=SimSun,FontSize=19,MarginV=32,MarginL=14,MarginR=14,Outline=1.5,Shadow=0"
                elif aspect == (9, 16):
                    subtitle_style = "Alignment=2,FontSize=15,MarginV=34,MarginL=14,MarginR=14,BorderStyle=1,Outline=1.6,Shadow=0.6"
                else:
                    subtitle_style = "Alignment=2,FontSize=16,MarginV=20,MarginL=16,MarginR=16,BorderStyle=1,Outline=1.8,Shadow=0.5"
                filters = [f"subtitles=subtitle.srt:force_style='{subtitle_style}'"]
                title = " ".join((task.get("intro_title") or "").split())
                title = title.translate(str.maketrans("", "", "{}<>\\")).strip()
                title_end = min(3.8, timeline[-1]["end"])
                if title and title_end > 1.2:
                    output_file.with_name("intro_title.srt").write_text(
                        f"1\n{format_srt_time(1.2)} --> {format_srt_time(title_end)}\n{title}\n",
                        encoding="utf-8",
                    )
                    filters.append(
                        "subtitles=intro_title.srt:force_style='Alignment=8,FontSize=22,MarginV=34,Outline=2'"
                    )
                ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
                loudnorm_filter = measure_loudnorm_filter(ffmpeg_exe, raw_file) if audio_segments else None
                cmd = [ffmpeg_exe, "-y", "-i", raw_file.name, "-vf", ",".join(filters)]
                if loudnorm_filter:
                    cmd += ["-af", loudnorm_filter, "-c:a", "aac", "-b:a", "192k"]
                else:
                    cmd += ["-c:a", "copy"]
                cmd.append(staged.name)
                result = subprocess.run(cmd, cwd=str(output_file.parent), capture_output=True, timeout=180)
                if result.returncode:
                    raise RuntimeError(f"Subtitle burn failed: {result.stderr.decode(errors='replace')[-1000:]}")
                from services.media_quality import extract_tail
                extract_tail(staged, output_file.with_name("cover.png"), min(1, timeline[-1]["end"]))
                staged.replace(output_file)
                raw_file.unlink(missing_ok=True)
                output_file.with_name("timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
                editing_meta = {
                    "shot_color_match": {
                        sid: {"gains": list(m.gains), "offsets": list(m.offsets)}
                        for sid, m in shot_match_plan.items()
                    },
                    "transitions_applied": transitions_applied,
                    "loudnorm_applied": bool(loudnorm_filter),
                }
                output_file.with_name("editing_meta.json").write_text(
                    json.dumps(editing_meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            synthesis_msg = "Video composed with aligned scene audio and subtitles"
            source_type = "moviepy"

            # 4. 保存最终视频素材记录
            now = datetime.now(UTC)
            asset_id = f"asset_{task_id}_video"
            asset = Asset(
                asset_id=asset_id,
                task_id=task_id,
                scene_id=None,
                asset_type="video",
                path=relative_path,
                source=source_type,
                status="success",
                created_at=now,
                updated_at=now,
            )
            await mongodb.replace_one(
                ASSETS_COLLECTION,
                {"_id": asset.asset_id},
                with_document_id(asset.model_dump(), asset.asset_id),
                upsert=True,
            )

            result = {
                "source": source_type,
                "status": "success",
                "path": relative_path,
                "mixed_inputs": mixed_inputs,
                "message": f"{synthesis_msg}; final.mp4 created.",
            }

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=96,
                status=VideoTaskStatus.EDITING,
                message=synthesis_msg,
                extra_fields={
                    "metadata.editor_status": "success",
                    "metadata.video_path": relative_path,
                },
            )
            return result
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
