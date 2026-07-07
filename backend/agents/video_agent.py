from datetime import UTC, datetime
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
from core.database import mongodb
from models.asset_model import Asset
from models.task_model import VideoTaskStatus
from services.provider_factory import get_video_service


def _build_motion_prompt(scene: dict[str, object]) -> str:
    video_prompt = str(scene.get("video_prompt") or scene.get("visual_description") or "")
    motion_beats = scene.get("motion_beats")
    continuity_note = str(scene.get("continuity_note") or scene.get("scene_continuity") or "")
    last_frame = str(scene.get("last_frame_for_transition") or scene.get("transition_note") or "")

    beat_text = ""
    if isinstance(motion_beats, list):
        beat_text = "\n".join(str(beat) for beat in motion_beats)

    return (
        f"{video_prompt}\n"
        f"动作节拍：\n{beat_text}\n"
        f"连续性要求：{continuity_note}\n"
        f"最后一帧衔接：{last_frame}\n"
        "每0.5到1秒必须有一个明确小变化，不能让画面静止超过1秒；不要突然换场景、换主体、换服装或加入无关元素。"
    ).strip()


class VideoAgent:
    agent_name = "VideoAgent"

    async def run(self, task_id: str) -> list[dict[str, object]]:
        try:
            await mark_agent_start(
                task_id,
                self.agent_name,
                progress=88,
                status=VideoTaskStatus.VIDEO_GENERATING,
            )

            scenes = await mongodb.find_many(SCENES_COLLECTION, limit=100)
            task_scenes = [scene for scene in scenes if scene.get("task_id") == task_id]
            task_scenes.sort(key=lambda scene: int(scene.get("scene_index", 0)))
            if not task_scenes:
                raise ValueError(f"Scenes not found for task: {task_id}")

            assets_in_db = await mongodb.find_many(ASSETS_COLLECTION, limit=200)
            image_assets = {
                asset["scene_id"]: asset["path"]
                for asset in assets_in_db
                if asset.get("task_id") == task_id and asset.get("asset_type") == "image"
            }

            video_service = get_video_service()
            assets: list[dict[str, object]] = []
            now = datetime.now(UTC)
            dynamic_scenes = [scene for scene in task_scenes if scene.get("need_dynamic_video") is True]

            from pathlib import Path
            backend_dir = Path(__file__).resolve().parents[1]

            for scene in task_scenes:
                scene_id = str(scene["scene_id"])
                image_path = image_assets.get(scene_id)
                duration = int(scene.get("duration", 5))
                is_selected = scene in dynamic_scenes

                if not is_selected or not image_path:
                    continue

                # 检查该分镜视频文件是否已经生成在本地
                existing_video_path = scene.get("video_path")
                if existing_video_path:
                    clean_path = str(existing_video_path).replace("backend/", "")
                    abs_path = backend_dir / clean_path
                    if abs_path.exists():
                        # 查找对应的 Asset 记录
                        existing_asset = await mongodb.find_one(
                            ASSETS_COLLECTION,
                            {"task_id": task_id, "scene_id": scene_id, "asset_type": "video_clip"}
                        )
                        if existing_asset:
                            assets.append(existing_asset)
                            print(f"[VideoAgent] Video already exists for {scene_id}, skipping generation.")
                            continue

                motion_prompt = _build_motion_prompt(scene)
                try:
                    video_result = await anyio.to_thread.run_sync(
                        lambda _tid=task_id, _sid=scene_id, _ip=image_path, _vp=motion_prompt, _dur=duration: video_service.generate_video(
                            task_id=_tid,
                            scene_id=_sid,
                            image_path=_ip,
                            video_prompt=_vp,
                            duration=_dur,
                        )
                    )
                except Exception as video_err:
                    print(f"[VideoAgent] Video generation failed for {scene_id}: {video_err}. Falling back to static image.")
                    # 视频生成失败时降级为静态图片
                    await mongodb.update_one(
                        SCENES_COLLECTION,
                        {"_id": f"{task_id}_{scene_id}"},
                        {
                            "need_dynamic_video": False,
                            "status": "image_fallback",
                            "updated_at": datetime.now(UTC),
                        },
                    )
                    continue

                if video_result.get("status") == "success" and video_result.get("asset_path"):
                    asset = Asset(
                        asset_id=f"asset_{uuid4().hex[:12]}",
                        task_id=task_id,
                        scene_id=scene_id,
                        asset_type=str(video_result.get("asset_type", "video_clip")),
                        path=str(video_result["asset_path"]),
                        source=str(video_result.get("source", "vidu")),
                        status="success",
                        created_at=now,
                        updated_at=now,
                    )
                    await mongodb.insert_one(
                        ASSETS_COLLECTION,
                        with_document_id(asset.model_dump(), asset.asset_id),
                    )
                    await mongodb.update_one(
                        SCENES_COLLECTION,
                        {"_id": f"{task_id}_{scene_id}"},
                        {
                            "video_path": asset.path,
                            "status": "video_done",
                            "updated_at": now,
                        },
                    )
                    assets.append(asset.model_dump())

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=92,
                status=VideoTaskStatus.VIDEO_GENERATING,
                message="Video clips generated successfully",
                extra_fields={"metadata.dynamic_video_count": len(assets)},
            )
            return assets
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
