import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.storyboard_agent import validate_storyboard_scenes
from agents.voice_agent import clean_tts_text
from services.tts_service import resolve_voice_for_speaker


def valid_scene(**overrides):
    scene = {
        "scene_id": "scene_001",
        "scene_index": 1,
        "duration": 4,
        "voiceover": "考官拍案而起，催他立刻落笔答题。",
        "subtitle": "考官拍案而起，催他立刻落笔答题。",
        "visual_description": "李白站在考场中央，考官拍案催促，学生们回头张望。",
        "character_state": "李白从错愕转为镇定，考官焦急催促。",
        "scene_continuity": "延续上一镜头的高考考场、人群和紧张气氛。",
        "transition_note": "李白低头看向试卷，镜头切到他提笔。",
    }
    scene.update(overrides)
    return scene


class VoiceTextQualityTest(unittest.TestCase):
    def test_rejects_four_second_scene_with_too_little_voice_text(self):
        scenes = [valid_scene(voiceover="快答题！", subtitle="快答题！")]

        with self.assertRaisesRegex(ValueError, "voiceover is too short for duration"):
            validate_storyboard_scenes(scenes, target_duration=4)

    def test_accepts_moderate_voice_text_for_four_second_scene(self):
        validate_storyboard_scenes([valid_scene()], target_duration=4)

    def test_tts_text_removes_speaker_labels(self):
        self.assertEqual(clean_tts_text("【李白】：且看我以诗会试！"), "且看我以诗会试！")
        self.assertEqual(clean_tts_text("李白说：且看我以诗会试！"), "且看我以诗会试！")
        self.assertEqual(clean_tts_text("李白说，且看我以诗会试！"), "且看我以诗会试！")
        self.assertEqual(clean_tts_text("旁白：一道金光落在现代街头。"), "一道金光落在现代街头。")
        self.assertEqual(clean_tts_text("一道金光，诗仙李白跌落现代街头。"), "一道金光，诗仙李白跌落现代街头。")

    def test_tts_text_removes_stage_directions(self):
        self.assertEqual(clean_tts_text("（李白仰天大笑）哈哈哈哈！"), "哈哈哈哈！")
        self.assertEqual(clean_tts_text("【音效：打雷】天色骤然昏暗下来。"), "天色骤然昏暗下来。")
        self.assertEqual(clean_tts_text("李白[兴奋地]：太妙了！"), "太妙了！")

    def test_rejects_scene_with_excessive_voiceover(self):
        long_text = "这是一段非常非常长而且明显超出四秒钟时长的超多字数台词根本读不完。"
        scenes = [valid_scene(voiceover=long_text, subtitle=long_text)]
        with self.assertRaisesRegex(ValueError, "voiceover is too long for duration"):
            validate_storyboard_scenes(scenes, target_duration=4)

    def test_resolve_voice_for_different_speakers(self):
        self.assertEqual(resolve_voice_for_speaker("李白"), "zh-CN-YunxiNeural")
        self.assertEqual(resolve_voice_for_speaker("主考官"), "zh-CN-YunjianNeural")
        self.assertEqual(resolve_voice_for_speaker("旁白"), "zh-CN-YunyangNeural")
        self.assertEqual(resolve_voice_for_speaker("少女"), "zh-CN-XiaoyiNeural")


if __name__ == "__main__":
    unittest.main()
