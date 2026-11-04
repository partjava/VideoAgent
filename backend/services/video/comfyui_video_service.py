from __future__ import annotations

from pathlib import Path
from typing import Any

from services.comfyui.client import ComfyUIClient
from services.comfyui.workflow_mapper import apply_mapping, find_output_files
from services.comfyui.workflow_store import WorkflowStore
from services.video.base import BaseVideoService


def prepare_video_workflow(data, client, image_path, prompt, duration, ratio):
    import math
    mapping, workflow = data["mapping"], data["workflow"]
    required = {"positive_prompt", "image_path", "width", "height", "length", "fps"}
    if not required.issubset(mapping):
        raise ValueError(f"Missing required video mappings: {sorted(required - set(mapping))}")
    if not 4 <= duration <= 12 or ratio not in {"9:16", "16:9"}:
        raise ValueError("Unsupported video duration or aspect ratio")
    # Validate mappings before any upload or queue operation.
    fps_mapping = mapping["fps"]
    fps_node = workflow.get(str(fps_mapping["node"]), {})
    fps = fps_node
    for key in fps_mapping["path"].split("."):
        fps = fps.get(key, {}) if isinstance(fps, dict) else None
    if not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise ValueError("Workflow fps must be positive")
    # Wan temporal VAE uses 4n+1 frames; retain at least requested duration.
    length = math.ceil((duration * fps - 1) / 4) * 4 + 1
    width, height = (704, 1280) if ratio == "9:16" else (1280, 704)
    values = {"positive_prompt": prompt, "image_path": "validation.png", "width": width,
              "height": height, "length": length, "fps": fps}
    apply_mapping(workflow, mapping, values)
    values["image_path"] = client.upload_image(image_path)
    return apply_mapping(workflow, mapping, values)


class ComfyUIVideoService(BaseVideoService):
    def __init__(
        self,
        client: ComfyUIClient | None = None,
        workflow_store: WorkflowStore | None = None,
    ) -> None:
        self.client = client or ComfyUIClient()
        self.workflow_store = workflow_store or WorkflowStore()

    def generate_video(
        self,
        task_id: str,
        scene_id: str,
        image_path: str,
        video_prompt: str | None = None,
        duration: int = 5,
        ratio: str = "9:16",
    ) -> dict[str, Any]:
        try:
            data = self.workflow_store.load_workflow("video")
            from services.media_quality import resolve_media, inspect_video
            workflow = prepare_video_workflow(data, self.client, resolve_media(image_path), video_prompt,
                                              duration, ratio)
            prompt_id = self.client.queue_prompt(workflow)
            history = self.client.wait_for_history(prompt_id)
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
                raise ValueError("ComfyUI video workflow did not produce a video output")

            backend_dir = Path(__file__).resolve().parents[2]
            dest_path = backend_dir / "assets" / task_id / "videos" / f"{scene_id}.mp4"
            candidates = [o for o in outputs if Path(o.get("filename", "")).suffix.lower() == ".mp4"]
            if not candidates:
                raise ValueError("Workflow must return an MP4 video")
            self.client.download_output(candidates[0], dest_path)
            media = inspect_video(dest_path)
            relative_video_path = f"backend/assets/{task_id}/videos/{scene_id}.mp4"

            return {
                "source": "comfyui",
                "provider": "comfyui_video",
                "task_id": task_id,
                "scene_id": scene_id,
                "asset_type": "video_clip",
                "asset_path": relative_video_path,
                "image_path": image_path,
                "video_prompt": video_prompt,
                "duration": duration,
                "actual_duration": media["duration"],
                "status": "success",
                "message": "Video generated successfully via ComfyUI.",
            }
        except Exception as exc:
            raise RuntimeError(f"ComfyUIVideoService failed for {scene_id}: {exc}") from exc
