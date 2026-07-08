from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import httpx
import websocket

from services.comfyui.config_store import build_comfyui_config


# 全局进度存储：prompt_id → {"value": int, "max": int, "stage": str, "done": bool, "error": str|None}
_PROGRESS: dict[str, dict[str, Any]] = {}
_PROGRESS_LOCK = threading.Lock()


def _ws_progress_listener(ws_url: str, prompt_id: str) -> None:
    """在后台线程中连接 ComfyUI WebSocket，监听进度并写入 _PROGRESS。"""
    try:
        ws = websocket.create_connection(ws_url, timeout=30)
    except Exception as e:
        # WS 连接失败不影响主流程，只是没进度而已
        print(f"[ComfyUI] WS progress connection failed: {e}")
        return

    try:
        while True:
            raw = ws.recv()
            if not raw:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            import json
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")
            data = msg.get("data", {})

            if msg_type == "progress" and data.get("prompt_id") == prompt_id:
                with _PROGRESS_LOCK:
                    _PROGRESS[prompt_id] = {
                        "value": data.get("value", 0),
                        "max": data.get("max", 100),
                        "stage": "generating",
                        "done": False,
                        "error": None,
                    }

            elif msg_type == "executing" and data.get("node") is None and data.get("prompt_id") == prompt_id:
                with _PROGRESS_LOCK:
                    p = _PROGRESS.get(prompt_id, {})
                    p["stage"] = "done"
                    p["done"] = True
                    _PROGRESS[prompt_id] = p
                break

            elif msg_type == "execution_error" and data.get("prompt_id") == prompt_id:
                with _PROGRESS_LOCK:
                    _PROGRESS[prompt_id] = {
                        "value": 0,
                        "max": 0,
                        "stage": "error",
                        "done": True,
                        "error": str(data.get("exception_message", "Unknown error")),
                    }
                break
    except Exception as e:
        print(f"[ComfyUI] WS progress listener error: {e}")
    finally:
        try:
            ws.close()
        except Exception:
            pass


def get_progress(prompt_id: str) -> dict[str, Any] | None:
    with _PROGRESS_LOCK:
        return _PROGRESS.get(prompt_id)


class ComfyUIClient:
    def __init__(self, base_url: str | None = None, timeout_seconds: int | None = None) -> None:
        config = build_comfyui_config()
        self.base_url = (base_url or config["comfyui_base_url"]).rstrip("/")
        self.timeout_seconds = int(timeout_seconds or config["comfyui_timeout_seconds"])

    def check(self) -> dict[str, Any]:
        with httpx.Client(timeout=min(self.timeout_seconds, 30)) as client:
            response = client.get(f"{self.base_url}/system_stats")
            response.raise_for_status()
            return response.json()

    def queue_prompt(self, workflow: dict[str, Any]) -> str:
        with httpx.Client(timeout=min(self.timeout_seconds, 60)) as client:
            response = client.post(f"{self.base_url}/prompt", json={"prompt": workflow})
            response.raise_for_status()
            data = response.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ValueError(f"ComfyUI did not return prompt_id: {data}")

        # 启动 WebSocket 监听进度（后台线程）
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        with _PROGRESS_LOCK:
            _PROGRESS[prompt_id] = {"value": 0, "max": 0, "stage": "queued", "done": False, "error": None}
        t = threading.Thread(target=_ws_progress_listener, args=(ws_url, prompt_id), daemon=True)
        t.start()

        return str(prompt_id)

    def wait_for_history(self, prompt_id: str) -> dict[str, Any]:
        deadline = time.time() + self.timeout_seconds
        with httpx.Client(timeout=30.0) as client:
            while time.time() < deadline:
                response = client.get(f"{self.base_url}/history/{prompt_id}")
                response.raise_for_status()
                history = response.json()
                if history:
                    # WS 进度回退：如果 WS 没连上，这里标记完成
                    with _PROGRESS_LOCK:
                        p = _PROGRESS.get(prompt_id, {})
                        if not p.get("done"):
                            p["stage"] = "done"
                            p["done"] = True
                            _PROGRESS[prompt_id] = p
                    return history
                time.sleep(2)
        raise TimeoutError(f"Timed out waiting for ComfyUI prompt: {prompt_id}")

    def download_output(self, file_info: dict[str, Any], destination: Path) -> None:
        params = {
            "filename": file_info.get("filename"),
            "subfolder": file_info.get("subfolder", ""),
            "type": file_info.get("type", "output"),
        }
        if not params["filename"]:
            raise ValueError("ComfyUI output file is missing filename")

        destination.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=min(self.timeout_seconds, 120)) as client:
            response = client.get(f"{self.base_url}/view", params=params)
            response.raise_for_status()
            destination.write_bytes(response.content)

    def upload_image(self, image_path: Path) -> str:
        if not image_path.exists():
            raise FileNotFoundError(f"Input image not found: {image_path}")
        with httpx.Client(timeout=min(self.timeout_seconds, 120)) as client:
            with image_path.open("rb") as image_file:
                response = client.post(
                    f"{self.base_url}/upload/image",
                    files={"image": (image_path.name, image_file, "image/png")},
                    data={"overwrite": "true"},
                )
            response.raise_for_status()
            data = response.json()
        return str(data.get("name") or image_path.name)
