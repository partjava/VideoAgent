import unittest
from unittest.mock import AsyncMock, patch

from services.audio_timing import retime_audio
from services.generation_contract import render_srt


class AudioTimingTests(unittest.TestCase):
    def test_legacy_dialogue_duration_survives_repeated_edits(self):
        scene = {"duration": 10, "dialogue_audio": [
            {"start": 1, "end": 3, "speed": 1.2, "text": "第一句"},
            {"start": 4, "end": 5, "speed": 1, "text": "第二句"},
        ]}
        values = retime_audio(scene, [{"start": 0, "speed": 1}, {"start": 3, "speed": 1}])
        self.assertAlmostEqual(values["dialogue_audio"][0]["end"], 2.4)
        edited = {**scene, **values}
        again = retime_audio(edited, [{"start": 1, "speed": 1.2}, {"start": 4, "speed": 1}])
        self.assertAlmostEqual(again["dialogue_audio"][0]["end"], 3)
        self.assertEqual(scene["dialogue_audio"][0]["start"], 1)

    def test_narration_subtitles_use_selected_speed(self):
        scene = {"scene_id": "a", "duration": 10, "voiceover": "旁白",
                 "audio_duration": 4, "voice_start": 0}
        values = retime_audio(scene, [{"start": 2, "speed": 0.9}])
        subtitles = render_srt([{**scene, **values}])
        self.assertIn("00:00:02,000 --> 00:00:06,444", subtitles)

    def test_overflow_and_overlap_are_rejected(self):
        with self.assertRaises(ValueError):
            retime_audio({"scene_id": "a", "duration": 10, "audio_duration": 4},
                         [{"start": 8, "speed": 1.25}])
        with self.assertRaises(ValueError):
            retime_audio({"duration": 10, "dialogue_audio": [
                {"start": 0, "end": 3, "speed": 1},
                {"start": 4, "end": 6, "speed": 1},
            ]}, [{"start": 0, "speed": 1}, {"start": 2, "speed": 1}])


class AudioRemixRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_remix_reuses_assets_and_runs_only_composition_stages(self):
        from routes.video_routes import AudioTimingRequest, apply_audio_timing
        scene = {"_id": "scene", "scene_id": "a", "duration": 10,
                 "audio_path": "original.mp3", "audio_duration": 2}
        with patch("routes.video_routes.task_service.get_task", AsyncMock(return_value={"status": "success"})), \
             patch("routes.video_routes.mongodb.find_one", AsyncMock(return_value=scene)), \
             patch("routes.video_routes.mongodb.find_many", AsyncMock(return_value=[scene])), \
             patch("routes.video_routes.mongodb.update_one", AsyncMock(return_value=1)), \
             patch("routes.video_routes.resolve_media") as media, \
             patch("routes.video_routes.SubtitleAgent.run", AsyncMock()) as subtitle, \
             patch("routes.video_routes.EditorAgent.run", AsyncMock()) as editor, \
             patch("routes.video_routes.QualityAgent.run", AsyncMock()), \
             patch("routes.video_routes.ExportAgent.run", AsyncMock()), \
             patch("routes.video_routes.tts_service.generate_voice", AsyncMock()) as tts:
            media.return_value.is_file.return_value = True
            result = await apply_audio_timing("task", "a", AudioTimingRequest(lines=[{"start": 1, "speed": 1}]))
            self.assertEqual(result.status, "success")
            subtitle.assert_awaited_once_with("task")
            editor.assert_awaited_once_with("task")
            tts.assert_not_called()
