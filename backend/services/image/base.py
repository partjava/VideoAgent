from abc import ABC, abstractmethod
from typing import Any


class BaseImageService(ABC):
    @abstractmethod
    def generate_image(
        self,
        task_id: str,
        scene_id: str,
        image_prompt: str,
        negative_prompt: str | None = None,
        ratio: str = "9:16",
    ) -> dict[str, Any]:
        raise NotImplementedError
