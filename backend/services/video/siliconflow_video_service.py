import base64
import json
import time
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from config import settings
from services.model_config import model_config, siliconflow_v1_base
from services.video.base import BaseVideoService, VideoRenderRejected


class SiliconFlowVideoService(BaseVideoService):
    """硅基流动（SiliconFlow /v1/video/submit + /v1/video/status）异步视频生成适配器。"""

    supports_last_frame = True
    supports_negative_prompt = False
    supports_resolution = True

    def __init__(self, model: str | None = None, storage_root: Path | None = None):
        profile = model_config("video", settings)
        self.model = str(model or profile.get("model") or "MiniMaxAI/MiniMax-H3").strip()
        self.api_key = str(profile.get("key") or "").strip()
        self.api_url = str(profile.get("url") or "https://api.siliconflow.cn/v1").strip()
        self.storage_root = Path(storage_root) if storage_root is not None else Path(__file__).resolve().parents[2]

    @property
    def api_base(self) -> str:
        return siliconflow_v1_base(self.api_url)

    def _resolve_local(self, raw_path: str) -> Path:
        p = Path(raw_path)
        if p.is_absolute():
            return p
        return self.storage_root / str(raw_path).replace("backend/", "", 1)

    def _encode_frame_uri(self, raw_path: str, ratio: str, tmp_name: str, tmp_dir: Path) -> str:
        from PIL import Image
        from services.media_quality import write_aspect_frame

        source = self._resolve_local(raw_path)
        normalized = tmp_dir / tmp_name
        write_aspect_frame(source, normalized, ratio)
        buffer = BytesIO()
        with Image.open(normalized) as frame:
            frame.convert("RGB").save(buffer, format="JPEG", quality=92)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    def _render_paths(
        self,
        task_id: str,
        scene_id: str,
        image_path: str,
        video_prompt: str | None,
        duration: int,
        ratio: str,
        resolution: str,
        last_frame_path: str | None = None,
    ) -> tuple[Path, Path]:
        source = self._resolve_local(image_path)
        fp = sha256(source.read_bytes())
        fp.update(json.dumps([self.model, video_prompt, duration, ratio, resolution]).encode("utf-8"))
        if last_frame_path:
            last_src = self._resolve_local(last_frame_path)
            if last_src.is_file():
                fp.update(sha256(last_src.read_bytes()).digest())
        job = self.storage_root / "assets" / task_id / "video_jobs" / f"sf-{scene_id}-{fp.hexdigest()}.json"
        return source, job

    def has_pending_render(
        self,
        task_id: str,
        scene_id: str,
        image_path: str,
        video_prompt: str | None = None,
        duration: int = 5,
        ratio: str = "9:16",
        resolution: str | None = None,
        last_frame_path: str | None = None,
    ) -> bool:
        res_opt = str(resolution or getattr(settings, "siliconflow_video_resolution", "768p") or "768p")
        _, job = self._render_paths(task_id, scene_id, image_path, video_prompt, duration, ratio, res_opt, last_frame_path)
        return job.is_file()

    def generate_video(
        self,
        task_id: str,
        scene_id: str,
        image_path: str,
        video_prompt: str | None = None,
        duration: int = 5,
        ratio: str = "9:16",
        last_frame_path: str | None = None,
        resolution: str | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_paid_api:
            raise ValueError("付费 API 调用未启用，请在环境变量中设置 ENABLE_PAID_API=true 开启。")
        if not self.api_key:
            raise ValueError("未配置硅基流动视频 API Key，请在「API 配置」页面填写。")

        raw_res = str(resolution or getattr(settings, "siliconflow_video_resolution", "768p") or "768p").strip().lower()
        res_opt = raw_res if raw_res in {"480p", "768p"} else "768p"
        min_dur = 5 if "minimax" in self.model.lower() else 3
        api_duration = max(min_dur, min(15, int(duration)))

        try:
            from tempfile import TemporaryDirectory

            _, job = self._render_paths(
                task_id, scene_id, image_path, video_prompt, api_duration, ratio, res_opt, last_frame_path
            )
            job.parent.mkdir(parents=True, exist_ok=True)
            request_id = None
            if job.is_file():
                request_id = json.loads(job.read_text(encoding="utf-8")).get("task_id")
                if not request_id:
                    raise RuntimeError("Previous SiliconFlow video submission is unconfirmed. Check console before retrying.")

            images_payload: list[dict[str, str]] = []
            if not request_id:
                with TemporaryDirectory(prefix="sf_frame_") as tmp:
                    tmp_dir = Path(tmp)
                    first_uri = self._encode_frame_uri(image_path, ratio, "first.png", tmp_dir)
                    images_payload.append({"url": first_uri, "role": "first_frame"})
                    if last_frame_path and self._resolve_local(last_frame_path).is_file():
                        last_uri = self._encode_frame_uri(last_frame_path, ratio, "last.png", tmp_dir)
                        images_payload.append({"url": last_uri, "role": "last_frame"})

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            submit_url = f"{self.api_base}/video/submit"
            status_url = f"{self.api_base}/video/status"

            with httpx.Client(timeout=60.0) as client:
                if not request_id:
                    payload = {
                        "model": self.model,
                        "prompt": video_prompt or "smooth cinematic movement, consistent character appearance",
                        "duration": api_duration,
                        "resolution": res_opt,
                        "ratio": ratio,
                        "images": images_payload,
                    }
                    with job.open("x", encoding="utf-8") as handle:
                        json.dump({"task_id": None}, handle)
                    res = client.post(submit_url, json=payload, headers=headers)
                    if res.status_code == 429:
                        time.sleep(3)
                        res = client.post(submit_url, json=payload, headers=headers)
                    if 400 <= res.status_code < 500:
                        job.unlink(missing_ok=True)
                        body_txt = res.text[:300]
                        if any(k in body_txt.lower() for k in ("sensitive", "safety", "content", "policy", "审核")):
                            raise VideoRenderRejected(f"SiliconFlow 内容审核拒绝: HTTP {res.status_code} {body_txt}")
                        raise RuntimeError(f"SiliconFlow 提交参数被拒绝: HTTP {res.status_code} {body_txt}")
                    res.raise_for_status()
                    request_id = res.json().get("requestId")
                    if not request_id:
                        job.unlink(missing_ok=True)
                        raise RuntimeError(f"SiliconFlow 未返回 requestId: {res.text[:300]}")
                    staged = job.with_suffix(".tmp")
                    staged.write_text(json.dumps({"task_id": request_id}), encoding="utf-8")
                    staged.replace(job)

                video_url: str | None = None
                for attempt in range(180):
                    time.sleep(5)
                    try:
                        poll_res = client.post(status_url, json={"requestId": request_id}, headers=headers)
                        if poll_res.status_code == 429:
                            time.sleep(3)
                            continue
                        poll_res.raise_for_status()
                        poll_data = poll_res.json()
                    except (httpx.TransportError, httpx.HTTPError) as net_err:
                        print(f"[SiliconFlowVideoService] Polling warning (attempt {attempt + 1}): {net_err}", flush=True)
                        continue

                    status = str(poll_data.get("status") or "")
                    if status == "Succeed":
                        results = poll_data.get("results") or {}
                        videos = results.get("videos") or []
                        video_url = (
                            videos[0].get("url")
                            if videos and isinstance(videos[0], dict)
                            else results.get("url")
                        )
                        break
                    if status == "Failed":
                        job.unlink(missing_ok=True)
                        raise VideoRenderRejected(f"SiliconFlow 视频生成失败: {poll_data.get('reason') or 'unknown error'}")

                if not video_url:
                    raise TimeoutError("Polling SiliconFlow video task timed out.")

                dest_dir = self.storage_root / "assets" / task_id / "videos"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / f"{scene_id}.mp4"
                for dl_attempt in range(3):
                    try:
                        vid_res = client.get(video_url, timeout=120.0)
                        vid_res.raise_for_status()
                        dest_path.write_bytes(vid_res.content)
                        job.unlink(missing_ok=True)
                        break
                    except (httpx.TransportError, httpx.HTTPError):
                        if dl_attempt == 2:
                            raise
                        time.sleep(3)

            rel_path = f"backend/assets/{task_id}/videos/{scene_id}.mp4"
            return {
                "source": "siliconflow",
                "provider_task_id": request_id,
                "provider": "siliconflow_video",
                "task_id": task_id,
                "scene_id": scene_id,
                "asset_type": "video_clip",
                "asset_path": rel_path,
                "image_path": image_path,
                "video_prompt": video_prompt,
                "duration": api_duration,
                "status": "success",
                "message": f"Video generated successfully via SiliconFlow ({self.model}).",
            }
        except VideoRenderRejected:
            raise
        except Exception as exc:
            raise RuntimeError(f"SiliconFlowVideoService failed for {scene_id}: {exc}") from exc
