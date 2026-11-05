import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.settings_routes import router
from services.model_config import model_config, save_models, saved_models


class ModelKeyClearTests(unittest.TestCase):
    def test_clear_saved_key_keeps_model_and_requires_a_new_key(self):
        app = FastAPI()
        app.include_router(router)
        url = "https://example.test"
        profile = {"url": url, "model": "wan2.6-i2v-flash", "key": "old-key"}
        with TemporaryDirectory() as directory, patch(
            "services.model_config.PATH", Path(directory) / "models.json"
        ), TestClient(app) as client:
            save_models({"video": profile, "image": {**profile, "key": "other-key"}})
            endpoint = f"{router.prefix}/models/video"
            response = client.delete(f"{endpoint}/key")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["key_source"], "none")
            self.assertEqual(model_config("video"), {**profile, "key": ""})
            self.assertEqual(saved_models()["image"]["key"], "other-key")
            self.assertEqual(client.delete(f"{endpoint}/key").status_code, 200)
            self.assertEqual(client.put(endpoint, json={**profile, "key": ""}).status_code, 422)
            result = client.put(endpoint, json={**profile, "key": "new-key"})
            self.assertEqual(result.json()["data"]["key_source"], "local")
            self.assertEqual(model_config("video")["key"], "new-key")
