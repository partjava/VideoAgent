from abc import ABC, abstractmethod
from typing import Any


class BaseImageService(ABC):
    supports_reference_images = False

    @abstractmethod
    def generate_image(
        self,
        task_id: str,
        scene_id: str,
        image_prompt: str,
        negative_prompt: str | None = None,
        ratio: str = "9:16",
        reference_images: list[str] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError
