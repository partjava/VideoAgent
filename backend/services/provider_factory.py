from config import settings
from services.image.base import BaseImageService
from services.image.qwen_image_service import QwenImageService
from services.llm.base import BaseLLMService
from services.llm.deepseek_service import DeepSeekService
from services.video.base import BaseVideoService
from services.video.wan_video_service import WanVideoService
from services.video.vidu_video_service import ViduVideoService
from services.video.doubao_video_service import DoubaoVideoService


def get_llm_service() -> BaseLLMService:
    if settings.model_provider == "deepseek":
        return DeepSeekService()
    raise ValueError(f"Unsupported MODEL_PROVIDER for real generation: {settings.model_provider}")


def get_image_service() -> BaseImageService:
    if settings.image_provider == "qwen":
        return QwenImageService()
    raise ValueError(f"Unsupported IMAGE_PROVIDER for real generation: {settings.image_provider}")


def get_video_service() -> BaseVideoService:
    if settings.video_provider == "vidu":
        return ViduVideoService()
    if settings.video_provider == "wan":
        return WanVideoService()
    if settings.video_provider == "doubao":
        return DoubaoVideoService()
    raise ValueError(f"Unsupported VIDEO_PROVIDER for real generation: {settings.video_provider}")
