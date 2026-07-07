import os
import httpx
from pathlib import Path
from typing import Any
from config import settings
from services.image.base import BaseImageService


class QwenImageService(BaseImageService):
    def generate_image(
        self,
        task_id: str,
        scene_id: str,
        image_prompt: str,
        negative_prompt: str | None = None,
    ) -> dict[str, Any]:
        if not settings.enable_paid_api:
            raise ValueError("付费 API 调用未启用，请在环境变量中设置 ENABLE_PAID_API=true 开启。")

        if not settings.dashscope_api_key:
            raise ValueError("未找到 DASHSCOPE_API_KEY，请在环境变量中配置。")

        try:
            # 1. 同步提交文生图任务（不使用异步请求头）
            submit_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
            headers = {
                "Authorization": f"Bearer {settings.dashscope_api_key}",
                "Content-Type": "application/json"
            }
            
            content_list = [{"text": image_prompt}]
            payload = {
                "model": settings.qwen_image_model,
                "input": {
                    "messages": [
                        {
                            "role": "user",
                            "content": content_list
                        }
                    ]
                },
                "parameters": {
                    "size": "1080*1920"
                }
            }

            import time
            res = None
            max_retries = 6
            backoff_factor = 2.0

            with httpx.Client(timeout=60.0) as client:
                for attempt in range(max_retries):
                    res = client.post(submit_url, json=payload, headers=headers)
                    try:
                        res.raise_for_status()
                        break
                    except httpx.HTTPStatusError as err:
                        if res.status_code == 429 and attempt < max_retries - 1:
                            sleep_time = (backoff_factor ** attempt) + 1.0
                            print(f"[QwenImageService] Got 429 Too Many Requests. Retrying in {sleep_time}s... (Attempt {attempt+1}/{max_retries})")
                            time.sleep(sleep_time)
                            continue
                        print(f"DashScope API Error Response: {res.text}")
                        raise err
                
                res_data = res.json()
                
                output = res_data.get("output", {})
                image_url = None
                
                # 从多模态 choices 格式中提取图片 URL
                choices = output.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", [])
                    if content and isinstance(content, list):
                        image_url = content[0].get("image")
                
                # 如果没有 choices，则回退读取标准 results 格式
                if not image_url:
                    results = output.get("results", [])
                    if results and len(results) > 0:
                        image_url = results[0].get("url")
                
                if not image_url:
                    raise ValueError(f"Failed to find generated image URL in response: {res_data}")

                # 2. 基于 backend 根目录创建目标目录
                backend_dir = Path(__file__).resolve().parents[2]
                dest_dir = backend_dir / "assets" / task_id / "images"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = dest_dir / f"{scene_id}.png"

                # 3. 下载图片内容
                img_res = client.get(image_url, timeout=60.0)
                img_res.raise_for_status()
                with open(dest_path, "wb") as f:
                    f.write(img_res.content)

                # assets 数据库中保存相对路径，方便匹配前端 URL 映射
                relative_asset_path = f"backend/assets/{task_id}/images/{scene_id}.png"

                return {
                    "source": "qwen",
                    "provider": "qwen_image",
                    "task_id": task_id,
                    "scene_id": scene_id,
                    "asset_type": "image",
                    "asset_path": relative_asset_path,
                    "image_prompt": image_prompt,
                    "negative_prompt": negative_prompt,
                    "status": "success",
                    "message": "Image generated successfully via Qwen-Image.",
                }
        except Exception as e:
            raise RuntimeError(f"QwenImageService failed for {scene_id}: {e}") from e
