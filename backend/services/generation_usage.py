"""Reserve generation operations before sending requests to a provider."""

from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from core.database import mongodb

COLLECTION = 'generation_usage'
LOG_LIMIT = 300
MAX_LIMIT = 2_147_483_647


class GenerationLimitExceeded(RuntimeError):
    """The operation was blocked before any generation request was sent."""


@dataclass(frozen=True)
class Reservation:
    ledger_id: str
    event_id: str


def _scope(task):
    task_id = task.get('task_id') or task.get('_id')
    if not task_id:
        raise ValueError('生成任务缺少编号')
    series_id = task.get('series_id')
    return (f'series:{series_id}' if series_id else f'task:{task_id}'), str(task_id)


class _OfflineCollection:
    def __init__(self):
        self.rows = {}

    async def find_one(self, query):
        row = self.rows.get(query.get('_id'))
        return dict(row) if row else None

    async def update_one(self, query, update, upsert=False):
        from types import SimpleNamespace
        key = query.get('_id')
        if key not in self.rows and upsert:
            self.rows[key] = {'_id': key, **update.get('$setOnInsert', {})}
        row = self.rows.get(key)
        if row is None or ('$or' in query and row.get('limit') is not None and row.get('used', 0) >= row['limit']):
            return SimpleNamespace(matched_count=0)
        for field, value in update.get('$inc', {}).items():
            row[field] = row.get(field, 0) + value
        for field, value in update.get('$set', {}).items():
            if field.startswith('events.$.'):
                for ev in row.get('events', []):
                    if ev.get('id') == query.get('events.id'):
                        ev[field.removeprefix('events.$.')] = value
            else:
                row[field] = value
        for field, value in update.get('$push', {}).items():
            row[field] = (list(row.get(field, [])) + list(value['$each']))[value['$slice']:]
        return SimpleNamespace(matched_count=1)


_offline_ledger = _OfflineCollection()


def _get_ledger_collection():
    try:
        return mongodb.get_collection(COLLECTION)
    except RuntimeError:
        return _offline_ledger


async def _ensure(collection, ledger_id):
    try:
        await collection.update_one({'_id': ledger_id}, {
            '$setOnInsert': {'limit': None, 'used': 0, 'reused': 0, 'events': []},
        }, upsert=True)
    except DuplicateKeyError:
        # A concurrent reservation or limit update created the same ledger.
        pass


async def _log(task, kind, scene_id, *, reused):
    ledger_id, task_id = _scope(task)
    collection = _get_ledger_collection()
    await _ensure(collection, ledger_id)
    event_id = uuid4().hex
    event = {
        'id': event_id,
        'task_id': task_id,
        'scene_id': str(scene_id) if scene_id is not None else None,
        'kind': kind,
        'status': 'reused' if reused else 'reserved',
        'created_at': datetime.now(UTC).isoformat(),
    }
    query = {'_id': ledger_id}
    if not reused:
        # Evaluate the live limit in the same write that increments the counter.
        query['$or'] = [{'limit': None}, {'$expr': {'$lt': ['$used', '$limit']}}]
    result = await collection.update_one(query, {
        '$inc': {'reused' if reused else 'used': 1},
        '$push': {'events': {'$each': [event], '$slice': -LOG_LIMIT}},
    })
    if not result.matched_count:
        raise GenerationLimitExceeded('生成操作额度已用完。请提高额度后继续，已生成的素材可以复用。')
    return Reservation(ledger_id, event_id)


async def reserve_generation(task, kind, scene_id=None):
    return await _log(task, kind, scene_id, reused=False)


async def record_reuse(task, kind, scene_id=None):
    await _log(task, kind, scene_id, reused=True)


async def finish_generation(token, status):
    if status not in {'submitted', 'success', 'unconfirmed', 'failed'}:
        raise ValueError(f'Invalid generation status: {status}')
    collection = _get_ledger_collection()
    await collection.update_one({'_id': token.ledger_id, 'events.id': token.event_id}, {
        '$set': {
            'events.$.status': status,
            'events.$.updated_at': datetime.now(UTC).isoformat(),
        },
    })


async def set_limit(scope_type, scope_id, limit):
    if limit is not None and (type(limit) is not int or not 1 <= limit <= MAX_LIMIT):
        raise ValueError(f'额度必须为空或 1 至 {MAX_LIMIT} 之间的整数')
    collection = _get_ledger_collection()
    ledger_id = f'{scope_type}:{scope_id}'
    await _ensure(collection, ledger_id)
    await collection.update_one({'_id': ledger_id}, {'$set': {'limit': limit}})


async def get_usage(scope_type, scope_id):
    collection = _get_ledger_collection()
    ledger = await collection.find_one({'_id': f'{scope_type}:{scope_id}'}) or {}
    limit = ledger.get('limit')
    used = ledger.get('used', 0)
    return {
        'scope_type': scope_type,
        'scope_id': scope_id,
        'limit': limit,
        'used': used,
        'remaining': None if limit is None else max(0, limit - used),
        'reused': ledger.get('reused', 0),
        'log_limit': LOG_LIMIT,
        'logs': list(reversed(ledger.get('events', []))),
    }


def estimate_generation(duration, mode=None, episodes=1):
    if not isinstance(duration, (int, float)) or duration <= 0:
        return None
    if mode == 'wan3_whole':
        images, videos = 1, 1
    elif mode == 'paired_5s':
        images = ceil(duration / 10)
        videos = images * 2
    else:
        images = videos = ceil(duration / 10)
    return {'images': images * episodes, 'videos': videos * episodes, 'total': (images + videos) * episodes}
