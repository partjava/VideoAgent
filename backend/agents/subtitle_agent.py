import os
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


def format_srt_time(seconds: float) -> str:
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


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

            existing_subtitle = await mongodb.find_one(ASSETS_COLLECTION, {"task_id": task_id, "asset_type": "subtitle"})
            if existing_subtitle and output_file.exists():
                await mark_agent_success(
                    task_id,
                    self.agent_name,
                    progress=93,
                    status=VideoTaskStatus.SUBTITLE_GENERATING,
                    message="Skipped SubtitleAgent (subtitle already exists)",
                    extra_fields={
                        "metadata.subtitle_status": "success",
                        "metadata.subtitle_path": relative_path,
                    },
                )
                return {
                    "source": "system",
                    "status": "success",
                    "path": relative_path,
                }

            # 1. 读取分镜
            scenes = await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=100)
            task_scenes = [scene for scene in scenes]
            if not task_scenes:
                raise ValueError(f"Scenes not found for task: {task_id}")
            
            task_scenes.sort(key=lambda s: int(s.get("scene_index", 0)))

            # 2. 组装 SRT 字幕内容
            current_time = 0.0
            srt_lines = []
            for idx, scene in enumerate(task_scenes, start=1):
                duration = float(scene.get("duration") or 5)
                subtitle_text = scene.get("subtitle") or scene.get("voiceover") or ""
                start_str = format_srt_time(current_time)
                end_str = format_srt_time(current_time + duration)
                current_time += duration
                
                srt_lines.append(f"{idx}\n{start_str} --> {end_str}\n{subtitle_text}\n")

            srt_content = "\n".join(srt_lines)

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
