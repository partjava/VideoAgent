import os
import re
from datetime import UTC, datetime
from pathlib import Path
from models.task_model import VideoTaskStatus
from models.voice_model import Voice
from models.asset_model import Asset
from services.tts_service import tts_service
from core.database import mongodb

from agents.agent_utils import (
    SCRIPTS_COLLECTION,
    ASSETS_COLLECTION,
    SCENES_COLLECTION,
    VOICES_COLLECTION,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    with_document_id,
)


SPEAKER_PREFIX_RE = re.compile(
    r"^\s*(?:[【\[\(（][^】\]\)）]{1,12}[】\]\)）]\s*[:：,，]?\s*|"
    r"[一-鿿A-Za-z0-9_]{1,12}(?:说|道)\s*[,，]\s*|"
    r"[一-鿿A-Za-z0-9_]{1,12}(?:说|道)?\s*[:：]\s*)"
)


def clean_tts_text(text: str) -> str:
    cleaned_lines: list[str] = []
    for line in str(text or "").splitlines():
        cleaned = line.strip()
        for _ in range(2):
            cleaned = SPEAKER_PREFIX_RE.sub("", cleaned).strip()
        cleaned = cleaned.strip("\"'“”‘’ ")
        if cleaned:
            cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines)


class VoiceAgent:
    agent_name = "VoiceAgent"

    async def run(self, task_id: str) -> dict[str, object]:
        try:
            await mark_agent_start(
                task_id,
                self.agent_name,
                progress=88,
                status=VideoTaskStatus.VOICE_GENERATING,
            )

            # 检查配音文件是否已存在
            backend_dir = Path(__file__).resolve().parents[1]
            existing_voice = await mongodb.find_one(VOICES_COLLECTION, {"task_id": task_id})
            combined_voice_path = backend_dir / "assets" / task_id / "audio" / "voice.mp3"
            if existing_voice and combined_voice_path.exists():
                await mark_agent_success(
                    task_id,
                    self.agent_name,
                    progress=90,
                    status=VideoTaskStatus.VOICE_GENERATING,
                    message="Skipped VoiceAgent (voice already exists)",
                    extra_fields={
                        "metadata.voice_status": "success",
                        "metadata.voice_path": f"backend/assets/{task_id}/audio/voice.mp3",
                        "metadata.voice_duration": existing_voice.get("duration", 0),
                    },
                )
                return {
                    "source": "edge-tts",
                    "status": "success",
                    "path": f"backend/assets/{task_id}/audio/voice.mp3",
                    "duration": existing_voice.get("duration", 0),
                    "voice_id": existing_voice.get("voice_id"),
                }

            # 1. 从 scripts 集合读取脚本
            script_doc = await mongodb.find_one(SCRIPTS_COLLECTION, {"_id": f"script_{task_id}"})
            if not script_doc:
                script_doc = await mongodb.find_one(SCRIPTS_COLLECTION, {"task_id": task_id})

            if not script_doc:
                raise ValueError(f"Script not found for task: {task_id}")

            scenes = await mongodb.find_many(SCENES_COLLECTION, limit=100)
            task_scenes = [scene for scene in scenes if scene.get("task_id") == task_id]
            task_scenes.sort(key=lambda scene: int(scene.get("scene_index", 0)))

            scene_voiceovers = []
            for scene in task_scenes:
                voiceover = clean_tts_text(str(scene.get("voiceover") or ""))
                if voiceover:
                    scene_voiceovers.append(voiceover)

            # 2. 为每个分镜生成独立的配音文件 (不改变分镜时长)
            audio_dir = backend_dir / "assets" / task_id / "audio"
            os.makedirs(str(audio_dir), exist_ok=True)

            now = datetime.now(UTC)
            total_duration = 0.0
            for scene in task_scenes:
                scene_id = str(scene["scene_id"])
                voiceover = clean_tts_text(str(scene.get("voiceover") or ""))
                if not voiceover:
                    continue

                scene_audio_relative = f"backend/assets/{task_id}/audio/voice_{scene_id}.mp3"
                scene_audio_path = backend_dir / "assets" / task_id / "audio" / f"voice_{scene_id}.mp3"

                # 跳过已存在的分镜配音
                existing_asset = await mongodb.find_one(
                    ASSETS_COLLECTION,
                    {"task_id": task_id, "scene_id": scene_id, "asset_type": "audio"}
                )
                if existing_asset and scene_audio_path.exists():
                    scene_duration = existing_asset.get("duration", 0)
                    total_duration += scene_duration
                    continue

                scene_duration = await tts_service.generate_voice(
                    text=voiceover,
                    output_path=str(scene_audio_path),
                    voice="zh-CN-XiaoxiaoNeural"
                )

                # 保存分镜配音到 assets 集合 (带有 scene_id)
                asset = Asset(
                    asset_id=f"asset_{task_id}_voice_{scene_id}",
                    task_id=task_id,
                    scene_id=scene_id,
                    asset_type="audio",
                    path=scene_audio_relative,
                    source="edge-tts",
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

                # 更新分镜文档中的配音路径 (供 EditorAgent 使用)
                # 注意: mongodb.update_one 内部已自动包装 $set
                await mongodb.update_one(
                    SCENES_COLLECTION,
                    {"_id": f"{task_id}_{scene_id}"},
                    {
                        "audio_path": scene_audio_relative,
                        "audio_duration": scene_duration,
                        "updated_at": now,
                    },
                )

                total_duration += scene_duration

            # 3. 生成完整的配音文件 (voice.mp3)，用于前端预览等
            text = "\n".join(scene_voiceovers).strip()
            if not text:
                text = clean_tts_text(script_doc.get("content", ""))
            if not text:
                raise ValueError(f"Script content is empty for task: {task_id}")

            relative_path = f"backend/assets/{task_id}/audio/voice.mp3"
            output_file = backend_dir / "assets" / task_id / "audio" / "voice.mp3"

            duration = await tts_service.generate_voice(
                text=text,
                output_path=str(output_file),
                voice="zh-CN-XiaoxiaoNeural"
            )

            # 不再覆盖分镜时长 —— 视频时长以分镜规划的原始时长为准

            # 4. 将配音记录保存到 voices 集合
            now = datetime.now(UTC)
            voice_id = f"voice_{task_id}"
            voice = Voice(
                voice_id=voice_id,
                task_id=task_id,
                text=text,
                voice_type="edge_tts_default",
                path=relative_path,
                duration=duration,
                status="success",
                created_at=now,
                updated_at=now,
            )
            await mongodb.replace_one(
                VOICES_COLLECTION,
                {"_id": voice.voice_id},
                with_document_id(voice.model_dump(), voice.voice_id),
                upsert=True,
            )

            # 5. 将音频素材记录保存到 assets 集合 (场景无关的总配音)
            asset_id = f"asset_{task_id}_voice"
            asset = Asset(
                asset_id=asset_id,
                task_id=task_id,
                scene_id=None,
                asset_type="audio",
                path=relative_path,
                source="edge-tts",
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
                "source": "edge-tts",
                "status": "success",
                "path": relative_path,
                "duration": duration,
                "voice_id": voice_id,
            }

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=90,
                status=VideoTaskStatus.VOICE_GENERATING,
                message="Voice generation completed successfully",
                extra_fields={
                    "metadata.voice_status": "success",
                    "metadata.voice_path": relative_path,
                    "metadata.voice_duration": duration,
                    "metadata.voice_text_source": "scene_voiceovers" if scene_voiceovers else "script_content",
                },
            )
            return result
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
