import os
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ProviderFactoryComfyUITest(unittest.TestCase):
    def test_get_image_service_uses_runtime_comfyui_provider(self):
        import services.provider_factory as provider_factory

        old_value = os.environ.get("IMAGE_PROVIDER")
        try:
            os.environ["IMAGE_PROVIDER"] = "comfyui"
            with patch("services.image.comfyui_image_service.ComfyUIImageService") as service_cls:
                result = provider_factory.get_image_service()

            service_cls.assert_called_once_with()
            self.assertEqual(result, service_cls.return_value)
        finally:
            if old_value is None:
                os.environ.pop("IMAGE_PROVIDER", None)
            else:
                os.environ["IMAGE_PROVIDER"] = old_value

    def test_get_video_service_uses_runtime_comfyui_provider(self):
        import services.provider_factory as provider_factory

        old_value = os.environ.get("VIDEO_PROVIDER")
        try:
            os.environ["VIDEO_PROVIDER"] = "comfyui"
            with patch("services.video.comfyui_video_service.ComfyUIVideoService") as service_cls:
                result = provider_factory.get_video_service()

            service_cls.assert_called_once_with()
            self.assertEqual(result, service_cls.return_value)
        finally:
            if old_value is None:
                os.environ.pop("VIDEO_PROVIDER", None)
            else:
                os.environ["VIDEO_PROVIDER"] = old_value


if __name__ == "__main__":
    unittest.main()
