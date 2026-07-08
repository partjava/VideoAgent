from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.comfyui.config_store import backend_root


VALID_WORKFLOW_KINDS = {"image", "video"}


class WorkflowStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or backend_root() / "data" / "comfyui"

    def _validate_kind(self, kind: str) -> str:
        normalized = str(kind).strip().lower()
        if normalized not in VALID_WORKFLOW_KINDS:
            raise ValueError(f"Unsupported workflow kind: {kind}")
        return normalized

    def _workflow_path(self, kind: str) -> Path:
        return self.root / f"{self._validate_kind(kind)}_workflow.json"

    def _mapping_path(self, kind: str) -> Path:
        return self.root / f"{self._validate_kind(kind)}_mapping.json"

    def save_workflow(
        self,
        kind: str,
        workflow: dict[str, Any],
        mapping: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_kind = self._validate_kind(kind)
        if not isinstance(workflow, dict) or not workflow:
            raise ValueError("Workflow JSON must be a non-empty object")
        if not isinstance(mapping, dict):
            raise ValueError("Workflow mapping must be an object")

        self.root.mkdir(parents=True, exist_ok=True)
        self._workflow_path(normalized_kind).write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._mapping_path(normalized_kind).write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.load_workflow(normalized_kind)

    def load_workflow(self, kind: str) -> dict[str, Any]:
        workflow_path = self._workflow_path(kind)
        mapping_path = self._mapping_path(kind)
        if not workflow_path.exists():
            raise FileNotFoundError(f"ComfyUI {kind} workflow is not configured")
        if not mapping_path.exists():
            raise FileNotFoundError(f"ComfyUI {kind} workflow mapping is not configured")

        return {
            "workflow": json.loads(workflow_path.read_text(encoding="utf-8")),
            "mapping": json.loads(mapping_path.read_text(encoding="utf-8")),
        }

    def load_all(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for kind in sorted(VALID_WORKFLOW_KINDS):
            try:
                result[kind] = self.load_workflow(kind)
            except FileNotFoundError:
                result[kind] = {"workflow": None, "mapping": None}
        return result
