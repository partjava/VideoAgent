import unittest
from models.task_model import RerunSceneRequest


class SceneRerunModelTest(unittest.TestCase):
    def test_rerun_scene_request_defaults(self):
        req = RerunSceneRequest()
        self.assertEqual(req.rerun_type, "voice")
        self.assertIsNone(req.voiceover)
        self.assertIsNone(req.speaker)
        self.assertIsNone(req.prompt)

    def test_rerun_scene_request_custom_fields(self):
        req = RerunSceneRequest(
            rerun_type="all",
            voiceover="李白笑答：此乃天地之作！",
            speaker="李白",
            prompt="李白站在考场中挥毫泼墨",
        )
        self.assertEqual(req.rerun_type, "all")
        self.assertEqual(req.voiceover, "李白笑答：此乃天地之作！")
        self.assertEqual(req.speaker, "李白")
        self.assertEqual(req.prompt, "李白站在考场中挥毫泼墨")


if __name__ == "__main__":
    unittest.main()
