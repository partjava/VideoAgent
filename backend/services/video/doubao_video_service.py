import base64
import time
import httpx
from pathlib import Path
from typing import Any

from config import settings
from services.video.base import BaseVideoService


class DoubaoVideoService(BaseVideoService):
    """Doubao Video Generation (Volcengine Ark platform) image-to-video service."""

    API_BASE = "https://ark.cn-beijing.volces.com/api/v3"

    def _get_headers(self) -> dict[str, str]:
        key = settings.volcengine_api_key.strip() if settings.volcengine_api_key else ""
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _image_to_base64_url(file_path: str) -> str:
        """Read a local image, compress it to JPEG using PIL in memory, and return a data-URI (base64) string."""
        import io
        from PIL import Image
        
        path = Path(file_path)
        img = Image.open(path)
        
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
            
        buffer = io.BytesIO()
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

        if not settings.volcengine_api_key:
            raise ValueError("未找到 VOLCENGINE_API_KEY，请在环境变量中配置。")

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
            submit_url = f"{self.API_BASE}/contents/generations/tasks"
            payload: dict[str, Any] = {
                "model": settings.volcengine_model,
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_data_uri
                        },
                        "role": "first_frame"
                    },
                    {
                        "type": "text",
                        "text": video_prompt or "smooth movement, high quality, consistent with character"
                    }
                ],
                "resolution": "1080p",
                "ratio": "9:16",
                "duration": duration,
                "watermark": False
            }

            with httpx.Client(timeout=60.0) as client:
                res = client.post(
                    submit_url, json=payload, headers=self._get_headers()
                )
                res.raise_for_status()
                res_data = res.json()

                doubao_task_id = res_data.get("id")
                if not doubao_task_id:
                    raise ValueError(
                        f"Doubao API 未返回 task_id: {res_data}"
                    )

                # 4. 轮询任务状态
                poll_url = f"{self.API_BASE}/contents/generations/tasks/{doubao_task_id}"
                video_url: str | None = None
                max_retries = 120  # 最多等 10 分钟 (120 * 5s)

                for _ in range(max_retries):
                    time.sleep(5)
                    poll_res = client.get(poll_url, headers=self._get_headers())
                    poll_res.raise_for_status()
                    poll_data = poll_res.json()

                    state = poll_data.get("status", "")

                    if state == "succeeded":
                        video_url = poll_data.get("content", {}).get("video_url")
                        break
                    elif state == "failed":
                        err = poll_data.get("error", {})
                        err_msg = err.get("message", "unknown error")
                        raise ValueError(
                            f"Doubao 视频生成失败: {err_msg}"
                        )
                    # state 为 queued / running 则继续等待

                if not video_url:
                    raise TimeoutError("轮询 Doubao 视频生成任务超时。")

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
                    "source": "doubao",
                    "provider": "doubao_video",
                    "task_id": task_id,
                    "scene_id": scene_id,
                    "asset_type": "video_clip",
                    "asset_path": relative_video_path,
                    "image_path": image_path,
                    "video_prompt": video_prompt,
                    "duration": duration,
                    "status": "success",
                    "message": "Video generated successfully via Doubao.",
                }
        except Exception as e:
            raise RuntimeError(
                f"DoubaoVideoService failed for {scene_id}: {e}"
            ) from e
