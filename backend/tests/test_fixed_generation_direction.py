import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.llm.deepseek_service import _style_template


class FixedGenerationDirectionTest(unittest.TestCase):
    def test_uses_history_story_direction(self):
        rules = _style_template(None)

        self.assertIn("历史", rules["visual"])
        self.assertIn("叙事", rules["voice"])
        self.assertIn("历史故事", rules["subtitle"])


if __name__ == "__main__":
    unittest.main()
