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


if __name__ == "__main__":
    unittest.main()
