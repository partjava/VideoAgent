import os
from hashlib import sha256

import anyio
from datetime import UTC, datetime
from pathlib import Path
from models.task_model import VideoTaskStatus
from models.asset_model import Asset
from core.database import mongodb

from agents.agent_utils import (
    SCENES_COLLECTION,
    ASSETS_COLLECTION,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    with_document_id,
)


from services.generation_contract import render_srt


class SubtitleAgent:
    agent_name = "SubtitleAgent"

    async def run(self, task_id: str) -> dict[str, object]:
        try:
            await mark_agent_start(
                task_id,
                self.agent_name,
                progress=91,
                status=VideoTaskStatus.SUBTITLE_GENERATING,
            )

            # 检查字幕是否已存在
            backend_dir = Path(__file__).resolve().parents[1]
            relative_path = f"backend/outputs/{task_id}/subtitle.srt"
            output_file = backend_dir / "outputs" / task_id / "subtitle.srt"

            # 始终重新生成字幕，保证 voiceover 和字幕一致
            if output_file.exists():
                output_file.unlink()

            # 1. 读取分镜
            scenes = await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=100)
            task_scenes = [scene for scene in scenes]
            if not task_scenes:
                raise ValueError(f"Scenes not found for task: {task_id}")
            
            task_scenes.sort(key=lambda s: int(s.get("scene_index", 0)))

            from services.video_vision import align_scene_audio, review_enabled
            from services.media_quality import resolve_media
            if review_enabled():
                for scene in task_scenes:
                    video_path = scene.get("video_path")
                    if not video_path or not ((scene.get("voice_cue") and not scene.get("voice_start_locked"))
                                              or scene.get("sound_cues")):
                        continue
                    path = resolve_media(video_path)
                    if not path.is_file():
                        continue
                    digest = sha256(path.read_bytes()).hexdigest()
                    if scene.get("audio_alignment_digest") == digest:
                        continue
                    try:
                        fields = await anyio.to_thread.run_sync(align_scene_audio, scene)
                    except Exception:
                        continue
                    fields["audio_alignment_digest"] = digest
                    await mongodb.update_one(SCENES_COLLECTION, {"_id": scene["_id"]}, fields)
                    scene.update(fields)
            srt_content = render_srt(task_scenes)

            # 3. 保存 SRT 文件
            backend_dir = Path(__file__).resolve().parents[1]
            relative_path = f"backend/outputs/{task_id}/subtitle.srt"
            output_file = backend_dir / "outputs" / task_id / "subtitle.srt"
            
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(srt_content)

            # 4. 写入 assets 集合
            now = datetime.now(UTC)
            asset_id = f"asset_{task_id}_subtitle"
            asset = Asset(
                asset_id=asset_id,
                task_id=task_id,
                scene_id=None,
                asset_type="subtitle",
                path=relative_path,
                source="system",
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
                "source": "system",
                "status": "success",
                "path": relative_path,
            }

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=93,
                status=VideoTaskStatus.SUBTITLE_GENERATING,
                message="SRT subtitle file generated successfully",
                extra_fields={
                    "metadata.subtitle_status": "success",
                    "metadata.subtitle_path": relative_path,
                },
            )
            return result
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
