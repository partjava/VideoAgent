import json
import re
import time
from typing import Any

from config import settings
from services.model_config import model_config
from services.visual_style import PHOTO_RULES
from services.llm.base import BaseLLMService

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage


def _quality_rules(ratio: str = "9:16") -> str:
    aspect = "竖屏9:16，1080x1920构图" if ratio == "9:16" else "横屏16:9，1920x1080构图"
    return f"""
统一画质规格：1080P高清{aspect}，30fps，标准短视频发布画质。
画面清晰自然、主体明确、背景干净，满足抖音/视频号正常播放即可。
禁止写入这些高成本词：4K、8K、超高清、极致细节、超高解析度、超采样、细节增强。
根据场景明确主光来源和方向，用自然明暗层次塑造体积。保持人物材质真实，关键表情、道具线索和接触点清晰。
"""


STYLE_TEMPLATES: dict[str, dict[str, str]] = {
    "漫剧": {
        "visual": (
            "1080P高清漫画/插画风格，统一美术风格（日系赛璐珞/韩漫厚涂/国漫水墨任选一种并全片保持）。"
            "角色设计固定：发型、瞳色、服装、体型在全片不变。"
            "场景用漫画式构图：特写给表情、中景给动作、远景给环境氛围。"
            "色调统一，高饱和度，光影简洁有力，背景可适度简化但不能空白。"
            "对话字幕由后期添加，生成画面保持干净。"
        ),
        "voice": "角色演绎式配音，男女声分角色，语气有情绪起伏，关键台词加重语气，节奏偏快偏紧凑。",
        "subtitle": "漫剧字幕风格，白色粗体加黑色描边，角色对话用不同颜色区分，旁白用斜体灰色，短句分行。",
    },
    "短剧": {
        "visual": (
            PHOTO_RULES +
            "角色外貌固定：五官、发型、肤色、体型在全片不变。"
            "服装道具与场景匹配时代背景，室内/室外光线自然真实。"
            "镜头语言偏影视化：正反打、过肩镜头、手持跟拍、推拉摇移。"
            "环境细节丰富但不喧宾夺主，始终突出角色表情和肢体动作。"
        ),
        "voice": "影视级角色配音，分角色男女声演绎，情绪代入感强，对白自然，节奏跟随剧情张力变化。",
        "subtitle": "短剧字幕风格，白色粗体加深色阴影，对白居中底部，旁白用灰色小号字，关键情节词用黄色高亮。",
    },
}

DEFAULT_STYLE = "漫剧"


def _style_template(style: str | None) -> dict[str, str]:
    if style and style in STYLE_TEMPLATES:
        return STYLE_TEMPLATES[style]
    return STYLE_TEMPLATES[DEFAULT_STYLE]


def _normalize_openai_base_url(url: str) -> str:
    from urllib.parse import urlsplit
    cleaned = (url or "").strip().rstrip("/")
    if not cleaned:
        return "https://api.deepseek.com/v1"
    if cleaned == "https://dashscope.aliyuncs.com":
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    parsed = urlsplit(cleaned)
    if parsed.path in ("", "/"):
        return f"{cleaned}/v1"
    return cleaned


def _close_json_stack(candidate: str) -> str:
    """清理尾部悬空键名/逗号并补全未闭合的引号与括号栈。"""
    c = candidate.rstrip()
    if c.endswith("\\"):
        c = c[:-1]
    in_str = False
    esc = False
    for ch in c:
        if esc:
            esc = False
        elif ch == "\\" and in_str:
            esc = True
        elif ch == '"':
            in_str = not in_str
    if in_str:
        c += '"'

    # 去掉没有对应冒号/值的尾部残缺键，如 `{"k": 1, "bad_k"` 或 `{"bad_k"`
    for _ in range(3):
        prev = c
        c = re.sub(r',\s*"[^"]*"\s*:\s*$', "", c)
        c = re.sub(r',\s*"[^"]*"\s*$', "", c)
        c = re.sub(r'\{\s*"[^"]*"\s*$', "{", c)
        c = re.sub(r":\s*$", ": null", c)
        c = re.sub(r",(\s*[}\]])", r"\1", c).rstrip(", \t\r\n")
        if c == prev:
            break

    restack: list[str] = []
    in_str = False
    esc = False
    for ch in c:
        if esc:
            esc = False
        elif ch == "\\" and in_str:
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch in "{[":
                restack.append("}" if ch == "{" else "]")
            elif ch in "}]" and restack and restack[-1] == ch:
                restack.pop()

    return c + "".join(reversed(restack))


def _repair_truncated_json(text: str) -> list[str]:
    """生成被 max_tokens 截断 JSON 的修复候选集（保留半截字段或回退至上一完整分隔位）。"""
    start = text.find("{")
    if start < 0:
        return []
    fragment = text[start:]
    stack: list[str] = []
    in_string = False
    escaped = False
    comma_positions: list[int] = []

    for idx, ch in enumerate(fragment):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_string:
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
                if not stack:
                    return [fragment[: idx + 1]]
        elif ch == ",":
            comma_positions.append(idx)

    if not stack:
        return [fragment]

    results = [_close_json_stack(fragment)]
    for pos in reversed(comma_positions[-3:]):
        if pos > 1:
            results.append(_close_json_stack(fragment[:pos]))
    return results


def _escape_inner_quotes(text: str) -> str:
    """在标准 JSON 解析失败后，转义中文字符串内部的未转义英文双引号。"""
    out: list[str] = []
    in_string = False
    escaped = False
    n = len(text)

    def _next_non_ws(pos: int) -> tuple[str, int]:
        j = pos
        while j < n and text[j] in " \t\r\n":
            j += 1
        return (text[j], j) if j < n else ("", n)

    for idx, ch in enumerate(text):
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\" and in_string:
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            if not in_string:
                in_string = True
                out.append(ch)
            else:
                nxt, nxt_idx = _next_non_ws(idx + 1)
                is_closing = nxt in (":", "}", "]", "")
                if not is_closing and nxt == ",":
                    after_comma, _ = _next_non_ws(nxt_idx + 1)
                    is_closing = after_comma in ('"', "{", "[", "}", "]", "t", "f", "n", "-") or after_comma.isdigit()
                if is_closing:
                    in_string = False
                    out.append(ch)
                else:
                    out.append('\\"')
            continue
        out.append(ch)
    return "".join(out)


def _extract_and_parse_json(content: str) -> dict[str, Any]:
    """容错解析模型返回的 JSON（清理思考标签、Markdown围栏、尾逗号、内嵌引号、换行及截断恢复）。"""
    text = re.sub(r"<think>.*?</think>", "", str(content or ""), flags=re.DOTALL).strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    else:
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()

    first_brace = text.find("{")
    if first_brace > 0:
        text = text[first_brace:]

    candidates = [
        text,
        re.sub(r",(\s*[}\]])", r"\1", text),
    ]
    escaped_quotes = _escape_inner_quotes(text)
    candidates.append(escaped_quotes)
    candidates.append(re.sub(r",(\s*[}\]])", r"\1", escaped_quotes))

    for raw in (text, escaped_quotes):
        for repaired in _repair_truncated_json(raw):
            candidates.append(repaired)
            candidates.append(re.sub(r",(\s*[}\]])", r"\1", repaired))

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate, strict=False)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise ValueError(f"JSON format error. Snippet: {content[:200]}")


class DeepSeekService(BaseLLMService):
    def _call_api(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> dict[str, Any]:
        if not settings.enable_paid_api:
            raise ValueError("付费 API 调用未启用，请在环境变量中设置 ENABLE_PAID_API=true。")
        profile = model_config("prompt", settings)
        if not profile["key"]:
            raise ValueError("未找到 DEEPSEEK_API_KEY，请在环境变量中配置。")

        base_url = _normalize_openai_base_url(profile["url"])

        def _invoke(use_json_mode: bool, temperature: float, sys_msg: str) -> str:
            kwargs: dict[str, Any] = {
                "model": profile["model"],
                "api_key": profile["key"],
                "base_url": base_url,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "timeout": 300,
            }
            if use_json_mode:
                kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}
            llm = ChatOpenAI(**kwargs)
            messages = [
                SystemMessage(content=sys_msg),
                HumanMessage(content=user_prompt),
            ]
            print(f"[DeepSeekService] Requesting model={profile['model']} (max_tokens={max_tokens}, json_mode={use_json_mode})...", flush=True)
            t0 = time.time()
            response = llm.invoke(messages)
            content = response.content if response else ""
            if isinstance(content, list):
                content = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            print(f"[DeepSeekService] Response received in {time.time()-t0:.1f}s ({len(str(content or ''))} chars)", flush=True)
            return str(content or "")

        last_err: Exception | None = None
        for attempt, (use_json_mode, temperature, sys_msg) in enumerate(
            [
                (True, 0.65, system_prompt),
                (False, 0.25, system_prompt + "\n注意：只能输出一个合法 JSON 对象，字符串内部禁止出现未转义英文双引号 \" ，不要输出 ```json 标记。"),
            ],
            start=1,
        ):
            try:
                content = _invoke(use_json_mode, temperature, sys_msg)
            except Exception as invoke_exc:
                msg = str(invoke_exc).lower()
                if use_json_mode and (
                    getattr(invoke_exc, "status_code", None) == 400
                    or any(k in msg for k in ("response_format", "json_object", "json mode", "unsupported", "invalid parameter"))
                ):
                    try:
                        content = _invoke(False, temperature, sys_msg)
                    except Exception as fallback_exc:
                        last_err = fallback_exc
                        continue
                else:
                    raise
            if not content:
                last_err = ValueError("DeepSeek returned an empty response.")
                continue
            try:
                return _extract_and_parse_json(content)
            except ValueError as parse_exc:
                print(f"[DeepSeekService] JSON parse failed on attempt {attempt}. Raw: {content[:300]}")
                last_err = parse_exc

        raise last_err or ValueError("DeepSeek returned invalid JSON.")

    def plan_task(
        self,
        user_input: str,
        duration: int | None = None,
        style: str | None = None,
        platform: str | None = None,
        ratio: str = "9:16",
        generation_mode: str = "full_dynamic",
    ) -> dict[str, Any]:
        try:
            resolved_style = style if style in STYLE_TEMPLATES else DEFAULT_STYLE
            style_rules = _style_template(resolved_style)
            system_prompt = (
                "你是爆款短视频金牌导演与创意总监。你的任务是把用户的一句话需求转化为极具戏剧张力、"
                "充满反差感、节奏紧凑、绝不枯燥乏味的视频方案。坚决拒绝平铺直叙和平庸AI流水账。必须输出严格 JSON。"
            )
            if generation_mode == "auto_director":
                mode_guidance = (
                    "当前为全自主导演（auto_director）模式：严禁套用『每段必须10秒』『固定3段』机械模板！"
                    "未锁定总时长时，按内容密度自主决定最合理的总时长（12–75秒均可）与节拍数（2–8个节拍均可）："
                    "主力剧情/对峙/连贯戏每段 8–12 秒减少切缝，开场钩子/惊愕特写/反转点可用 4–7 秒紧凑切镜。"
                )
                beats_guidance = (
                    "plot_beats: 数组，每个包含 beat_name, description, suggested_duration（整数秒，推荐主力戏8-12秒、快切反应/钩子4-7秒）。根据叙事自然拆分 2–8 个节拍，不必凑3段。"
                )
            elif generation_mode == "storyboard_flow":
                mode_guidance = (
                    "当前为剧组单元自由流（storyboard_flow · 不逐秒卡拍）模式："
                    "如果用户已给出分段/分单元/多镜头剧本模板，必须完整保留用户的全部单元剧情、角色跨时空换装、光影色调阶段变化与每镜景别节奏（不得擅自删减合并成3段）！"
                    "如果用户只给了一句话梗概且未锁定总时长，按每 15 秒一个情感单元拆分 2–6 个单元（总时长 30–90 秒整数，每镜 3–6 秒）。"
                )
                beats_guidance = (
                    "plot_beats: 数组，每个节拍包含 beat_name, description, suggested_duration（整数秒 3–8 秒），"
                    "若原输入包含多单元分镜则逐条忠实保留所有单元与核心镜头节点（最多可输出 24 个节拍），否则拆出 6–16 个景别交替的影视镜头节拍。"
                )
            elif generation_mode == "native_10s":
                mode_guidance = "若生成模式为 native_10s，自动时长选 10 的整数倍，未指定则 30 秒。"
                beats_guidance = (
                    "plot_beats: 数组，每个包含 beat_name 和 description。native_10s 模式先确定最终 duration（用户指定时以指定值为准），"
                    "然后输出恰好 duration / 10 个节拍，每个节拍对应一个10秒段落的核心事件。"
                )
            else:
                mode_guidance = f"生成模式：{generation_mode}"
                beats_guidance = "plot_beats: 数组，每个包含 beat_name 和 description，输出3–5个节拍。"
            user_prompt = f"""\
用户原始需求：{user_input}
目标时长：{str(duration) + " 秒（固定）" if duration is not None else "从原句提取；未指定则由导演按内容张力自主决定（12-75秒）"}
{mode_guidance}
画面风格：{style or "自动选择"}（漫剧=漫画插画，短剧=写实影视）
发布场景：{platform or "通用视频"}
画面比例：{ratio}
生成模式：{generation_mode}

{_quality_rules(ratio)}

风格参考：
画面：{style_rules["visual"]}
配音：{style_rules["voice"]}
字幕：{style_rules["subtitle"]}

创意与叙事原则（反乏味、反套路、禁止一分钟站桩水时长）：
- 强化【核心戏剧冲突】与【身份/认知反差】：拒绝白开水流水账！40-75秒视频绝不允许整集只写“在桌前互相放狠话”这一件小事，必须拆出 3~4 个递增冲突回合（如：0~15s 破局逼宫 → 15~32s 动手加码冲突升级 → 32~48s 亮底牌/反杀打脸 → 48~60s 突发第三股压迫或更高反转断章）！
- 空间调度与情绪弧线强制变化：每相邻两拍必须有明显体位调度或景别反差（起身逼近、退步撞椅、猛拍桌面、抢步拦门、逼退墙角），且人物情绪必须有跨度（从轻蔑嗤笑 → 突然拍案暴怒 → 震惊血色褪尽/僵住 → 压低嘶哑发声），严禁全片维持同一个表情坐着对聊。
- 黄金前3秒抓人：开篇首秒直接抛出最具视觉冲击力或最具冲突性的动作情境，拒绝长篇大论的慢热铺垫。
- 结尾有力：给出一个意想不到的强反转或悬念钩子。

先识别 video_type，只能选 story（剧情）、explainer（科普/教程）、product（产品介绍）。
- story：铺垫、发展、解决，角色与道具保持一致。
- explainer：展示现象、逐步解释、回答开头的问题。可无角色，不编造事实。
- product：使用场景、功能演示、总结。只使用用户提供的产品事实，不捏造规格、效果或承诺。
- plot_beats 依次列出开头、核心内容、结尾。每段写明观众应看到或听到什么。短片首秒直接出现可见的事件、现象或演示结果，把背景交代放进随后动作或旁白。结尾给出结果及主体反应。
- 原生10秒模式的每个节拍写清同一事件的三个阶段：什么打破开场局面、哪个交互或发现推动变化、观众最后看到了什么结果。
- 用户明确指定的题材、时长、风格优先。未指定时按故事自然节奏决定合理时长（不固定凑30秒或10的倍数）。

返回 JSON 字段：
- video_type: story、explainer 或 product
- topic: 视频主题
- optimized_brief: 完整剧情梗概（200字以内的故事大纲）
- characters: 数组，每个角色包含 name, gender（"male" 或 "female"）, appearance（具体外貌描述）, personality, role_in_story
- duration: 整数秒
- style: 漫剧或短剧，用户未指定时按内容选择
- platform: 发布平台
- ratio: 画面比例
- generation_mode: 生成模式
- target_audience: 目标观众
- objective: 视频目标（吸引关注/引发讨论/情感共鸣/悬念追更）
- visual_world: 全片统一视觉世界设定（时代、地点、色调、氛围）
- {beats_guidance}
- conflict: 核心冲突是什么
- climax: 高潮或反转是什么
- ending_hook: 结尾钩子（悬念/反转/情绪高点）
- pacing: 节奏策略
- voice_style: 配音风格
- subtitle_style: 字幕风格
"""
            result = self._call_api(system_prompt, user_prompt, max_tokens=4000)
            result["source"] = "deepseek"
            result["user_input"] = user_input
            planned_duration = result.get("duration")
            max_auto_dur = 120 if generation_mode == "storyboard_flow" else 90
            result["duration"] = duration if duration is not None else (
                planned_duration if type(planned_duration) is int and 4 <= planned_duration <= max_auto_dur else 30
            )
            if generation_mode in ("paired_5s", "native_10s"):
                if duration is not None and (duration < 10 or duration % 10):
                    raise ValueError("This mode requires a duration divisible by 10 seconds")
                if duration is None:
                    result["duration"] = max(10, min(90, (result["duration"] + 5) // 10 * 10))
            if result.get("video_type") not in {"story", "explainer", "product"}:
                result["video_type"] = "story"
            if style:
                result["style"] = resolved_style
            if result.get("style") not in STYLE_TEMPLATES:
                result["style"] = resolved_style
            result.setdefault("platform", platform or "通用视频")
            result["ratio"] = ratio
            result["generation_mode"] = generation_mode
            return result
        except Exception as e:
            raise RuntimeError(f"DeepSeek plan_task failed: {e}") from e

    def shorten_voiceover(self, text: str, max_chars: int) -> str:
        result = self._call_api(
            "压缩配音句子。保留关键信息、语气和事实，不增加新信息。返回JSON对象，字段voiceover。",
            f"目标最多{max_chars}字。原句：{text}",
            max_tokens=300,
        )
        return str(result.get("voiceover") or "")

    def review_story_coverage(self, user_input, beats, scenes):
        return self._call_api(
            "检查短视频制作方案是否回应原始需求，开头、关键信息和结尾是否有画面或声音承载。"
            "静态图片不能承载必须看到运动才能理解的信息。只审查提供的文字与素材类型，不声称看过实际画面。返回JSON对象，字段 verdict 为 pass 或 needs_review，issues 为字符串数组。",
            json.dumps({"request": user_input, "planned_beats": beats, "scenes": scenes},
                       ensure_ascii=False, default=str),
            max_tokens=650,
        )

    def generate_script(self, task_plan: dict[str, Any]) -> dict[str, Any]:
        try:
            target_duration = int(task_plan.get("duration") or 60)
            resolved_style = str(task_plan.get("style") or DEFAULT_STYLE)
            characters_json = json.dumps(task_plan.get("characters", []), ensure_ascii=False, default=str)
            mode = task_plan.get("generation_mode", "full_dynamic")
            if mode == "native_10s":
                segment_rules = (
                    f"输出 segments，严格每段10秒，共{target_duration // 10}段。"
                    "每段填写 plot、duration、voice_start、voiceover、dialogue_lines。"
                    "多人对白填写 speaker、text、start，时间从本段第0秒起算，0 ≤ 时间 < 10。"
                    "voice_start 等于第一句时间。旁白段 dialogue_lines 为空。"
                    "content 展示同一份分段剧本。"
                )
            elif mode == "auto_director":
                segment_rules = (
                    "当前为全自主导演（auto_director）模式：输出 segments 结构化段落数组，"
                    "段落数量由戏剧节奏自主决定（2–8 段均可，严禁硬凑 3 段或强制每段 10 秒）。"
                    "每段填写 plot、duration（整数秒：主力对话与连续调度戏推荐 8–12 秒，强钩子/反应特写/反转冲击可用 4–7 秒，区间 4–12 秒）、"
                    "voice_start、voiceover、dialogue_lines。"
                    "多人对白填写 speaker、text、start（0 ≤ start < duration）以及可选 emotion（从 mocking / angry / cold_threat / shocked / urgent / calm 选择以驱动配音语调剧烈起伏）。"
                    "voice_start 等于第一句时间。旁白段 dialogue_lines 为空。content 展示同一份完整分段剧本。"
                )
            elif mode == "storyboard_flow":
                segment_rules = (
                    "当前为剧组单元自由流（storyboard_flow · 不逐秒卡拍）模式：输出 segments 结构化段落数组，"
                    "若项目方案/原始需求中已有明确分镜或15秒单元节奏，务必按其镜头粒度拆出 6–24 段（每段 3–8 秒整数，优先 3–5 秒换镜，合计贴合总时长）。"
                    "每段填写 plot、duration（整数 3–10 秒）、voice_start、voiceover、dialogue_lines；"
                    "多人对白填写 speaker、text、start（0 ≤ start < duration）以及可选 emotion（mocking / angry / cold_threat / shocked / urgent / calm）；"
                    "允许纯视觉情绪留白/转场镜（如闭眼入梦、无声对视落泪等）将 dialogue_lines 设为空数组且 voiceover 留空字符串 \"\"；"
                    "content 展示完整分段剧本。"
                )
            else:
                segment_rules = "segments 返回空数组，在 content 中编写完整画面和对应台词。"
            system_prompt = (
                "你是顶级短剧总编剧与分镜导演。你的剧本严格遵循『15秒四挡推进』、"
                "『每2-3句对白必插强视觉△物理动作（物理结果/身份反差/物证对抗）』与『台词去AI味规则』。"
                "严禁出现“眼神微动、微微一怔、心里一紧”等不可拍心理套话，严禁“然而、不仅如此、显而易见、命运的齿轮”等书面腔。必须输出严格 JSON。"
            )
            user_prompt = f"""\
项目方案：{json.dumps(task_plan, ensure_ascii=False, default=str)}

角色设定：{characters_json}

内容类型：{resolved_style}

剧本核心原则：
- 单集 15 秒四挡推进：
  1) 0~15s 抓人立事（强视觉动作开场 + 亮明具体数字/死线/压迫处境，严禁环境空镜与日常寒暄铺垫）；
  2) 15~45s 正面交锋（至少2轮筹码或动作拉扯）；
  3) 45~85s 亮牌翻转（当集结算，物证与大白话利害结论同轮直给，反派当场付出可见代价）；
  4) 85~110s 余波断章钩子（在动作半途或新危机破门瞬间戛然而止）。
- 拦截站桩纯对白与无效微动作：每 2~3 句对白必插一条强视觉 △（物理结果 / 身份反差 / 物证对抗），彻底封杀“眼神微动、微微一怔、心里一紧、心中五味杂陈”等镜头拍不出来的无效微动作。
- 台词去 AI 味：物证与大白话利害结论同轮直给（拒当谜语人）；专业/玄学设定词当句翻译成“多少钱/判几年/谁死谁活”；采用 3~6 字快刀短句与 8~16 字算账句快慢变速；禁用“然而、不仅如此、显而易见、命运的齿轮”等书面套话。

脚本要求：
- 按项目方案的 video_type 编写。剧情用角色行动和对白推进，科普按现象、解释、结论展开，产品介绍按场景、演示、总结展开。
- 保留原始需求中的关键信息。每一段都写出画面和对应的台词或旁白，结尾必须完成开头提出的叙事或说明目标。
- 拒绝原地复读与车轱辘吵架：第 1 段 0~5s 必须直接拍物证/动作/具体数字死线立事；中段至少包含【两轮实质筹码加码或反扑升级】（每轮必须出现新信息、新动作或新代价，严禁同一句威胁话换个说法重复）；结尾最后 3~5 秒再引爆核心反转钩子，绝不提前漏底。
- 紧凑对白与零冷场原则：每段 voice_start 控制在 0.2~0.7 秒内立刻起音（禁止留 1.5 秒以上无声呆立再开口）；每段台词/旁白总汉字数控制在 floor(duration * 3.0) ~ floor(duration * 3.8) 字之间，句子之间仅留 0.25~0.45 秒呼吸气口，严禁留下 2 秒以上的整段无声干瞪眼；多角色对手戏务必拆分写进 dialogue_lines，且 speaker 必须填写清晰纯角色姓名（禁止混写“旁白”代读角色对白）。
- 先写画面推进：首秒动作钩子 → 行动让局面发生变化 → 清晰结果和反应。10秒以内的短片尽量围绕一个事件，不用多秒站立、凝视或慢慢准备来铺垫。
- 当前模式的结构要求：{segment_rules}
- 旁白按每秒约 3.5 个汉字估算，标点不计；末尾保留约 0.4~0.8 秒稳定收束动作即可，不要故意留白几秒静音。旁白只补画外信息，关键结果出现前不提前揭晓。
- 科普避免虚构事实，产品介绍不添加未提供的规格与功效。

返回 JSON 字段：
- title: 本集标题（吸引眼球的短句）
- hook: 开头钩子（前3秒抓住观众的台词或画面描述）
- content: 完整剧本（旁白+对白混排）
- segments: native_10s 或 auto_director 模式输出结构化段落数组，其他模式返回空数组
- ending: 结尾钩子台词
- publish_copy: 发布文案（带悬念的短文案，引导点赞关注）
"""
            result = self._call_api(system_prompt, user_prompt, max_tokens=3600)
            result["source"] = "deepseek"
            if mode not in {"native_10s", "auto_director", "storyboard_flow"}:
                result["segments"] = []
            return result
        except Exception as e:
            raise RuntimeError(f"DeepSeek generate_script failed: {e}") from e

    def rewrite_script_with_judgment(
        self,
        task_plan: dict[str, Any],
        draft_script: dict[str, Any],
        judge_report: dict[str, Any],
    ) -> dict[str, Any]:
        """根据情节审稿与节奏诊断，结构化重写爆款短剧剧本与 segments。"""
        mode = str(task_plan.get("generation_mode") or "auto_director")
        target_duration = int(task_plan.get("duration") or 48)
        if mode == "native_10s":
            seg_constraint = f"保持 segments 总段数与每段 duration 严格为 10 秒（共 {max(1, target_duration // 10)} 段）。"
        elif mode == "storyboard_flow":
            seg_constraint = "segments 数量 4–24 段、每段整数 3–10 秒（主节奏 3–5 秒快切，允许极少数无台词纯情绪凝视留白段）。"
        else:
            seg_constraint = "segments 数量 4–7 段、每段整数 4–12 秒（总时长贴近剧情需要且节奏紧凑，40–65 秒优先）。"
        system_prompt = (
            "你是金牌爆款短剧总编剧兼剧本医生。你刚刚审完一份节奏拖沓、缺乏筹码升级、对白过于稀碎或存在无声干瞪眼的初稿。"
            "请你保留原有核心设定与角色姓名，彻底重构写出一版高张力、无废话、密集攻防、反转精准落底的结构化剧本。"
            "必须输出严格 JSON。"
        )
        user_prompt = f"""\
项目方案：{json.dumps(task_plan, ensure_ascii=False, default=str)}

待改写初稿：{json.dumps(draft_script, ensure_ascii=False, default=str)}

审稿诊断发现的病灶：{json.dumps(judge_report, ensure_ascii=False, default=str)}

重写必须逐条落实的硬要求：
1. 【砍掉原地复读】：绝不重复同一轮筹码或原地干吵。每 1 段都必须推动局面出现新物理变化（亮出新证/动作逼近/代价落下/局势反转）。
2. 【消灭无声干瞪眼】：每段 voice_start 控制在 0.2~0.7 秒；每段 spoken 总字数必须饱满（约为 duration * 3.0 ~ duration * 3.8 个汉字，如 8 秒段落配 24~30 字紧凑攻防短句），相邻 dialogue_lines 间隔 0.25~0.45 秒，绝不留 2 秒以上空画面发呆！
3. 【台词去 AI 味】：3~6 字快刀短句 + 8~16 字利害算账句变速，每 2~3 句插 `△` 可拍摄物理动作（content 中体现）；禁写眼神微动/然而/不仅如此/命运的齿轮。
4. 【角色分声线清晰】：dialogue_lines 中每句明确标出说话人纯姓名 speaker 与递增 start，不把角色台词混写成旁白。
5. 【反转严格落定最后 1 段】：前段层层压迫蓄势，结尾核心悬念或物证亮牌严格放在最后 1 段最后 3~5 秒引爆，绝不在倒数第二段提前剧透！
6. 【时长结构约束】：{seg_constraint}

返回严格 JSON 对象：
{{
  "title": "...",
  "hook": "...",
  "content": "...",
  "segments": [
    {{
      "duration": 8,
      "plot": "...",
      "voice_start": 0.3,
      "voiceover": "...",
      "dialogue_lines": [{{"speaker": "角色名", "text": "...", "start": 0.3}}]
    }}
  ],
  "ending": "...",
  "publish_copy": "...",
  "changes_made": ["改动说明1", "改动说明2"]
}}
"""
        result = self._call_api(system_prompt, user_prompt, max_tokens=4500)
        result["source"] = "deepseek"
        return result

    def generate_storyboard(
        self,
        script: dict[str, Any],
        target_duration: int | None = None,
        generation_mode: str = "full_dynamic",
    ) -> list[dict[str, Any]]:
        try:
            dynamic_hint = {
                "auto_director": (
                    "全自主导演模式（auto_director）：所有分镜 need_dynamic_video 都设为 true。"
                    "如果剧本含 segments，一个段落严格对应一个分镜并保持其规划好的 duration、对白与时间；"
                    "若无 segments，自由按内容节拍拆镜（不限定段数，主力对话与连贯动作 8–12 秒减少切缝，强钩子与面部反应特写 4–7 秒），单镜整数 4–12 秒。"
                    "由导演按画面因果自主挑选 transition_type（cut 换机位 / continue_action 连续动作接力 / scene_change 换场）。"
                ),
                "storyboard_flow": (
                    "剧组单元自由流（storyboard_flow · 不逐秒卡拍）：所有分镜 need_dynamic_video 都设为 true。"
                    "如果剧本含 segments，一个段落对应一个分镜（小数时长向上或向近取整数 3–10 秒，合计严格等于目标时长）；"
                    "按 15 秒情感单元拆成 3–4 个镜号（每镜 3–6 秒为主），且**相邻两镜强制交替不同景别（全景/中景/中近景/近景/特写/过肩）与不同拍摄视角**，严禁连续固定同构图；"
                    "允许不同单元切换阶段光影色调（如暖金课堂 → 低饱和灰褐 → 火把橙红 → 暖金结尾）与人物时代服装；"
                    "如遇跨时空闪白/入梦/梦醒切场，请设 transition_type='scene_change' 并在 transition_note 写明'闪白转场'；"
                    "纯无声情绪镜头可将 voiceover 设为空字符串 \"\" 并填好 sound_cues 氛围音效。"
                ),
                "native_10s": "每个故事段落恰好一条10秒动态视频。分镜数量严格等于总时长除以10。所有 duration=10、need_dynamic_video=true、transition_type=cut。每段用独立首帧，一次生成完整事件。旁白按该10秒段落编写，中间不断句重启。",
                "full_dynamic": "所有分镜 need_dynamic_video 都设为 true。",
                "paired_5s": (
                    "成片按10秒一部分组织，每部分恰好两段各5秒，所有段 need_dynamic_video=true。"
                    "奇数段 transition_type=cut，偶数段 transition_type=continue_action。"
                    "同一部分是同一场景、同一机位里的一个连续事件。前5秒触发并推进，后5秒继续并交代结果和反应。"
                    "第一段尾部停在可辨识的交接状态，第二段从该状态立即继续，不重播开头动作。"
                    "两段中间不得换场、切景别或改变方向。只在每10秒完成事件后切换场景或机位。"
                ),
                "key_scenes": "选择 2-4 个最适合动起来的关键分镜设为 true，其余 false。",
            }.get(generation_mode, "默认只在必要分镜设为 true，静态也能表达的分镜设为 false。")
            system_prompt = (
                "你是短剧/漫剧分镜导演。你要把剧本拆成连续的视觉镜头，"
                "每个镜头都必须能画成一张图并动起来。角色外貌必须全片一致。必须输出严格 JSON。"
            )
            feedback_block = ""
            if script.get("review_feedback"):
                feedback_block = (
                    f"\n[上一版分镜审片发现如下时序/剧透偏差，本版务必逐条修正]："
                    f"{json.dumps(script['review_feedback'], ensure_ascii=False)}\n"
                )
            user_prompt = f"""\
剧本：{json.dumps(script, ensure_ascii=False, default=str)}
{feedback_block}
画面风格：{script.get("visual_style") or "跟随剧本设定"}。短剧为真人演员实景拍摄，使用自然五官比例、皮肤纹理和实物材质；漫剧为漫画插画。角色名和故事题材不能改变所选媒介。
目标时长：{target_duration or 60} 秒
生成模式：{generation_mode}
{dynamic_hint}

分镜要求：
- 镜头数量、时长和衔接方式以当前模式要求为准，所有 duration 相加必须严格等于 {target_duration or 60} 秒。未规定单段时长的模式使用 4–12 秒。
- 剧本含 segments 时，一段对应一个分镜，按原顺序保留该段事件、台词、说话人和时间，不拆分或把台词移到另一段。
- 每拍只突出一个观看重点。双人交互可随动作和反应交接焦点，保持人物位置与拍摄轴线。
- 复杂交接优先改成借桌面等支撑面完成，或把交接前后分配给已有的相邻段落。关键对白时保留说话者或反应主体的面部。
- 若镜头从接物者取物开始，把道具已落稳、递物者已松手、接物者尚未接触写入 start_state，并交代支撑面。
- reaction_focus 写明关键配音时观众必须看清的具体主体：剧情人物的反应、产品演示结果或科普现象。只写本镜已经存在的可见主体。
- 有注册角色时，character_ids 只填写本镜可见角色的原始character_id，不得自造ID。画外说话者不放入可见角色列表，空镜返回空数组。对白speaker使用已绑定角色的原名。
- 先用一个贯穿本段的可见事件串起起因、动作、结果和反应，再写 visual_description。后一个动作必须由前一个动作的可见结果或台词触发。预留交互完成和人物反应的时间，删去挤占揭晓或反应的次要动作、额外转场和结尾字卡。
- 重要道具每次换手、落到支撑面、翻面或离开画面，都交代动作过程及完成后的状态。情节依赖观众看见的线索，提前选好能看清线索的构图。
- transition_type 为 cut（切镜或换机位）、continue_action（同机位同构图连续动作，必须继承上一镜尾帧）、scene_change（明确换场）。首镜必须为 cut。
- start_state 和 end_state 写明角色位置、朝向、持物手、道具位置及动作阶段；连续动作必须使本镜 start_state 与上镜 end_state 一致。
- 每个分镜时长至少 4 秒，不足 4 秒的补到 4 秒。
- 每个分镜服务同一条内容主线。第一镜首帧构图就要看清动作对象，首秒发生可见变化。每个镜头须推进事件，保留结果和反应的时长。
- 景别由内容决定。道具接触、按钮按下等细节要让主体和接触点清晰可见。需要换景别或插切第二画面（例如门缝推开、手机震动特写）时，必须单独拆出一个独立分镜，绝不能在同一分镜内部写“突然切到门/切手再切回”，避免视频模型自发硬切鬼影叠化。
- visual_description 写清一个可见事件的起因、主要动作和结果。禁止连续两镜都只是双方坐在原地动嘴！同时保持单一镜头内肩臂结构完整与统一景别；长镜（7–12秒）如果在前半段说完台词，后半段必须安排对方的具体动作反应、肢体逼近或接话台词填满表演（严禁结尾剩2秒以上互盯傻站着）；且谁发主声、镜头焦点或口型动作就落在谁身上。
- visual_description 必须包含角色的外貌特征（发型、服装、体型），确保每个镜头角色可识别。
- character_state 必须写出本镜【起始情绪 → 突发触发后的结尾情绪跨度】（绝不能全程面无表情）。
- scene_continuity 必须说明如何继承上一镜头的角色位置、表情、道具、场景和色调。
- transition_note 必须说明最后一帧怎么接下一镜头（动作延续/切景/表情变化）。
- shot_type 用影视镜头语言：特写、近景、中景、全景、过肩、俯拍、仰拍等。
- voice_start 写台词相对本段开始的秒数，台词在所描述的动作之后出现，必须留够朗读时间。sound_cues 写发生时间和音效名，仅支持 footstep、impact、water、paper、wind；凡画面出现拍桌/关门/碰撞（impact）、走动逼近/踩踏（footstep）、翻甩文件/撕纸（paper）、倒茶/溅水（water）或压迫冷风/氛围转折（wind），主动填写 1 条精准的音效时间点（0 ≤ start < duration），无明显声源再返回空数组。
- 对“最后3秒/末尾才发生”的关键反转或进门线索，严格放在最后 1–2 个分镜中呈现，严禁提前剧透写进中段分镜末尾。
- voiceover 写这个镜头对应的旁白或角色台词，这是直接送入 TTS 配音的文本。
- 如果剧本是多人对白，必须保留关键原对白的说话人、意思和顺序，不改成独白。为该分镜输出 dialogue_lines，按出声顺序填写 speaker、text（纯台词）、start（本段开始后的秒数）。按该分镜 duration 安排对白，超出时删去次要对白，给每句话留出自然读时。voiceover 同步填写这些台词拼接后的纯文本。纯旁白场景的 dialogue_lines 为空。
- voiceover 禁止出现“李白：”“旁白：”“某某说：”这类角色标签，禁止包含括号、音效或动作指示（如“（大笑）”、“【音效】”）。
- 没有已定稿台词时，按 max(0, floor((duration - voice_start - 0.5) * 3.5)) 估算旁白汉字数上限，标点不计，保留收尾空间。
- subtitle 直接使用 voiceover 原文，不再缩写。
- 禁止空泛词：震撼、电影感、高质量、氛围感、未来感大片、引发思考。
- 空间站位声明（blocking）：每个分镜必须显式写明谁在画左/画右、依托什么物件、身体与视线朝向、锁死 180° 轴线及每人身后的固定背景物理参照物（如门框、窗台、书桌沿、立柱），防止人物未动而背景漂移。单人镜严禁在 visual_description 中点名画外其他角色姓名（防AI克隆双胞胎或硬塞画外人）。
- 跨镜衔接与景别节奏：9:16 竖屏优先靠抢白台词钩、未完动作打断或信息反差切换（少用拖沓纯空镜）；16:9 横屏可用短反应镜、环境空镜与斜轴纵深推镜。段内避免连续3镜同景别（默认1级景别递进）；continue_action 接缝必须保持 0 级同档景别，且前镜末段禁用极大远景。跨镜新增角色须写明前1.5秒从何侧入画或先声后人，禁止无交代凭空出现。
- 小道具单实例与朝向锁：手机、信封、证件、照片等带正反面的关键道具，须写清屏幕/字迹面朝向（朝手心还是朝镜头），拿起后原桌面位置须变空。
- 段缝衔接模式（transition_mode）：从 freeze_handoff（末帧定格承接）、angle_cut（换机位硬切）、empty_shot（空镜/道具过渡）、whip_morph（动势转场）中选填。

每个分镜返回字段：
- scene_id
- scene_index
- duration
- shot_type
- blocking（空间站位声明与180°轴线锁）
- transition_mode（freeze_handoff / angle_cut / empty_shot / whip_morph）
- voiceover
- voice_cue（台词应该落在的可见动作，比如抬眼直视镜头）
- voice_start
- sound_cues（每条 name 与 start）
- subtitle
- visual_description
- character_state
- scene_continuity
- transition_note
- audio_hint
- camera_motion
- need_dynamic_video
- transition_type
- start_state
- end_state
- reaction_focus
- character_ids

返回 JSON：{{"scenes": [...]}}
"""
            result = self._call_api(system_prompt, user_prompt, max_tokens=5200)
            scenes = result.get("scenes", [])
            for scene in scenes:
                scene["source"] = "deepseek"
            return scenes
        except Exception as e:
            raise RuntimeError(f"DeepSeek generate_storyboard failed: {e}") from e

    def polish_dialogue(
        self,
        scenes: list[dict[str, Any]],
        target_duration: int | None = None,
    ) -> list[dict[str, Any]]:
        try:
            clean_scenes = [
                {
                    "scene_id": scene.get("scene_id"),
                    "scene_index": scene.get("scene_index"),
                    "duration": scene.get("duration"),
                    "voice_start": scene.get("voice_start", 0),
                    "speaker": scene.get("speaker"),
                    "voiceover": scene.get("voiceover"),
                    "subtitle": scene.get("subtitle"),
                    "visual_description": scene.get("visual_description"),
                    "character_state": scene.get("character_state"),
                }
                for scene in scenes
            ]
            system_prompt = (
                "你是短剧台词精修导演。请对每个分镜的配音文本进行去AI味精修："
                "1) 砍掉书面连接词（严禁然而、不仅如此、显而易见、命运的齿轮）；"
                "2) 3~6字短句与8~16字大白话利害算账句快慢变速，单句不超过15字；"
                "3) 物证与大白话利害结论同步直给，把专业设定词当句翻译成多少钱/判几年/谁输谁赢。必须输出严格 JSON。"
            )
            user_prompt = f"""\
分镜列表：{json.dumps(clean_scenes, ensure_ascii=False, default=str)}
目标总时长：{target_duration or "跟随分镜 duration"} 秒

润色要求：
- 台词必须鲜活口语化、有情绪张力与戏剧色彩：融入人物特有的性格语气与反差梗，朗朗上口、节奏有力，严禁干瘪平淡的书面语与正经说教。
- 以 duration 减 voice_start 再减 0.5 秒计算可朗读时长。汉字数上限按该时长乘 3.5 后向下取整，最低为零，标点不计。根据实际停顿进一步缩短，让关键台词与动作或反应对齐。
- 原句明显偏短时，补一层画面看不出的情感、人物记忆或因果。转折前可以安排一次自然停顿。不要复述已经看得见的动作。只有剧情明确需要沉默时才让旁白提早收住。
- 每个输入分镜都必须返回一条结果，scene_id 必须保持不变。
- 根据 duration 减去 voice_start 的剩余时长写台词。台词用一口气能念完的短句。
- speaker 单独填写说话人，例如“李白”“旁白”“考官”；不确定时填“旁白”。
- voiceover 是直接送入 TTS 的文本，禁止出现“李白：”“旁白：”等角色标签，严禁包含括号、音效或动作指示词（如“（兴奋地）”）。
- subtitle 直接使用 voiceover 原文，不再缩写。
- 不要新增角色、不要改剧情事实、不要解释输出。

返回 JSON：{{"dialogues": [
  {{"scene_id": "...", "speaker": "...", "voiceover": "...", "subtitle": "..."}}
]}}
"""
            result = self._call_api(system_prompt, user_prompt, max_tokens=3200)
            dialogues = result.get("dialogues", [])
            for dialogue in dialogues:
                dialogue["source"] = "deepseek"
            return dialogues
        except Exception as e:
            raise RuntimeError(f"DeepSeek polish_dialogue failed: {e}") from e

    def generate_image_prompts(
        self,
        scenes: list[dict[str, Any]],
        ratio: str = "9:16",
        style: str | None = None,
        visual_bible: dict[str, Any] | None = None,
        per_second: bool = True,
    ) -> list[dict[str, Any]]:
        try:
            resolved_style = style if style in STYLE_TEMPLATES else DEFAULT_STYLE
            style_rules = _style_template(resolved_style)
            ratio_desc = "竖屏9:16，1080x1920构图" if ratio == "9:16" else "横屏16:9，1920x1080构图"
            clean_scenes = [
                {
                    "scene_id": scene.get("scene_id"),
                    "scene_index": scene.get("scene_index"),
                    "duration": scene.get("duration"),
                    "voiceover": scene.get("voiceover"),
                    "subtitle": scene.get("subtitle"),
                    "visual_description": scene.get("visual_description"),
                    "shot_type": scene.get("shot_type"),
                    "voice_start": scene.get("voice_start", 0),
                    "dialogue_lines": scene.get("dialogue_lines") or [],
                    "reaction_focus": scene.get("reaction_focus"),
                    "character_state": scene.get("character_state"),
                    "scene_continuity": scene.get("scene_continuity"),
                    "transition_note": scene.get("transition_note"),
                    "audio_hint": scene.get("audio_hint"),
                    "camera_motion": scene.get("camera_motion"),
                    "blocking": scene.get("blocking"),
                    "transition_mode": scene.get("transition_mode"),
                    "character_ids": scene.get("character_ids") or [],
                    "continuity_handoff": scene.get("continuity_handoff"),
                    "camera_section_number": scene.get("camera_section_number"),
                    "camera_section_offset": scene.get("camera_section_offset"),
                    "previous_segment_context": scene.get("previous_segment_context"),
                    "start_state": scene.get("start_state"),
                    "end_state": scene.get("end_state"),
                    "transition_type": scene.get("transition_type"),
                }
                for scene in scenes
            ]
            system_prompt = (
                "你是 AI 图片提示词导演和图生视频动作导演。你的输出会直接交给 Qwen-Image 图片模型和当前配置的视频生成模型。"
                "必须把每个分镜写成清楚、可生成、可运动、可衔接的提示词。必须输出严格 JSON。"
            )
            style_hint = "漫画/插画风格" if resolved_style == "漫剧" else "写实影视风格"
            legacy_camera_rules = ""
            if any(scene.get("camera_section_offset") == 0 for scene in clean_scenes):
                legacy_camera_rules = """
历史双段续接规则：首段返回 camera_plan，包含 purpose、focus、framing、viewpoint、
opening、closing、handoff、movement、direction、move_start、move_end。
movement 只能是 fixed/track/pan/tilt/push/pull，时间用整组秒数，固定时填 0 和 0。
后段沿用同一运镜计划，并从上一段结束状态继续。逐秒 camera 与整组运镜一致。
"""
            user_prompt = f"""\
分镜列表：{json.dumps(clean_scenes, ensure_ascii=False, default=str)}

{_quality_rules(ratio)}

全片角色与世界设定（仅使用该镜头实际出现的角色）：{json.dumps(visual_bible or {}, ensure_ascii=False, default=str)}
内容类型：{resolved_style}
画面风格：{style_hint}
风格以此处选择为准。写实影视必须描述真人实拍画面，角色与世界设定仅用于继承人物、服装和环境，不能改变指定的视觉媒介。
风格模板：
画面：{style_rules["visual"]}
配音：{style_rules["voice"]}
构图：{ratio_desc}

请为每个分镜生成给 Qwen-Image 和当前配置的视频生成模型使用的提示词。
如果分镜提供 previous_segment_context，将其作为上一段已经写好的剧情、blocking 站位和 final_second 收尾物态（手拿什么、道具落在何处、人物朝向与伤痕状态）。按当前分镜设计新的首帧，严格延续仍相关的人物左右轴线、手持/放下道具的归属位置（上一镜放下、甩出或转交的道具绝不可在本镜首帧无故捏回原主手里）和剧情因果。允许按分镜切换场景或机位，不重演上一段已经完成的动作，不把计划收尾当作实际尾帧。只输出当前分镜的提示词和逐秒描述。

image_prompt 要求（采用三层工艺分离与八段式结构）：
- 首帧的角色姿态、道具状态和相对位置以 start_state 为唯一时序依据，与第0秒的起始画面一致。visual_description 仅提供静态场景与角色信息，剥离动作过程、后续姿态、接触结果及事后痕迹。只描述此刻已经存在、能直接画出来的画面。
- **三层分离与防变脸原则**：如果全片角色已绑定参考图（visual_bible 中包含 bound_characters 或 series_context 中的 characters 带 reference_path），image_prompt 中对于已绑定角色严禁长篇重描五官与服饰细节（否则生图模型会覆盖参考图），只写 `[CHARACTER] 角色名(@角色名参考图) — same identity, 画面方位与朝向`，把文字重心留给 `[BACKGROUND]`、`[ACTION]`、`[SCENE]`、`[CAMERA]`、`[LIGHT]`、`[TEXT]`、`[STYLE]`；单镜具名清晰人脸严格 ≤ 2 人，超出人物描述为远景虚化剪影（blurred silhouette in shallow DoF bokeh）。
- 首帧防呆立与势能起手：非 continue_action 的动态镜头，首帧尽量构图在“动作刚启动 0.1 秒、视线与重心已蓄势”的预备态（而非直立木桩照），并遵循每镜透出的 `continuity_handoff`（新增角色入画来源、180°轴线背景锚点与景别建议）。
- 严格区分首帧分镜与定妆照光影：禁用“纯白背景、影棚平光”，改为明确物理主光源方向与冷暖色温对比 + 侧逆轮廓边缘光（rim light）。
- 如果 start_state 是桌面中转后、取物之前，画清道具和支撑面的接触、递物者已经撤开的手，以及接物者尚未碰到道具的双手。若放置动作在本镜内发生，首帧仍保留原持有状态。
- 必须注明画面风格（{style_hint}），确保全片风格统一。
- 只描述首帧中实际可见的角色。空镜和纯物品特写保持无人，手部特写仅描述入画部位。禁止为了重复全片角色设定而新增人物。
- 不要出现 4K、8K、超高清、极致细节、超高解析度。
- 不要要求图片里生成大段文字；如需要文字，只允许短词、标签、数字或图标。

全片叙事与动作编排：
- 在 continuity_note 中重复固定角色造型、主道具状态和场景方向。每个10秒部分的第一段写明上一部分留下的线索如何引出本部分，使用视线、动作结果或声音引导切换。
- 先在内部排好整段动作因果链，再把动作连续切分到逐秒描述。同一个动作可以跨多秒，按时间写出准备、进行、完成与反应。动作仍在进行时，下一秒先续上同一运动方向和接触状态。每秒可以保持动作或构图，画面重点随事件推进。
- 对照已定的对白时间安排画面：台词所依赖的线索先进入可辨认的景别，揭晓句说出时保留说话人或听者的可见反应，句末留出反应的画面时间。片段的结尾优先停在事件结果或人物反应上。
- 生图和视频提示词仅描述可拍摄的画面。对白用来安排动作与反应的时机，不把台词原文、字幕、标题、气泡或黑底字卡写入画面提示词。情节所需的道具内文字可单独描述。
- 按 dialogue_lines 中的 speaker、text、start 安排说话者和听者反应。start 是段内计划秒点。关键对白期间留住能看清表情的构图，把转身和焦点转移留到反应之后。
- 已有 reaction_focus 时按它安排关键配音的观看重点。说话者在画面内时双唇自然轻微开合、下颌微动；倾听者自然闭口。画外旁白不要求画面人物同步开口。
- 禁止把后期字幕、字卡或标题安排写进 camera、action、起止状态、衔接说明或收尾构图。结尾保留剧情画面。
{legacy_camera_rules}

video_prompt 要求：
- 这是给图生视频模型的动作提示词，必须基于首帧图片（@首帧）自然演进，严格保持人物面孔、服装颜色及光影一致。
- 先确定观众需要依次看清的起因、关键交互和结果，再分配具体秒点。动作幅度、速度和停顿服从剧情。情绪通过当前景别能看见的表情、视线、手势或姿态呈现。
- 运镜写清起始构图、拍摄侧、跟随目标、方向和落点。注明是哪一个可见动作触发了取景或焦点变化。关键接触期间保留双方和接触点，构图稳定时写清画面要保持的范围。
- 每秒只突出一个观看重点。人物互动时允许焦点沿因果转到另一人或关键道具，保持各人的左右位置、视线与拍摄轴线。只有动作确实影响环境时才写可见的环境反馈。
- 逐秒 action 描述从哪里到哪里、哪一侧肢体发力、接触点、速度和方向、接触后的道具反馈、视线或重心如何变化；涉及物理接触按“靠近 → 触碰点 → 施力方向 → 物体/身体位移反应 → 落定稳定”先后顺序写清（未触碰前绝不提前后仰）。反应镜头按“感官触发 → 停顿半拍（定眼神/停手） → 眼眉下颌肌肉微动渗出 → 身体决断”演绎。
- 末拍收束缓冲：每个分镜最后一秒的后 0.6 秒须落定在稳定可读姿态与视觉终态（若下镜为 continue_action 则严禁大幅甩头或快速出画，确保尾帧清晰可接）。
- start_state/end_state 写出画面内位置、朝向、支撑脚或持物手，以及重要道具的位置与状态。下一秒延用上一秒的结束状态。
- action 的第一个动作必须能从该秒 start_state 直接发生，最后一个动作必须产生该秒 end_state。重要道具的位置、持物人、持物手、朝向和接触面发生变化时，把靠近、接触、承重转移与松手按实际顺序写入对应秒；秒末仍在接触就写明保持的接触点。人物转身、出画或改变视线时交代可见的过程。
- camera 的起始景别、拍摄侧和取景目标承接上一秒的结束构图。运镜跨秒时，写清当前秒推进到哪里及下一秒从哪里接上。关键交互和揭晓期间保持接触点或反应主体可见，不安排突然切镜或让关键主体无故出画。
- continue_action 的首秒延续上一段尾部的姿态、运动方向、光线和机位，从未完成的动作继续。已经落下、打开或离开画面的东西保持其结果，不要复位。
- video_prompt 只概括首帧继承、镜头运动、关键动作目标与结尾衔接。逐秒时间和动作只写在 motion_beats，避免同一时间轴在两个字段里重复或冲突。
- 必须按逐镜递进写清强烈的情绪弧线与身体发力动态：相邻秒必须体现动作发力阶段和面部微表情剧变（如猛然拍桌站起、倾身压近、骤然僵住瞳孔微缩），避免整镜从头到尾站桩僵直微呼吸。
- 不要只写"缓慢运动、自然运动、画面更生动"。
- 不要突然换场景、换主体、换服装、换道具，不要加入无关角色。
- **六条运镜与动作稳定性约束（强制执行）**：
  1) 严禁文学比喻（禁写“端坐如钟、目光如炬、心如刀绞”，防AI凭空生成铜钟或火把，只写纯物理动作）；
  2) 单人镜严禁点名画外角色（单人特写只写“目光投向画右前方画外”，绝不出现画外角色名字，防AI克隆双胞胎）；
  3) 一镜一视线（短镜头只锁死1个视线落点，严禁眼球左右乱飘）；
  4) 情绪必须拆解为面部肌肉与呼吸动作（如“瞳孔骤缩、下颌绷紧、胸口起伏”）；
  5) 反光道具零光效描写（玉佩/金属只写“哑光实心物理材质原色”，禁写“泛着幽光/映出火苗”防变灯泡）；
  6) 句尾环境微锁（每镜结尾保持“；身后固定背景结构与主光色温保持不变”）。
  7) **单镜一镜到底锁（最高优先）**：同一分镜所有秒的 `motion_beats.camera` 必须保持同一个连续机位景别（如全段都用“中近景缓慢跟推”），严禁在同一条片段里写“0-2s 中景 -> 2-4s 手部特写 -> 4-6s 切木门”，否则视频模型会触发段内硬切导致重影叠化与道具错乱！需要特写反打或看门外请留给下一条独立分镜；
  8) 严禁要求屏幕/纸面生成可读长文字（禁止拍“信封/合同/手机显示清晰成段小字”，防乱码错字；文件类道具用红印章/牛皮纸角标替代，关键内容由配音和后期字幕承载）；全程严禁AI自发生成画面内字幕与文字气泡。
- **动作描述必须委婉、暗示性**，禁用暴力/战争词汇："杀"、"斩"、"刺"、"砍"、"箭穿"、"纵火"、"焚烧"、"挥剑"、"拔剑"、"打斗"、"激战"、"厮杀"、"受伤"、"流血"。用"手势变化"、"快速转身"、"高举手臂"、"用力投出"、"握紧拳头"、"抬手示意"、"身体前倾"等中性动作描述代替。

每个分镜返回字段：
- scene_id
- blocking（空间站位声明，明确画左/画右、依托物、朝向与180°轴线锁）
- transition_mode（freeze_handoff / angle_cut / empty_shot / whip_morph）
- image_prompt
- image_negative_prompt: 针对该首帧最可能出现的结构、位置或材质错误，列出具体可见缺陷。只排除不需要的额外肢体、错误接触、文字、水印或风格偏差，按当前画面取舍。
- video_negative_prompt: 针对该镜头的动作和运镜，列出具体的时序缺陷，包括道具瞬移、接触穿模、角色外观漂移、动作倒放或非计划的抖动；排除画面浮现字幕、标题或字卡，保留情节所需的道具内文字。不要排除分镜要求的静止、运动或效果。两个负面词字段用简短短语，不堆砌画质评分词。
- video_prompt
- motion_beats: 对象数组，每条包含 start、end、subject、camera、start_state、action、end_state。{"按片段本地时间从0秒逐秒写到 duration，时间连续且不重叠。" if per_second else "【自由流·不逐秒模式】不要机械拆成每秒一条！每个分镜只返回 1–2 条完整连贯的连续时间块（例如 0 到 duration 一整条，或因果清晰的前后两半段，严格连续覆盖 0 到 duration），让视频模型顺滑完成整个连贯动作与单一运镜。"}每条都写完整可见画面：camera 写景别、机位、取景重点及运镜；start_state 写人物在画面中的方位、姿态、视线和相关道具的位置；action 写动作方向、肢体和接触点如何变化；end_state 写段末可见结果。输出只描述当前分镜的实际内容，不包含示例、类比、占位词或模板说明。
- motion_beats 的 start 和 end 必须使用 JSON 数字，单位为秒，不加引号或单位后缀。
- {"每个动态分镜必须返回条数与自身 duration 完全相等的逐秒描述（如 6 秒返回 6 条、8 秒返回 8 条、11 秒返回 11 条，严格从 0 到 duration 秒）。每秒的 camera、start_state、action、end_state 合计精炼控制在 30–55 个汉字，突出构图、发力部位、接触过程和秒末结果（总提示词避免臃肿稀释模型注意力）。" if per_second else "每个动态分镜返回 1–2 条连贯时间块覆盖 0 到 duration 秒（如 start=0, end=duration），禁止按 1 秒强行切碎！每条写清景别构图、连贯运镜方向、核心发力与眼神表情弧线，同时严格保留跨时空换装与当前单元的光影色调要求。"}共同的外貌、服装、场景和光线放在 continuity_note 中。
- 画面中要看清什么，先在 video_prompt 指明接触点和景别；每秒写清入画侧、接触面的相对位置、力的方向、动作结果和可见反应。不要用气氛词代替位移。
- 图片只能画出动作开始状态，不得提前呈现动作结果；末段结果必须符合 end_state。
- first_frame_focus: 首帧最重要的可见主体
- last_frame_for_transition: 最后一帧停在哪里，方便接下一镜
- continuity_note: 需要保持一致的角色外貌/道具/场景/色调说明

返回 JSON：{{"prompts": [...]}}
"""
            result = self._call_api(system_prompt, user_prompt, max_tokens=14000)
            prompts = result.get("prompts", [])
            for prompt in prompts:
                prompt["source"] = "deepseek"
            return prompts
        except Exception as e:
            raise RuntimeError(f"DeepSeek generate_image_prompts failed: {e}") from e

    # ====== 预设故事模板 ======

    STORY_TEMPLATES = {
        "治愈晚风": {
            "title": "治愈晚风",
            "tag": "9:16竖屏 / 温柔氛围感",
            "scene": "少女蹲在江边石阶，一只橘猫靠近她，她抚摸猫咪后望向落日，心情从委屈变得温暖。",
            "segments": [
                "少女蹲在江边石阶，低头揪着裙角抿嘴委屈，眼眶泛红，晚风把碎发吹糊脸颊",
                "一只橘猫慢慢蹭她手背，少女睫毛颤了颤，缓缓抬头，嘴角轻轻扬起，伸手顺着猫背抚摸",
                "她侧头望向落日，眉眼舒展，浅浅笑出梨涡，指尖搭在猫咪头顶不动",
            ],
            "visual": "暖黄落日余晖，江面波光粼粼，温柔治愈色调",
            "camera": "开场近景侧拍少女表情，镜头缓慢下摇到橘猫蹭手，然后缓缓上摇到少女远望落日的侧脸",
        },
        "都市失落": {
            "title": "都市失落",
            "tag": "16:9横屏 / 情绪短片",
            "scene": "西装男子站在天桥上，反复看手机消息却等不到回复，疲惫失落。",
            "segments": [
                "西装男生站天桥，攥紧手机，眉头紧锁，反复刷新聊天框，嘴角往下垮，眼底满是疲惫",
                "手机屏幕熄灭，他垂下手，低头长叹，肩膀垮下来，双眼无神望着车流",
                "抬手揉了揉发红的眼尾，侧脸落寞，静静靠着护栏发呆",
            ],
            "visual": "冷蓝灰调，城市夜景，天桥路灯昏黄，情绪低落氛围",
            "camera": "从中景跟拍男生看手机，推进到手机屏幕熄灭特写，拉远到全景他孤独靠在护栏上的剪影",
        },
        "少年心动": {
            "title": "少年心动",
            "tag": "9:16竖屏 / 短视频爆款",
            "scene": "走廊里男生偶遇心仪的女生，擦肩而过后的心动反应。",
            "segments": [
                "走廊男生转头撞见迎面走来的女生，瞬间僵住，瞳孔微放大，下意识攥紧手里书本，耳根飞快泛红",
                "女生冲他轻轻笑了下擦肩而过，男生呆呆停在原地，目光跟着她背影，嘴角不受控上扬",
                "低头偷偷傻笑，用书本挡住半张脸，脚尖轻轻蹭地面",
            ],
            "visual": "校园走廊，午后阳光从窗户斜射，暖白明亮色调",
            "camera": "正反打：先拍男生僵住的表情，摇移到女生擦肩，再切回男生目送背影的镜头",
        },
        "微悬疑惊悚": {
            "title": "微悬疑惊悚",
            "tag": "9:16竖屏 / 短惊悚",
            "scene": "女生独自翻旧木箱，发现一张和自己一模一样的照片，身后衣柜突然自动打开。",
            "segments": [
                "女生独自翻旧木箱，翻到一张陌生黑白照片，好奇歪头细看，表情平静放松",
                "看清照片人脸和自己一模一样，她瞳孔骤缩，猛地后仰后退，嘴巴微张露出惊恐",
                "身后衣柜柜门吱呀自动推开，女生浑身僵硬，不敢回头，浑身微微发抖",
            ],
            "visual": "暗调冷色，只有台灯局部照明，阴影强烈，恐怖氛围",
            "camera": "特写照片和女生表情变化，快速拉远展现衣柜方向，定格在女生僵硬的背影和敞开的衣柜之间",
        },
        "热血瞬间": {
            "title": "热血瞬间",
            "tag": "16:9横屏 / 燃向",
            "scene": "少年输掉比赛后心有不甘，在朋友的鼓励下重新燃起斗志。",
            "segments": [
                "少年输掉比赛垂头弯腰，咬着下唇，满脸不甘，双拳紧紧攥起",
                "朋友伸手拍他肩膀鼓励，少年缓缓抬头，眼神从黯淡变得锐利坚定",
                "松开拳头，挺直脊背，目光望向赛场前方，露出不服输的冷硬神情",
            ],
            "visual": "赛场侧光，明暗对比强烈，汗水反光，热血氛围",
            "camera": "低角度仰拍少年不甘的表情，镜头缓缓推进到朋友拍肩的手，再拉回到少年重新挺直的背影",
        },
        "古风离别": {
            "title": "古风离别",
            "tag": "9:16竖屏 / 古风情感",
            "scene": "渡口边女子送别心上人，不舍却无法挽留，最终独自落泪。",
            "segments": [
                "女子站渡口，攥着对方袖口不肯松手，眼眶蓄满泪水，鼻头发酸",
                "对方缓缓抽开衣袖转身登船，女子嘴唇颤抖，强忍眼泪不让落下",
                "小船离岸，她抬手捂住嘴，终于低头落下眼泪，肩膀轻轻抽动",
            ],
            "visual": "黄昏渡口，暖黄带灰调，水面薄雾，古风凄美色调",
            "camera": "中景双人镜头，缓慢推进到女子攥袖口的手部特写，然后拉远到全景：船远去，女子独自立在渡口",
        },
    }

    def generate_story_prompt(self, story_id: str) -> dict[str, str]:
        """预选故事 → DeepSeek 生成完整提示词"""
        template = self.STORY_TEMPLATES.get(story_id)
        if not template:
            raise ValueError(f"Unknown story: {story_id}")

        segment_text = "\n".join(
            f"  {i+1}. {seg}" for i, seg in enumerate(template["segments"])
        )
        description = (
            f"场景：{template['scene']}\n"
            f"分镜：\n{segment_text}\n"
            f"画面风格：{template['visual']}\n"
            f"镜头运动：{template['camera']}"
        )
        return self.generate_single_prompt(description)

    def generate_single_prompt(self, description: str) -> dict[str, str]:
        """给 ComfyUI 测试页用：一句话描述 → positive_prompt + negative_prompt"""
        try:
            system_prompt = (
                "你是 Wan2.2 文生视频模型的提示词专家。用户给你一段画面描述，你生成详细的英文正向提示词和反向提示词。\n\n"
                "========== 正向提示词格式（必须严格照做）==========\n"
                "按以下 3 段组织，每段对应约 3-4 秒，总共 10 秒：\n\n"
                "[0-3s: 开场] 建立场景 + 主体第一个动作 → 主体表情、肢体、环境光线。至少 6-8 句。\n\n"
                "[4-7s: 发展] 关键事件发生 → 主体反应、表情变化、光影变化、镜头推进。至少 6-8 句。\n\n"
                "[8-10s: 收尾] 情绪收束 → 主体最后的表情/动作定格、镜头拉远或固定。结尾必须加稳定约束 'smooth and stable motion, face remains clear and undistorted, no flickering or stuttering'。至少 4-6 句。\n\n"
                "========== 绝对不能做的事 ==========\n"
                "- 不要写画质词：4K、8K、high quality、masterpiece、HDR、ultra HD\n"
                "- 不要笼统描写，每一句必须写出具体的视觉变化\n\n"
                "========== 参考示例（复制这个结构，换成用户的内容）==========\n"
                "[0-3s: Opening] A teenage girl in a white dress squats on stone steps by the river bank at sunset. She looks down, picking at the hem of her skirt with a pout, her eyes rimmed red. The evening breeze blows loose strands of hair across her cheek. She sniffles quietly. The warm golden sunlight casts a soft glow on her face. She hugs her knees tighter, looking small and fragile.\n\n"
                "[4-7s: Development] A small orange cat slowly approaches, rubbing against the back of her hand. The girl's eyelashes flutter. She slowly lifts her head, tears still clinging to her lashes. The corner of her mouth lifts gently. She reaches out and strokes the cat's back with her fingertips. The cat purrs and leans into her touch. Her breathing gradually steadies.\n\n"
                "[8-10s: Closure] She turns her head toward the setting sun on the horizon. Her brows relax, a faint dimple appears as she smiles softly. Her fingertips rest still on the cat's head. The golden light wraps around her silhouette. The river sparkles. A quiet warmth fills the frame. Smooth and stable motion, face remains clear and undistorted, no flickering or stuttering.\n\n"
                "========== 反向提示词 ==========\n"
                "列出需要避免的画面缺陷，中英文混合。\n\n"
                "返回 JSON：{\"positive_prompt\": \"...\", \"negative_prompt\": \"...\"}"
            )
            result = self._call_api(system_prompt, description, max_tokens=128000)
            return {
                "positive_prompt": str(result.get("positive_prompt", description)),
                "negative_prompt": str(result.get("negative_prompt", "")),
            }
        except Exception as e:
            raise RuntimeError(f"DeepSeek generate_single_prompt failed: {e}") from e

    def doctor_scene_prompt(self, scene: dict[str, Any], diagnosis_reasons: list[str]) -> dict[str, Any]:
        """质检发现单镜瑕疵后，针对病灶进行局部外科手术式提示词自愈改写。"""
        duration = int(scene.get("duration") or 5)
        system_prompt = (
            "你是 AI 短剧片段修复导演。成片质检发现某个单镜片段存在局部瑕疵，请在严格保留原剧情、原角色、"
            f"原时长（{duration} 秒）与逐秒结构前提下，仅针对诊断病灶重写更稳妥的动作与镜头描述。"
            "重点：稳定曝光与主光色温、去掉易产生闪白或穿模的急剧甩头与复杂多手指切换、增强动作物理连续性。必须输出严格 JSON。"
        )
        user_prompt = json.dumps(
            {
                "scene_id": scene.get("scene_id"),
                "duration": duration,
                "transition_type": scene.get("transition_type"),
                "transition_mode": scene.get("transition_mode"),
                "blocking": scene.get("blocking"),
                "diagnosis_reasons": diagnosis_reasons,
                "current_video_prompt": scene.get("video_prompt"),
                "current_motion_beats": scene.get("motion_beats"),
                "start_state": scene.get("start_state"),
                "end_state": scene.get("end_state"),
                "required_json_keys": ["video_prompt", "motion_beats", "transition_type", "heal_summary"],
            },
            ensure_ascii=False,
            default=str,
        )
        result = self._call_api(system_prompt, user_prompt, max_tokens=2200)
        return result if isinstance(result, dict) else {}
