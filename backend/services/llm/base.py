from abc import ABC, abstractmethod
from typing import Any


class BaseLLMService(ABC):
    @abstractmethod
    def plan_task(
        self,
        user_input: str,
        duration: int | None = None,
        style: str | None = None,
        platform: str | None = None,
        ratio: str = "9:16",
        generation_mode: str = "full_dynamic",
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def generate_script(self, task_plan: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def generate_storyboard(
        self,
        script: dict[str, Any],
        target_duration: int | None = None,
        generation_mode: str = "full_dynamic",
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def polish_dialogue(
        self,
        scenes: list[dict[str, Any]],
        target_duration: int | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def generate_image_prompts(
        self,
        scenes: list[dict[str, Any]],
        ratio: str = "9:16",
        style: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError
