from datetime import UTC, datetime
from models.task_model import VideoTaskStatus
from models.render_output_model import RenderOutput
from core.database import mongodb

from agents.agent_utils import (
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    with_document_id,
)


class ExportAgent:
    agent_name = "ExportAgent"

    async def run(self, task_id: str) -> dict[str, object]:
        try:
            await mark_agent_start(task_id, self.agent_name, progress=99)

            now = datetime.now(UTC)
            output_id = f"output_{task_id}"
            
            # 组装导出路径
            video_path = f"backend/outputs/{task_id}/final.mp4"
            subtitle_path = f"backend/outputs/{task_id}/subtitle.srt"
            
            # 将最终渲染输出记录保存到数据库
            render_output = RenderOutput(
                output_id=output_id,
                task_id=task_id,
                video_path=video_path,
                cover_path=None,
                subtitle_path=subtitle_path,
                script_path=None,
                storyboard_path=None,
                publish_path=None,
                duration=None,
                status="success",
                created_at=now,
                updated_at=now,
            )
            await mongodb.replace_one(
                "render_outputs",
                {"_id": render_output.output_id},
                with_document_id(render_output.model_dump(), render_output.output_id),
                upsert=True,
            )

            result = {
                "source": "real",
                "status": "success",
                "video_path": video_path,
                "subtitle_path": subtitle_path,
                "output_id": output_id,
                "message": "ExportAgent completed; final output saved to render_outputs.",
            }

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=100,
                status=VideoTaskStatus.SUCCESS,
                message="Export completed; pipeline completed",
                extra_fields={
                    "current_agent": None,
                    "metadata.export_status": "success",
                    "metadata.pipeline_status": "completed",
                    "metadata.video_path": video_path,
                    "metadata.subtitle_path": subtitle_path,
                    "metadata.output_id": output_id,
                },
            )
            return result
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
