import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agents.voice_agent import synthesize_dialogue


class DialogueTimingTests(unittest.IsolatedAsyncioTestCase):
    async def render(self, durations):
        scene = {
            "scene_id": "scene_01",
            "duration": 10,
            "dialogue_lines": [
                {"speaker": "李白", "text": "又是孤身一别。", "start": 0.3},
                {"speaker": "岸上众人", "text": "太白——！", "start": 2.9},
                {"speaker": "李白", "text": "这嗓子……汪伦？！", "start": 3.8},
            ],
        }
        with patch("agents.voice_agent.tts_service.generate_voice", AsyncMock(side_effect=durations)):
            return await synthesize_dialogue(scene, Path("."), "test", "scene_01")

    async def test_short_slot_can_borrow_time_from_later_in_scene(self):
        lines = await self.render([2, 1.872, 2])
        self.assertEqual([line["speed"] for line in lines], [1, 1, 1])
        self.assertAlmostEqual(lines[2]["start"], 4.772)
        self.assertLessEqual(lines[2]["end"], 10)

    async def test_keeps_original_timing_when_it_fits(self):
        lines = await self.render([2, 1.05, 2])
        self.assertEqual([line["start"] for line in lines], [0.3, 2.9, 3.8])

    async def test_rejects_dialogue_that_cannot_fit_in_the_scene(self):
        with self.assertRaisesRegex(ValueError, "scene_01"):
            await self.render([4.5, 4.5, 4.5])
