from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _history_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "comfyui" / "test_history.json"


def save_test(record: dict[str, Any]) -> None:
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    records = _load_all()
    records.insert(0, record)
    # 最多保留 50 条
    path.write_text(
        json.dumps(records[:50], ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _load_all() -> list[dict[str, Any]]:
    path = _history_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def load_history(limit: int = 50) -> list[dict[str, Any]]:
    return _load_all()[:limit]


def record_test_success(
    test_type: str,
    prompt: str,
    story: str | None,
    asset_path: str,
) -> dict[str, Any]:
    record = {
        "source": "comfyui_test",
        "type": test_type,
        "prompt": prompt[:120],
        "story": story or "",
        "asset_path": asset_path,
        "status": "success",
        "created_at": datetime.now(UTC).isoformat(),
    }
    save_test(record)
    return record
