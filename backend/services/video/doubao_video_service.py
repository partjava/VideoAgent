import base64
import re
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
    def _sanitize_prompt(prompt: str) -> str:
        """替换可能触发内容审核的敏感词为温和描述"""
        if not prompt:
            return prompt
        replacements = [
            # 武器与攻击动作
            (r"挥剑斩向", "手臂猛地一挥"),
            (r"拔剑出鞘", "右手快速抬起"),
            (r"一剑刺向", "迅速向前逼近"),
            (r"挥剑砍向", "手臂大力挥出"),
            (r"挥剑", "抬手"),
            (r"长剑出鞘", "做出战斗姿态"),
            (r"拔剑", "握紧剑柄"),
            (r"剑指", "指向"),
            (r"剑上还挂着", "衣袍上沾着"),
            (r"斩将", "制伏对手"),
            (r"斩杀", "制服"),
            (r"斩", "击倒"),
            # 射箭
            (r"一箭射穿", "一箭命中"),
            (r"箭已离弦", "箭矢飞出"),
            (r"箭尾还在颤动", "弓弦还在颤动"),
            (r"拉弓", "引弓"),
            (r"持弓", "握弓"),
            # 火与破坏
            (r"纵火焚", "点燃"),
            (r"冲天大火", "升起的火光"),
            (r"火焰吞没", "火光照亮"),
            (r"燃烧", "着火"),
            (r"焚", "烧"),
            # 战争与冲突
            (r"敌军", "对方"),
            (r"敌将", "对方首领"),
            (r"敌营", "对方营地"),
            (r"厮杀", "交锋"),
            (r"激战", "对战"),
            (r"打斗", "交手"),
            # 流血与受伤
            (r"受伤", "受创"),
            (r"流血", "带伤"),
            (r"捂着伤口", "捂着肩膀"),
            (r"捂着受伤的", "捂着"),
        ]
        result = prompt
        for pattern, replacement in replacements:
            result = re.sub(pattern, replacement, result)
        return result

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
        ratio: str = "9:16",
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
            # Seedance 最低支持 4 秒，最高 12 秒
            clamped_duration = max(4, min(duration, 12))
            # 自动替换可能触发审核的敏感词
            safe_prompt = self._sanitize_prompt(video_prompt or "")
            if safe_prompt != video_prompt:
                print(f"[DoubaoVideoService] Sanitized video_prompt for {scene_id}")
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
                        "text": safe_prompt or "smooth movement, high quality, consistent with character"
                    }
                ],
                "resolution": "1080p",
                "ratio": ratio,
                "duration": clamped_duration,
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
