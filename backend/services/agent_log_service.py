from datetime import UTC, datetime
from uuid import uuid4

from core.database import mongodb
from models.agent_log_model import AgentLog, CreateAgentLogRequest


AGENT_LOGS_COLLECTION = "agent_logs"


def _build_log_id() -> str:
    return f"log_{uuid4().hex[:12]}"


def _log_to_document(log: AgentLog) -> dict[str, object]:
    # MongoDB 用 _id 做主键，业务接口仍然使用 log_id。
    document = log.model_dump()
    document["_id"] = log.log_id
    return document


class AgentLogService:
    async def write_log(self, payload: CreateAgentLogRequest) -> str:
        # 当前阶段只记录基础日志，不执行真正的 Agent Pipeline。
        log = AgentLog(
            log_id=_build_log_id(),
            task_id=payload.task_id,
            agent_name=payload.agent_name,
            status=payload.status,
            input_summary=payload.input_summary,
            output_summary=payload.output_summary,
            message=payload.message,
            created_at=datetime.now(UTC),
        )
        await mongodb.insert_one(AGENT_LOGS_COLLECTION, _log_to_document(log))
        return log.log_id


agent_log_service = AgentLogService()
