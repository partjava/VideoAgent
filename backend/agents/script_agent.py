import anyio
from datetime import UTC, datetime
from services.character_binding import series_prompt_context

from config import settings
from core.database import mongodb
from models.script_model import Script
from models.task_model import VideoTaskStatus
from agents.story_judge_agent import StoryJudgeAgent, normalize_segments_for_mode
from services.generation_contract import AUTO_DIRECTOR_MODE, NATIVE_GENERATION_MODE, provider_duration_bounds
from services.provider_factory import get_llm_service
from services.task_service import task_service

from agents.agent_utils import (
    SCRIPTS_COLLECTION,
    get_required_task,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    with_document_id,
)


class ScriptAgent:
    agent_name = "ScriptAgent"

    async def run(self, task_id: str) -> dict[str, object]:
        try:
            task = await get_required_task(task_id)
            
            # 检查脚本是否已生成
            existing_script = await mongodb.find_one(SCRIPTS_COLLECTION, {"task_id": task_id})
            if not existing_script:
                existing_script = await mongodb.find_one(SCRIPTS_COLLECTION, {"_id": f"script_{task_id}"})
            
            if existing_script:
                await mark_agent_success(
                    task_id,
                    self.agent_name,
                    progress=30,
                    status=VideoTaskStatus.SCRIPT_DONE,
                    message="Skipped ScriptAgent (script already exists)",
                    extra_fields={"metadata.script_id": existing_script.get("_id") or f"script_{task_id}"},
                )
                return existing_script

            await mark_agent_start(task_id, self.agent_name, progress=20)

            task_plan = task.get("metadata", {}).get("task_plan") if isinstance(task.get("metadata"), dict) else None
            if not isinstance(task_plan, dict):
                task_plan = {
                    "source": "deepseek",
                    "topic": task.get("topic") or task.get("user_input"),
                    "duration": task.get("duration") or 60,
                    "style": task.get("style") or "real",
                    "platform": task.get("platform") or "抖音",
                    "ratio": task.get("ratio") or "9:16",
                    "generation_mode": task.get("generation_mode") or "full_dynamic",
                }

            llm_service = get_llm_service()
            task_plan = {**task_plan, "generation_mode": task.get("generation_mode", "full_dynamic"),
                         "series_context": series_prompt_context(task)}
            script_data = await anyio.to_thread.run_sync(
                lambda _plan=task_plan: llm_service.generate_script(_plan)
            )
            mode = str(task.get("generation_mode") or AUTO_DIRECTOR_MODE)
            min_s, max_s = provider_duration_bounds(getattr(settings, "video_provider", None))

            # 情节审判官 + 自动打回重写精修（仅在配置启用且非兼容旧测试空段落时生效）
            judge_bundle = await anyio.to_thread.run_sync(
                lambda: StoryJudgeAgent.judge_and_polish(
                    task,
                    script_data,
                    task_plan,
                    llm_service=llm_service,
                    allow_llm=settings.enable_paid_api,
                )
            )
            script_data = judge_bundle["script_data"]
            raw_segments = normalize_segments_for_mode(
                script_data.get("segments") or [],
                mode=mode,
                min_sec=min_s,
                max_sec=max_s,
            ) if mode in {AUTO_DIRECTOR_MODE, NATIVE_GENERATION_MODE} else (script_data.get("segments") or [])

            now = datetime.now(UTC)
            script = Script(
                script_id=f"script_{task_id}",
                task_id=task_id,
                title=script_data.get("title") if isinstance(script_data.get("title"), str) else None,
                hook=script_data.get("hook") if isinstance(script_data.get("hook"), str) else None,
                content=str(script_data.get("content", "")),
                segments=raw_segments,
                ending=script_data.get("ending") if isinstance(script_data.get("ending"), str) else None,
                publish_copy=script_data.get("publish_copy") if isinstance(script_data.get("publish_copy"), str) else None,
                source=str(script_data.get("source", "deepseek")),
                status="created",
                created_at=now,
                updated_at=now,
            )
            if mode == "native_10s" and (
                any(segment.duration != 10 for segment in script.segments)
                or sum(segment.duration for segment in script.segments) != task.get("duration")
            ):
                raise ValueError("原生10秒模式要求每段严格为10秒且总时长等于任务时长")
            if mode == AUTO_DIRECTOR_MODE and script.segments:
                total_seg_dur = sum(seg.duration for seg in script.segments)
                if not task.get("duration_locked"):
                    await task_service.update_task_fields(
                        task_id,
                        {"duration": total_seg_dur, "metadata.task_plan.duration": total_seg_dur},
                    )
            script_doc = with_document_id(script.model_dump(), script.script_id)
            script_doc["story_review"] = judge_bundle["script_review_38"]
            script_doc["story_judge"] = judge_bundle["story_judge"]
            await mongodb.replace_one(
                SCRIPTS_COLLECTION,
                {"_id": script.script_id},
                script_doc,
                upsert=True,
            )

            from services.drama_craft_service import scan_script_metrics
            script_metrics = scan_script_metrics(script.content)

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=30,
                status=VideoTaskStatus.SCRIPT_DONE,
                message="Real script saved",
                extra_fields={
                    "metadata.script_id": script.script_id,
                    "metadata.script_metrics": script_metrics,
                    "metadata.script_review_38": judge_bundle["script_review_38"],
                    "metadata.story_judge": judge_bundle["story_judge"],
                },
            )
            return script.model_dump()
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
