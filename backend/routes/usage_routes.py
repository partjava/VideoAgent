from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from models.response_model import ApiResponse
from services import generation_usage as usage
from services.series_service import series_service
from services.task_service import task_service

router = APIRouter(prefix='/api/usage', tags=['generation-usage'])
ScopeType = Literal['task', 'series']


class LimitRequest(BaseModel):
    limit: int | None = Field(strict=True, ge=1, le=usage.MAX_LIMIT)


async def resolve_scope(scope_type, scope_id):
    if scope_type == 'task':
        task = await task_service.get_task(scope_id)
        if not task:
            raise HTTPException(404, '未找到视频任务')
        if task.get('series_id'):
            return await resolve_scope('series', task['series_id'])
        estimate = usage.estimate_generation(task.get('duration'), task.get('generation_mode'))
        return scope_type, scope_id, estimate
    try:
        series = await series_service.require(scope_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    estimate = usage.estimate_generation(series.get('episode_duration'), episodes=series.get('total_episodes') or 1)
    return scope_type, scope_id, estimate


async def response(scope_type, scope_id, estimate):
    summary = await usage.get_usage(scope_type, scope_id)
    return ApiResponse(status='success', data={**summary, 'estimate': estimate})


@router.get('/{scope_type}/{scope_id}')
async def get_usage(scope_type: ScopeType, scope_id: str):
    return await response(*await resolve_scope(scope_type, scope_id))


@router.put('/{scope_type}/{scope_id}')
async def update_limit(scope_type: ScopeType, scope_id: str, payload: LimitRequest):
    ledger_scope, ledger_id, estimate = await resolve_scope(scope_type, scope_id)
    await usage.set_limit(ledger_scope, ledger_id, payload.limit)
    return await response(ledger_scope, ledger_id, estimate)
