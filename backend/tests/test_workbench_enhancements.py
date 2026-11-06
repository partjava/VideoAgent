import asyncio
import sys
import types
import unittest
from PIL import Image

# Provide lightweight module stubs when running in WSL /usr/bin/python3 without third-party wheels
if "services.tts_service" not in sys.modules:
    try:
        import services.tts_service  # noqa: F401
    except ModuleNotFoundError:
        tts_mod = types.ModuleType("services.tts_service")
        tts_mod.resolve_voice_for_speaker = lambda speaker: "zh-CN-YunyangNeural"
        sys.modules["services.tts_service"] = tts_mod

if "pymongo" not in sys.modules:
    try:
        import pymongo  # noqa: F401
    except ModuleNotFoundError:
        pymongo_mod = types.ModuleType("pymongo")
        errors_mod = types.ModuleType("pymongo.errors")
        class DuplicateKeyError(Exception):
            pass
        errors_mod.DuplicateKeyError = DuplicateKeyError
        pymongo_mod.errors = errors_mod
        sys.modules["pymongo"] = pymongo_mod
        sys.modules["pymongo.errors"] = errors_mod

if "config" not in sys.modules:
    try:
        import config  # noqa: F401
    except ModuleNotFoundError:
        cfg_mod = types.ModuleType("config")
        cfg_mod.settings = types.SimpleNamespace(
            mongodb_uri="",
            mongodb_db_name="",
            mongodb_server_selection_timeout_ms=1000,
            deepseek_api_key="",
            dashscope_api_key="",
            qwen_image_model="qwen-image-plus",
            wan_video_model="wan2.6-i2v-flash",
            enable_paid_api=False,
        )
        sys.modules["config"] = cfg_mod

for _mod_name, _builder in [
    ("httpx", lambda: types.SimpleNamespace(
        AsyncClient=None,
        TimeoutException=TimeoutError,
        RequestError=OSError,
        NetworkError=OSError,
        HTTPStatusError=RuntimeError,
        ReadTimeout=TimeoutError,
        ConnectTimeout=TimeoutError,
        TransportError=OSError,
        Client=None,
    )),
    ("fastapi", lambda: types.SimpleNamespace(
        APIRouter=lambda **kw: types.SimpleNamespace(
            prefix=kw.get("prefix", ""),
            get=lambda *a, **k: (lambda fn: fn),
            post=lambda *a, **k: (lambda fn: fn),
            put=lambda *a, **k: (lambda fn: fn),
            delete=lambda *a, **k: (lambda fn: fn),
        ),
        HTTPException=type("HTTPException", (Exception,), {
            "__init__": lambda self, status_code, detail="": (
                setattr(self, "status_code", status_code),
                setattr(self, "detail", detail),
            ) and None
        }),
    )),
    ("fastapi.responses", lambda: types.SimpleNamespace(JSONResponse=lambda content, **kw: content)),
    ("pydantic", lambda: types.SimpleNamespace(
        BaseModel=type("BaseModel", (), {
            "__init__": lambda self, **kw: self.__dict__.update(kw),
            "model_copy": lambda self, update=None: type(self)(**{**self.__dict__, **(update or {})}),
        }),
        SecretStr=type("SecretStr", (), {
            "__init__": lambda self, val="": setattr(self, "_val", val),
            "get_secret_value": lambda self: self._val,
        }),
    )),
    ("dotenv", lambda: types.SimpleNamespace(load_dotenv=lambda *a, **k: None, set_key=lambda *a, **k: None)),
]:
    if _mod_name not in sys.modules:
        try:
            __import__(_mod_name)
        except ModuleNotFoundError:
            sys.modules[_mod_name] = _builder()

if "anyio" not in sys.modules:
    try:
        import anyio  # noqa: F401
    except ModuleNotFoundError:
        anyio_mod = types.ModuleType("anyio")
        async def _run_sync(fn, *args):
            return fn(*args)
        anyio_mod.to_thread = types.SimpleNamespace(run_sync=_run_sync)
        sys.modules["anyio"] = anyio_mod

from services.character_binding import reference_choices_for_scene, reference_paths_for_scene
from services.publish_materials import _draw_title
from services.snapshot_diff import compare_states
from services import generation_usage as usage
from services.generation_dispatch import dispatch_generation


class WorkbenchEnhancementsTests(unittest.IsolatedAsyncioTestCase):
    def test_snapshot_diff_detects_added_removed_and_changed_fields(self):
        before = {
            "location": "天桥下",
            "character_states": {"老周": "持旧皮鞋", "阿琳": "观望"},
            "props": ["旧皮鞋"],
        }
        after = {
            "location": "修理铺内",
            "character_states": {"老周": "打开暗格"},
            "props": ["旧皮鞋", "铜钥匙"],
            "ending_hook": "暗格里出现录音带",
        }
        changes = compare_states(before, after)
        by_field = {item["field"]: item for item in changes}
        self.assertEqual(by_field["location"]["kind"], "changed")
        self.assertEqual(by_field["character_states.老周"]["kind"], "changed")
        self.assertEqual(by_field["character_states.阿琳"]["kind"], "removed")
        self.assertEqual(by_field["props"]["kind"], "changed")
        self.assertEqual(by_field["ending_hook"]["kind"], "added")

    def test_locate_quality_and_semantic_issues(self):
        if "models.task_model" not in sys.modules:
            mod = types.ModuleType("models.task_model")
            class DummyStatus:
                CHECKING = "checking"
            mod.VideoTaskStatus = DummyStatus
            sys.modules["models.task_model"] = mod
        if "agents.agent_utils" not in sys.modules:
            au = types.ModuleType("agents.agent_utils")
            au.mark_agent_failed = None
            au.mark_agent_start = None
            au.mark_agent_success = None
            sys.modules["agents.agent_utils"] = au

        from agents.quality_agent import locate_quality_issues, locate_semantic_issues

        scenes = [
            {"scene_id": "scene_1", "scene_index": 1, "duration": 10},
            {"scene_id": "scene_2", "scene_index": 2, "duration": 10},
        ]
        timeline = [
            {"scene_id": "scene_1", "start": 0.0, "end": 10.0, "duration": 10.0},
            {"scene_id": "scene_2", "start": 10.0, "end": 20.0, "duration": 10.0},
        ]
        located_visual = locate_quality_issues(
            [{"time": 13.4, "issue": "手中道具消失"}], scenes, timeline
        )
        self.assertEqual(len(located_visual), 1)
        self.assertEqual(located_visual[0]["scene_id"], "scene_2")
        self.assertEqual(located_visual[0]["scene_index"], 2)
        self.assertAlmostEqual(located_visual[0]["scene_offset"], 3.4)

        located_sem = locate_semantic_issues(
            ["镜头 2 缺少结尾悬念特写", "整体节奏偏快"], scenes, timeline
        )
        self.assertEqual(located_sem[0]["scene_id"], "scene_2")
        self.assertEqual(located_sem[0]["scene_index"], 2)
        self.assertEqual(located_sem[0]["time"], 10.0)
        self.assertNotIn("scene_id", located_sem[1])

    def test_draw_title_works_in_wsl_and_linux(self):
        img = Image.new("RGB", (640, 360), color=(24, 32, 48))
        _draw_title(img, "第1集：雨夜来客")

    def test_multi_view_reference_choices(self):
        task = {
            "metadata": {
                "series_context": {
                    "characters": [
                        {
                            "character_id": "char_1",
                            "name": "沈默",
                            "revision": 1,
                            "reference_path": "backend/assets/characters/char_1/v1/anchor.png",
                            "reference_images": {
                                "front": "backend/assets/characters/char_1/v1/anchor.png",
                                "profile": "backend/assets/characters/char_1/v1/profile.png",
                                "whole": "backend/assets/characters/char_1/v1/whole.png",
                            },
                        }
                    ]
                }
            }
        }
        choices = reference_choices_for_scene(
            task, {"character_ids": ["char_1"], "shot_type": "wide_shot"}
        )
        self.assertEqual(
            choices,
            [
                {
                    "character_id": "char_1",
                    "view": "full",
                    "path": "backend/assets/characters/char_1/v1/whole.png",
                }
            ],
        )
        self.assertEqual(
            reference_paths_for_scene(task, {"character_ids": ["char_1"], "visual_description": "侧脸特写"}),
            ["backend/assets/characters/char_1/v1/profile.png"],
        )

        # Standalone single-video task with bound_characters from Character Library
        from services.character_binding import series_prompt_context, validate_series_scene
        standalone_task = {
            "metadata": {
                "bound_characters": task["metadata"]["series_context"]["characters"]
            }
        }
        self.assertEqual(
            reference_paths_for_scene(standalone_task, {"visual_description": "沈默走在街头，全身全景"}),
            ["backend/assets/characters/char_1/v1/whole.png"],
        )
        self.assertIn("沈默", series_prompt_context(standalone_task))
        # Standalone bound_characters task allows extra/minor speaker without raising
        validate_series_scene(
            standalone_task,
            {"dialogue_lines": [{"speaker": "路人甲", "text": "快看那边！"}]},
        )

    async def test_generation_usage_and_dispatch_offline_ledger(self):
        usage._offline_ledger.rows.clear()
        await usage.set_limit("series", "s_test", 2)
        task = {"task_id": "t_1", "series_id": "s_test"}

        res1 = await dispatch_generation(
            task, "image", "scene_1", lambda: {"status": "success", "asset_path": "a.png"}
        )
        self.assertEqual(res1["status"], "success")

        res2 = await dispatch_generation(
            task, "video", "scene_1", lambda: {"status": "success", "asset_path": "a.mp4"}
        )
        self.assertEqual(res2["status"], "success")

        with self.assertRaises(usage.GenerationLimitExceeded):
            await dispatch_generation(
                task, "video", "scene_2", lambda: {"status": "success", "asset_path": "b.mp4"}
            )

        await usage.record_reuse(task, "video", "scene_1")
        summary = await usage.get_usage("series", "s_test")
        self.assertEqual(summary["used"], 2)
        self.assertEqual(summary["remaining"], 0)
        self.assertEqual(summary["reused"], 1)

    def test_model_catalog_candidates_and_third_party_matching(self):
        from services.video.wan_video_service import FLASH_MODEL, SILENT_OUTPUT_MODELS, WAN3_MODELS
        SUPPORTED = SILENT_OUTPUT_MODELS | WAN3_MODELS | {FLASH_MODEL}

        # Test WanVideoService base URL normalization and prefixed model names
        from services.video.wan_video_service import WanVideoService
        svc = WanVideoService(model="ali/wan2.6-i2v-flash")
        svc.api_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.assertEqual(svc.api_base, "https://dashscope.aliyuncs.com")
        self.assertTrue(svc.supports_negative_prompt)
        self.assertEqual(svc._normalized_model_name, "wan2.6-i2v-flash")

        # Test QwenImageService base URL normalization
        from services.image.qwen_image_service import _normalize_dashscope_base
        self.assertEqual(
            _normalize_dashscope_base("https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "https://dashscope.aliyuncs.com",
        )
        self.assertEqual(
            _normalize_dashscope_base("https://my-relay.example.com/v1"),
            "https://my-relay.example.com",
        )

    async def test_settings_catalog_fallback_and_connection_test(self):
        # Provide minimal stubs for fastapi/pydantic/httpx if not installed in system python
        for mod_name, builder in [
            ("httpx", lambda: types.SimpleNamespace(
                AsyncClient=None,
                TimeoutException=TimeoutError,
                RequestError=OSError,
                NetworkError=OSError,
                HTTPStatusError=RuntimeError,
                ReadTimeout=TimeoutError,
                ConnectTimeout=TimeoutError,
                TransportError=OSError,
                Client=None,
            )),
            ("fastapi", lambda: types.SimpleNamespace(
                APIRouter=lambda **kw: types.SimpleNamespace(
                    prefix=kw.get("prefix", ""),
                    get=lambda *a, **k: (lambda fn: fn),
                    post=lambda *a, **k: (lambda fn: fn),
                    put=lambda *a, **k: (lambda fn: fn),
                    delete=lambda *a, **k: (lambda fn: fn),
                ),
                HTTPException=type("HTTPException", (Exception,), {
                    "__init__": lambda self, status_code, detail="": (
                        setattr(self, "status_code", status_code),
                        setattr(self, "detail", detail),
                    ) and None
                }),
            )),
            ("fastapi.responses", lambda: types.SimpleNamespace(JSONResponse=lambda content, **kw: content)),
            ("pydantic", lambda: types.SimpleNamespace(
                BaseModel=type("BaseModel", (), {
                    "__init__": lambda self, **kw: self.__dict__.update(kw),
                    "model_copy": lambda self, update=None: type(self)(**{**self.__dict__, **(update or {})}),
                }),
                SecretStr=type("SecretStr", (), {
                    "__init__": lambda self, val="": setattr(self, "_val", val),
                    "get_secret_value": lambda self: self._val,
                }),
            )),
            ("dotenv", lambda: types.SimpleNamespace(load_dotenv=lambda *a, **k: None, set_key=lambda *a, **k: None)),
        ]:
            if mod_name not in sys.modules:
                try:
                    __import__(mod_name)
                except ModuleNotFoundError:
                    sys.modules[mod_name] = builder()

        from unittest.mock import patch
        from routes.settings_routes import (
            ModelInput,
            model_catalog_candidates,
            matches_model_catalog,
            query_model_catalog,
            test_model,
        )
        from pydantic import SecretStr

        # 1. Verify candidate URLs order preserves primary first and includes fallbacks
        prompt_cands = model_catalog_candidates("prompt", "https://custom-relay.example.com")
        self.assertEqual(prompt_cands[0], "https://custom-relay.example.com/models")
        self.assertIn("https://custom-relay.example.com/v1/models", prompt_cands)

        video_cands = model_catalog_candidates("video", "https://custom-relay.example.com/v1")
        self.assertEqual(video_cands[0], "https://custom-relay.example.com/v1/compatible-mode/v1/models")
        self.assertIn("https://custom-relay.example.com/v1/models", video_cands)

        # 2. Verify prefixed Wan model and third-party image model matching
        self.assertTrue(matches_model_catalog("video", {"id": "ali/wan2.6-i2v-flash"}, "ali/wan2.6-i2v-flash"))
        self.assertTrue(matches_model_catalog("image", {"id": "black-forest-labs/flux-1-dev"}, "black-forest-labs/flux-1-dev"))

        # 3. Verify query_model_catalog falls back when primary URL returns 404 and extracts third-party video candidates
        class FakeResponse:
            def __init__(self, status_code, body=None, path="/v1/models"):
                self.status_code = status_code
                self.is_success = 200 <= status_code < 300
                self._body = body
                self.request = types.SimpleNamespace(url=types.SimpleNamespace(path=path))
            def json(self):
                return self._body

        called_urls = []
        async def fake_get(url, headers=None):
            called_urls.append(url)
            if url.endswith("/compatible-mode/v1/models"):
                return FakeResponse(404)
            return FakeResponse(200, {"data": [{"id": "kling-v1-6-i2v"}, {"id": "vidu-2.0"}, {"id": "gpt-4o"}]})

        class FakeAsyncClient:
            def __init__(self, **kw):
                pass
            async def __aenter__(self):
                return types.SimpleNamespace(get=fake_get)
            async def __aexit__(self, *args):
                pass

        with patch("routes.settings_routes.httpx.AsyncClient", FakeAsyncClient):
            res = await query_model_catalog(
                "video",
                ModelInput(url="https://custom-relay.example.com/v1", model="", key=SecretStr("sk-test")),
            )
        self.assertEqual(res["data"]["models"], [])
        self.assertEqual(res["data"]["suggested_models"], ["kling-v1-6-i2v", "vidu-2.0"])
        self.assertEqual(res["data"]["suggestion_source"], "catalog")
        self.assertGreaterEqual(len(called_urls), 2)

        # 4. Verify test_model falls back from /api/v1/files (404) to /v1/models (200) on third-party URLs
        async def fake_test_get(url, headers=None):
            if url.endswith("/api/v1/files") or url.endswith("/compatible-mode/v1/models"):
                return FakeResponse(404)
            return FakeResponse(200, {"data": [{"id": "flux-1-dev"}, {"id": "qwen-image-plus"}]})

        class FakeTestClient:
            def __init__(self, **kw):
                pass
            async def __aenter__(self):
                return types.SimpleNamespace(get=fake_test_get)
            async def __aexit__(self, *args):
                pass

        with patch("routes.settings_routes.httpx.AsyncClient", FakeTestClient):
            test_res = await test_model(
                "image",
                ModelInput(url="https://custom-relay.example.com/v1", model="flux-1-dev", key=SecretStr("sk-test")),
            )
        self.assertEqual(test_res["data"]["result"], "model_listed")


if __name__ == "__main__":
    unittest.main()
