import os
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.comfyui.config_store import (
    COMFYUI_ENV_KEYS,
    build_comfyui_config,
    normalize_comfyui_config,
    write_env_values,
)


class ComfyUIConfigTest(unittest.TestCase):
    def test_normalize_accepts_comfyui_and_existing_providers(self):
        config = normalize_comfyui_config(
            {
                "image_provider": "comfyui",
                "video_provider": "vidu",
                "comfyui_base_url": "http://127.0.0.1:8188/",
                "comfyui_timeout_seconds": "120",
            }
        )

        self.assertEqual(config["image_provider"], "comfyui")
        self.assertEqual(config["video_provider"], "vidu")
        self.assertEqual(config["comfyui_base_url"], "http://127.0.0.1:8188")
        self.assertEqual(config["comfyui_timeout_seconds"], 120)

    def test_normalize_rejects_unknown_provider(self):
        with self.assertRaisesRegex(ValueError, "Unsupported image provider"):
            normalize_comfyui_config({"image_provider": "fake"})

        with self.assertRaisesRegex(ValueError, "Unsupported video provider"):
            normalize_comfyui_config({"video_provider": "fake"})

    def test_write_env_values_updates_existing_lines_and_preserves_other_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "DEEPSEEK_API_KEY=keep-me\nIMAGE_PROVIDER=qwen\nOTHER=value\n",
                encoding="utf-8",
            )

            write_env_values(
                env_path,
                {
                    "IMAGE_PROVIDER": "comfyui",
                    "VIDEO_PROVIDER": "comfyui",
                    "COMFYUI_BASE_URL": "http://127.0.0.1:8188",
                },
            )

            content = env_path.read_text(encoding="utf-8")
            self.assertIn("DEEPSEEK_API_KEY=keep-me\n", content)
            self.assertIn("OTHER=value\n", content)
            self.assertIn("IMAGE_PROVIDER=comfyui\n", content)
            self.assertIn("VIDEO_PROVIDER=comfyui\n", content)
            self.assertIn("COMFYUI_BASE_URL=http://127.0.0.1:8188\n", content)

    def test_build_config_uses_runtime_environment(self):
        old_values = {key: os.environ.get(key) for key in COMFYUI_ENV_KEYS}
        try:
            os.environ["IMAGE_PROVIDER"] = "comfyui"
            os.environ["VIDEO_PROVIDER"] = "comfyui"
            os.environ["COMFYUI_BASE_URL"] = "http://localhost:8188"
            os.environ["COMFYUI_TIMEOUT_SECONDS"] = "90"

            config = build_comfyui_config()

            self.assertEqual(config["image_provider"], "comfyui")
            self.assertEqual(config["video_provider"], "comfyui")
            self.assertEqual(config["comfyui_base_url"], "http://localhost:8188")
            self.assertEqual(config["comfyui_timeout_seconds"], 90)
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
