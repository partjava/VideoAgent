import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import AsyncMock, patch
import asyncio

from services.video.comfyui_video_service import prepare_video_workflow
from services.media_quality import inspect_video


class OfflineMediaTest(unittest.TestCase):
    def test_pipeline_waits_for_voice_and_images_before_video(self):
        from contextlib import ExitStack
        from agents import pipeline

        completed = set()

        def node(name):
            async def run(state):
                if name == "voice":
                    await asyncio.sleep(0.01)
                if name == "video":
                    self.assertTrue({"voice", "images"}.issubset(completed))
                completed.add(name)
                return {"results": {name: {"status": "success"}}}
            return run

        names = ("task_planner", "script", "storyboard", "dialogue", "prompts", "images",
                 "voice", "video", "subtitle", "editor", "quality", "export")
        with ExitStack() as patches:
            for name in names:
                patches.enter_context(patch.object(pipeline, f"{name}_node", node(name)))
            result = asyncio.run(pipeline.run_pipeline("offline"))
        self.assertEqual(result["status"], "success")
        self.assertEqual(completed, set(names))

    def test_video_agent_uses_decoded_tail_for_continuation(self):
        from contextlib import ExitStack
        from uuid import uuid4
        from moviepy.editor import ColorClip
        from PIL import Image
        from agents.video_agent import VideoAgent
        from services.media_quality import resolve_media

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            image = folder / "first.png"
            Image.new("RGB", (64, 112), (20, 20, 20)).save(image)
            clip_path = folder / "clip.mp4"
            clip = ColorClip((64, 112), color=(200, 40, 40)).set_duration(4)
            clip.write_videofile(str(clip_path), fps=8, codec="libx264", logger=None)
            clip.close()
            task_id = f"offline_{uuid4().hex}"
            scenes = [{"scene_id": sid, "scene_index": i, "duration": 4,
                       "need_dynamic_video": True, "transition_type": transition,
                       "motion_beats": [{"start": 0, "end": 4, "subject": "person", "action": "raise hand",
                                         "start_state": "hand lowered", "end_state": "hand raised", "camera": "fixed"}]}
                      for i, (sid, transition) in enumerate((("a", "cut"), ("b", "continue_action")), 1)]
            db = Mock()
            db.find_many = AsyncMock(side_effect=lambda name, *a, **k: scenes if name == "scenes" else
                                     [{"scene_id": "a", "asset_type": "image", "path": str(image)}])
            db.find_one = AsyncMock(return_value={"ratio": "9:16", "duration": 8})
            db.replace_one = AsyncMock()
            db.update_one = AsyncMock()
            service = Mock()
            received = []

            def generate(**request):
                with Image.open(resolve_media(request["image_path"])) as first:
                    received.append((request["scene_id"], first.getpixel((30, 50))))
                return {"status": "success", "asset_path": str(clip_path)}

            def offline_resolve(path):
                return folder / Path(path).name if str(path).startswith("backend/assets/") else resolve_media(path)

            def load_offline_first_frame(**request):
                request["image_path"] = offline_resolve(request["image_path"])
                return generate(**request)

            service.generate_video.side_effect = load_offline_first_frame
            with ExitStack() as patches:
                for name, value in {"mongodb": db, "get_video_service": lambda: service,
                                    "resolve_media": offline_resolve, "mark_agent_start": AsyncMock(),
                                    "mark_agent_success": AsyncMock(), "mark_agent_failed": AsyncMock()}.items():
                    patches.enter_context(patch(f"agents.video_agent.{name}", value))
                asyncio.run(VideoAgent().run(task_id))

            self.assertEqual(received[0], ("a", (20, 20, 20)))
            self.assertEqual(received[1][0], "b")
            self.assertGreater(received[1][1][0], 150)
            self.assertEqual(db.replace_one.await_count, 2)

    def test_confirmed_video_failure_uses_still_and_keeps_other_clip(self):
        from contextlib import ExitStack
        from agents.video_agent import VideoAgent
        from services.video.base import VideoRenderRejected

        with tempfile.TemporaryDirectory() as tmp:
            first_frame = Path(tmp) / "first.png"
            first_frame.write_bytes(b"mock first frame")
            clip = Path(tmp) / "finished.mp4"
            clip.touch()
            scenes = [{
                "scene_id": sid, "scene_index": index, "duration": 4,
                "image_path": str(first_frame), "need_dynamic_video": True,
                "motion_beats": [{"start": 0, "end": 4, "subject": "person", "action": "raise hand",
                                  "start_state": "lowered", "end_state": "raised", "camera": "fixed"}],
            } for index, sid in enumerate(("a", "b"), 1)]
            db = Mock()
            db.find_many = AsyncMock(side_effect=lambda name, *a, **k: scenes if name == "scenes" else [])
            db.find_one = AsyncMock(return_value={"ratio": "9:16", "duration": 8})
            db.replace_one = AsyncMock()
            db.update_one = AsyncMock()
            service = Mock()
            service.generate_video.side_effect = [
                {"status": "success", "asset_path": str(clip)},
                VideoRenderRejected("render failed"),
            ]
            with ExitStack() as patches:
                for name, value in {"mongodb": db, "get_video_service": lambda: service,
                                    "mark_agent_start": AsyncMock(), "mark_agent_success": AsyncMock(),
                                    "mark_agent_failed": AsyncMock()}.items():
                    patches.enter_context(patch(f"agents.video_agent.{name}", value))
                assets = asyncio.run(VideoAgent().run("offline"))
            self.assertEqual([asset["scene_id"] for asset in assets], ["a"])
            self.assertEqual(service.generate_video.call_count, 2)
            fallback = db.update_one.await_args.args[2]
            self.assertEqual(fallback["status"], "static_fallback")
            self.assertEqual(fallback["image_path"], str(first_frame))

    def test_editor_places_second_voice_at_scene_start_and_quality_accepts(self):
        import numpy as np
        from moviepy.editor import AudioClip, AudioFileClip
        from PIL import Image
        from agents.editor_agent import EditorAgent
        from agents.subtitle_agent import SubtitleAgent
        from agents.quality_agent import QualityAgent
        backend = Path(__file__).resolve().parents[1]
        (backend / "outputs").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="offline_", dir=backend / "outputs") as tmp:
            folder = Path(tmp)
            task_id = folder.name
            image = folder / "frame.png"
            Image.new("RGB", (144, 256), (30, 80, 120)).save(image)
            audio = folder / "tone.wav"
            tone = AudioClip(lambda t: 0.2 * np.sin(2 * np.pi * 440 * t), duration=0.5, fps=44100)
            tone.write_audiofile(str(audio), fps=44100, logger=None)
            tone.close()
            scenes = [{"scene_id": sid, "scene_index": i, "duration": 4, "voiceover": "test",
                       "image_path": str(image), "audio_path": str(audio), "audio_duration": 0.5,
                       "need_dynamic_video": False}
                      for i, sid in enumerate(("a", "b"), 1)]
            assets = [{"scene_id": sid, "asset_type": kind, "path": str(path), "source": "local"}
                      for sid in ("a", "b") for kind, path in (("image", image), ("audio", audio))]
            db = Mock()
            db.find_many = AsyncMock(side_effect=lambda collection, *args, **kwargs: scenes if collection == "scenes" else assets)
            db.find_one = AsyncMock(return_value={"ratio": "9:16", "duration": 8})
            db.replace_one = AsyncMock(return_value=1)
            with patch("core.database.mongodb", db), \
                 patch("agents.editor_agent.mongodb", db), patch("agents.subtitle_agent.mongodb", db), \
                 patch("agents.editor_agent.mark_agent_start", AsyncMock()), \
                 patch("agents.editor_agent.mark_agent_success", AsyncMock()), \
                 patch("agents.editor_agent.mark_agent_failed", AsyncMock()), \
                 patch("agents.subtitle_agent.mark_agent_start", AsyncMock()), \
                 patch("agents.subtitle_agent.mark_agent_success", AsyncMock()), \
                 patch("agents.subtitle_agent.mark_agent_failed", AsyncMock()), \
                 patch("agents.quality_agent.mark_agent_start", AsyncMock()), \
                 patch("agents.quality_agent.mark_agent_success", AsyncMock()), \
                 patch("agents.quality_agent.mark_agent_failed", AsyncMock()):
                asyncio.run(SubtitleAgent().run(task_id))
                asyncio.run(EditorAgent().run(task_id))
                result = asyncio.run(QualityAgent().run(task_id))
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["semantic_review"], "not_run")
            with AudioFileClip(str(folder / "final.mp4")) as rendered:
                self.assertGreater(np.max(np.abs(rendered.get_frame(np.linspace(4.1, 4.3, 1000)))), 0.02)
                self.assertLess(np.max(np.abs(rendered.get_frame(np.linspace(1.1, 1.3, 1000)))), 0.01)
            self.assertIn("00:00:04,000 --> 00:00:04,500", (folder / "subtitle.srt").read_text())

    def test_wan_uses_aspect_of_uploaded_frame_and_sends_duration(self):
        from types import SimpleNamespace
        from PIL import Image
        from services.video.wan_video_service import WanVideoService

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "square.png"
            Image.new("RGB", (200, 200)).save(path)
            service = WanVideoService(model="wan2.7-i2v", storage_root=tmp)

            def upload(frame):
                with Image.open(frame) as img:
                    self.assertEqual(img.size, (1280, 720))
                return "https://example.invalid/first.png"

            client = Mock()
            client.__enter__ = Mock(return_value=client)
            client.__exit__ = Mock(return_value=False)
            client.post.side_effect = RuntimeError("stop after capturing request")
            with patch.object(service, "_upload_file", side_effect=upload), \
                 patch("services.video.wan_video_service.settings", SimpleNamespace(
                     enable_paid_api=True, dashscope_api_key="fake", wan_video_model="wan2.7-i2v")), \
                 patch("services.video.wan_video_service.httpx.Client", return_value=client):
                with self.assertRaisesRegex(RuntimeError, "stop after capturing request"):
                    service.generate_video("offline", "a", str(path), duration=7, ratio="16:9")
            parameters = client.post.call_args.kwargs["json"]["parameters"]
            self.assertEqual(parameters["duration"], 7)
            self.assertNotIn("ratio", parameters)

    def test_wan_flash_submits_silent_720p_with_inline_first_frame(self):
        from base64 import b64decode
        from io import BytesIO
        from types import SimpleNamespace
        from PIL import Image
        from services.video.wan_video_service import WanVideoService

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "square.png"
            Image.new("RGB", (200, 200)).save(path)
            service = WanVideoService(model="wan2.6-i2v-flash", storage_root=tmp)
            client = Mock()
            client.__enter__ = Mock(return_value=client)
            client.__exit__ = Mock(return_value=False)
            client.post.side_effect = RuntimeError("stop after capturing request")
            with patch.object(service, "_upload_file") as upload, \
                 patch("services.video.wan_video_service.settings", SimpleNamespace(
                     enable_paid_api=True, dashscope_api_key="fake", wan_video_model="wan2.6-i2v-flash")), \
                 patch("services.video.wan_video_service.httpx.Client", return_value=client):
                with self.assertRaisesRegex(RuntimeError, "stop after capturing request"):
                    service.generate_video("offline", "a", str(path), duration=5, ratio="9:16")
            upload.assert_not_called()
            payload = client.post.call_args.kwargs["json"]
            self.assertEqual(payload["parameters"], {
                "resolution": "720P", "duration": 5, "audio": False, "shot_type": "single",
            })
            self.assertNotIn("media", payload["input"])
            prefix, encoded = payload["input"]["img_url"].split(",", 1)
            self.assertEqual(prefix, "data:image/jpeg;base64")
            with Image.open(BytesIO(b64decode(encoded))) as image:
                self.assertEqual(image.size, (720, 1280))

    def test_wan_resumes_original_accepted_job_after_interrupted_poll(self):
        from types import SimpleNamespace
        from PIL import Image
        from services.video.wan_video_service import WanVideoService

        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "first.png"
            Image.new("RGB", (240, 320)).save(frame)
            service = WanVideoService(model="wan3.0-video", storage_root=tmp)
            client = Mock()
            client.__enter__ = Mock(return_value=client)
            client.__exit__ = Mock(return_value=False)
            client.post.return_value = Mock(status_code=200)
            client.post.return_value.json.return_value = {"output": {"task_id": "accepted-once"}}
            client.get.side_effect = RuntimeError("poll interrupted")
            with patch("services.video.wan_video_service.settings", SimpleNamespace(
                    enable_paid_api=True, dashscope_api_key="fake")), \
                 patch("services.video.wan_video_service.httpx.Client", return_value=client), \
                 patch("services.video.wan_video_service.time.sleep"):
                with self.assertRaisesRegex(RuntimeError, "poll interrupted"):
                    service.generate_video("offline", "a", str(frame), duration=30)
                ready = Mock(status_code=200)
                ready.json.return_value = {"output": {"task_status": "SUCCEEDED",
                                                       "video_url": "https://example.invalid/clip.mp4"}}
                download = Mock(content=b"offline clip")
                client.get.side_effect = [ready, download]
                result = service.generate_video("offline", "a", str(frame), duration=30)
            self.assertEqual(result["provider_task_id"], "accepted-once")
            self.assertEqual(client.post.call_count, 1)
            params = client.post.call_args.kwargs["json"]["parameters"]
            self.assertEqual(params["duration"], 30)
            self.assertFalse(params["audio"])

    def test_comfyui_upload_and_frames_and_ratio(self):
        root = Path(__file__).resolve().parents[1] / "data" / "comfyui"
        data = {"workflow": json.loads((root / "video_workflow.json").read_text()),
                "mapping": json.loads((root / "video_mapping.json").read_text())}
        client = Mock()
        client.upload_image.return_value = "uploaded.png"
        workflow = prepare_video_workflow(data, client, Path("first.png"), "拿起信封", 5, "9:16")
        self.assertEqual(workflow["60"]["inputs"]["image"], "uploaded.png")
        self.assertEqual(workflow["55"]["inputs"]["start_image"], ["60", 0])
        self.assertEqual(workflow["55"]["inputs"]["length"], 81)
        self.assertLess(workflow["55"]["inputs"]["width"], workflow["55"]["inputs"]["height"])
        client.upload_image.assert_called_once()

    def test_comfyui_missing_mapping_fails_before_upload(self):
        client = Mock()
        with self.assertRaises(ValueError):
            prepare_video_workflow({"workflow": {}, "mapping": {}}, client, Path("x.png"), "x", 5, "9:16")
        client.upload_image.assert_not_called()

    def test_empty_and_wrong_duration_media_rejected(self):
        from moviepy.editor import ColorClip
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "local.mp4"
            path.touch()
            with self.assertRaises(ValueError):
                inspect_video(path)
            clip = ColorClip((64, 112), color=(30, 80, 120)).set_duration(0.5)
            clip.write_videofile(str(path), fps=24, codec="libx264", logger=None)
            clip.close()
            self.assertAlmostEqual(inspect_video(path, 0.5, "9:16")["duration"], 0.5, places=1)
            with self.assertRaisesRegex(ValueError, "duration"):
                inspect_video(path, 5)
            with self.assertRaisesRegex(ValueError, "audio"):
                inspect_video(path, require_audio=True)


if __name__ == "__main__":
    unittest.main()
