import os
import time
import httpx
from pathlib import Path
from typing import Any
from config import settings
from services.video.base import BaseVideoService


class WanVideoService(BaseVideoService):
    def _upload_file(self, file_path: str) -> str:
        upload_url = "https://dashscope.aliyuncs.com/api/v1/files"
        headers = {
            "Authorization": f"Bearer {settings.dashscope_api_key}"
        }
        
        # 1. 上传本地文件，获取 file_id
        with open(file_path, "rb") as f:
            data = {
                "purpose": "file-extract"
            }
            
            with httpx.Client(timeout=45.0) as client:
                res_data = None
                max_retries = 5
                retry_delay = 2.0
                
                # 尝试上传文件
                for attempt in range(max_retries):
                    f.seek(0)
                    files = {
                        "files": (os.path.basename(file_path), f, "image/png")
                    }
                    res = client.post(upload_url, headers=headers, files=files, data=data)
                    if res.status_code == 429:
                        print(f"[WanVideoService] Received 429 during upload, retrying in {retry_delay}s... (Attempt {attempt+1}/{max_retries})")
                        time.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    res.raise_for_status()
                    res_data = res.json()
                    break
                
                if not res_data:
                    raise ValueError(f"File upload failed after retries.")

                uploaded_files = res_data.get("data", {}).get("uploaded_files", [])
                if not uploaded_files:
                    raise ValueError(f"File upload failed to return uploaded_files metadata: {res_data}")
                
                file_id = uploaded_files[0].get("file_id")
                if not file_id:
                    raise ValueError(f"File upload response missing file_id: {res_data}")

                # 2. 获取文件详情，拿到临时下载 URL
                detail_url = f"https://dashscope.aliyuncs.com/api/v1/files/{file_id}"
                detail_data = None
                retry_delay = 2.0
                
                for attempt in range(max_retries):
                    detail_res = client.get(detail_url, headers=headers)
                    if detail_res.status_code == 429:
                        print(f"[WanVideoService] Received 429 during detail check, retrying in {retry_delay}s... (Attempt {attempt+1}/{max_retries})")
                        time.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    detail_res.raise_for_status()
                    detail_data = detail_res.json()
                    break
                
                if not detail_data:
                    raise ValueError(f"Failed to query file details after retries.")
                
                file_url = detail_data.get("data", {}).get("url")
                if not file_url:
                    raise ValueError(f"Failed to retrieve temporary URL for file {file_id}: {detail_data}")
                return file_url

    def generate_video(
        self,
        task_id: str,
        scene_id: str,
        image_path: str,
        video_prompt: str | None = None,
        duration: int = 5,
    ) -> dict[str, Any]:
        if not settings.enable_paid_api:
            raise ValueError("付费 API 调用未启用，请在环境变量中设置 ENABLE_PAID_API=true 开启。")

        if not settings.dashscope_api_key:
            raise ValueError("未找到 DASHSCOPE_API_KEY，请在环境变量中配置。")

        try:
            # 1. 如果是相对路径，则按 backend 根目录解析本地图片路径
            backend_dir = Path(__file__).resolve().parents[2]
            resolved_image_path = image_path
            if not Path(image_path).is_absolute():
                # 如果数据库相对路径以 backend/ 开头，则先去掉这段前缀
                clean_path = image_path.replace("backend/", "")
                resolved_image_path = str(backend_dir / clean_path)

            # 2. 上传本地首帧图片，获取公网 URL
            public_image_url = self._upload_file(resolved_image_path)

            # 3. 提交视频生成任务
            submit_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"
            headers = {
                "Authorization": f"Bearer {settings.dashscope_api_key}",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable"
            }
            payload = {
                "model": settings.wan_video_model,
                "input": {
                    "prompt": video_prompt or "smooth movement, high quality, consistent with character",
                    "media": [
                        {
                            "type": "first_frame",
                            "url": public_image_url
                        }
                    ]
                },
                "parameters": {
                    "resolution": "720P",
                    "ratio": "9:16"
                }
            }

            with httpx.Client(timeout=30.0) as client:
                res = client.post(submit_url, json=payload, headers=headers)
                res.raise_for_status()
                res_data = res.json()
                
                api_task_id = res_data.get("output", {}).get("task_id")
                if not api_task_id:
                    raise ValueError(f"Failed to submit video task: {res_data}")

                # 4. 轮询任务状态
                status_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{api_task_id}"
                status_headers = {
                    "Authorization": f"Bearer {settings.dashscope_api_key}"
                }
                
                video_url = None
                max_retries = 100
                for _ in range(max_retries):
                    time.sleep(5)
                    task_res = client.get(status_url, headers=status_headers)
                    task_res.raise_for_status()
                    task_data = task_res.json()
                    
                    output = task_data.get("output", {})
                    task_status = output.get("task_status")
                    
                    if task_status == "SUCCEEDED":
                        video_url = output.get("video_url")
                        break
                    elif task_status in ["FAILED", "UNKNOWN"]:
                        raise ValueError(f"Video task failed: {output.get('message', 'unknown error')}")

                if not video_url:
                    raise TimeoutError("Polling video synthesis task timed out.")

                # 5. 创建目标目录
                dest_dir = backend_dir / "assets" / task_id / "videos"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / f"{scene_id}.mp4"

                # 6. 下载生成的视频片段
                vid_res = client.get(video_url, timeout=120.0)
                vid_res.raise_for_status()
                with open(dest_path, "wb") as f:
                    f.write(vid_res.content)

                relative_video_path = f"backend/assets/{task_id}/videos/{scene_id}.mp4"

                return {
                    "source": "wan",
                    "provider": "wan_video",
                    "task_id": task_id,
                    "scene_id": scene_id,
                    "asset_type": "video_clip",
                    "asset_path": relative_video_path,
                    "image_path": image_path,
                    "video_prompt": video_prompt,
                    "duration": duration,
                    "status": "success",
                    "message": "Video generated successfully via Wan Video.",
                }
        except Exception as e:
            raise RuntimeError(f"WanVideoService failed for {scene_id}: {e}") from e
