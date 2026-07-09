import httpx
from pathlib import Path
from typing import Any

from config import settings
from services.image.base import BaseImageService


class DoubaoImageService(BaseImageService):
    """Doubao Image Generation (Volcengine Ark platform) via Seedream 5.0 Lite model.

    Uses the OpenAI-compatible /images/generations endpoint:
      POST https://ark.cn-beijing.volces.com/api/v3/images/generations
    """

    API_BASE = "https://ark.cn-beijing.volces.com/api/v3"

    def _get_headers(self) -> dict[str, str]:
        key = settings.volcengine_api_key.strip() if settings.volcengine_api_key else ""
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def generate_image(
        self,
        task_id: str,
        scene_id: str,
        image_prompt: str,
        negative_prompt: str | None = None,
        ratio: str = "9:16",
    ) -> dict[str, Any]:
        if not settings.enable_paid_api:
            raise ValueError(
                "付费 API 调用未启用，请在环境变量中设置 ENABLE_PAID_API=true 开启。"
            )

        if not settings.volcengine_api_key:
            raise ValueError("未找到 VOLCENGINE_API_KEY，请在环境变量中配置。")

        try:
            # 1. 构建请求 payload（OpenAI 兼容格式）
            # 注意: Seedream 5.0 Lite 不支持 negative_prompt 和 n 参数
            url = f"{self.API_BASE}/images/generations"
            payload: dict[str, Any] = {
                "model": settings.volcengine_image_model,
                "prompt": image_prompt,
                "size": "2848x1600" if ratio == "16:9" else "1600x2848",
                "output_format": "png",
                "watermark": False,
            }

            with httpx.Client(timeout=120.0) as client:
                res = client.post(url, json=payload, headers=self._get_headers())
                if res.status_code != 200:
                    print(f"[DoubaoImageService] API error response: {res.text}")
                res.raise_for_status()
                res_data = res.json()

                # 2. 从响应中提取图片 URL
                # OpenAI 兼容格式: { "data": [ { "url": "..." } ] }
                data = res_data.get("data", [])
                if not data or not isinstance(data, list):
                    raise ValueError(
                        f"Seedream API 返回格式异常，缺少 data 字段: {res_data}"
                    )
                image_data = data[0]
                image_url = image_data.get("url") or image_data.get("b64_json")
                if not image_url:
                    raise ValueError(
                        f"Seedream API 返回中未找到图片 url/b64_json: {res_data}"
                    )

                # 3. 基于 backend 根目录创建目标目录
                backend_dir = Path(__file__).resolve().parents[2]
                dest_dir = backend_dir / "assets" / task_id / "images"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / f"{scene_id}.png"

                # 4. 下载图片内容
                img_res = client.get(image_url, timeout=60.0)
                img_res.raise_for_status()
                with open(dest_path, "wb") as f:
                    f.write(img_res.content)

                relative_asset_path = f"backend/assets/{task_id}/images/{scene_id}.png"

                return {
                    "source": "doubao",
                    "provider": "doubao_image",
                    "task_id": task_id,
                    "scene_id": scene_id,
                    "asset_type": "image",
                    "asset_path": relative_asset_path,
                    "image_prompt": image_prompt,
                    "negative_prompt": negative_prompt,
                    "status": "success",
                    "message": "Image generated successfully via Doubao Seedream 5.0 Lite.",
                }
        except Exception as e:
            raise RuntimeError(
                f"DoubaoImageService failed for {scene_id}: {e}"
            ) from e
