import unittest

from agents.video_agent import _build_motion_prompt, video_prompt_options
from services.visual_style import PHOTO_RULES, lock_visual_style


class VisualStyleTests(unittest.TestCase):
    def test_real_style_removes_medium_conflicts_and_preserves_character(self):
        original = "漫画风格，一位漫画家穿青色长袍，站在石桥上。"
        result = lock_visual_style(original, "短剧")
        self.assertNotIn("漫画风格", result)
        self.assertIn("一位漫画家穿青色长袍", result)
        self.assertEqual(result, lock_visual_style(result, "短剧"))
        self.assertEqual(original, lock_visual_style(original, "漫剧"))

    def test_final_video_request_locks_selected_real_style(self):
        scene = {
            "scene_id": "a", "duration": 10, "style": "短剧",
            "video_prompt": "动漫风格，人物抬眼。",
            "motion_beats": [{
                "start": 0, "end": 10, "subject": "人物",
                "camera": "近景", "start_state": "低头", "action": "抬眼", "end_state": "平视",
            }],
        }
        prompt = _build_motion_prompt(scene)
        self.assertTrue(prompt.startswith(PHOTO_RULES))
        self.assertNotIn("动漫风格", prompt)
        service = type("Provider", (), {"supports_negative_prompt": True})()
        self.assertIn("游戏CG", video_prompt_options(service, scene)["negative_prompt"])
