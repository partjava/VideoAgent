import unittest
from unittest.mock import patch
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.llm.deepseek_service import DeepSeekService, _style_template


class FixedGenerationDirectionTest(unittest.TestCase):
    def test_uses_current_default_comic_style(self):
        rules = _style_template(None)

        self.assertIn("漫画", rules["visual"])
        self.assertIn("角色", rules["voice"])
        self.assertIn("漫剧", rules["subtitle"])

    def test_prompt_can_plan_three_different_video_types(self):
        service = DeepSeekService()
        for request, kind in (
            ("拍一只猫开面包店", "story"),
            ("用20秒讲清楚猫为什么踩奶", "explainer"),
            ("介绍这个杯子的可拆卸杯盖", "product"),
        ):
            with self.subTest(kind=kind), patch.object(service, "_call_api", return_value={
                "video_type": kind, "duration": 20, "style": "短剧", "ratio": "1:1",
            }) as call:
                result = service.plan_task(request)
                self.assertEqual(result["video_type"], kind)
                self.assertEqual(result["duration"], 20)
                self.assertEqual(result["ratio"], "9:16")
                self.assertIn(request, call.call_args.args[1])
                self.assertIn("explainer", call.call_args.args[1])

    def test_explicit_duration_and_style_override_model(self):
        service = DeepSeekService()
        with patch.object(service, "_call_api", return_value={
            "video_type": "explainer", "duration": 45, "style": "短剧",
        }):
            result = service.plan_task("解释猫为什么踩奶", duration=20, style="漫剧")
        self.assertEqual(result["duration"], 20)
        self.assertEqual(result["style"], "漫剧")


if __name__ == "__main__":
    unittest.main()
