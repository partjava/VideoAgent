import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

from services.shot_continuity import (
    ChannelStats,
    ShotColorMatch,
    SHOT_MATCH_GAIN_LIMITS,
    adapt_video_submission,
    apply_color_match_to_clip,
    apply_cut_transitions,
    audit_final_video_continuity,
    build_scene_continuity_handoff,
    build_scene_runs,
    measure_clip_rgb_stats,
    measure_loudnorm_filter,
    plan_shot_color_match,
    resolve_provider_dialect,
    resolve_vidu_movement_amplitude,
    speech_ducking_gain,
)


def _runtime_stubs():
    stubs = {}
    if "config" not in sys.modules:
        try:
            __import__("config")
        except ModuleNotFoundError:
            stubs["config"] = types.SimpleNamespace(
                settings=types.SimpleNamespace(
                    enable_paid_api=True,
                    dashscope_api_key="fake-dash",
                    dashscope_api_base="https://dashscope.aliyuncs.com",
                    volcengine_api_key="fake-volc",
                    volcengine_model="doubao-seedance-1-5-pro-251215",
                    vidu_api_token="fake-vidu",
                    vidu_model="viduq3-turbo",
                    wan_video_model="wan3.0-video",
                )
            )
    if "httpx" not in sys.modules:
        try:
            __import__("httpx")
        except ModuleNotFoundError:
            stubs["httpx"] = types.SimpleNamespace(
                AsyncClient=None,
                Client=None,
                TimeoutException=TimeoutError,
                RequestError=OSError,
                NetworkError=OSError,
                HTTPStatusError=RuntimeError,
                ReadTimeout=TimeoutError,
                ConnectTimeout=TimeoutError,
                TransportError=OSError,
            )
    def _make_pydantic_stub():
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
        mod.SecretStr = type(
            "SecretStr",
            (),
            {
                "__init__": lambda self, val="": setattr(self, "_val", val),
                "get_secret_value": lambda self: self._val,
            },
        )
        mod.ConfigDict = lambda **kw: kw
        mod.field_validator = lambda *a, **k: (lambda fn: fn)
        mod.model_validator = lambda *a, **k: (lambda fn: fn)
        mod.ValidationError = ValueError
        return mod

    if not getattr(sys.modules.get("pydantic"), "__file__", None):
        stubs["pydantic"] = _make_pydantic_stub()

    if not getattr(sys.modules.get("services.tts_service"), "__file__", None):
        existing_tts = sys.modules.get("services.tts_service")
        stubs["services.tts_service"] = types.SimpleNamespace(
            resolve_voice_for_speaker=lambda *a, **k: "zh-CN-YunyangNeural",
            tts_service=getattr(existing_tts, "tts_service", types.SimpleNamespace()),
        )

    for mod_name, builder in [
        ("pymongo", lambda: types.SimpleNamespace(errors=types.SimpleNamespace(DuplicateKeyError=Exception))),
        ("pymongo.errors", lambda: types.SimpleNamespace(DuplicateKeyError=Exception)),
        ("core.database", lambda: types.SimpleNamespace(mongodb=types.SimpleNamespace())),
        ("services.audio_timing", lambda: types.SimpleNamespace(narration_speed=lambda *a, **k: 1.0)),
    ]:
        if mod_name not in sys.modules:
            try:
                __import__(mod_name)
            except ModuleNotFoundError:
                stubs[mod_name] = builder()
    if "anyio" not in sys.modules:
        try:
            __import__("anyio")
        except ModuleNotFoundError:
            import asyncio
            stubs["anyio"] = types.SimpleNamespace(
                Semaphore=asyncio.Semaphore,
                to_thread=types.SimpleNamespace(
                    run_sync=lambda fn, *a, **k: asyncio.to_thread(fn, *a, **k)
                )
            )
    return stubs


class ShotContinuityTests(unittest.TestCase):
    def test_provider_dialect_adaptation_and_vidu_amplitude(self):
        class WanVideoService:
            model = "wan3.0-video"

        class DoubaoVideoService:
            model = "doubao-seedance-1-5-pro-251215"
            supports_last_frame = True

        class ViduVideoService:
            model = "viduq3-turbo"
            supports_movement_amplitude = True

        raw_prompt = (
            "[站位与轴线] 【双人轴线锁：左林墨·右沈晚】\n"
            "[衔接模式: 换机位硬切]\n"
            "[节奏动作链]\n"
            "[0-1s]机位:近景｜起:林墨低头持杯｜林墨抬眼看向@沈晚｜止:眼神停在对方眉心"
        )
        wan_svc = WanVideoService()
        wan_out, wan_opts = adapt_video_submission(wan_svc, {"scene_id": "s1"}, raw_prompt)
        self.assertEqual(resolve_provider_dialect(wan_svc), "wan")
        self.assertNotIn("[站位与轴线]", wan_out)
        self.assertNotIn("【双人轴线锁", wan_out)
        self.assertNotIn("@", wan_out)
        self.assertIn("(0-1s)", wan_out)
        self.assertEqual(wan_opts, {})

        with tempfile.TemporaryDirectory() as tmp:
            tail = Path(tmp) / "tail.png"
            Image.new("RGB", (64, 64), (80, 90, 100)).save(tail)
            doubao_svc = DoubaoVideoService()
            db_out, db_opts = adapt_video_submission(
                doubao_svc,
                {"scene_id": "s2", "last_frame_target_path": str(tail)},
                raw_prompt,
            )
            self.assertIn("0-1s：", db_out)
            self.assertEqual(db_opts, {"last_frame_path": str(tail)})

        vidu_svc = ViduVideoService()
        _, small_opts = adapt_video_submission(
            vidu_svc,
            {"shot_type": "近景", "dialogue_lines": [{"speaker": "甲", "text": "别动", "start": 0.2}]},
            raw_prompt,
        )
        _, large_opts = adapt_video_submission(
            vidu_svc,
            {"shot_type": "中景", "transition_mode": "whip_morph", "video_prompt": "镜头向右甩镜追拍"},
            raw_prompt,
        )
        self.assertEqual(small_opts, {"movement_amplitude": "small"})
        self.assertEqual(large_opts, {"movement_amplitude": "large"})
        self.assertEqual(resolve_vidu_movement_amplitude({"shot_type": "中景", "video_prompt": "缓慢起身走近桌边"}), "medium")

        mock_svc = Mock()
        _, mock_opts = adapt_video_submission(mock_svc, {"last_frame_target_path": "/nonexistent.png"}, raw_prompt)
        self.assertEqual(mock_opts, {})

    def test_cross_shot_handoff_rules(self):
        cast = [
            {"character_id": "c1", "name": "陈安"},
            {"character_id": "c2", "name": "赵敏"},
        ]
        scenes = [
            {"scene_id": "s1", "shot_type": "中景", "character_ids": ["c1"], "end_state": "单手按桌"},
            {"scene_id": "s2", "shot_type": "中景", "character_ids": ["c1", "c2"], "transition_type": "cut"},
            {"scene_id": "s3", "shot_type": "中景", "character_ids": ["c1", "c2"], "transition_type": "cut"},
            {"scene_id": "s4", "shot_type": "中景", "character_ids": ["c1"], "transition_type": "continue_action"},
        ]
        h1 = build_scene_continuity_handoff(scenes, 1, ratio="9:16", bound_cast=cast)
        self.assertEqual(h1["cast_delta"]["entering"], ["赵敏"])
        self.assertIn("新增角色", h1["entry_instruction"])
        self.assertIn("竖屏9:16", h1["ratio_pacing"])

        h2 = build_scene_continuity_handoff(scenes, 2, ratio="16:9", bound_cast=cast)
        self.assertIn("横屏16:9", h2["ratio_pacing"])
        self.assertIn("连续两镜相同景别", h2["scale_advice"])
        self.assertIn("收束至清晰可截稳态", h2["scale_advice"])
        self.assertTrue(h2["next_is_continue"])

        h3 = build_scene_continuity_handoff(scenes, 3, ratio="9:16", bound_cast=cast)
        self.assertIn("0级落差", h3["scale_advice"])

    def test_shot_color_match_convergence_lut_and_clip_transform(self):
        scenes = [
            {"scene_id": "s1", "transition_type": "cut"},
            {"scene_id": "s2", "transition_type": "cut"},
            {"scene_id": "s3", "transition_type": "cut"},
            {"scene_id": "s4", "transition_type": "scene_change"},
            {"scene_id": "s5", "transition_type": "cut"},
        ]
        runs = build_scene_runs(scenes)
        self.assertEqual(runs, [[0, 1, 2], [3, 4]])

        stats = {
            0: ChannelStats((90.0, 95.0, 100.0), (35.0, 35.0, 35.0)),
            1: ChannelStats((120.0, 120.0, 120.0), (45.0, 45.0, 45.0)),
            2: ChannelStats((160.0, 155.0, 150.0), (55.0, 55.0, 55.0)),
            3: ChannelStats((60.0, 60.0, 60.0), (1.0, 1.0, 1.0)),
            4: ChannelStats((160.0, 160.0, 160.0), (70.0, 70.0, 70.0)),
        }
        plan = plan_shot_color_match(scenes, lambda idx: stats[idx])
        self.assertIn("s1", plan)
        self.assertNotIn("s2", plan)
        self.assertIn("s3", plan)
        m_s1 = plan["s1"]
        after_r = stats[0].mean[0] * m_s1.gains[0] + m_s1.offsets[0]
        self.assertAlmostEqual(after_r, 90.0 + 0.68 * (120.0 - 90.0), places=0)
        self.assertLessEqual(plan["s4"].gains[0], SHOT_MATCH_GAIN_LIMITS[1])
        self.assertGreaterEqual(plan["s4"].gains[0], SHOT_MATCH_GAIN_LIMITS[0])

        class DummyClip:
            duration = 2.0

            def get_frame(self, _t):
                return np.full((32, 32, 3), 90, dtype=np.uint8)

            def fl_image(self, fn):
                out_frame = fn(self.get_frame(0.5))
                return SimpleNamespace(frame=out_frame)

        measured = measure_clip_rgb_stats(DummyClip())
        self.assertIsNotNone(measured)
        self.assertAlmostEqual(measured.mean[0], 90.0, places=1)
        transformed = apply_color_match_to_clip(DummyClip(), m_s1)
        self.assertEqual(transformed.frame.dtype, np.uint8)
        self.assertGreater(float(transformed.frame[..., 0].mean()), 100.0)

    def test_speech_ducking_and_loudnorm_fallback(self):
        times = np.array([0.0, 0.8, 1.0, 1.5, 2.0, 2.22, 3.0], dtype=np.float32)
        gains = speech_ducking_gain(times, [(1.0, 2.0)], base_gain=1.0, duck_gain=0.4, ramp=0.2)
        self.assertAlmostEqual(float(gains[0]), 1.0, places=3)
        self.assertAlmostEqual(float(gains[1]), 1.0, places=3)
        self.assertAlmostEqual(float(gains[2]), 0.4, places=3)
        self.assertAlmostEqual(float(gains[6]), 1.0, places=3)
        self.assertIsNone(measure_loudnorm_filter("/nonexistent/ffmpeg", Path("missing.mp4")))

    def test_editor_cut_transitions_keep_exact_bounds(self):
        class MiniClip:
            def __init__(self, duration=2.0, level=180):
                self.duration = duration
                self.level = level

            def fl(self, fn):
                sample_tail = fn(lambda _t: np.full((16, 16, 3), self.level, dtype=np.uint8), self.duration - 0.01)
                sample_head = fn(lambda _t: np.full((16, 16, 3), self.level, dtype=np.uint8), 0.01)
                c = MiniClip(self.duration, self.level)
                c.sample_tail = sample_tail
                c.sample_head = sample_head
                return c

        clips = [MiniClip(2.0, 180), MiniClip(2.0, 180), MiniClip(2.0, 180)]
        scenes = [
            {"scene_id": "s1", "transition_type": "cut", "transition_mode": "angle_cut"},
            {"scene_id": "s2", "transition_type": "scene_change", "transition_mode": "angle_cut"},
            {"scene_id": "s3", "transition_type": "cut", "transition_mode": "whip_morph"},
        ]
        applied = apply_cut_transitions(clips, scenes)
        self.assertEqual([a["mode"] for a in applied], ["scene_fade", "whip_morph"])
        self.assertEqual(clips[0].duration, 2.0)
        self.assertLess(float(clips[0].sample_tail.mean()), 120.0)
        self.assertEqual(clips[1].sample_head.dtype, np.uint8)

    def test_audit_final_video_continuity_flags_jumps_and_glitches(self):
        fps = 10.0
        dur = 4.0

        def make_frame(t: float) -> np.ndarray:
            if abs(t - 1.25) < 0.06:
                return np.full((32, 32, 3), 245, dtype=np.uint8)
            if t < 2.0:
                return np.full((32, 32, 3), 40, dtype=np.uint8)
            return np.full((32, 32, 3), 190, dtype=np.uint8)

        fake_audio = SimpleNamespace(
            get_frame=lambda ts: (
                np.full((len(ts), 2), 0.05, dtype=np.float32)
                if np.mean(ts) < 2.0
                else np.full((len(ts), 2), 0.35, dtype=np.float32)
            )
        )

        class FakeVideoFileClip:
            def __init__(self, _path):
                self.duration = dur
                self.fps = fps
                self.audio = fake_audio

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get_frame(self, t):
                return make_frame(float(t))

        fake_moviepy = types.ModuleType("moviepy")
        fake_moviepy_editor = types.ModuleType("moviepy.editor")
        fake_moviepy_editor.VideoFileClip = FakeVideoFileClip
        fake_moviepy.editor = fake_moviepy_editor

        scenes = [
            {"scene_id": "s1", "scene_index": 1, "transition_type": "cut", "transition_mode": "angle_cut"},
            {"scene_id": "s2", "scene_index": 2, "transition_type": "cut", "transition_mode": "angle_cut"},
        ]
        timeline = [
            {"scene_id": "s1", "start": 0.0, "end": 2.0, "duration": 2.0},
            {"scene_id": "s2", "start": 2.0, "end": 4.0, "duration": 2.0},
        ]
        with patch.dict(sys.modules, {"moviepy": fake_moviepy, "moviepy.editor": fake_moviepy_editor}):
            report = audit_final_video_continuity(
                Path("dummy.mp4"),
                scenes,
                timeline,
                shot_match_plan={"s1": ShotColorMatch((1.1, 1.1, 1.1), (5.0, 5.0, 5.0))},
            )
        self.assertEqual(len(report["cut_checks"]), 1)
        self.assertGreater(report["cut_checks"][0]["delta_luma"], 100.0)
        self.assertGreaterEqual(len(report["suspect_frames"]), 1)
        self.assertEqual(len(report["audio_jumps"]), 1)
        self.assertGreater(report["audio_jumps"][0]["delta_db"], 14.0)
        self.assertLess(report["smoothness_score"], 100)
        self.assertEqual(report["shot_color_match_count"], 1)

    def test_doubao_wan_and_vidu_accept_continuity_extensions(self):
        fake_settings = types.SimpleNamespace(
            enable_paid_api=True,
            dashscope_api_key="fake-dash",
            dashscope_api_base="https://dashscope.aliyuncs.com",
            volcengine_api_key="fake-volc",
            volcengine_model="doubao-seedance-1-5-pro-251215",
            vidu_api_token="fake-vidu",
            vidu_model="viduq3-turbo",
            wan_video_model="wan3.0-video",
        )
        with patch.dict(sys.modules, _runtime_stubs()), tempfile.TemporaryDirectory() as tmp:
            from services.video.doubao_video_service import DoubaoVideoService
            from services.video.wan_video_service import WanVideoService
            from services.video.vidu_video_service import ViduVideoService

            first = Path(tmp) / "first.png"
            last = Path(tmp) / "last.png"
            Image.new("RGB", (64, 64), (20, 30, 40)).save(first)
            Image.new("RGB", (64, 64), (90, 100, 110)).save(last)

            client = Mock()
            client.__enter__ = Mock(return_value=client)
            client.__exit__ = Mock(return_value=False)
            client.post.side_effect = RuntimeError("captured")

            doubao = DoubaoVideoService()
            with patch("services.video.doubao_video_service.settings", fake_settings), patch(
                "services.video.doubao_video_service.httpx.Client", return_value=client
            ):
                with self.assertRaisesRegex(RuntimeError, "captured"):
                    doubao.generate_video("t1", "s1", str(first), "test", duration=5, last_frame_path=str(last))
            roles = [item.get("role") for item in client.post.call_args.kwargs["json"]["content"] if item.get("type") == "image_url"]
            self.assertEqual(roles, ["first_frame", "last_frame"])

            with patch("services.video.wan_video_service.model_config", return_value={
                "url": "https://dashscope.aliyuncs.com", "model": "wan3.0-video", "key": "fake-dash",
            }), patch("services.video.wan_video_service.settings", fake_settings), patch(
                "services.video.wan_video_service.httpx.Client", return_value=client
            ):
                wan = WanVideoService(model="wan3.0-video", storage_root=tmp)
                self.assertTrue(wan.supports_last_frame)
                with self.assertRaisesRegex(RuntimeError, "captured"):
                    wan.generate_video("t1", "s1", str(first), "test", duration=5, last_frame_path=str(last))
            media_types = [m["type"] for m in client.post.call_args.kwargs["json"]["input"]["media"]]
            self.assertEqual(media_types, ["first_frame", "last_frame"])

            vidu = ViduVideoService()
            with patch("services.video.vidu_video_service.settings", fake_settings), patch(
                "services.video.vidu_video_service.httpx.Client", return_value=client
            ), patch(
                "services.media_quality.write_aspect_frame",
                side_effect=lambda src, dst, r: Image.open(src).save(dst),
            ):
                with self.assertRaisesRegex(RuntimeError, "captured"):
                    vidu.generate_video("t1", "s1", str(first), "test", duration=5, movement_amplitude="small")
            self.assertEqual(client.post.call_args.kwargs["json"]["movement_amplitude"], "small")

    def test_auto_director_variable_duration_contract_and_rebalance(self):
        from services.generation_contract import (
            provider_duration_bounds,
            rebalance_motion_beats_duration,
            validate_auto_director_scenes,
            validate_motion_beats,
        )

        self.assertEqual(provider_duration_bounds("wan3", "wan3.0-video"), (4, 12))
        self.assertEqual(provider_duration_bounds("vidu", "viduq3-turbo"), (4, 10))

        scenes = [
            {"scene_id": "s1", "duration": 5, "need_dynamic_video": True, "transition_type": "cut"},
            {"scene_id": "s2", "duration": 9, "need_dynamic_video": True, "transition_type": "continue_action"},
            {"scene_id": "s3", "duration": 11, "need_dynamic_video": True, "transition_type": "scene_change"},
            {"scene_id": "s4", "duration": 6, "need_dynamic_video": True, "transition_type": "cut"},
        ]
        validate_auto_director_scenes(scenes, target_duration=31, bounds=(4, 12))

        with self.assertRaises(ValueError):
            validate_auto_director_scenes(
                [{"scene_id": "bad", "duration": 3, "need_dynamic_video": True}], bounds=(4, 12)
            )
        with self.assertRaises(ValueError):
            validate_auto_director_scenes(
                [{"scene_id": "bad", "duration": 13, "need_dynamic_video": True}], bounds=(4, 12)
            )

        beats_8s = [
            {
                "start": i,
                "end": i + 1,
                "subject": "林晚",
                "camera": "中近景稳定跟拍",
                "start_state": f"状态{i}",
                "action": f"动作{i}",
                "end_state": f"收尾状态{i}" if i == 7 else f"状态{i + 1}",
            }
            for i in range(8)
        ]
        extended = rebalance_motion_beats_duration(beats_8s, 8, 10)
        validate_motion_beats(extended, 10, detailed=True, per_second=True)
        self.assertEqual(extended[-1]["end_state"], "收尾状态7")

        shrunk = rebalance_motion_beats_duration(extended, 10, 6)
        validate_motion_beats(shrunk, 6, detailed=True, per_second=True)
        self.assertEqual(shrunk[-1]["end_state"], "收尾状态7")

    def test_director_self_heal_diagnose_plan_and_budget(self):
        import asyncio

        stubs = _runtime_stubs()
        for mod_name, builder in [
            ("openai", lambda: types.SimpleNamespace(OpenAI=object)),
            ("dashscope", lambda: types.SimpleNamespace()),
            ("langgraph", lambda: types.SimpleNamespace()),
            ("langgraph.graph", lambda: types.SimpleNamespace(StateGraph=object, END="END")),
            ("langgraph.types", lambda: types.SimpleNamespace(Send=object)),
        ]:
            if mod_name not in sys.modules:
                try:
                    __import__(mod_name)
                except ModuleNotFoundError:
                    stubs[mod_name] = builder()

        with patch.dict(sys.modules, stubs), tempfile.TemporaryDirectory() as tmp:
            from services.director_heal_service import (
                DEFAULT_HEAL_PROMPT_LOCK,
                diagnose_heal_candidates,
                execute_director_self_heal,
                plan_scene_self_heal,
            )
            from services.generation_usage import GenerationLimitExceeded

            scenes = [
                {
                    "scene_id": "s1",
                    "scene_index": 1,
                    "duration": 8,
                    "need_dynamic_video": True,
                    "transition_type": "cut",
                    "video_prompt": "林晚推门猛然甩头看窗外",
                    "motion_beats": [
                        {
                            "start": i,
                            "end": i + 1,
                            "subject": "林晚",
                            "camera": "中近景",
                            "start_state": "推门",
                            "action": "猛然甩头" if i == 2 else "站稳观察",
                            "end_state": "定格门边",
                        }
                        for i in range(8)
                    ],
                },
                {
                    "scene_id": "s2",
                    "scene_index": 2,
                    "duration": 6,
                    "need_dynamic_video": True,
                    "transition_type": "continue_action",
                    "join_review": {"review_suggested": True, "difference": 0.28},
                    "auto_healed_count": 1,
                },
                {
                    "scene_id": "s3",
                    "scene_index": 3,
                    "duration": 9,
                    "need_dynamic_video": True,
                    "transition_type": "cut",
                    "status": "static_fallback",
                },
            ]
            timeline = [
                {"scene_id": "s1", "start": 0.0, "end": 8.0, "duration": 8.0},
                {"scene_id": "s2", "start": 8.0, "end": 14.0, "duration": 6.0},
                {"scene_id": "s3", "start": 14.0, "end": 23.0, "duration": 9.0},
            ]
            quality_report = {
                "visual_review": {"issues": [{"scene_id": "s1", "issue": "主体突然闪现偏移"}]},
                "continuity_audit": {"suspect_frames": [{"time": 2.5}], "issues": []},
                "static_fallbacks": ["s3"],
            }
            ranked = diagnose_heal_candidates(scenes, timeline, quality_report, max_repairs=2)
            self.assertEqual([c["scene_id"] for c in ranked], ["s1", "s3"])

            mock_llm = Mock()
            mock_llm.doctor_scene_prompt.return_value = {
                "video_prompt": "林晚缓缓推门，中近景保持稳态照明",
                "motion_beats": [
                    {
                        "start": i,
                        "end": i + 1,
                        "subject": "林晚",
                        "camera": "中近景固定",
                        "start_state": "推门前",
                        "action": "稳步推门入室",
                        "end_state": "平稳定格",
                    }
                    for i in range(8)
                ],
                "transition_type": "cut",
                "heal_summary": "消除甩头闪变",
            }
            llm_plan = plan_scene_self_heal(scenes[0], ranked[0]["reasons"], allow_llm=True, llm_service=mock_llm)
            self.assertEqual(llm_plan["rewrite_source"], "llm_doctor")
            self.assertIn("消除甩头闪变", llm_plan["heal_summary"])

            fb_plan = plan_scene_self_heal(scenes[0], ranked[0]["reasons"], allow_llm=False)
            self.assertEqual(fb_plan["rewrite_source"], "deterministic_fallback")
            self.assertIn(DEFAULT_HEAL_PROMPT_LOCK.rstrip("。"), fb_plan["video_prompt"])
            self.assertEqual(fb_plan["motion_beats"][2]["action"], "平稳转向")

            first_png = Path(tmp) / "s1.png"
            repaired_mp4 = Path(tmp) / "s1_heal.mp4"
            Image.new("RGB", (64, 64), (10, 20, 30)).save(first_png)
            repaired_mp4.write_bytes(b"fake-mp4")

            scenes[0]["image_path"] = str(first_png)
            fake_task = {
                "task_id": "t_heal",
                "generation_mode": "auto_director",
                "auto_heal": True,
                "max_heal_attempts": 1,
                "ratio": "9:16",
                "style": "短剧",
                "metadata": {},
            }
            fake_db = Mock()
            fake_db.find_many = Mock(side_effect=lambda *a, **k: asyncio.sleep(0, result=[dict(scenes[0])]))
            fake_db.replace_one = Mock(side_effect=lambda *a, **k: asyncio.sleep(0))
            fake_db.update_one = Mock(side_effect=lambda *a, **k: asyncio.sleep(0))
            fake_task_svc = Mock()
            fake_task_svc.get_task = Mock(side_effect=lambda *a, **k: asyncio.sleep(0, result=fake_task))
            fake_task_svc.update_task_fields = Mock(side_effect=lambda *a, **k: asyncio.sleep(0))

            class FakeVideoService:
                model = "wan3.0-video"

                def generate_video(self, **kwargs):
                    return {"status": "success", "asset_path": str(repaired_mp4), "source": "fake"}

            async def _ok_dispatch(task, op_type, op_key, fn):
                return fn()

            with patch("core.database.mongodb", fake_db), patch(
                "services.director_heal_service.inspect_video", return_value=None
            ), patch("services.director_heal_service.resolve_media", side_effect=lambda p: Path(p)), patch(
                "services.director_heal_service.dispatch_generation", side_effect=_ok_dispatch
            ), patch("services.task_service.task_service", fake_task_svc):
                heal_res = asyncio.run(
                    execute_director_self_heal(
                        "t_heal",
                        {"visual_review": {"issues": [{"scene_id": "s1", "issue": "闪烁"}]}},
                        video_service=FakeVideoService(),
                        llm_service=mock_llm,
                    )
                )
            self.assertTrue(heal_res["healed"])
            self.assertEqual(len(heal_res["repaired_scenes"]), 1)
            fake_task_svc.update_task_fields.assert_called_once()
            self.assertEqual(fake_task_svc.update_task_fields.call_args.args[1]["metadata.heal_attempts_used"], 1)

            async def _quota_dispatch(task, op_type, op_key, fn):
                raise GenerationLimitExceeded("limit reached")

            with patch("core.database.mongodb", fake_db), patch(
                "services.director_heal_service.resolve_media", side_effect=lambda p: Path(p)
            ), patch(
                "services.director_heal_service.dispatch_generation", side_effect=_quota_dispatch
            ), patch("services.task_service.task_service", fake_task_svc):
                quota_res = asyncio.run(
                    execute_director_self_heal(
                        "t_heal",
                        {"visual_review": {"issues": [{"scene_id": "s1", "issue": "闪烁"}]}},
                        video_service=FakeVideoService(),
                        llm_service=mock_llm,
                    )
                )
            self.assertFalse(quota_res["healed"])
            self.assertEqual(quota_res["reason"], "quota_exhausted")

    def test_voice_agent_auto_director_adapts_slot_duration(self):
        import asyncio

        stubs = _runtime_stubs()
        for mod_name, builder in [
            ("openai", lambda: types.SimpleNamespace(OpenAI=object)),
            ("dashscope", lambda: types.SimpleNamespace()),
            ("langgraph", lambda: types.SimpleNamespace()),
            ("langgraph.graph", lambda: types.SimpleNamespace(StateGraph=object, END="END")),
            ("langgraph.types", lambda: types.SimpleNamespace(Send=object)),
        ]:
            if mod_name not in sys.modules:
                try:
                    __import__(mod_name)
                except ModuleNotFoundError:
                    stubs[mod_name] = builder()

        with patch.dict(sys.modules, stubs), tempfile.TemporaryDirectory() as tmp:
            from agents.voice_agent import synthesize_dialogue

            async def _fake_voice(text, output_path, **kwargs):
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(b"mp3")
                return 8.4

            scene = {
                "scene_id": "s1",
                "duration": 6,
                "voice_start": 0,
                "dialogue_lines": [
                    {"speaker": "沈默", "start": 0, "text": "这笔账我们今天当场算清楚，一分都不能少！"}
                ],
            }
            fake_tts = Mock()
            fake_tts.generate_voice = Mock(side_effect=_fake_voice)

            with patch("agents.voice_agent.tts_service", fake_tts):
                audio = asyncio.run(
                    synthesize_dialogue(
                        scene,
                        Path(tmp),
                        "t_voice",
                        "s1",
                        allow_duration_extend=True,
                        max_duration=12,
                    )
                )
            self.assertEqual(len(audio), 1)
            self.assertGreater(scene.get("_adapted_duration", 0), 6)
            self.assertLessEqual(scene["_adapted_duration"], 12)

            locked_scene = {
                "scene_id": "s2",
                "duration": 6,
                "voice_start": 0,
                "dialogue_lines": [
                    {"speaker": "沈默", "start": 0, "text": "这笔账我们今天当场算清楚，一分都不能少！"}
                ],
            }
            with patch("agents.voice_agent.tts_service", fake_tts):
                with self.assertRaisesRegex(ValueError, "Voice too long"):
                    asyncio.run(
                        synthesize_dialogue(
                            locked_scene,
                            Path(tmp),
                            "t_voice",
                            "s2",
                            allow_duration_extend=False,
                            max_duration=12,
                        )
                    )

    def test_deepseek_json_resilience_fences_quotes_and_truncation(self):
        stubs = _runtime_stubs()
        for mod_name, builder in [
            ("langchain_openai", lambda: types.SimpleNamespace(ChatOpenAI=Mock())),
            ("langchain_core", lambda: types.SimpleNamespace()),
            ("langchain_core.messages", lambda: types.SimpleNamespace(
                SystemMessage=lambda content="": SimpleNamespace(content=content),
                HumanMessage=lambda content="": SimpleNamespace(content=content),
            )),
        ]:
            if mod_name not in sys.modules:
                try:
                    __import__(mod_name)
                except ModuleNotFoundError:
                    stubs[mod_name] = builder()

        with patch.dict(sys.modules, stubs):
            from services.llm.deepseek_service import DeepSeekService, _extract_and_parse_json

            # 1. <think> 标签 + markdown 围栏 + 真实换行
            raw_fenced = '<think>推理{忽略}</think>\n```json\n{\n  "video_type": "story",\n  "topic": "第一行\n第二行",\n}\n```'
            parsed1 = _extract_and_parse_json(raw_fenced)
            self.assertEqual(parsed1["video_type"], "story")
            self.assertIn("第一行", parsed1["topic"])

            # 2. 中文内容中混入未转义英文双引号
            raw_quotes = '```json\n{"video_type": "story", "optimized_brief": "沈默说："U盘里还有第二层名单。"", "ending_hook": "手机弹出："你打算怎么谈？""}\n```'
            parsed2 = _extract_and_parse_json(raw_quotes)
            self.assertEqual(parsed2["video_type"], "story")
            self.assertIn("第二层名单", parsed2["optimized_brief"])
            self.assertIn("怎么谈", parsed2["ending_hook"])

            # 3. 嵌套数组截断恢复（值截断 & 键名截断）
            raw_truncated = '```json\n{"video_type": "story", "characters": [{"name": "沈默"}], "plot_beats": [{"beat_name": "起"}, {"beat_name": "截断'
            parsed3 = _extract_and_parse_json(raw_truncated)
            self.assertEqual(parsed3["video_type"], "story")
            self.assertEqual(parsed3["characters"][0]["name"], "沈默")
            self.assertGreaterEqual(len(parsed3["plot_beats"]), 1)

            raw_truncated_key = '{"video_type": "story", "topic": "反转", "plot_beats": [{"beat_name": "起"}, {"incomplete_ke'
            parsed3b = _extract_and_parse_json(raw_truncated_key)
            self.assertEqual(parsed3b["topic"], "反转")
            self.assertEqual(len(parsed3b["plot_beats"]), 2)
            self.assertEqual(parsed3b["plot_beats"][0]["beat_name"], "起")

            # 4. _call_api: response_format 不支持回退 + 失败重试恢复
            calls = []

            class FakeLLM:
                def __init__(self, **kw):
                    self.kw = kw

                def invoke(self, msgs):
                    calls.append(dict(self.kw))
                    if self.kw.get("model_kwargs"):
                        raise RuntimeError("response_format is unsupported")
                    if len(calls) == 2:
                        return SimpleNamespace(content="抱歉我跑题了，重来")
                    return SimpleNamespace(content='{"video_type": "story", "topic": "ok"}')

            fake_settings = types.SimpleNamespace(
                enable_paid_api=True,
                deepseek_api_key="sk-test",
                deepseek_model="deepseek-chat",
                qwen_image_model="qwen-image-2.0",
                wan_video_model="wan3.0-video",
                dashscope_api_base="https://dashscope.aliyuncs.com",
                dashscope_api_key="sk-dash",
            )
            svc = DeepSeekService()
            with patch("services.llm.deepseek_service.settings", fake_settings), patch(
                "services.llm.deepseek_service.model_config",
                return_value={"url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "key": "sk-test"},
            ), patch("services.llm.deepseek_service.ChatOpenAI", side_effect=lambda **kw: FakeLLM(**kw)):
                res = svc._call_api("sys", "user", max_tokens=2000)
            self.assertEqual(res["topic"], "ok")
            self.assertEqual(len(calls), 3)

            # 5. _call_api: LengthFinishReasonError 自动扩容 max_tokens 并支持从 completion 截断修复
            class LengthFinishReasonError(Exception):
                def __init__(self, message, completion=None):
                    super().__init__(message)
                    self.completion = completion

            len_calls = []

            class FakeLengthLLM:
                def __init__(self, **kw):
                    self.kw = kw

                def invoke(self, msgs):
                    len_calls.append(dict(self.kw))
                    if len(len_calls) == 1:
                        partial_completion = SimpleNamespace(
                            choices=[
                                SimpleNamespace(
                                    message=SimpleNamespace(content='{"title": "退婚夜", "characters": [{"name": "沈栀"}]')
                                )
                            ]
                        )
                        raise LengthFinishReasonError(
                            "Could not parse response content as the length limit was reached - CompletionUsage(completion_tokens=6000)",
                            completion=partial_completion,
                        )
                    return SimpleNamespace(content='{"title": "退婚夜", "status": "complete"}')

            with patch("services.llm.deepseek_service.settings", fake_settings), patch(
                "services.llm.deepseek_service.model_config",
                return_value={"url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3", "key": "sk-sf"},
            ), patch("services.llm.deepseek_service.ChatOpenAI", side_effect=lambda **kw: FakeLengthLLM(**kw)):
                res_expanded = svc._call_api("sys", "user", max_tokens=6000)
            self.assertEqual(res_expanded["status"], "complete")
            self.assertEqual(len_calls[0]["max_tokens"], 6000)
            self.assertEqual(len_calls[1]["max_tokens"], 8192)
            self.assertNotIn("model_kwargs", len_calls[1])

            always_truncated_completion = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"title": "抢表反杀", "characters": [{"name": "沈栀"}, {"incomplete')
                    )
                ]
            )

            class AlwaysLengthLLM:
                def __init__(self, **kw):
                    self.kw = kw

                def invoke(self, msgs):
                    if self.kw.get("max_tokens", 0) > 8192:
                        err = RuntimeError("Invalid max_tokens: exceeds 8192 limit")
                        err.status_code = 400
                        raise err
                    raise LengthFinishReasonError(
                        "Could not parse response content as the length limit was reached",
                        completion=always_truncated_completion,
                    )

            with patch("services.llm.deepseek_service.settings", fake_settings), patch(
                "services.llm.deepseek_service.model_config",
                return_value={"url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "key": "sk-test"},
            ), patch("services.llm.deepseek_service.ChatOpenAI", side_effect=lambda **kw: AlwaysLengthLLM(**kw)):
                res_repaired = svc._call_api("sys", "user", max_tokens=8192)
            self.assertEqual(res_repaired["title"], "抢表反杀")
            self.assertEqual(res_repaired["characters"][0]["name"], "沈栀")

            from services import drama_craft_service

            seen_tokens = []

            class StubCraftLLM:
                def _call_api(self, sys_p, usr_p, max_tokens=2000):
                    seen_tokens.append(max_tokens)
                    return {
                        "title": "三集精悍测试",
                        "shootability_report": {"overall_feasibility": "S级", "metrics": []},
                        "characters": [{"name": "沈栀", "role": "女主", "face_dna": "浅疤", "signature_clothing": "帆布鞋"}],
                        "scenes_spec": [],
                        "props_spec": [],
                        "episodes_outline": [{"episode_num": 1, "title": "第1集"}],
                        "first_episode_script": "△推门。",
                        "continuity_ledgers": {},
                    }

            with patch.object(drama_craft_service, "_get_real_llm", return_value=StubCraftLLM()):
                out = drama_craft_service.ai_decompose_novel_to_drama("沈栀带怀表去退婚" * 3, total_episodes=3)
            self.assertGreaterEqual(seen_tokens[0], 8192)
            self.assertEqual(len(out["episodes_outline"]), 3)

            # 6. GLM-5.3-Flash / Qwen3 思考模型：GLM-5.3 使用 reasoning_effort=low，Qwen3 使用 enable_thinking=False，支持 </think> 与 reasoning_content 提取
            parsed_unpaired = _extract_and_parse_json('先分析{草案}\n</think>\n{"title": "古董表", "ok": true}')
            self.assertEqual(parsed_unpaired["title"], "古董表")

            glm_calls = []

            class FakeGlmLLM:
                def __init__(self, **kw):
                    self.kw = kw

                def invoke(self, msgs):
                    glm_calls.append(dict(self.kw))
                    return SimpleNamespace(
                        content="",
                        additional_kwargs={"reasoning_content": '内部规划 {跳过片段\n{"title": "硅基GLM提取", "characters": []}'},
                        response_metadata={"finish_reason": "stop"},
                    )

            with patch("services.llm.deepseek_service.settings", fake_settings), patch(
                "services.llm.deepseek_service.model_config",
                return_value={"url": "https://api.siliconflow.cn/v1", "model": "zai-org/GLM-5.3-Flash", "key": "sk-sf"},
            ), patch("services.llm.deepseek_service.ChatOpenAI", side_effect=lambda **kw: FakeGlmLLM(**kw)):
                res_glm = svc._call_api("sys", "user", max_tokens=8192)
            self.assertEqual(res_glm["title"], "硅基GLM提取")
            self.assertEqual(glm_calls[0].get("extra_body"), {"reasoning_effort": "low"})
            self.assertNotIn("model_kwargs", glm_calls[0])

            qwen3_calls = []

            class FakeQwen3LLM:
                def __init__(self, **kw):
                    self.kw = kw

                def invoke(self, msgs):
                    qwen3_calls.append(dict(self.kw))
                    return SimpleNamespace(content='{"title": "Qwen3直出"}', response_metadata={"finish_reason": "stop"})

            with patch("services.llm.deepseek_service.settings", fake_settings), patch(
                "services.llm.deepseek_service.model_config",
                return_value={"url": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen3-32B", "key": "sk-sf"},
            ), patch("services.llm.deepseek_service.ChatOpenAI", side_effect=lambda **kw: FakeQwen3LLM(**kw)):
                res_q3 = svc._call_api("sys", "user", max_tokens=8192)
            self.assertEqual(res_q3["title"], "Qwen3直出")
            self.assertEqual(qwen3_calls[0].get("extra_body"), {"enable_thinking": False})

            class FakeReasoningOnlyLLM:
                def __init__(self, **kw):
                    self.kw = kw

                def invoke(self, msgs):
                    return SimpleNamespace(
                        content="",
                        additional_kwargs={"reasoning_content": "全程在分析剧情结构但没有输出任何JSON"},
                        response_metadata={"finish_reason": "length"},
                    )

            with patch("services.llm.deepseek_service.settings", fake_settings), patch(
                "services.llm.deepseek_service.model_config",
                return_value={"url": "https://api.siliconflow.cn/v1", "model": "zai-org/GLM-5.3-Flash", "key": "sk-sf"},
            ), patch("services.llm.deepseek_service.ChatOpenAI", side_effect=lambda **kw: FakeReasoningOnlyLLM(**kw)):
                with self.assertRaises(ValueError) as ctx:
                    svc._call_api("sys", "user", max_tokens=8192)
            self.assertIn("zai-org/GLM-5.3-Flash", str(ctx.exception))
            self.assertIn("推理过程", str(ctx.exception))

    def test_script_segment_accepts_auto_director_variable_durations(self):
        with patch.dict(sys.modules, _runtime_stubs()):
            from models.script_model import Script, ScriptSegment

            segments = [
                ScriptSegment(duration=6, plot="开场拍桌对峙", voice_start=0.5, voiceover="三分钟内签字背锅。"),
                ScriptSegment(duration=9, plot="反转亮出U盘录音", voice_start=1.2, voiceover="昨晚的录音你听清楚了吗？"),
                ScriptSegment(duration=5, plot="抢U盘反被制伏", voice_start=0.2, voiceover="审计原件已同步发出。"),
                ScriptSegment(duration=11, plot="结尾悬念回信", voice_start=10.1, voiceover="第二层名单，你打算怎么谈？"),
            ]
            for seg in segments:
                seg.validate_speech()
            script = Script(script_id="sc_1", task_id="t_1", segments=segments)
            self.assertEqual([s.duration for s in script.segments], [6, 9, 5, 11])

            bad = ScriptSegment(duration=5, plot="越界", voice_start=5.5, voiceover="超时测试")
            with self.assertRaisesRegex(ValueError, "必须小于本段时长"):
                bad.validate_speech()

    def test_deduplicate_scenes_by_index_cleans_duplicate_runs(self):
        import asyncio

        with patch.dict(sys.modules, _runtime_stubs()):
            from agents.agent_utils import deduplicate_scenes_by_index

            deleted = []
            fake_db = Mock()
            fake_db.delete_one = Mock(
                side_effect=lambda col, query: (deleted.append(query["_id"]), asyncio.sleep(0))[1]
            )
            scenes = [
                {"_id": "t_scene_1", "scene_index": 1, "duration": 8, "updated_at": "2026-11-18T05:00:00Z"},
                {"_id": "t_scene_01", "scene_index": 1, "duration": 8, "updated_at": "2026-11-18T05:01:00Z"},
                {"_id": "t_scene_2", "scene_index": 2, "duration": 10, "updated_at": "2026-11-18T05:00:00Z"},
                {"_id": "t_scene_02", "scene_index": 2, "duration": 10, "updated_at": "2026-11-18T05:01:00Z"},
            ]
            clean = asyncio.run(deduplicate_scenes_by_index(scenes, fake_db))
            self.assertEqual([s["_id"] for s in clean], ["t_scene_01", "t_scene_02"])
            self.assertEqual(deleted, ["t_scene_1", "t_scene_2"])

    def test_auto_voice_separation_and_story_judge_rewrite(self):
        with patch.dict(sys.modules, _runtime_stubs()):
            from agents.story_judge_agent import StoryJudgeAgent, audit_script_pacing_and_drama
            from services.character_binding import build_effective_voice_profiles, scene_voice_options

            # 1. 验证未入库男女主角（名字无“姐/妹”）也能自动分离声线，不再共用男播音腔
            task = {
                "task_id": "t_drama",
                "generation_mode": "auto_director",
                "metadata": {
                    "task_plan": {
                        "duration": 42,
                        "generation_mode": "auto_director",
                        "characters": [
                            {"name": "沈清秋", "appearance": "24岁民国女子，穿浅灰暗花旗袍，长发盘起"},
                            {"name": "顾行舟", "appearance": "28岁年轻少帅，深绿军装，短寸头"},
                        ],
                    }
                },
            }
            scenes = [
                {"scene_id": "s1", "dialogue_lines": [{"speaker": "沈清秋", "text": "这笔账算清", "start": 0.3}, {"speaker": "顾行舟", "text": "三日内搬走", "start": 2.5}]}
            ]
            profiles = build_effective_voice_profiles(task, scenes)
            for sc in scenes:
                sc["voice_profiles"] = profiles
            female_voice = scene_voice_options(scenes[0], "沈清秋")["voice"]
            male_voice = scene_voice_options(scenes[0], "顾行舟")["voice"]
            self.assertIn("Xiao", female_voice)
            self.assertIn("Yun", male_voice)
            self.assertNotEqual(female_voice, male_voice)

            # 2. 验证 StoryJudgeAgent 识别 5 秒静默干瞪眼并自动调用重写结构化 segments
            weak_draft = {
                "title": "退婚书",
                "content": "顾行舟：搬走。\n沈清秋：不走。",
                "segments": [
                    {"duration": 10, "plot": "沉默开门", "voice_start": 3.0, "voiceover": "搬走。", "dialogue_lines": [{"speaker": "顾行舟", "text": "搬走。", "start": 3.0}]},
                    {"duration": 10, "plot": "继续发呆", "voice_start": 2.0, "voiceover": "不走。", "dialogue_lines": [{"speaker": "沈清秋", "text": "不走。", "start": 2.0}]},
                ],
            }
            audit = audit_script_pacing_and_drama(weak_draft, task["metadata"]["task_plan"], allow_llm=False)
            self.assertTrue(audit["needs_rewrite"])
            self.assertIn("EXCESSIVE_SILENCE_GAP", audit["deterministic_flags"])
            self.assertIn("LOW_STORY_DENSITY", audit["deterministic_flags"])

            class FakeJudgeLLM:
                def rewrite_script_with_judgment(self, plan, draft, report):
                    return {
                        "title": "银戒反杀",
                        "hook": "退婚书摔脸逼搬，下一秒地契直接拍成废纸！",
                        "content": "△顾行舟将退婚书砸在桌上。\n顾行舟：三日搬走！\n沈清秋：地契早就作废了！",
                        "segments": [
                            {
                                "duration": 6,
                                "plot": "开场砸退婚书逼宫",
                                "voice_start": 0.3,
                                "voiceover": "三日内沈家净身出户！这封退婚书你今天签也得签！",
                                "dialogue_lines": [
                                    {"speaker": "顾行舟", "text": "三日内净身出户！这封退婚书今天必须签！", "start": 0.3}
                                ],
                            },
                            {
                                "duration": 7,
                                "plot": "亮旧印揭破伪造地契并反制",
                                "voice_start": 0.3,
                                "voiceover": "官署三年前就作废了你的抄本！这枚沈字银戒你先认清楚！",
                                "dialogue_lines": [
                                    {"speaker": "沈清秋", "text": "官署三年前就作废了你的抄本！这枚银戒先认认！", "start": 0.3}
                                ],
                            },
                        ],
                        "ending": "当年码头救我的人，竟然是你！",
                        "publish_copy": "拿废地契逼婚？下一秒少帅直接看傻！",
                        "changes_made": ["压紧开场至0.3秒起声，消灭3秒无声呆立"],
                    }

            with patch("agents.story_judge_agent.settings.enable_paid_api", False):
                res = StoryJudgeAgent.judge_and_polish(
                    task,
                    weak_draft,
                    task["metadata"]["task_plan"],
                    llm_service=FakeJudgeLLM(),
                    allow_llm=True,
                )
            self.assertTrue(res["story_judge"]["rewritten"])
            self.assertGreater(res["story_judge"]["after_score"], res["story_judge"]["before_score"])
            self.assertEqual(len(res["script_data"]["segments"]), 2)
            self.assertEqual(res["script_data"]["segments"][0]["voice_start"], 0.3)

    def test_emotion_prosody_flat_plot_audit_and_first_frame_anchoring(self):
        import asyncio

        with patch.dict(sys.modules, _runtime_stubs()), tempfile.TemporaryDirectory() as tmp:
            from agents.image_agent import ImageAgent
            from agents.story_judge_agent import audit_script_pacing_and_drama
            from services.character_binding import apply_dialogue_emotion_prosody

            # 1. 单句情绪语气偏移（暴怒加快升调 / 阴狠放慢压低 / 震惊微颤）
            base = {"voice": "zh-CN-YunjianNeural", "rate": "+0%", "pitch": "+0Hz"}
            angry = apply_dialogue_emotion_prosody(base, "angry")
            threat = apply_dialogue_emotion_prosody(base, "cold_threat")
            shocked = apply_dialogue_emotion_prosody(base, "shocked")
            self.assertEqual((angry["rate"], angry["pitch"]), ("+12%", "+5Hz"))
            self.assertEqual((threat["rate"], threat["pitch"]), ("-8%", "-5Hz"))
            self.assertEqual((shocked["rate"], shocked["pitch"]), ("-10%", "+4Hz"))

            # 2. 48秒4段全员静态桌面聊且无情绪变化时，触发 FLAT_PLOT_AND_EMOTION 高危拦截
            flat_48s = {
                "content": "两人坐桌聊天。",
                "segments": [
                    {"duration": 12, "plot": "坐在桌前递文件说一句", "voice_start": 0.3, "voiceover": "你看看这份材料是否还有效呢请问。"},
                    {"duration": 12, "plot": "坐在桌前继续看文件回应", "voice_start": 0.3, "voiceover": "我觉得你可以再看看这页记录数据。"},
                    {"duration": 12, "plot": "坐在桌前继续谈合同条款", "voice_start": 0.3, "voiceover": "这份条款其实还有很多细节可探讨。"},
                    {"duration": 12, "plot": "坐在桌前平静结束对话", "voice_start": 0.3, "voiceover": "那就先这么定了，我们明天再约吧。"},
                ],
            }
            audit = audit_script_pacing_and_drama(flat_48s, {"duration": 48}, allow_llm=False)
            self.assertIn("FLAT_PLOT_AND_EMOTION", audit["deterministic_flags"])
            self.assertTrue(audit["needs_rewrite"])

            # 3. 无角色库时 ImageAgent 先画第1镜，自动作为后续同片场镜头的 reference_images 锚点
            anchor_img = Path(tmp) / "s1.png"
            s2_img = Path(tmp) / "s2.png"
            Image.new("RGB", (64, 64), (30, 40, 50)).save(anchor_img)
            Image.new("RGB", (64, 64), (60, 70, 80)).save(s2_img)

            seen_calls = []

            class FakeImageSvc:
                supports_reference_images = True
                max_reference_images = 3

                def generate_image(self, task_id, scene_id, image_prompt, negative_prompt=None, ratio="9:16", reference_images=None):
                    seen_calls.append((scene_id, list(reference_images or []), image_prompt))
                    path = str(anchor_img if scene_id == "s1" else s2_img)
                    return {"status": "success", "asset_path": path, "source": "qwen"}

            from types import SimpleNamespace
            from unittest.mock import AsyncMock

            db = Mock()
            scenes = [
                {"scene_id": "s1", "scene_index": 1, "duration": 6, "need_dynamic_video": True, "image_prompt": "开场拍桌站起"},
                {"scene_id": "s2", "scene_index": 2, "duration": 8, "need_dynamic_video": True, "image_prompt": "逼近墙角特写"},
            ]
            db.find_many = AsyncMock(side_effect=lambda *a, **k: [dict(s) for s in scenes])
            db.find_one = AsyncMock(side_effect=lambda col, *a, **k: (
                {"task_id": "t_img", "ratio": "9:16", "style": "短剧", "metadata": {}} if col == "video_tasks" else None
            ))
            db.replace_one = AsyncMock()
            db.update_one = AsyncMock()

            async def _ok_dispatch(task, op_type, op_key, fn):
                return fn()

            stub_settings = SimpleNamespace(max_images_per_task=20, video_provider="wan")
            stub_anyio = SimpleNamespace(
                Semaphore=asyncio.Semaphore,
                to_thread=SimpleNamespace(run_sync=lambda fn, *a, **k: asyncio.to_thread(fn, *a, **k)),
            )
            with patch("agents.image_agent.mongodb", db), patch(
                "agents.image_agent.settings", stub_settings
            ), patch("agents.image_agent.anyio", stub_anyio), patch(
                "agents.image_agent.get_image_service", return_value=FakeImageSvc()
            ), patch(
                "agents.image_agent.dispatch_generation", side_effect=_ok_dispatch
            ), patch("agents.image_agent.mark_agent_start", new=AsyncMock()), patch(
                "agents.image_agent.mark_agent_success", new=AsyncMock()
            ), patch("agents.image_agent.mark_agent_failed", new=AsyncMock()), patch(
                "services.media_quality.resolve_media", side_effect=lambda p: Path(p)
            ):
                asyncio.run(ImageAgent().run("t_img"))

            self.assertEqual(len(seen_calls), 2)
            self.assertEqual(seen_calls[0][1], [])
            self.assertEqual(seen_calls[1][1], [str(anchor_img)])
            self.assertIn("同片场造型与环境锚定", seen_calls[1][2])

    def test_auto_director_relay_wan27_ref_vision_endpoints_and_audio_assets(self):
        import asyncio
        import wave
        from unittest.mock import AsyncMock

        # 1. Verify BGM + 5 Foley SFX audio assets exist and are valid PCM WAV files
        data_dir = Path(__file__).resolve().parents[1] / "data"
        for name in ("footstep", "impact", "water", "paper", "wind"):
            sfx_path = data_dir / "sfx" / f"{name}.wav"
            self.assertTrue(sfx_path.is_file(), f"Missing SFX file: {sfx_path}")
            with wave.open(str(sfx_path), "rb") as wf:
                self.assertGreater(wf.getnframes(), 1000)
        bgm_files = list((data_dir / "bgm").glob("*.wav")) + list((data_dir / "bgm").glob("*.mp3"))
        self.assertGreaterEqual(len(bgm_files), 1)

        with patch.dict(sys.modules, _runtime_stubs()), tempfile.TemporaryDirectory() as tmp:
            import services.image.qwen_image_service as qwen_mod
            import services.video_vision as vision_mod
            from agents.image_agent import ImageAgent
            from agents.prompt_agent import PromptAgent

            # 2. Verify wan2.7-image supports up to 9 reference images and selects 16:9 / 9:16 dimensions
            with patch.object(qwen_mod, "model_config", return_value={"model": "wan2.7-image", "url": "https://dashscope.aliyuncs.com", "key": "k"}):
                svc = qwen_mod.QwenImageService()
                self.assertTrue(svc.supports_reference_images)
                self.assertEqual(svc.max_reference_images, 9)

            # 3. Verify ImageAgent picks first character-bearing scene (skipping pure empty shot) as anchor
            seen_anchor_order = []
            tmp_dir = Path(tmp)
            s2_img = tmp_dir / "s2.png"

            class SmartAnchorImgSvc:
                supports_reference_images = True
                max_reference_images = 9

                def generate_image(self, task_id, scene_id, image_prompt, negative_prompt=None, ratio="9:16", reference_images=None):
                    p = tmp_dir / f"{scene_id}.png"
                    Image.new("RGB", (64, 64), (30, 40, 50)).save(p)
                    seen_anchor_order.append((scene_id, list(reference_images or [])))
                    return {"status": "success", "asset_path": str(p), "source": "qwen"}

            img_scenes = [
                {"scene_id": "s1", "scene_index": 1, "duration": 5, "shot_type": "空镜", "need_dynamic_video": True, "image_prompt": "走廊夜景"},
                {"scene_id": "s2", "scene_index": 2, "duration": 6, "shot_type": "近景", "dialogue_lines": [{"speaker": "沈总", "text": "签字", "start": 0.5}], "need_dynamic_video": True, "image_prompt": "沈总冷笑"},
                {"scene_id": "s3", "scene_index": 3, "duration": 5, "shot_type": "特写", "need_dynamic_video": True, "image_prompt": "摔下合同"},
            ]
            img_db = Mock()
            img_db.find_many = AsyncMock(side_effect=lambda *a, **k: [dict(s) for s in img_scenes])
            img_db.find_one = AsyncMock(side_effect=lambda col, *a, **k: (
                {"task_id": "t_anchor", "ratio": "9:16", "style": "短剧", "metadata": {}} if col == "video_tasks" else None
            ))
            img_db.replace_one = AsyncMock()
            img_db.update_one = AsyncMock()
            stub_anyio = SimpleNamespace(
                Semaphore=asyncio.Semaphore,
                to_thread=SimpleNamespace(run_sync=lambda fn, *a, abandon_on_cancel=None, **k: asyncio.to_thread(fn, *a, **k)),
            )
            with patch("agents.image_agent.mongodb", img_db), patch(
                "agents.image_agent.settings", SimpleNamespace(max_images_per_task=20, video_provider="wan")
            ), patch("agents.image_agent.anyio", stub_anyio), patch(
                "agents.image_agent.get_image_service", return_value=SmartAnchorImgSvc()
            ), patch(
                "agents.image_agent.dispatch_generation", side_effect=lambda t, o, k, fn: fn()
            ), patch("agents.image_agent.mark_agent_start", new=AsyncMock()), patch(
                "agents.image_agent.mark_agent_success", new=AsyncMock()
            ), patch("agents.image_agent.mark_agent_failed", new=AsyncMock()), patch(
                "services.media_quality.resolve_media", side_effect=lambda p: Path(p)
            ):
                asyncio.run(ImageAgent().run("t_anchor"))
            self.assertEqual(seen_anchor_order[0], ("s2", []))
            self.assertEqual(dict(seen_anchor_order)["s1"], [str(s2_img)])
            self.assertEqual(dict(seen_anchor_order)["s3"], [str(s2_img)])

            # 4. Verify auto_director PromptAgent passes previous_segment_context sequentially
            prompt_batches = []

            class FakeLLM:
                def generate_image_prompts(self, batch, ratio="9:16", style=None, visual_bible=None):
                    prompt_batches.append([
                        {"scene_id": sc["scene_id"], "previous_segment_context": sc.get("previous_segment_context")}
                        for sc in batch
                    ])
                    sc = batch[0]
                    dur = int(sc["duration"])
                    beats = [
                        {
                            "start": i, "end": i + 1, "subject": "林墨", "camera": "中景平视",
                            "start_state": "左站桌前右手握U盘", "action": "抬手将U盘拍在桌心",
                            "end_state": "U盘平放桌面中央，双手离开" if i == dur - 1 else "手臂前伸压向桌面",
                        }
                        for i in range(dur)
                    ]
                    return [{
                        "scene_id": sc["scene_id"],
                        "blocking": "【双人轴线锁：左林墨·右周明】",
                        "transition_mode": "angle_cut",
                        "image_prompt": "会议室对峙",
                        "video_prompt": "林墨拍下U盘",
                        "last_frame_for_transition": "U盘停在桌心，林墨直视对方",
                        "motion_beats": beats,
                    }]

            pa_scenes = [
                {"scene_id": "scene_1", "scene_index": 1, "duration": 4, "visual_description": "放下U盘", "voiceover": "看清楚"},
                {"scene_id": "scene_2", "scene_index": 2, "duration": 4, "visual_description": "对方抢看", "voiceover": "不可能"},
            ]
            pa_task = {
                "task_id": "t_pa", "generation_mode": "auto_director", "ratio": "9:16", "style": "短剧", "metadata": {"task_plan": {}},
            }
            pa_db = Mock()
            pa_db.find_many = AsyncMock(side_effect=lambda *a, **k: [dict(s) for s in pa_scenes])
            pa_db.find_one = AsyncMock(return_value=pa_task)
            pa_db.update_one = AsyncMock()
            with patch("agents.prompt_agent.mongodb", pa_db), patch(
                "agents.prompt_agent.get_required_task", new=AsyncMock(return_value=pa_task)
            ), patch("agents.prompt_agent.anyio", stub_anyio), patch(
                "agents.prompt_agent.get_llm_service", return_value=FakeLLM()
            ), patch("agents.prompt_agent.task_service.update_task_fields", new=AsyncMock()), patch(
                "agents.prompt_agent.mark_agent_start", new=AsyncMock()
            ), patch("agents.prompt_agent.mark_agent_success", new=AsyncMock()), patch(
                "agents.prompt_agent.mark_agent_failed", new=AsyncMock()
            ):
                asyncio.run(PromptAgent().run("t_pa"))

            self.assertEqual(len(prompt_batches), 2)
            self.assertIsNone(prompt_batches[0][0]["previous_segment_context"])
            prev_ctx = prompt_batches[1][0]["previous_segment_context"]
            self.assertIsNotNone(prev_ctx)
            self.assertEqual(prev_ctx["scene_index"], 1)
            self.assertEqual(prev_ctx["final_second"]["end_state"], "U盘平放桌面中央，双手离开")
            self.assertIn("左林墨", prev_ctx["blocking"])

            # 5. Verify video_vision endpoint discovery and Token Plan / DashScope fallback
            with patch.object(
                vision_mod, "settings",
                SimpleNamespace(enable_paid_api=True, video_review_model="qwen3-vl-plus", dashscope_api_base="https://dashscope.aliyuncs.com", dashscope_api_key="sk-ws-1"),
            ), patch("services.model_config.model_config", return_value={"url": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1", "key": "sk-sp-1"}):
                eps = vision_mod._review_endpoints()
                self.assertEqual(
                    eps,
                    [
                        ("https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions", "sk-sp-1"),
                        ("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "sk-ws-1"),
                    ],
                )

    def test_single_shot_lock_vision_fallback_spoiler_rewrite_and_srt_wrap(self):
        import asyncio
        from unittest.mock import AsyncMock

        with patch.dict(sys.modules, _runtime_stubs()), tempfile.TemporaryDirectory() as tmp:
            from agents.storyboard_agent import StoryboardAgent
            from agents.video_agent import _build_motion_prompt, prepare_motion_prompt
            from services.character_binding import apply_dialogue_emotion_prosody, infer_dialogue_emotion
            from services.generation_contract import render_srt

            # 1. Wan single-shot anti-cut compilation + single camera emission in _compact_timeline_slices
            beats = [
                {"start": 0, "end": 1, "subject": "沈清秋", "camera": "中景", "start_state": "坐桌后", "action": "推开合同", "end_state": "手收回"},
                {"start": 1, "end": 2, "subject": "沈清秋", "camera": "特写手部", "start_state": "手收回", "action": "抬眸直视", "end_state": "眼神逼人"},
            ]
            scene = {
                "scene_id": "scene_01", "duration": 2, "style": "短剧", "shot_type": "中景",
                "camera_motion": "缓慢推进", "video_prompt": "沈清秋推开合同冷视。", "motion_beats": beats,
                "blocking": "【双人轴线锁：左沈·右顾】",
            }
            built = _build_motion_prompt(scene)
            self.assertEqual(built.count("机位:"), 1)

            wan_svc = type("WanVideoService", (), {"model": "wan3.0-video"})()
            compiled, _ = adapt_video_submission(wan_svc, scene, built)
            self.assertIn("单一连续一镜到底", compiled)
            self.assertNotIn("[起止]", compiled)
            self.assertNotIn("[锚定与微锁]", compiled)
            self.assertNotIn("机位:特写手部", compiled)

            # 2. prepare_motion_prompt gracefully falls back if replan_from_tail raises
            img = Path(tmp) / "tail.png"
            Image.new("RGB", (64, 64), (10, 20, 30)).save(img)
            with patch("agents.video_agent.review_enabled", return_value=True), patch(
                "agents.video_agent.replan_from_tail", side_effect=RuntimeError("vision timeout")
            ):
                cont_scene = {**scene, "transition_type": "continue_action"}
                res_prompt, res_obs = asyncio.run(
                    prepare_motion_prompt(cont_scene, {"video_path": "prev.mp4", "duration": 5}, str(img), "digest1")
                )
            self.assertIsNone(res_obs)
            self.assertIn("顺承首帧落地姿态", res_prompt)

            # 3. StoryboardAgent pre-shoot semantic check catches spoiler & triggers one clean rewrite
            draft_calls = []

            class SpoilerThenFixedLLM:
                def generate_storyboard(self, ctx, target_duration=None, generation_mode="full_dynamic"):
                    draft_calls.append(ctx.get("review_feedback"))
                    is_fixed = bool(ctx.get("review_feedback"))
                    return [
                        {
                            "scene_id": "s1", "scene_index": 1, "duration": 5, "shot_type": "中景",
                            "voiceover": "你确定不签字？", "voice_start": 0.5, "need_dynamic_video": True,
                            "transition_type": "cut",
                            "visual_description": "书房桌前对峙，后面木门被推开" if not is_fixed else "书房桌前顾行舟抬手按下退婚书并冷眼追问",
                            "character_state": "冷静→压迫", "scene_continuity": "同一书房",
                            "transition_note": "接下一镜反应", "start_state": "按书", "end_state": "盯紧对方",
                            "camera_motion": "推进",
                        },
                        {
                            "scene_id": "s2", "scene_index": 2, "duration": 5, "shot_type": "近景",
                            "voiceover": "内壁刻着沈字。", "voice_start": 1.0, "need_dynamic_video": True,
                            "transition_type": "cut",
                            "visual_description": "末尾最后2秒书房木门被推开半寸，顾行舟低看银戒",
                            "character_state": "强硬→震惊", "scene_continuity": "同一书房",
                            "transition_note": "悬念收尾", "start_state": "看桌", "end_state": "门缝微开",
                            "camera_motion": "推进",
                        },
                    ]

                def review_story_coverage(self, req, beats, inputs):
                    if "木门被推开" in str(inputs[0].get("picture")):
                        return {"verdict": "needs_review", "issues": ["木门推开的结尾悬念提前剧透出现在第一镜"]}
                    return {"verdict": "pass", "issues": []}

            sb_task = {"task_id": "t_sb", "user_input": "最后3秒木门被推开半寸", "duration": 10, "generation_mode": "auto_director", "style": "短剧", "duration_locked": True, "metadata": {"task_plan": {"plot_beats": []}}}
            sb_db = Mock()
            sb_db.find_many = AsyncMock(return_value=[])
            sb_db.find_one = AsyncMock(return_value={"task_id": "t_sb", "content": "script", "segments": []})
            sb_db.delete_one = AsyncMock()
            sb_db.replace_one = AsyncMock()
            with patch("agents.storyboard_agent.mongodb", sb_db), patch(
                "agents.storyboard_agent.get_required_task", new=AsyncMock(return_value=sb_task)
            ), patch("agents.storyboard_agent.settings", SimpleNamespace(enable_paid_api=True, video_provider="wan")), patch(
                "agents.storyboard_agent.get_llm_service", return_value=SpoilerThenFixedLLM()
            ), patch("agents.storyboard_agent.anyio", SimpleNamespace(to_thread=SimpleNamespace(run_sync=lambda fn, *a, **k: asyncio.sleep(0, result=fn(*a, **k))))), patch(
                "agents.storyboard_agent.mark_agent_start", new=AsyncMock()
            ), patch("agents.storyboard_agent.mark_agent_success", new=AsyncMock()), patch(
                "agents.storyboard_agent.mark_agent_failed", new=AsyncMock()
            ):
                saved_scenes = asyncio.run(StoryboardAgent().run("t_sb"))
            self.assertEqual(len(draft_calls), 2)
            self.assertIsNone(draft_calls[0])
            self.assertIsNotNone(draft_calls[1])
            self.assertNotIn("木门被推开", saved_scenes[0]["visual_description"])

            # 4. CJK SRT soft wrap + fallback dialogue emotion inference
            srt = render_srt([{"scene_id": "s1", "duration": 8, "voice_start": 0.5, "audio_duration": 3.0, "voiceover": "官署里早就留了备案，你手里这份地契根本就是假货。"}])
            self.assertIn("官署里早就留了备案\n你手里这份地契根本就是假货。", srt)
            self.assertEqual(infer_dialogue_emotion("不可能？！", ""), "shocked")
            self.assertEqual(infer_dialogue_emotion("滚出去！", ""), "angry")
            self.assertEqual(apply_dialogue_emotion_prosody({"rate": "+0%", "pitch": "+0Hz"}, "angry")["rate"], "+12%")

    def test_storyboard_flow_mode_non_per_second_cast_cards_and_flash_white(self):
        import asyncio
        from unittest.mock import AsyncMock

        with patch.dict(sys.modules, _runtime_stubs()), tempfile.TemporaryDirectory() as tmp:
            from agents.image_agent import ImageAgent
            from agents.prompt_agent import PromptAgent
            from agents.story_judge_agent import StoryJudgeAgent
            from agents.storyboard_agent import StoryboardAgent, validate_storyboard_scenes
            from services.generation_contract import (
                STORYBOARD_FLOW_MODE,
                provider_duration_bounds,
                rebalance_motion_beats_duration,
                render_srt_for_task,
                validate_storyboard_flow_scenes,
            )

            # 1. Contracts: 3s minimum & continuous beat rebalance & ratio-aware SRT
            self.assertEqual(provider_duration_bounds("wan", "wan3.0-video", generation_mode=STORYBOARD_FLOW_MODE), (3, 12))
            flow_scenes = [
                {"scene_id": "s1", "scene_index": 1, "duration": 3, "need_dynamic_video": True, "transition_type": "cut",
                 "voiceover": "", "visual_description": "男孩俯桌闭眼入梦", "character_state": "困倦→迷茫",
                 "scene_continuity": "课堂切换", "transition_note": "闪白入梦"},
                {"scene_id": "s2", "scene_index": 2, "duration": 4, "need_dynamic_video": True, "transition_type": "scene_change",
                 "voiceover": "一切都变了。", "visual_description": "男孩穿着粗布衣站在1927年泥泞乡道",
                 "character_state": "惊愕→警觉", "scene_continuity": "转入1927年煤窑外", "transition_note": "镜头上摇揭示全貌"},
            ]
            validate_storyboard_scenes(flow_scenes, target_duration=7, enforce_voiceover_density=False, allow_silent=True)
            validate_storyboard_flow_scenes(flow_scenes, target_duration=7, bounds=(3, 12))
            rebalanced = rebalance_motion_beats_duration(
                [{"start": 0, "end": 4, "subject": "男孩", "camera": "近景", "start_state": "站立", "action": "回头", "end_state": "望向前方"}],
                4,
                6,
            )
            self.assertEqual(len(rebalanced), 1)
            self.assertEqual((rebalanced[0]["start"], rebalanced[0]["end"]), (0, 6))

            long_line_scene = [{"scene_id": "s1", "duration": 8, "voice_start": 0.5, "audio_duration": 3.0, "voiceover": "这是一条二十字长度的测试台词用来核对换行效果。"}]
            self.assertIn("\n", render_srt_for_task(long_line_scene, {"ratio": "9:16"}))
            self.assertNotIn("\n", render_srt_for_task(long_line_scene, {"ratio": "16:9"}).splitlines()[2])

            # 2. StoryJudge preserves silent visual beat in storyboard_flow without failing
            judged = StoryJudgeAgent.judge_and_polish(
                {"task_id": "t_flow", "generation_mode": STORYBOARD_FLOW_MODE},
                {
                    "content": "对视沉默。",
                    "segments": [
                        {"duration": 3, "plot": "男孩闭眼入梦", "voice_start": 0.0, "voiceover": "", "dialogue_lines": []},
                        {"duration": 5, "plot": "小巷抬头对视", "voice_start": 0.3, "voiceover": "别放弃我，一定要等着我。", "dialogue_lines": [{"speaker": "男主", "text": "别放弃我，一定要等着我。", "start": 0.3}]},
                    ],
                },
                {"generation_mode": STORYBOARD_FLOW_MODE, "duration": 8},
                allow_llm=False,
            )
            self.assertNotIn("SLOW_OPENING", judged["story_judge"]["deterministic_flags"])

            # 2b. ScriptSegment / Script 允许 storyboard_flow 静默分镜，非流模式仍校验旁白文本
            from agents.story_judge_agent import normalize_segments_for_mode
            from models.script_model import Script, ScriptSegment

            silent_norm = normalize_segments_for_mode(
                [{"plot": "全景：椅子刮地乱响", "voiceover": None, "dialogue_lines": []}],
                mode=STORYBOARD_FLOW_MODE,
                min_sec=5,
                max_sec=12,
            )
            self.assertTrue(silent_norm[0]["allow_silent"])
            silent_seg = ScriptSegment(**silent_norm[0])
            silent_seg.validate_speech()
            Script(script_id="sc_flow", task_id="t_flow", segments=silent_norm)
            with self.assertRaises(ValueError):
                ScriptSegment(duration=5, plot="无旁白普通段落", voice_start=0.0, voiceover="").validate_speech()

            # 3. PromptAgent passes per_second=False & accepts single continuous motion_beat
            seen_per_second = []

            class FlowPromptLLM:
                def generate_image_prompts(self, batch, ratio="9:16", style=None, visual_bible=None, per_second=True):
                    seen_per_second.append(per_second)
                    sc = batch[0]
                    dur = int(sc["duration"])
                    return [{
                        "scene_id": sc["scene_id"],
                        "blocking": "左男孩独框",
                        "transition_mode": "flash_white",
                        "image_prompt": "男孩趴在课桌上闭眼",
                        "video_prompt": "镜头平滑推近闭上的眼睛",
                        "last_frame_for_transition": "双眼闭合，光晕柔和",
                        "motion_beats": [{
                            "start": 0, "end": dur, "subject": "小男孩", "camera": "近景平滑推近",
                            "start_state": "男孩侧趴课桌手握铅笔", "action": "眼皮慢慢合上铅笔轻滑", "end_state": "完全闭眼呼吸平稳",
                        }],
                    }]

            pa_task = {"task_id": "t_flow_pa", "generation_mode": STORYBOARD_FLOW_MODE, "ratio": "9:16", "style": "短剧", "metadata": {"task_plan": {}}}
            pa_db = Mock()
            pa_db.find_many = AsyncMock(return_value=[{**flow_scenes[0]}])
            pa_db.update_one = AsyncMock()
            stub_anyio = SimpleNamespace(
                Semaphore=asyncio.Semaphore,
                to_thread=SimpleNamespace(run_sync=lambda fn, *a, abandon_on_cancel=None, **k: asyncio.sleep(0, result=fn(*a, **k))),
            )
            with patch("agents.prompt_agent.mongodb", pa_db), patch(
                "agents.prompt_agent.get_required_task", new=AsyncMock(return_value=pa_task)
            ), patch("agents.prompt_agent.anyio", stub_anyio), patch(
                "agents.prompt_agent.get_llm_service", return_value=FlowPromptLLM()
            ), patch("agents.prompt_agent.task_service.update_task_fields", new=AsyncMock()), patch(
                "agents.prompt_agent.mark_agent_start", new=AsyncMock()
            ), patch("agents.prompt_agent.mark_agent_success", new=AsyncMock()), patch(
                "agents.prompt_agent.mark_agent_failed", new=AsyncMock()
            ):
                prompts_out = asyncio.run(PromptAgent().run("t_flow_pa"))
            self.assertEqual(seen_per_second, [False])
            self.assertEqual(len(prompts_out[0]["motion_beats"]), 1)

            # 4. ImageAgent creates pure light-gray 4-view studio cast cards & avoids full-room anchor
            tmp_dir = Path(tmp)
            generated_log = []

            class FlowImageSvc:
                supports_reference_images = True
                max_reference_images = 9

                def generate_image(self, task_id, scene_id, image_prompt, negative_prompt=None, ratio="9:16", reference_images=None):
                    out = tmp_dir / f"{scene_id}.png"
                    Image.new("RGB", (64, 64), (220, 220, 220)).save(out)
                    generated_log.append((scene_id, ratio, list(reference_images or []), image_prompt))
                    return {"status": "success", "asset_path": str(out), "source": "qwen"}

            img_task = {
                "task_id": "t_flow_img",
                "generation_mode": STORYBOARD_FLOW_MODE,
                "ratio": "9:16",
                "style": "短剧",
                "metadata": {
                    "task_plan": {
                        "characters": [
                            {"name": "小男孩", "gender": "male", "appearance": "10岁圆脸短黑发"},
                            {"name": "女老师", "gender": "female", "appearance": "30岁浅色衬衫低马尾"},
                        ]
                    }
                },
            }
            img_scenes = [
                {"scene_id": "scene_01", "scene_index": 1, "duration": 4, "shot_type": "全景", "need_dynamic_video": True, "image_prompt": "小男孩与女老师在教室"},
                {"scene_id": "scene_02", "scene_index": 2, "duration": 3, "shot_type": "空镜", "need_dynamic_video": True, "image_prompt": "风吹过煤炭山谷空无一人"},
                {"scene_id": "scene_03", "scene_index": 3, "duration": 4, "shot_type": "近景", "need_dynamic_video": True, "image_prompt": "小男孩穿着粗布衣站在煤窑前"},
            ]
            img_db = Mock()
            img_db.find_many = AsyncMock(side_effect=lambda *a, **k: [dict(s) for s in img_scenes])
            img_db.find_one = AsyncMock(side_effect=lambda col, *a, **k: img_task if col == "video_tasks" else None)
            img_db.replace_one = AsyncMock()
            img_db.update_one = AsyncMock()
            with patch("agents.image_agent.mongodb", img_db), patch(
                "agents.image_agent.settings", SimpleNamespace(max_images_per_task=20, video_provider="wan")
            ), patch("agents.image_agent.anyio", stub_anyio), patch(
                "agents.image_agent.get_image_service", return_value=FlowImageSvc()
            ), patch(
                "agents.image_agent.dispatch_generation", side_effect=lambda t, o, k, fn: fn()
            ), patch("agents.image_agent.mark_agent_start", new=AsyncMock()), patch(
                "agents.image_agent.mark_agent_success", new=AsyncMock()
            ), patch("agents.image_agent.mark_agent_failed", new=AsyncMock()), patch(
                "services.media_quality.resolve_media",
                side_effect=lambda p: (tmp_dir / f"{Path(p).stem}.png") if "cast_ref_" in str(p) else Path(p),
            ):
                asyncio.run(ImageAgent().run("t_flow_img"))

            by_id = {sid: (r, refs, pr) for sid, r, refs, pr in generated_log}
            self.assertEqual(by_id["cast_ref_1"][0], "16:9")
            self.assertIn("4视图", by_id["cast_ref_1"][2])
            self.assertEqual(len(by_id["scene_01"][1]), 2)
            self.assertEqual(by_id["scene_02"][1], [])
            self.assertEqual(by_id["scene_03"][1], [str(tmp_dir / "cast_ref_1.png")])
            self.assertNotIn(str(tmp_dir / "scene_01.png"), by_id["scene_03"][1])

            # 5. Flash-white transition triggers cleanly on scene_change dream switch
            class DummyClip:
                def __init__(self, dur, val=100):
                    self.duration = dur
                    self._val = val

                def get_frame(self, t):
                    return np.full((8, 8, 3), self._val, dtype=np.uint8)

                def fl(self, fn):
                    out = DummyClip(self.duration, self._val)
                    out.get_frame = lambda t, _fn=fn, _src=self: _fn(_src.get_frame, t)
                    return out

            clips = [DummyClip(2.0, 100), DummyClip(2.0, 100)]
            applied = apply_cut_transitions(
                clips,
                [
                    {"scene_id": "s1", "transition_type": "cut", "transition_note": "闪白入梦"},
                    {"scene_id": "s2", "transition_type": "scene_change", "transition_note": "梦醒回到现实"},
                ],
            )
            self.assertEqual(applied[0]["mode"], "flash_white")
            self.assertGreater(int(clips[0].get_frame(1.99)[0, 0, 0]), 200)
            self.assertGreater(int(clips[1].get_frame(0.01)[0, 0, 0]), 200)

    def test_seven_pipeline_optimizations_bounds_bgm_cast_concurrency_and_vidu(self):
        import asyncio
        import threading
        import time
        from unittest.mock import AsyncMock

        with patch.dict(sys.modules, _runtime_stubs()), tempfile.TemporaryDirectory() as tmp:
            from agents.image_agent import ImageAgent, _match_cast_card_paths, build_end_frame_prompt
            from agents.video_agent import VideoAgent
            from services.director_heal_service import plan_scene_self_heal
            from services.generation_contract import STORYBOARD_FLOW_MODE, provider_duration_bounds
            from services.shot_continuity import select_bgm_file

            # 1. Seedance enforces >= 4s even in storyboard_flow; self-heal honors non-per-second beats
            self.assertEqual(
                provider_duration_bounds("doubao", "doubao-seedance-1-5-pro-251215", generation_mode=STORYBOARD_FLOW_MODE),
                (4, 12),
            )
            self.assertEqual(
                provider_duration_bounds("wan", "wan2.6-i2v-flash", generation_mode=STORYBOARD_FLOW_MODE),
                (3, 12),
            )
            heal_llm = SimpleNamespace(
                doctor_scene_prompt=lambda sc, rs: {
                    "video_prompt": "平滑推进特写表情",
                    "transition_type": "cut",
                    "heal_summary": "平滑收束",
                    "motion_beats": [{
                        "start": 0, "end": 4, "subject": "男生", "camera": "近景推进",
                        "start_state": "抬头", "action": "泛红眼眶微笑", "end_state": "定格微红眼眶",
                    }],
                }
            )
            healed = plan_scene_self_heal(
                {"scene_id": "s1", "duration": 4, "video_prompt": "原提示词"},
                ["画面微颤"],
                allow_llm=True,
                llm_service=heal_llm,
                generation_mode=STORYBOARD_FLOW_MODE,
            )
            self.assertEqual(healed["rewrite_source"], "llm_doctor")
            self.assertEqual(len(healed["motion_beats"]), 1)

            # 2. BGM auto-selection matches campus / heroic / suspense / none
            bgm_dir = Path(__file__).resolve().parents[1] / "data" / "bgm"
            campus_bgm = select_bgm_file({"user_input": "90秒校园早恋虐恋短剧，地铁重逢"}, [], bgm_dir)
            heroic_bgm = select_bgm_file({"user_input": "方志敏1927年弋横暴动革命历史课"}, [], bgm_dir)
            none_bgm = select_bgm_file({"bgm_preset": "none", "user_input": "校园"}, [], bgm_dir)
            self.assertIsNotNone(campus_bgm)
            self.assertIn("warm", Path(campus_bgm).name)
            self.assertIsNotNone(heroic_bgm)
            self.assertIn("heroic", Path(heroic_bgm).name)
            self.assertIsNone(none_bgm)

            # 3. ImageAgent generates up to 4 turnaround cards concurrently, persists metadata, avoids mis-matching 5th speaker, and generates end frame when flagged
            tmp_dir = Path(tmp)
            card_log = []

            class MultiCastImageSvc:
                supports_reference_images = True
                max_reference_images = 9

                def generate_image(self, task_id, scene_id, image_prompt, negative_prompt=None, ratio="9:16", reference_images=None):
                    out = tmp_dir / f"{scene_id}.png"
                    Image.new("RGB", (64, 64), (210, 210, 210)).save(out)
                    card_log.append((scene_id, list(reference_images or [])))
                    return {"status": "success", "asset_path": str(out), "source": "qwen"}

            cast_task = {
                "task_id": "t_opt_img",
                "generation_mode": STORYBOARD_FLOW_MODE,
                "ratio": "9:16",
                "style": "短剧",
                "metadata": {
                    "task_plan": {
                        "characters": [
                            {"name": "林夏", "gender": "female"},
                            {"name": "周延", "gender": "male"},
                            {"name": "班主任", "gender": "male"},
                            {"name": "母亲", "gender": "female"},
                            {"name": "地铁乘客", "gender": "male"},
                        ]
                    }
                },
            }
            cast_scenes = [
                {"scene_id": "s1", "scene_index": 1, "duration": 4, "shot_type": "近景", "need_dynamic_video": True, "need_end_frame": True, "end_state": "笔记本摊开在桌面", "image_prompt": "林夏与周延在教室"},
                {"scene_id": "s2", "scene_index": 2, "duration": 4, "shot_type": "中景", "need_dynamic_video": True, "image_prompt": "班主任怒斥众人"},
                {"scene_id": "s3", "scene_index": 3, "duration": 4, "shot_type": "特写", "need_dynamic_video": True, "image_prompt": "母亲哽咽劝说林夏"},
                {"scene_id": "s4", "scene_index": 4, "duration": 4, "shot_type": "近景", "need_dynamic_video": True, "speaker": "地铁乘客", "image_prompt": "陌生大叔低头看手机"},
            ]
            updated_calls = []

            async def _track_update(col, q, payload, *a, **k):
                updated_calls.append((col, q, payload))

            img_db = Mock()
            img_db.find_many = AsyncMock(side_effect=lambda *a, **k: [dict(s) for s in cast_scenes])
            img_db.find_one = AsyncMock(side_effect=lambda col, *a, **k: cast_task if col == "video_tasks" else None)
            img_db.replace_one = AsyncMock()
            img_db.update_one = AsyncMock(side_effect=_track_update)
            stub_anyio = SimpleNamespace(
                Semaphore=asyncio.Semaphore,
                to_thread=SimpleNamespace(run_sync=lambda fn, *a, abandon_on_cancel=None, **k: asyncio.sleep(0, result=fn(*a, **k))),
            )
            with patch("agents.image_agent.mongodb", img_db), patch(
                "agents.image_agent.settings", SimpleNamespace(max_images_per_task=20, video_provider="wan")
            ), patch("agents.image_agent.anyio", stub_anyio), patch(
                "agents.image_agent.get_image_service", return_value=MultiCastImageSvc()
            ), patch("agents.image_agent.dispatch_generation", side_effect=lambda t, o, k, fn: fn()), patch(
                "agents.image_agent.mark_agent_start", new=AsyncMock()
            ), patch("agents.image_agent.mark_agent_success", new=AsyncMock()), patch(
                "agents.image_agent.mark_agent_failed", new=AsyncMock()
            ), patch(
                "services.media_quality.resolve_media",
                side_effect=lambda p: (tmp_dir / f"{Path(p).stem}.png") if "cast_ref_" in str(p) or Path(p).is_absolute() is False else Path(p),
            ):
                asyncio.run(ImageAgent().run("t_opt_img"))

            card_ids = [sid for sid, _ in card_log if sid.startswith("cast_ref_")]
            self.assertEqual(len(card_ids), 4)
            # s1_end generated referencing s1.png & saved as last_frame_target_path
            s1_end_refs = [refs for sid, refs in card_log if sid == "s1_end"]
            self.assertEqual(s1_end_refs, [[str(tmp_dir / "s1.png")]])
            s1_updates = [p for c, q, p in updated_calls if c == "scenes" and q.get("_id") == "t_opt_img_s1"]
            self.assertEqual(s1_updates[-1]["last_frame_target_path"], str(tmp_dir / "s1_end.png"))
            # implicit_cast_cards persisted to video_tasks metadata for frontend pre-shoot UI
            meta_updates = [p for c, q, p in updated_calls if c == "video_tasks" and "metadata.implicit_cast_cards" in p]
            self.assertEqual(len(meta_updates), 1)
            self.assertEqual(len(meta_updates[0]["metadata.implicit_cast_cards"]), 4)
            # 5th minor character with no cast card must not receive protagonist's card[0]
            self.assertEqual(_match_cast_card_paths(cast_scenes[3], meta_updates[0]["metadata.implicit_cast_cards"], 9), [])
            self.assertIn("笔记本摊开在桌面", build_end_frame_prompt(cast_scenes[0]))

            # 4. Concurrent VideoAgent execution + Vidu trial resolution + dual-frame end_target handoff
            class ConcurrentViduSvc:
                model = "viduq3-turbo"
                supports_movement_amplitude = True
                supports_resolution = True

                def __init__(self):
                    self.active = 0
                    self.max_active = 0
                    self.lock = threading.Lock()
                    self.calls = []

                def generate_video(self, task_id, scene_id, image_path, video_prompt=None, duration=5, ratio="9:16", movement_amplitude="auto", resolution=None):
                    with self.lock:
                        self.active += 1
                        self.max_active = max(self.max_active, self.active)
                    time.sleep(0.03)
                    out = tmp_dir / f"vid_{scene_id}.mp4"
                    out.write_bytes(b"mp4")
                    with self.lock:
                        self.active -= 1
                        self.calls.append((scene_id, resolution, movement_amplitude))
                    return {"status": "success", "asset_path": str(out), "source": "vidu"}

            v_scenes = [
                {
                    "scene_id": f"vs_{i}", "scene_index": i, "duration": 4, "need_dynamic_video": True,
                    "transition_type": "cut", "image_path": str(tmp_dir / "s1.png"), "video_prompt": "推近反应",
                    "motion_beats": [{"start": 0, "end": 4, "subject": "林夏", "camera": "近景", "start_state": "静立", "action": "抬头微笑", "end_state": "微笑定格"}],
                }
                for i in range(1, 4)
            ]
            v_task = {"task_id": "t_vid_conc", "generation_mode": STORYBOARD_FLOW_MODE, "ratio": "9:16", "duration": 12, "style": "短剧", "metadata": {"task_plan": {}}}
            v_db = Mock()
            v_db.find_many = AsyncMock(side_effect=lambda col, *a, **k: [dict(s) for s in v_scenes] if col == "scenes" else [])
            v_db.find_one = AsyncMock(return_value=v_task)
            v_db.replace_one = AsyncMock()
            v_db.update_one = AsyncMock()
            conc_svc = ConcurrentViduSvc()

            async def _fake_dispatch(t, o, k, fn):
                return await asyncio.to_thread(fn)

            with patch("agents.video_agent.mongodb", v_db), patch(
                "agents.video_agent.anyio", SimpleNamespace(Semaphore=asyncio.Semaphore, to_thread=SimpleNamespace(run_sync=asyncio.to_thread))
            ), patch("agents.video_agent.video_service_for_task", return_value=conc_svc), patch(
                "agents.video_agent.dispatch_generation",
                side_effect=_fake_dispatch,
            ), patch("agents.video_agent.resolve_media", side_effect=lambda p: Path(p)), patch(
                "agents.video_agent.mark_agent_start", new=AsyncMock()
            ), patch("agents.video_agent.mark_agent_success", new=AsyncMock()), patch(
                "agents.video_agent.mark_agent_failed", new=AsyncMock()
            ):
                out_assets = asyncio.run(VideoAgent().run("t_vid_conc"))
                self.assertEqual(conc_svc.max_active, 3)
                self.assertEqual([a["scene_id"] for a in out_assets], ["vs_1", "vs_2", "vs_3"])
                self.assertTrue(all(res == "1080p" for _, res, _ in conc_svc.calls))

                # Trial mode automatically steps down Vidu resolution to configured trial resolution
                conc_svc.calls.clear()
                asyncio.run(VideoAgent().run("t_vid_conc", trial_scene_id="vs_1"))
                self.assertEqual(conc_svc.calls[0][1], "720p")

    def test_siliconflow_image_video_routing_bounds_and_catalog(self):
        import asyncio
        import io
        import json

        with patch.dict(sys.modules, _runtime_stubs()), tempfile.TemporaryDirectory() as tmp:
            from routes.settings_routes import ModelInput, query_model_catalog, test_model
            from services import provider_factory
            from services.generation_contract import STORYBOARD_FLOW_MODE, provider_duration_bounds
            from services.image.siliconflow_image_service import SiliconFlowImageService
            from services.model_config import is_siliconflow_url, siliconflow_v1_base
            from services.video.base import VideoRenderRejected
            from services.video.siliconflow_video_service import SiliconFlowVideoService

            # 1. URL helpers & provider routing + duration bounds
            self.assertTrue(is_siliconflow_url("https://api.siliconflow.cn/v1"))
            self.assertFalse(is_siliconflow_url("https://dashscope.aliyuncs.com"))
            self.assertEqual(siliconflow_v1_base("https://api.siliconflow.cn/v1/video/submit"), "https://api.siliconflow.cn/v1")
            self.assertEqual(
                provider_duration_bounds("siliconflow", "MiniMaxAI/MiniMax-H3", generation_mode=STORYBOARD_FLOW_MODE),
                (5, 12),
            )

            sf_saved = {
                "image": {"url": "https://api.siliconflow.cn/v1", "model": "black-forest-labs/FLUX.1-Kontext-dev", "key": "sf-k"},
                "video": {"url": "https://api.siliconflow.cn/v1", "model": "MiniMaxAI/MiniMax-H3", "key": "sf-k"},
            }
            with patch.object(provider_factory, "saved_models", return_value=sf_saved), patch.object(
                provider_factory, "model_config", return_value=sf_saved["video"]
            ), patch(
                "services.image.siliconflow_image_service.model_config", return_value=sf_saved["image"]
            ), patch(
                "services.video.siliconflow_video_service.model_config", return_value=sf_saved["video"]
            ):
                img_svc = provider_factory.get_image_service()
                vid_svc = provider_factory.get_video_service()
                self.assertIsInstance(img_svc, SiliconFlowImageService)
                self.assertIsInstance(vid_svc, SiliconFlowVideoService)
                self.assertTrue(img_svc.supports_reference_images)
                self.assertEqual(img_svc.max_reference_images, 1)
                self.assertTrue(vid_svc.supports_last_frame)
                self.assertEqual(provider_factory.active_video_provider_and_model(), ("siliconflow", "MiniMaxAI/MiniMax-H3"))

            # 2. SiliconFlowImageService generates image & supports 1 reference image on Kontext/Edit
            tmp_dir = Path(tmp)
            png_bytes = io.BytesIO()
            Image.new("RGB", (64, 64), (120, 160, 200)).save(png_bytes, format="PNG")
            png_raw = png_bytes.getvalue()

            posted_img_payloads = []

            class FakeResp:
                def __init__(self, status_code=200, body=None, content=b""):
                    self.status_code = status_code
                    self.is_success = 200 <= status_code < 300
                    self._body = body or {}
                    self.content = content
                    self.text = json.dumps(self._body)
                    self.request = SimpleNamespace(url=SimpleNamespace(path="/v1/models"))

                def json(self):
                    return self._body

                def raise_for_status(self):
                    if self.status_code >= 400:
                        raise RuntimeError(f"HTTP {self.status_code}")

            class FakeImgClient:
                def __init__(self, **kw):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

                def post(self, url, json=None, headers=None):
                    posted_img_payloads.append((url, json))
                    return FakeResp(200, {"images": [{"url": "https://cdn.sf.test/img.png"}]})

                def get(self, url, timeout=60):
                    return FakeResp(200, content=png_raw)

            ref_disk = tmp_dir / "ref.png"
            ref_disk.write_bytes(png_raw)
            img_profile = {"url": "https://api.siliconflow.cn", "model": "black-forest-labs/FLUX.1-Kontext-dev", "key": "sf-k"}
            with patch("services.image.siliconflow_image_service.model_config", return_value=img_profile), patch(
                "services.image.siliconflow_image_service.settings", SimpleNamespace(enable_paid_api=True)
            ), patch("services.image.siliconflow_image_service.httpx.Client", FakeImgClient), patch(
                "services.character_service.local_asset", return_value=ref_disk
            ), patch("services.character_service.validate_image", return_value=None):
                img_out = SiliconFlowImageService(storage_root=tmp_dir).generate_image(
                    "t_sf", "s1", "校园走廊男生回头", ratio="9:16", reference_images=["backend/assets/t_sf/images/ref.png"]
                )
            self.assertEqual(img_out["source"], "siliconflow")
            self.assertEqual(posted_img_payloads[0][0], "https://api.siliconflow.cn/v1/images/generations")
            self.assertTrue(str(posted_img_payloads[0][1]["image"]).startswith("data:image/png;base64,"))
            self.assertTrue((tmp_dir / "assets" / "t_sf" / "images" / "s1.png").is_file())

            # 3. SiliconFlowVideoService submits first+last frame & polls POST /v1/video/status
            vid_posts = []

            class FakeVidClient:
                def __init__(self, **kw):
                    self.polls = 0

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    pass

                def post(self, url, json=None, headers=None):
                    vid_posts.append((url, json))
                    if url.endswith("/video/submit"):
                        return FakeResp(200, {"requestId": "req-sf-1"})
                    self.polls += 1
                    if self.polls == 1:
                        return FakeResp(200, {"status": "InProgress"})
                    return FakeResp(200, {"status": "Succeed", "results": {"videos": [{"url": "https://cdn.sf.test/v.mp4"}]}})

                def get(self, url, timeout=120):
                    return FakeResp(200, content=b"fake-mp4-bytes")

            first_p = tmp_dir / "first.png"
            last_p = tmp_dir / "last.png"
            first_p.write_bytes(png_raw)
            last_p.write_bytes(png_raw)
            vid_profile = {"url": "https://api.siliconflow.cn/v1", "model": "MiniMaxAI/MiniMax-H3", "key": "sf-k"}
            with patch("services.video.siliconflow_video_service.model_config", return_value=vid_profile), patch(
                "services.video.siliconflow_video_service.settings",
                SimpleNamespace(enable_paid_api=True, siliconflow_video_resolution="768p"),
            ), patch("services.video.siliconflow_video_service.httpx.Client", FakeVidClient), patch(
                "services.video.siliconflow_video_service.time.sleep", return_value=None
            ):
                svc = SiliconFlowVideoService(storage_root=tmp_dir)
                dialect_prompt, dialect_opts = adapt_video_submission(
                    svc,
                    {"scene_id": "s1", "last_frame_target_path": str(last_p), "_is_trial": True},
                    "男生转身微笑",
                )
                self.assertEqual(dialect_opts["resolution"], "480p")
                self.assertEqual(dialect_opts["last_frame_path"], str(last_p))
                vid_res = svc.generate_video(
                    "t_sf", "s1", str(first_p), video_prompt=dialect_prompt, duration=4, ratio="9:16", **dialect_opts
                )
            self.assertEqual(vid_res["source"], "siliconflow")
            self.assertEqual(vid_res["duration"], 5)  # clamped cleanly to MiniMax-H3 >=5s minimum
            submit_url, submit_body = vid_posts[0]
            self.assertEqual(submit_url, "https://api.siliconflow.cn/v1/video/submit")
            self.assertEqual([img["role"] for img in submit_body["images"]], ["first_frame", "last_frame"])
            self.assertEqual(submit_body["resolution"], "480p")
            self.assertEqual(vid_posts[1][0], "https://api.siliconflow.cn/v1/video/status")
            self.assertEqual(vid_posts[1][1], {"requestId": "req-sf-1"})

            # 4. SiliconFlow catalog query + test skips /api/v1/files and surfaces Edit/Kontext + MiniMax-H3
            from pydantic import SecretStr
            sf_urls_seen = []

            class FakeAsyncCatalog:
                def __init__(self, **kw):
                    pass

                async def __aenter__(self):
                    async def _get(url, headers=None):
                        sf_urls_seen.append(url)
                        if "type=video" in url:
                            return FakeResp(200, {"data": [{"id": "MiniMaxAI/MiniMax-H3"}]})
                        return FakeResp(200, {"data": [{"id": "black-forest-labs/FLUX.1-Kontext-dev"}, {"id": "Qwen/Qwen-Image-Edit"}]})

                    return SimpleNamespace(get=_get)

                async def __aexit__(self, *a):
                    pass

            with patch("routes.settings_routes.httpx.AsyncClient", FakeAsyncCatalog):
                vid_cat = asyncio.run(query_model_catalog("video", ModelInput(url="https://api.siliconflow.cn/v1", model="", key=SecretStr("sf-k"))))
                img_cat = asyncio.run(query_model_catalog("image", ModelInput(url="https://api.siliconflow.cn/v1", model="", key=SecretStr("sf-k"))))
                vid_tst = asyncio.run(test_model("video", ModelInput(url="https://api.siliconflow.cn/v1", model="MiniMaxAI/MiniMax-H3", key=SecretStr("sf-k"))))
            self.assertEqual(vid_cat["data"]["models"], ["MiniMaxAI/MiniMax-H3"])
            self.assertIn("Qwen/Qwen-Image-Edit", img_cat["data"]["models"])
            self.assertIn("black-forest-labs/FLUX.1-Kontext-dev", img_cat["data"]["models"])
            self.assertEqual(vid_tst["data"]["result"], "model_listed")
            self.assertTrue(all("/api/v1/files" not in u for u in sf_urls_seen))

    def test_dynamic_generation_limits_web_override_and_unlimited(self):
        import asyncio
        from unittest.mock import AsyncMock

        with patch.dict(sys.modules, _runtime_stubs()), tempfile.TemporaryDirectory() as tmp:
            from routes.settings_routes import LimitsInput, get_limits, reset_limits, update_limits
            from services import model_config

            tmp_limits = Path(tmp) / ".env.limits.local"
            with patch.object(model_config, "LIMITS_PATH", tmp_limits):
                initial = asyncio.run(get_limits())
                self.assertEqual(initial["data"]["source"], "env")

                saved = asyncio.run(
                    update_limits(
                        LimitsInput(
                            max_images_per_task=0,
                            max_video_seconds_per_task=0,
                            max_dynamic_scenes_per_task=0,
                        )
                    )
                )
                self.assertEqual(saved["data"]["source"], "local")
                self.assertEqual(saved["data"]["max_images_per_task"], 0)
                self.assertEqual(model_config.generation_limits()["max_images_per_task"], 0)

                # Reset restores env defaults
                restored = asyncio.run(reset_limits())
                self.assertEqual(restored["data"]["source"], "env")
                self.assertFalse(tmp_limits.exists())

            # ImageAgent honors unlimited (0) and task.generation_limit override
            from agents.image_agent import ImageAgent

            fake_scenes = [
                {"scene_id": f"s{i}", "scene_index": i, "duration": 5, "need_dynamic_video": True, "transition_type": "cut", "image_prompt": "test"}
                for i in range(1, 13)
            ]
            img_db = Mock()
            img_db.find_many = AsyncMock(return_value=fake_scenes)
            img_db.find_one = AsyncMock(return_value={"task_id": "t_lim", "ratio": "9:16", "generation_limit": 20})
            img_db.replace_one = AsyncMock()
            img_db.update_one = AsyncMock()
            stub_img = Mock(supports_reference_images=False, max_reference_images=0)
            stub_img.generate_image.side_effect = lambda **kw: {"status": "success", "asset_path": f"backend/assets/t_lim/images/{kw['scene_id']}.png", "source": "stub"}

            async def _fake_dispatch(t, o, k, fn):
                return fn()

            stub_anyio = SimpleNamespace(
                Semaphore=asyncio.Semaphore,
                to_thread=SimpleNamespace(run_sync=lambda fn, *a, abandon_on_cancel=None, **k: asyncio.sleep(0, result=fn(*a, **k))),
            )
            with patch("agents.image_agent.mongodb", img_db), patch(
                "agents.image_agent.anyio", stub_anyio
            ), patch(
                "agents.image_agent.settings", SimpleNamespace(max_images_per_task=10, video_provider="siliconflow")
            ), patch("agents.image_agent.get_image_service", return_value=stub_img), patch(
                "agents.image_agent.dispatch_generation", side_effect=_fake_dispatch
            ), patch("agents.image_agent.mark_agent_start", new=AsyncMock()), patch(
                "agents.image_agent.mark_agent_success", new=AsyncMock()
            ), patch("agents.image_agent.mark_agent_failed", new=AsyncMock()):
                out = asyncio.run(ImageAgent().run("t_lim"))
            self.assertEqual(len(out), 12)

            # Unlimited (0) allows >10 scenes even without task.generation_limit
            img_db.find_one = AsyncMock(return_value={"task_id": "t_unlim", "ratio": "9:16"})
            with patch("agents.image_agent.mongodb", img_db), patch(
                "agents.image_agent.anyio", stub_anyio
            ), patch(
                "agents.image_agent.settings", SimpleNamespace(max_images_per_task=0, video_provider="siliconflow")
            ), patch("agents.image_agent.get_image_service", return_value=stub_img), patch(
                "agents.image_agent.dispatch_generation", side_effect=_fake_dispatch
            ), patch("agents.image_agent.mark_agent_start", new=AsyncMock()), patch(
                "agents.image_agent.mark_agent_success", new=AsyncMock()
            ), patch("agents.image_agent.mark_agent_failed", new=AsyncMock()):
                out_unlim = asyncio.run(ImageAgent().run("t_unlim"))
            self.assertEqual(len(out_unlim), 12)

    def test_end_to_end_pipeline_hardening_storyboard_dialogue_prompt_voice(self):
        import asyncio
        from unittest.mock import AsyncMock

        with patch.dict(sys.modules, _runtime_stubs()), tempfile.TemporaryDirectory() as tmp:
            from agents.dialogue_polish_agent import DialoguePolishAgent
            from agents.prompt_agent import PromptAgent, _format_visual_bible_brief, _normalize_motion_beats
            from agents.storyboard_agent import _sanitize_storyboard_draft, validate_storyboard_scenes
            from agents.voice_agent import synthesize_dialogue

            # 1. Storyboard sanitizer repairs vague terms, unknown transition, missing helper fields & out-of-range cues
            messy_draft = [
                {
                    "scene_id": "",
                    "duration": 5,
                    "transition_type": "flash_white",
                    "visual_description": "电影感氛围感震撼，林晚抬手插进测温针",
                    "character_state": "",
                    "scene_continuity": "",
                    "transition_note": "",
                    "voiceover": "",
                    "dialogue_lines": [],
                    "voice_start": 9.0,
                    "sound_cues": [{"name": "impact", "start": 8.0}, {"name": "unknown_fx", "start": 1.0}],
                },
                {
                    "scene_id": "scene_02",
                    "duration": 5,
                    "transition_type": "continue_action",
                    "visual_description": "周明哲低头盯住表盘指针滑到86度",
                    "voiceover": "清汤九十二度，你差六度。",
                },
            ]
            _sanitize_storyboard_draft(messy_draft, allow_silent=True)
            validate_storyboard_scenes(messy_draft, target_duration=10, enforce_voiceover_density=False, allow_silent=True)
            self.assertEqual(messy_draft[0]["scene_id"], "scene_01")
            self.assertEqual(messy_draft[0]["transition_type"], "scene_change")
            self.assertNotIn("电影感", messy_draft[0]["visual_description"])
            self.assertEqual(messy_draft[0]["sound_cues"], [{"name": "impact", "start": 4.9}])
            self.assertLess(messy_draft[0]["voice_start"], 5.0)
            self.assertTrue(messy_draft[0]["end_state"])

            # 2. DialoguePolishAgent survives missing scene_id / omitted scene without crashing
            dp_scenes = [
                {"scene_id": "scene_01", "scene_index": 1, "duration": 5, "transition_type": "cut",
                 "voiceover": "二十秒后封条贴门。", "visual_description": "林晚站在松鹤楼木雕大门前",
                 "character_state": "冷静→锋利", "scene_continuity": "开场建立", "transition_note": "切近景"},
                {"scene_id": "scene_02", "scene_index": 2, "duration": 5, "transition_type": "cut",
                 "voiceover": "", "visual_description": "全场宾客椅子刮地站起",
                 "character_state": "骚动→震惊", "scene_continuity": "接上镜", "transition_note": "切特写"},
            ]
            dp_db = Mock()
            dp_db.find_many = AsyncMock(return_value=dp_scenes)
            dp_db.update_one = AsyncMock()
            dp_llm = Mock()
            dp_llm.polish_dialogue.return_value = []  # intentionally omit all scenes to test fallback
            stub_anyio = SimpleNamespace(
                Semaphore=asyncio.Semaphore,
                to_thread=SimpleNamespace(run_sync=lambda fn, *a, abandon_on_cancel=None, **k: asyncio.sleep(0, result=fn(*a, **k))),
            )
            with patch("agents.dialogue_polish_agent.mongodb", dp_db), patch(
                "agents.dialogue_polish_agent.anyio", stub_anyio
            ), patch(
                "agents.dialogue_polish_agent.get_required_task",
                new=AsyncMock(return_value={"task_id": "t_dp", "duration": 10, "generation_mode": "storyboard_flow"}),
            ), patch("agents.dialogue_polish_agent.get_llm_service", return_value=dp_llm), patch(
                "agents.dialogue_polish_agent.mark_agent_start", new=AsyncMock()
            ), patch("agents.dialogue_polish_agent.mark_agent_success", new=AsyncMock()), patch(
                "agents.dialogue_polish_agent.mark_agent_failed", new=AsyncMock()
            ):
                dp_out = asyncio.run(DialoguePolishAgent().run("t_dp"))
            self.assertEqual(dp_out[0]["voiceover"], "二十秒后封条贴门。")
            self.assertEqual(dp_out[1]["voiceover"], "")

            # 3. PromptAgent strips raw JSON bible, aligns mismatched scene_id & normalizes motion_beats
            brief = _format_visual_bible_brief(
                {
                    "visual_world": "暖黄吊灯的老字号红木厅堂",
                    "characters": [{"name": "林晚", "face_dna": "左耳后朱砂痣", "signature_clothing": "白色厨师服", "tts_voice": "zh-CN-XiaoxiaoNeural"}],
                },
                {"visual_description": "林晚抽起黄铜测温针"},
            )
            self.assertIn("林晚", brief)
            self.assertNotIn("tts_voice", brief)
            self.assertNotIn("{", brief)

            beats_ps = _normalize_motion_beats(
                [{"start": "0", "end": "3", "subject": "林晚", "action": "扎针测温"}],
                5,
                {"visual_description": "林晚扎针测温"},
                per_second=True,
            )
            self.assertEqual(len(beats_ps), 5)
            self.assertEqual([(b["start"], b["end"]) for b in beats_ps], [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)])

            pa_scene = {
                "scene_id": "scene_01", "scene_index": 1, "duration": 5, "transition_type": "cut",
                "visual_description": "林晚左手抽出黄铜测温针", "voiceover": "你差六度。",
            }
            pa_db = Mock()
            pa_db.find_many = AsyncMock(return_value=[pa_scene])
            pa_db.update_one = AsyncMock()
            pa_llm = Mock()
            pa_llm.generate_image_prompts.return_value = [{
                "scene_id": "1",  # mismatched ID format
                "image_prompt": "林晚手握测温针近景",
                "video_prompt": "林晚抬手把测温针扎进汤盅",
                "motion_beats": [{"start": 0, "end": 3, "subject": "林晚", "camera": "近景", "start_state": "握针", "action": "扎针", "end_state": "指针停86"}],
            }]
            with patch("agents.prompt_agent.mongodb", pa_db), patch(
                "agents.prompt_agent.anyio", stub_anyio
            ), patch(
                "agents.prompt_agent.get_required_task",
                new=AsyncMock(return_value={"task_id": "t_pa2", "ratio": "9:16", "style": "短剧", "generation_mode": "storyboard_flow", "metadata": {"task_plan": {"visual_world": "松鹤楼大厅"}}}),
            ), patch("agents.prompt_agent.get_llm_service", return_value=pa_llm), patch(
                "agents.prompt_agent.task_service.update_task_fields", new=AsyncMock()
            ), patch("agents.prompt_agent.mark_agent_start", new=AsyncMock()), patch(
                "agents.prompt_agent.mark_agent_success", new=AsyncMock()
            ), patch("agents.prompt_agent.mark_agent_failed", new=AsyncMock()):
                pa_out = asyncio.run(PromptAgent().run("t_pa2"))
            self.assertEqual(pa_out[0]["scene_id"], "scene_01")
            self.assertEqual(pa_out[0]["motion_beats"][-1]["end"], 5)
            self.assertNotIn('{"characters"', pa_out[0]["image_prompt"])

            # 4. VoiceAgent skips stage-direction-only dialogue lines instead of crashing TTS
            v_scene = {
                "scene_id": "s_v",
                "duration": 5,
                "dialogue_lines": [
                    {"speaker": "赵鹏", "text": "（冷笑一声）", "start": 0.2},
                    {"speaker": "林晚", "text": "你差六度。", "start": 1.0},
                ],
            }
            with patch("agents.voice_agent.tts_service.generate_voice", new=AsyncMock(return_value=1.2), create=True):
                rendered_v = asyncio.run(synthesize_dialogue(v_scene, Path(tmp), "t_v", "s_v"))
            self.assertEqual(len(rendered_v), 1)
            self.assertEqual(rendered_v[0]["text"], "你差六度。")
            self.assertEqual(len(v_scene["dialogue_lines"]), 1)

    def test_prompt_batching_series_duration_range_and_settings_db_sync(self):
        import asyncio
        from unittest.mock import AsyncMock

        with patch.dict(sys.modules, _runtime_stubs()), tempfile.TemporaryDirectory() as tmp:
            from agents.prompt_agent import PromptAgent, _match_batch_prompts
            from services.series_service import episode_duration_plan

            # 1. Batch matcher aligns wrong scene_id formats by index / position
            chunk = [{"scene_id": "scene_01", "scene_index": 1}, {"scene_id": "scene_02", "scene_index": 2}]
            matched = _match_batch_prompts([{"scene_id": "2"}, {"scene_id": "1"}], chunk)
            self.assertEqual(set(matched), {"scene_01", "scene_02"})
            self.assertEqual(matched["scene_02"]["scene_id"], "scene_02")

            # 2. 6 per-second scenes -> 2 batched LLM calls (3 each), context flows across batches
            calls = []

            class BatchLLM:
                def generate_image_prompts(self, batch, ratio="9:16", style=None, visual_bible=None):
                    calls.append([(sc["scene_id"], sc.get("previous_segment_context")) for sc in batch])
                    out = []
                    for sc in batch:
                        dur = int(sc["duration"])
                        out.append({
                            "scene_id": sc["scene_id"],
                            "image_prompt": "首帧", "video_prompt": "动作",
                            "motion_beats": [
                                {"start": i, "end": i + 1, "subject": "甲", "camera": "中景", "start_state": "站",
                                 "action": "动", "end_state": f"{sc['scene_id']}收尾" if i == dur - 1 else "进行"}
                                for i in range(dur)
                            ],
                        })
                    return out

            scenes = [{"scene_id": f"scene_{i}", "scene_index": i, "duration": 4, "visual_description": f"镜{i}",
                       "voiceover": "台词"} for i in range(1, 7)]
            task = {"task_id": "t_batch", "generation_mode": "auto_director", "ratio": "9:16", "style": "短剧",
                    "metadata": {"task_plan": {}}}
            db = Mock()
            db.find_many = AsyncMock(side_effect=lambda *a, **k: [dict(s) for s in scenes])
            db.update_one = AsyncMock()
            stub_anyio = SimpleNamespace(
                Semaphore=asyncio.Semaphore,
                to_thread=SimpleNamespace(run_sync=lambda fn, *a, abandon_on_cancel=None, **k: asyncio.sleep(0, result=fn(*a, **k))),
            )
            with patch("agents.prompt_agent.mongodb", db), patch(
                "agents.prompt_agent.get_required_task", new=AsyncMock(return_value=task)
            ), patch("agents.prompt_agent.anyio", stub_anyio), patch(
                "agents.prompt_agent.get_llm_service", return_value=BatchLLM()
            ), patch("agents.prompt_agent.task_service.update_task_fields", new=AsyncMock()), patch(
                "agents.prompt_agent.mark_agent_start", new=AsyncMock()
            ), patch("agents.prompt_agent.mark_agent_success", new=AsyncMock()), patch(
                "agents.prompt_agent.mark_agent_failed", new=AsyncMock()
            ):
                out = asyncio.run(PromptAgent().run("t_batch"))
            self.assertEqual(len(out), 6)
            self.assertEqual(len(calls), 2)
            self.assertEqual([len(c) for c in calls], [3, 3])
            self.assertIsNone(calls[0][0][1])
            self.assertEqual(calls[1][0][1]["scene_index"], 3)
            self.assertEqual(calls[1][0][1]["final_second"]["end_state"], "scene_3收尾")

            # 3. Series duration plan: native locks multiples of 10, flexible modes give a range
            self.assertEqual(episode_duration_plan({"episode_duration": 30}), ("native_10s", 30, None, None))
            self.assertEqual(episode_duration_plan({"generation_mode": "native_10s", "episode_duration": 45})[1], 50)
            self.assertEqual(
                episode_duration_plan({"generation_mode": "auto_director", "episode_duration": 68,
                                       "episode_duration_min": 45, "episode_duration_max": 90}),
                ("auto_director", None, 45, 90),
            )
            mode, dur, lo, hi = episode_duration_plan({"generation_mode": "storyboard_flow", "episode_duration": 60})
            self.assertEqual((mode, dur, lo, hi), ("storyboard_flow", None, 45, 75))

            # 4. Settings persistence: DB doc hydrates missing local cache; save writes to DB
            from services import settings_persistence as sp

            store = {}

            class FakeDB:
                database = object()

                async def find_one(self, coll, query):
                    return store.get(query["_id"])

                async def replace_one(self, coll, query, doc, upsert=False):
                    store[query["_id"]] = doc
                    return 1

                async def delete_one(self, coll, query):
                    store.pop(query["_id"], None)
                    return 1

            models_path = Path(tmp) / "models.json"
            limits_path = Path(tmp) / "limits.json"
            real_models_path = Path(__file__).resolve().parents[1] / ".env.models.local"
            existed_before = real_models_path.exists()
            profile = {"prompt": {"url": "https://api.deepseek.com", "model": "deepseek-chat", "key": "test-placeholder"}}
            with patch.object(sp.model_config, "PATH", models_path), patch.object(
                sp.model_config, "LIMITS_PATH", limits_path
            ), patch.object(sp, "mongodb", FakeDB()):
                from datetime import UTC as _UTC, datetime as _dt
                store["model_profiles"] = {"_id": "model_profiles", "models": profile, "updated_at": _dt.now(_UTC)}
                store["generation_limits"] = {"_id": "generation_limits", "limits": {"max_images_per_task": 0},
                                              "updated_at": _dt.now(_UTC)}
                result = asyncio.run(sp.hydrate_settings_from_db())
                self.assertEqual(result, {"models": True, "limits": True})
                self.assertEqual(sp.model_config.saved_models()["prompt"]["model"], "deepseek-chat")
                self.assertEqual(sp.model_config.saved_limits(), {"max_images_per_task": 0})

                new_models = {**profile, "image": {"url": "https://api.siliconflow.cn/v1", "model": "Kwai-Kolors/Kolors", "key": "test-x"}}
                self.assertTrue(asyncio.run(sp.persist_models_to_db(new_models)))
                self.assertIn("image", store["model_profiles"]["models"])
                self.assertTrue(asyncio.run(sp.persist_limits_to_db(None)))
                self.assertNotIn("generation_limits", store)

            if not existed_before:
                self.assertFalse(real_models_path.exists())

            # Offline DB: persistence gracefully reports False
            with patch.object(sp, "mongodb", SimpleNamespace(database=None)):
                self.assertFalse(asyncio.run(sp.persist_models_to_db(profile)))
                self.assertEqual(asyncio.run(sp.hydrate_settings_from_db()), {"models": False, "limits": False})

    def test_series_nonref_fallback_and_story_craft_hardening(self):
        import asyncio
        from unittest.mock import AsyncMock

        with patch.dict(sys.modules, _runtime_stubs()), tempfile.TemporaryDirectory() as tmp:
            from agents.image_agent import ImageAgent
            from agents.prompt_agent import PromptAgent
            from agents.storyboard_agent import _sanitize_storyboard_draft
            from services import drama_craft_service, series_service as ss_mod
            from services.character_binding import validate_series_scene

            bound_char = {
                "character_id": "char_linwan",
                "name": "林晚",
                "revision": 1,
                "face_dna": "25岁，高马尾，左耳后朱砂痣",
                "signature_clothing": "白色立领厨师服配黑防滑鞋",
                "reference_path": "backend/assets/characters/char_linwan/v1/anchor.png",
                "reference_images": {"front": "backend/assets/characters/char_linwan/v1/anchor.png"},
                "tts_voice": "zh-CN-XiaoxiaoNeural",
            }
            task = {
                "_id": "t_nr",
                "task_id": "t_nr",
                "ratio": "9:16",
                "style": "短剧",
                "generation_mode": "auto_director",
                "metadata": {
                    "task_plan": {"visual_world": "松鹤楼大厅"},
                    "series_context": {"characters": [bound_char]},
                },
            }

            # 1. Storyboard sanitizer maps character name -> character_id & drops unknown hallucinated IDs;
            #    validate_series_scene permits minor non-bound speaker without crashing.
            draft = [{
                "scene_id": "scene_01",
                "duration": 5,
                "character_ids": ["林晚", "hallucinated_id"],
                "visual_description": "林晚手持黄铜测温针逼近汤盅",
                "voiceover": "请退后！",
                "dialogue_lines": [{"speaker": "保安", "text": "请退后！", "start": 0.2}],
            }]
            _sanitize_storyboard_draft(draft, bound_cast=[bound_char])
            self.assertEqual(draft[0]["character_ids"], ["char_linwan"])
            validate_series_scene(task, draft[0])

            # 2. PromptAgent + ImageAgent gracefully fall back to text DNA (no ref images sent) for Kolors/FLUX
            non_ref_img_calls = []

            class NonRefImgSvc:
                model = "Kwai-Kolors/Kolors"
                supports_reference_images = False
                max_reference_images = 0

                def generate_image(self, task_id, scene_id, image_prompt, negative_prompt=None, ratio="9:16", reference_images=None):
                    non_ref_img_calls.append({"scene_id": scene_id, "reference_images": reference_images})
                    dest = Path(tmp) / f"{scene_id}.png"
                    dest.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 1600)
                    return {"status": "success", "asset_path": str(dest)}

            scene_doc = {
                "_id": "t_nr_scene_01",
                "task_id": "t_nr",
                "scene_id": "scene_01",
                "scene_index": 1,
                "duration": 4,
                "character_ids": ["char_linwan"],
                "visual_description": "林晚手握测温针",
                "voiceover": "差六度。",
                "need_dynamic_video": True,
                "status": "storyboard_done",
            }
            pa_llm = Mock()
            pa_llm.generate_image_prompts.return_value = [{
                "scene_id": "scene_01",
                "image_prompt": "林晚站在餐桌旁",
                "video_prompt": "林晚抬头质问",
                "motion_beats": [{"start": 0, "end": 4, "subject": "林晚", "camera": "中景", "start_state": "握针", "action": "抬眼", "end_state": "直视"}],
            }]
            db = Mock()
            db.find_many = AsyncMock(side_effect=lambda *a, **k: [dict(scene_doc)])
            db.find_one = AsyncMock(return_value=task)
            db.update_one = AsyncMock()
            db.replace_one = AsyncMock()
            stub_anyio = SimpleNamespace(
                Semaphore=asyncio.Semaphore,
                to_thread=SimpleNamespace(run_sync=lambda fn, *a, abandon_on_cancel=None, **k: asyncio.sleep(0, result=fn(*a, **k))),
            )
            nonref_svc = NonRefImgSvc()
            with patch("agents.prompt_agent.mongodb", db), patch(
                "agents.prompt_agent.get_required_task", new=AsyncMock(return_value=task)
            ), patch("agents.prompt_agent.anyio", stub_anyio), patch(
                "agents.prompt_agent.get_llm_service", return_value=pa_llm
            ), patch(
                "services.provider_factory.get_image_service", return_value=nonref_svc
            ), patch("agents.prompt_agent.task_service.update_task_fields", new=AsyncMock()), patch(
                "agents.prompt_agent.mark_agent_start", new=AsyncMock()
            ), patch("agents.prompt_agent.mark_agent_success", new=AsyncMock()), patch(
                "agents.prompt_agent.mark_agent_failed", new=AsyncMock()
            ):
                pa_out = asyncio.run(PromptAgent().run("t_nr"))
            self.assertFalse(pa_out[0]["three_layer_stripped"])
            self.assertIn("左耳后朱砂痣", pa_out[0]["image_prompt"])
            scene_doc.update(pa_out[0])

            async def _fake_dispatch(t, op, sid, fn):
                return fn()

            with patch("agents.image_agent.mongodb", db), patch("agents.image_agent.anyio", stub_anyio), patch(
                "agents.image_agent.settings", SimpleNamespace(max_images_per_task=0, video_provider="siliconflow")
            ), patch("agents.image_agent.get_image_service", return_value=nonref_svc), patch(
                "agents.image_agent.dispatch_generation", side_effect=_fake_dispatch
            ), patch("agents.image_agent.mark_agent_start", new=AsyncMock()), patch(
                "agents.image_agent.mark_agent_success", new=AsyncMock()
            ), patch("agents.image_agent.mark_agent_failed", new=AsyncMock()):
                img_out = asyncio.run(ImageAgent().run("t_nr"))
            self.assertEqual(len(img_out), 1)
            self.assertIsNone(non_ref_img_calls[0]["reference_images"])

            # 3. SeriesService.start_run allows bound-character series with non-reference image models
            svc = ss_mod.SeriesService()
            series_doc = {
                "_id": "ser_nr",
                "series_id": "ser_nr",
                "status": "ready",
                "outline_confirmed": True,
                "characters": [bound_char],
                "episodes": [{"episode_num": 1, "status": "pending", "task_id": None}],
                "updated_at": "2026-11-19T00:00:00+00:00",
            }
            ss_db = Mock()
            ss_db.find_one = AsyncMock(return_value=series_doc)
            ss_db.update_one = AsyncMock(return_value=1)
            with patch.object(ss_mod, "mongodb", ss_db), patch(
                "services.provider_factory.get_image_service", return_value=nonref_svc
            ):
                claimed = asyncio.run(svc.start_run("ser_nr"))
            self.assertEqual(claimed["series_id"], "ser_nr")

            # 4. StudioCraft ai_decompose_novel_to_drama resolves outline aliases & range fields
            fake_craft_llm = Mock()
            fake_craft_llm._call_api.return_value = {
                "title": "测试剧",
                "episodes_outline": [{"episode_num": 1, "title": "第一集", "opening_3s_hook": "倒计时十秒", "core_event": "", "cliffhanger_hook": ""}],
                "first_episode_script": "△林晚拍桌。",
            }
            with patch.object(drama_craft_service, "_get_real_llm", return_value=fake_craft_llm):
                craft = drama_craft_service.ai_decompose_novel_to_drama(
                    "松鹤楼老字号危机故事正文内容",
                    total_episodes=2,
                    episode_duration=60,
                    episode_duration_min=45,
                    episode_duration_max=90,
                )
            self.assertEqual(craft["episode_duration_min"], 45)
            self.assertEqual(craft["episode_duration_max"], 90)
            self.assertEqual(craft["episodes_outline"][0]["opening_hook"], "倒计时十秒")
            self.assertTrue(craft["episodes_outline"][0]["core_event"])
            self.assertTrue(craft["episodes_outline"][1]["ending_hook"])

    def test_character_portrait_face_lock_routing_prompts_and_negative_prompts(self):
        import asyncio
        import io
        import json
        from PIL import Image

        with patch.dict(sys.modules, _runtime_stubs()), tempfile.TemporaryDirectory() as tmp:
            from services import character_service as cs_mod
            from services.image.siliconflow_image_service import SiliconFlowImageService

            tmp_dir = Path(tmp)
            png_bytes = io.BytesIO()
            Image.new("RGB", (128, 128), (110, 140, 170)).save(png_bytes, format="PNG")
            png_raw = png_bytes.getvalue()

            posted = []

            class FakeResp:
                def __init__(self, status_code=200, body=None, content=b""):
                    self.status_code = status_code
                    self.is_success = 200 <= status_code < 300
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
                    posted.append(json)
                    return FakeResp(200, {"images": [{"url": "https://cdn.sf.test/out.png"}]})

                def get(self, url, timeout=60):
                    return FakeResp(200, content=png_raw)

            ref1 = tmp_dir / "ref1.png"
            ref2 = tmp_dir / "ref2.png"
            ref1.write_bytes(png_raw)
            ref2.write_bytes(png_raw)

            # 1. SiliconFlow Qwen-Image-Edit-2509: 3 refs, auto T2I fallback -> Qwen/Qwen-Image (928x1664),
            #    multi-ref -> image + image2 without image_size; Kolors -> 720x1280 + negative_prompt
            edit_profile = {"url": "https://api.siliconflow.cn/v1", "model": "Qwen/Qwen-Image-Edit-2509", "key": "test-sf"}
            with patch("services.image.siliconflow_image_service.model_config", return_value=edit_profile), patch(
                "services.image.siliconflow_image_service.settings", SimpleNamespace(enable_paid_api=True)
            ), patch("services.image.siliconflow_image_service.httpx.Client", FakeClient), patch(
                "services.character_service.local_asset", side_effect=[ref1, ref2]
            ), patch("services.character_service.validate_image", return_value=None):
                svc = SiliconFlowImageService(storage_root=tmp_dir)
                self.assertTrue(svc.supports_reference_images)
                self.assertEqual(svc.max_reference_images, 3)

                # No refs -> auto Qwen/Qwen-Image with 928x1664
                svc.generate_image("t1", "front", "正面照", negative_prompt="多张脸", ratio="9:16")
                # 2 refs -> Qwen/Qwen-Image-Edit-2509 with image + image2 and no image_size
                svc.generate_image(
                    "t1", "scene1", "两人同框", negative_prompt="重影", ratio="9:16",
                    reference_images=["backend/assets/r1.png", "backend/assets/r2.png"],
                )

            self.assertEqual(posted[0]["model"], "Qwen/Qwen-Image")
            self.assertEqual(posted[0]["image_size"], "928x1664")
            self.assertEqual(posted[0]["negative_prompt"], "多张脸")
            self.assertEqual(posted[1]["model"], "Qwen/Qwen-Image-Edit-2509")
            self.assertNotIn("image_size", posted[1])
            self.assertTrue(posted[1]["image"].startswith("data:image/png;base64,"))
            self.assertTrue(posted[1]["image2"].startswith("data:image/png;base64,"))

            kolors_profile = {"url": "https://api.siliconflow.cn/v1", "model": "Kwai-Kolors/Kolors", "key": "test-sf"}
            with patch("services.image.siliconflow_image_service.model_config", return_value=kolors_profile), patch(
                "services.image.siliconflow_image_service.settings", SimpleNamespace(enable_paid_api=True)
            ), patch("services.image.siliconflow_image_service.httpx.Client", FakeClient):
                svc_k = SiliconFlowImageService(storage_root=tmp_dir)
                self.assertFalse(svc_k.supports_reference_images)
                svc_k.generate_image("t2", "s1", "正面", negative_prompt="多张脸", ratio="9:16")
            self.assertEqual(posted[2]["image_size"], "720x1280")
            self.assertEqual(posted[2]["negative_prompt"], "多张脸")

            # 2. CharacterService portrait prompts put framing+identity first, move exclusions to negative_prompt,
            #    and return consistency_warning on non-reference models
            svc_c = cs_mod.CharacterService()
            cid = "char_0123456789ab"
            profile = {
                "character_id": cid,
                "name": "陆沉",
                "face_dna": "28岁冷峻男性，剑眉星目，左眼尾浅疤",
                "signature_clothing": "深棕色高领毛衣配风衣",
                "tts_voice": "zh-CN-YunjianNeural",
            }
            whole_p = svc_c._build_portrait_prompt(profile, "whole", style="realistic", has_reference=False)
            profile_p = svc_c._build_portrait_prompt(profile, "profile", style="realistic", has_reference=True)
            whole_neg = svc_c._build_portrait_negative_prompt(profile, "whole", style="realistic")
            profile_neg = svc_c._build_portrait_negative_prompt(profile, "profile", style="realistic")
            self.assertTrue(whole_p.startswith("角色定妆照（广角全身"))
            self.assertNotIn("85mm", whole_p)
            self.assertNotIn("毛孔", whole_p)
            self.assertNotIn("严禁", whole_p)
            self.assertIn("same person", profile_p)
            self.assertIn("大头照", whole_neg)
            self.assertIn("正脸", profile_neg)

            tmp_backend = tmp_dir / "backend"
            front_rel = f"backend/assets/characters/{cid}/v1/anchor.png"
            front_file = tmp_backend / "assets" / "characters" / cid / "v1" / "anchor.png"
            front_file.parent.mkdir(parents=True, exist_ok=True)
            front_file.write_bytes(png_raw)
            out_img = tmp_backend / "assets" / "character_portraits" / "gen.png"
            out_img.parent.mkdir(parents=True, exist_ok=True)
            out_img.write_bytes(png_raw)

            char_doc = {
                "_id": cid,
                "character_id": cid,
                "revision": 1,
                "archived": False,
                "versions": [{
                    **profile,
                    "revision": 1,
                    "confirmed": False,
                    "archived": False,
                    "rate": "+0%",
                    "pitch": "+0Hz",
                    "reference_path": front_rel,
                    "reference_images": {"front": front_rel},
                }],
            }

            class CharMemDB:
                async def find_one(self, coll, query):
                    return json.loads(json.dumps(char_doc))

                async def replace_one(self, coll, query, doc, upsert=False):
                    char_doc.clear()
                    char_doc.update(doc)
                    return 1

            gen_calls = []

            class RefCapableMock:
                model = "Qwen/Qwen-Image-Edit-2509"
                supports_reference_images = True
                max_reference_images = 3

                def generate_image(self, task_id, scene_id, prompt, negative_prompt=None, ratio="9:16", reference_images=None):
                    gen_calls.append({"prompt": prompt, "negative_prompt": negative_prompt, "refs": reference_images})
                    return {"status": "success", "asset_path": "backend/assets/character_portraits/gen.png"}

            class NonRefMock:
                model = "Kwai-Kolors/Kolors"
                supports_reference_images = False
                max_reference_images = 0

                def generate_image(self, task_id, scene_id, prompt, negative_prompt=None, ratio="9:16", reference_images=None):
                    gen_calls.append({"prompt": prompt, "negative_prompt": negative_prompt, "refs": reference_images})
                    return {"status": "success", "asset_path": "backend/assets/character_portraits/gen.png"}

            with patch.object(cs_mod, "mongodb", CharMemDB()), patch.object(cs_mod, "BACKEND_DIR", tmp_backend):
                with patch("services.provider_factory.get_image_service", return_value=RefCapableMock()):
                    res_ref = asyncio.run(svc_c._generate_single_view(cid, "profile", style="realistic"))
                self.assertNotIn("consistency_warning", res_ref)
                self.assertEqual(gen_calls[-1]["refs"], [front_rel])
                self.assertIn("正脸", gen_calls[-1]["negative_prompt"])
                self.assertIn("same person", gen_calls[-1]["prompt"])

                with patch("services.provider_factory.get_image_service", return_value=NonRefMock()):
                    res_warn = asyncio.run(svc_c._generate_single_view(cid, "whole", style="realistic"))
                self.assertIn("consistency_warning", res_warn)
                self.assertIn("Kwai-Kolors/Kolors", res_warn["consistency_warning"])
                self.assertIsNone(gen_calls[-1]["refs"])


if __name__ == "__main__":
    unittest.main()
