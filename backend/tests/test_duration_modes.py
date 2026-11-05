import unittest
from unittest.mock import patch
from pydantic import ValidationError

from services.generation_contract import build_timeline, validate_native_scenes, validate_scene_durations
from services.llm.deepseek_service import DeepSeekService
from agents.video_agent import video_service_for_task
from models.task_model import CreateVideoTaskRequest


class DurationModeTests(unittest.TestCase):
    def test_sixty_seconds_native_is_six_ten_second_clips(self):
        scenes = [{"scene_id": str(i), "duration": 10, "transition_type": "cut",
                   "need_dynamic_video": True} for i in range(6)]
        validate_native_scenes(scenes, 60)
        self.assertEqual(build_timeline(scenes)[-1]["end"], 60)
        service = DeepSeekService()
        with patch.object(service, "_call_api", return_value={"segments": []}) as call:
            service.generate_script({"duration": 60, "generation_mode": "native_10s"})
        self.assertIn("共6段", call.call_args.args[1])

    def test_new_tasks_reject_removed_whole_mode(self):
        with self.assertRaises(ValidationError):
            CreateVideoTaskRequest(user_input="测试", duration=30, generation_mode="wan3_whole")
        for duration in (10, 30, 60, 90):
            request = CreateVideoTaskRequest(user_input="测试", duration=duration)
            self.assertEqual(request.generation_mode, "native_10s")

    def test_historical_whole_task_keeps_its_render_service(self):
        with patch("services.video.wan_video_service.model_config", return_value={
            "url": "https://example.invalid", "key": "fake", "model": "wan2.6-i2v-flash",
        }):
            service = video_service_for_task({"generation_mode": "wan3_whole"})
        self.assertEqual(service.model, "wan3.0-video")
        self.assertEqual(service.api_url, "https://example.invalid")
