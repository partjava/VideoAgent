import asyncio
import json
import subprocess
import zipfile
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
import imageio_ffmpeg
from models.response_model import ApiResponse
from models.series_model import CreateSeriesRequest, OutlineRequest, ConfirmSnapshotRequest, PublishNotesRequest, CoverRequest
from services.series_service import series_service, BACKEND
from services.publish_materials import write_notes, write_cover
from services.snapshot_diff import compare_states

router = APIRouter(prefix='/api/series', tags=['series'])


def _safe_unlink(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


async def respond(call):
    try:
        return ApiResponse(status='success', data=await call)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('')
async def list_series():
    return await respond(series_service.list_series())


@router.post('')
async def create_series(payload: CreateSeriesRequest):
    return await respond(series_service.create_series(payload))


@router.get('/{series_id}')
async def get_series(series_id: str):
    return await respond(series_service.require(series_id))


@router.post('/{series_id}/outline')
async def generate_outline(series_id: str):
    return await respond(series_service.generate_outline(series_id))


@router.put('/{series_id}/outline')
async def update_outline(series_id: str, payload: OutlineRequest):
    return await respond(series_service.update_outline(series_id, payload.episodes))


@router.post('/{series_id}/confirm')
async def confirm_outline(series_id: str):
    return await respond(series_service.confirm_outline(series_id))


@router.post('/{series_id}/run')
async def run_series(series_id: str, background_tasks: BackgroundTasks):
    try:
        claimed = await series_service.start_run(series_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    background_tasks.add_task(series_service.run_series, series_id, claimed['lease'])
    return ApiResponse(status='success', data=await series_service.get_series(series_id))


@router.post('/{series_id}/stop')
async def stop_series(series_id: str):
    return await respond(series_service.stop(series_id))


@router.post('/{series_id}/approve-shoot')
async def approve_episode_shoot(series_id: str, task_id: str, background_tasks: BackgroundTasks):
    response = await respond(series_service.approve_episode_shoot(series_id, task_id))
    background_tasks.add_task(series_service.run_series, series_id, response.data['lease'])
    return ApiResponse(status='success', data={'status': 'running'})


@router.get('/{series_id}/episodes/{episode_num}/dependency-diff')
async def episode_dependency_diff(series_id: str, episode_num: int):
    async def build():
        project = await series_service.require(series_id)
        if not 2 <= episode_num <= len(project['episodes']):
            raise LookupError('本集没有上一集依赖')
        episode = project['episodes'][episode_num - 1]
        previous = project['episodes'][episode_num - 2]
        old = await series_service.get_snapshot(series_id, episode.get('input_snapshot_id'))
        new = await series_service.get_snapshot(series_id, previous.get('output_snapshot_id'))
        if not old or not new or new['status'] != 'published':
            raise ValueError('缺少可对比的已发布快照，请先确认上一集')
        return {'episode_num': episode_num, 'from_snapshot': old['snapshot_id'],
                'to_snapshot': new['snapshot_id'], 'changes': compare_states(old['state'], new['state'])}
    return await respond(build())


@router.get('/{series_id}/snapshots/{snapshot_id}')
async def get_snapshot(series_id: str, snapshot_id: str):
    result = await series_service.get_snapshot(series_id, snapshot_id)
    if not result:
        raise HTTPException(404, '快照不存在')
    return ApiResponse(status='success', data=result)


@router.post('/{series_id}/snapshots/{snapshot_id}/confirm')
async def confirm_snapshot(series_id: str, snapshot_id: str, payload: ConfirmSnapshotRequest, background_tasks: BackgroundTasks):
    response = await respond(series_service.confirm_snapshot(series_id, snapshot_id, payload.state))
    project = await series_service.require(series_id)
    next_episode = next((episode for episode in project['episodes'] if episode['status'] != 'success'), None)
    if project['auto_continue'] and not project.get('stop_requested') and next_episode and next_episode['status'] == 'pending':
        try:
            claimed = await series_service.start_run(series_id)
        except ValueError as exc:
            # Confirmation remains saved if a model preflight or competing run prevents automatic dispatch.
            response.data = {**response.data, 'auto_continue_error': str(exc)}
        else:
            background_tasks.add_task(series_service.run_series, series_id, claimed['lease'])
    return response


@router.post('/{series_id}/episodes/{episode_num}/regenerate')
async def regenerate_episode(series_id: str, episode_num: int):
    return await respond(series_service.regenerate(series_id, episode_num))


@router.post('/{series_id}/episodes/{episode_num}/accept-dependency')
async def accept_dependency(series_id: str, episode_num: int):
    return await respond(series_service.accept_dependency(series_id, episode_num))


@router.delete('/{series_id}')
async def archive_series(series_id: str):
    return await respond(series_service.archive(series_id))


async def episode_material_context(series_id, episode_num):
    project = await series_service.require(series_id)
    if not 1 <= episode_num <= len(project['episodes']):
        raise LookupError('集号不存在')
    episode = project['episodes'][episode_num - 1]
    video = await series_service.episode_export_path(project, episode)
    return project, episode, video


@router.post('/{series_id}/episodes/{episode_num}/publish-notes')
async def publish_notes(series_id: str, episode_num: int, payload: PublishNotesRequest):
    async def build():
        project, episode, video = await episode_material_context(series_id, episode_num)
        snapshot = await series_service.get_snapshot(series_id, episode['output_snapshot_id'])
        episode = {**episode, 'confirmed_ending': snapshot['state']}
        path = video.with_name('publish_notes.txt')
        notes = await asyncio.to_thread(write_notes, path, project, episode, payload.platform)
        return {**notes, 'path': path.relative_to(BACKEND.parent).as_posix()}
    return await respond(build())


@router.post('/{series_id}/episodes/{episode_num}/cover')
async def publish_cover(series_id: str, episode_num: int, payload: CoverRequest):
    async def build():
        _, _, video = await episode_material_context(series_id, episode_num)
        path = video.with_name('publish_cover.jpg')
        second = await asyncio.to_thread(write_cover, video, path, payload.second, payload.title)
        return {'path': path.relative_to(BACKEND.parent).as_posix(), 'second': second}
    return await respond(build())


@router.get('/{series_id}/download.zip')
async def download_series(series_id: str):
    try:
        project, paths = await series_service.export_paths(series_id)
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc)) from exc
    output_dir = BACKEND / 'outputs' / series_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f'{uuid4().hex}.zip'

    def build():
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_STORED) as archive:
            archive.writestr('series.json', json.dumps(project, ensure_ascii=False, indent=2, default=str))
            for index, path in enumerate(paths, 1):
                archive.write(path, f'episode_{index:02d}.mp4')
                subtitle = path.with_name('subtitle.srt').resolve()
                if subtitle.is_relative_to(path.parent) and subtitle.is_file():
                    archive.write(subtitle, f'episode_{index:02d}.srt')
                for name in ('publish_notes.txt', 'publish_cover.jpg'):
                    material = path.with_name(name).resolve()
                    if material.is_relative_to(path.parent) and material.is_file():
                        archive.write(material, f'episode_{index:02d}_{name}')

    try:
        await asyncio.to_thread(build)
    except (OSError, zipfile.BadZipFile) as exc:
        _safe_unlink(output)
        raise HTTPException(409, '打包失败，请检查分集文件') from exc
    return FileResponse(output, media_type='application/zip', filename=f'{series_id}.zip',
                        background=BackgroundTask(_safe_unlink, output))


@router.post('/{series_id}/compile')
async def compile_series(series_id: str):
    # Optional export uses only already-rendered local media; no provider calls.
    try:
        project, paths = await series_service.export_paths(series_id)
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc)) from exc
    output_dir = BACKEND / 'outputs' / series_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f'{uuid4().hex}.mp4'
    width, height = (1920, 1080) if project['ratio'] == '16:9' else (1080, 1920)

    def compile_local():
        from services.media_quality import inspect_video

        command = [imageio_ffmpeg.get_ffmpeg_exe(), '-y']
        for path in paths:
            command += ['-i', str(path)]
        filters = []
        for i, path in enumerate(paths):
            filters.append(
                f'[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,'
                f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p,setpts=PTS-STARTPTS[v{i}]'
            )
            media = inspect_video(path)
            if media['has_audio']:
                filters.append(f'[{i}:a]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a{i}]')
            else:
                filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={media['duration']}[a{i}]")
        filters.append(''.join(f'[v{i}][a{i}]' for i in range(len(paths))) + f'concat=n={len(paths)}:v=1:a=1[v][a]')
        command += ['-filter_complex', ';'.join(filters), '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-preset', 'fast', '-c:a', 'aac', '-movflags', '+faststart', str(output)]
        subprocess.run(command, check=True, capture_output=True, timeout=1800)
    try:
        await asyncio.to_thread(compile_local)
    except (subprocess.SubprocessError, OSError, RuntimeError, ValueError) as exc:
        _safe_unlink(output)
        raise HTTPException(409, '合并失败，请确认分集音视频完整；可下载分集 ZIP') from exc
    return FileResponse(output, media_type='video/mp4', filename=f'{series_id}.mp4',
                        background=BackgroundTask(_safe_unlink, output))
