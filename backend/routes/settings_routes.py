"""API Key 管理路由 — 前端页面配置，存入 MongoDB settings 集合"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.database import mongodb
from models.response_model import ApiResponse

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTINGS_COLLECTION = "settings"
SETTINGS_DOC_ID = "api_keys"

# 允许前端配置的 key 列表
ALLOWED_KEYS = {
    "deepseek_api_key": "DeepSeek API Key",
    "dashscope_api_key": "DashScope API Key（通义千问 + Wan）",
    "vidu_api_token": "Vidu API Token",
}


@router.get("/keys", response_model=ApiResponse)
async def get_api_keys() -> JSONResponse:
    """获取已保存的 API Key 列表（返回时隐藏完整密钥，仅显示前后几位）"""
    doc = await mongodb.find_one(SETTINGS_COLLECTION, {"_id": SETTINGS_DOC_ID})
    saved_keys = doc.get("keys", {}) if doc else {}

    masked = {}
    for key, label in ALLOWED_KEYS.items():
        value = saved_keys.get(key) or ""
        if value and len(value) > 8:
            masked[key] = {
                "label": label,
                "masked": value[:4] + "*" * (len(value) - 8) + value[-4:],
                "configured": True,
            }
        elif value:
            masked[key] = {"label": label, "masked": "***", "configured": True}
        else:
            masked[key] = {"label": label, "masked": "", "configured": False}

    return JSONResponse(content={"status": "success", "data": masked})


@router.post("/keys", response_model=ApiResponse)
async def save_api_keys(payload: dict) -> JSONResponse:
    """保存 API Key 并写入 .env 文件"""
    from datetime import UTC, datetime
    from pathlib import Path

    keys = {}
    for key in ALLOWED_KEYS:
        value = payload.get(key, "")
        if value:
            keys[key] = value.strip()

    now = datetime.now(UTC)
    await mongodb.replace_one(
        SETTINGS_COLLECTION,
        {"_id": SETTINGS_DOC_ID},
        {
            "_id": SETTINGS_DOC_ID,
            "keys": keys,
            "updated_at": now,
        },
        upsert=True,
    )

    # 同时写入 .env 文件，使配置立即生效
    env_path = Path(__file__).resolve().parents[1] / ".env"
    env_lines = []
    seen_keys = set()

    # 键名映射 (settings key -> env var name)
    key_to_env = {
        "deepseek_api_key": "DEEPSEEK_API_KEY",
        "dashscope_api_key": "DASHSCOPE_API_KEY",
        "vidu_api_token": "VIDU_API_TOKEN",
    }

    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            env_lines = f.readlines()

    # 更新或追加环境变量
    new_lines = []
    for line in env_lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            var_name = stripped.split("=", 1)[0].strip()
            if var_name in key_to_env.values():
                # 找到对应的 key
                for sdk, env_n in key_to_env.items():
                    if env_n == var_name and sdk in keys:
                        new_lines.append(f"{env_n}={keys[sdk]}\n")
                        seen_keys.add(sdk)
                        break
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    # 追加尚不存在的 key
    for sdk, env_n in key_to_env.items():
        if sdk not in seen_keys and sdk in keys:
            new_lines.append(f"{env_n}={keys[sdk]}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    return JSONResponse(content={"status": "success", "data": {"saved": list(keys.keys()), "env_updated": True}})


@router.get("/keys/check", response_model=ApiResponse)
async def check_api_keys() -> JSONResponse:
    """检查当前环境 / 数据库中的 Key 是否足够启动 pipeline"""
    from config import settings

    env_keys = {
        "deepseek_api_key": bool(settings.deepseek_api_key),
        "dashscope_api_key": bool(settings.dashscope_api_key),
    }

    # 如果环境变量没有，查数据库
    doc = await mongodb.find_one(SETTINGS_COLLECTION, {"_id": SETTINGS_DOC_ID})
    db_keys = doc.get("keys", {}) if doc else {}

    deepseek_ok = env_keys["deepseek_api_key"] or bool(db_keys.get("deepseek_api_key"))
    dashscope_ok = env_keys["dashscope_api_key"] or bool(db_keys.get("dashscope_api_key"))

    return JSONResponse(content={
        "status": "success",
        "data": {
            "deepseek_configured": deepseek_ok,
            "dashscope_configured": dashscope_ok,
            "all_ready": deepseek_ok and dashscope_ok,
        },
    })


@router.post("/keys/test", response_model=ApiResponse)
async def test_api_keys(payload: dict) -> JSONResponse:
    """测试指定的 API Key 是否可用"""
    import httpx
    import json

    results = {}

    # 测试 DeepSeek（零成本只读探测 /models，不消耗 Token）
    dsk = payload.get("deepseek_api_key") or ""
    if dsk:
        try:
            prompt_base = model_config("prompt").get("url") or "https://api.deepseek.com/v1"
            async with httpx.AsyncClient(timeout=12, follow_redirects=False) as c:
                r = None
                for probe_url in model_catalog_candidates("prompt", prompt_base):
                    r = await c.get(
                        probe_url,
                        headers={"Authorization": f"Bearer {dsk}"},
                    )
                    if r.status_code not in (404, 405, 501):
                        break
                if r is not None and r.status_code == 200:
                    results["deepseek_api_key"] = {"ok": True, "msg": "连接成功（只读校验通过）"}
                elif r is not None and r.status_code in (401, 403):
                    results["deepseek_api_key"] = {"ok": False, "msg": f"Key 无效或无权限（{r.status_code}）"}
                elif r is not None and r.status_code == 402:
                    results["deepseek_api_key"] = {"ok": False, "msg": "账户余额不足"}
                else:
                    code = r.status_code if r is not None else "未知"
                    results["deepseek_api_key"] = {"ok": False, "msg": f"响应异常 ({code})"}
        except Exception as e:
            results["deepseek_api_key"] = {"ok": False, "msg": f"连接失败: {str(e)[:60]}"}
    else:
        results["deepseek_api_key"] = {"ok": False, "msg": "未填写"}

    # 测试 DashScope（零成本只读探测 /api/v1/files 或模型目录，不消耗 Token）
    dsc = payload.get("dashscope_api_key") or ""
    if dsc:
        try:
            image_base = model_config("image").get("url") or "https://dashscope.aliyuncs.com"
            probe_urls = [
                f"{dashscope_base_url(image_base)}/api/v1/files",
                *model_catalog_candidates("image", image_base),
            ]
            async with httpx.AsyncClient(timeout=12, follow_redirects=False) as c:
                r = None
                for probe_url in dict.fromkeys(probe_urls):
                    r = await c.get(
                        probe_url,
                        headers={"Authorization": f"Bearer {dsc}"},
                    )
                    if r.status_code not in (404, 405, 501):
                        break
                if r is not None and r.status_code == 200:
                    results["dashscope_api_key"] = {"ok": True, "msg": "连接成功（只读校验通过）"}
                elif r is not None and r.status_code in (401, 403):
                    results["dashscope_api_key"] = {"ok": False, "msg": f"Key 无效或无权限（{r.status_code}）"}
                else:
                    try:
                        body = r.json() if r is not None else {}
                    except ValueError:
                        body = {}
                    err_msg = body.get("message") if isinstance(body, dict) else ""
                    code = r.status_code if r is not None else "未知"
                    results["dashscope_api_key"] = {
                        "ok": False,
                        "msg": f"错误: {err_msg[:60]}" if err_msg else f"响应异常 ({code})",
                    }
        except Exception as e:
            results["dashscope_api_key"] = {"ok": False, "msg": f"连接失败: {str(e)[:60]}"}
    else:
        results["dashscope_api_key"] = {"ok": False, "msg": "未填写"}

    # 测试 Vidu（轻量 ping）
    vidu = payload.get("vidu_api_token") or ""
    if vidu:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    "https://api.vidu.com/ent/v2/user/info",
                    headers={"X-API-KEY": vidu},
                )
                if r.status_code == 200:
                    results["vidu_api_token"] = {"ok": True, "msg": "连接成功"}
                elif r.status_code == 401:
                    results["vidu_api_token"] = {"ok": False, "msg": "Token 无效（401）"}
                else:
                    results["vidu_api_token"] = {"ok": False, "msg": f"响应异常 ({r.status_code})"}
        except Exception as e:
            results["vidu_api_token"] = {"ok": False, "msg": f"连接失败: {str(e)[:60]}"}
    else:
        results["vidu_api_token"] = {"ok": True, "msg": "未填写（可选）"}

    return JSONResponse(content={"status": "success", "data": results})

from typing import Literal
import re

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, SecretStr

from urllib.parse import urlsplit

from services.image.qwen_image_service import REFERENCE_MODELS as SUPPORTED_QWEN_IMAGE_MODELS
from services.model_config import (
    KINDS,
    clear_limits,
    env_default_limits,
    generation_limits,
    is_siliconflow_url,
    model_config,
    save_limits,
    save_models,
    saved_limits,
    saved_models,
    siliconflow_v1_base,
    validate_config,
)
from services.settings_persistence import (
    hydrate_limits_from_db,
    hydrate_models_from_db,
    persist_limits_to_db,
    persist_models_to_db,
)
from services.video.wan_video_service import FLASH_MODEL, SILENT_OUTPUT_MODELS, WAN3_MODELS

SUPPORTED_WAN_VIDEO_MODELS = SILENT_OUTPUT_MODELS | WAN3_MODELS | {FLASH_MODEL}
SUPPORTED_SILICONFLOW_VIDEO_MODELS = frozenset({"MiniMaxAI/MiniMax-H3"})
SUPPORTED_SILICONFLOW_IMAGE_MODELS = frozenset({
    "Qwen/Qwen-Image-Edit-2509",
    "Qwen/Qwen-Image-Edit",
    "Qwen/Qwen-Image",
    "black-forest-labs/FLUX.1-Kontext-dev",
    "black-forest-labs/FLUX.1-Kontext-pro",
    "black-forest-labs/FLUX.1-Kontext-max",
    "black-forest-labs/FLUX.1-dev",
    "black-forest-labs/FLUX.1-schnell",
    "Kwai-Kolors/Kolors",
})
SUPPORTED_DASHSCOPE_IMAGE_MODELS = frozenset(SUPPORTED_QWEN_IMAGE_MODELS) | {
    "wan2.7-image",
    "wan2.6-image",
    "wan2.5-t2i-preview",
    "wanx2.1-t2i-turbo",
    "wanx2.1-t2i-plus",
}


def _is_dashscope_url(base: str) -> bool:
    host = (urlsplit(dashscope_base_url(base)).hostname or "").lower()
    return host == "dashscope.aliyuncs.com" or host.endswith(".aliyuncs.com")


def dashscope_base_url(base: str) -> str:
    cleaned = (base or "").strip().rstrip("/")
    for suffix in ("/compatible-mode/v1", "/api/v1", "/v1"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].rstrip("/")
    return cleaned or (base or "").strip().rstrip("/")


def model_catalog_url(kind, base):
    if kind == "prompt":
        if base == "https://api.deepseek.com/v1":
            return "https://api.deepseek.com/models"
        return f"{base}/models"
    return f"{base.removesuffix('/compatible-mode/v1')}/compatible-mode/v1/models"


def model_catalog_candidates(kind: str, base: str) -> list[str]:
    if is_siliconflow_url(base):
        sf_v1 = siliconflow_v1_base(base)
        sf_type = {"prompt": "text", "image": "image", "video": "video"}.get(kind, "text")
        return [f"{sf_v1}/models?type={sf_type}", f"{sf_v1}/models"]
    primary = model_catalog_url(kind, base)
    root = dashscope_base_url(base)
    if kind == "prompt":
        candidates = [
            primary,
            f"{root}/v1/models",
            f"{root}/models",
            f"{root}/compatible-mode/v1/models",
        ]
    else:
        candidates = [
            primary,
            f"{base}/models",
            f"{root}/v1/models",
            f"{root}/models",
            f"{root}/compatible-mode/v1/models",
        ]
    return list(dict.fromkeys(c for c in candidates if c))


def extract_catalog_items(body) -> list[dict] | None:
    if not isinstance(body, dict):
        return None
    raw_list = body.get("data")
    if not isinstance(raw_list, list):
        raw_list = body.get("models")
    if not isinstance(raw_list, list) and isinstance(body.get("output"), dict):
        raw_list = body["output"].get("models")
    if not isinstance(raw_list, list):
        return None
    normalized: list[dict] = []
    for entry in raw_list:
        if isinstance(entry, str) and entry.strip():
            normalized.append({"id": entry.strip()})
        elif isinstance(entry, dict):
            raw_id = entry.get("id") or entry.get("model") or entry.get("name")
            if isinstance(raw_id, str) and raw_id.strip():
                clean_id = raw_id.strip()
                if clean_id.startswith("models/") and "name" in entry and "id" not in entry:
                    clean_id = clean_id.removeprefix("models/")
                normalized.append({**entry, "id": clean_id})
    return normalized


def _model_in_catalog(model_name: str, items: list[dict]) -> bool:
    target = (model_name or "").strip().lower()
    if not target:
        return False
    target_short = target.rsplit("/", 1)[-1]
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        cid = item["id"].strip().lower()
        if cid == target or cid.rsplit("/", 1)[-1] == target_short:
            return True
    return False


def matches_third_party_video_catalog(item: dict, model_id: str) -> bool:
    name = model_id.lower().rsplit("/", 1)[-1]
    outputs = item.get("output_modalities")
    if isinstance(outputs, list):
        return "video" in outputs
    non_video = re.search(
        r"(?:^|[-_])(?:embeddings?|rerank(?:er)?|tts|asr|stt|whisper|transcription|"
        r"moderation|image|imageedit|t2i|i2i|inpaint)(?:[-_]|$)",
        name,
    )
    if non_video:
        return False
    return bool(
        re.search(
            r"(?:^|[-_/])(?:video|i2v|t2v|v2v|r2v|s2v|kling|vidu|seedance|veo|wanx?\d*(?:\.\d+)*|"
            r"cogvideox?|hunyuan-video|luma|runway|sora|hailuo|minimax(?:-video|-h\d+)?|pika|mochi)(?:[-_\d.]|$)",
            name,
        )
    )


def matches_model_catalog(kind, item, model_id, *, siliconflow: bool = False):
    name = model_id.lower().rsplit("/", 1)[-1]
    if kind == "video":
        if siliconflow:
            return model_id in SUPPORTED_SILICONFLOW_VIDEO_MODELS or matches_third_party_video_catalog(item, model_id)
        return model_id in SUPPORTED_WAN_VIDEO_MODELS or name in SUPPORTED_WAN_VIDEO_MODELS

    inputs = item.get("input_modalities")
    outputs = item.get("output_modalities")
    if isinstance(inputs, list) and "text" not in inputs:
        return False

    qwen_image = name.startswith("qwen-image")
    wan_image = bool(re.match(r"wanx?\d+(?:\.\d+)*-(?:image|t2i)(?:-|$)", name))
    edit_only = bool(re.search(r"(?:^|[-_])(?:edit|imageedit|i2i|inpaint)(?:[-_]|$)", name))
    if kind == "image":
        if edit_only and not siliconflow:
            return False
        if isinstance(outputs, list):
            return "image" in outputs and (isinstance(inputs, list) or qwen_image or wan_image)
        return (
            qwen_image
            or wan_image
            or name.startswith(
                (
                    "z-image-",
                    "z-image",
                    "gpt-image-",
                    "dall-e-",
                    "flux-",
                    "flux.",
                    "kolors",
                    "sdxl",
                    "stable-diffusion",
                    "seedream",
                    "cogview",
                    "hunyuan-image",
                    "ideogram",
                    "recraft",
                    "imagen-",
                    "nano-banana",
                    "midjourney",
                )
            )
            or bool(re.search(r"gemini.*image", name))
        )

    non_chat = re.search(
        r"(?:^|[-_])(?:embeddings?|rerank(?:er)?|tts|asr|stt|whisper|transcription|"
        r"moderation|image|imageedit|t2i|i2i|video|i2v|t2v|r2v|v2v|s2v)(?:[-_]|$)",
        name,
    )
    return not non_chat and (not isinstance(outputs, list) or "text" in outputs)


class ModelInput(BaseModel):
    url: str
    model: str
    key: SecretStr | None = None


def effective_model(kind, payload):
    current = model_config(kind)
    url = payload.url.strip().rstrip("/")
    key = payload.key.get_secret_value().strip() if payload.key else ""
    if not key and dashscope_base_url(url) == dashscope_base_url(current["url"]):
        key = current["key"]
    try:
        return validate_config({"url": url, "model": payload.model.strip(), "key": key})
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.get("/models")
async def get_models():
    await hydrate_models_from_db()
    models = {}
    configured = saved_models()
    db_connected = getattr(mongodb, "database", None) is not None
    for kind in KINDS:
        config = model_config(kind)
        local_key = configured.get(kind, {}).get("key")
        key_source = "local" if local_key else "environment" if config["key"] else "none"
        models[kind] = {"url": config["url"], "model": config["model"],
                        "has_key": bool(config["key"]), "key_source": key_source,
                        "active": kind in configured,
                        "persisted_in_db": db_connected and kind in configured}
    return {"status": "success", "data": models}


@router.put("/models/{kind}")
async def put_model(kind: Literal["prompt", "image", "video"], payload: ModelInput):
    config = effective_model(kind, payload)
    if not config["key"]:
        raise HTTPException(422, detail="Enter a key for this URL.")
    models = saved_models()
    models[kind] = config
    save_models(models)
    persisted = await persist_models_to_db(models)
    return {
        "status": "success",
        "data": {"has_key": True, "key_source": "local", "active": True, "persisted_in_db": persisted},
    }


@router.delete("/models/{kind}/key")
async def clear_model_key(kind: Literal["prompt", "image", "video"]):
    models = saved_models()
    profile = models.get(kind)
    if profile and profile.get("key"):
        # Keep an explicit empty key so the environment key is not reused.
        profile["key"] = ""
        save_models(models)
        await persist_models_to_db(models)
    return {"status": "success", "data": (await get_models())["data"][kind]}


@router.post("/models/{kind}/test")
async def test_model(kind: Literal["prompt", "image", "video"], payload: ModelInput):
    config = effective_model(kind, payload)
    if not config["key"]:
        raise HTTPException(422, detail="Enter a key for this URL.")
    headers = {"Authorization": f"Bearer {config['key']}"}
    probe_urls = (
        model_catalog_candidates(kind, config["url"])
        if kind == "prompt" or is_siliconflow_url(config["url"])
        else [
            f"{dashscope_base_url(config['url'])}/api/v1/files",
            *model_catalog_candidates(kind, config["url"]),
        ]
    )
    result = "unconfirmed"
    matched_url = None
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            for url in dict.fromkeys(probe_urls):
                response = await client.get(url, headers=headers)
                if response.status_code in (401, 403):
                    result = "denied"
                    matched_url = url
                    break
                if response.status_code == 402:
                    result = "balance"
                    matched_url = url
                    break
                if response.is_success:
                    try:
                        body = response.json()
                    except ValueError:
                        body = None
                    items = extract_catalog_items(body)
                    if isinstance(items, list):
                        matched_url = url
                        if kind == "prompt":
                            result = (
                                "model_listed"
                                if _model_in_catalog(config["model"], items)
                                else "model_unlisted"
                            )
                        else:
                            result = (
                                "model_listed"
                                if _model_in_catalog(config["model"], items)
                                else "authenticated"
                            )
                        break
                    if kind != "prompt" and isinstance(body, dict) and "output" in body:
                        result = "authenticated"
                        matched_url = url
                        break
                    result = "unconfirmed"
                elif response.status_code in (404, 405, 501):
                    if result == "http_error":
                        continue
                    result = "unconfirmed"
                else:
                    result = "http_error"
                    matched_url = url
    except httpx.TimeoutException:
        result = "timeout"
    except (httpx.RequestError, ValueError):
        result = "unreachable"
    return {"status": "success", "data": {"result": result, "endpoint": matched_url}}


@router.post("/models/{kind}/catalog")
async def query_model_catalog(kind: Literal["prompt", "image", "video"], payload: ModelInput):
    # Catalog lookup only requires a URL and credential, even when no model is selected.
    config = effective_model(kind, payload.model_copy(update={"model": "catalog"}))
    if not config["key"]:
        raise HTTPException(422, detail="Enter a key for this URL.")
    candidates = model_catalog_candidates(kind, config["url"])
    official_dashscope = _is_dashscope_url(config["url"])
    sf_mode = is_siliconflow_url(config["url"])
    headers = {"Authorization": f"Bearer {config['key']}"}
    items = None
    last_response = None
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            for url in candidates:
                response = await client.get(url, headers=headers)
                last_response = response
                if response.status_code in (401, 403):
                    raise HTTPException(422, detail="The catalog rejected this key or workspace.")
                if response.status_code in (404, 405, 501):
                    continue
                if not response.is_success:
                    raise HTTPException(502, detail=f"Model catalog returned HTTP {response.status_code}.")
                try:
                    body = response.json()
                except ValueError:
                    body = None
                parsed = extract_catalog_items(body)
                if isinstance(parsed, list):
                    items = parsed
                    break
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail="Model catalog request timed out.") from exc
    except (httpx.RequestError, ValueError) as exc:
        raise HTTPException(502, detail="Could not connect to the model catalog.") from exc

    available = isinstance(items, list)
    models = sorted({
        model_id for item in (items if available else [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
        and (model_id := item["id"].strip()) and matches_model_catalog(kind, item, model_id, siliconflow=sf_mode)
    })
    suggestion_source = "none"
    if official_dashscope and kind == "video":
        suggested_models = sorted(SUPPORTED_WAN_VIDEO_MODELS.difference(models))
        suggestion_source = "official" if suggested_models else "none"
    elif official_dashscope and kind == "image":
        suggested_models = sorted(SUPPORTED_DASHSCOPE_IMAGE_MODELS.difference(models))
        suggestion_source = "official" if suggested_models else "none"
    elif sf_mode and kind == "video":
        suggested_models = sorted(SUPPORTED_SILICONFLOW_VIDEO_MODELS.difference(models))
        suggestion_source = "official" if suggested_models else "none"
    elif sf_mode and kind == "image":
        suggested_models = sorted(SUPPORTED_SILICONFLOW_IMAGE_MODELS.difference(models))
        suggestion_source = "official" if suggested_models else "none"
    elif kind == "video" and available:
        suggested_models = sorted({
            model_id for item in items
            if isinstance(item, dict) and isinstance(item.get("id"), str)
            and (model_id := item["id"].strip())
            and model_id not in models
            and matches_third_party_video_catalog(item, model_id)
        })
        suggestion_source = "catalog" if suggested_models else "none"
    else:
        suggested_models = []
    return {"status": "success", "data": {
        "models": models,
        "available": available,
        "raw_count": len(items) if available else 0,
        "suggested_models": suggested_models,
        "suggestion_source": suggestion_source,
        "endpoint": last_response.request.url.path if (last_response is not None and getattr(last_response, "request", None) is not None) else None,
    }}


class LimitsInput(BaseModel):
    max_images_per_task: int = 0
    max_video_seconds_per_task: int = 0
    max_dynamic_scenes_per_task: int = 0


@router.get("/limits")
async def get_limits():
    await hydrate_limits_from_db()
    local = saved_limits()
    effective = generation_limits()
    return {
        "status": "success",
        "data": {
            **effective,
            "source": "local" if local else "env",
            "persisted_in_db": getattr(mongodb, "database", None) is not None and bool(local),
            "env_defaults": env_default_limits(),
        },
    }


@router.put("/limits")
async def update_limits(body: LimitsInput):
    try:
        cleaned = save_limits(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    await persist_limits_to_db(cleaned)
    return await get_limits()


@router.delete("/limits")
async def reset_limits():
    clear_limits()
    await persist_limits_to_db(None)
    return await get_limits()
