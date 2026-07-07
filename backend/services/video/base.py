from abc import ABC, abstractmethod
from typing import Any


class BaseVideoService(ABC):
    @abstractmethod
    def generate_video(
        self,
        task_id: str,
        scene_id: str,
        image_path: str,
        video_prompt: str | None = None,
        duration: int = 5,
    ) -> dict[str, Any]:
        raise NotImplementedError
