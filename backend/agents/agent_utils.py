from datetime import UTC, datetime
from typing import Any

from core.database import mongodb
from models.agent_log_model import CreateAgentLogRequest
from models.task_model import VideoTaskStatus
from services.agent_log_service import agent_log_service
from services.task_service import task_service


SCRIPTS_COLLECTION = "scripts"
SCENES_COLLECTION = "scenes"
ASSETS_COLLECTION = "assets"
VOICES_COLLECTION = "voices"
MOCK_SOURCE = "mock"


async def write_agent_log(
    task_id: str,
    agent_name: str,
    status: str,
    message: str,
    input_summary: str | None = None,
    output_summary: str | None = None,
) -> None:
    await agent_log_service.write_log(
        CreateAgentLogRequest(
            task_id=task_id,
            agent_name=agent_name,
            status=status,
            input_summary=input_summary,
            output_summary=output_summary,
            message=message,
        )
    )


async def mark_agent_start(
    task_id: str,
    agent_name: str,
    progress: int,
    status: VideoTaskStatus | str | None = None,
) -> None:
    values: dict[str, Any] = {
        "current_agent": agent_name,
        "progress": progress,
        "updated_at": datetime.now(UTC),
    }
    if status is not None:
        values["status"] = status.value if isinstance(status, VideoTaskStatus) else status
    await task_service.update_task_fields(task_id, values)
    await write_agent_log(task_id, agent_name, "running", f"{agent_name} started")


async def mark_agent_success(
    task_id: str,
    agent_name: str,
    progress: int,
    status: VideoTaskStatus | str | None = None,
    message: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> None:
    values: dict[str, Any] = {
        "current_agent": agent_name,
        "progress": progress,
        "updated_at": datetime.now(UTC),
    }
    if status is not None:
        values["status"] = status.value if isinstance(status, VideoTaskStatus) else status
    if extra_fields:
        values.update(extra_fields)
    await task_service.update_task_fields(task_id, values)
    await write_agent_log(
        task_id,
        agent_name,
        "success",
        message or f"{agent_name} finished",
    )


async def mark_agent_failed(task_id: str, agent_name: str, error: Exception) -> None:
    await task_service.update_task_fields(
        task_id,
        {
            "status": VideoTaskStatus.FAILED.value,
            "current_agent": agent_name,
            "metadata.last_error": str(error),
            "updated_at": datetime.now(UTC),
        },
    )
    await write_agent_log(
        task_id,
        agent_name,
        "failed",
        f"{agent_name} failed",
        output_summary=str(error),
    )


async def get_required_task(task_id: str) -> dict[str, Any]:
    task = await task_service.get_task(task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    return task


def with_document_id(document: dict[str, Any], document_id: str) -> dict[str, Any]:
    result = document.copy()
    result["_id"] = document_id
    return result


def assert_no_mock_assets(assets: list[dict[str, Any]]) -> None:
    for asset in assets:
        if str(asset.get("source", "")).lower() == MOCK_SOURCE:
            asset_id = asset.get("asset_id") or asset.get("_id") or "unknown"
            asset_type = asset.get("asset_type") or "unknown"
            path = asset.get("path") or "unknown"
            raise ValueError(
                f"Mock asset is not allowed in real generation: "
                f"{asset_id} ({asset_type}) at {path}"
            )
