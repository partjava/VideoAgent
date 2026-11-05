import json
from hashlib import sha256
import base64
from io import BytesIO
import os
import time
import httpx
from pathlib import Path
from typing import Any
from config import settings
from services.model_config import model_config
from services.video.base import BaseVideoService, VideoRenderRejected


SILENT_OUTPUT_MODELS = frozenset({
    "wan2.2-i2v-flash", "wan2.2-i2v-plus", "wanx2.1-i2v-turbo", "wanx2.1-i2v-plus",
})
WAN3_MODELS = frozenset({"wan3.0-video", "wan3.0-video-prime"})
FLASH_MODEL = "wan2.6-i2v-flash"


class WanVideoService(BaseVideoService):
    @property
    def supports_negative_prompt(self):
        return self.model == FLASH_MODEL or self.model in SILENT_OUTPUT_MODELS

    def __init__(self, model=None, storage_root=None):
        profile = model_config("video", settings)
        self.model = model or profile["model"]
        self.api_key = profile["key"]
        self.api_url = profile["url"]
        self.storage_root = Path(storage_root) if storage_root is not None else Path(__file__).resolve().parents[2]

    @property
    def api_base(self):
        return self.api_url

    def _upload_file(self, file_path: str) -> str:
        upload_url = f"{self.api_base}/api/v1/files"
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        # 1. 上传本地文件，获取 file_id
        with open(file_path, "rb") as f:
            data = {
                "purpose": "file-extract"
            }
            
            with httpx.Client(timeout=60.0) as client:
                res_data = None
                max_retries = 5
                retry_delay = 2.0
                
                # 尝试上传文件
                for attempt in range(max_retries):
                    f.seek(0)
                    files = {
                        "files": (os.path.basename(file_path), f, "image/png")
                    }
                    try:
                        res = client.post(upload_url, headers=headers, files=files, data=data)
                        if res.status_code == 429:
                            print(f"[WanVideoService] Received 429 during upload, retrying in {retry_delay}s... (Attempt {attempt+1}/{max_retries})")
                            time.sleep(retry_delay)
                            retry_delay *= 2
                            continue
                        res.raise_for_status()
                        res_data = res.json()
                        break
                    except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                        if attempt == max_retries - 1:
                            raise
                        print(f"[WanVideoService] Upload network warning: {exc}, retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
                        retry_delay *= 1.5
                
                if not res_data:
                    raise ValueError(f"File upload failed after retries.")

                uploaded_files = res_data.get("data", {}).get("uploaded_files", [])
                if not uploaded_files:
                    raise ValueError(f"File upload failed to return uploaded_files metadata: {res_data}")
                
                file_id = uploaded_files[0].get("file_id")
                if not file_id:
                    raise ValueError(f"File upload response missing file_id: {res_data}")

                # 2. 获取文件详情，拿到临时下载 URL
                detail_url = f"{self.api_base}/api/v1/files/{file_id}"
                detail_data = None
                retry_delay = 2.0
                
                for attempt in range(max_retries):
                    try:
                        detail_res = client.get(detail_url, headers=headers)
                        if detail_res.status_code == 429:
                            print(f"[WanVideoService] Received 429 during detail check, retrying in {retry_delay}s... (Attempt {attempt+1}/{max_retries})")
                            time.sleep(retry_delay)
                            retry_delay *= 2
                            continue
                        detail_res.raise_for_status()
                        detail_data = detail_res.json()
                        break
                    except (httpx.TransportError, httpx.HTTPStatusError) as net_err:
                        if attempt == max_retries - 1:
                            raise
                        print(f"[WanVideoService] Detail check network warning: {net_err}, retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
                        retry_delay *= 1.5
                
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
        ratio: str = "9:16",
        negative_prompt: str | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_paid_api:
            raise ValueError("付费 API 调用未启用，请在环境变量中设置 ENABLE_PAID_API=true 开启。")

        if not self.api_key:
            raise ValueError("未找到 DASHSCOPE_API_KEY，请在环境变量中配置。")

        flash = self.model == FLASH_MODEL
        wan3 = self.model in WAN3_MODELS
        if not (flash or wan3 or self.model in SILENT_OUTPUT_MODELS):
            raise ValueError(
                f"当前模型 {self.model} 无法确认支持无声生成，未提交任务。"
                "请在模型设置中选择 wan2.6-i2v-flash、wan3.0-video 或 wan3.0-video-prime。"
            )
        if flash and not 2 <= duration <= 15:
            raise ValueError("wan2.6-i2v-flash 无声生成支持 2–15 秒。")
        if wan3 and not 2 <= duration <= 30:
            raise ValueError(f"{self.model} 无声生成支持 2–30 秒。")

        try:
            # 1. 如果是相对路径，则按 backend 根目录解析本地图片路径
            backend_dir = self.storage_root
            resolved_image_path = image_path
            if not Path(image_path).is_absolute():
                # 如果数据库相对路径以 backend/ 开头，则先去掉这段前缀
                clean_path = image_path.replace("backend/", "")
                resolved_image_path = str(backend_dir / clean_path)

            fingerprint = sha256(Path(resolved_image_path).read_bytes())
            fingerprint.update(json.dumps([self.model, video_prompt, duration, ratio]).encode())
            if negative_prompt and self.supports_negative_prompt:
                fingerprint.update(negative_prompt.encode("utf-8"))
            job_dir = backend_dir / "assets" / task_id / "video_jobs"
            job_dir.mkdir(parents=True, exist_ok=True)
            job = job_dir / f"{scene_id}-{fingerprint.hexdigest()}.json"
            api_task_id = None
            if job.is_file():
                api_task_id = json.loads(job.read_text(encoding="utf-8")).get("task_id")
                if not api_task_id:
                    raise RuntimeError("Previous Wan submission is unconfirmed. Check the provider console before retrying.")
            # Both API versions use the first frame to determine the aspect ratio.
            from tempfile import TemporaryDirectory
            from services.media_quality import write_aspect_frame
            with TemporaryDirectory(prefix="wan_frame_") as tmp:
                normalized = Path(tmp) / "first_frame.png"
                write_aspect_frame(resolved_image_path, normalized, ratio)
                if flash or wan3:
                    from PIL import Image
                    buffer = BytesIO()
                    with Image.open(normalized) as frame:
                        frame.convert("RGB").save(buffer, format="JPEG", quality=92)
                    first_frame = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
                elif api_task_id:
                    first_frame = None
                else:
                    first_frame = self._upload_file(str(normalized))

            # 3. 提交视频生成任务
            submit_url = f"{self.api_base}/api/v1/services/aigc/video-generation/video-synthesis"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable"
            }
            payload = {
                "model": self.model,
                "input": {
                    "prompt": video_prompt or "smooth movement, high quality, consistent with character",
                    "media": [
                        {
                            "type": "first_frame",
                            "url": first_frame
                        }
                    ]
                },
                "parameters": {
                    "resolution": "720P",
                    "duration": duration
                }
            }

            if flash or wan3:
                payload["parameters"]["audio"] = False
            if negative_prompt and self.supports_negative_prompt:
                payload["input"]["negative_prompt"] = negative_prompt

            if flash:
                payload["input"].pop("media")
                payload["input"]["img_url"] = first_frame
                payload["parameters"]["shot_type"] = "single"

            if wan3:
                payload["parameters"].update({"ratio": ratio, "prompt_extend": False})

            with httpx.Client(timeout=60.0) as client:
                if not api_task_id:
                    # Claim this exact render before POST. An ambiguous network failure must not cause a second POST.
                    with job.open("x", encoding="utf-8") as handle:
                        json.dump({"task_id": None}, handle)
                    res = client.post(submit_url, json=payload, headers=headers)
                    if 400 <= res.status_code < 500:
                        job.unlink(missing_ok=True)
                    res.raise_for_status()
                    api_task_id = res.json().get("output", {}).get("task_id")
                    if not api_task_id:
                        raise RuntimeError("Wan did not return a task ID. Check provider status before retrying.")
                    staged_job = job.with_suffix(".tmp")
                    staged_job.write_text(json.dumps({"task_id": api_task_id}), encoding="utf-8")
                    staged_job.replace(job)

                # 4. 轮询任务状态
                status_url = f"{self.api_base}/api/v1/tasks/{api_task_id}"
                status_headers = {
                    "Authorization": f"Bearer {self.api_key}"
                }
                
                video_url = None
                max_retries = 180 if wan3 else 100
                for attempt in range(max_retries):
                    time.sleep(5)
                    try:
                        task_res = client.get(status_url, headers=status_headers)
                        if task_res.status_code == 429:
                            time.sleep(3)
                            continue
                        task_res.raise_for_status()
                        task_data = task_res.json()
                    except (httpx.TransportError, httpx.HTTPError) as net_err:
                        print(f"[WanVideoService] Polling warning on attempt {attempt+1}: {net_err}, retrying...")
                        continue
                    
                    output = task_data.get("output", {})
                    task_status = output.get("task_status")
                    
                    if task_status == "SUCCEEDED":
                        video_url = output.get("video_url")
                        break
                    elif task_status in {"FAILED", "CANCELED"}:
                        job.unlink(missing_ok=True)
                        raise VideoRenderRejected(f"Video task failed: {output.get('message', 'unknown error')}")

                if not video_url:
                    raise TimeoutError("Polling video synthesis task timed out.")

                # 5. 创建目标目录
                dest_dir = backend_dir / "assets" / task_id / "videos"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / f"{scene_id}.mp4"

                # 6. 下载生成的视频片段（带重试保护）
                max_dl_retries = 3
                for dl_attempt in range(max_dl_retries):
                    try:
                        vid_res = client.get(video_url, timeout=120.0)
                        vid_res.raise_for_status()
                        with open(dest_path, "wb") as f:
                            f.write(vid_res.content)
                        break
                    except (httpx.TransportError, httpx.HTTPError) as dl_err:
                        if dl_attempt == max_dl_retries - 1:
                            raise
                        print(f"[WanVideoService] Video download warning (attempt {dl_attempt+1}): {dl_err}, retrying...")
                        time.sleep(3)

                relative_video_path = f"backend/assets/{task_id}/videos/{scene_id}.mp4"

                return {
                    "source": "wan",
                    "provider_task_id": api_task_id,
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
