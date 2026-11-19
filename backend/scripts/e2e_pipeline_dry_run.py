"""
全流程端到端干跑验证脚本（不调用付费图片/视频 API，零出图与视频费用）。

用法：
  1. 离线全链路自检（无需外网，用拟真脏数据压测全部 10+ 智能体衔接与防崩护栏）：
     PYTHONPATH=backend python3 backend/scripts/e2e_pipeline_dry_run.py --offline

  2. 真实文本 API 全流程联调（调用「API 配置」里真实的文本大模型，仅图片/视频/配音用本地模拟文件替代）：
     PYTHONPATH=backend python backend/scripts/e2e_pipeline_dry_run.py --live-llm
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
import types
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _install_missing_runtime_stubs() -> None:
    """当在精简 Python 环境运行 --offline 时，为缺失的第三方可选包提供最小运行桩。"""
    def _try_import(name: str) -> bool:
        try:
            __import__(name)
            return True
        except ModuleNotFoundError:
            return False

    if not _try_import("dotenv"):
        sys.modules["dotenv"] = types.SimpleNamespace(
            load_dotenv=lambda *a, **k: None,
            dotenv_values=lambda *a, **k: {},
        )
    if not _try_import("anyio"):
        sys.modules["anyio"] = types.SimpleNamespace(
            Semaphore=asyncio.Semaphore,
            to_thread=types.SimpleNamespace(
                run_sync=lambda fn, *a, abandon_on_cancel=None, **kw: asyncio.sleep(0, result=fn(*a, **kw))
            ),
        )
    if not _try_import("httpx"):
        sys.modules["httpx"] = types.SimpleNamespace(
            AsyncClient=None,
            Client=None,
            TimeoutException=TimeoutError,
            RequestError=OSError,
            NetworkError=OSError,
            HTTPStatusError=RuntimeError,
            ReadTimeout=TimeoutError,
            ConnectTimeout=TimeoutError,
            TransportError=OSError,
        )
    if not _try_import("pymongo"):
        err_mod = types.SimpleNamespace(DuplicateKeyError=Exception)
        sys.modules["pymongo"] = types.SimpleNamespace(errors=err_mod)
        sys.modules["pymongo.errors"] = err_mod
    if not _try_import("pydantic"):
        mod = types.ModuleType("pydantic")

        class _BaseModel:
            def __init__(self, **kw):
                for key, val in self.__class__.__dict__.items():
                    if not key.startswith("_") and not callable(val) and key not in kw:
                        if isinstance(val, (dict, list)):
                            kw[key] = deepcopy(val)
                        elif not isinstance(val, property):
                            kw[key] = val
                self.__dict__.update(kw)
                if self.__class__.__name__ == "Script" and isinstance(self.__dict__.get("segments"), list):
                    from models.script_model import ScriptSegment
                    self.__dict__["segments"] = [
                        seg if isinstance(seg, ScriptSegment) else ScriptSegment(**seg)
                        for seg in self.__dict__["segments"]
                    ]
                if self.__class__.__name__ == "ScriptSegment" and isinstance(self.__dict__.get("dialogue_lines"), list):
                    from models.script_model import ScriptLine
                    self.__dict__["dialogue_lines"] = [
                        ln if isinstance(ln, ScriptLine) else ScriptLine(**ln)
                        for ln in self.__dict__["dialogue_lines"]
                    ]
                for hook_name in ("validate_speech", "normalize_duration_range", "validate_duration_range"):
                    if hasattr(self, hook_name) and callable(getattr(self, hook_name)):
                        getattr(self, hook_name)()

            def model_dump(self, **kw):
                def _ser(obj):
                    if isinstance(obj, _BaseModel):
                        return {k: _ser(v) for k, v in obj.__dict__.items()}
                    if isinstance(obj, list):
                        return [_ser(x) for x in obj]
                    if isinstance(obj, dict):
                        return {k: _ser(v) for k, v in obj.items()}
                    return deepcopy(obj)
                return _ser(self)

        mod.BaseModel = _BaseModel
        mod.Field = lambda *a, **k: k.get("default") if "default" in k else (k["default_factory"]() if "default_factory" in k else None)
        mod.ConfigDict = lambda **kw: dict(kw)
        mod.model_validator = lambda *a, **k: (lambda fn: fn)
        mod.field_validator = lambda *a, **k: (lambda fn: fn)
        mod.SecretStr = type("SecretStr", (), {"__init__": lambda s, v="": setattr(s, "_v", v), "get_secret_value": lambda s: s._v})
        sys.modules["pydantic"] = mod
    if not _try_import("imageio_ffmpeg"):
        sys.modules["imageio_ffmpeg"] = types.SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg")
    if not _try_import("edge_tts"):
        sys.modules["edge_tts"] = types.SimpleNamespace()
    if not _try_import("core.database"):
        sys.modules["core.database"] = types.SimpleNamespace(mongodb=None)
    if not _try_import("services.tts_service"):
        sys.modules["services.tts_service"] = types.SimpleNamespace(
            resolve_voice_for_speaker=lambda *a, **k: "zh-CN-YunyangNeural",
            tts_service=types.SimpleNamespace(generate_voice=None),
        )


class InMemoryMongo:
    """内存版 MongoDB，完整支持点号嵌套 $set 更新，避免污染真实数据库。"""

    def __init__(self) -> None:
        self.cols: dict[str, dict[str, dict]] = {}

    def _col(self, name: str) -> dict[str, dict]:
        return self.cols.setdefault(name, {})

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        for key, expected in (query or {}).items():
            cur = doc
            for part in key.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            if isinstance(expected, dict) and "$ne" in expected:
                if cur == expected["$ne"]:
                    return False
            elif cur != expected:
                return False
        return True

    async def insert_one(self, col: str, doc: dict) -> str:
        copy = deepcopy(doc)
        doc_id = str(copy.get("_id") or copy.get("task_id") or copy.get("series_id") or len(self._col(col)))
        copy["_id"] = doc_id
        self._col(col)[doc_id] = copy
        return doc_id

    async def find_one(self, col: str, query: dict):
        for doc in self._col(col).values():
            if self._matches(doc, query):
                return deepcopy(doc)
        return None

    async def find_many(self, col: str, query: dict, limit: int = 100, sort_field: str | None = None, sort_direction: int = 1):
        rows = [deepcopy(doc) for doc in self._col(col).values() if self._matches(doc, query)]
        if sort_field:
            rows.sort(key=lambda item: item.get(sort_field, 0), reverse=(sort_direction < 0))
        return rows[:limit]

    async def replace_one(self, col: str, query: dict, doc: dict, upsert: bool = False):
        target = await self.find_one(col, query)
        key = str(target["_id"]) if target else str(doc.get("_id") or query.get("_id"))
        copy = deepcopy(doc)
        copy["_id"] = key
        self._col(col)[key] = copy
        return True

    async def update_one(self, col: str, query: dict, values: dict):
        for key, doc in self._col(col).items():
            if self._matches(doc, query):
                for field, val in values.items():
                    parts = field.split(".")
                    cur = doc
                    for part in parts[:-1]:
                        if not isinstance(cur.get(part), dict):
                            cur[part] = {}
                        cur = cur[part]
                    cur[parts[-1]] = deepcopy(val)
                return True
        return False

    async def delete_one(self, col: str, query: dict):
        for key, doc in list(self._col(col).items()):
            if self._matches(doc, query):
                del self._col(col)[key]
                return True
        return False


class LocalMockImageService:
    """本地生成真实有效 PNG 图片（零 API 费用）。"""

    model = "Kwai-Kolors/Kolors"
    supports_reference_images = False
    max_reference_images = 0

    def generate_image(self, task_id: str, scene_id: str, image_prompt: str, negative_prompt=None, ratio: str = "9:16", reference_images=None):
        from PIL import Image

        dest_dir = BACKEND_DIR / "assets" / task_id / "images"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{scene_id}.png"
        size = (360, 640) if ratio == "9:16" else (640, 360)
        Image.new("RGB", size, color=(42, 64, 96)).save(dest, format="PNG")
        return {
            "status": "success",
            "source": "mock_local_png",
            "asset_path": f"backend/assets/{task_id}/images/{scene_id}.png",
        }


class LocalMockVideoService:
    """本地用 ffmpeg 生成真实有效 MP4 片段（零 API 费用，带模拟硅基流动接口特性）。"""

    model = "MiniMaxAI/MiniMax-H3"
    supports_last_frame = True
    supports_negative_prompt = False
    supports_resolution = True

    def generate_video(self, task_id: str, scene_id: str, image_path: str, video_prompt=None, duration: int = 5, ratio: str = "9:16", **kwargs):
        dest_dir = BACKEND_DIR / "assets" / task_id / "videos"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{scene_id}.mp4"
        size = "360x640" if ratio == "9:16" else "640x360"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x2a4060:s={size}:r=24:d={int(duration)}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {
            "status": "success",
            "source": "mock_siliconflow_mp4",
            "asset_path": f"backend/assets/{task_id}/videos/{scene_id}.mp4",
            "duration": int(duration),
        }


async def _mock_tts_generate(text: str, output_path: str, **kwargs) -> float:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    dur = max(1.0, min(4.2, round(len(text) / 4.2, 2)))
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono:d={dur}",
            "-c:a", "libmp3lame", "-q:a", "6", str(out),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return dur


class AdversarialOfflineLLM:
    """模拟真实文本大模型（刻意夹带静默镜头、空泛词、错位 scene_id 与舞台指示对白，验证全链路容错）。"""

    def _call_api(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000):
        if "发布文案" in system_prompt or ("title" in system_prompt and "tags" in system_prompt):
            return {
                "title": "20分钟倒计时！百年老字号后厨惊天反转",
                "body": "林晚亮出1986黄铜测温针当众验汤，媒体直播车撞门而入！第2集顾慎之亮评委令接管现场。",
                "tags": ["老字号保卫战", "短剧反转", "高能对峙"],
            }
        if "episodes_outline" in system_prompt:
            return {
                "title": "后厨倒计时：老字号保卫战",
                "shootability_report": {"overall_feasibility": "S级", "metrics": [{"name": "前3秒视觉抓力", "score": 94, "comment": "封条倒计时强冲突"}]},
                "characters": [
                    {"name": "林晚", "role": "女主", "face_dna": "25岁，左耳后小朱砂痣，高马尾", "signature_clothing": "白色立领厨师服配黑色防滑厨师鞋", "tts_voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "pitch": "+0Hz"},
                    {"name": "周明哲", "role": "反派", "face_dna": "32岁，背头金边方框眼镜", "signature_clothing": "银灰西装马甲配亮面黑色德比皮鞋", "tts_voice": "zh-CN-YunyangNeural", "rate": "+0%", "pitch": "+0Hz"},
                ],
                "scenes_spec": [{"scene_name": "松鹤楼一楼大厅", "space_type": "indoor", "era_and_style": "现代中式实景", "layout_and_materials": "红木雕花与原木品鉴桌", "lighting_and_kelvin": "3200K暖黄吊灯", "angles": [f"第{i}格：松鹤楼机位{i}" for i in range(1, 10)]}],
                "props_spec": [{"prop_name": "1986黄铜测温针", "material_and_color": "哑光黄铜实心", "size_vs_palm": "手掌1.2倍长", "key_detail": "针管刻有1986编号", "style": "写实道具"}],
                "episodes_outline": [
                    {"episode_num": 1, "title": "第1集 测温反转", "opening_hook": "20分钟封条逼门", "core_event": "测温针戳穿调包汤底", "ending_hook": "媒体直播车撞门而入", "paywall_card": False, "paywall_reason": ""},
                    {"episode_num": 2, "title": "第2集 秘方对质", "opening_hook": "冷库被锁断货", "core_event": "顾慎之亮评委令保现场", "ending_hook": "盗版菜品抢先报分", "paywall_card": True, "paywall_reason": "反转前卡点"},
                    {"episode_num": 3, "title": "第3集 直播打假", "opening_hook": "全球直播开场", "core_event": "三分钟盲测揭穿非法添加物", "ending_hook": "十亿联营合约落定", "paywall_card": False, "paywall_reason": ""},
                ],
                "first_episode_script": "△周明哲将2000万解约协议拍上红木桌。\n周明哲：签字，老宅归我。\n△林晚抽出黄铜测温针扎进汤盅。\n林晚：清汤九十二度，你差六度。\n△全场宾客椅子刮地乱响站起。\n△东门外直播车顶灯啪地打亮。",
                "continuity_ledgers": {"physical_ledger": [], "knowledge_ledger": [], "prop_ledger": [], "relationship_ledger": []},
            }
        return {"overall_score": 88, "grade": "A", "verdict": "pass", "issues": [], "polished_script": "△精修剧本", "changes_made": ["强化开场动作"], "upgraded_prompt": "林晚抽出黄铜测温针扎进汤盅"}

    def plan_task(self, user_input, duration=None, style=None, platform=None, ratio="9:16", generation_mode="storyboard_flow"):
        return {
            "video_type": "story",
            "topic": "后厨倒计时：老字号保卫战",
            "duration": duration or 15,
            "style": style or "短剧",
            "platform": platform or "抖音",
            "ratio": ratio,
            "generation_mode": generation_mode,
            "visual_world": "3200K 暖黄吊灯照亮的百年红木酒楼大厅",
            "characters": [{"name": "林晚", "face_dna": "高马尾左耳朱砂痣", "signature_clothing": "白色厨师服黑厨师鞋", "tts_voice": "zh-CN-XiaoxiaoNeural"}],
            "plot_beats": [{"beat_name": "逼签", "description": "周明哲拍协议逼签", "suggested_duration": 5}, {"beat_name": "测温反转", "description": "林晚扎针揭穿差六度", "suggested_duration": 5}, {"beat_name": "静默惊变", "description": "门外直播车强光打入", "suggested_duration": 5}],
        }

    def generate_script(self, task_plan):
        mode = task_plan.get("generation_mode")
        series_ctx = str(task_plan.get("series_context") or "")
        is_ep2 = '"episode_num": 2' in series_ctx or "第2集" in series_ctx or "已破门" in series_ctx
        if mode == "storyboard_flow":
            return {
                "title": "后厨倒计时",
                "hook": "20分钟封条逼门",
                "content": "逼签、测温打脸、门外直播车撞门。",
                "segments": [
                    {"duration": 5, "plot": "周明哲摔2000万协议逼签", "voice_start": 0.3, "voiceover": "签字，老宅归我。", "dialogue_lines": [{"speaker": "周明哲", "text": "（冷笑）", "start": 0.1}, {"speaker": "周明哲", "text": "签字，老宅归我。", "start": 0.5}]},
                    {"duration": 5, "plot": "林晚抽黄铜测温针扎进汤盅", "voice_start": 0.4, "voiceover": "清汤九十二度，你差六度。", "dialogue_lines": [{"speaker": "林晚", "text": "清汤九十二度，你差六度。", "start": 0.4}]},
                    {"duration": 5, "plot": "全景：门外直播车顶灯啪地打亮", "voice_start": 0.0, "voiceover": "", "dialogue_lines": []},
                ],
                "source": "offline_mock",
            }
        if is_ep2:
            return {
                "title": "第2集 秘方对质",
                "hook": "直播车强光照入后厨，冷库已被反锁",
                "content": "承接上集测温针与直播车强光，顾慎之亮牌保现场、保安拦门对质。",
                "segments": [
                    {"duration": 6, "plot": "林晚握紧1986黄铜测温针转身迎向直播镜头，冷库铁门被锁死", "voice_start": 0.2, "voiceover": "冷库锁了？当着直播查账！", "dialogue_lines": [{"speaker": "保安", "text": "闲人退后，冷库已封！", "start": 0.2}, {"speaker": "林晚", "text": "当着直播面开锁查账！", "start": 2.4}]},
                    {"duration": 6, "plot": "顾慎之亮出金匙榜评委令按住协议桌", "voice_start": 0.3, "voiceover": "这锅汤我亲口尝过，查原料单。", "dialogue_lines": [{"speaker": "顾慎之", "text": "这锅汤我尝过，查原料单！", "start": 0.3}]},
                ],
                "source": "offline_mock",
            }
        return {
            "title": "第1集 测温反转",
            "hook": "20分钟封条逼门",
            "content": "逼签与测温针反转。",
            "segments": [
                {"duration": 6, "plot": "周明哲摔协议逼签，保安守住大厅门口", "voice_start": 0.3, "voiceover": "签字，这老宅明早归我。", "dialogue_lines": [{"speaker": "周明哲", "text": "签字，这老宅明早归我。", "start": 0.3}]},
                {"duration": 6, "plot": "林晚抽黄铜测温针揭穿假汤，媒体直播车撞门亮灯", "voice_start": 0.3, "voiceover": "清汤九十二度，你差整整六度。", "dialogue_lines": [{"speaker": "林晚", "text": "清汤九十二度，你差整整六度。", "start": 0.3}]},
            ],
            "source": "offline_mock",
        }

    def generate_storyboard(self, script, target_duration=60, generation_mode="storyboard_flow"):
        segs = script.get("segments") or []
        out = []
        for idx, seg in enumerate(segs, start=1):
            out.append({
                "scene_id": "" if idx == 1 else f"scene_{idx:02d}",  # 刻意置空第1镜ID测兜底
                "scene_index": idx,
                "duration": seg["duration"],
                "character_ids": ["林晚", "hallucinated_id"] if idx == 1 else ["char_linwan"],  # 刻意传名字和错ID测纠偏
                "shot_type": "近景" if idx % 2 else "特写",
                "visual_description": f"电影感氛围感，{seg['plot']}",  # 刻意混入空泛词测清洗
                "voiceover": seg.get("voiceover", ""),
                "subtitle": seg.get("voiceover", ""),
                "voice_start": seg.get("voice_start", 0.0),
                "dialogue_lines": seg.get("dialogue_lines") or [],
                "character_state": "" if idx == 3 else "冷峻→紧绷",
                "scene_continuity": "" if idx == 3 else "承接大厅站位",
                "transition_note": "" if idx == 3 else "硬切推进",
                "transition_type": "flash_white" if idx == 3 else "cut",  # 刻意传非标转场测清洗
                "sound_cues": [{"name": "impact", "start": 9.9}],  # 刻意越界测钳制
                "need_dynamic_video": True,
                "start_state": f"{seg['plot']}起始姿态",
                "end_state": f"{seg['plot']}定格状态",
            })
        return out

    def polish_dialogue(self, scenes, target_duration=None):
        return []  # 刻意漏返全部条目测保底不崩

    def generate_image_prompts(self, batch, ratio="9:16", style=None, visual_bible=None, per_second=True):
        out = []
        for sc in batch:
            out.append({
                "scene_id": str(sc.get("scene_index", 1)),  # 刻意返回错位短ID测纠偏
                "image_prompt": f"{sc.get('visual_description')}，中式木雕厅堂实景，侧逆暖光",
                "video_prompt": f"从首帧自然推进：{sc.get('visual_description')}",
                "motion_beats": [
                    {"start": "0", "end": "3", "subject": "林晚", "camera": "中近景缓推", "start_state": "持针起手", "action": "推进动作", "end_state": "稳态定格"}
                ],  # 刻意给错误尾秒与字符串数字测自动对齐
                "last_frame_for_transition": "人物停在稳态",
            })
        return out

    def review_story_coverage(self, *a, **k):
        return {"verdict": "pass", "issues": []}


async def run_all(live_llm: bool) -> int:
    if not live_llm:
        _install_missing_runtime_stubs()

    from agents import (
        dialogue_polish_agent,
        export_agent,
        image_agent,
        prompt_agent,
        script_agent,
        storyboard_agent,
        subtitle_agent,
        task_planner_agent,
        video_agent,
        voice_agent,
    )
    from services import drama_craft_service, task_service as task_svc_mod

    db = InMemoryMongo()
    if live_llm:
        from services.provider_factory import get_llm_service
        llm = get_llm_service()
        print("▶ 模式: --live-llm（使用真实文本模型 API，图片/视频/配音全部本地零费模拟）")
    else:
        llm = AdversarialOfflineLLM()
        print("▶ 模式: --offline（拟真脏数据全链路压测，图片/视频/配音全部本地零费模拟）")

    img_svc = LocalMockImageService()
    vid_svc = LocalMockVideoService()
    created_task_ids: list[str] = []
    report_lines: list[str] = []

    def _step(ok: bool, name: str, detail: str) -> None:
        mark = "PASS" if ok else "FAIL"
        line = f"[{mark}] {name}: {detail}"
        report_lines.append(line)
        print(line, flush=True)

    async def _fake_dispatch(task, op_kind, scene_id, fn):
        return await asyncio.to_thread(fn)

    async def _get_req_task(tid: str):
        doc = await db.find_one("video_tasks", {"_id": tid})
        if not doc:
            raise ValueError(f"Task not found: {tid}")
        return doc

    async def _update_task(tid: str, fields: dict):
        await db.update_one("video_tasks", {"_id": tid}, fields)

    async def _mark_plan_success(tid: str, agent_name: str, **kw):
        await _update_task(tid, kw.get("extra_fields") or {})

    # ── Stage 0: StudioCraft 工坊拆剧 ──
    t0 = time.time()
    sample_story = (
        "距离银行贴封条只剩20分钟。百年老字号松鹤楼传人林晚（25岁，高马尾，白厨师服黑防滑鞋）"
        "遭运营总监周明哲（32岁，银灰西装黑皮鞋）拿2000万违约单逼签并调包汤底。"
        "林晚抽出刻有1986编号的黄铜测温针戳穿汤温差6度，角落里的金匙榜评委顾慎之起身按住桌面。"
        "第1集逼签反转；第2集查证秘方；第3集直播打假重夺三星。"
    )
    if live_llm:
        craft_out = drama_craft_service.ai_decompose_novel_to_drama(sample_story, title="松鹤楼干跑测试", total_episodes=3)
    else:
        with patch.object(drama_craft_service, "_get_real_llm", return_value=llm):
            craft_out = drama_craft_service.ai_decompose_novel_to_drama(sample_story, title="松鹤楼干跑测试", total_episodes=3)
    _step(
        bool(craft_out.get("first_episode_script") and len(craft_out.get("episodes_outline") or []) == 3),
        "1. StudioCraft AI拆剧",
        f"耗时 {time.time()-t0:.1f}s，剧名={craft_out.get('title')}，角色={len(craft_out.get('characters') or [])}人，分集={len(craft_out.get('episodes_outline') or [])}集",
    )

    # ── Stage 1..10: 运行完整视频流水线（storyboard_flow + auto_director）──
    from contextlib import ExitStack
    fake_settings = SimpleNamespace(
        enable_paid_api=True,
        max_images_per_task=0,
        max_video_seconds_per_task=0,
        max_dynamic_scenes_per_task=0,
        video_provider="siliconflow",
        siliconflow_video_resolution="768p",
        wan_video_model="wan3.0-video",
        vidu_resolution="1080p",
        vidu_trial_resolution="720p",
    )

    def _build_patches():
        return [
            patch.object(task_planner_agent, "get_required_task", side_effect=_get_req_task),
            patch.object(task_planner_agent, "get_llm_service", return_value=llm),
            patch.object(task_planner_agent, "mark_agent_start", new=AsyncMock()),
            patch.object(task_planner_agent, "mark_agent_success", new=AsyncMock(side_effect=_mark_plan_success)),
            patch.object(task_planner_agent, "mark_agent_failed", new=AsyncMock()),
            patch.object(script_agent, "mongodb", db),
            patch.object(script_agent, "settings", fake_settings),
            patch.object(script_agent, "get_required_task", side_effect=_get_req_task),
            patch.object(script_agent, "get_llm_service", return_value=llm),
            patch.object(script_agent.task_service, "update_task_fields", side_effect=_update_task),
            patch.object(script_agent, "mark_agent_start", new=AsyncMock()),
            patch.object(script_agent, "mark_agent_success", new=AsyncMock()),
            patch.object(script_agent, "mark_agent_failed", new=AsyncMock()),
            patch.object(storyboard_agent, "mongodb", db),
            patch.object(storyboard_agent, "settings", fake_settings),
            patch.object(storyboard_agent, "get_required_task", side_effect=_get_req_task),
            patch.object(storyboard_agent, "get_llm_service", return_value=llm),
            patch.object(storyboard_agent.task_service, "update_task_fields", side_effect=_update_task),
            patch.object(storyboard_agent, "mark_agent_start", new=AsyncMock()),
            patch.object(storyboard_agent, "mark_agent_success", new=AsyncMock()),
            patch.object(storyboard_agent, "mark_agent_failed", new=AsyncMock()),
            patch.object(dialogue_polish_agent, "mongodb", db),
            patch.object(dialogue_polish_agent, "get_required_task", side_effect=_get_req_task),
            patch.object(dialogue_polish_agent, "get_llm_service", return_value=llm),
            patch.object(dialogue_polish_agent, "mark_agent_start", new=AsyncMock()),
            patch.object(dialogue_polish_agent, "mark_agent_success", new=AsyncMock()),
            patch.object(dialogue_polish_agent, "mark_agent_failed", new=AsyncMock()),
            patch.object(prompt_agent, "mongodb", db),
            patch.object(prompt_agent, "get_required_task", side_effect=_get_req_task),
            patch.object(prompt_agent, "get_llm_service", return_value=llm),
            patch.object(prompt_agent.task_service, "update_task_fields", side_effect=_update_task),
            patch.object(prompt_agent, "mark_agent_start", new=AsyncMock()),
            patch.object(prompt_agent, "mark_agent_success", new=AsyncMock()),
            patch.object(prompt_agent, "mark_agent_failed", new=AsyncMock()),
            patch.object(image_agent, "mongodb", db),
            patch.object(image_agent, "settings", fake_settings),
            patch.object(image_agent, "get_image_service", return_value=img_svc),
            patch.object(image_agent, "dispatch_generation", side_effect=_fake_dispatch),
            patch.object(image_agent, "mark_agent_start", new=AsyncMock()),
            patch.object(image_agent, "mark_agent_success", new=AsyncMock()),
            patch.object(image_agent, "mark_agent_failed", new=AsyncMock()),
            patch.object(voice_agent, "mongodb", db),
            patch.object(voice_agent, "settings", fake_settings),
            patch.object(voice_agent, "get_llm_service", return_value=llm),
            patch.object(voice_agent.tts_service, "generate_voice", side_effect=_mock_tts_generate),
            patch.object(voice_agent, "mark_agent_start", new=AsyncMock()),
            patch.object(voice_agent, "mark_agent_success", new=AsyncMock()),
            patch.object(voice_agent, "mark_agent_failed", new=AsyncMock()),
            patch.object(video_agent, "mongodb", db),
            patch.object(video_agent, "video_service_for_task", return_value=vid_svc),
            patch.object(video_agent, "dispatch_generation", side_effect=_fake_dispatch),
            patch.object(video_agent, "mark_agent_start", new=AsyncMock()),
            patch.object(video_agent, "mark_agent_success", new=AsyncMock()),
            patch.object(video_agent, "mark_agent_failed", new=AsyncMock()),
            patch.object(subtitle_agent, "mongodb", db),
            patch.object(subtitle_agent, "mark_agent_start", new=AsyncMock()),
            patch.object(subtitle_agent, "mark_agent_success", new=AsyncMock()),
            patch.object(subtitle_agent, "mark_agent_failed", new=AsyncMock()),
            patch.object(export_agent, "mongodb", db),
            patch.object(export_agent, "mark_agent_start", new=AsyncMock()),
            patch.object(export_agent, "mark_agent_success", new=AsyncMock()),
            patch.object(export_agent, "mark_agent_failed", new=AsyncMock()),
            patch("services.provider_factory.get_image_service", return_value=img_svc),
            patch("services.drama_craft_service._get_real_llm", return_value=llm),
            patch("services.video_vision.review_enabled", return_value=False),
        ]

    modes_to_run = [("storyboard_flow", 15)] if live_llm else [("storyboard_flow", 15), ("auto_director", 12)]
    try:
        for mode_name, target_dur in modes_to_run:
            tid = f"dryrun_{mode_name}_{int(time.time())}"
            created_task_ids.append(tid)
            await db.insert_one(
                "video_tasks",
                {
                    "_id": tid,
                    "task_id": tid,
                    "user_input": f"【剧名】{craft_out.get('title')}\n{craft_out.get('first_episode_script')}",
                    "duration": target_dur,
                    "duration_locked": False,
                    "style": "短剧",
                    "ratio": "9:16",
                    "generation_mode": mode_name,
                    "pre_shoot_review": False,
                    "status": "created",
                    "metadata": {},
                },
            )
            with ExitStack() as stack:
                for p in _build_patches():
                    stack.enter_context(p)
                t_mode = time.time()
                plan = await task_planner_agent.TaskPlannerAgent().run(tid)
                _step(bool(plan), f"2. [{mode_name}] TaskPlannerAgent", f"topic={plan.get('topic')}")
                script_doc = await script_agent.ScriptAgent().run(tid)
                _step(bool(script_doc.get("segments")), f"3. [{mode_name}] ScriptAgent + StoryJudge", f"segments={len(script_doc.get('segments') or [])}")
                sb = await storyboard_agent.StoryboardAgent().run(tid)
                _step(len(sb) >= 1, f"4. [{mode_name}] StoryboardAgent", f"scenes={len(sb)}, total_dur={sum(int(s['duration']) for s in sb)}s")
                dp = await dialogue_polish_agent.DialoguePolishAgent().run(tid)
                _step(len(dp) == len(sb), f"5. [{mode_name}] DialoguePolishAgent", f"polished={len(dp)}")
                pr = await prompt_agent.PromptAgent().run(tid)
                _step(all(p.get("image_prompt") and p.get("video_prompt") for p in pr), f"6. [{mode_name}] PromptAgent", f"prompts={len(pr)}, clean_no_json={all('tts_voice' not in p['image_prompt'] for p in pr)}")
                imgs = await image_agent.ImageAgent().run(tid)
                _step(len(imgs) >= 1, f"7. [{mode_name}] ImageAgent (Mock PNG)", f"images={len(imgs)}")
                vc = await voice_agent.VoiceAgent().run(tid)
                _step(vc.get("status") == "success", f"8. [{mode_name}] VoiceAgent (Mock MP3)", f"duration={vc.get('duration', 0):.1f}s")
                vids = await video_agent.VideoAgent().run(tid)
                _step(len(vids) >= 1, f"9. [{mode_name}] VideoAgent (Mock MP4)", f"clips={len(vids)}")
                sub = await subtitle_agent.SubtitleAgent().run(tid)
                _step(sub.get("status") == "success", f"10. [{mode_name}] SubtitleAgent", f"srt={sub.get('path')}")
                exp = await export_agent.ExportAgent().run(tid)
                _step(exp.get("status") == "success", f"11. [{mode_name}] ExportAgent", f"总耗时 {time.time()-t_mode:.1f}s")

        # ── Stage M: 多集连载全流程闭环（建剧 -> 大纲确认 -> 第1集 -> 快照确认 -> 第2集继承 -> 发布文案与差异） ──
        from models.series_model import CreateSeriesRequest
        from services import agent_log_service as als_mod, series_service as ss_mod
        from services.character_service import character_service
        from services.publish_materials import write_notes
        from services.snapshot_diff import compare_states

        bound_linwan = {
            "character_id": "char_linwan",
            "name": "林晚",
            "revision": 1,
            "face_dna": "25岁，高马尾，左耳后朱砂痣",
            "signature_clothing": "白色立领厨师服配黑防滑鞋",
            "reference_path": "backend/assets/characters/char_linwan/v1/anchor.png",
            "reference_images": {"front": "backend/assets/characters/char_linwan/v1/anchor.png"},
            "tts_voice": "zh-CN-XiaoxiaoNeural",
            "rate": "+0%",
            "pitch": "+0Hz",
            "confirmed": True,
        }

        async def _run_episode_pipeline(ep_task_id: str) -> dict:
            created_task_ids.append(ep_task_id)
            with ExitStack() as ep_stack:
                for p in _build_patches():
                    ep_stack.enter_context(p)
                await task_planner_agent.TaskPlannerAgent().run(ep_task_id)
                await script_agent.ScriptAgent().run(ep_task_id)
                await storyboard_agent.StoryboardAgent().run(ep_task_id)
                await dialogue_polish_agent.DialoguePolishAgent().run(ep_task_id)
                await prompt_agent.PromptAgent().run(ep_task_id)
                await image_agent.ImageAgent().run(ep_task_id)
                await voice_agent.VoiceAgent().run(ep_task_id)
                ep_vids = await video_agent.VideoAgent().run(ep_task_id)
                await subtitle_agent.SubtitleAgent().run(ep_task_id)
                await export_agent.ExportAgent().run(ep_task_id)
            out_dir = BACKEND_DIR / "outputs" / ep_task_id
            out_dir.mkdir(parents=True, exist_ok=True)
            final_mp4 = out_dir / "final.mp4"
            raw_vid_rel = ep_vids[0].get("path") or ep_vids[0].get("asset_path") if ep_vids and isinstance(ep_vids[0], dict) else None
            if raw_vid_rel:
                src_vid = BACKEND_DIR.parent / str(raw_vid_rel)
                if src_vid.is_file():
                    shutil.copy2(src_vid, final_mp4)
            return {"status": "success", "task_id": ep_task_id}

        def _fake_extract_tail(video_path, destination, display_duration=None):
            from PIL import Image
            dest = Path(destination)
            dest.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (180, 320), color=(36, 52, 82)).save(dest, format="PNG")
            return str(dest)

        svc = ss_mod.SeriesService()
        print("\n▶ Stage M: 开始测试多集连载全流程（AI自主导演 · 弹性时长 10–20s · 非参考生图模型兼容）", flush=True)
        for idx, ep in enumerate(craft_out.get("episodes_outline") or [], start=1):
            print(f"   第{idx}集大纲《{ep.get('title')}》: 开场={ep.get('opening_hook')} | 核心={ep.get('core_event')} | 结尾={ep.get('ending_hook')}", flush=True)

        with ExitStack() as m_stack:
            m_stack.enter_context(patch.object(ss_mod, "mongodb", db))
            m_stack.enter_context(patch.object(task_svc_mod, "mongodb", db))
            m_stack.enter_context(patch.object(als_mod, "mongodb", db))
            m_stack.enter_context(patch.object(ss_mod, "pipeline_runner", side_effect=_run_episode_pipeline))
            m_stack.enter_context(patch.object(character_service, "get_confirmed_profiles", new=AsyncMock(return_value=[bound_linwan])))
            m_stack.enter_context(patch("services.provider_factory.get_image_service", return_value=img_svc))
            m_stack.enter_context(patch("services.provider_factory.get_llm_service", return_value=llm))
            m_stack.enter_context(patch("services.media_quality.extract_tail", side_effect=_fake_extract_tail))
            m_stack.enter_context(patch("services.video_vision.review_enabled", return_value=False))

            series_proj = await svc.create_series(CreateSeriesRequest(
                title=craft_out.get("title") or "松鹤楼保卫战",
                brief=sample_story,
                total_episodes=len(craft_out.get("episodes_outline") or []) or 3,
                episode_duration=15,
                episode_duration_min=10,
                episode_duration_max=20,
                generation_mode="auto_director",
                style="短剧",
                ratio="9:16",
                market_mode="cn",
                character_ids=["char_linwan"],
                initial_episodes=craft_out.get("episodes_outline"),
                continuity_ledgers=craft_out.get("continuity_ledgers"),
                first_episode_script=craft_out.get("first_episode_script"),
            ))
            sid = series_proj["series_id"]
            await svc.confirm_outline(sid)
            _step(
                len(series_proj["episodes"]) == 3 and series_proj["episodes"][0]["status"] == "pending",
                "12. [MultiEpisode] 建剧与大纲入库",
                f"series_id={sid}, mode={series_proj.get('generation_mode')}, range={series_proj.get('episode_duration_min')}–{series_proj.get('episode_duration_max')}s",
            )

            # 第1集执行
            claim1 = await svc.start_run(sid)
            await svc.run_series(sid, claim1["lease"])
            after_ep1 = await svc.get_series(sid)
            ep1 = after_ep1["episodes"][0]
            ep1_task = await db.find_one("video_tasks", {"_id": ep1["task_id"]})
            ep1_snap = await svc.get_snapshot(sid, ep1["output_snapshot_id"])
            _step(
                ep1["status"] == "needs_confirmation"
                and ep1_task.get("duration_locked") is False
                and ep1_task.get("duration_min") == 10
                and ep1_task.get("duration_max") == 20
                and bool(ep1_snap and ep1_snap.get("source_frame_path"))
                and (BACKEND_DIR / "outputs" / ep1["task_id"] / "final.mp4").is_file(),
                "13. [MultiEpisode] 第1集全流水线 + 计划快照",
                f"task={ep1['task_id']}, actual_dur={ep1_snap['state'].get('actual_duration')}s, status={ep1['status']}",
            )

            # 人工确认第1集快照
            confirmed_state_ep1 = {
                **{k: v for k, v in (ep1_snap["state"] or {}).items() if k != "note"},
                "holding": "1986黄铜测温针",
                "location": "松鹤楼一楼大厅（媒体直播车已破门）",
            }
            pub1 = await svc.confirm_snapshot(sid, ep1["output_snapshot_id"], confirmed_state_ep1)

            # 第2集执行，验证完整继承第1集已确认快照
            claim2 = await svc.start_run(sid)
            await svc.run_series(sid, claim2["lease"])
            after_ep2 = await svc.get_series(sid)
            ep2 = after_ep2["episodes"][1]
            ep2_task = await db.find_one("video_tasks", {"_id": ep2["task_id"]})
            ep2_ctx = (ep2_task.get("metadata") or {}).get("series_context") or {}
            ep2_snap = await svc.get_snapshot(sid, ep2["output_snapshot_id"])
            _step(
                ep2["status"] == "needs_confirmation"
                and ep2["input_snapshot_id"] == pub1["snapshot_id"]
                and (ep2_ctx.get("previous_snapshot") or {}).get("state", {}).get("holding") == "1986黄铜测温针"
                and (BACKEND_DIR / "outputs" / ep2["task_id"] / "final.mp4").is_file(),
                "14. [MultiEpisode] 第2集状态继承与全流水线",
                f"task={ep2['task_id']}, inherited_snapshot={ep2['input_snapshot_id']}, holding={(ep2_ctx.get('previous_snapshot') or {}).get('state', {}).get('holding')}",
            )

            # 快照差异对比与第1集发布文案生成
            pub2 = await svc.confirm_snapshot(sid, ep2["output_snapshot_id"], {
                **(ep2_snap["state"] or {}),
                "holding": "金匙榜评委令与原料单",
            })
            diff_items = compare_states(pub1["state"], pub2["state"])
            notes_path = BACKEND_DIR / "outputs" / ep1["task_id"] / "publish_notes.txt"
            notes = write_notes(notes_path, after_ep2, {**ep1, "confirmed_ending": pub1["state"]}, "抖音")
            _step(
                bool(diff_items) and notes_path.is_file() and bool(notes.get("title")),
                "15. [MultiEpisode] 跨集差异与发布文案生成",
                f"diff_fields={len(diff_items)}, publish_title={notes.get('title')}",
            )
    finally:
        for tid in created_task_ids:
            shutil.rmtree(BACKEND_DIR / "assets" / tid, ignore_errors=True)
            shutil.rmtree(BACKEND_DIR / "outputs" / tid, ignore_errors=True)

    failed = sum(1 for line in report_lines if line.startswith("[FAIL]"))
    passed = len(report_lines) - failed
    print(f"\n=== 全流程干跑完成：通过 {passed} 项，失败 {failed} 项（已自动清理临时文件） ===")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="VideoAgent 零图片/视频费用全流程端到端干跑检查")
    parser.add_argument("--live-llm", action="store_true", help="调用真实文本大模型 API（图片/视频/配音仍本地模拟，零出图出视频费用）")
    parser.add_argument("--offline", action="store_true", help="纯离线拟真脏数据全链路压测（默认）")
    args = parser.parse_args()
    return asyncio.run(run_all(live_llm=bool(args.live_llm)))


if __name__ == "__main__":
    raise SystemExit(main())
