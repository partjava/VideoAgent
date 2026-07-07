import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.agent_utils import assert_no_mock_assets


class RealAssetGuardTest(unittest.TestCase):
    def test_rejects_mock_assets(self):
        assets = [
            {
                "asset_id": "asset_scene_001",
                "asset_type": "video_clip",
                "source": "mock",
                "path": "backend/assets/task/videos/scene_001.mp4",
            }
        ]

        with self.assertRaisesRegex(ValueError, "Mock asset is not allowed"):
            assert_no_mock_assets(assets)

    def test_accepts_real_assets(self):
        assets = [
            {
                "asset_id": "asset_scene_001",
                "asset_type": "video_clip",
                "source": "wan",
                "path": "backend/assets/task/videos/scene_001.mp4",
            },
            {
                "asset_id": "asset_scene_001_image",
                "asset_type": "image",
                "source": "qwen",
                "path": "backend/assets/task/images/scene_001.png",
            },
        ]

        assert_no_mock_assets(assets)


if __name__ == "__main__":
    unittest.main()
