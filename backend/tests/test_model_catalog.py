import unittest
from unittest.mock import patch

import httpx

from routes.settings_routes import ModelInput, query_model_catalog


class ModelCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def query(self, kind, url="https://example.test", *, suggestions=False):
        items = [
            {"id": model} for model in (
                "deepseek-chat", "deepseek-reasoner", "text-embedding-v3",
                "qwen-image-plus", "wan2.7-image", "wan2.7-image-pro",
                "wan2.5-t2i-preview", "qwen-image-edit-plus", "wan2.5-i2i-preview",
                "wan2.6-i2v-flash", "wan2.7-i2v", "wan3.0-video",
            )
        ] + [
            {"id": "custom-chat", "output_modalities": ["text"]},
            {"id": "custom-painter", "input_modalities": ["text"], "output_modalities": ["image"]},
            {"id": "custom-editor", "input_modalities": ["image"], "output_modalities": ["image"]},
        ]
        with patch("routes.settings_routes.httpx.AsyncClient") as client:
            get = client.return_value.__aenter__.return_value.get
            get.return_value = httpx.Response(200, json={"data": items})
            result = await query_model_catalog(kind, ModelInput(url=url, model="", key="test"))
        field = "suggested_models" if suggestions else "models"
        return result["data"][field], get.call_args.args[0]

    async def test_prompt_filters_non_language_models_and_preserves_proxy_path(self):
        models, url = await self.query("prompt", "https://example.test/team/v1")
        self.assertEqual(models, ["custom-chat", "deepseek-chat", "deepseek-reasoner"])
        self.assertEqual(url, "https://example.test/team/v1/models")

    async def test_official_deepseek_uses_documented_catalog(self):
        _, url = await self.query("prompt", "https://api.deepseek.com/v1")
        self.assertEqual(url, "https://api.deepseek.com/models")

    async def test_image_includes_text_to_image_but_excludes_edit_only_models(self):
        models, _ = await self.query("image")
        self.assertEqual(models, [
            "custom-painter", "qwen-image-plus", "wan2.5-t2i-preview", "wan2.7-image", "wan2.7-image-pro",
        ])

    async def test_video_lists_supported_models_and_avoids_duplicate_path(self):
        models, url = await self.query("video", "https://example.test/compatible-mode/v1")
        self.assertEqual(models, ["wan2.6-i2v-flash", "wan3.0-video"])
        self.assertEqual(url, "https://example.test/compatible-mode/v1/models")

    async def test_official_video_suggestions_are_separate_from_catalog_matches(self):
        models, _ = await self.query("video", "https://dashscope.aliyuncs.com", suggestions=True)
        self.assertIn("wan3.0-video-prime", models)
        self.assertNotIn("wan3.0-video", models)
        self.assertNotIn("wan2.7-image", models)
        proxy_models, _ = await self.query("video", suggestions=True)
        self.assertEqual(proxy_models, [])
