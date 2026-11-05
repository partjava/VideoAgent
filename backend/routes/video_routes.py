from datetime import UTC, datetime
from pathlib import Path
from hashlib import sha256
from uuid import uuid4
import anyio
from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from pydantic import BaseModel, Field

from agents.pipeline import run_pipeline
from core.database import mongodb
from models.response_model import ApiResponse
from models.task_model import CreateVideoTaskRequest, VideoTaskStatus, RerunSceneRequest
from services.task_service import task_service
from services.provider_factory import get_llm_service
from agents.voice_agent import clean_tts_text, synthesize_dialogue
from services.tts_service import resolve_voice_for_speaker, tts_service
from agents.editor_agent import EditorAgent
from agents.quality_agent import QualityAgent
from agents.subtitle_agent import SubtitleAgent
from agents.export_agent import ExportAgent
from agents.video_agent import _build_motion_prompt, prepare_motion_prompt, render_signature, video_prompt_options, video_service_for_task
from services.media_quality import compare_join_frames
from agents.agent_utils import with_document_id
from models.asset_model import Asset
from services.generation_contract import MAX_VOICE_SPEED, continuity_input, voice_speed, voice_window
from services.media_quality import extract_tail, inspect_video, resolve_media


router = APIRouter(prefix="/api/video", tags=["video"])


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "message": message,
        },
    )


@router.get("/status")
async def video_route_status() -> dict[str, str]:
    return {
        "status": "ok",
        "message": "video routes are ready for real generation",
    }


@router.post("/create", response_model=ApiResponse)
async def create_video_task(payload: CreateVideoTaskRequest) -> ApiResponse | JSONResponse:
    try:
        task = await task_service.create_task(payload)
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)
    return ApiResponse(status="success", data=task.model_dump())


@router.get("/list", response_model=ApiResponse)
async def list_video_tasks() -> ApiResponse | JSONResponse:
    try:
        tasks = await task_service.list_tasks()
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)
    return ApiResponse(status="success", data=tasks)


async def run_pipeline_wrapper(task_id: str) -> None:
    print(f"[Pipeline Wrapper] Starting pipeline for {task_id}")
    try:
        result = await run_pipeline(task_id)
        print(f"[Pipeline Wrapper] Pipeline finished: {result.get('status')}")
    except Exception as exc:
        print(f"[Pipeline Wrapper] Pipeline failed for task {task_id}: {exc}")
        try:
            await task_service.update_task_fields(
                task_id,
                {
                    "status": VideoTaskStatus.FAILED.value,
                    "metadata.last_error": str(exc),
                },
            )
        except Exception as inner_exc:
            print(f"[BackgroundTasks] Failed to update task error status: {inner_exc}")


@router.post("/{task_id}/run-pipeline", response_model=ApiResponse)
async def run_video_pipeline(
    task_id: str,
    background_tasks: BackgroundTasks,
    run_async: bool = True,
    resume: bool = True,
) -> ApiResponse | JSONResponse:
    try:
        task = await task_service.get_task(task_id)
        if task is None:
            return error_response("Task not found", 404)

        if not resume:
            # 清理历史资产文件和数据库，准备重头开始生成
            await task_service.clear_task_assets(task_id)
            await task_service.update_task_fields(
                task_id,
                {
                    "status": VideoTaskStatus.CREATED.value,
                    "progress": 0,
                    "current_agent": None,
                    "metadata.stage_states": {},
                },
            )

        if run_async:
            background_tasks.add_task(run_pipeline_wrapper, task_id)
            return ApiResponse(status="success", data={"message": "Pipeline execution started."})

        result = await run_pipeline(task_id)
        return ApiResponse(status="success", data=result)
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)
    except ValueError as exc:
        return error_response(str(exc), 400)


@router.get("/{task_id}/script", response_model=ApiResponse)
async def get_video_task_script(task_id: str) -> ApiResponse | JSONResponse:
    try:
        script = await mongodb.find_one("scripts", {"task_id": task_id})
        if script is None:
            script = await mongodb.find_one("scripts", {"_id": f"script_{task_id}"})
        if script is None:
            return error_response("Script not found", 404)
        if "_id" in script:
            script["script_id"] = str(script.pop("_id"))
        return ApiResponse(status="success", data=script)
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)


@router.get("/{task_id}/storyboard", response_model=ApiResponse)
async def get_video_task_storyboard(task_id: str) -> ApiResponse | JSONResponse:
    try:
        scenes = await mongodb.find_many("scenes", {"task_id": task_id}, limit=100)
        task_scenes = [scene for scene in scenes]
        task_scenes.sort(key=lambda scene: int(scene.get("scene_index", 0)))
        for scene in task_scenes:
            if "_id" in scene:
                scene["_id"] = str(scene["_id"])
            if (scene.get("need_dynamic_video") and scene.get("motion_beats")
                    and scene.get("video_prompt") and scene.get("transition_type") != "continue_action"):
                try:
                    scene["planned_video_prompt"] = _build_motion_prompt(scene)
                except (ValueError, KeyError, TypeError):
                    # Older scenes may lack the fields required to build a render prompt.
                    pass
            if scene.get("audio_path") and scene.get("audio_duration"):
                try:
                    start, slot = voice_window(scene)
                    duration = float(scene["audio_duration"])
                    scene["voice_end"] = start + duration / voice_speed(scene["scene_id"], duration, slot)
                except (ValueError, KeyError, TypeError):
                    pass
        return ApiResponse(status="success", data=task_scenes)
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)


@router.get("/{task_id}", response_model=ApiResponse)
async def get_video_task(task_id: str) -> ApiResponse | JSONResponse:
    try:
        task = await task_service.get_task(task_id)
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)
    if task is None:
        return error_response("Task not found", 404)
    return ApiResponse(status="success", data=task)


@router.get("/{task_id}/progress", response_model=ApiResponse)
async def get_video_task_progress(task_id: str) -> ApiResponse | JSONResponse:
    try:
        progress = await task_service.get_task_progress(task_id)
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)
    if progress is None:
        return error_response("Task not found", 404)
    return ApiResponse(status="success", data=progress.model_dump())


@router.delete("/{task_id}", response_model=ApiResponse)
async def delete_video_task(task_id: str) -> ApiResponse | JSONResponse:
    try:
        task = await task_service.get_task(task_id)
        if task is None:
            return error_response("Task not found", 404)
        deleted = await task_service.delete_task(task_id)
        return ApiResponse(status="success", data={"deleted": deleted})
    except PyMongoError:
        return error_response("MongoDB unavailable", 503)


class PolishSceneRequest(BaseModel):
    voiceover: str = Field(max_length=2000)
    speaker: str = Field(max_length=100)
    voice_start: float = Field(ge=0, allow_inf_nan=False)


@router.post("/{task_id}/scene/{scene_id}/polish-dialogue", response_model=ApiResponse)
async def polish_scene_dialogue(task_id: str, scene_id: str, payload: PolishSceneRequest):
    scene = await mongodb.find_one("scenes", {"task_id": task_id, "scene_id": scene_id})
    if not scene:
        return error_response("找不到该镜头", 404)
    draft = {**scene, **payload.model_dump(), "subtitle": payload.voiceover}
    try:
        voice_window(draft)
    except ValueError:
        return error_response("请检查旁白起点是否在镜头时长内", 422)
    try:
        service = get_llm_service()
        results = await anyio.to_thread.run_sync(service.polish_dialogue, [draft])
        for result in results:
            if str(result.get("scene_id")) == scene_id:
                voiceover = str(result.get("voiceover") or "").strip()
                if voiceover:
                    return ApiResponse(status="success", data={"voiceover": voiceover})
    except Exception:
        return error_response("旁白优化失败，请检查提示词模型配置后重试", 502)
    return error_response("模型没有返回该镜头的有效旁白", 502)


@router.post("/{task_id}/scene/{scene_id}/rerun", response_model=ApiResponse)
async def rerun_scene_asset(
    task_id: str,
    scene_id: str,
    payload: RerunSceneRequest,
) -> ApiResponse | JSONResponse:
    """
    OpenCut / OpenMontage 风格：单镜头局部重新生成与微调。
    支持重新生成配音、图片、视频，并自动重新合成最终视频。
    """
    try:
        task = await task_service.get_task(task_id)
        if not task:
            return error_response("Task not found", 404)

        scene = await mongodb.find_one("scenes", {"task_id": task_id, "scene_id": scene_id})
        if not scene:
            scene = await mongodb.find_one("scenes", {"_id": f"{task_id}_{scene_id}"})
        if not scene:
            return error_response("Scene not found", 404)

        now = datetime.now(UTC)
        backend_dir = Path(__file__).resolve().parents[1]
        updated_fields = {}
        if payload.rerun_type not in {"voice", "image", "video", "all"}:
            return error_response("Invalid rerun type", 400)
        if scene.get("dialogue_lines") and payload.rerun_type in {"voice", "all"}:
            if payload.rerun_type != "voice":
                return error_response("请先单独重新生成对白，再处理其他素材", 400)
            lines = await synthesize_dialogue(scene, backend_dir, task_id, f"{scene_id}_{uuid4().hex[:12]}")
            await mongodb.update_one("scenes", {"_id": scene["_id"]}, {"dialogue_audio": lines, "updated_at": now})
            if task.get("status") == "failed":
                return ApiResponse(status="success", data={"message": "对白已保存，可以继续原任务"})
            await SubtitleAgent().run(task_id)
            await EditorAgent().run(task_id)
            await QualityAgent().run(task_id)
            await ExportAgent().run(task_id)
            return ApiResponse(status="success", data={"message": "对白已更新并重新合成"})
        if payload.voice_start is not None:
            if payload.voice_start != scene.get("voice_start", 0):
                updated_fields["voice_start_locked"] = True
            scene["voice_start"] = payload.voice_start
            voice_window(scene)
            updated_fields["voice_start"] = payload.voice_start
        render_id = f"{scene_id}_edit_{uuid4().hex[:12]}"
        prior = None
        if scene.get("transition_type") == "continue_action":
            prior = await mongodb.find_one("scenes", {"task_id": task_id, "scene_index": scene["scene_index"] - 1})
            if payload.rerun_type in {"image", "all"}:
                return error_response("This clip uses its previous tail. Rerun the video to use the updated tail.", 400)

        async def save_asset(asset_type, path, source, **extra):
            asset_id = f"asset_{task_id}_{scene_id}_{asset_type}"
            asset = Asset(asset_id=asset_id, task_id=task_id, scene_id=scene_id, asset_type=asset_type,
                          path=path, source=source, status="success", created_at=now, updated_at=now)
            await mongodb.replace_one("assets", {"_id": asset_id},
                                      with_document_id({**asset.model_dump(), **extra}, asset_id), upsert=True)


        # 1. 更新台词或角色
        if payload.voiceover is not None:
            new_voiceover = clean_tts_text(payload.voiceover)
            updated_fields["voiceover"] = new_voiceover
            updated_fields["subtitle"] = new_voiceover
            scene["voiceover"] = new_voiceover
            scene["subtitle"] = new_voiceover

        if payload.speaker is not None:
            updated_fields["speaker"] = payload.speaker
            scene["speaker"] = payload.speaker

        # 2. 重新生成配音
        if payload.rerun_type in ("voice", "all"):
            voiceover = scene.get("voiceover", "")
            speaker = scene.get("speaker", "旁白")
            voice_name = resolve_voice_for_speaker(speaker)
            audio_path = backend_dir / "assets" / task_id / "audio" / f"voice_{render_id}.mp3"
            _, duration = voice_window(scene)

            audio_duration = await tts_service.generate_voice(
                text=voiceover,
                output_path=str(audio_path),
                voice=voice_name,
            )
            if duration < audio_duration <= duration * MAX_VOICE_SPEED:
                rate_pct = min(25, max(5, int(round((audio_duration / duration - 1.0) * 100))))
                audio_duration = await tts_service.generate_voice(
                    text=voiceover,
                    output_path=str(audio_path),
                    voice=voice_name,
                    rate=f"+{rate_pct}%",
                )

            voice_speed(scene_id, audio_duration, duration)
            relative_audio = f"backend/assets/{task_id}/audio/voice_{render_id}.mp3"
            updated_fields["audio_path"] = relative_audio
            updated_fields["audio_duration"] = audio_duration
            updated_fields["audio_text"] = voiceover
            updated_fields["speaker"] = speaker
            updated_fields["voice"] = voice_name
            scene["audio_path"] = relative_audio
            scene["audio_duration"] = audio_duration

            asset_id = f"asset_{task_id}_voice_{scene_id}"
            asset = Asset(
                asset_id=asset_id,
                task_id=task_id,
                scene_id=scene_id,
                asset_type="audio",
                path=relative_audio,
                source="edge-tts",
                status="success",
                created_at=now,
                updated_at=now,
            )
            await mongodb.replace_one("assets", {"_id": asset_id}, with_document_id(asset.model_dump(), asset_id), upsert=True)

        # 3. 重新生成图片底图
        if payload.rerun_type in ("image", "all"):
            from services.provider_factory import get_image_service
            image_prompt = payload.prompt or scene.get("image_prompt")
            if image_prompt:
                image_service = get_image_service()
                img_res = await anyio.to_thread.run_sync(
                    lambda: image_service.generate_image(
                        task_id=task_id,
                        scene_id=render_id,
                        prompt=image_prompt,
                        ratio=task.get("ratio", "9:16"),
                    )
                )
                if img_res.get("status") == "success" and img_res.get("asset_path"):
                    updated_fields["image_path"] = img_res["asset_path"]
                    scene["image_path"] = img_res["asset_path"]
                    await save_asset("image", img_res["asset_path"], img_res.get("source") or "image_provider")
                else:
                    raise ValueError("Image rerun did not return an asset")
            else:
                raise ValueError("Image prompt is missing")

        if payload.rerun_type in ("video", "all"):
            previous_tail = (prior or {}).get("last_frame_path") or (prior or {}).get("image_path")
            first = continuity_input(scene, previous_tail, scene.get("image_path"))
            if not first or not resolve_media(first).is_file():
                raise ValueError("The source frame is missing")
            if prior and prior.get("stale_tail"):
                raise ValueError("Rerun the previous clip first")
            first_digest = sha256(resolve_media(first).read_bytes()).hexdigest()
            prompt_scene = {**scene, "video_prompt": payload.prompt or scene.get("video_prompt")}
            if payload.prompt:
                prompt_scene.pop("observed_handoff", None)
            prompt, observed = await prepare_motion_prompt(prompt_scene, prior, first, first_digest)
            provider = video_service_for_task(task)
            duration = int(scene["duration"])
            prompt_options = video_prompt_options(provider, scene)
            pending_key = render_signature(provider, first_digest, prompt, duration, task.get("ratio", "9:16"), **prompt_options)
            pending_job = scene.get("pending_video_rerun") or {}
            if pending_job.get("key") == pending_key:
                render_id = pending_job["render_id"]
            else:
                await mongodb.update_one("scenes", {"_id": scene["_id"]}, {
                    "pending_video_rerun": {"key": pending_key, "render_id": render_id},
                    "observed_handoff": observed,
                })
            result = await anyio.to_thread.run_sync(
                lambda: provider.generate_video(task_id=task_id, scene_id=render_id,
                                                image_path=first, video_prompt=prompt,
                                                duration=duration, ratio=task.get("ratio", "9:16"), **prompt_options)
            )
            if result.get("status") != "success" or not result.get("asset_path"):
                raise ValueError("Video rerun did not return an asset")
            video_path = result["asset_path"]
            media = await anyio.to_thread.run_sync(inspect_video, resolve_media(video_path))
            if media["duration"] < duration - 0.5:
                raise ValueError("Replacement video is too short")
            tail = f"backend/assets/{task_id}/frames/{render_id}_tail.png"
            await anyio.to_thread.run_sync(extract_tail, video_path, resolve_media(tail), duration)
            await save_asset("video_clip", video_path, result.get("source") or "video_provider",
                             submitted_video_prompt=prompt,
                             first_frame_digest=first_digest, render_signature=pending_key, provider_task_id=result.get("provider_task_id"))
            updated_fields.update(video_path=video_path, last_frame_path=tail, continuity_input_path=first,
                                  submitted_video_prompt=prompt,
                                  observed_handoff=observed, need_dynamic_video=True, stale_tail=False,
                                  status="video_done", pending_video_rerun=None)
            if scene.get("transition_type") == "continue_action":
                actual_first = f"backend/assets/{task_id}/frames/{render_id}_first.png"
                await anyio.to_thread.run_sync(extract_tail, video_path, resolve_media(actual_first), 0)
                updated_fields["actual_first_frame_path"] = actual_first
                updated_fields["join_review"] = await anyio.to_thread.run_sync(compare_join_frames, first, actual_first)
            successor = await mongodb.find_one("scenes", {
                "task_id": task_id, "scene_index": scene["scene_index"] + 1,
                "transition_type": "continue_action",
            })
            if successor:
                await mongodb.update_one("scenes", {"_id": successor["_id"]}, {"stale_tail": True})

        updated_fields["updated_at"] = now
        await mongodb.update_one("scenes", {"_id": f"{task_id}_{scene_id}"}, updated_fields)
        if task.get("status") == "failed" and payload.rerun_type == "voice":
            return ApiResponse(status="success", data={"message": "配音已保存，可以继续原任务"})

        pending = await mongodb.find_many("scenes", {"task_id": task_id, "stale_tail": True}, limit=100)
        if pending:
            return ApiResponse(status="success", data={
                "message": "素材已更新，请重拍标记为待续接的片段后再合成",
                "pending_scenes": [item["scene_id"] for item in pending],
            })

        # 5. 触发更新字幕与重新合成视频
        await SubtitleAgent().run(task_id)
        await EditorAgent().run(task_id)
        quality_res = await QualityAgent().run(task_id)
        await ExportAgent().run(task_id)

        # 清除 _id 避免返回报错
        scene_data = {**scene, **updated_fields}
        if "_id" in scene_data:
            scene_data["_id"] = str(scene_data["_id"])

        return ApiResponse(
            status="success",
            data={
                "message": f"Scene {scene_id} re-generated successfully",
                "scene": scene_data,
                "quality": quality_res,
            },
        )
    except Exception as exc:
        return error_response(f"Scene rerun failed: {str(exc)}", 500)
