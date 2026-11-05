import unittest

from agents.video_agent import align_measured_motion, sanitize_motion_text


class MeasuredMotionTests(unittest.TestCase):
    def test_overlay_cleanup_keeps_prop_text(self):
        text = "鞋底出现磨损的文字。画面正中出现‘走了，你快点’字样。屏幕浮现一行文字。招牌显示修鞋铺字样。"
        result = sanitize_motion_text(text)
        self.assertNotIn("走了", result)
        self.assertNotIn("屏幕浮现", result)
        self.assertIn("鞋底出现磨损的文字", result)
        self.assertIn("招牌显示修鞋铺字样", result)

    def test_measured_dialogue_replaces_conflicting_state_chain(self):
        scene = {
            "dialogue_audio": [{"start": 6.2, "end": 9.6}],
            "motion_beats": [
                {"start": 6, "end": 7, "action": "听口信", "camera": "面部近景", "start_state": "站立持鞋"},
                {"start": 7, "end": 8, "action": "转身拉门", "camera": "背影", "start_state": "站立持鞋"},
                {"start": 8, "end": 10, "action": "离场", "start_state": "背对镜头"},
            ],
            "end_state": "已经离场",
        }
        result = align_measured_motion(scene)
        self.assertEqual(result["end_state"], "站立持鞋")
        self.assertEqual(result["motion_beats"][2]["start_state"], "站立持鞋")
        self.assertEqual(scene["end_state"], "已经离场")

    def test_narration_uses_playback_duration(self):
        scene = {
            "scene_id": "a", "duration": 10, "voice_start": 1,
            "voiceover": "介绍", "audio_duration": 10,
            "motion_beats": [{"start": 9, "end": 10, "action": "转身", "start_state": "展示产品"}],
        }
        self.assertEqual(align_measured_motion(scene)["end_state"], "展示产品")
