import os

from config import settings
from services.image.base import BaseImageService
from services.llm.base import BaseLLMService
from services.video.base import BaseVideoService


def _runtime_provider(env_key: str, configured_value: str) -> str:
    return str(os.getenv(env_key) or configured_value).strip().lower()


def get_llm_service() -> BaseLLMService:
    provider = _runtime_provider("MODEL_PROVIDER", settings.model_provider)
    if provider == "deepseek":
        from services.llm.deepseek_service import DeepSeekService

        return DeepSeekService()
    raise ValueError(f"Unsupported MODEL_PROVIDER for real generation: {provider}")


def get_image_service() -> BaseImageService:
    provider = _runtime_provider("IMAGE_PROVIDER", settings.image_provider)
    if provider == "qwen":
        from services.image.qwen_image_service import QwenImageService

        return QwenImageService()
    if provider == "doubao":
        from services.image.doubao_image_service import DoubaoImageService

        return DoubaoImageService()
    if provider == "comfyui":
        from services.image.comfyui_image_service import ComfyUIImageService

        return ComfyUIImageService()
    raise ValueError(f"Unsupported IMAGE_PROVIDER for real generation: {provider}")


def get_video_service() -> BaseVideoService:
    provider = _runtime_provider("VIDEO_PROVIDER", settings.video_provider)
    if provider == "vidu":
        from services.video.vidu_video_service import ViduVideoService

        return ViduVideoService()
    if provider == "wan":
        from services.video.wan_video_service import WanVideoService

        return WanVideoService()
    if provider == "doubao":
        from services.video.doubao_video_service import DoubaoVideoService

        return DoubaoVideoService()
    if provider == "comfyui":
        from services.video.comfyui_video_service import ComfyUIVideoService

        return ComfyUIVideoService()
    raise ValueError(f"Unsupported VIDEO_PROVIDER for real generation: {provider}")
