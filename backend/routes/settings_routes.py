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

    # 测试 DeepSeek
    dsk = payload.get("deepseek_api_key") or ""
    if dsk:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {dsk}", "Content-Type": "application/json"},
                    json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                )
                if r.status_code == 200:
                    results["deepseek_api_key"] = {"ok": True, "msg": "连接成功"}
                elif r.status_code == 401:
                    results["deepseek_api_key"] = {"ok": False, "msg": "Key 无效（401）"}
                elif r.status_code == 402:
                    results["deepseek_api_key"] = {"ok": False, "msg": "账户余额不足"}
                else:
                    results["deepseek_api_key"] = {"ok": False, "msg": f"响应异常 ({r.status_code})"}
        except Exception as e:
            results["deepseek_api_key"] = {"ok": False, "msg": f"连接失败: {str(e)[:60]}"}
    else:
        results["deepseek_api_key"] = {"ok": False, "msg": "未填写"}

    # 测试 DashScope
    dsc = payload.get("dashscope_api_key") or ""
    if dsc:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
                    headers={
                        "Authorization": f"Bearer {dsc}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "qwen-turbo",
                        "input": {"messages": [{"role": "user", "content": "hi"}]},
                        "parameters": {"max_tokens": 5, "result_format": "message"},
                    },
                )
                if r.status_code == 200:
                    results["dashscope_api_key"] = {"ok": True, "msg": "连接成功"}
                elif r.status_code == 401:
                    results["dashscope_api_key"] = {"ok": False, "msg": "Key 无效（401）"}
                else:
                    body = r.json()
                    results["dashscope_api_key"] = {"ok": False, "msg": f"错误: {body.get('message','')[:60]}"}
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
