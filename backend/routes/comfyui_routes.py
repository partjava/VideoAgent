from __future__ import annotations

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from pathlib import Path

from models.response_model import ApiResponse
from services.comfyui.client import get_progress
from services.comfyui.config_store import build_comfyui_config, save_comfyui_config
from services.comfyui.test_history import record_test_success, load_history
from services.comfyui.workflow_mapper import apply_mapping, find_output_files
from services.comfyui.workflow_store import WorkflowStore
from services.image.comfyui_image_service import ComfyUIImageService
from services.video.comfyui_video_service import ComfyUIVideoService
from services.provider_factory import get_llm_service


# 临时存储测试 prompt 文本，供结果接口存历史用
_TEST_META: dict[str, dict[str, str]] = {}


router = APIRouter(prefix="/api/comfyui", tags=["comfyui"])


def success(data: dict) -> JSONResponse:
    return JSONResponse(content={"status": "success", "data": data})


def error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"status": "error", "message": message})


@router.get("/config", response_model=ApiResponse)
async def get_comfyui_config() -> JSONResponse:
    return success(build_comfyui_config())


@router.post("/config", response_model=ApiResponse)
async def update_comfyui_config(payload: dict) -> JSONResponse:
    try:
        config = save_comfyui_config(payload)
    except ValueError as exc:
        return error(str(exc))
    return success({"config": config, "env_updated": True})


@router.post("/check", response_model=ApiResponse)
async def check_comfyui_connection(payload: dict | None = None) -> JSONResponse:
    try:
        config = build_comfyui_config()
        base_url = str(payload.get("comfyui_base_url") if payload else config["comfyui_base_url"]).rstrip("/")
        timeout = int(payload.get("comfyui_timeout_seconds") if payload and payload.get("comfyui_timeout_seconds") else 15)
        async with httpx.AsyncClient(timeout=min(timeout, 30)) as client:
            response = await client.get(f"{base_url}/system_stats")
            response.raise_for_status()
        return success({"ok": True, "base_url": base_url, "message": "ComfyUI connection is available."})
    except Exception as exc:
        return success({"ok": False, "message": f"ComfyUI connection failed: {str(exc)[:160]}"})


@router.get("/workflows", response_model=ApiResponse)
async def get_comfyui_workflows() -> JSONResponse:
    return success(WorkflowStore().load_all())


@router.post("/workflows/image", response_model=ApiResponse)
async def save_image_workflow(payload: dict) -> JSONResponse:
    try:
        result = WorkflowStore().save_workflow(
            "image",
            payload.get("workflow"),
            payload.get("mapping") or {},
        )
    except Exception as exc:
        return error(str(exc))
    return success(result)


@router.post("/workflows/video", response_model=ApiResponse)
async def save_video_workflow(payload: dict) -> JSONResponse:
    try:
        result = WorkflowStore().save_workflow(
            "video",
            payload.get("workflow"),
            payload.get("mapping") or {},
        )
    except Exception as exc:
        return error(str(exc))
    return success(result)


@router.post("/test-image", response_model=ApiResponse)
async def test_comfyui_image(payload: dict) -> JSONResponse:
    try:
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return error("Prompt is required")
        store = WorkflowStore()
        data = store.load_workflow("image")
        workflow = apply_mapping(
            data["workflow"], data["mapping"],
            {"positive_prompt": prompt, "negative_prompt": payload.get("negative_prompt")},
        )
        from services.comfyui.client import ComfyUIClient
        prompt_id = ComfyUIClient().queue_prompt(workflow)
        return success({"prompt_id": prompt_id, "status": "queued"})
    except Exception as exc:
        return error(str(exc), 500)


@router.get("/test-image/{prompt_id}", response_model=ApiResponse)
async def get_test_image_result(prompt_id: str) -> JSONResponse:
    try:
        from services.comfyui.client import ComfyUIClient
        client = ComfyUIClient()
        history = client.wait_for_history(prompt_id)
        outputs = find_output_files(history, "images")
        if not outputs:
            return error("ComfyUI did not produce an image output", 500)

        backend_dir = Path(__file__).resolve().parent.parent
        dest_path = backend_dir / "assets" / "comfyui_test" / "images" / "test_image.png"
        client.download_output(outputs[0], dest_path)
        return success({
            "asset_path": f"backend/assets/comfyui_test/images/test_image.png",
            "status": "success",
        })
    except Exception as exc:
        return error(str(exc), 500)


@router.post("/test-video", response_model=ApiResponse)
async def test_comfyui_video(payload: dict) -> JSONResponse:
    try:
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return error("prompt is required")
        store = WorkflowStore()
        data = store.load_workflow("video")
        workflow = apply_mapping(
            data["workflow"], data["mapping"],
            {"positive_prompt": prompt},
        )
        from services.comfyui.client import ComfyUIClient
        prompt_id = ComfyUIClient().queue_prompt(workflow)
        _TEST_META[prompt_id] = {"prompt": prompt, "story": str(payload.get("story_id") or "")}
        return success({"prompt_id": prompt_id, "status": "queued"})
    except Exception as exc:
        return error(str(exc), 500)


@router.get("/test-video/{prompt_id}", response_model=ApiResponse)
async def get_test_video_result(prompt_id: str) -> JSONResponse:
    try:
        from services.comfyui.client import ComfyUIClient
        client = ComfyUIClient()
        print(f"[ComfyUI] Waiting for history: {prompt_id}")
        history = client.wait_for_history(prompt_id)
        print(f"[ComfyUI] Got history keys: {list(history.keys())}")
        # SaveVideo 节点输出 key 是 images 或 animated（不是 videos）
        outputs = find_output_files(history, "videos")
        if not outputs:
            outputs = find_output_files(history, "gifs")
        if not outputs:
            outputs = find_output_files(history, "video")
        if not outputs:
            outputs = find_output_files(history, "images")
        if not outputs:
            outputs = find_output_files(history, "animated")
        if not outputs:
            for pid, entry in history.items():
                for nid, nout in (entry.get("outputs") or {}).items():
                    print(f"[ComfyUI] Node {nid} output keys: {list(nout.keys())}")
            return error("ComfyUI did not produce a video output", 500)

        backend_dir = Path(__file__).resolve().parent.parent
        dest_path = backend_dir / "assets" / "comfyui_test" / "videos" / "test_video.mp4"
        client.download_output(outputs[0], dest_path)
        print(f"[ComfyUI] Video saved to {dest_path}")
        meta = _TEST_META.pop(prompt_id, {})
        record_test_success(
            test_type="video",
            prompt=meta.get("prompt", ""),
            story=meta.get("story"),
            asset_path=f"backend/assets/comfyui_test/videos/test_video.mp4",
        )
        return success({
            "asset_path": f"backend/assets/comfyui_test/videos/test_video.mp4",
            "status": "success",
        })
    except Exception as exc:
        print(f"[ComfyUI] get_test_video_result error: {exc}")
        import traceback
        traceback.print_exc()
        return error(str(exc), 500)


@router.get("/history", response_model=ApiResponse)
async def get_comfyui_test_history() -> JSONResponse:
    return success({"tests": load_history()})


@router.get("/progress/{prompt_id}", response_model=ApiResponse)
async def get_comfyui_progress(prompt_id: str) -> JSONResponse:
    progress = get_progress(prompt_id)
    if progress is None:
        return success({"prompt_id": prompt_id, "stage": "unknown", "value": 0, "max": 0, "done": False, "error": None})
    return success({"prompt_id": prompt_id, **progress})


@router.get("/stories", response_model=ApiResponse)
async def get_story_templates() -> JSONResponse:
    """返回预设故事模板列表"""
    from services.llm.deepseek_service import DeepSeekService
    stories = []
    for sid, tmpl in DeepSeekService.STORY_TEMPLATES.items():
        stories.append({
            "id": sid,
            "title": tmpl["title"],
            "tag": tmpl["tag"],
            "scene": tmpl["scene"],
        })
    return success({"stories": stories})


@router.post("/generate-prompt", response_model=ApiResponse)
async def generate_comfyui_prompt(payload: dict) -> JSONResponse:
    """一句话描述或预选故事 → DeepSeek 生成 positive_prompt + negative_prompt"""
    try:
        llm = get_llm_service()
        story_id = str(payload.get("story_id") or "").strip()
        if story_id:
            result = llm.generate_story_prompt(story_id)
            return success(result)

        description = str(payload.get("description") or "").strip()
        if not description:
            return error("description or story_id is required")
        result = llm.generate_single_prompt(description)
        return success(result)
    except Exception as exc:
        return error(str(exc), 500)
