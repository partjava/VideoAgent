from pathlib import Path
from models.task_model import VideoTaskStatus

from agents.agent_utils import mark_agent_failed, mark_agent_start, mark_agent_success


class QualityAgent:
    agent_name = "QualityAgent"

    async def run(self, task_id: str) -> dict[str, object]:
        try:
            await mark_agent_start(
                task_id,
                self.agent_name,
                progress=97,
                status=VideoTaskStatus.CHECKING,
            )

            backend_dir = Path(__file__).resolve().parents[1]
            video_file = backend_dir / "outputs" / task_id / "final.mp4"
            if not video_file.exists():
                raise FileNotFoundError(f"Video file final.mp4 was not generated for task: {task_id}")

            result = {
                "source": "real",
                "status": "success",
                "message": f"Quality inspection passed; {video_file.name} is present.",
            }
            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=98,
                status=VideoTaskStatus.CHECKING,
                message="Quality check passed",
                extra_fields={"metadata.quality_status": "success"},
            )
            return result
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
