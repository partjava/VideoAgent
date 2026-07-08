import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.comfyui.workflow_mapper import apply_mapping, find_output_files
from services.comfyui.workflow_store import WorkflowStore


class ComfyUIWorkflowMapperTest(unittest.TestCase):
    def test_apply_mapping_writes_nested_node_values_without_mutating_source(self):
        workflow = {
            "1": {"inputs": {"text": "old prompt"}},
            "2": {"inputs": {"seed": 1}},
        }
        mapping = {
            "positive_prompt": {"node": "1", "path": "inputs.text"},
            "seed": {"node": "2", "path": "inputs.seed"},
        }

        patched = apply_mapping(workflow, mapping, {"positive_prompt": "new prompt", "seed": 42})

        self.assertEqual(patched["1"]["inputs"]["text"], "new prompt")
        self.assertEqual(patched["2"]["inputs"]["seed"], 42)
        self.assertEqual(workflow["1"]["inputs"]["text"], "old prompt")

    def test_apply_mapping_rejects_missing_nodes_and_paths(self):
        workflow = {"1": {"inputs": {"text": "old prompt"}}}

        with self.assertRaisesRegex(ValueError, "Node not found"):
            apply_mapping(
                workflow,
                {"positive_prompt": {"node": "missing", "path": "inputs.text"}},
                {"positive_prompt": "new prompt"},
            )

        with self.assertRaisesRegex(ValueError, "Invalid mapping"):
            apply_mapping(
                workflow,
                {"positive_prompt": {"node": "1"}},
                {"positive_prompt": "new prompt"},
            )

    def test_find_output_files_reads_comfyui_history_outputs(self):
        history = {
            "abc": {
                "outputs": {
                    "9": {
                        "images": [{"filename": "scene.png", "subfolder": "", "type": "output"}],
                        "videos": [{"filename": "scene.mp4", "subfolder": "clips", "type": "output"}],
                    }
                }
            }
        }

        images = find_output_files(history, "images")
        videos = find_output_files(history, "videos")

        self.assertEqual(images[0]["filename"], "scene.png")
        self.assertEqual(videos[0]["filename"], "scene.mp4")
        self.assertEqual(videos[0]["subfolder"], "clips")


class ComfyUIWorkflowStoreTest(unittest.TestCase):
    def test_store_round_trips_workflows_and_mappings(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))
            workflow = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "prompt"}}}
            mapping = {"positive_prompt": {"node": "1", "path": "inputs.text"}}

            store.save_workflow("image", workflow, mapping)
            loaded = store.load_workflow("image")

            self.assertEqual(loaded["workflow"], workflow)
            self.assertEqual(loaded["mapping"], mapping)

    def test_store_rejects_invalid_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = WorkflowStore(Path(tmp))

            with self.assertRaisesRegex(ValueError, "Unsupported workflow kind"):
                store.save_workflow("audio", {}, {})


if __name__ == "__main__":
    unittest.main()
