import asyncio
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

            scenes = await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=100)
            task_scenes = [scene for scene in scenes]
            task_scenes.sort(key=lambda scene: int(scene.get("scene_index", 0)))
            if not task_scenes:
                raise ValueError(f"Scenes not found for task: {task_id}")

            task_scenes = task_scenes[: settings.max_images_per_task]
            image_service = get_image_service()
            # 获取任务比例，后续传给生图服务
            task = await mongodb.find_one("video_tasks", {"_id": task_id})
            task_ratio = str(task.get("ratio", "9:16")) if task else "9:16"

            assets: list[dict[str, object]] = []
            now = datetime.now(UTC)
            backend_dir = Path(__file__).resolve().parents[1]

            # ── 收集需要生图的分镜（跳过已有图片的） ──────────────
            scenes_to_generate = []
            for scene in task_scenes:
                scene_id = str(scene["scene_id"])

                existing_path = scene.get("image_path")
                if existing_path:
                    clean_path = str(existing_path).replace("backend/", "")
                    abs_path = backend_dir / clean_path
                    if abs_path.exists():
                        existing_asset = await mongodb.find_one(
                            ASSETS_COLLECTION,
                            {"task_id": task_id, "scene_id": scene_id, "asset_type": "image"}
                        )
                        if existing_asset:
                            assets.append(existing_asset)
                            print(f"[ImageAgent] Image already exists for {scene_id}, skipping generation.")
                            continue

                scenes_to_generate.append(scene)

            if not scenes_to_generate:
                await mark_agent_success(
                    task_id,
                    self.agent_name,
                    progress=85,
                    status=VideoTaskStatus.IMAGE_GENERATING,
                    message="All images already exist, skipping generation",
                    extra_fields={"metadata.image_count": len(assets)},
                )
                return assets

            # ── 并行生成所有图片 ──────────────────────────────────
            print(f"[ImageAgent] Generating {len(scenes_to_generate)} images in parallel...")

            async def _generate_one(scene: dict) -> dict | None:
                scene_id = str(scene["scene_id"])
                image_prompt = str(scene.get("image_prompt") or "")
                negative_prompt = (
                    scene.get("negative_prompt")
                    if isinstance(scene.get("negative_prompt"), str)
                    else None
                )
                try:
                    result = await anyio.to_thread.run_sync(
                        lambda _sid=scene_id, _ip=image_prompt, _np=negative_prompt, _ratio=task_ratio:
                            image_service.generate_image(
                                task_id=task_id,
                                scene_id=_sid,
                                image_prompt=_ip,
                                negative_prompt=_np,
                                ratio=_ratio,
                            )
                    )
                    print(f"[ImageAgent] Scene {scene_id} image generated successfully.")
                    return result
                except Exception as e:
                    print(f"[ImageAgent] Scene {scene_id} image generation failed: {e}")
                    return None

            sem = anyio.Semaphore(4)  # 限制最多 4 个并发，避免打满线程池

            async def _bounded_generate(scene: dict) -> dict | None:
                async with sem:
                    return await _generate_one(scene)

            results = await asyncio.gather(
                *[_bounded_generate(s) for s in scenes_to_generate],
                return_exceptions=False,
            )

            # ── 保存生成结果 ──────────────────────────────────────
            success_count = 0
            for scene, image_result in zip(scenes_to_generate, results):
                scene_id = str(scene["scene_id"])
                if image_result is None:
                    print(f"[ImageAgent] Skipping asset save for {scene_id} due to generation failure.")
                    continue

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
                success_count += 1

            if success_count == 0:
                raise RuntimeError("All image generation tasks failed.")

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
