from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from config import settings
from core.database import mongodb
from routes.video_routes import router as video_router
from routes.settings_routes import router as settings_router
from routes.comfyui_routes import router as comfyui_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await mongodb.connect()
    try:
        yield
    finally:
        await mongodb.close()


from pathlib import Path

from fastapi.staticfiles import StaticFiles

app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(video_router)
app.include_router(settings_router)
app.include_router(comfyui_router)

# 静态挂载生成的媒体素材和最终视频
# 使用基于 main.py 位置的绝对路径，不依赖 CWD
_backend_root = Path(__file__).resolve().parent
# 确保目录存在（空目录 FastAPI/Starlette 不允许 mount）
(_backend_root / "assets").mkdir(parents=True, exist_ok=True)
(_backend_root / "outputs").mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=str(_backend_root / "assets")), name="assets")
app.mount("/outputs", StaticFiles(directory=str(_backend_root / "outputs")), name="outputs")


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
