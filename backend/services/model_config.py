import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from config import settings

PATH = Path(__file__).resolve().parents[1] / ".env.models.local"
LIMITS_PATH = Path(__file__).resolve().parents[1] / ".env.limits.local"
KINDS = ("prompt", "image", "video")
LIMIT_KEYS = ("max_images_per_task", "max_video_seconds_per_task", "max_dynamic_scenes_per_task")


def is_siliconflow_url(url: str | None) -> bool:
    """判断配置地址是否指向硅基流动（siliconflow.cn / siliconflow.com）端点。"""
    host = (urlsplit(str(url or "").strip()).hostname or "").lower()
    return host == "siliconflow.cn" or host.endswith(".siliconflow.cn") or host == "siliconflow.com" or host.endswith(".siliconflow.com")


def siliconflow_v1_base(url: str | None) -> str:
    """归一化硅基流动 API 根路径为 .../v1，避免重复拼接或缺失 /v1。"""
    cleaned = str(url or "").strip().rstrip("/")
    if not cleaned:
        return "https://api.siliconflow.cn/v1"
    for suffix in ("/images/generations", "/video/submit", "/video/status", "/chat/completions", "/models"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].rstrip("/")
    if not cleaned.endswith("/v1"):
        cleaned = f"{cleaned}/v1"
    return cleaned


def saved_models():
    return json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}


def model_config(kind, defaults=settings):
    if kind == "prompt":
        fallback = ("https://api.deepseek.com/v1", defaults.deepseek_model, defaults.deepseek_api_key)
    else:
        model = defaults.qwen_image_model if kind == "image" else defaults.wan_video_model
        fallback = (getattr(defaults, "dashscope_api_base", "https://dashscope.aliyuncs.com"),
                    model, defaults.dashscope_api_key)
    return dict(zip(("url", "model", "key"), fallback)) | saved_models().get(kind, {})


def validate_config(config):
    url = config["url"]
    parts = urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or any(ch.isspace() for ch in url)):
        raise ValueError("Use an HTTPS API base URL without credentials, query, or fragment.")
    if not config["model"]:
        raise ValueError("Model name is required.")
    return config


def save_models(models):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=PATH.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(models, file)
            file.close()
            temporary.replace(PATH)
        finally:
            temporary.unlink(missing_ok=True)


def saved_limits() -> dict[str, int]:
    if not LIMITS_PATH.exists():
        return {}
    try:
        raw = json.loads(LIMITS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, int] = {}
    for key in LIMIT_KEYS:
        if key in raw and raw[key] is not None:
            try:
                val = int(raw[key])
            except (TypeError, ValueError):
                continue
            if 0 <= val <= 10000:
                cleaned[key] = val
    return cleaned


def save_limits(limits: dict[str, int]) -> dict[str, int]:
    cleaned: dict[str, int] = {}
    for key in LIMIT_KEYS:
        if key not in limits or limits[key] is None:
            continue
        val = int(limits[key])
        if val < 0 or val > 10000:
            raise ValueError(f"{key} 必须是 0 到 10000 之间的整数（0 表示不限制）")
        cleaned[key] = val
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=LIMITS_PATH.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(cleaned, file)
            file.close()
            temporary.replace(LIMITS_PATH)
        finally:
            temporary.unlink(missing_ok=True)
    return cleaned


def clear_limits() -> None:
    LIMITS_PATH.unlink(missing_ok=True)


def env_default_limits(defaults=settings) -> dict[str, int]:
    return {
        "max_images_per_task": max(0, int(getattr(defaults, "max_images_per_task", 20) or 0)),
        "max_video_seconds_per_task": max(0, int(getattr(defaults, "max_video_seconds_per_task", 600) or 0)),
        "max_dynamic_scenes_per_task": max(0, int(getattr(defaults, "max_dynamic_scenes_per_task", 99) or 0)),
    }


def generation_limits(defaults=settings) -> dict[str, int]:
    """返回实时生效的生成额度上限（优先读取网页保存的本地配置，0 表示不限制；测试替换 settings 时保持隔离）。"""
    base = env_default_limits(defaults)
    if defaults is settings:
        return base | saved_limits()
    return base
