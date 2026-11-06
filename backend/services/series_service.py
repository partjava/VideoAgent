"""Versioned series orchestration. Provider work always runs through the existing pipeline."""
import asyncio
import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from core.database import mongodb
from models.series_model import CreateSeriesRequest
from models.task_model import CreateVideoTaskRequest
from services.task_service import task_service

PROJECTS = 'series_projects'
SNAPSHOTS = 'series_snapshots'
BACKEND = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(UTC).isoformat()


def public(document):
    if document is None:
        return None
    value = copy.deepcopy(document)
    value.pop('_id', None)
    return value


def normalize_outline(episodes, count):
    if len(episodes) != count:
        raise ValueError(f'大纲必须包含 {count} 集')
    result = []
    for index, source in enumerate(episodes, 1):
        if source.get('episode_num') != index:
            raise ValueError('集号必须从 1 开始连续排列')
        item = {'episode_num': index}
        for key, alias in [('title', 'title'), ('opening_hook', 'opening_3s_hook'),
                           ('core_event', 'core_event'), ('ending_hook', 'cliffhanger_hook')]:
            value = source.get(key, source.get(alias))
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'第 {index} 集缺少 {key}')
            item[key] = value.strip()
        result.append(item)
    return result


async def pipeline_runner(task_id):
    from agents.pipeline import run_pipeline
    return await run_pipeline(task_id)


class SeriesService:
    def __init__(self):
        self._active_leases = set()

    async def get_series(self, series_id):
        return public(await mongodb.find_one(PROJECTS, {'_id': series_id}))

    async def require(self, series_id):
        project = await self.get_series(series_id)
        if not project or project['status'] == 'archived':
            raise LookupError('连载项目不存在')
        return project

    async def list_series(self):
        return [public(p) for p in await mongodb.find_many(PROJECTS, {'status': {'$ne': 'archived'}}, limit=200)]

    async def create_series(self, payload: CreateSeriesRequest):
        from services.character_service import character_service
        characters = await character_service.get_confirmed_profiles(payload.character_ids)
        sid = 'series_' + uuid4().hex[:16]
        project = {'_id': sid, 'series_id': sid, **payload.model_dump(), 'characters': copy.deepcopy(characters),
                   'status': 'draft', 'outline_revision': 1, 'outline_confirmed': False,
                   'stop_requested': False, 'created_at': now(), 'updated_at': now(), 'episodes': []}
        for number in range(1, payload.total_episodes + 1):
            project['episodes'].append({'episode_num': number, 'title': '', 'opening_hook': '', 'core_event': '',
                'ending_hook': '', 'status': 'draft', 'revision': 1, 'task_id': None,
                'input_snapshot_id': None, 'output_snapshot_id': None, 'revisions': []})
        await mongodb.insert_one(PROJECTS, project)
        if payload.generation_limit is not None:
            from services.generation_usage import set_limit

            await set_limit('series', sid, payload.generation_limit)
        return public(project)

    async def _claim(self, sid, target='editing', allowed=None):
        project = await self.require(sid)
        allowed = allowed or ['draft', 'ready', 'awaiting_confirmation', 'awaiting_shoot_review', 'stopped', 'failed', 'completed']
        if project['status'] not in allowed:
            raise ValueError('项目正在运行或修改，请稍后重试')
        lease = uuid4().hex
        if target == 'running':
            self._active_leases.add(lease)
        changed = await mongodb.update_one(PROJECTS, {'_id': sid, 'status': project['status'],
            'updated_at': project['updated_at']}, {'status': target, 'lease': lease, 'updated_at': now()})
        if not changed:
            self._active_leases.discard(lease)
            raise ValueError('项目状态已变化，请刷新后重试')
        project['lease'] = lease
        project['_prior_status'] = project['status']
        project['status'] = target
        return project

    async def _save(self, project, status):
        values = {k: v for k, v in project.items() if k not in {'_id', '_prior_status', 'stop_requested'}}
        values.update(status=status, updated_at=now(), lease=None)
        if not await mongodb.update_one(PROJECTS, {'_id': project['series_id'], 'lease': project['lease']}, values):
            raise ValueError('项目锁已变化')
        return await self.get_series(project['series_id'])

    async def _release_error(self, project):
        await self._save(project, project['_prior_status'])
        self._active_leases.discard(project.get('lease'))

    async def update_outline(self, sid, episodes):
        project = await self._claim(sid)
        try:
            if any(e['task_id'] or e.get('revisions') for e in project['episodes']):
                raise ValueError('已生成项目的大纲已锁定；全局修改请新建项目')
            normalized = normalize_outline(episodes, project['total_episodes'])
            for old, new in zip(project['episodes'], normalized):
                old.update(new, status='pending')
            project['outline_revision'] += 1
            project['outline_confirmed'] = False
            return await self._save(project, 'draft')
        except Exception:
            await self._release_error(project)
            raise

    async def generate_outline(self, sid):
        project = await self._claim(sid)
        try:
            if any(e['task_id'] or e.get('revisions') for e in project['episodes']):
                raise ValueError('大纲已锁定；请新建项目')
            from services.llm.deepseek_service import DeepSeekService
            prompt = {'title': project['title'], 'brief': project['brief'], 'characters': project['characters'],
                      'total_episodes': project['total_episodes'], 'episode_duration': project['episode_duration']}
            result = await asyncio.to_thread(DeepSeekService()._call_api,
                '你是连载短剧总编剧。只返回 JSON {"episodes":[...]}。严格按总集数，episode_num 从1连续。'
                '每集包括 title, opening_hook, core_event, ending_hook。开场抓人，事件有因果，结尾承接下一集。'
                '保持角色身份及道具状态连续，不虚构实际拍摄观察。', json.dumps(prompt, ensure_ascii=False), 6000)
            normalized = normalize_outline(result.get('episodes', []), project['total_episodes'])
            for old, new in zip(project['episodes'], normalized):
                old.update(new, status='pending')
            project['outline_revision'] += 1
            project['outline_confirmed'] = False
            return await self._save(project, 'draft')
        except Exception:
            await self._release_error(project)
            raise

    async def confirm_outline(self, sid):
        project = await self._claim(sid)
        try:
            normalize_outline(project['episodes'], project['total_episodes'])
            project['outline_confirmed'] = True
            return await self._save(project, 'ready')
        except Exception:
            await self._release_error(project)
            raise

    async def start_run(self, sid):
        current = await self.require(sid)
        if current['characters']:
            from services.provider_factory import get_image_service
            if not getattr(get_image_service(), 'supports_reference_images', False):
                raise ValueError('连载角色需要支持参考图的图片模型，请先更换型号')
        allowed = ['ready', 'stopped', 'failed', 'awaiting_confirmation']
        if current['status'] == 'running' and current.get('lease') not in self._active_leases:
            if (datetime.now(UTC) - datetime.fromisoformat(current['updated_at'])).total_seconds() < 60:
                raise ValueError('任务正在启动，请稍后重试')
            episode = next((e for e in current['episodes'] if e['status'] != 'success'), None)
            task = await task_service.get_task(episode['task_id']) if episode and episode['task_id'] else None
            if task and task.get('status') not in {'success', 'failed'}:
                raise ValueError('上次任务尚未处于终态，暂不重新派发以避免重复付费；请检查原任务状态')
            allowed.append('running')
        project = await self._claim(sid, 'running', allowed)
        if not project['outline_confirmed']:
            await self._release_error(project)
            raise ValueError('请先确认完整大纲')
        # Validate the first unresolved episode before accepting background work.
        episode = next((e for e in project['episodes'] if e['status'] != 'success'), None)
        if not episode or episode['status'] in {'needs_confirmation', 'needs_review', 'awaiting_shoot_review'}:
            await self._release_error(project)
            raise ValueError('请先确认快照、开拍预审或处理继承状态')
        if episode['episode_num'] > 1:
            try:
                await self._predecessor(project, episode['episode_num'])
            except Exception:
                await self._release_error(project)
                raise
        await mongodb.update_one(PROJECTS, {'_id': sid, 'lease': project['lease']}, {'stop_requested': False})
        return project

    async def _predecessor(self, project, number):
        if number == 1:
            return None
        predecessor = project['episodes'][number - 2]
        snapshot = await self.get_snapshot(project['series_id'], predecessor.get('output_snapshot_id'))
        if predecessor['status'] != 'success' or not snapshot or snapshot['status'] != 'published':
            raise ValueError('上一集必须成功并发布确认快照')
        return snapshot

    async def approve_episode_shoot(self, sid, task_id):
        from routes.shoot_review_routes import claim_shoot_review_approval

        project = await self._claim(sid, 'running', ['awaiting_shoot_review'])
        try:
            episode = next((item for item in project['episodes'] if item['status'] != 'success'), None)
            if not episode or episode['task_id'] != task_id or episode['status'] != 'awaiting_shoot_review':
                raise ValueError('当前没有可确认开拍的单集')
            await claim_shoot_review_approval(task_id, allow_series=True)
            await mongodb.update_one(PROJECTS, {'_id': sid, 'lease': project['lease']}, {'stop_requested': False})
            return project
        except Exception:
            await self._release_error(project)
            raise

    async def run_series(self, sid, lease):
        project = await self.require(sid)
        if project.get('lease') != lease or project['status'] != 'running':
            return
        project['_prior_status'] = 'ready'
        try:
            for episode in project['episodes']:
                if episode['status'] == 'success':
                    continue
                current = await self.require(sid)
                if current.get('stop_requested'):
                    await self._save(project, 'stopped')
                    return
                if episode['status'] in {'needs_confirmation', 'needs_review'}:
                    await self._save(project, 'awaiting_confirmation')
                    return
                previous = await self._predecessor(project, episode['episode_num'])
                context = {'episode_outline': {k: episode[k] for k in ['episode_num', 'title', 'opening_hook', 'core_event', 'ending_hook']},
                           'characters': project['characters'], 'previous_snapshot': previous,
                           'outline_revision': project['outline_revision'], 'episode_revision': episode['revision']}
                if not episode['task_id']:
                    task = await task_service.create_task(CreateVideoTaskRequest(
                        user_input=project['brief'] + '\n本集大纲：' + json.dumps(context['episode_outline'], ensure_ascii=False),
                        duration=project['episode_duration'], style=project['style'], ratio=project['ratio'],
                        generation_mode='native_10s', pre_shoot_review=project.get('pre_shoot_review', False)))
                    episode['task_id'] = task.task_id
                    episode['input_snapshot_id'] = previous['snapshot_id'] if previous else None
                    await mongodb.update_one('video_tasks', {'_id': task.task_id}, {'series_id': sid,
                        'episode_num': episode['episode_num'], 'series_context': context, 'metadata.series_context': context})
                episode['status'] = 'running'
                await mongodb.update_one(PROJECTS, {'_id': sid, 'lease': lease}, {'episodes': project['episodes'], 'updated_at': now()})
                existing = await task_service.get_task(episode['task_id'])
                result = {'status': 'success'} if existing and existing.get('status') == 'success' else await pipeline_runner(episode['task_id'])
                if isinstance(result, dict) and result.get('status') == 'awaiting_shoot_review':
                    episode['status'] = 'awaiting_shoot_review'
                    project.pop('error', None)
                    await self._save(project, 'awaiting_shoot_review')
                    return
                if not isinstance(result, dict) or result.get('status') != 'success':
                    raise RuntimeError(str(result.get('message', result.get('error', '单集生成失败'))) if isinstance(result, dict) else '单集生成失败')
                scenes = await mongodb.find_many('scenes', {'task_id': episode['task_id']}, limit=200, sort_field='scene_index', sort_direction=1)
                last = scenes[-1] if scenes else {}
                frame_path = None
                if last.get('last_frame_path'):
                    raw = Path(last['last_frame_path'])
                    candidate = (raw if raw.is_absolute() else BACKEND.parent / raw).resolve()
                    if candidate.is_file() and any(candidate.is_relative_to((BACKEND / directory / episode['task_id']).resolve())
                                                   for directory in ['assets', 'outputs']):
                        frame_path = candidate.relative_to(BACKEND.parent).as_posix()
                if not frame_path:
                    final = BACKEND / 'outputs' / episode['task_id'] / 'final.mp4'
                    if final.is_file():
                        from services.media_quality import extract_tail

                        destination = final.with_name(f'ending-{episode["revision"]}.png')
                        try:
                            await asyncio.to_thread(extract_tail, final, destination)
                            if destination.is_file():
                                frame_path = destination.relative_to(BACKEND.parent).as_posix()
                        except (OSError, RuntimeError, ValueError):
                            pass
                planned = {'ending_hook': episode['ending_hook'], 'core_event': episode['core_event'],
                           'last_scene_start_state': last.get('start_state'),
                           'last_scene_end_state': last.get('effective_end_state', last.get('end_state')),
                           'note': '根据大纲和分镜形成的计划状态，尚未进行实际画面观察；请人工补充确认。'}
                snapshot_id = 'snapshot_' + uuid4().hex[:16]
                snapshot = {'_id': snapshot_id, 'snapshot_id': snapshot_id, 'series_id': sid,
                    'episode_num': episode['episode_num'], 'revision': episode['revision'], 'source_task_id': episode['task_id'],
                    'source_kind': 'planned', 'status': 'draft', 'planned_state': planned, 'observed_state': None,
                    'source_frame_path': frame_path, 'state': planned, 'created_at': now()}
                from services.video_vision import review_enabled, observe_episode_ending, ending_is_verified

                verified = False
                if last.get('video_path') and review_enabled():
                    try:
                        observation = await asyncio.to_thread(
                            observe_episode_ending, last['video_path'], float(last['duration']), planned
                        )
                        verified = ending_is_verified(planned, observation)
                        snapshot.update(
                            source_kind='observed', observed_state=observation.get('observed_state'),
                            observation=observation,
                        )
                    except Exception:
                        snapshot['observation_error'] = '画面观察未完成，请人工对照成片确认。'
                await mongodb.insert_one(SNAPSHOTS, snapshot)
                episode.update(status='needs_confirmation', output_snapshot_id=snapshot_id)
                project.pop('error', None)
                current = await self.require(sid)
                if project['auto_continue'] and verified and not current.get('stop_requested'):
                    state = {key: value for key, value in planned.items() if key != 'note'}
                    await self._publish_snapshot(project, episode, snapshot, state, 'visual_verified')
                    await mongodb.update_one(PROJECTS, {'_id': sid, 'lease': lease}, {
                        'episodes': project['episodes'], 'updated_at': now(),
                    })
                    continue
                await self._save(project, 'awaiting_confirmation')
                return
            await self._save(project, 'completed')
        except Exception as exc:
            for item in project['episodes']:
                if item['status'] == 'running':
                    item['status'] = 'failed'
            project['error'] = str(exc)
            await self._save(project, 'failed')
        finally:
            self._active_leases.discard(lease)

    async def stop(self, sid):
        project = await self.require(sid)
        if project['status'] == 'editing':
            raise ValueError('项目正在修改')
        values = {'stop_requested': True, 'updated_at': now()}
        if project['status'] != 'running':
            values['status'] = 'stopped'
        await mongodb.update_one(PROJECTS, {'_id': sid, 'status': project['status']}, values)
        return await self.get_series(sid)

    async def get_snapshot(self, sid, snapshot_id):
        if not snapshot_id:
            return None
        return public(await mongodb.find_one(SNAPSHOTS, {'_id': snapshot_id, 'series_id': sid}))

    async def confirm_snapshot(self, sid, snapshot_id, state):
        project = await self._claim(sid)
        try:
            original = await self.get_snapshot(sid, snapshot_id)
            if not original:
                raise LookupError('快照不存在')
            episode = project['episodes'][original['episode_num'] - 1]
            if episode['output_snapshot_id'] != snapshot_id:
                raise ValueError('只能确认当前单集版本的快照')
            published = await self._publish_snapshot(project, episode, original, state, 'user_confirmed')
            status = 'completed' if all(e['status'] == 'success' for e in project['episodes']) else 'ready'
            await self._save(project, status)
            return public(published)
        except Exception:
            await self._release_error(project)
            raise

    async def _publish_snapshot(self, project, episode, original, state, source):
        published_id = 'snapshot_' + uuid4().hex[:16]
        published = {**original, '_id': published_id, 'snapshot_id': published_id,
            'revision': original['revision'] + 1, 'status': 'published', 'source_kind': source,
            'parent_snapshot_id': original['snapshot_id'], 'state': copy.deepcopy(state), 'created_at': now()}
        await mongodb.insert_one(SNAPSHOTS, published)
        episode.update(status='success', output_snapshot_id=published_id)
        old = episode.get('previous_output_snapshot_id')
        old_snapshot = await self.get_snapshot(project['series_id'], old) if old else (
            original if original['status'] == 'published' else None
        )
        if old_snapshot and old_snapshot['state'] != state:
            for downstream in project['episodes'][episode['episode_num']:]:
                if downstream['task_id']:
                    downstream['status'] = 'needs_review'
        return published

    async def regenerate(self, sid, number):
        project = await self._claim(sid)
        try:
            if number < 1 or number > len(project['episodes']):
                raise LookupError('集号不存在')
            episode = project['episodes'][number - 1]
            await self._predecessor(project, number)
            if episode['task_id']:
                episode['revisions'].append({k: copy.deepcopy(v) for k, v in episode.items() if k != 'revisions'})
            episode['previous_output_snapshot_id'] = episode['output_snapshot_id'] or episode.get('previous_output_snapshot_id')
            episode.update(revision=episode['revision'] + 1, status='pending', task_id=None,
                           input_snapshot_id=None, output_snapshot_id=None)
            return await self._save(project, 'ready')
        except Exception:
            await self._release_error(project)
            raise

    async def accept_dependency(self, sid, number):
        project = await self._claim(sid)
        try:
            if not 1 <= number <= len(project['episodes']):
                raise LookupError('集号不存在')
            episode = project['episodes'][number - 1]
            if episode['status'] != 'needs_review':
                raise ValueError('该集不需要确认继承状态')
            previous = await self._predecessor(project, number)
            output = await self.get_snapshot(sid, episode['output_snapshot_id'])
            if not output or output['status'] != 'published':
                raise ValueError('请重新生成或确认该集输出快照')
            snapshot_id = 'snapshot_' + uuid4().hex[:16]
            adapted = {**output, '_id': snapshot_id, 'snapshot_id': snapshot_id, 'revision': output['revision'] + 1,
                'parent_snapshot_id': output['snapshot_id'], 'source_kind': 'user_confirmed',
                'dependency_acceptance': {'previous_input_snapshot_id': episode['input_snapshot_id'],
                    'accepted_input_snapshot_id': previous['snapshot_id'] if previous else None,
                    'reused_source_task_id': episode['task_id']}, 'created_at': now()}
            await mongodb.insert_one(SNAPSHOTS, adapted)
            episode.update(status='success', input_snapshot_id=previous['snapshot_id'] if previous else None,
                           output_snapshot_id=snapshot_id)
            status = 'completed' if all(e['status'] == 'success' for e in project['episodes']) else 'ready'
            return await self._save(project, status)
        except Exception:
            await self._release_error(project)
            raise

    async def archive(self, sid):
        project = await self._claim(sid)
        return await self._save(project, 'archived')

    async def export_paths(self, sid):
        project = await self.require(sid)
        paths = [await self.episode_export_path(project, episode) for episode in project['episodes']]
        return project, paths

    async def episode_export_path(self, project, episode):
        snapshot = await self.get_snapshot(project['series_id'], episode['output_snapshot_id'])
        if episode['status'] != 'success' or not snapshot or snapshot['status'] != 'published':
            raise ValueError('请先完成并发布本集快照')
        task_id = episode['task_id']
        if not isinstance(task_id, str) or not task_id.startswith('task_') or not task_id.replace('_', '').isalnum():
            raise ValueError('任务路径无效')
        root = (BACKEND / 'outputs' / task_id).resolve()
        path = (root / 'final.mp4').resolve()
        if not path.is_relative_to((BACKEND / 'outputs').resolve()) or not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f'第 {episode["episode_num"]} 集成片不可用')
        return path


series_service = SeriesService()
