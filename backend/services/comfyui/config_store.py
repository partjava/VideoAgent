from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from config import settings


ALLOWED_IMAGE_PROVIDERS = {"qwen", "comfyui"}
ALLOWED_VIDEO_PROVIDERS = {"wan", "vidu", "doubao", "comfyui"}
COMFYUI_ENV_KEYS = (
    "IMAGE_PROVIDER",
    "VIDEO_PROVIDER",
    "COMFYUI_BASE_URL",
    "COMFYUI_TIMEOUT_SECONDS",
)


def backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def env_path() -> Path:
    return backend_root() / ".env"


def _clean_base_url(value: Any) -> str:
    text = str(value or "http://127.0.0.1:8188").strip()
    return text.rstrip("/") or "http://127.0.0.1:8188"


def _clean_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = 600
    return max(10, min(timeout, 3600))


def normalize_comfyui_config(payload: dict[str, Any]) -> dict[str, Any]:
    image_provider = str(
        payload.get("image_provider")
        or os.getenv("IMAGE_PROVIDER")
        or settings.image_provider
        or "qwen"
    ).strip().lower()
    video_provider = str(
        payload.get("video_provider")
        or os.getenv("VIDEO_PROVIDER")
        or settings.video_provider
        or "vidu"
    ).strip().lower()

    if image_provider not in ALLOWED_IMAGE_PROVIDERS:
        raise ValueError(f"Unsupported image provider: {image_provider}")
    if video_provider not in ALLOWED_VIDEO_PROVIDERS:
        raise ValueError(f"Unsupported video provider: {video_provider}")

    return {
        "image_provider": image_provider,
        "video_provider": video_provider,
        "comfyui_base_url": _clean_base_url(payload.get("comfyui_base_url") or os.getenv("COMFYUI_BASE_URL")),
        "comfyui_timeout_seconds": _clean_timeout(
            payload.get("comfyui_timeout_seconds") or os.getenv("COMFYUI_TIMEOUT_SECONDS")
        ),
    }


def build_comfyui_config() -> dict[str, Any]:
    return normalize_comfyui_config({})


def write_env_values(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.exists() else []
    remaining = dict(values)
    output: list[str] = []

    for line in lines:
        stripped = line.strip()
        if "=" not in stripped or stripped.startswith("#"):
            output.append(line)
            continue

        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}\n")
        else:
            output.append(line)

    for key, value in remaining.items():
        output.append(f"{key}={value}\n")

    path.write_text("".join(output), encoding="utf-8")


def save_comfyui_config(payload: dict[str, Any]) -> dict[str, Any]:
    config = normalize_comfyui_config(payload)
    env_values = {
        "IMAGE_PROVIDER": config["image_provider"],
        "VIDEO_PROVIDER": config["video_provider"],
        "COMFYUI_BASE_URL": config["comfyui_base_url"],
        "COMFYUI_TIMEOUT_SECONDS": str(config["comfyui_timeout_seconds"]),
    }
    write_env_values(env_path(), env_values)
    os.environ.update(env_values)
    return config
