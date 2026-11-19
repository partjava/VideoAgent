import asyncio
import base64
from copy import deepcopy
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw, ImageStat

# Provide lightweight stubs when running under WSL /usr/bin/python3 without optional wheels
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
            max_images_per_task=20,
            max_dynamic_scenes_per_task=20,
            max_video_seconds_per_task=180,
            video_provider="siliconflow",
        )
        sys.modules["config"] = cfg_mod

for _mod_name, _factory in [
    ("httpx", lambda: types.SimpleNamespace(
        AsyncClient=None,
        Client=None,
        TimeoutException=TimeoutError,
        RequestError=OSError,
        NetworkError=OSError,
        HTTPStatusError=RuntimeError,
        ReadTimeout=TimeoutError,
        ConnectTimeout=TimeoutError,
        TransportError=OSError,
    )),
    ("anyio", lambda: types.SimpleNamespace(
        Semaphore=asyncio.Semaphore,
        to_thread=types.SimpleNamespace(
            run_sync=lambda fn, *a, abandon_on_cancel=None, **kw: asyncio.sleep(0, result=fn(*a, **kw))
        ),
    )),
    ("services.tts_service", lambda: types.SimpleNamespace(
        resolve_voice_for_speaker=lambda *a, **kw: "zh-CN-YunyangNeural",
        tts_service=types.SimpleNamespace(generate_voice=AsyncMock(return_value=1.0)),
    )),
    ("edge_tts", lambda: types.SimpleNamespace()),
    ("motor", lambda: types.SimpleNamespace(motor_asyncio=types.SimpleNamespace(AsyncIOMotorClient=None))),
    ("motor.motor_asyncio", lambda: types.SimpleNamespace(AsyncIOMotorClient=None)),
    ("pymongo", lambda: types.SimpleNamespace(errors=types.SimpleNamespace(DuplicateKeyError=Exception, PyMongoError=Exception))),
    ("pymongo.errors", lambda: types.SimpleNamespace(DuplicateKeyError=Exception, PyMongoError=Exception)),
]:
    if _mod_name not in sys.modules:
        try:
            __import__(_mod_name)
        except ModuleNotFoundError:
            sys.modules[_mod_name] = _factory()


def _png_bytes(size=(160, 160), color=(0, 0, 0), needle=False, textured=False) -> bytes:
    img = Image.new("RGB", size, color)
    if needle:
        draw = ImageDraw.Draw(img)
        w, h = size
        draw.rectangle((int(w * 0.38), int(h * 0.65), int(w * 0.62), int(h * 0.67)), fill=(210, 180, 90))
    if textured:
        import random
        rng = random.Random(42)
        px = img.load()
        w, h = size
        base_r, base_g, base_b = color
        for y in range(0, h, 2):
            for x in range(0, w, 2):
                px[x, y] = (
                    (base_r + rng.randint(0, 40)) % 255,
                    (base_g + rng.randint(0, 40)) % 255,
                    (base_b + rng.randint(0, 40)) % 255,
                )
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _complete_pydantic_patch() -> dict:
    existing = sys.modules.get("pydantic")
    if existing is not None and getattr(existing, "__file__", None):
        return {}
    mod = types.ModuleType("pydantic")
    mod.BaseModel = type(
        "BaseModel",
        (),
        {
            "__init__": lambda self, **kw: self.__dict__.update(kw),
            "model_dump": lambda self, **kw: dict(self.__dict__),
            "model_copy": lambda self, update=None: type(self)(**{**self.__dict__, **(update or {})}),
        },
    )
    mod.Field = lambda *a, **k: k.get("default") if "default" in k else (a[0] if a else None)
    mod.ConfigDict = lambda **kw: kw
    mod.SecretStr = type(
        "SecretStr",
        (),
        {
            "__init__": lambda self, val="": setattr(self, "_val", val),
            "get_secret_value": lambda self: self._val,
        },
    )
    mod.field_validator = lambda *a, **k: (lambda fn: fn)
    mod.model_validator = lambda *a, **k: (lambda fn: fn)
    mod.ValidationError = ValueError
    return {"pydantic": mod}


class _FakeMongo:
    def __init__(self):
        self.cols = {"characters": {}, "series_projects": {}, "video_tasks": {}}

    async def insert_one(self, col, doc):
        self.cols.setdefault(col, {})[doc["_id"]] = deepcopy(doc)
        return str(doc["_id"])

    async def find_one(self, col, query):
        for doc in self.cols.get(col, {}).values():
            ok = True
            for k, v in (query or {}).items():
                val = doc
                for part in k.split("."):
                    val = val.get(part) if isinstance(val, dict) else None
                if isinstance(v, dict) and "$ne" in v:
                    if val == v["$ne"]:
                        ok = False
                        break
                elif isinstance(val, list) and not isinstance(v, list):
                    if v not in val:
                        ok = False
                        break
                elif val != v:
                    ok = False
                    break
            if ok:
                return deepcopy(doc)
        return None

    async def find_many(self, col, query=None, limit=100, **kw):
        out = []
        for doc in self.cols.get(col, {}).values():
            ok = True
            for k, v in (query or {}).items():
                if doc.get(k) != v:
                    ok = False
                    break
            if ok:
                out.append(deepcopy(doc))
            if len(out) >= limit:
                break
        return out

    async def replace_one(self, col, query, doc, upsert=False):
        store = self.cols.setdefault(col, {})
        existing = store.get(query.get("_id"))
        if existing is None and not upsert:
            return 0
        if existing is not None and "revision" in query and existing.get("revision") != query["revision"]:
            return 0
        store[doc["_id"]] = deepcopy(doc)
        return 1

    async def update_one(self, col, query, values):
        doc = self.cols.get(col, {}).get(query.get("_id"))
        if not doc:
            return 0
        doc.update(deepcopy(values))
        return 1

    async def delete_one(self, col, query):
        store = self.cols.get(col, {})
        if query.get("_id") in store:
            del store[query["_id"]]
            return 1
        return 0


class BugfixRegressionTests(unittest.TestCase):
    def test_black_frame_detection_thresholds(self):
        from services.image.siliconflow_image_service import is_black_or_collapsed_image

        pure_black = _png_bytes((320, 180), (0, 0, 0))
        needle_black = _png_bytes((320, 180), (0, 0, 0), needle=True)
        normal_dark = _png_bytes((320, 180), (55, 57, 57))
        moody_interior = _png_bytes((320, 180), (45, 50, 55), textured=True)

        # Dark-void frame (small dim subject covering ~10% on pure-black room -> nb >= 0.82)
        dark_void_img = Image.new("RGB", (320, 180), (0, 0, 0))
        ImageDraw.Draw(dark_void_img).rectangle((100, 60, 150, 120), fill=(110, 100, 90))
        buf = BytesIO()
        dark_void_img.save(buf, format="PNG")
        dark_void = buf.getvalue()

        # Horizontal letterbox (top & bottom 22% black bars around a bright center)
        letterbox_img = Image.new("RGB", (320, 480), (0, 0, 0))
        ImageDraw.Draw(letterbox_img).rectangle((0, 110, 320, 370), fill=(150, 145, 140))
        buf = BytesIO()
        letterbox_img.save(buf, format="PNG")
        letterbox = buf.getvalue()

        # Vertical pillarbox (left & right 22% black bars)
        pillarbox_img = Image.new("RGB", (480, 320), (0, 0, 0))
        ImageDraw.Draw(pillarbox_img).rectangle((110, 0, 370, 320), fill=(150, 145, 140))
        buf = BytesIO()
        pillarbox_img.save(buf, format="PNG")
        pillarbox = buf.getvalue()

        # Half-black split-screen (left half bright, right half pure black)
        split_img = Image.new("RGB", (320, 180), (0, 0, 0))
        ImageDraw.Draw(split_img).rectangle((0, 0, 155, 180), fill=(155, 150, 140))
        buf = BytesIO()
        split_img.save(buf, format="PNG")
        half_black = buf.getvalue()

        self.assertTrue(is_black_or_collapsed_image(pure_black))
        self.assertTrue(is_black_or_collapsed_image(needle_black))
        self.assertTrue(is_black_or_collapsed_image(dark_void))
        self.assertTrue(is_black_or_collapsed_image(letterbox))
        self.assertTrue(is_black_or_collapsed_image(pillarbox))
        self.assertTrue(is_black_or_collapsed_image(half_black))
        self.assertFalse(is_black_or_collapsed_image(normal_dark))
        self.assertFalse(is_black_or_collapsed_image(moody_interior))
        self.assertTrue(is_black_or_collapsed_image(b"not-an-image"))

    def test_siliconflow_edit_reference_crop_omits_negative_and_recovers_from_black(self):
        from services.image.siliconflow_image_service import SiliconFlowImageService

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            card1_bytes = _png_bytes((1664, 928), (150, 120, 100), textured=True)
            card2_bytes = _png_bytes((1664, 928), (130, 145, 160), textured=True)
            good_vertical = _png_bytes((928, 1664), (110, 120, 130), textured=True)
            pure_black = _png_bytes((1368, 760), (0, 0, 0))

            letterbox_im = Image.new("RGB", (768, 1360), (0, 0, 0))
            ImageDraw.Draw(letterbox_im).rectangle((0, 320, 768, 1040), fill=(135, 125, 115))
            lbuf = BytesIO()
            letterbox_im.save(lbuf, format="PNG")
            letterbox_bytes = lbuf.getvalue()

            dark_void_im = Image.new("RGB", (928, 1664), (0, 0, 0))
            ImageDraw.Draw(dark_void_im).rectangle((80, 700, 260, 1100), fill=(130, 125, 120))
            dbuf = BytesIO()
            dark_void_im.save(dbuf, format="PNG")
            dark_void_bytes = dbuf.getvalue()

            card1 = tmp_dir / "assets" / "t1" / "images" / "cast_ref_1.png"
            card2 = tmp_dir / "assets" / "t1" / "images" / "cast_ref_2.png"
            card1.parent.mkdir(parents=True, exist_ok=True)
            card1.write_bytes(card1_bytes)
            card2.write_bytes(card2_bytes)

            posted_payloads = []
            returned_images = [letterbox_bytes, good_vertical]
            call_counter = {"idx": 0}

            class FakeResp:
                def __init__(self, status=200, body=None, content=b""):
                    self.status_code = status
                    self._body = body or {}
                    self.content = content
                    self.text = json.dumps(self._body)

                def json(self):
                    return self._body

                def raise_for_status(self):
                    if self.status_code >= 400:
                        raise RuntimeError(f"HTTP {self.status_code}")

            class FakeClient:
                def __init__(self, **kw):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

                def post(self, url, json=None, headers=None):
                    posted_payloads.append(deepcopy(json))
                    cur = call_counter["idx"]
                    call_counter["idx"] += 1
                    return FakeResp(200, {"images": [{"url": f"https://cdn.test/{cur}.png"}]})

                def get(self, url, timeout=60):
                    idx = int(url.rsplit("/", 1)[-1].split(".")[0])
                    return FakeResp(200, content=returned_images[idx])

            profile = {"url": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen-Image-Edit-2509", "key": "sf-test"}
            with patch.dict(sys.modules, _complete_pydantic_patch()), patch(
                "services.image.siliconflow_image_service.model_config", return_value=profile
            ), patch(
                "services.image.siliconflow_image_service.settings", types.SimpleNamespace(enable_paid_api=True)
            ), patch("services.image.siliconflow_image_service.httpx.Client", FakeClient), patch(
                "services.character_service.local_asset", side_effect=[card1, card2]
            ):
                svc = SiliconFlowImageService(storage_root=tmp_dir)
                self.assertTrue(svc.prefers_t2i_for_studio_cast_sheets)
                res = svc.generate_image(
                    "t1",
                    "scene_01",
                    "红木大厅内林晚手持黄铜针面对周明哲\n[角色外貌定妆参考] 严格保持参考图人物五官一致。",
                    negative_prompt="动漫, 二次元, 三头身",
                    ratio="9:16",
                    reference_images=[
                        "backend/assets/t1/images/cast_ref_1.png",
                        "backend/assets/t1/images/cast_ref_2.png",
                    ],
                )

            self.assertEqual(res["status"], "success")
            self.assertIn("black-frame fallback", res["message"])
            # 1-step direct fallback (no 2nd wasteful Qwen-Image-Edit retry -> only 2 POSTs total)
            self.assertEqual(len(posted_payloads), 2)

            # Attempt 0: Qwen-Image-Edit-2509 omits negative_prompt and crops 16:9 turnaround full-bleed onto 576x1024
            p0 = posted_payloads[0]
            self.assertEqual(p0["model"], "Qwen/Qwen-Image-Edit-2509")
            self.assertNotIn("negative_prompt", p0)
            decoded_b64 = p0["image"].split(",", 1)[1]
            with Image.open(BytesIO(base64.b64decode(decoded_b64))) as ref_im:
                self.assertEqual(ref_im.size, (576, 1024))
                # Full-bleed ImageOps.fit leaves zero uniform #EFEFEF padding strips at top or bottom
                top_extrema = ImageStat.Stat(ref_im.crop((0, 0, 576, 40))).extrema
                bot_extrema = ImageStat.Stat(ref_im.crop((0, 984, 576, 1024))).extrema
                self.assertTrue(any(lo < hi for lo, hi in top_extrema))
                self.assertTrue(any(lo < hi for lo, hi in bot_extrema))

            # Attempt 1: Direct T2I fallback switches to Qwen/Qwen-Image with 928x1664, anti-black negatives, and strips edit clause
            p1 = posted_payloads[1]
            self.assertEqual(p1["model"], "Qwen/Qwen-Image")
            self.assertEqual(p1["image_size"], "928x1664")
            self.assertIn("上下黑边", p1["negative_prompt"])
            self.assertNotIn("[角色外貌定妆参考]", p1["prompt"])

            saved_file = tmp_dir / "assets" / "t1" / "images" / "scene_01.png"
            self.assertEqual(saved_file.read_bytes(), good_vertical)

            # Direct T2I dark-void 1-time retry: starts on Qwen/Qwen-Image, retries once with boosted lighting
            posted_payloads.clear()
            returned_images = [dark_void_bytes, good_vertical]
            call_counter["idx"] = 0
            with patch("services.image.siliconflow_image_service.model_config", return_value=profile), patch(
                "services.image.siliconflow_image_service.settings", types.SimpleNamespace(enable_paid_api=True)
            ), patch("services.image.siliconflow_image_service.httpx.Client", FakeClient):
                t2i_res = svc.generate_image("t1", "scene_t2i", "夜间红木厅堂", negative_prompt="模糊", ratio="9:16")
            self.assertEqual(t2i_res["status"], "success")
            self.assertEqual(len(posted_payloads), 2)
            self.assertEqual(posted_payloads[0]["model"], "Qwen/Qwen-Image")
            self.assertEqual(posted_payloads[0]["negative_prompt"], "模糊")
            self.assertIn("无黑边满画幅，明亮清晰的实景室内光线", posted_payloads[1]["prompt"])
            self.assertIn("上下黑边", posted_payloads[1]["negative_prompt"])

            # All-black responses must raise rather than writing a black frame
            returned_images = [pure_black, pure_black]
            call_counter["idx"] = 0
            with patch("services.image.siliconflow_image_service.model_config", return_value=profile), patch(
                "services.image.siliconflow_image_service.settings", types.SimpleNamespace(enable_paid_api=True)
            ), patch("services.image.siliconflow_image_service.httpx.Client", FakeClient):
                with self.assertRaises(RuntimeError):
                    svc.generate_image("t1", "scene_fail", "测试黑图拦截", ratio="9:16")
            self.assertFalse((tmp_dir / "assets" / "t1" / "images" / "scene_fail.png").exists())

    def test_image_agent_rejects_cached_black_scene_and_reuses_valid(self):
        with patch.dict(sys.modules, _complete_pydantic_patch()):
            from agents.image_agent import _is_usable_image_file

            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                black_file = tmp_dir / "scene_01.png"
                valid_file = tmp_dir / "scene_02.png"
                black_file.write_bytes(_png_bytes((200, 200), (0, 0, 0)))
                valid_file.write_bytes(_png_bytes((200, 200), (95, 105, 115), textured=True))

                self.assertGreater(valid_file.stat().st_size, 1024)
                self.assertFalse(_is_usable_image_file(black_file))
                self.assertTrue(_is_usable_image_file(valid_file))

    def test_dynamic_four_gear_guide_scales_to_duration_range(self):
        from services.drama_craft_service import ai_decompose_novel_to_drama, format_four_gear_guide

        g_short = format_four_gear_guide(30, 60)
        self.assertEqual(g_short["min"], 30)
        self.assertEqual(g_short["max"], 60)
        self.assertEqual(g_short["target"], 45)
        self.assertEqual(g_short["boundaries"][-1], 45)
        self.assertNotIn("85~110s", g_short["chain"])

        g_med = format_four_gear_guide(45, 90)
        self.assertEqual(g_med["min"], 45)
        self.assertEqual(g_med["max"], 90)
        self.assertEqual(g_med["target"], 68)
        self.assertEqual(g_med["boundaries"][-1], 68)

        captured_prompts = []

        class FakeLLM:
            def _call_api(self, sys_p, usr_p, max_tokens=8192):
                captured_prompts.append((sys_p, usr_p))
                return {
                    "title": "短平快测试",
                    "shootability_report": {"overall_feasibility": "A级", "metrics": []},
                    "characters": [],
                    "scenes_spec": [],
                    "props_spec": [],
                    "episodes_outline": [{"episode_num": 1, "title": "第1集", "opening_hook": "h", "core_event": "c", "ending_hook": "e"}],
                    "first_episode_script": "0-8s △开场推门。",
                    "continuity_ledgers": {},
                }

        with patch("services.drama_craft_service._get_real_llm", return_value=FakeLLM()):
            out = ai_decompose_novel_to_drama(
                "测试一段足够长的小说故事内容用于拆解。",
                total_episodes=1,
                episode_duration_min=30,
                episode_duration_max=60,
            )

        self.assertEqual(out["episode_duration_min"], 30)
        self.assertEqual(out["episode_duration_max"], 60)
        self.assertEqual(out["episode_duration"], 45)
        self.assertIn("严禁超过 60 秒", captured_prompts[0][0])

    def test_story_judge_rejects_score_regression_rewrite(self):
        with patch.dict(sys.modules, _complete_pydantic_patch()):
            from agents.story_judge_agent import StoryJudgeAgent

            task = {"task_id": "t_reg", "generation_mode": "auto_director", "duration": 45}
            draft_script = {
                "title": "原标题",
                "content": "林晚亮针测温。",
                "segments": [
                    {"duration": 10, "plot": "亮针测温", "voice_start": 0.3, "voiceover": "八十六度。差六度。", "dialogue_lines": []},
                ],
            }
            task_plan = {"topic": "老字号对峙"}

            fake_llm = types.SimpleNamespace(
                rewrite_script_with_judgment=lambda *a, **kw: {
                    "title": "改坏标题",
                    "content": "改坏内容",
                    "segments": [
                        {"duration": 10, "plot": "站桩聊", "voice_start": 0.3, "voiceover": "嗯。", "dialogue_lines": []},
                    ],
                    "changes_made": ["改弱了"],
                }
            )
            reviews = [
                {"overall_score": 80, "grade": "B", "verdict": "needs_polish", "needs_rewrite": True, "issues": [{"severity": "high"}], "deterministic_flags": [], "pacing_stats": {}},
                {"overall_score": 76, "grade": "B", "verdict": "needs_polish", "needs_rewrite": True, "issues": [{"severity": "high"}], "deterministic_flags": [], "pacing_stats": {}},
            ]

            with patch("agents.story_judge_agent.audit_script_pacing_and_drama", side_effect=reviews):
                out = StoryJudgeAgent.judge_and_polish(task, draft_script, task_plan, llm_service=fake_llm, allow_llm=True)

            self.assertFalse(out["story_judge"]["rewritten"])
            self.assertEqual(out["story_judge"]["before_score"], 80)
            self.assertEqual(out["story_judge"]["after_score"], 80)
            self.assertEqual(out["script_data"]["title"], "原标题")

    def test_character_crop_sync_and_permanent_delete(self):
        with patch.dict(sys.modules, _complete_pydantic_patch()):
            import services.character_service as cs_mod
            from models.character_model import CharacterFields

            async def _run():
                fake_db = _FakeMongo()
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_backend = Path(tmp) / "backend"
                    turnaround_file = tmp_backend / "assets" / "t1" / "images" / "cast_ref_1.png"
                    turnaround_file.parent.mkdir(parents=True, exist_ok=True)
                    turnaround_file.write_bytes(_png_bytes((1664, 928), (190, 180, 170)))

                    def _fake_local(rel_path):
                        return Path(tmp) / rel_path

                    with patch.object(cs_mod, "mongodb", fake_db), patch.object(
                        cs_mod, "BACKEND_DIR", tmp_backend
                    ), patch.object(
                        cs_mod, "local_asset", side_effect=_fake_local
                    ), patch(
                        "services.media_quality.resolve_media", side_effect=_fake_local
                    ):
                        svc = cs_mod.CharacterService()

                        # 1. Headshot crop preserves portrait orientation and passes validate_image
                        headshot = cs_mod.crop_headshot_from_turnaround(turnaround_file.read_bytes())
                        with Image.open(BytesIO(headshot)) as him:
                            self.assertLess(him.width, him.height)
                            self.assertGreaterEqual(min(him.size), 64)

                        # 2. Seed an existing blank-avatar character "赵鹏" and an already-imaged confirmed character "周明哲"
                        c_zhaopeng = await svc.create(CharacterFields(name="赵鹏", face_dna="圆脸寸头", signature_clothing="厨师服"))
                        c_zhou = await svc.create(CharacterFields(name="周明哲", face_dna="金丝眼镜", signature_clothing="西装马甲"))
                        c_zhou_ref = await svc.reference(c_zhou["character_id"], base64.b64encode(_png_bytes((200, 200), (140, 120, 110))).decode(), "front")
                        await svc.confirm(c_zhou_ref["character_id"])
                        await fake_db.insert_one("series_projects", {"_id": "series_1", "archived": False, "character_ids": [c_zhou["character_id"]]})

                        cards = [
                            {"card_id": "cast_ref_1", "name": "赵鹏", "gender": "male", "appearance": "圆脸", "path": "backend/assets/t1/images/cast_ref_1.png"},
                            {"card_id": "cast_ref_2", "name": "周明哲", "gender": "male", "appearance": "眼镜", "path": "backend/assets/t1/images/cast_ref_1.png"},
                            {"card_id": "cast_ref_3", "name": "林晚", "gender": "female", "appearance": "25岁女性，高马尾；默认服装：白色厨师服配防滑黑鞋", "path": "backend/assets/t1/images/cast_ref_1.png"},
                        ]
                        summary = await svc.sync_cast_cards_to_library(cards, style="短剧", overwrite_existing=False)
                        actions = {item["name"]: item["action"] for item in summary}
                        self.assertEqual(actions["赵鹏"], "populated_existing")
                        self.assertEqual(actions["周明哲"], "linked_existing")
                        self.assertEqual(actions["林晚"], "created")

                        # Verify 赵鹏 populated cleanly (still only 1 赵鹏, now has front + sheet)
                        all_chars = await svc.list()
                        zps = [c for c in all_chars if c["name"] == "赵鹏"]
                        self.assertEqual(len(zps), 1)
                        self.assertTrue(zps[0]["reference_images"].get("front"))
                        self.assertTrue(zps[0]["reference_images"].get("sheet"))

                        # 3. Permanent delete allowed on unconfirmed "赵鹏" and deletes directory,
                        #    refused on confirmed bound "周明哲"
                        with self.assertRaises(ValueError):
                            await svc.delete_permanent(c_zhou["character_id"])
                        del_res = await svc.delete_permanent(c_zhaopeng["character_id"])
                        self.assertTrue(del_res["deleted"])
                        self.assertFalse((tmp_backend / "assets" / "characters" / c_zhaopeng["character_id"]).exists())

            asyncio.run(_run())

    def test_image_agent_prefers_t2i_for_studio_cast_sheets_and_versions_scenes(self):
        with patch.dict(sys.modules, _complete_pydantic_patch()):
            from agents.image_agent import ImageAgent

            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                calls = []

                class StudioPreferT2ISvc:
                    model = "Qwen/Qwen-Image-Edit-2509"
                    supports_reference_images = True
                    max_reference_images = 3
                    prefers_t2i_for_studio_cast_sheets = True

                    def generate_image(self, task_id, scene_id, image_prompt, negative_prompt=None, ratio="9:16", reference_images=None):
                        calls.append({"scene_id": scene_id, "ratio": ratio, "refs": reference_images, "prompt": image_prompt})
                        out_path = tmp_dir / f"{scene_id}.png"
                        out_path.write_bytes(_png_bytes((160, 160), (120, 130, 140), textured=True))
                        return {"status": "success", "asset_path": str(out_path), "source": "siliconflow"}

                fake_db = _FakeMongo()
                task_doc = {
                    "_id": "t_sf_cast",
                    "task_id": "t_sf_cast",
                    "generation_mode": "storyboard_flow",
                    "style": "短剧",
                    "ratio": "9:16",
                    "metadata": {"task_plan": {"characters": [{"name": "林晚", "gender": "female", "appearance": "高马尾厨师服"}]}},
                }
                fake_db.cols["video_tasks"]["t_sf_cast"] = task_doc
                scenes_data = [
                    {"_id": "t_sf_cast_scene_01", "task_id": "t_sf_cast", "scene_id": "scene_01", "scene_index": 1, "duration": 5, "shot_type": "近景", "need_dynamic_video": True, "image_prompt": "林晚站在红木长案前"},
                    {"_id": "t_sf_cast_scene_02", "task_id": "t_sf_cast", "scene_id": "scene_02", "scene_index": 2, "duration": 5, "shot_type": "空镜", "need_dynamic_video": True, "image_prompt": "宫灯映照红木大厅"},
                ]
                for s in scenes_data:
                    fake_db.cols.setdefault("scenes", {})[s["_id"]] = deepcopy(s)

                with patch("agents.image_agent.mongodb", fake_db), patch(
                    "agents.image_agent.anyio",
                    types.SimpleNamespace(
                        Semaphore=asyncio.Semaphore,
                        to_thread=types.SimpleNamespace(
                            run_sync=lambda fn, *a, abandon_on_cancel=None, **kw: asyncio.sleep(0, result=fn(*a, **kw))
                        ),
                    ),
                ), patch("agents.image_agent.SCENES_COLLECTION", "scenes"), patch(
                    "agents.image_agent.ASSETS_COLLECTION", "assets"
                ), patch(
                    "agents.image_agent.with_document_id", side_effect=lambda d, _id: {**d, "_id": _id}
                ), patch(
                    "agents.image_agent.VideoTaskStatus", types.SimpleNamespace(IMAGE_GENERATING="image_generating")
                ), patch(
                    "agents.image_agent.Asset",
                    side_effect=lambda **kw: types.SimpleNamespace(**kw, model_dump=lambda: dict(kw)),
                ), patch(
                    "agents.image_agent.settings", types.SimpleNamespace(max_images_per_task=20, video_provider="wan")
                ), patch("agents.image_agent.get_image_service", return_value=StudioPreferT2ISvc()), patch(
                    "agents.image_agent.dispatch_generation", side_effect=lambda t, o, k, fn: fn()
                ), patch("agents.image_agent.mark_agent_start", new=AsyncMock()), patch(
                    "agents.image_agent.mark_agent_success", new=AsyncMock()
                ), patch("agents.image_agent.mark_agent_failed", new=AsyncMock()), patch(
                    "services.character_service.character_service.sync_cast_cards_to_library", new=AsyncMock()
                ), patch(
                    "services.media_quality.resolve_media",
                    side_effect=lambda p: (tmp_dir / f"{Path(p).stem}.png") if "cast_ref_" in str(p) else Path(p),
                ):
                    asyncio.run(ImageAgent().run("t_sf_cast"))

                by_sid = {c["scene_id"]: c for c in calls}
                self.assertEqual(by_sid["cast_ref_1"]["ratio"], "16:9")
                self.assertIsNone(by_sid["scene_01"]["refs"])
                self.assertIsNone(by_sid["scene_02"]["refs"])
                for sid in ("scene_01", "scene_02"):
                    saved_scene = fake_db.cols["scenes"][f"t_sf_cast_{sid}"]
                    self.assertEqual(len(saved_scene.get("image_version", "")), 8)
                    self.assertIsNotNone(saved_scene.get("updated_at"))

    def test_no_cache_static_files_sets_cache_control(self):
        main_path = Path(__file__).resolve().parents[1] / "main.py"

        class FakeStaticBase:
            def __init__(self, directory=None, **kw):
                self.directory = directory
                self.last_scope = None

            async def get_response(self, path, scope):
                self.last_scope = scope
                return types.SimpleNamespace(headers={})

        fake_fastapi = types.ModuleType("fastapi")
        fake_fastapi.FastAPI = lambda **kw: types.SimpleNamespace(
            include_router=lambda r: None,
            mount=lambda path, app, name=None: None,
            get=lambda *a, **kw: (lambda fn: fn),
        )
        fake_statics = types.ModuleType("fastapi.staticfiles")
        fake_statics.StaticFiles = FakeStaticBase

        fake_core_db = types.ModuleType("core.database")
        fake_core_db.mongodb = types.SimpleNamespace()

        stubs = {
            "config": types.SimpleNamespace(
                settings=types.SimpleNamespace(
                    app_name="VideoAgent",
                    mongodb_db_name="video_agent",
                    deepseek_provider="deepseek",
                    qwen_image_provider="siliconflow",
                    wan_video_provider="siliconflow",
                )
            ),
            "fastapi": fake_fastapi,
            "fastapi.staticfiles": fake_statics,
            "core.database": fake_core_db,
        }
        for rname in (
            "routes.video_routes",
            "routes.settings_routes",
            "routes.comfyui_routes",
            "routes.character_routes",
            "routes.series_routes",
            "routes.shoot_review_routes",
            "routes.usage_routes",
            "routes.drama_craft_routes",
        ):
            mod = types.ModuleType(rname)
            mod.router = types.SimpleNamespace()
            stubs[rname] = mod

        with patch.dict(sys.modules, {**_complete_pydantic_patch(), **stubs}), tempfile.TemporaryDirectory() as tmp:
            spec = importlib.util.spec_from_file_location("test_loaded_main", main_path)
            loaded_main = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(loaded_main)
            handler = loaded_main.NoCacheStaticFiles(directory=tmp)
            resp = asyncio.run(handler.get_response("scene_01.png", {}))
            self.assertEqual(resp.headers.get("Cache-Control"), "no-cache, must-revalidate")

            # Video Range requests must strip If-None-Match/If-Modified-Since so 206 Partial Content is never short-circuited to 304
            range_scope = {
                "headers": [
                    (b"range", b"bytes=65536-"),
                    (b"if-none-match", b'"etag-123"'),
                    (b"if-modified-since", b"Thu, 19 Nov 2026 08:04:08 GMT"),
                ]
            }
            vid_resp = asyncio.run(handler.get_response("videos/scene_05.mp4", range_scope))
            self.assertEqual(vid_resp.headers.get("Cache-Control"), "public, max-age=3600")
            forwarded_keys = [k.lower() for k, _ in handler.last_scope.get("headers", [])]
            self.assertIn(b"range", forwarded_keys)
            self.assertNotIn(b"if-none-match", forwarded_keys)
            self.assertNotIn(b"if-modified-since", forwarded_keys)

    def test_video_agent_rebalances_short_storyboard_flow_clips_on_provider_switch(self):
        with patch.dict(sys.modules, _complete_pydantic_patch()):
            from agents.video_agent import VideoAgent

            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                first_png = tmp_dir / "first.png"
                first_png.write_bytes(_png_bytes((160, 160), (90, 100, 110), textured=True))
                clip_mp4 = tmp_dir / "out.mp4"
                clip_mp4.write_bytes(b"fake-mp4-bytes")

                class SiliconFlowMiniMaxStub:
                    model = "MiniMaxAI/MiniMax-H3"
                    supports_last_frame = True

                    def __init__(self):
                        self.durations = []

                    def generate_video(self, **kw):
                        self.durations.append(kw.get("duration"))
                        return {"status": "success", "asset_path": str(clip_mp4), "source": "siliconflow"}

                fake_db = _FakeMongo()
                fake_db.cols["video_tasks"]["t_switch"] = {
                    "_id": "t_switch",
                    "task_id": "t_switch",
                    "generation_mode": "storyboard_flow",
                    "ratio": "9:16",
                    "style": "短剧",
                    "duration": 7,
                    "metadata": {"task_plan": {"duration": 7}},
                }
                for idx, (sid, dur, dyn, st) in enumerate(
                    (("scene_06", 5, False, "static_fallback"), ("scene_08", 4, True, "image_done"), ("scene_10", 3, True, "image_done")),
                    1,
                ):
                    fake_db.cols.setdefault("scenes", {})[f"t_switch_{sid}"] = {
                        "_id": f"t_switch_{sid}",
                        "task_id": "t_switch",
                        "scene_id": sid,
                        "scene_index": idx,
                        "duration": dur,
                        "need_dynamic_video": dyn,
                        "status": st,
                        "transition_type": "cut",
                        "image_path": str(first_png),
                        "motion_beats": [
                            {
                                "start": 0,
                                "end": dur,
                                "subject": "林晚",
                                "camera": "近景",
                                "start_state": "站立",
                                "action": "抬眼盯住对方",
                                "end_state": "定格",
                            }
                        ],
                    }

                svc = SiliconFlowMiniMaxStub()
                with patch("agents.video_agent.mongodb", fake_db), patch(
                    "agents.video_agent.SCENES_COLLECTION", "scenes"
                ), patch("agents.video_agent.ASSETS_COLLECTION", "assets"), patch(
                    "agents.video_agent.with_document_id", side_effect=lambda d, _id: {**d, "_id": _id}
                ), patch(
                    "agents.video_agent.VideoTaskStatus", types.SimpleNamespace(VIDEO_GENERATING="video_generating")
                ), patch(
                    "agents.video_agent.Asset",
                    side_effect=lambda **kw: types.SimpleNamespace(**kw, model_dump=lambda: dict(kw)),
                ), patch(
                    "agents.video_agent.anyio",
                    types.SimpleNamespace(
                        Semaphore=asyncio.Semaphore,
                        to_thread=types.SimpleNamespace(
                            run_sync=lambda fn, *a, abandon_on_cancel=None, **kw: asyncio.sleep(0, result=fn(*a, **kw))
                        ),
                    ),
                ), patch("agents.video_agent.video_service_for_task", return_value=svc), patch(
                    "agents.video_agent.dispatch_generation", side_effect=lambda t, o, k, fn: fn()
                ), patch("agents.video_agent.resolve_media", side_effect=lambda p: Path(p)), patch(
                    "agents.video_agent.review_enabled", return_value=False
                ), patch("agents.video_agent.mark_agent_start", new=AsyncMock()), patch(
                    "agents.video_agent.mark_agent_success", new=AsyncMock()
                ), patch("agents.video_agent.mark_agent_failed", new=AsyncMock()):
                    out = asyncio.run(VideoAgent().run("t_switch"))

                self.assertEqual(len(out), 3)
                self.assertEqual(svc.durations, [5, 5, 5])
                self.assertTrue(fake_db.cols["scenes"]["t_switch_scene_06"]["need_dynamic_video"])
                self.assertEqual(fake_db.cols["scenes"]["t_switch_scene_06"]["status"], "video_done")
                self.assertEqual(len(fake_db.cols["scenes"]["t_switch_scene_06"].get("video_version", "")), 8)
                self.assertEqual(fake_db.cols["scenes"]["t_switch_scene_08"]["duration"], 5)
                self.assertEqual(fake_db.cols["scenes"]["t_switch_scene_10"]["duration"], 5)
                self.assertEqual(fake_db.cols["video_tasks"]["t_switch"]["duration"], 15)

    def test_siliconflow_video_cleans_unconfirmed_job_and_retries_transient_failure(self):
        from services.video.siliconflow_video_service import SiliconFlowVideoService

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            first_png = tmp_dir / "first.png"
            first_png.write_bytes(_png_bytes((160, 160), (90, 100, 110), textured=True))

            submits = []
            polls = []

            class FakeResp:
                def __init__(self, status=200, body=None, content=b""):
                    self.status_code = status
                    self._body = body or {}
                    self.content = content
                    self.text = json.dumps(self._body)

                def json(self):
                    return self._body

                def raise_for_status(self):
                    if self.status_code >= 400:
                        raise RuntimeError(f"HTTP {self.status_code}")

            class FakeVidClient:
                def __init__(self, **kw):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

                def post(self, url, json=None, headers=None):
                    if url.endswith("/video/submit"):
                        submits.append(json)
                        return FakeResp(200, {"requestId": f"req-{len(submits)}"})
                    polls.append(json)
                    if len(polls) == 1:
                        return FakeResp(200, {"status": "Failed", "reason": "Request failed: Unknown error."})
                    return FakeResp(200, {"status": "Succeed", "results": {"videos": [{"url": "https://cdn.sf.test/ok.mp4"}]}})

                def get(self, url, timeout=120):
                    return FakeResp(200, content=b"valid-mp4-bytes")

            vid_profile = {"url": "https://api.siliconflow.cn/v1", "model": "MiniMaxAI/MiniMax-H3", "key": "sf-k"}
            with patch("services.video.siliconflow_video_service.model_config", return_value=vid_profile), patch(
                "services.video.siliconflow_video_service.settings",
                types.SimpleNamespace(enable_paid_api=True, siliconflow_video_resolution="768p"),
            ), patch("services.video.siliconflow_video_service.httpx.Client", FakeVidClient), patch(
                "services.video.siliconflow_video_service.time.sleep", return_value=None
            ):
                svc = SiliconFlowVideoService(storage_root=tmp_dir)
                _, job = svc._render_paths("t_unconf", "scene_06", str(first_png), "推门而入", 5, "9:16", "768p", None)
                job.parent.mkdir(parents=True, exist_ok=True)
                job.write_text('{"task_id": null}', encoding="utf-8")
                self.assertFalse(svc.has_pending_render("t_unconf", "scene_06", str(first_png), "推门而入", 5, "9:16", "768p"))

                res = svc.generate_video("t_unconf", "scene_06", str(first_png), video_prompt="推门而入", duration=5, ratio="9:16")

            self.assertEqual(res["status"], "success")
            self.assertEqual(len(submits), 2)
            self.assertEqual(len(polls), 2)
            self.assertFalse(job.exists())

    def test_series_create_with_first_episode_task_and_bind_episode(self):
        fake_db = _FakeMongo()
        asyncio.run(
            fake_db.insert_one(
                "video_tasks",
                {
                    "_id": "task_ep1_done",
                    "task_id": "task_ep1_done",
                    "topic": "《后厨倒计时》：百年老字号传人二十分钟内自证清白",
                    "user_input": "一针测温反杀调包阴谋",
                    "status": "success",
                    "duration": 60,
                    "style": "短剧",
                    "ratio": "9:16",
                },
            )
        )
        asyncio.run(
            fake_db.insert_one(
                "scenes",
                {
                    "_id": "task_ep1_done_scene_01",
                    "task_id": "task_ep1_done",
                    "scene_id": "scene_01",
                    "duration": 60,
                    "start_state": "林晚持针",
                    "end_state": "冷库落锁",
                    "voiceover": "冷库谁锁的？",
                },
            )
        )
        with patch.dict(sys.modules, _complete_pydantic_patch()):
            from models.series_model import CreateSeriesRequest
            from services.series_service import SeriesService

            svc = SeriesService()
            with patch("services.series_service.mongodb", fake_db), patch(
                "services.task_service.mongodb", fake_db
            ):
                req = CreateSeriesRequest(
                    title="后厨倒计时",
                    brief="百年老字号传人二十分钟内自证清白",
                    total_episodes=3,
                    episode_duration=60,
                    episode_duration_min=45,
                    episode_duration_max=90,
                    generation_mode="auto_director",
                    style="短剧",
                    ratio="9:16",
                    market_mode="cn",
                    character_ids=[],
                    auto_continue=False,
                    pre_shoot_review=True,
                    generation_limit=None,
                    continuity_ledgers=None,
                    first_episode_script=None,
                    initial_episodes=[
                        {
                            "episode_num": 1,
                            "title": "第1集：一根86度的针",
                            "opening_hook": "倒计时逼签",
                            "core_event": "测温针验出86度",
                            "ending_hook": "冷库突然落锁",
                        },
                        {
                            "episode_num": 2,
                            "title": "第2集：冷库盲测",
                            "opening_hook": "直播镜头怼到灶台",
                            "core_event": "备用原浆复原清汤",
                            "ending_hook": "金匙榜封存盲测",
                        },
                        {
                            "episode_num": 3,
                            "title": "第3集：终局反杀",
                            "opening_hook": "盲测揭晓",
                            "core_event": "周明哲阴谋败露",
                            "ending_hook": "老字号守住招牌",
                        },
                    ],
                    first_episode_task_id="task_ep1_done",
                )
                project = asyncio.run(svc.create_series(req))
                self.assertTrue(project["outline_confirmed"])
                self.assertEqual(project["status"], "awaiting_confirmation")
                self.assertEqual(project["episodes"][0]["task_id"], "task_ep1_done")
                self.assertEqual(project["episodes"][0]["status"], "needs_confirmation")
                self.assertTrue(project["episodes"][0]["output_snapshot_id"])
                self.assertEqual(
                    fake_db.cols["video_tasks"]["task_ep1_done"]["series_id"],
                    project["series_id"],
                )


if __name__ == "__main__":
    unittest.main()
