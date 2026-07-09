import asyncio
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
from services.provider_factory import get_llm_service, get_video_service


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

            scenes = await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=100)
            task_scenes = [scene for scene in scenes]
            task_scenes.sort(key=lambda scene: int(scene.get("scene_index", 0)))
            if not task_scenes:
                raise ValueError(f"Scenes not found for task: {task_id}")

            assets_in_db = await mongodb.find_many(ASSETS_COLLECTION, {"task_id": task_id}, limit=200)
            image_assets = {

            video_service = get_video_service()
            assets: list[dict[str, object]] = []
            now = datetime.now(UTC)
            dynamic_scenes = [scene for scene in task_scenes if scene.get("need_dynamic_video") is True]

            from pathlib import Path
            backend_dir = Path(__file__).resolve().parents[1]
            # 获取任务比例
            task_doc = await mongodb.find_one("video_tasks", {"_id": task_id})
            task_ratio = str(task_doc.get("ratio", "9:16")) if task_doc else "9:16"

            # ── 所有分镜至少 4 秒（Seedance 最低要求） ──────────
            for scene in task_scenes:
                scene_id = str(scene["scene_id"])
                curr_dur = int(scene.get("duration", 5))
                if curr_dur < 4:
                    print(f"[VideoAgent] Bumping scene {scene_id} duration {curr_dur}s → 4s")
                    await mongodb.update_one(
                        SCENES_COLLECTION,
                        {"_id": f"{task_id}_{scene_id}"},
                        {"duration": 4, "updated_at": now},
                    )
                    scene["duration"] = 4

            # ── 收集需要生成视频的分镜（跳过已有的） ──────────────
            scenes_to_generate = []
            for scene in task_scenes:
                scene_id = str(scene["scene_id"])
                image_path = image_assets.get(scene_id)
                duration = int(scene.get("duration", 5))

                # 没有图片的分镜跳过
                if not image_path:
                    continue

                # 检测是否应生成视频：
                #   1) need_dynamic_video=True（正常动态分镜）
                #   2) 有 video_prompt（被 PromptAgent 处理过）
                #   3) 之前被拒的（状态是 image_fallback，且没有 video_path 视频文件）
                has_image = bool(image_path)
                mark_dynamic = scene.get("need_dynamic_video") is True
                has_video_prompt = bool(scene.get("video_prompt"))
                scene_video_path = scene.get("video_path")
                video_file_exists = False
                if scene_video_path:
                    clean_path = str(scene_video_path).replace("backend/", "")
                    video_file_exists = (backend_dir / clean_path).exists()

                # 不符合任何视频生成条件的跳过
                if not (mark_dynamic or has_video_prompt):
                    if not (scene.get("status") in ("image_fallback",) and has_image and not video_file_exists):
                        continue

                # 已有视频文件 → 跳过
                if video_file_exists:
                    existing_asset = await mongodb.find_one(
                        ASSETS_COLLECTION,
                        {"task_id": task_id, "scene_id": scene_id, "asset_type": "video_clip"}
                    )
                    if existing_asset:
                        assets.append(existing_asset)
                        print(f"[VideoAgent] Video already exists for {scene_id}, skipping generation.")
                        continue

                # 重置为动态状态，准备生成/重试
                print(f"[VideoAgent] {'Retrying' if scene.get('status') == 'image_fallback' else 'Generating'} video for scene {scene_id}...")
                await mongodb.update_one(
                    SCENES_COLLECTION,
                    {"_id": f"{task_id}_{scene_id}"},
                    {"need_dynamic_video": True, "status": "retrying_video", "updated_at": now},
                )
                scene["need_dynamic_video"] = True

                motion_prompt = _build_motion_prompt(scene)
                scenes_to_generate.append((scene, scene_id, image_path, motion_prompt, duration))

            if not scenes_to_generate:
                await mark_agent_success(
                    task_id,
                    self.agent_name,
                    progress=92,
                    status=VideoTaskStatus.VIDEO_GENERATING,
                    message="All videos already exist, skipping generation",
                    extra_fields={"metadata.dynamic_video_count": len(assets)},
                )
                return assets

            # ── 并行生成所有视频 ──────────────────────────────────
            print(f"[VideoAgent] Generating {len(scenes_to_generate)} videos in parallel...")

            async def _try_generate(prompt: str, scene_id: str, image_path: str, duration: int) -> dict | None:
                """发起一次视频生成调用"""
                try:
                    result = await anyio.to_thread.run_sync(
                        lambda _sid=scene_id, _ip=image_path, _vp=prompt, _dur=duration, _ratio=task_ratio:
                            video_service.generate_video(
                                task_id=task_id,
                                scene_id=_sid,
                                image_path=_ip,
                                video_prompt=_vp,
                                duration=_dur,
                                ratio=_ratio,
                            )
                    )
                    if result.get("status") == "success" and result.get("asset_path"):
                        return result
                    return None
                except Exception as e:
                    err_msg = str(e)
                    # 内容审核拦截，抛出来让上层处理重试
                    if "sensitive information" in err_msg.lower():
                        raise RuntimeError(f"sensitive:{err_msg}")
                    # 其他错误直接失败
                    print(f"[VideoAgent] Scene {scene_id} non-sensitive error: {e}")
                    return None

            async def _rewrite_prompt(original_prompt: str) -> str:
                """调用 DeepSeek 将提示词改写为更温和的版本"""
                try:
                    llm = get_llm_service()
                    new_prompt = await anyio.to_thread.run_sync(
                        lambda _p=original_prompt: llm._call_api(
                            system_prompt="你是一个视频提示词安全改写专家。用户给你一段可能触发内容审核的视频运动描述，你把它改写得温和、中性、不包含任何暴力或敏感词汇，但保留镜头运动和画面变化的核心动作。只输出改写后的文本，不要输出解释。",
                            user_prompt=f"原文：{_p}\n\n请改写成温和版本，保持镜头运动、角色动作和环境变化，去掉所有暴力、战争、武器、流血相关的描述。",
                            max_tokens=500,
                        )
                    )
                    rewritten = new_prompt.get("text", "") if isinstance(new_prompt, dict) else str(new_prompt)
                    if rewritten and len(rewritten) > 10:
                        print(f"[VideoAgent] Prompt rewritten for sensitive content.")
                        return rewritten
                    return original_prompt
                except Exception as e:
                    print(f"[VideoAgent] Prompt rewrite failed: {e}, using original.")
                    return original_prompt

            async def _generate_one(
                scene: dict, scene_id: str, image_path: str, motion_prompt: str, duration: int
            ) -> dict | None:
                prompts_to_try = [motion_prompt]  # 先试原始提示词
                result = await _try_generate(prompts_to_try[0], scene_id, image_path, duration)
                if result is not None:
                    print(f"[VideoAgent] Scene {scene_id} video generated successfully.")
                    return result

                # 被审核拦截 → 用 AI 重写提示词再试（最多 2 次重写）
                for attempt in range(2):
                    print(f"[VideoAgent] Scene {scene_id} sensitive content detected, rewriting prompt (attempt {attempt+1})...")
                    new_prompt = await _rewrite_prompt(prompts_to_try[-1])
                    if new_prompt == prompts_to_try[-1]:
                        print(f"[VideoAgent] Prompt unchanged after rewrite, giving up.")
                        break
                    prompts_to_try.append(new_prompt)
                    result = await _try_generate(new_prompt, scene_id, image_path, duration)
                    if result is not None:
                        print(f"[VideoAgent] Scene {scene_id} video generated successfully after prompt rewrite.")
                        return result

                # 全部失败，降级为静态图片
                print(f"[VideoAgent] Scene {scene_id} all retries exhausted, falling back to static image.")
                await mongodb.update_one(
                    SCENES_COLLECTION,
                    {"_id": f"{task_id}_{scene_id}"},
                    {
                        "need_dynamic_video": False,
                        "status": "image_fallback",
                        "updated_at": datetime.now(UTC),
                    },
                )
                return None

            sem = anyio.Semaphore(3)  # 视频生成较贵，最多 3 个并发

            async def _bounded_generate(
                scene: dict, scene_id: str, image_path: str, motion_prompt: str, duration: int
            ) -> dict | None:
                async with sem:
                    return await _generate_one(scene, scene_id, image_path, motion_prompt, duration)

            results = await asyncio.gather(
                *[_bounded_generate(s, sid, ip, vp, dur) for (s, sid, ip, vp, dur) in scenes_to_generate],
                return_exceptions=False,
            )

            # ── 保存生成结果 ──────────────────────────────────────
            for (scene, scene_id, *_rest), video_result in zip(scenes_to_generate, results):
                if video_result is None:
                    continue

                asset = Asset(
                    asset_id=f"asset_{uuid4().hex[:12]}",
                    task_id=task_id,
                    scene_id=str(scene["scene_id"]),
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
