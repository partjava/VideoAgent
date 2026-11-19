"""模型配置与生成额度上限的 MongoDB 持久化同步与启动回填。

本地 `.env.models.local` / `.env.limits.local` 作为同步运行期缓存（供同步服务函数实时读取），
同时与 MongoDB `settings` 集合双向同步，避免配置仅存于单文件或在无文件环境下丢失。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from core.database import mongodb
from services import model_config

SETTINGS_COLLECTION = "settings"
MODEL_PROFILES_DOC_ID = "model_profiles"
GENERATION_LIMITS_DOC_ID = "generation_limits"


def _doc_timestamp(doc: dict[str, Any] | None) -> float:
    if not isinstance(doc, dict):
        return 0.0
    raw = doc.get("updated_at")
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
        return dt.timestamp()
    if isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _file_mtime(path) -> float:
    try:
        return path.stat().st_mtime if path.exists() else 0.0
    except OSError:
        return 0.0


async def persist_models_to_db(models: dict[str, Any]) -> bool:
    if getattr(mongodb, "database", None) is None:
        return False
    try:
        await mongodb.replace_one(
            SETTINGS_COLLECTION,
            {"_id": MODEL_PROFILES_DOC_ID},
            {
                "_id": MODEL_PROFILES_DOC_ID,
                "models": models,
                "updated_at": datetime.now(UTC),
            },
            upsert=True,
        )
        return True
    except Exception as exc:
        print(f"[SettingsPersistence] persist_models_to_db skipped: {exc}", flush=True)
        return False


async def persist_limits_to_db(limits: dict[str, int] | None) -> bool:
    if getattr(mongodb, "database", None) is None:
        return False
    try:
        if not limits:
            await mongodb.delete_one(SETTINGS_COLLECTION, {"_id": GENERATION_LIMITS_DOC_ID})
            return True
        await mongodb.replace_one(
            SETTINGS_COLLECTION,
            {"_id": GENERATION_LIMITS_DOC_ID},
            {
                "_id": GENERATION_LIMITS_DOC_ID,
                "limits": limits,
                "updated_at": datetime.now(UTC),
            },
            upsert=True,
        )
        return True
    except Exception as exc:
        print(f"[SettingsPersistence] persist_limits_to_db skipped: {exc}", flush=True)
        return False


async def hydrate_models_from_db() -> bool:
    if getattr(mongodb, "database", None) is None:
        return False
    try:
        doc = await mongodb.find_one(SETTINGS_COLLECTION, {"_id": MODEL_PROFILES_DOC_ID})
        db_models = doc.get("models") if isinstance(doc, dict) else None
        local_models = model_config.saved_models()
        if isinstance(db_models, dict) and db_models:
            db_ts = _doc_timestamp(doc)
            file_ts = _file_mtime(model_config.PATH)
            if not local_models or (db_ts > 0 and db_ts > file_ts + 0.5):
                model_config.save_models(db_models)
                return True
            if local_models != db_models and file_ts > db_ts + 0.5:
                await persist_models_to_db(local_models)
            return False
        if local_models:
            await persist_models_to_db(local_models)
        return False
    except Exception as exc:
        print(f"[SettingsPersistence] hydrate_models_from_db skipped: {exc}", flush=True)
        return False


async def hydrate_limits_from_db() -> bool:
    if getattr(mongodb, "database", None) is None:
        return False
    try:
        doc = await mongodb.find_one(SETTINGS_COLLECTION, {"_id": GENERATION_LIMITS_DOC_ID})
        db_limits = doc.get("limits") if isinstance(doc, dict) else None
        local_limits = model_config.saved_limits()
        if isinstance(db_limits, dict) and db_limits:
            db_ts = _doc_timestamp(doc)
            file_ts = _file_mtime(model_config.LIMITS_PATH)
            if not local_limits or (db_ts > 0 and db_ts > file_ts + 0.5):
                model_config.save_limits(db_limits)
                return True
            if local_limits != db_limits and file_ts > db_ts + 0.5:
                await persist_limits_to_db(local_limits)
            return False
        if local_limits:
            await persist_limits_to_db(local_limits)
        return False
    except Exception as exc:
        print(f"[SettingsPersistence] hydrate_limits_from_db skipped: {exc}", flush=True)
        return False


async def hydrate_settings_from_db() -> dict[str, bool]:
    models_hydrated = await hydrate_models_from_db()
    limits_hydrated = await hydrate_limits_from_db()
    return {"models": models_hydrated, "limits": limits_hydrated}
