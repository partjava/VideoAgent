import anyio

from services.generation_usage import reserve_generation, finish_generation, record_reuse


async def dispatch_generation(task, kind, scene_id, operation, *, reuse=False):
    if reuse:
        await record_reuse(task, kind, scene_id)
        return await anyio.to_thread.run_sync(operation)
    token = await reserve_generation(task, kind, scene_id)
    await finish_generation(token, "submitted")
    try:
        result = await anyio.to_thread.run_sync(operation)
    except Exception:
        await finish_generation(token, "unconfirmed")
        raise
    await finish_generation(token, "success" if result.get("status") == "success" else "failed")
    return result
