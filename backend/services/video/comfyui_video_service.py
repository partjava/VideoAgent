from __future__ import annotations

from pathlib import Path
from typing import Any

from services.comfyui.client import ComfyUIClient
from services.comfyui.workflow_mapper import apply_mapping, find_output_files
from services.comfyui.workflow_store import WorkflowStore
from services.video.base import BaseVideoService


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
            workflow = apply_mapping(
                data["workflow"],
                data["mapping"],
                {
                    "positive_prompt": video_prompt,
                    "image_path": image_path,
                    "duration": duration,
                },
            )
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
            self.client.download_output(outputs[0], dest_path)
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
                "status": "success",
                "message": "Video generated successfully via ComfyUI.",
            }
        except Exception as exc:
            raise RuntimeError(f"ComfyUIVideoService failed for {scene_id}: {exc}") from exc
