import base64
from io import BytesIO
import re
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageOps, ImageStat

from config import settings
from services.image.base import BaseImageService
from services.model_config import model_config, siliconflow_v1_base

_EDIT_REF_CLAUSE_RE = re.compile(
    r"\n*\[(?:角色外貌定妆参考|角色外貌设定参考|同片场造型与环境锚定|环境参考锚定)\][^\n]*",
)
_ANTI_BLACK_NEGATIVE_PREFIX = "上下黑边, 左右黑边, 电影遮幅, 纯黑背景, 分屏"


def _band_black_ratio(gray_img: Image.Image, box: tuple[int, int, int, int]) -> float:
    band = gray_img.crop(box)
    pixels = max(1, band.width * band.height)
    return sum(band.histogram()[:12]) / float(pixels)


def is_black_or_collapsed_image(raw_bytes: bytes) -> bool:
    """Return True if image bytes decode to a pure-black, letterboxed/pillarboxed, split-black, or collapsed dark-void frame."""
    if not raw_bytes:
        return True
    try:
        with Image.open(BytesIO(raw_bytes)) as im:
            small = im.convert("L").resize((160, 160))
        mean_luma = float(ImageStat.Stat(small).mean[0])
        hist = small.histogram()
        near_black_ratio = sum(hist[:12]) / float(160 * 160)
        if mean_luma < 16.0 or near_black_ratio >= 0.82:
            return True

        top_black = _band_black_ratio(small, (0, 0, 160, 24))
        bot_black = _band_black_ratio(small, (0, 136, 160, 160))
        if top_black >= 0.92 and bot_black >= 0.92:
            return True

        left_black = _band_black_ratio(small, (0, 0, 24, 160))
        right_black = _band_black_ratio(small, (136, 0, 160, 160))
        if left_black >= 0.92 and right_black >= 0.92:
            return True

        left_half_black = _band_black_ratio(small, (0, 0, 80, 160))
        right_half_black = _band_black_ratio(small, (80, 0, 160, 160))
        return left_half_black >= 0.92 or right_half_black >= 0.92
    except Exception:
        return True


def _strip_edit_reference_clauses(prompt: str) -> str:
    """Remove edit-only reference anchoring directives before falling back to T2I."""
    cleaned = _EDIT_REF_CLAUSE_RE.sub("", str(prompt or "")).strip()
    return cleaned or str(prompt or "").strip()


def _build_t2i_prompt(prompt: str, ratio: str) -> str:
    """Strip edit-only directives and add full-bleed framing guidance for T2I fallback."""
    cleaned = _strip_edit_reference_clauses(prompt)
    if "满画幅" not in cleaned and "无黑边" not in cleaned:
        suffix = f"满画幅无黑边（{ratio}），完整实景环境与清晰自然光照。"
        return f"{cleaned}\n{suffix}" if cleaned else suffix
    return cleaned


def _anti_black_negative_prompt(negative_prompt: str | None) -> str:
    """Prepend black-bar and split-screen exclusions to a T2I negative prompt."""
    base = str(negative_prompt or "").strip()
    merged = f"{_ANTI_BLACK_NEGATIVE_PREFIX}, {base}" if base else _ANTI_BLACK_NEGATIVE_PREFIX
    return merged[:500]


def _prepare_reference_image(
    img: Image.Image,
    target_ratio: str | None,
    is_edit_model: bool,
) -> tuple[Image.Image, bool]:
    """Normalize reference image dimensions and aspect ratio as full-bleed canvases for edit models."""
    rgb = img.convert("RGB")
    w, h = rgb.size
    changed = img.mode != "RGB"

    if is_edit_model and target_ratio == "9:16":
        if w > h * 1.25:
            # Crop the left portrait headshot panel of a 16:9 4-view sheet and fit full-bleed
            # onto 576x1024 without padding bars so Qwen-Image-Edit will not paint black letterboxes.
            crop_box = (0, 0, max(64, int(w * 0.45)), max(64, int(h * 0.88)))
            portrait = rgb.crop(crop_box)
            fitted = ImageOps.fit(
                portrait,
                (576, 1024),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.35),
            )
            return fitted, True
        if w / max(1, h) > 0.72:
            fitted = ImageOps.fit(
                rgb,
                (576, 1024),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.35),
            )
            return fitted, True

    if is_edit_model and target_ratio == "16:9" and (h > w * 1.25 or w / max(1, h) < 1.35):
        fitted = ImageOps.fit(
            rgb,
            (1024, 576),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.35),
        )
        return fitted, True

    if max(w, h) > 1024:
        resized = rgb.copy()
        resized.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        return resized, True

    return rgb, changed


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

    @property
    def prefers_t2i_for_studio_cast_sheets(self) -> bool:
        """Qwen/Qwen-Image-Edit* 是原地编辑模型，直接喂纯底人物设定卡生成全新多人实景易出黑边/黑底；
        这类首帧应直接走 Qwen/Qwen-Image 文生图（单次计费、满画幅），保留 Edit 能力给尾帧与三视图编辑。"""
        return self._is_qwen_edit_model(self.model)

    def _encode_reference(
        self,
        path: str,
        *,
        target_ratio: str | None = None,
        is_edit_model: bool = False,
    ) -> str:
        from services.character_service import local_asset, validate_image

        data = local_asset(path).read_bytes()
        validated = validate_image(data)
        if isinstance(validated, Image.Image):
            img = validated
            original_changed = False
        else:
            with Image.open(BytesIO(data)) as opened:
                original_changed = opened.mode != "RGB"
                img = opened.convert("RGB")
        prepared, ratio_changed = _prepare_reference_image(img, target_ratio, is_edit_model)
        if not ratio_changed and not original_changed:
            mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
            return f"data:{mime};base64,{base64.b64encode(data).decode()}"
        buf = BytesIO()
        prepared.convert("RGB").save(buf, format="PNG", optimize=True)
        return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"

    def _submit_and_download(
        self,
        client: httpx.Client,
        submit_url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> bytes:
        res = None
        max_retries = 5
        req_payload = dict(payload)
        for attempt in range(max_retries):
            try:
                res = client.post(submit_url, json=req_payload, headers=headers)
                if (
                    res.status_code == 400
                    and "image_size" in req_payload
                    and "image_size" in str(getattr(res, "text", "") or "").lower()
                ):
                    req_payload.pop("image_size", None)
                    res = client.post(submit_url, json=req_payload, headers=headers)
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

        if image_url:
            img_res = client.get(image_url, timeout=60.0)
            img_res.raise_for_status()
            return img_res.content
        return base64.b64decode(b64_data)

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
        is_edit = self._is_edit_model(effective_model)
        if not is_kontext and not is_qwen_edit:
            if self._is_qwen_t2i_model(effective_model):
                payload["image_size"] = "1664x928" if ratio == "16:9" else "928x1664"
            else:
                payload["image_size"] = "1280x720" if ratio == "16:9" else "720x1280"
        # Qwen-Image-Edit* 与 FLUX.1-Kontext 发送长 negative_prompt 极易引发 CFG 塌陷导致纯黑输出
        if negative_prompt and not is_kontext and not is_qwen_edit:
            payload["negative_prompt"] = negative_prompt[:500]

        encoded_refs: list[str] = []
        if reference_images:
            encoded_refs.append(
                self._encode_reference(reference_images[0], target_ratio=ratio, is_edit_model=is_edit)
            )
            payload["image"] = encoded_refs[0]
            if self._is_multi_ref_edit_model(effective_model):
                if len(reference_images) >= 2:
                    encoded_refs.append(
                        self._encode_reference(reference_images[1], target_ratio=ratio, is_edit_model=is_edit)
                    )
                    payload["image2"] = encoded_refs[1]
                if len(reference_images) >= 3:
                    encoded_refs.append(
                        self._encode_reference(reference_images[2], target_ratio=ratio, is_edit_model=is_edit)
                    )
                    payload["image3"] = encoded_refs[2]

        try:
            with httpx.Client(timeout=180.0) as client:
                img_bytes = self._submit_and_download(client, submit_url, headers, payload)

                if is_black_or_collapsed_image(img_bytes):
                    # 1) 编辑模型返回纯黑/黑边/半黑异常图时，直接一步降级至 Qwen/Qwen-Image 满画幅文生图（不重复二次编辑浪费额度）
                    if is_qwen_edit:
                        print(f"[SiliconFlowImage] Black/letterboxed frame detected for {scene_id}, falling back directly to Qwen/Qwen-Image T2I...", flush=True)
                        t2i_payload: dict[str, Any] = {
                            "model": "Qwen/Qwen-Image",
                            "prompt": _build_t2i_prompt(image_prompt, ratio),
                            "image_size": "1664x928" if ratio == "16:9" else "928x1664",
                            "negative_prompt": _anti_black_negative_prompt(negative_prompt),
                        }
                        img_bytes = self._submit_and_download(client, submit_url, headers, t2i_payload)
                        effective_model = "Qwen/Qwen-Image (black-frame fallback)"
                    elif self._is_qwen_t2i_model(effective_model):
                        # 2) 直出 Qwen/Qwen-Image 暗黑废片时，补充明光满画幅指引后重试 1 次
                        print(f"[SiliconFlowImage] Dark-void frame detected on Qwen/Qwen-Image for {scene_id}, retrying with boosted full-bleed lighting...", flush=True)
                        retry_payload: dict[str, Any] = {
                            "model": effective_model,
                            "prompt": f"无黑边满画幅，明亮清晰的实景室内光线，禁止大面积纯黑背景。{_build_t2i_prompt(image_prompt, ratio)[:700]}",
                            "image_size": payload.get("image_size") or ("1664x928" if ratio == "16:9" else "928x1664"),
                            "negative_prompt": _anti_black_negative_prompt(negative_prompt),
                        }
                        img_bytes = self._submit_and_download(client, submit_url, headers, retry_payload)

                    if is_black_or_collapsed_image(img_bytes):
                        raise RuntimeError("SiliconFlow 连续返回纯黑/黑边异常画面，已拦截保存，请稍后重试或检查提示词")

                dest_dir = self.storage_root / "assets" / task_id / "images"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / f"{scene_id}.png"
                dest_path.write_bytes(img_bytes)

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
