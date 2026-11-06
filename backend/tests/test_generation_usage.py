import asyncio
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import generation_usage as usage


class MemoryCollection:
    """Simulate the atomic Mongo update, yielding before each write."""

    def __init__(self):
        self.rows = {}

    async def find_one(self, query):
        await asyncio.sleep(0)
        return copy.deepcopy(self.rows.get(query['_id']))

    async def update_one(self, query, update, upsert=False):
        await asyncio.sleep(0)
        key = query['_id']
        if key not in self.rows and upsert:
            self.rows[key] = {'_id': key, **update.get('$setOnInsert', {})}
        row = self.rows.get(key)
        if row is None or ('$or' in query and row['limit'] is not None and row['used'] >= row['limit']):
            return SimpleNamespace(matched_count=0)
        for field, value in update.get('$inc', {}).items():
            row[field] += value
        for field, value in update.get('$set', {}).items():
            if field.startswith('events.$.'):
                event = next(item for item in row['events'] if item['id'] == query['events.id'])
                event[field.removeprefix('events.$.')] = value
            else:
                row[field] = value
        for field, value in update.get('$push', {}).items():
            row[field] = (row[field] + value['$each'])[value['$slice']:]
        return SimpleNamespace(matched_count=1)


class GenerationUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_series_episodes_compete_for_same_last_reservation(self):
        collection = MemoryCollection()
        with patch.object(usage.mongodb, 'get_collection', return_value=collection):
            await usage.set_limit('series', 'series-a', 1)
            tasks = [{'task_id': f'episode-{n}', 'series_id': 'series-a'} for n in range(2)]
            outcomes = await asyncio.gather(
                *(usage.reserve_generation(task, 'video', 'scene-1') for task in tasks),
                return_exceptions=True,
            )
            rejected = [outcome for outcome in outcomes if isinstance(outcome, usage.GenerationLimitExceeded)]
            self.assertEqual(len(rejected), 1)
            token = next(outcome for outcome in outcomes if not isinstance(outcome, Exception))
            await usage.finish_generation(token, 'unconfirmed')
            await usage.record_reuse(tasks[0], 'video', 'scene-2')
            summary = await usage.get_usage('series', 'series-a')
            self.assertEqual((summary['used'], summary['remaining'], summary['reused']), (1, 0, 1))
            self.assertEqual([entry['status'] for entry in summary['logs']], ['reused', 'unconfirmed'])
            await usage.set_limit('series', 'series-a', 2)
            await usage.reserve_generation(tasks[0], 'image', 'scene-3')
            self.assertEqual((await usage.get_usage('series', 'series-a'))['used'], 2)
