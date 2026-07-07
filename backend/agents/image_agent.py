from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import anyio

from agents.agent_utils import (
    ASSETS_COLLECTION,
    SCENES_COLLECTION,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    with_document_id,
)
from config import settings
from core.database import mongodb
from models.asset_model import Asset
from models.task_model import VideoTaskStatus
from services.provider_factory import get_image_service


class ImageAgent:
    agent_name = "ImageAgent"

    async def run(self, task_id: str) -> list[dict[str, object]]:
        try:
            await mark_agent_start(task_id, self.agent_name, progress=75)

            scenes = await mongodb.find_many(SCENES_COLLECTION, limit=100)
            task_scenes = [scene for scene in scenes if scene.get("task_id") == task_id]
            task_scenes.sort(key=lambda scene: int(scene.get("scene_index", 0)))
            if not task_scenes:
                raise ValueError(f"Scenes not found for task: {task_id}")

            task_scenes = task_scenes[: settings.max_images_per_task]
            image_service = get_image_service()

            assets: list[dict[str, object]] = []
            now = datetime.now(UTC)
            backend_dir = Path(__file__).resolve().parents[1]

            for scene in task_scenes:
                scene_id = str(scene["scene_id"])
                
                # 检查该分镜图片文件是否已经生成在本地
                existing_path = scene.get("image_path")
                if existing_path:
                    clean_path = str(existing_path).replace("backend/", "")
                    abs_path = backend_dir / clean_path
                    if abs_path.exists():
                        # 文件存在，寻找已保存的 Asset 记录
                        existing_asset = await mongodb.find_one(
                            ASSETS_COLLECTION,
                            {"task_id": task_id, "scene_id": scene_id, "asset_type": "image"}
                        )
                        if existing_asset:
                            assets.append(existing_asset)
                            print(f"[ImageAgent] Image already exists for {scene_id}, skipping generation.")
                            continue

                image_result = await anyio.to_thread.run_sync(
                    lambda: image_service.generate_image(
                        task_id=task_id,
                        scene_id=scene_id,
                        image_prompt=str(scene.get("image_prompt") or ""),
                        negative_prompt=scene.get("negative_prompt")
                        if isinstance(scene.get("negative_prompt"), str)
                        else None,
                    )
                )
                asset = Asset(
                    asset_id=f"asset_{uuid4().hex[:12]}",
                    task_id=task_id,
                    scene_id=scene_id,
                    asset_type="image",
                    path=str(image_result["asset_path"]),
                    source=str(image_result.get("source", "qwen")),
                    status=str(image_result.get("status", "success")),
                    created_at=now,
                    updated_at=now,
                )
                await mongodb.insert_one(ASSETS_COLLECTION, with_document_id(asset.model_dump(), asset.asset_id))
                await mongodb.update_one(
                    SCENES_COLLECTION,
                    {"_id": f"{task_id}_{scene_id}"},
                    {
                        "image_path": asset.path,
                        "status": "image_done",
                        "updated_at": now,
                    },
                )
                assets.append(asset.model_dump())

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=85,
                status=VideoTaskStatus.IMAGE_GENERATING,
                message="Images generated successfully",
                extra_fields={"metadata.image_count": len(assets)},
            )
            return assets
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
