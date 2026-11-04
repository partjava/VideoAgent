import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from config import settings

PATH = Path(__file__).resolve().parents[1] / ".env.models.local"
KINDS = ("prompt", "image", "video")


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
