import os

from config import settings
from services.model_config import is_siliconflow_url, model_config, saved_models
from services.image.base import BaseImageService
from services.llm.base import BaseLLMService
from services.video.base import BaseVideoService


def _runtime_provider(env_key: str, configured_value: str) -> str:
    return str(os.getenv(env_key) or configured_value).strip().lower()


def get_llm_service() -> BaseLLMService:
    provider = _runtime_provider("MODEL_PROVIDER", getattr(settings, "model_provider", "deepseek"))
    if provider == "deepseek":
        from services.llm.deepseek_service import DeepSeekService

        return DeepSeekService()
    raise ValueError(f"Unsupported MODEL_PROVIDER for real generation: {provider}")


def get_image_service() -> BaseImageService:
    provider = _runtime_provider("IMAGE_PROVIDER", getattr(settings, "image_provider", "qwen"))
    saved = saved_models()
    if provider != "comfyui" and "image" in saved:
        provider = "siliconflow" if is_siliconflow_url(saved["image"].get("url")) else "qwen"
    if provider == "siliconflow":
        from services.image.siliconflow_image_service import SiliconFlowImageService

        return SiliconFlowImageService()
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


def _resolve_video_provider_name(cfg=None) -> str:
    resolved_cfg = cfg if cfg is not None else settings
    provider = _runtime_provider("VIDEO_PROVIDER", getattr(resolved_cfg, "video_provider", "wan"))
    if (cfg is None or cfg is settings) and provider != "comfyui":
        saved = saved_models()
        if "video" in saved:
            return "siliconflow" if is_siliconflow_url(saved["video"].get("url")) else "wan"
    return provider


def active_video_provider_and_model(cfg=None) -> tuple[str, str]:
    """返回当前真实生效的视频服务商和模型名（感知 .env.models.local 与环境变量，同时保留测试 patch）。"""
    resolved_cfg = cfg if cfg is not None else settings
    provider = _resolve_video_provider_name(resolved_cfg)
    if provider == "siliconflow":
        try:
            cfg_model = str(model_config("video", resolved_cfg).get("model") or "")
        except Exception:
            cfg_model = ""
        if (
            (cfg is None or cfg is settings)
            and "video" not in saved_models()
            and cfg_model == getattr(resolved_cfg, "wan_video_model", "")
        ):
            cfg_model = "MiniMaxAI/MiniMax-H3"
        return provider, cfg_model or "MiniMaxAI/MiniMax-H3"
    if provider == "wan":
        try:
            model = str(model_config("video", resolved_cfg).get("model") or getattr(resolved_cfg, "wan_video_model", "") or "")
        except Exception:
            model = str(getattr(resolved_cfg, "wan_video_model", "") or "")
        return provider, model
    if provider == "vidu":
        return provider, str(getattr(resolved_cfg, "vidu_model", "") or "")
    if provider == "doubao":
        return provider, str(getattr(resolved_cfg, "volcengine_model", "") or "")
    return provider, ""


def get_video_service() -> BaseVideoService:
    provider = _resolve_video_provider_name(settings)
    if provider == "vidu":
        from services.video.vidu_video_service import ViduVideoService

        return ViduVideoService()
    if provider == "siliconflow":
        from services.video.siliconflow_video_service import SiliconFlowVideoService

        return SiliconFlowVideoService()
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
