from __future__ import annotations

import copy
from typing import Any


def _split_path(path: str) -> list[str]:
    parts = [part for part in str(path or "").split(".") if part]
    if not parts:
        raise ValueError("Invalid mapping path")
    return parts


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:
    parts = _split_path(path)
    cursor: Any = target
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            raise ValueError(f"Field path not found: {path}")
        cursor = cursor[part]
    if not isinstance(cursor, dict):
        raise ValueError(f"Field path not writable: {path}")
    cursor[parts[-1]] = value


def apply_mapping(
    workflow: dict[str, Any],
    mapping: dict[str, Any],
    values: dict[str, Any],
) -> dict[str, Any]:
    patched = copy.deepcopy(workflow)

    for value_key, value in values.items():
        if value is None or value_key not in mapping:
            continue

        field_mapping = mapping[value_key]
        if not isinstance(field_mapping, dict):
            raise ValueError(f"Invalid mapping for {value_key}")
        node_id = str(field_mapping.get("node") or "")
        path = str(field_mapping.get("path") or "")
        if not node_id or not path:
            raise ValueError(f"Invalid mapping for {value_key}")
        if node_id not in patched:
            raise ValueError(f"Node not found in workflow: {node_id}")

        _set_path(patched[node_id], path, value)

    return patched


def find_output_files(history: dict[str, Any], output_key: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for prompt_result in history.values():
        if not isinstance(prompt_result, dict):
            continue
        outputs = prompt_result.get("outputs", {})
        if not isinstance(outputs, dict):
            continue
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            for item in node_output.get(output_key, []) or []:
                if isinstance(item, dict) and item.get("filename"):
                    files.append(item)
    return files
