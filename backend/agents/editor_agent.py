from datetime import UTC, datetime
from pathlib import Path
from models.task_model import VideoTaskStatus
from models.asset_model import Asset
from core.database import mongodb
from services.audio_timing import narration_speed

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
                canvas = None
                for scene, item, row in zip(task_scenes, mixed_inputs, timeline):
                    path = resolve_media(item["path"])
                    if not path.is_file():
                        raise FileNotFoundError(f"Missing scene media: {path}")
                    duration = row["duration"]
                    if item["type"] == "dynamic_video":
                        source = resources.enter_context(VideoFileClip(str(path)))
                        clip = source.without_audio()
                        if source.duration < duration:
                            if duration - source.duration > 0.5:
                                raise ValueError(f"Scene {scene['scene_id']} video is too short: {source.duration}s for {duration}s")
                            # Preserve the planned clock; hold only the final frame.
                            still = ImageClip(source.get_frame(max(0, source.duration - 1 / (source.fps or 24)))).set_duration(duration - source.duration)
                            clip = concatenate_videoclips([clip, still], method="compose")
                        else:
                            clip = clip.subclip(0, duration)
                    else:
                        if needs_video(scene):
                            raise ValueError(f"Dynamic scene has no video: {scene['scene_id']}")
                        with Image.open(path) as image:
                            clip = ImageClip(np.asarray(ImageOps.exif_transpose(image).convert("RGB"))).set_duration(duration)
                            # Remotion / OpenMontage 风格：Ken Burns 缓慢平滑推镜（呼吸感动效）
                            clip = clip.resize(lambda t: 1.0 + 0.05 * (t / max(0.1, duration)))
                    if canvas is None:
                        units = max(2, int(min(clip.w / aspect[0], clip.h / aspect[1], 120) // 2) * 2)
                        canvas = (aspect[0] * units, aspect[1] * units)
                    if tuple(clip.size) != canvas:
                        clip = clip.fl_image(lambda frame: np.asarray(ImageOps.fit(
                            Image.fromarray(frame), canvas, method=Image.Resampling.LANCZOS)))
                    clips.append(clip)
                    audio_path = scene_audio_map.get(scene["scene_id"])
                    if scene.get("dialogue_lines"):
                        lines = scene.get("dialogue_audio") or []
                        if len(lines) != len(scene["dialogue_lines"]):
                            raise ValueError(f"Missing dialogue audio: {scene['scene_id']}")
                        for line in lines:
                            audio = resources.enter_context(AudioFileClip(str(resolve_media(line["path"]))))
                            audio = _fit_dialogue_audio(audio, line["speed"], line["end"] - line["start"])
                            audio_segments.append(audio.set_start(row["start"] + line["start"]))
                        audio_path = None
                    elif str(scene.get("voiceover") or "").strip() and not audio_path:
                        raise ValueError(f"Missing scene audio: {scene['scene_id']}")
                    if audio_path:
                        audio = resources.enter_context(AudioFileClip(str(resolve_media(audio_path))))
                        offset, slot = voice_window(scene)
                        speed = narration_speed(scene, float(scene.get("audio_duration") or audio.duration), slot)
                        if speed != 1:
                            audio = audio.fx(vfx.speedx, speed)
                        # Absolute placement preserves silence and missing-dialogue gaps.
                        audio_segments.append(audio.set_start(row["start"] + offset))
                    for cue in scene.get("sound_cues") or []:
                        name = cue["name"]
                        if name not in {"footstep", "impact", "water", "paper", "wind"}:
                            continue
                        offset = scene_event_offset(scene, cue["start"])
                        path = backend_dir / "data" / "sfx" / f"{name}.wav"
                        if not path.is_file():
                            continue
                        effect = resources.enter_context(AudioFileClip(str(path)))
                        effect = effect.subclip(0, min(effect.duration, duration - offset)).volumex(0.28)
                        audio_segments.append(effect.set_start(row["start"] + offset))
                final_video = concatenate_videoclips(clips, method="compose")
                resources.callback(final_video.close)
                if audio_segments:
                    # MoneyPrinterTurbo 风格：BGM 背景音乐自动铺底与混音
                    bgm_dir = backend_dir / "data" / "bgm"
                    bgm_candidates = (list(bgm_dir.glob("*.mp3")) + list(bgm_dir.glob("*.wav"))) if bgm_dir.exists() else []
                    bgm_file = task.get("bgm_path") or (str(bgm_candidates[0]) if bgm_candidates else None)
                    if bgm_file and Path(bgm_file).is_file():
                        from moviepy.audio.fx.all import audio_loop, volumex
                        bgm = resources.enter_context(AudioFileClip(str(bgm_file)))
                        bgm = audio_loop(bgm, duration=timeline[-1]["end"]).fx(volumex, 0.15)
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
                subtitle_style = (
                    "FontName=SimSun,FontSize=20,MarginV=28,Outline=1.5,Shadow=0"
                    if task.get("subtitle_style") == "poetry" else "FontSize=16,Outline=2"
                )
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
                cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", raw_file.name,
                       "-vf", ",".join(filters),
                       "-c:a", "copy", staged.name]
                result = subprocess.run(cmd, cwd=str(output_file.parent), capture_output=True, timeout=180)
                if result.returncode:
                    raise RuntimeError(f"Subtitle burn failed: {result.stderr.decode(errors='replace')[-1000:]}")
                from services.media_quality import extract_tail
                extract_tail(staged, output_file.with_name("cover.png"), min(1, timeline[-1]["end"]))
                staged.replace(output_file)
                raw_file.unlink(missing_ok=True)
                output_file.with_name("timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
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
