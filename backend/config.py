import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "video-agent-backend")
    app_env: str = os.getenv("APP_ENV", "development")
    mongodb_uri: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    mongodb_db_name: str = os.getenv("MONGODB_DB_NAME", "video_agent")
    mongodb_server_selection_timeout_ms: int = int(
        os.getenv("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "3000")
    )
    model_provider: str = os.getenv("MODEL_PROVIDER", os.getenv("DEEPSEEK_PROVIDER", "deepseek"))
    image_provider: str = os.getenv("IMAGE_PROVIDER", os.getenv("QWEN_IMAGE_PROVIDER", "qwen"))
    video_provider: str = os.getenv("VIDEO_PROVIDER", os.getenv("WAN_VIDEO_PROVIDER", "vidu"))
    deepseek_provider: str = os.getenv("DEEPSEEK_PROVIDER", "deepseek")
    qwen_image_provider: str = os.getenv("QWEN_IMAGE_PROVIDER", "qwen")
    wan_video_provider: str = os.getenv("WAN_VIDEO_PROVIDER", "wan")
    deepseek_api_key: str | None = os.getenv("DEEPSEEK_API_KEY")
    enable_paid_api: bool = os.getenv("ENABLE_PAID_API", "false").lower() == "true"
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    dashscope_api_key: str | None = os.getenv("DASHSCOPE_API_KEY", os.getenv("QWEN_API_KEY"))
    max_images_per_task: int = int(os.getenv("MAX_IMAGES_PER_TASK", "20"))
    max_video_seconds_per_task: int = int(os.getenv("MAX_VIDEO_SECONDS_PER_TASK", "600"))
    max_dynamic_scenes_per_task: int = int(os.getenv("MAX_DYNAMIC_SCENES_PER_TASK", "99"))
    wan_video_model: str = os.getenv("WAN_VIDEO_MODEL", "wan2.7-i2v-2026-04-25")
    qwen_image_model: str = os.getenv("QWEN_IMAGE_MODEL", "qwen-image-2.0")
    vidu_api_token: str | None = os.getenv("VIDU_API_TOKEN")
    vidu_model: str = os.getenv("VIDU_MODEL", "viduq3-turbo")
    volcengine_api_key: str | None = os.getenv("VOLCENGINE_API_KEY")
    volcengine_model: str = os.getenv("VOLCENGINE_MODEL", "doubao-seedance-1-5-pro-251215")
    volcengine_image_model: str = os.getenv(
        "VOLCENGINE_IMAGE_MODEL", "doubao-seedream-5-0-lite-260128"
    )


settings = Settings()
