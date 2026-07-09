from __future__ import annotations

from pathlib import Path
from typing import Any

from services.comfyui.client import ComfyUIClient
from services.comfyui.workflow_mapper import apply_mapping, find_output_files
from services.comfyui.workflow_store import WorkflowStore
from services.image.base import BaseImageService


class ComfyUIImageService(BaseImageService):
    def __init__(
        self,
        client: ComfyUIClient | None = None,
        workflow_store: WorkflowStore | None = None,
    ) -> None:
        self.client = client or ComfyUIClient()
        self.workflow_store = workflow_store or WorkflowStore()

    def generate_image(
        self,
        task_id: str,
        scene_id: str,
        image_prompt: str,
        negative_prompt: str | None = None,
        ratio: str = "9:16",
    ) -> dict[str, Any]:
        try:
            data = self.workflow_store.load_workflow("image")
            workflow = apply_mapping(
                data["workflow"],
                data["mapping"],
                {
                    "positive_prompt": image_prompt,
                    "negative_prompt": negative_prompt,
                },
            )
            prompt_id = self.client.queue_prompt(workflow)
            history = self.client.wait_for_history(prompt_id)
            outputs = find_output_files(history, "images")
            if not outputs:
                raise ValueError("ComfyUI image workflow did not produce an image output")

            backend_dir = Path(__file__).resolve().parents[2]
            dest_path = backend_dir / "assets" / task_id / "images" / f"{scene_id}.png"
            self.client.download_output(outputs[0], dest_path)
            relative_asset_path = f"backend/assets/{task_id}/images/{scene_id}.png"

            return {
                "source": "comfyui",
                "provider": "comfyui_image",
                "task_id": task_id,
                "scene_id": scene_id,
                "asset_type": "image",
                "asset_path": relative_asset_path,
                "image_prompt": image_prompt,
                "negative_prompt": negative_prompt,
                "status": "success",
                "message": "Image generated successfully via ComfyUI.",
            }
        except Exception as exc:
            raise RuntimeError(f"ComfyUIImageService failed for {scene_id}: {exc}") from exc
