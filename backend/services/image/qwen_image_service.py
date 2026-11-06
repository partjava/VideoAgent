import os
import base64
import httpx
from pathlib import Path
from typing import Any
from config import settings
from services.model_config import model_config
from services.image.base import BaseImageService


# Official API: https://www.alibabacloud.com/help/en/model-studio/qwen-image-edit-api
# Image entries contain public URLs or data:image/...;base64 data, alongside text.
REFERENCE_MODELS = {
    "qwen-image-2.0", "qwen-image-2.0-pro", "qwen-image-2.1-pro", "qwen-image-2.1-turbo",
    "qwen-image-3.0", "qwen-image-3.0-pro",
    "qwen-image-2.0-pro-2026-06-22", "qwen-image-2.0-pro-2026-04-22",
    "qwen-image-2.0-pro-2026-03-03", "qwen-image-2.0-2026-03-03",
}


def _normalize_dashscope_base(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    for suffix in ("/compatible-mode/v1", "/api/v1", "/v1"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].rstrip("/")
    return cleaned or (url or "").strip().rstrip("/")


class QwenImageService(BaseImageService):
    @property
    def supports_reference_images(self) -> bool:
        model = model_config("image", settings)["model"]
        short_name = (model or "").rsplit("/", 1)[-1]
        return model in REFERENCE_MODELS or short_name in REFERENCE_MODELS

    def _reference_content(self, paths, model):
        from services.character_service import local_asset, validate_image
        short_name = (model or "").rsplit("/", 1)[-1]
        if model not in REFERENCE_MODELS and short_name not in REFERENCE_MODELS:
            raise ValueError(f"Configured image model {model} does not support reference images")
        limit = 10 if short_name in {"qwen-image-2.1-pro", "qwen-image-2.1-turbo"} else 3
        if len(paths) > limit:
            raise ValueError(f"Configured image model supports at most {limit} reference images")
        content = []
        for path in paths:
            data = local_asset(path).read_bytes()
            validate_image(data)
            mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
            content.append({"image": f"data:{mime};base64,{base64.b64encode(data).decode()}"})
        return content

    def generate_image(
        self,
        task_id: str,
        scene_id: str,
        image_prompt: str,
        negative_prompt: str | None = None,
        ratio: str = "9:16",
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        profile = model_config("image", settings)
        reference_content = self._reference_content(reference_images, profile["model"]) if reference_images else []
        if not settings.enable_paid_api:
            raise ValueError("付费 API 调用未启用，请在环境变量中设置 ENABLE_PAID_API=true 开启。")

        profile = model_config("image", settings)
        if not profile["key"]:
            raise ValueError("未找到 DASHSCOPE_API_KEY，请在环境变量中配置。")

        try:
            # 1. 同步提交文生图任务（不使用异步请求头）
            base_url = _normalize_dashscope_base(profile["url"])
            submit_url = f"{base_url}/api/v1/services/aigc/multimodal-generation/generation"
            headers = {
                "Authorization": f"Bearer {profile['key']}",
                "Content-Type": "application/json"
            }
            
            content_list = reference_content + [{"text": image_prompt}]
            payload = {
                "model": profile["model"],
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": content_list
                        }
                    ]
                },
                "parameters": {
                    "size": "1080*1920"
                }
            }
            if negative_prompt:
                short_name = (profile["model"] or "").rsplit("/", 1)[-1]
                if short_name.startswith("qwen-image-2.0"):
                    payload["parameters"]["negative_prompt"] = negative_prompt[:500]
                else:
                    content_list[-1]["text"] = f"{image_prompt}\n请避免出现：{negative_prompt}"

            import time
            res = None
            max_retries = 6
            backoff_factor = 2.0

            with httpx.Client(timeout=180.0) as client:
                for attempt in range(max_retries):
                    try:
                        res = client.post(submit_url, json=payload, headers=headers)
                        res.raise_for_status()
                        break
                    except (httpx.HTTPStatusError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TransportError) as err:
                        if attempt < max_retries - 1:
                            sleep_time = (backoff_factor ** attempt) + 1.0
                            print(f"[QwenImageService] Request error ({err}). Retrying in {sleep_time}s... (Attempt {attempt+1}/{max_retries})")
                            time.sleep(sleep_time)
                            continue
                        if hasattr(err, "response") and err.response is not None:
                            print(f"DashScope API Error Response: {err.response.text}")
                        raise err
                
                res_data = res.json()
                
                output = res_data.get("output", {})
                image_url = None
                
                # 从多模态 choices 格式中提取图片 URL
                choices = output.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", [])
                    if content and isinstance(content, list):
                        image_url = content[0].get("image")
                
                # 如果没有 choices，则回退读取标准 results 格式
                if not image_url:
                    results = output.get("results", [])
                    if results and len(results) > 0:
                        image_url = results[0].get("url")
                
                if not image_url:
                    raise ValueError(f"Failed to find generated image URL in response: {res_data}")

                # 2. 基于 backend 根目录创建目标目录
                backend_dir = Path(__file__).resolve().parents[2]
                dest_dir = backend_dir / "assets" / task_id / "images"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / f"{scene_id}.png"

                # 3. 下载图片内容
                img_res = client.get(image_url, timeout=60.0)
                img_res.raise_for_status()
                with open(dest_path, "wb") as f:
                    f.write(img_res.content)

                # assets 数据库中保存相对路径，方便匹配前端 URL 映射
                relative_asset_path = f"backend/assets/{task_id}/images/{scene_id}.png"

                return {
                    "source": "qwen",
                    "provider": "qwen_image",
                    "task_id": task_id,
                    "scene_id": scene_id,
                    "asset_type": "image",
                    "asset_path": relative_asset_path,
                    "image_prompt": image_prompt,
                    "negative_prompt": negative_prompt,
                    "status": "success",
                    "message": "Image generated successfully via Qwen-Image.",
                }
        except Exception as e:
            raise RuntimeError(f"QwenImageService failed for {scene_id}: {e}") from e
