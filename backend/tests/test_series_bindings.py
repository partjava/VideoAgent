import unittest

from services.character_binding import (
    characters_in_scene,
    reference_choices_for_scene,
    reference_paths_for_scene,
    scene_voice_options,
    series_prompt_context,
)


class SeriesBindingTests(unittest.TestCase):
    def setUp(self):
        self.character = {"character_id": "char_a", "name": "老周", "revision": 2,
                          "face_dna": "花白短发", "signature_clothing": "蓝马甲",
                          "reference_path": "backend/assets/characters/char_a/v2/anchor.png",
                          "tts_voice": "zh-CN-YunjianNeural", "rate": "-5%", "pitch": "-2Hz"}
        self.task = {"metadata": {"series_context": {
            "characters": [self.character],
            "episode_outline": {"core_event": "发现线索"},
            "previous_snapshot": {"state": {"holding_item": "左手持鞋"}},
        }}}

    def test_visible_cast_uses_frozen_ids_and_avoids_unrelated_references(self):
        self.assertEqual(characters_in_scene(self.task, {"character_ids": ["char_a"]}), [self.character])
        self.assertEqual(characters_in_scene(self.task, {"visual_description": "桌面道具特写"}), [])
        with self.assertRaises(ValueError):
            characters_in_scene(self.task, {"character_ids": ["unknown"]})

    def test_voice_binding_does_not_depend_on_name_guessing(self):
        scene = {"voice_profiles": [self.character]}
        self.assertEqual(scene_voice_options(scene, "老周"),
                         {"voice": "zh-CN-YunjianNeural", "rate": "-5%", "pitch": "-2Hz"})

    def test_context_preserves_state_and_character_revision(self):
        context = series_prompt_context(self.task)
        self.assertIn("左手持鞋", context)
        self.assertIn('"revision": 2', context)

    def test_reference_selection_uses_frozen_views_and_falls_back_to_front(self):
        front = self.character["reference_path"]
        side = "backend/assets/characters/char_a/v3/profile.png"
        whole = "backend/assets/characters/char_a/v4/whole.png"
        self.character["reference_images"] = {"front": front, "profile": side, "whole": whole}
        for hints, view, path in [
            ({"shot_type": "wide_shot"}, "full", whole),
            ({"start_state": "老周背对镜头"}, "full", whole),
            ({"visual_description": "老周的侧脸"}, "side", side),
            ({"camera_motion": "side-on"}, "side", side),
            ({"shot_type": "close_up"}, "front", front),
        ]:
            scene = {"character_ids": ["char_a"], **hints}
            self.assertEqual(reference_choices_for_scene(self.task, scene), [
                {"character_id": "char_a", "view": view, "path": path},
            ])
            self.assertEqual(reference_paths_for_scene(self.task, scene), [path])
        self.assertEqual(reference_paths_for_scene(self.task, {"shot_type": "wide_shot"}), [])
        self.character.pop("reference_images")
        self.assertEqual(reference_paths_for_scene(self.task, scene), [front])
