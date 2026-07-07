import base64
import time

import httpx
from pathlib import Path
from typing import Any

from config import settings
from services.video.base import BaseVideoService


class ViduVideoService(BaseVideoService):
    """Vidu API (api.vidu.com) image-to-video service."""

    API_BASE = "https://api.vidu.com/ent/v2"

    def _get_headers(self) -> dict[str, str]:
        token = settings.vidu_api_token.strip() if settings.vidu_api_token else ""
        return {
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://www.vidu.com",
            "Referer": "https://www.vidu.com/",
        }

    @staticmethod
    def _image_to_base64_url(file_path: str) -> str:
        """Read a local image, compress it to JPEG using PIL in memory, and return a data-URI (base64) string."""
        import base64
        import io
        from PIL import Image
        
        path = Path(file_path)
        img = Image.open(path)
        
        # 转换 RGBA/P 等模式为 RGB（因为 JPEG 不支持 Alpha 透明通道）
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        buffer = io.BytesIO()
        # 压缩为 JPEG 格式，质量为 85 （能保证极高画质的同时，文件体积缩小 90% 左右，缩减至 150KB-300KB 左右）
        img.save(buffer, format="JPEG", quality=85)
        raw = buffer.getvalue()
        
        b64 = base64.b64encode(raw).decode()
        return f"data:image/jpeg;base64,{b64}"

    def generate_video(
        self,
        task_id: str,
        scene_id: str,
        image_path: str,
        video_prompt: str | None = None,
        duration: int = 5,
    ) -> dict[str, Any]:
        if not settings.enable_paid_api:
            raise ValueError(
                "付费 API 调用未启用，请在环境变量中设置 ENABLE_PAID_API=true 开启。"
            )

        if not settings.vidu_api_token:
            raise ValueError("未找到 VIDU_API_TOKEN，请在环境变量中配置。")

        try:
            # 1. 解析本地图片路径
            backend_dir = Path(__file__).resolve().parents[2]
            resolved_image_path = image_path
            if not Path(image_path).is_absolute():
                clean_path = image_path.replace("backend/", "")
                resolved_image_path = str(backend_dir / clean_path)

            # 2. 将图片编码为 base64 data-URI
            image_data_uri = self._image_to_base64_url(resolved_image_path)

            # 3. 提交图生视频任务
            submit_url = f"{self.API_BASE}/img2video"
            payload: dict[str, Any] = {
                "model": settings.vidu_model,
                "images": [image_data_uri],
                "prompt": video_prompt
                or "smooth movement, high quality, consistent with character",
                "duration": duration,
                "resolution": "1080p",
                "movement_amplitude": "auto",
            }

            with httpx.Client(timeout=60.0) as client:
                res = client.post(
                    submit_url, json=payload, headers=self._get_headers()
                )
                res.raise_for_status()
                res_data = res.json()

                vidu_task_id = res_data.get("id") or res_data.get("task_id")
                if not vidu_task_id:
                    raise ValueError(
                        f"Vidu API 未返回 task_id: {res_data}"
                    )

                # 4. 轮询任务状态
                poll_url = f"{self.API_BASE}/tasks/{vidu_task_id}/creations"
                video_url: str | None = None
                max_retries = 120  # 最多等 10 分钟 (120 * 5s)

                for _ in range(max_retries):
                    time.sleep(5)
                    poll_res = client.get(poll_url, headers=self._get_headers())
                    poll_res.raise_for_status()
                    poll_data = poll_res.json()

                    state = poll_data.get("state", "")

                    if state == "success":
                        creations = poll_data.get("creations", [])
                        if creations:
                            video_url = creations[0].get("url")
                        break
                    elif state == "failed":
                        err_msg = poll_data.get("err_code", "unknown error")
                        raise ValueError(
                            f"Vidu 视频生成失败: {err_msg}"
                        )
                    # state 为 processing / pending 则继续等待

                if not video_url:
                    raise TimeoutError("轮询 Vidu 视频生成任务超时。")

                # 5. 创建目标目录
                dest_dir = backend_dir / "assets" / task_id / "videos"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / f"{scene_id}.mp4"

                # 6. 下载生成的视频片段
                vid_res = client.get(video_url, timeout=120.0)
                vid_res.raise_for_status()
                with open(dest_path, "wb") as f:
                    f.write(vid_res.content)

                relative_video_path = (
                    f"backend/assets/{task_id}/videos/{scene_id}.mp4"
                )

                return {
                    "source": "vidu",
                    "provider": "vidu_video",
                    "task_id": task_id,
                    "scene_id": scene_id,
                    "asset_type": "video_clip",
                    "asset_path": relative_video_path,
                    "image_path": image_path,
                    "video_prompt": video_prompt,
                    "duration": duration,
                    "status": "success",
                    "message": "Video generated successfully via Vidu.",
                }
        except Exception as e:
            raise RuntimeError(
                f"ViduVideoService failed for {scene_id}: {e}"
            ) from e
