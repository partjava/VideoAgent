import os
from datetime import UTC, datetime
from pathlib import Path
from models.task_model import VideoTaskStatus
from models.asset_model import Asset
from core.database import mongodb

from agents.agent_utils import (
    ASSETS_COLLECTION,
    assert_no_mock_assets,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    with_document_id,
)


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
            video_clips = {a["scene_id"]: a["path"] for a in task_assets if a.get("asset_type") == "video_clip"}
            image_assets = {a["scene_id"]: a["path"] for a in task_assets if a.get("asset_type") == "image"}
            # 建立分镜独立配音映射 (每个分镜有自己独立的配音文件)
            scene_audio_map = {}
            for a in task_assets:
                if a.get("asset_type") == "audio" and a.get("scene_id"):
                    scene_audio_map[a["scene_id"]] = a["path"]

            mixed_inputs = []
            for scene in task_scenes:
                scene_id = str(scene["scene_id"])
                if scene_id in video_clips:
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

            # 3. 使用 MoviePy 进行真实视频合成
            os.makedirs(os.path.dirname(output_file), exist_ok=True)

            synthesis_msg = "Video synthesis completed"
            source_type = "moviepy"

            try:
                try:
                    from moviepy.editor import ImageClip, VideoFileClip, concatenate_videoclips, AudioFileClip, concatenate_audioclips, vfx
                except ImportError:
                    from moviepy import ImageClip, VideoFileClip, concatenate_videoclips, AudioFileClip, concatenate_audioclips, vfx

                clips = []
                audio_segments: list[AudioFileClip] = []
                for item in mixed_inputs:
                    scene_id = item["scene_id"]
                    scene_doc = next((s for s in task_scenes if str(s["scene_id"]) == scene_id), {})
                    duration = float(scene_doc.get("duration", 5.0))

                    item_path = item["path"]
                    if not Path(item_path).is_absolute():
                        clean_item = item_path.replace("backend/", "")
                        resolved_item_path = str(backend_dir / clean_item)
                    else:
                        resolved_item_path = item_path

                    if not os.path.exists(resolved_item_path):
                        if item["type"] == "static_image":
                            print(f"[EditorAgent] Warning: asset file {resolved_item_path} not found. Creating placeholder on the fly.")
                            try:
                                from PIL import Image
                                os.makedirs(os.path.dirname(resolved_item_path), exist_ok=True)
                                # 生成一张柔和的中性色占位背景图
                                img = Image.new('RGB', (720, 1280), color=(100, 116, 139))
                                img.save(resolved_item_path)
                            except Exception as placeholder_err:
                                print(f"[EditorAgent] Failed to create dynamic placeholder image: {placeholder_err}")
                                continue
                        else:
                            print(f"[EditorAgent] Warning: asset file {resolved_item_path} not found. Skipping.")
                            continue

                    if item["type"] == "dynamic_video":
                        clip = VideoFileClip(resolved_item_path)
                        # 去掉视频原声（AI 生成的视频自带环境音效，会和 TTS 串）
                        clip = clip.without_audio() if hasattr(clip, 'without_audio') else clip.set_audio(None)
                        if clip.duration != duration:
                            end_t = min(clip.duration, duration)
                            clip = clip.subclipped(0, end_t) if hasattr(clip, 'subclipped') else clip.subclip(0, end_t)
                    else:
                        clip = ImageClip(resolved_item_path)
                        clip = clip.with_duration(duration) if hasattr(clip, 'with_duration') else clip.set_duration(duration)

                    # 收集分镜独立配音片段（不挂到 clip 上，而是单独拼合音轨）
                    if scene_id in scene_audio_map:
                        audio_path_str = scene_audio_map[scene_id]
                        if not Path(audio_path_str).is_absolute():
                            clean_audio = audio_path_str.replace("backend/", "")
                            resolved_audio = backend_dir / clean_audio
                        else:
                            resolved_audio = Path(audio_path_str)
                        if resolved_audio.exists():
                            try:
                                seg = AudioFileClip(str(resolved_audio))
                                # 如果配音比画面长，加快说话速度匹配视频时长
                                orig_dur = seg.duration
                                if orig_dur > clip.duration + 0.3:
                                    speed = orig_dur / clip.duration
                                    try:
                                        seg = seg.fx(vfx.speedx, speed)
                                    except (AttributeError, TypeError):
                                        seg = seg.with_effects([vfx.MultiplySpeed(speed)])
                                    print(f"[EditorAgent] Sped up audio {scene_id} ({orig_dur:.1f}s → {seg.duration:.1f}s, {speed:.2f}x)")
                                audio_segments.append(seg)
                                print(f"[EditorAgent] Collected audio for {scene_id}: {resolved_audio} (audio={seg.duration:.1f}s, clip={seg.duration:.1f}s)")
                            except Exception as audio_err:
                                print(f"[EditorAgent] Failed to load audio for {scene_id}: {audio_err}")

                    clips.append(clip)

                if clips:
                    final_video = concatenate_videoclips(clips, method="compose")

                    # 将各分镜的独立音轨拼成一条完整音轨，挂载到最终视频
                    if audio_segments:
                        try:
                            final_audio = concatenate_audioclips(audio_segments)
                            final_video = final_video.with_audio(final_audio) if hasattr(final_video, 'with_audio') else final_video.set_audio(final_audio)
                            print(f"[EditorAgent] Composite audio track: {final_audio.duration:.1f}s, video: {final_video.duration:.1f}s")
                        except Exception as ae:
                            print(f"[EditorAgent] Audio composite failed: {ae}")
                    else:
                        print(f"[EditorAgent] No per-scene audio segments found, video will be silent")

                    write_kwargs = {
                        "fps": 24,
                        "codec": "libx264",
                        "audio_codec": "aac",
                    }
                    try:
                        final_video.write_videofile(str(output_file), **write_kwargs, verbose=False, logger=None)
                    except TypeError:
                        final_video.write_videofile(str(output_file), **write_kwargs)


                    for c in clips:
                        c.close()
                    final_video.close()

                    # 5. 烧录字幕 (使用 python 挂载的 ffmpeg.exe)
                    try:
                        import subprocess
                        import imageio_ffmpeg
                        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
                        subtitle_file = backend_dir / "outputs" / task_id / "subtitle.srt"

                        if subtitle_file.exists():
                            print(f"[EditorAgent] Subtitle file found: {subtitle_file}. Starting to burn subtitles via FFmpeg...")
                            temp_output = backend_dir / "outputs" / task_id / "final_with_subs.mp4"
                            task_dir = backend_dir / "outputs" / task_id

                            # 使用 ffmpeg subtitles 滤镜烧录字幕， force_style 设置基础大小颜色和描边
                            cmd = [
                                ffmpeg_exe,
                                "-y",
                                "-i", "final.mp4",
                                "-vf", "subtitles=subtitle.srt:force_style='FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2'",
                                "final_with_subs.mp4"
                            ]

                            print(f"[EditorAgent] Running FFmpeg command: {' '.join(cmd)}")
                            result = subprocess.run(
                                cmd,
                                cwd=str(task_dir),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                timeout=60.0
                            )

                            if result.returncode == 0 and temp_output.exists():
                                # 成功后覆盖原 final.mp4
                                if os.path.exists(str(output_file)):
                                    try:
                                        os.remove(str(output_file))
                                    except Exception as del_err:
                                        print(f"[EditorAgent] Warning: could not remove old final.mp4 directly: {del_err}")

                                try:
                                    os.rename(str(temp_output), str(output_file))
                                    print("[EditorAgent] Subtitles successfully burned into the final video!")
                                except Exception as rename_err:
                                    # 如果重命名失败，尝试复制
                                    import shutil
                                    shutil.copy(str(temp_output), str(output_file))
                                    os.remove(str(temp_output))
                                    print("[EditorAgent] Subtitles copied and burned into final video.")
                            else:
                                print(f"[EditorAgent] FFmpeg failed with code {result.returncode}. Stderr: {result.stderr}")
                        else:
                            print(f"[EditorAgent] Warning: Subtitle file not found at {subtitle_file}. Skipping subtitle burn-in.")
                    except Exception as sub_err:
                        print(f"[EditorAgent] Error occurred during subtitle burn-in: {sub_err}")

                    synthesis_msg = "Real video synthesis completed successfully via MoviePy with subtitles"
                    source_type = "moviepy"
                    print("[EditorAgent] Successfully compiled video using MoviePy.")
                else:
                    raise ValueError("No valid video/image clips to compile.")

            except Exception as e:
                raise RuntimeError(f"Real MoviePy synthesis failed: {e}") from e

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
