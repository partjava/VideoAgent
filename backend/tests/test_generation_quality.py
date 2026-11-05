import unittest
from unittest.mock import patch
from services.generation_contract import (
    build_timeline, continuity_input, render_srt, voice_window, validate_paired_scenes, validate_motion_beats, validate_scene_durations, voice_speed,
)


class GenerationContractTest(unittest.TestCase):
    def test_short_voice_does_not_move_next_scene(self):
        scenes = [{"scene_id": "a", "duration": 5, "audio_duration": 3},
                  {"scene_id": "b", "duration": 5, "audio_duration": 2}]
        rows = build_timeline(scenes)
        self.assertEqual(rows[1]["start"], 5)
        self.assertEqual(rows[1]["end"], 10)

    def test_motion_must_cover_entire_scene(self):
        with self.assertRaisesRegex(ValueError, "cover"):
            validate_motion_beats([{"start": 0, "end": 1, "action": "伸手"}], 5)

    def test_motion_rejects_gaps_and_overlap(self):
        for start in (0.5, 2):
            with self.assertRaises(ValueError):
                validate_motion_beats([{"start": 0, "end": 1, "action": "伸手"},
                                       {"start": start, "end": 3, "action": "拿起"}], 3)

    def test_motion_accepts_continuous_action(self):
        validate_motion_beats([{"start": 0, "end": 2, "action": "持续伸出右手"},
                               {"start": 2, "end": 5, "action": "捏住信封并抬起"}], 5)

    def test_detailed_motion_requires_visible_start_and_end_state(self):
        beats = [{"start": 0, "end": 5, "subject": "李白", "action": "抬起信封", "camera": "固定"}]
        with self.assertRaisesRegex(ValueError, "start_state"):
            validate_motion_beats(beats, 5, detailed=True)

    def test_per_second_motion_requires_exact_integer_boundaries(self):
        beats = [{"start": i, "end": i + 1, "action": "continue reaching"} for i in range(5)]
        validate_motion_beats(beats, 5, per_second=True)
        beats[1]["end"] = beats[2]["start"] = 2.2
        with self.assertRaisesRegex(ValueError, "one-second intervals"):
            validate_motion_beats(beats, 5, per_second=True)
        with self.assertRaisesRegex(ValueError, "one entry per second"):
            validate_motion_beats([{"start": 0, "end": 5, "action": "reach"}], 5, per_second=True)

    def test_voiceover_speed_is_bounded(self):
        self.assertEqual(voice_speed("a", 3, 5), 1)
        self.assertAlmostEqual(voice_speed("a", 5.5, 5), 1.1)
        self.assertAlmostEqual(voice_speed("a", 6.1, 5), 1.22)
        with self.assertRaisesRegex(ValueError, "Voice too long"):
            voice_speed("a", 7, 5)

    def test_continuation_inherits_actual_previous_frame(self):
        self.assertEqual(continuity_input({"transition_type": "continue_action"}, "tail.png", "own.png"), "tail.png")
        self.assertEqual(continuity_input({"transition_type": "cut"}, "tail.png", "own.png"), "own.png")
        with self.assertRaises(ValueError):
            continuity_input({"transition_type": "continue_action"}, None, "own.png")

    def test_paired_mode_requires_two_five_second_dynamic_clips(self):
        first = {"duration": 5, "transition_type": "cut", "need_dynamic_video": True}
        second = {**first, "transition_type": "continue_action"}
        validate_paired_scenes([first, second, first, second], 20)
        for scenes in ([first, first], [first, {**second, "duration": 6}],
                       [first, {**second, "need_dynamic_video": False}]):
            with self.assertRaises(ValueError):
                validate_paired_scenes(scenes, 10)

    def test_voice_offset_applies_to_srt_and_remaining_window(self):
        scenes = [{"scene_id": "a", "duration": 5},
                  {"scene_id": "b", "duration": 5, "voice_start": 3, "audio_duration": 1.2,
                   "voiceover": "现在说"}]
        self.assertEqual(voice_window(scenes[1]), (3, 2))
        self.assertIn("00:00:08,000 --> 00:00:09,200", render_srt(scenes))
        with self.assertRaises(ValueError):
            voice_window({**scenes[1], "voice_start": 5})

    def test_wan_whole_mode_validates_a_single_dynamic_clip(self):
        clip = {"duration": 30, "need_dynamic_video": True}
        validate_scene_durations([clip], 30, whole=True)
        with self.assertRaises(ValueError):
            validate_scene_durations([clip], 30)
        with self.assertRaises(ValueError):
            validate_scene_durations([{**clip, "duration": 15}] * 2, 30, whole=True)

    def test_visual_audio_alignment_preserves_manual_voice_and_scene_bounds(self):
        from services.video_vision import align_scene_audio

        scene = {"scene_id": "b", "video_path": "fake.mp4", "duration": 5, "voice_start": 1,
                 "voice_cue": "抬眼", "audio_duration": 1,
                 "sound_cues": [{"name": "impact", "start": 1}]}
        with patch("services.video_vision._sample", return_value=[]), patch(
            "services.video_vision._review", return_value={
                "voice_start": 3.5, "sound_cues": [{"index": 0, "start": 2.5}, {"index": 0, "start": 7}],
            }
        ):
            aligned = align_scene_audio(scene)
            self.assertEqual(aligned["voice_start"], 3.5)
            self.assertEqual(aligned["sound_cues"], [{"name": "impact", "start": 2.5}])
            self.assertNotIn("voice_start", align_scene_audio({**scene, "voice_start_locked": True}))
            self.assertNotIn("voice_start", align_scene_audio({**scene, "audio_duration": 4}))

    def test_total_and_per_scene_duration_are_enforced(self):
        with self.assertRaises(ValueError):
            validate_scene_durations([{"duration": 12}, {"duration": 12}], 30)
        with self.assertRaises(ValueError):
            validate_scene_durations([{"duration": 15}, {"duration": 15}], 30)
        validate_scene_durations([{"duration": 5}] * 6, 30)


if __name__ == "__main__":
    unittest.main()
