import asyncio
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.dialogue_polish_agent import DialoguePolishAgent


class FakeMongo:
    def __init__(self):
        self.updated = []

    async def find_many(self, collection_name, limit=50, sort_field="created_at", sort_direction=-1):
        if collection_name != "scenes":
            return []
        return [
            {
                "_id": "task_001_scene_001",
                "task_id": "task_001",
                "scene_id": "scene_001",
                "scene_index": 1,
                "duration": 4,
                "voiceover": "快答题！",
                "subtitle": "快答题！",
                "visual_description": "李白站在现代考场中央，监考老师催促他答题。",
                "character_state": "李白惊讶后镇定下来。",
                "scene_continuity": "延续上一镜头的考场环境。",
                "transition_note": "李白低头看向试卷。",
            }
        ]

    async def update_one(self, collection_name, query, values):
        self.updated.append((collection_name, query, values))
        return 1


class FakeLLMService:
    def polish_dialogue(self, scenes, target_duration=None):
        return [
            {
                "scene_id": "scene_001",
                "speaker": "李白",
                "voiceover": "且看我以诗入题，今日也要会一会这场大考。",
                "subtitle": "且看我以诗入题，今日也要会一会这场大考。",
            }
        ]


async def fake_get_required_task(_task_id):
    return {
        "task_id": "task_001",
        "duration": 4,
    }


async def noop_async(*_args, **_kwargs):
    return None


class DialoguePolishAgentTest(unittest.TestCase):
    def test_polishes_short_voiceover_and_saves_speaker_separately(self):
        async def run_test():
            import agents.dialogue_polish_agent as module

            fake_mongo = FakeMongo()
            module.mongodb = fake_mongo
            module.get_required_task = fake_get_required_task
            module.get_llm_service = lambda: FakeLLMService()
            module.mark_agent_start = noop_async
            module.mark_agent_success = noop_async
            module.mark_agent_failed = noop_async

            result = await DialoguePolishAgent().run("task_001")

            self.assertEqual(result[0]["speaker"], "李白")
            self.assertEqual(result[0]["voiceover"], "且看我以诗入题，今日也要会一会这场大考。")
            self.assertEqual(fake_mongo.updated[0][1], {"_id": "task_001_scene_001"})
            self.assertEqual(fake_mongo.updated[0][2]["speaker"], "李白")
            self.assertEqual(fake_mongo.updated[0][2]["status"], "dialogue_done")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
