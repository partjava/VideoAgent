from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from config import settings
from core.database import mongodb
from routes.video_routes import router as video_router
from routes.settings_routes import router as settings_router
from routes.comfyui_routes import router as comfyui_router
from routes.character_routes import router as character_router
from routes.series_routes import router as series_router
from routes.shoot_review_routes import router as shoot_review_router
from routes.usage_routes import router as usage_router
from routes.drama_craft_routes import router as drama_craft_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await mongodb.connect()
    try:
        from services.settings_persistence import hydrate_settings_from_db

        await hydrate_settings_from_db()
    except Exception as exc:
        print(f"[Startup] Settings DB hydration skipped: {exc}", flush=True)
    try:
        yield
    finally:
        await mongodb.close()


from pathlib import Path

from fastapi.staticfiles import StaticFiles


class NoCacheStaticFiles(StaticFiles):
    """Serve generated media assets with fresh revalidation while preserving 206 Range streaming for video/audio."""

    async def get_response(self, path: str, scope):
        raw_headers = list(scope.get("headers") or [])
        has_range = any(k.lower() == b"range" for k, _ in raw_headers)
        if has_range:
            # Strip conditional headers on Range requests so Starlette returns 206 Partial Content
            # instead of 304 Not Modified (which breaks Chromium <video> moov-atom range loading).
            scope = dict(scope)
            scope["headers"] = [
                (k, v)
                for k, v in raw_headers
                if k.lower() not in (b"if-none-match", b"if-modified-since")
            ]
        response = await super().get_response(path, scope)
        lower_path = str(path or "").lower()
        if lower_path.endswith((".mp4", ".webm", ".mp3", ".wav")):
            response.headers["Cache-Control"] = "public, max-age=3600"
        else:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(video_router)
app.include_router(settings_router)
app.include_router(comfyui_router)
app.include_router(character_router)
app.include_router(series_router)
app.include_router(shoot_review_router)
app.include_router(usage_router)
app.include_router(drama_craft_router)

# 静态挂载生成的媒体素材和最终视频
# 使用基于 main.py 位置的绝对路径，不依赖 CWD
_backend_root = Path(__file__).resolve().parent
# 确保目录存在（空目录 FastAPI/Starlette 不允许 mount）
(_backend_root / "assets").mkdir(parents=True, exist_ok=True)
(_backend_root / "outputs").mkdir(parents=True, exist_ok=True)
app.mount("/assets", NoCacheStaticFiles(directory=str(_backend_root / "assets")), name="assets")
app.mount("/outputs", NoCacheStaticFiles(directory=str(_backend_root / "outputs")), name="outputs")


@app.get("/health")
async def health_check() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "database": {
            "configured": mongodb.is_configured(),
            "name": settings.mongodb_db_name,
        },
        "providers": {
            "deepseek": settings.deepseek_provider,
            "qwen_image": settings.qwen_image_provider,
            "wan_video": settings.wan_video_provider,
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

