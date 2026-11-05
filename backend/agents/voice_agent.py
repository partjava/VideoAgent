import anyio
import math
from services.provider_factory import get_llm_service
from services.generation_contract import MAX_VOICE_SPEED
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from models.task_model import VideoTaskStatus
from models.voice_model import Voice
from models.asset_model import Asset
from services.tts_service import resolve_voice_for_speaker, tts_service
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


STAGE_DIRECTIONS_RE = re.compile(r"[（\(【\[][^）\)】\]]{1,30}[）\)】\]]")
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
        cleaned = STAGE_DIRECTIONS_RE.sub("", cleaned).strip()
        for _ in range(2):
            cleaned = SPEAKER_PREFIX_RE.sub("", cleaned).strip()
        cleaned = cleaned.strip("\"'“”‘’（）()【】[] ")
        if cleaned:
            cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines)


async def synthesize_dialogue(scene, backend_dir, task_id, render_id):
    from services.generation_contract import scene_event_offset, voice_speed

    lines = scene["dialogue_lines"]
    offsets = [scene_event_offset(scene, line["start"]) for line in lines]
    scene_end = float(scene["duration"])
    slots = [end - start for start, end in zip(offsets, offsets[1:] + [scene_end])]
    if any(slot <= 0 for slot in slots):
        raise ValueError("对白的出声时间必须依次递增，并留在当前镜头内")
    rendered = []
    durations = []
    for index, line in enumerate(lines):
        text = clean_tts_text(line["text"])
        voice = resolve_voice_for_speaker(line["speaker"])
        path = f"assets/{task_id}/audio/dialogue_{render_id}_{index}.mp3"
        duration = await tts_service.generate_voice(text, str(backend_dir / path), voice=voice)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(f"Invalid audio duration for scene {scene['scene_id']}")
        durations.append(duration)
        rendered.append({"speaker": line["speaker"], "text": text, "path": f"backend/{path}"})

    if all(duration <= slot * MAX_VOICE_SPEED for duration, slot in zip(durations, slots)):
        speeds = [voice_speed(scene["scene_id"], duration, slot) for duration, slot in zip(durations, slots)]
    else:
        speed = voice_speed(scene["scene_id"], math.fsum(durations), scene_end - offsets[0])
        speeds = [speed] * len(lines)
        latest = [0.0] * len(lines)
        cursor = scene_end
        for index in reversed(range(len(lines))):
            cursor -= durations[index] / speed
            latest[index] = cursor
        cursor = offsets[0]
        for index, planned in enumerate(offsets):
            offsets[index] = max(cursor, min(planned, latest[index]))
            cursor = offsets[index] + durations[index] / speed

    for line, start, duration, speed in zip(rendered, offsets, durations, speeds):
        line.update(start=start, end=min(scene_end, start + duration / speed), speed=speed)
    return rendered


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
            # 1. 从 scripts 集合读取脚本
            script_doc = await mongodb.find_one(SCRIPTS_COLLECTION, {"_id": f"script_{task_id}"})
            if not script_doc:
                script_doc = await mongodb.find_one(SCRIPTS_COLLECTION, {"task_id": task_id})

            if not script_doc:
                raise ValueError(f"Script not found for task: {task_id}")

            scenes = await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=100)
            task_scenes = [scene for scene in scenes]
            task_scenes.sort(key=lambda scene: int(scene.get("scene_index", 0)))

            scene_voiceovers = []
            for scene in task_scenes:
                voiceover = clean_tts_text(str(scene.get("voiceover") or ""))
                if voiceover:
                    scene_voiceovers.append(voiceover)

            from services.generation_contract import voice_speed, voice_window
            from services.media_quality import resolve_media
            can_reuse = bool(task_scenes) and all(
                not clean_tts_text(str(s.get("voiceover") or "")) or (
                    s.get("audio_text") == clean_tts_text(str(s.get("voiceover") or ""))
                    and s.get("audio_path") and 0 < float(s.get("audio_duration") or 0) <= voice_window(s)[1] * MAX_VOICE_SPEED
                    and resolve_media(s["audio_path"]).is_file()
                ) for s in task_scenes
            )
            has_dialogue = any(scene.get("dialogue_lines") for scene in task_scenes)
            combined_text = "\n".join(scene_voiceovers).strip()
            if (not has_dialogue and existing_voice and combined_voice_path.exists() and can_reuse
                    and existing_voice.get("text") == combined_text):
                for scene in task_scenes:
                    if clean_tts_text(str(scene.get("voiceover") or "")):
                        voice_speed(scene["scene_id"], float(scene["audio_duration"]), voice_window(scene)[1])
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

            # 2. 为每个分镜生成独立的配音文件 (不改变分镜时长)
            audio_dir = backend_dir / "assets" / task_id / "audio"
            os.makedirs(str(audio_dir), exist_ok=True)

            now = datetime.now(UTC)
            total_duration = 0.0
            for scene in task_scenes:
                scene_id = str(scene["scene_id"])
                if scene.get("dialogue_lines"):
                    audio = scene.get("dialogue_audio") or []
                    if len(audio) != len(scene["dialogue_lines"]) or not all(resolve_media(line["path"]).is_file() for line in audio):
                        audio = await synthesize_dialogue(scene, backend_dir, task_id, scene_id)
                        await mongodb.update_one(SCENES_COLLECTION, {"_id": scene["_id"]}, {"dialogue_audio": audio})
                    total_duration += sum(line["end"] - line["start"] for line in audio)
                    continue
                voiceover = clean_tts_text(str(scene.get("voiceover") or ""))
                if not voiceover:
                    continue

                scene_audio_relative = f"backend/assets/{task_id}/audio/voice_{scene_id}.mp3"
                scene_audio_path = backend_dir / "assets" / task_id / "audio" / f"voice_{scene_id}.mp3"

                speaker = str(scene.get("speaker") or "").strip()
                scene_voice = resolve_voice_for_speaker(speaker)

                # 跳过已存在的分镜配音
                existing_asset = await mongodb.find_one(
                    ASSETS_COLLECTION,
                    {"task_id": task_id, "scene_id": scene_id, "asset_type": "audio"}
                )
                if scene.get("audio_path") and scene.get("audio_text") == voiceover and scene.get("voice") == scene_voice:
                    saved_audio = resolve_media(scene["audio_path"])
                    if saved_audio.is_file():
                        scene_audio_path = saved_audio
                        scene_audio_relative = scene["audio_path"]
                _, slot = voice_window(scene)
                cached = (existing_asset and scene_audio_path.exists() and scene.get("audio_text") == voiceover
                          and scene.get("audio_duration") and scene.get("voice") == scene_voice)
                scene_duration = float(scene["audio_duration"]) if cached else await tts_service.generate_voice(
                    text=voiceover, output_path=str(scene_audio_path), voice=scene_voice
                )

                # 原生语速优化：若音频略长于分镜时长，通过 edge-tts 原生 rate 微调，避免 MoviePy 变调失真
                if not cached and slot < scene_duration <= slot * MAX_VOICE_SPEED:
                    rate_pct = min(25, max(5, int(round((scene_duration / slot - 1.0) * 100))))
                    try:
                        adjusted_duration = await tts_service.generate_voice(
                            text=voiceover, output_path=str(scene_audio_path), voice=scene_voice, rate=f"+{rate_pct}%"
                        )
                        scene_duration = adjusted_duration
                    except Exception:
                        pass

                if cached and 0 < scene_duration <= slot * MAX_VOICE_SPEED:
                    total_duration += scene_duration
                    continue

                repaired = False
                if scene_duration > slot * MAX_VOICE_SPEED:
                    # Ask for a shorter line using the measured reading rate, then measure again.
                    budget = max(1, int(slot * len(voiceover) / scene_duration * 0.8))
                    llm = get_llm_service()
                    shorter = await anyio.to_thread.run_sync(
                        llm.shorten_voiceover, voiceover, budget
                    )
                    shorter = clean_tts_text(shorter)
                    if len(shorter) < 2 or len(shorter) >= len(voiceover):
                        raise ValueError(f"Could not shorten voice for scene {scene_id}")
                    candidate = scene_audio_path.with_suffix(".repair.mp3")
                    try:
                        scene_duration = await tts_service.generate_voice(
                            text=shorter, output_path=str(candidate), voice=scene_voice
                        )
                        if slot < scene_duration <= slot * MAX_VOICE_SPEED:
                            rate_pct = min(25, max(5, int(round((scene_duration / slot - 1.0) * 100))))
                            scene_duration = await tts_service.generate_voice(
                                text=shorter, output_path=str(candidate), voice=scene_voice, rate=f"+{rate_pct}%"
                            )
                        voice_speed(scene_id, scene_duration, slot)
                        candidate.replace(scene_audio_path)
                    finally:
                        candidate.unlink(missing_ok=True)
                    voiceover = shorter
                    repaired = True
                voice_speed(scene_id, scene_duration, slot)

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
                        **({"voiceover": voiceover, "subtitle": voiceover, "voiceover_repaired": True} if repaired else {}),
                        "audio_path": scene_audio_relative,
                        "audio_duration": scene_duration,
                        "audio_text": voiceover,
                        "speaker": speaker or "旁白",
                        "voice": scene_voice,
                        "updated_at": now,
                    },
                )

                scene["voiceover"] = voiceover
                scene["speaker"] = speaker or "旁白"
                scene["voice"] = scene_voice
                total_duration += scene_duration

            if has_dialogue:
                await mark_agent_success(task_id, self.agent_name, progress=90,
                                         status=VideoTaskStatus.VOICE_GENERATING,
                                         message="Scene dialogue audio saved",
                                         extra_fields={"metadata.voice_status": "success"})
                return {"status": "success", "duration": total_duration}

            # 3. 生成完整的配音文件 (voice.mp3)，用于前端预览等
            text = "\n".join(filter(None, (clean_tts_text(s.get("voiceover") or "") for s in task_scenes)))
            if not text:
                text = clean_tts_text(script_doc.get("content", ""))
            if not text:
                raise ValueError(f"Script content is empty for task: {task_id}")

            relative_path = f"backend/assets/{task_id}/audio/voice.mp3"
            output_file = backend_dir / "assets" / task_id / "audio" / "voice.mp3"

            duration = await tts_service.generate_voice(
                text=text,
                output_path=str(output_file),
                voice=resolve_voice_for_speaker("旁白")
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
