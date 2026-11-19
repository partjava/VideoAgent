import base64
import time
from pathlib import Path
from typing import Any

import httpx

from config import settings
from services.image.base import BaseImageService
from services.model_config import model_config, siliconflow_v1_base


class SiliconFlowImageService(BaseImageService):
    """硅基流动（SiliconFlow /v1/images/generations）图像生成与参考编辑适配器。"""

    def __init__(self, storage_root: Path | None = None):
        self.storage_root = Path(storage_root) if storage_root is not None else Path(__file__).resolve().parents[2]

    @staticmethod
    def _is_qwen_edit_model(model: str | None) -> bool:
        return "qwen-image-edit" in str(model or "").lower()

    @staticmethod
    def _is_multi_ref_edit_model(model: str | None) -> bool:
        return "qwen-image-edit-2509" in str(model or "").lower()

    @staticmethod
    def _is_kontext_model(model: str | None) -> bool:
        return "kontext" in str(model or "").lower()

    @staticmethod
    def _is_qwen_t2i_model(model: str | None) -> bool:
        lowered = str(model or "").lower()
        return "qwen-image" in lowered and not SiliconFlowImageService._is_qwen_edit_model(lowered)

    @staticmethod
    def _is_edit_model(model: str | None) -> bool:
        lowered = str(model or "").lower()
        return "kontext" in lowered or "image-edit" in lowered or "/edit" in lowered or "-edit" in lowered

    @property
    def model(self) -> str:
        return str(model_config("image", settings).get("model") or "black-forest-labs/FLUX.1-dev")

    @property
    def supports_reference_images(self) -> bool:
        return self._is_edit_model(self.model)

    @property
    def max_reference_images(self) -> int:
        if self._is_multi_ref_edit_model(self.model):
            return 3
        return 1 if self.supports_reference_images else 0

    def _encode_reference(self, path: str) -> str:
        from services.character_service import local_asset, validate_image

        data = local_asset(path).read_bytes()
        validate_image(data)
        mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"

    def generate_image(
        self,
        task_id: str,
        scene_id: str,
        image_prompt: str,
        negative_prompt: str | None = None,
        ratio: str = "9:16",
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_paid_api:
            raise ValueError("付费 API 调用未启用，请在环境变量中设置 ENABLE_PAID_API=true 开启。")

        profile = model_config("image", settings)
        api_key = str(profile.get("key") or "").strip()
        if not api_key:
            raise ValueError("未配置硅基流动图片 API Key，请在「API 配置」页面或环境变量中填写。")

        model_name = str(profile.get("model") or "black-forest-labs/FLUX.1-dev").strip()
        max_refs = 3 if self._is_multi_ref_edit_model(model_name) else (1 if self._is_edit_model(model_name) else 0)
        if reference_images:
            if max_refs <= 0:
                raise ValueError(
                    f"当前硅基流动图片模型 {model_name} 不支持参考图输入，请选用 Qwen/Qwen-Image-Edit-2509 或 FLUX.1-Kontext-dev。"
                )
            if len(reference_images) > max_refs:
                raise ValueError(f"当前硅基流动图片模型 {model_name} 最多支持 {max_refs} 张参考图")

        # Qwen/Qwen-Image-Edit* 为纯图生图编辑模型，无参考图（如首张正面定妆照或纯空镜）自动配对同系文生图模型 Qwen/Qwen-Image
        effective_model = (
            "Qwen/Qwen-Image"
            if (not reference_images and self._is_qwen_edit_model(model_name))
            else model_name
        )

        base_v1 = siliconflow_v1_base(profile.get("url"))
        submit_url = f"{base_v1}/images/generations"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": effective_model,
            "prompt": image_prompt,
        }
        is_kontext = self._is_kontext_model(effective_model)
        is_qwen_edit = self._is_qwen_edit_model(effective_model)
        if not is_kontext and not is_qwen_edit:
            if self._is_qwen_t2i_model(effective_model):
                payload["image_size"] = "1664x928" if ratio == "16:9" else "928x1664"
            else:
                payload["image_size"] = "1280x720" if ratio == "16:9" else "720x1280"
        if negative_prompt and not is_kontext:
            payload["negative_prompt"] = negative_prompt[:500]
        if reference_images:
            payload["image"] = self._encode_reference(reference_images[0])
            if self._is_multi_ref_edit_model(effective_model):
                if len(reference_images) >= 2:
                    payload["image2"] = self._encode_reference(reference_images[1])
                if len(reference_images) >= 3:
                    payload["image3"] = self._encode_reference(reference_images[2])

        try:
            res = None
            max_retries = 5
            with httpx.Client(timeout=180.0) as client:
                for attempt in range(max_retries):
                    try:
                        res = client.post(submit_url, json=payload, headers=headers)
                        if (
                            res.status_code == 400
                            and "image_size" in payload
                            and "image_size" in str(getattr(res, "text", "") or "").lower()
                        ):
                            payload.pop("image_size", None)
                            res = client.post(submit_url, json=payload, headers=headers)
                        if res.status_code == 429 and attempt < max_retries - 1:
                            time.sleep(2 ** attempt + 1)
                            continue
                        res.raise_for_status()
                        break
                    except (httpx.HTTPStatusError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TransportError) as err:
                        if attempt < max_retries - 1:
                            time.sleep(2 ** attempt + 1)
                            continue
                        detail = ""
                        if getattr(err, "response", None) is not None:
                            detail = f": {err.response.text[:300]}"
                        raise RuntimeError(f"SiliconFlow 图片请求失败{detail}") from err

                res_data = res.json()
                images = res_data.get("images") or res_data.get("data") or []
                image_url = images[0].get("url") if images and isinstance(images[0], dict) else None
                b64_data = images[0].get("b64_json") if images and isinstance(images[0], dict) else None
                if not image_url and not b64_data:
                    raise ValueError(f"SiliconFlow 未返回图片结果: {res_data}")

                dest_dir = self.storage_root / "assets" / task_id / "images"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / f"{scene_id}.png"

                if image_url:
                    img_res = client.get(image_url, timeout=60.0)
                    img_res.raise_for_status()
                    dest_path.write_bytes(img_res.content)
                else:
                    dest_path.write_bytes(base64.b64decode(b64_data))

            rel_path = f"backend/assets/{task_id}/images/{scene_id}.png"
            return {
                "source": "siliconflow",
                "provider": "siliconflow_image",
                "task_id": task_id,
                "scene_id": scene_id,
                "asset_type": "image",
                "asset_path": rel_path,
                "image_prompt": image_prompt,
                "negative_prompt": negative_prompt,
                "status": "success",
                "message": f"Image generated successfully via SiliconFlow ({effective_model}).",
            }
        except Exception as exc:
            raise RuntimeError(f"SiliconFlowImageService failed for {scene_id}: {exc}") from exc
