import asyncio
from datetime import UTC, datetime
from pathlib import Path
from services.visual_style import lock_visual_style, PHOTO_EXCLUSIONS
from services.character_binding import frozen_characters, reference_choices_for_scene
from services.generation_dispatch import dispatch_generation
from services.generation_usage import GenerationLimitExceeded, record_reuse
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

            from services.generation_contract import needs_video
            task_scenes = [scene for scene in task_scenes
                           if not (needs_video(scene) and scene.get("transition_type") == "continue_action")]
            if len(task_scenes) > settings.max_images_per_task:
                raise ValueError("Storyboard exceeds the configured image limit")
            image_service = get_image_service()
            # 获取任务比例，后续传给生图服务
            task = await mongodb.find_one("video_tasks", {"_id": task_id})
            if frozen_characters(task or {}) and not getattr(image_service, "supports_reference_images", False):
                raise ValueError("连载角色需要支持参考图的图片模型，请先在模型配置中更换型号")
            task_ratio = str(task.get("ratio", "9:16")) if task else "9:16"

            assets: list[dict[str, object]] = []
            now = datetime.now(UTC)
            backend_dir = Path(__file__).resolve().parents[1]

            # ── 收集需要生图的分镜（跳过已有图片的） ──────────────
            scenes_to_generate = []
            for scene in task_scenes:
                scene_id = str(scene["scene_id"])

                disk_img = backend_dir / "assets" / task_id / "images" / f"{scene_id}.png"
                existing_path = scene.get("image_path")
                has_valid_img = (existing_path and (backend_dir / str(existing_path).replace("backend/", "")).is_file()) or (disk_img.is_file() and disk_img.stat().st_size > 1024)

                if has_valid_img:
                    rel_path = f"backend/assets/{task_id}/images/{scene_id}.png"
                    if scene.get("status") == "static_fallback":
                        continue
                    existing_asset = await mongodb.find_one(
                        ASSETS_COLLECTION,
                        {"task_id": task_id, "scene_id": scene_id, "asset_type": "image"}
                    )
                    if not existing_asset:
                        asset = Asset(
                            asset_id=f"asset_{uuid4().hex[:12]}",
                            task_id=task_id,
                            scene_id=scene_id,
                            asset_type="image",
                            path=rel_path,
                            source=str(image_service.__class__.__name__),
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
                        existing_asset = asset.model_dump()
                    choices = reference_choices_for_scene(task or {}, scene)
                    await mongodb.update_one(
                        SCENES_COLLECTION,
                        {"_id": f"{task_id}_{scene_id}"},
                        {"image_path": rel_path, "reference_choices": choices, "status": "image_done", "updated_at": now},
                    )
                    assets.append(existing_asset)
                    await record_reuse(task or {"task_id": task_id}, "image", scene_id)
                    print(f"[ImageAgent] Image already exists for {scene_id}, reused existing file.")
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

            # ── 并行生成所有图片（无角色库但支持参考图时，优先画第1镜作为主角外貌与片场光色锚点） ──
            print(f"[ImageAgent] Generating {len(scenes_to_generate)} images...", flush=True)
            dynamic_anchor_path: str | None = None
            has_frozen_refs = bool(frozen_characters(task or {}))
            can_anchor = (
                not has_frozen_refs
                and getattr(image_service, "supports_reference_images", False)
                and int(getattr(image_service, "max_reference_images", 0) or 0) >= 1
            )
            if can_anchor and assets:
                # 若第1镜或前序镜头已在硬盘就绪，直接复用其作为首镜视觉锚点
                for existing in assets:
                    cand = str(existing.get("path") or "").strip()
                    if cand:
                        dynamic_anchor_path = cand
                        break

            async def _generate_one(scene: dict, anchor_path: str | None = None) -> dict | None:
                scene_id = str(scene["scene_id"])
                image_prompt = lock_visual_style(str(scene.get("image_prompt") or ""), (task or {}).get("style"))
                negative_prompt = (
                    scene.get("image_negative_prompt") or scene.get("negative_prompt")
                    if isinstance(scene.get("image_negative_prompt") or scene.get("negative_prompt"), str)
                    else None
                )
                if (task or {}).get("style") == "短剧":
                    negative_prompt = PHOTO_EXCLUSIONS + "，" + (negative_prompt or "")
                choices = reference_choices_for_scene(task or {}, scene)
                ref_paths = [item["path"] for item in choices]
                env_anchor = str(scene.get("scene_anchor_path") or "").strip()
                max_refs = int(getattr(image_service, "max_reference_images", 3) or 0)
                if env_anchor and len(ref_paths) < max_refs:
                    from services.media_quality import resolve_media
                    if resolve_media(env_anchor).is_file() and env_anchor not in ref_paths:
                        ref_paths.append(env_anchor)
                        image_prompt += "\n[环境参考锚定] 仅继承最后一张场景图的空间装修、材质与主光源方向，严格按本镜机位构图重排。"
                elif not ref_paths and anchor_path and max_refs >= 1:
                    from services.media_quality import resolve_media
                    if resolve_media(anchor_path).is_file():
                        ref_paths = [anchor_path]
                        image_prompt += "\n[同片场造型与环境锚定] 严格保持参考图1中人物五官、发型、服装款式与房间装修色调一致；仅继承人物造型与片场光色，人物姿态、走位距离、表情与机位景别必须完全按本镜新分镜重新构图。"
                reference_options = {"reference_images": ref_paths} if ref_paths else {}
                try:
                    result = await dispatch_generation(task or {"task_id": task_id}, "image", scene_id,
                        lambda _sid=scene_id, _ip=image_prompt, _np=negative_prompt, _ratio=task_ratio, _ro=reference_options:
                            image_service.generate_image(
                                task_id=task_id,
                                scene_id=_sid,
                                image_prompt=_ip,
                                negative_prompt=_np,
                                ratio=_ratio,
                                **_ro,
                            )
                    )
                    if isinstance(result, dict):
                        result["reference_choices"] = choices
                    print(f"[ImageAgent] Scene {scene_id} image generated successfully.")
                    return result
                except GenerationLimitExceeded:
                    raise
                except Exception as e:
                    print(f"[ImageAgent] Scene {scene_id} image generation failed: {e}")
                    return None

            sem = anyio.Semaphore(4)  # 限制最多 4 个并发，避免打满线程池

            async def _generate_and_save(scene: dict, anchor_path: str | None = None) -> str | None:
                scene_id = str(scene["scene_id"])
                async with sem:
                    image_result = await _generate_one(scene, anchor_path=anchor_path)
                if not image_result or image_result.get("status") != "success" or not image_result.get("asset_path"):
                    return scene_id

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
                await mongodb.replace_one(
                    ASSETS_COLLECTION,
                    {"_id": asset.asset_id},
                    with_document_id(asset.model_dump(), asset.asset_id),
                    upsert=True,
                )
                await mongodb.update_one(
                    SCENES_COLLECTION,
                    {"_id": f"{task_id}_{scene_id}"},
                    {
                        "image_path": asset.path,
                        "reference_choices": image_result.get("reference_choices") or [],
                        "status": "image_done",
                        "updated_at": now,
                    },
                )
                assets.append(asset.model_dump())

                return None

            remaining_scenes = list(scenes_to_generate)
            if can_anchor and not dynamic_anchor_path and len(remaining_scenes) > 1:
                anchor_idx = 0
                for idx, candidate in enumerate(remaining_scenes):
                    shot_label = str(candidate.get("shot_type") or "").strip()
                    has_person_cues = bool(
                        candidate.get("character_ids")
                        or candidate.get("dialogue_lines")
                        or candidate.get("reaction_focus")
                        or candidate.get("character_state")
                    )
                    if has_person_cues and "空镜" not in shot_label:
                        anchor_idx = idx
                        break
                first_scene = remaining_scenes.pop(anchor_idx)
                first_err = await _generate_and_save(first_scene)
                if first_err is not None:
                    raise RuntimeError(f"Missing generated images: {first_err}")
                if assets:
                    dynamic_anchor_path = str(assets[-1].get("path") or "").strip() or None

            results = await asyncio.gather(
                *(_generate_and_save(scene, anchor_path=dynamic_anchor_path) for scene in remaining_scenes),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            failed = [result for result in results if result is not None]
            if failed:
                raise RuntimeError(f"Missing generated images: {', '.join(failed)}")

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
