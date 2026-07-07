import asyncio
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.database import MongoDatabase


class FakeReplaceResult:
    modified_count = 1
    upserted_id = None


class FakeCollection:
    def __init__(self):
        self.calls = []

    async def replace_one(self, query, document, upsert=False):
        self.calls.append((query, document, upsert))
        return FakeReplaceResult()


class ReplaceOneUpsertTest(unittest.TestCase):
    def test_replace_one_passes_upsert_to_collection(self):
        async def run_test():
            db = MongoDatabase()
            collection = FakeCollection()
            db.get_collection = lambda _name: collection

            result = await db.replace_one(
                "assets",
                {"_id": "asset_task_123_video"},
                {"_id": "asset_task_123_video", "status": "success"},
                upsert=True,
            )

            self.assertEqual(result, 1)
            self.assertEqual(
                collection.calls,
                [
                    (
                        {"_id": "asset_task_123_video"},
                        {"_id": "asset_task_123_video", "status": "success"},
                        True,
                    )
                ],
            )

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
