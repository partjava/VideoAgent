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

        # 5. Verify DashScope image catalog supplies official supported image candidates & third-party mixed wan+kling+veo keeps all
        async def fake_dash_image_get(url, headers=None):
            return FakeResponse(200, {"data": [{"id": "qwen-plus"}, {"id": "qwen-image-plus"}]})

        class FakeDashClient:
            def __init__(self, **kw):
                pass
            async def __aenter__(self):
                return types.SimpleNamespace(get=fake_dash_image_get)
            async def __aexit__(self, *args):
                pass

        with patch("routes.settings_routes.httpx.AsyncClient", FakeDashClient):
            dash_img_res = await query_model_catalog(
                "image",
                ModelInput(url="https://dashscope.aliyuncs.com/compatible-mode/v1", model="", key=SecretStr("sk-dash")),
            )
        self.assertEqual(dash_img_res["data"]["models"], ["qwen-image-plus"])
        self.assertIn("wan2.7-image", dash_img_res["data"]["suggested_models"])
        self.assertEqual(dash_img_res["data"]["suggestion_source"], "official")

        async def fake_mixed_video_get(url, headers=None):
            return FakeResponse(200, {"data": [{"id": "wan2.6-i2v-flash"}, {"id": "kling-v3"}, {"id": "veo-3.1"}]})

        class FakeMixedVideoClient:
            def __init__(self, **kw):
                pass
            async def __aenter__(self):
                return types.SimpleNamespace(get=fake_mixed_video_get)
            async def __aexit__(self, *args):
                pass

        with patch("routes.settings_routes.httpx.AsyncClient", FakeMixedVideoClient):
            mixed_vid_res = await query_model_catalog(
                "video",
                ModelInput(url="https://custom-relay.example.com/v1", model="", key=SecretStr("sk-relay")),
            )
        self.assertEqual(mixed_vid_res["data"]["models"], ["wan2.6-i2v-flash"])
        self.assertEqual(mixed_vid_res["data"]["suggested_models"], ["kling-v3", "veo-3.1"])
        self.assertEqual(mixed_vid_res["data"]["suggestion_source"], "catalog")

    def test_drama_craft_engine(self):
        from unittest.mock import patch
        from services.drama_craft_service import (
            ai_decompose_novel_to_drama,
            ai_doctor_script,
            ai_expand_character,
            ai_review_script,
            ai_upgrade_video_prompt,
            build_character_sheet_4grid_prompt,
            build_eight_segment_image_prompt,
            ensure_scene_blocking,
            ensure_shoes_in_clothing,
            format_prop_sheet_prompt,
            format_scene_grid_prompt,
            infer_anti_drift_negatives,
            normalize_voice_params,
            sanitize_video_prompt_mcsla,
            scan_script_metrics,
        )

        # 1. Character shoes completeness & occupation constraints & voice normalization & 16:9 4-grid sheet
        clothing_with_shoes = ensure_shoes_in_clothing("深炭灰色立领风衣", "28岁冷峻男性")
        self.assertIn("鞋", clothing_with_shoes)
        anti_drift = infer_anti_drift_negatives("小区门卫大叔", "深蓝保安服", "老王")
        self.assertIn("严禁画成警察制服", anti_drift)
        self.assertEqual(normalize_voice_params("invalid-voice", "0", "-2"), ("zh-CN-YunyangNeural", "+0%", "-2Hz"))

        sheet_prompt = build_character_sheet_4grid_prompt(
            {"name": "林渊", "face_dna": "冷白皮，狭长黑眸", "signature_clothing": "深灰风衣"},
            style="3d",
        )
        self.assertIn("整板 16:9 横版构图", sheet_prompt)
        self.assertIn("全图共 4 格", sheet_prompt)
        self.assertIn("Unreal Engine 5", sheet_prompt)

        # 2. Three-Layer Separation + <=2 Reference Cap + 8-segment prompt
        three_char_task = {
            "metadata": {
                "bound_characters": [
                    {"character_id": "c1", "name": "人物甲", "face_dna": "28岁冷白皮狭长黑眸左眼尾泪痣", "reference_path": "a.png", "reference_images": {"front": "a.png"}},
                    {"character_id": "c2", "name": "人物乙", "face_dna": "24岁鹅蛋脸琥珀色瞳孔微卷短发", "reference_path": "b.png", "reference_images": {"front": "b.png"}},
                    {"character_id": "c3", "name": "人物丙", "face_dna": "32岁金丝眼镜背头", "reference_path": "c.png", "reference_images": {"front": "c.png"}},
                ]
            }
        }
        capped_refs = reference_choices_for_scene(three_char_task, {"character_ids": ["c1", "c2", "c3"]})
        self.assertEqual(len(capped_refs), 2)
        eight_seg = build_eight_segment_image_prompt(
            {"start_state": "人物甲按住合同", "visual_description": "28岁冷白皮狭长黑眸左眼尾泪痣的人物甲与人物乙对峙", "shot_type": "中近景"},
            "干净纯色摄影棚背景，28岁冷白皮狭长黑眸左眼尾泪痣",
            bound_profiles=three_char_task["metadata"]["bound_characters"][:2],
            overflow_profiles=three_char_task["metadata"]["bound_characters"][2:],
        )
        self.assertIn("[CHARACTER]", eight_seg)
        self.assertIn("[CAMERA]", eight_seg)
        self.assertIn("blurred silhouette in shallow DoF bokeh", eight_seg)
        self.assertNotIn("干净纯色摄影棚背景", eight_seg)

        # 3. Blocking (180-degree axis lock)
        blocking = ensure_scene_blocking(
            {"character_ids": ["c1", "c2"], "start_state": "长桌两侧对峙"},
            three_char_task["metadata"]["bound_characters"],
        )
        self.assertIn("180°", blocking)
        self.assertIn("左人物甲·右人物乙", blocking)

        # 4. Deterministic video prompt safety guards
        sanitized = sanitize_video_prompt_mcsla(
            "人物甲端坐如钟，非常愤怒地看向人物乙，手中玉佩泛着幽光，随后鲜血淋漓地刺穿胸膛",
            scene={"character_ids": ["c1"], "speaker": "人物甲"},
            all_character_names=["人物甲", "人物乙"],
        )
        self.assertNotIn("端坐如钟", sanitized["prompt"])
        self.assertNotIn("看向人物乙", sanitized["prompt"])
        self.assertIn("画外右前方方向", sanitized["prompt"])
        self.assertIn("哑光实心物理材质原色", sanitized["prompt"])
        self.assertNotIn("鲜血淋漓", sanitized["prompt"])
        self.assertLessEqual(sanitized["char_count"], 1400)

        # 5. Scene & prop prompt formatters + objective metrics scanner
        scene_grid = format_scene_grid_prompt("废旧修车厂", space_type="indoor", layout_and_materials="油污混凝土地面与升降台", lighting_and_kelvin="冷白日光灯管")
        self.assertEqual(len(scene_grid["angles"]), 9)
        self.assertIn("3×3 九宫格", scene_grid["prompt"])
        prop_sheet = format_prop_sheet_prompt("旧怀表", material_and_color="玉佩泛着幽光，黄铜外壳", size_vs_palm="0.5倍", key_detail="内盖刻字")
        self.assertIn("严禁出现任何人物、人手", prop_sheet["prompt"])
        self.assertIn("哑光实心物理材质原色", prop_sheet["prompt"])

        metrics = scan_script_metrics("人物甲眼神微动。\n甲：然而如此。\n乙：你好。\n丙：嗯。\n丁：行。")
        self.assertEqual(metrics["max_consecutive_dialogue"], 4)
        self.assertIn("眼神微动", metrics["found_invalid_micro_actions"])
        self.assertIn("然而", metrics["found_ai_cliches"])

        # 6. Verify real-AI calls route through configured LLM (never fake hardcoded fallbacks)
        class FakeLLM:
            def __init__(self):
                self.calls = 0

            def _call_api(self, system_prompt, user_prompt, max_tokens=2000):
                self.calls += 1
                if "造型与人物设定总监" in system_prompt:
                    return {
                        "name": "陆凛",
                        "face_dna": "30岁，方正下颌，深褐瞳孔，短寸头，左眉骨断眉",
                        "signature_clothing": "深蓝立领夹克",
                        "tts_voice": "zh-CN-YunjianNeural",
                        "rate": "0",
                        "pitch": "-2",
                    }
                if "总审稿导演" in system_prompt:
                    return {
                        "overall_score": 76,
                        "grade": "B",
                        "verdict": "needs_polish",
                        "dimensions": {"pacing": {"label": "15秒换挡节奏", "score": 72}},
                        "four_gear_timeline": [],
                        "issues": [{"rule_id": "站桩对白", "category": "可拍性", "severity": "high", "evidence": "甲：然而如此", "diagnosis": "缺动作", "prescription": "加△动作"}],
                    }
                if "改稿总编剧" in system_prompt:
                    return {
                        "polished_script": "△ 陆凛一把把账本摔在台面上。\n陆凛：三万块，一分不能少。",
                        "changes_made": ["重写开场为拍桌物证冲突"],
                        "after_review": {"overall_score": 92, "grade": "S", "verdict": "pass", "dimensions": {}, "four_gear_timeline": [], "issues": []},
                    }
                if "图生视频动作导演" in system_prompt:
                    return {"upgraded_prompt": "陆凛端坐如钟，左手拿起桌面茶杯轻叩木桌"}
                return {
                    "title": "《修车厂密钥》",
                    "shootability_report": {"overall_feasibility": "S级", "metrics": [{"name": "前3秒抓力", "score": 91, "comment": "紧凑"}]},
                    "characters": [{"name": "陈野", "role": "主角", "face_dna": "26岁寸头青年", "signature_clothing": "脏污机修连体服", "tts_voice": "bad-voice"}],
                    "scenes_spec": [{"scene_name": "城中村修车厂", "space_type": "indoor", "layout_and_materials": "举升机与工具墙"}],
                    "props_spec": [{"prop_name": "加密U盘", "material_and_color": "拉丝黑钢", "size_vs_palm": "0.3倍", "key_detail": "背面划痕"}],
                    "episodes_outline": [{"episode_num": 1, "title": "第1集", "opening_hook": "刹车失灵", "core_event": "发现U盘", "ending_hook": "卷帘门落下", "paywall_card": False, "paywall_reason": ""}],
                    "first_episode_script": "△ 陈野钻出车底，指节满是黑机油。\n陈野：刹车管被人剪了。",
                    "continuity_ledgers": {"physical_ledger": [{"character": "陈野", "location": "修车厂", "costume_state": "机油污渍", "injury_or_mark": "无", "stamina_posture": "警惕"}]},
                }

        fake_llm = FakeLLM()
        with patch("services.drama_craft_service._get_real_llm", return_value=fake_llm):
            expanded = ai_expand_character(name="陆凛", brief="退伍机械师")
            self.assertEqual(expanded["name"], "陆凛")
            self.assertIn("鞋", expanded["signature_clothing"])
            self.assertEqual(expanded["rate"], "+0%")
            self.assertEqual(expanded["pitch"], "-2Hz")

            decomposed = ai_decompose_novel_to_drama("陈野在修车厂底盘发现被剪断的刹车管和加密U盘。", total_episodes=1)
            self.assertEqual(decomposed["title"], "《修车厂密钥》")
            self.assertEqual(decomposed["characters"][0]["name"], "陈野")
            self.assertIn("鞋", decomposed["characters"][0]["signature_clothing"])
            self.assertEqual(decomposed["characters"][0]["tts_voice"], "zh-CN-YunyangNeural")
            self.assertIn("加密U盘", decomposed["props_sheet"][0]["prompt"])

            rev = ai_review_script("剧本内容测试")
            self.assertEqual(rev["overall_score"], 76)
            doc = ai_doctor_script("剧本内容测试")
            self.assertIn("陈" if "陈" in doc["polished_script"] else "陆凛", doc["polished_script"])
            up = ai_upgrade_video_prompt("他坐在椅子上端杯子", speaker="陆凛", character_ids=["c1"])
            self.assertNotIn("端坐如钟", up["prompt"])
            self.assertGreaterEqual(fake_llm.calls, 5)

        # Verify calling without enabled paid API raises instead of returning fake data
        def _blocked_llm():
            raise ValueError("付费 API 调用未启用，请在环境变量中设置 ENABLE_PAID_API=true。")

        with patch("services.drama_craft_service._get_real_llm", side_effect=_blocked_llm):
            with self.assertRaises(ValueError):
                ai_decompose_novel_to_drama("任意真实测试文本输入")

    def test_final_video_motion_prompt_compact_and_sanitized(self):
        # Provide stubs if optional video_agent imports aren't installed in system python
        for mod_name, builder in [
            ("numpy", lambda: types.SimpleNamespace()),
            ("cv2", lambda: types.SimpleNamespace()),
            ("imageio_ffmpeg", lambda: types.SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg")),
        ]:
            if mod_name not in sys.modules:
                try:
                    __import__(mod_name)
                except ModuleNotFoundError:
                    sys.modules[mod_name] = builder()
        if "agents.agent_utils" not in sys.modules:
            au = types.ModuleType("agents.agent_utils")
            for attr in ("ASSETS_COLLECTION", "SCENES_COLLECTION", "mark_agent_failed", "mark_agent_start", "mark_agent_success", "with_document_id"):
                setattr(au, attr, lambda *a, **k: None)
            sys.modules["agents.agent_utils"] = au
        if "models.asset_model" not in sys.modules:
            am = types.ModuleType("models.asset_model")
            am.Asset = object
            sys.modules["models.asset_model"] = am
        if "models.task_model" not in sys.modules:
            tm = types.ModuleType("models.task_model")
            class DummyStatus:
                VIDEO_GENERATING = "video_generating"
            tm.VideoTaskStatus = DummyStatus
            sys.modules["models.task_model"] = tm

        from agents.video_agent import _build_motion_prompt

        beats = [
            {
                "start": i,
                "end": i + 1,
                "subject": "陆凛",
                "camera": "近景",
                "start_state": f"第{i}秒站位状态稳定，手持账本保持面向画右前方的起始动作姿势",
                "action": "端坐如钟并看向周正" if i == 0 else f"第{i}秒左手手指用力按住账本边缘微前倾十度观察变化情况",
                "end_state": f"第{i+1}秒站位状态稳定，手持账本保持面向画右前方的起始动作姿势",
            }
            for i in range(10)
        ]
        scene = {
            "scene_id": "s_dense",
            "duration": 10,
            "style": "短剧",
            "speaker": "陆凛",
            "character_ids": ["c_1"],
            "cast_names": ["陆凛", "周正"],
            "blocking": "【单人入画锁：陆凛】位于画面左偏中，面向画右外。",
            "shot_type": "近景",
            "camera_motion": "缓慢推镜",
            "video_prompt": "陆凛坐在桌前抬眼冷视，手中玉佩泛着幽光。",
            "motion_beats": beats,
            "dialogue_audio": [
                {"speaker": "陆凛", "start": 1.0, "end": 3.5, "text": "账对不上。"},
                {"speaker": "周正", "start": 5.0, "end": 7.5, "text": "不可能！"},
            ],
        }
        prompt = _build_motion_prompt(scene)
        self.assertLessEqual(len(prompt), 1400)
        self.assertIn("[9-10s]", prompt)
        self.assertNotIn("端坐如钟", prompt)
        self.assertNotIn("周正", prompt)
        self.assertIn("画外对白", prompt)
        self.assertIn("主光色温", prompt)


if __name__ == "__main__":
    unittest.main()
