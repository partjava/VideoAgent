import asyncio
import copy
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models.series_model import CreateSeriesRequest
from services import series_service as module


class MemoryDB:
    def __init__(self):
        self.rows = {}

    def matches(self, row, query):
        return all((row.get(k) != v['$ne']) if isinstance(v, dict) and '$ne' in v else row.get(k) == v for k, v in query.items())

    async def insert_one(self, collection, row):
        self.rows[(collection, row['_id'])] = copy.deepcopy(row)
        return row['_id']

    async def find_one(self, collection, query):
        return next((copy.deepcopy(r) for (c, _), r in self.rows.items() if c == collection and self.matches(r, query)), None)

    async def find_many(self, collection, filter=None, **kwargs):
        return [copy.deepcopy(r) for (c, _), r in self.rows.items() if c == collection and self.matches(r, filter or {})]

    async def update_one(self, collection, query, values):
        for (c, _), row in self.rows.items():
            if c == collection and self.matches(row, query):
                row.update(copy.deepcopy(values))
                return 1
        return 0


class SeriesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = MemoryDB()
        self.patch = patch.object(module, 'mongodb', self.db)
        self.patch.start()
        self.characters = patch('services.character_service.character_service.get_confirmed_profiles', AsyncMock(return_value=[]))
        self.characters.start()
        self.service = module.SeriesService()
        self.calls = []
        async def create(payload):
            task_id = f'task_{len(self.calls) + 1}'
            self.calls.append(payload)
            await self.db.insert_one('video_tasks', {'_id': task_id, 'status': 'created'})
            return SimpleNamespace(task_id=task_id)
        self.tasks = patch.object(module.task_service, 'create_task', create)
        self.tasks.start()
        self.get_task_patch = patch.object(module.task_service, 'get_task',
            lambda task_id: self.db.find_one('video_tasks', {'_id': task_id}))
        self.get_task_patch.start()
        self.pipeline = patch.object(module, 'pipeline_runner', AsyncMock(return_value={'status': 'success'}))
        self.runner = self.pipeline.start()
        self.project = await self.service.create_series(CreateSeriesRequest(title='测试', brief='连续故事', total_episodes=3))
        self.sid = self.project['series_id']
        episodes = [{'episode_num': n, 'title': str(n), 'opening_hook': '开头', 'core_event': '事件', 'ending_hook': '结尾'} for n in range(1, 4)]
        await self.service.update_outline(self.sid, episodes)
        await self.service.confirm_outline(self.sid)

    async def asyncTearDown(self):
        self.pipeline.stop()
        self.tasks.stop()
        self.get_task_patch.stop()
        self.characters.stop()
        self.patch.stop()

    async def run_one(self):
        project = await self.service.start_run(self.sid)
        await self.service.run_series(self.sid, project['lease'])
        return await self.service.get_series(self.sid)

    async def publish(self, index, state):
        project = await self.service.get_series(self.sid)
        return await self.service.confirm_snapshot(self.sid, project['episodes'][index]['output_snapshot_id'], state)

    async def test_one_episode_then_immutable_confirmation_and_next_dependency(self):
        project = await self.run_one()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(project['status'], 'awaiting_confirmation')
        original_id = project['episodes'][0]['output_snapshot_id']
        original = await self.service.get_snapshot(self.sid, original_id)
        self.assertEqual(original['source_kind'], 'planned')
        self.assertIsNone(original['observed_state'])
        with self.assertRaises(ValueError):
            await self.service.start_run(self.sid)
        published = await self.publish(0, {'holding': 'key'})
        self.assertNotEqual(original_id, published['snapshot_id'])
        self.assertEqual((await self.service.get_snapshot(self.sid, original_id))['status'], 'draft')
        project = await self.run_one()
        self.assertEqual(project['episodes'][1]['input_snapshot_id'], published['snapshot_id'])
        task = await self.db.find_one('video_tasks', {'_id': project['episodes'][1]['task_id']})
        self.assertEqual(task['metadata.series_context']['previous_snapshot']['state'], {'holding': 'key'})
        self.assertEqual(self.calls[1].generation_mode, 'native_10s')

    async def test_concurrent_claim_and_failure_dict_no_snapshot(self):
        results = await asyncio.gather(self.service.start_run(self.sid), self.service.start_run(self.sid), return_exceptions=True)
        claims = [r for r in results if isinstance(r, dict)]
        self.assertEqual(len(claims), 1)
        self.runner.return_value = {'status': 'failed', 'message': 'provider failed'}
        await self.service.run_series(self.sid, claims[0]['lease'])
        project = await self.service.get_series(self.sid)
        self.assertEqual(project['status'], 'failed')
        self.assertIsNone(project['episodes'][0]['output_snapshot_id'])
        self.runner.return_value = {'status': 'success'}
        await self.run_one()
        self.assertEqual(len(self.calls), 1)

    async def test_auto_continue_requires_evidence_for_each_ending_claim(self):
        await self.db.update_one(module.PROJECTS, {'_id': self.sid}, {'auto_continue': True})

        async def finish(task_id):
            await self.db.insert_one('scenes', {
                '_id': task_id, 'task_id': task_id, 'scene_index': 1, 'duration': 10,
                'video_path': 'raw.mp4', 'end_state': '手持钥匙',
            })
            return {'status': 'success'}

        self.runner.side_effect = finish
        verified = {
            'observed_state': {'holding_item': '钥匙'}, 'unknowns': [], 'conflicts': [],
            'sampled_seconds': [9.9],
            'verified': {
                'last_scene_end_state': {'claim': '手持钥匙', 'second': 9.9, 'evidence': '手中可见钥匙'},
                'ending_hook': {'claim': '结尾', 'second': 9.9, 'evidence': '人物停在门前'},
            },
        }
        uncertain = {**verified, 'unknowns': ['无法确认道具']}
        with patch('services.video_vision.review_enabled', return_value=True), patch(
            'services.video_vision.observe_episode_ending', side_effect=[verified, uncertain]
        ):
            project = await self.run_one()
        first, second, third = project['episodes']
        self.assertEqual(first['status'], 'success')
        published = await self.service.get_snapshot(self.sid, first['output_snapshot_id'])
        self.assertEqual(published['source_kind'], 'visual_verified')
        self.assertEqual(second['input_snapshot_id'], published['snapshot_id'])
        self.assertEqual(second['status'], 'needs_confirmation')
        self.assertIsNone(third['task_id'])

    async def test_regeneration_preserves_history_and_invalidates_downstream(self):
        await self.run_one()
        await self.publish(0, {'holding': 'key'})
        await self.run_one()
        await self.publish(1, {'holding': 'key'})
        before = await self.service.get_series(self.sid)
        await self.service.regenerate(self.sid, 1)
        await self.run_one()
        await self.publish(0, {'holding': 'map'})
        project = await self.service.get_series(self.sid)
        self.assertEqual(project['episodes'][1]['status'], 'needs_review')
        self.assertEqual(project['episodes'][0]['revisions'][0]['task_id'], before['episodes'][0]['task_id'])
        self.assertEqual(project['episodes'][1]['task_id'], before['episodes'][1]['task_id'])
        project = await self.service.accept_dependency(self.sid, 2)
        self.assertEqual(project['episodes'][1]['status'], 'success')
        output = await self.service.get_snapshot(self.sid, project['episodes'][1]['output_snapshot_id'])
        self.assertIn('dependency_acceptance', output)

    async def test_stop_does_not_cancel_accepted_work(self):
        claim = await self.service.start_run(self.sid)
        async def finish(task_id):
            await self.service.stop(self.sid)
            return {'status': 'success'}
        self.runner.side_effect = finish
        await self.service.run_series(self.sid, claim['lease'])
        project = await self.service.get_series(self.sid)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(project['stop_requested'])
        self.assertEqual(project['episodes'][0]['status'], 'needs_confirmation')

    async def test_outline_locked_after_task_and_archive_preserves_records(self):
        await self.run_one()
        project = await self.service.get_series(self.sid)
        with self.assertRaises(ValueError):
            await self.service.update_outline(self.sid, project['episodes'])
        await self.service.archive(self.sid)
        self.assertEqual(await self.service.list_series(), [])
        self.assertIsNotNone(await self.db.find_one('video_tasks', {'_id': project['episodes'][0]['task_id']}))

    async def test_restart_recovers_terminal_task_without_provider_replay(self):
        claim = await self.service.start_run(self.sid)
        task_id = 'task_recovered'
        episode = claim['episodes'][0]
        episode.update(task_id=task_id, status='running')
        await self.db.insert_one('video_tasks', {'_id': task_id, 'status': 'success'})
        await self.db.update_one(module.PROJECTS, {'_id': self.sid}, {
            'episodes': claim['episodes'],
            'updated_at': (datetime.now(UTC) - timedelta(minutes=2)).isoformat()})
        restarted = module.SeriesService()
        recovered = await restarted.start_run(self.sid)
        await restarted.run_series(self.sid, recovered['lease'])
        self.runner.assert_not_awaited()
        project = await restarted.get_series(self.sid)
        self.assertEqual(project['episodes'][0]['status'], 'needs_confirmation')
        self.assertEqual(project['episodes'][0]['task_id'], task_id)


if __name__ == '__main__':
    unittest.main()
