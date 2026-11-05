import json
import time
from typing import Any

from config import settings
from services.model_config import model_config
from services.llm.base import BaseLLMService

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage


def _quality_rules(ratio: str = "9:16") -> str:
    aspect = "竖屏9:16，1080x1920构图" if ratio == "9:16" else "横屏16:9，1920x1080构图"
    return f"""
统一画质规格：1080P高清{aspect}，30fps，标准短视频发布画质。
画面清晰自然、主体明确、背景干净，满足抖音/视频号正常播放即可。
禁止写入这些高成本词：4K、8K、超高清、极致细节、超高解析度、超采样、细节增强。
不要追求复杂纹理和昂贵光影，优先保证画面稳定、动作清楚、镜头衔接自然。
"""


STYLE_TEMPLATES: dict[str, dict[str, str]] = {
    "漫剧": {
        "visual": (
            "1080P高清漫画/插画风格，统一美术风格（日系赛璐珞/韩漫厚涂/国漫水墨任选一种并全片保持）。"
            "角色设计固定：发型、瞳色、服装、体型在全片不变。"
            "场景用漫画式构图：特写给表情、中景给动作、远景给环境氛围。"
            "色调统一，高饱和度，光影简洁有力，背景可适度简化但不能空白。"
            "对话用漫画气泡框或底部字幕栏承载，不要直接在画面里渲染大段文字。"
        ),
        "voice": "角色演绎式配音，男女声分角色，语气有情绪起伏，关键台词加重语气，节奏偏快偏紧凑。",
        "subtitle": "漫剧字幕风格，白色粗体加黑色描边，角色对话用不同颜色区分，旁白用斜体灰色，短句分行。",
    },
    "短剧": {
        "visual": (
            "1080P高清写实影视画面，真人实拍质感。"
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


class DeepSeekService(BaseLLMService):
    def _call_api(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> dict[str, Any]:
        if not settings.enable_paid_api:
            raise ValueError("付费 API 调用未启用，请在环境变量中设置 ENABLE_PAID_API=true。")
        profile = model_config("prompt", settings)
        if not profile["key"]:
            raise ValueError("未找到 DEEPSEEK_API_KEY，请在环境变量中配置。")

        # 使用 LangChain ChatOpenAI 调用 DeepSeek（DeepSeek API 兼容 OpenAI 格式）
        llm = ChatOpenAI(
            model=profile["model"],
            api_key=profile["key"],
            base_url=profile["url"],
            temperature=0.65,
            max_tokens=max_tokens,
            timeout=300,
        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        print(f"[DeepSeekService] Requesting model={profile['model']} (max_tokens={max_tokens})...", flush=True)
        t0 = time.time()
        response = llm.invoke(messages)
        print(f"[DeepSeekService] Response received in {time.time()-t0:.1f}s ({len(response.content)} chars)", flush=True)
        content = response.content

        if not content:
            print(f"[DeepSeekService] API returned empty content.")
            raise ValueError("DeepSeek returned an empty response.")

        # 尝试从文本中提取 JSON 对象
        cleaned_content = content.strip()

        # 找第一个 { 和最后一个 }
        start = cleaned_content.find("{")
        end = cleaned_content.rfind("}")
        if start >= 0 and end > start:
            cleaned_content = cleaned_content[start:end+1]

        # 清理 Markdown 代码块包裹
        if cleaned_content.startswith("```json"):
            cleaned_content = cleaned_content[7:]
        elif cleaned_content.startswith("```"):
            cleaned_content = cleaned_content[3:]
        if cleaned_content.endswith("```"):
            cleaned_content = cleaned_content[:-3]
        cleaned_content = cleaned_content.strip()

        # 如果 JSON 被截断，尝试修复最后一个不完整的字段
        try:
            return json.loads(cleaned_content)
        except json.JSONDecodeError:
            # 截断修复：找到最后一个完整的键值对
            try:
                # 尝试补 }
                if not cleaned_content.endswith("}"):
                    cleaned_content += "}"
                return json.loads(cleaned_content)
            except json.JSONDecodeError:
                # 去掉最后不完整的字段重试
                for _ in range(3):
                    last_comma = cleaned_content.rfind(",")
                    if last_comma < 10:
                        break
                    cleaned_content = cleaned_content[:last_comma] + "}"
                    try:
                        return json.loads(cleaned_content)
                    except json.JSONDecodeError:
                        continue
                print(f"[DeepSeekService] JSON parse failed. Raw: {content[:300]}")
                raise ValueError(f"JSON format error. Snippet: {content[:200]}")

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
            user_prompt = f"""\
用户原始需求：{user_input}
目标时长：{str(duration) + " 秒（固定）" if duration is not None else "从原句提取；未指定则自行选择"}
若生成模式为 native_10s，自动时长选 10 的整数倍，未指定则 30 秒。
画面风格：{style or "自动选择"}（漫剧=漫画插画，短剧=写实影视）
发布平台：{platform or "抖音/视频号"}
画面比例：{ratio}
生成模式：{generation_mode}

{_quality_rules(ratio)}

风格参考：
画面：{style_rules["visual"]}
配音：{style_rules["voice"]}
字幕：{style_rules["subtitle"]}

创意与叙事原则（反乏味、反套路）：
- 强化【核心戏剧冲突】与【身份/认知反差】：拒绝白开水流水账！主角面对困境必须有戏剧化错位或反转，情绪起伏要有过山车般的冲击力。
- 黄金前3秒抓人：开篇首秒直接抛出最具视觉冲击力或最具冲突性的情境，拒绝长篇大论的慢热铺垫。
- 结尾有力：给出一个意想不到的爆笑反转、深刻共鸣或悬念钩子。

先识别 video_type，只能选 story（剧情）、explainer（科普/教程）、product（产品介绍）。
- story：铺垫、发展、解决，角色与道具保持一致。
- explainer：展示现象、逐步解释、回答开头的问题。可无角色，不编造事实。
- product：使用场景、功能演示、总结。只使用用户提供的产品事实，不捏造规格、效果或承诺。
- plot_beats 依次列出开头、核心内容、结尾。每段写明观众应看到或听到什么。短片首秒直接出现可见的事件、现象或演示结果，把背景交代放进随后动作或旁白。结尾给出结果及主体反应。
- 用户明确指定的题材、时长、风格优先。未指定时默认约30秒，可按内容调整。

返回 JSON 字段：
- video_type: story、explainer 或 product
- topic: 视频主题
- optimized_brief: 完整剧情梗概（200字以内的故事大纲）
- characters: 数组，每个角色包含 name, appearance（具体外貌描述）, personality, role_in_story
- duration: 整数秒
- style: 漫剧或短剧，用户未指定时按内容选择
- platform: 发布平台
- ratio: 画面比例
- generation_mode: 生成模式
- target_audience: 目标观众
- objective: 视频目标（吸引关注/引发讨论/情感共鸣/悬念追更）
- visual_world: 全片统一视觉世界设定（时代、地点、色调、氛围）
- plot_beats: 数组，每个包含 beat_name 和 description。native_10s 模式先确定最终 duration（用户指定时以指定值为准），然后输出恰好 duration / 10 个节拍，每个节拍对应一个10秒段落的核心事件。其他模式输出3–5个节拍。
- conflict: 核心冲突是什么
- climax: 高潮或反转是什么
- ending_hook: 结尾钩子（悬念/反转/情绪高点）
- pacing: 节奏策略
- voice_style: 配音风格
- subtitle_style: 字幕风格
"""
            result = self._call_api(system_prompt, user_prompt, max_tokens=2500)
            result["source"] = "deepseek"
            result["user_input"] = user_input
            planned_duration = result.get("duration")
            result["duration"] = duration if duration is not None else (
                planned_duration if type(planned_duration) is int and 4 <= planned_duration <= 90 else 30
            )
            if generation_mode == "wan3_whole":
                if duration is not None and not 4 <= duration <= 30:
                    raise ValueError("Wan 3.0 whole mode supports 4–30 seconds")
                result["duration"] = min(30, result["duration"])
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
            result.setdefault("platform", platform or "抖音")
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
            system_prompt = (
                "你是爆款短视频金牌编剧。你的剧本以戏剧张力强、反差感拉满、台词有梗生动著称。"
                "每句台词都要推动剧情或情绪。冲突强度服从题材，画面动作必须能在段落时长内完成。必须输出严格 JSON。"
            )
            user_prompt = f"""\
项目方案：{json.dumps(task_plan, ensure_ascii=False, default=str)}

角色设定：{characters_json}

内容类型：{resolved_style}

剧本核心原则（反平庸、反乏味规则）：
- 每段围绕一个可见事件推进。开场让观众看懂人物的目的，结尾给出一个可见结果或反应。冲突和反转只在符合题材、时长足够时使用。
- 黄金3秒视觉钩子：开篇第一秒直接抛出最具视觉冲击或身份错位的画面，禁止慢热铺垫。
- 角色台词必须有性格、有灵魂、有爆梗：台词要口语化、接地气、情绪饱满，体现人物性格色彩，融入角色标志性金句但必须产生搞笑或热血化学反应，严禁枯燥说教与空洞抒情。

脚本要求：
- 按项目方案的 video_type 编写。剧情用角色行动和对白推进，科普按现象、解释、结论展开，产品介绍按场景、演示、总结展开。
- 保留原始需求中的关键信息。每一段都写出画面和对应的台词或旁白，结尾必须完成开头提出的叙事或说明目标。
- 先写画面推进：首秒动作钩子 → 行动让局面发生变化 → 清晰结果和反应。10秒以内的短片尽量围绕一个事件，不用多秒站立、凝视或慢慢准备来铺垫。
- 原生 10 秒模式先按时长输出 segments，严格每段 10 秒，共 {target_duration // 10} 段。每段填写 plot、duration、voice_start、voiceover 和 dialogue_lines。多人对白按出声顺序填写 speaker、text、start。start 和 voice_start 均从当前段落的第 0 秒起算，取值为 0 ≤ 时间 < 10。voice_start 等于第一句出声时间。旁白段的 dialogue_lines 为空。content 展示同一份分段剧本。
- 旁白按每秒约 3.5 个汉字估算，标点不计。字数上限为 max(0, floor((duration - voice_start - 0.5) * 3.5))。多人对白每句按距下一句的时间预算，最后一句留 0.5 秒给画面。旁白补充画外信息，关键结果出现前不提前揭晓。
- 科普避免虚构事实，产品介绍不添加未提供的规格与功效。

返回 JSON 字段：
- title: 本集标题（吸引眼球的短句）
- hook: 开头钩子（前3秒抓住观众的台词或画面描述）
- content: 完整剧本（旁白+对白混排）
- segments: 原生10秒模式的结构化段落数组，其他模式返回空数组
- ending: 结尾钩子台词
- publish_copy: 发布文案（带悬念的短文案，引导点赞关注）
"""
            result = self._call_api(system_prompt, user_prompt, max_tokens=2500)
            result["source"] = "deepseek"
            return result
        except Exception as e:
            raise RuntimeError(f"DeepSeek generate_script failed: {e}") from e

    def generate_storyboard(
        self,
        script: dict[str, Any],
        target_duration: int | None = None,
        generation_mode: str = "full_dynamic",
    ) -> list[dict[str, Any]]:
        try:
            dynamic_hint = {
                "wan3_whole": "返回唯一一段动态视频，时长等于目标。可以在这段内部按事件推进、转移视线和调整景别，不拆分外部片段。",
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
            user_prompt = f"""\
剧本：{json.dumps(script, ensure_ascii=False, default=str)}
目标时长：{target_duration or 60} 秒
生成模式：{generation_mode}
{dynamic_hint}

分镜要求：
- 镜头数量、时长和衔接方式以当前模式要求为准，所有 duration 相加必须严格等于 {target_duration or 60} 秒。未规定单段时长的模式使用 4–12 秒。
- 剧本含 segments 时，一段对应一个分镜，按原顺序保留该段事件、台词、说话人和时间，不拆分或把台词移到另一段。
- 每拍只突出一个观看重点。双人交互可随动作和反应交接焦点，保持人物位置与拍摄轴线。
- 先用一个贯穿本段的可见事件串起起因、动作、结果和反应，再写 visual_description。后一个动作必须由前一个动作的可见结果或台词触发。预留交互完成和人物反应的时间，删去挤占揭晓或反应的次要动作、额外转场和结尾字卡。
- 重要道具每次换手、落到支撑面、翻面或离开画面，都交代动作过程及完成后的状态。情节依赖观众看见的线索，提前选好能看清线索的构图。
- transition_type 为 cut（切镜或换机位）、continue_action（同机位同构图连续动作，必须继承上一镜尾帧）、scene_change（明确换场）。首镜必须为 cut。
- start_state 和 end_state 写明角色位置、朝向、持物手、道具位置及动作阶段；连续动作必须使本镜 start_state 与上镜 end_state 一致。
- 每个分镜时长至少 4 秒，不足 4 秒的补到 4 秒。
- 每个分镜服务同一条内容主线。第一镜首帧构图就要看清动作对象，首秒发生可见变化。每个镜头须推进事件，保留结果和反应的时长。
- 景别由内容决定。道具接触、按钮按下等细节要让主体和接触点清晰可见。需要换景别时在镜头之间切；用户要求同机位或继续动作时，靠清晰的主体位移和反应做出节奏。
- visual_description 写清一个可见事件的起因、主要动作和结果。优先使用清楚的体态变化、单向位移或一次关键接触。避免同一拍安排高速碰撞、多件物体飞散、多个角色同时做复杂动作。情绪可以通过当前景别能看清的视线、表情或姿态表达。
- visual_description 必须包含角色的外貌特征（发型、服装、体型），确保每个镜头角色可识别。
- character_state 写清角色当下的姿态、视线、表情和情绪变化，强度服从剧情。
- scene_continuity 必须说明如何继承上一镜头的角色位置、表情、道具、场景和色调。
- transition_note 必须说明最后一帧怎么接下一镜头（动作延续/切景/表情变化）。
- shot_type 用影视镜头语言：特写、近景、中景、全景、过肩、俯拍、仰拍等。
- voice_start 写台词相对本段开始的秒数，台词在所描述的动作之后出现，必须留够朗读时间。sound_cues 写发生时间和音效名，仅支持 footstep、impact、water、paper、wind，没有就返回空数组。
- voiceover 写这个镜头对应的旁白或角色台词，这是直接送入 TTS 配音的文本。
- 如果剧本是多人对白，必须保留关键原对白的说话人、意思和顺序，不改成独白。为该分镜输出 dialogue_lines，按出声顺序填写 speaker、text（纯台词）、start（本段开始后的秒数）。在10秒内说不完时删去次要对白，给每句话留出自然读时。voiceover 同步填写这些台词拼接后的纯文本。纯旁白场景的 dialogue_lines 为空。
- voiceover 禁止出现“李白：”“旁白：”“某某说：”这类角色标签，禁止包含括号、音效或动作指示（如“（大笑）”、“【音效】”）。
- 没有已定稿台词时，按 max(0, floor((duration - voice_start - 0.5) * 3.5)) 估算旁白汉字数上限，标点不计，保留收尾空间。
- subtitle 直接使用 voiceover 原文，不再缩写。
- 禁止空泛词：震撼、电影感、高质量、氛围感、未来感大片、引发思考。

每个分镜返回字段：
- scene_id
- scene_index
- duration
- shot_type
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
                "你是金牌台词精修导演。你的任务是把每个分镜的配音文本润色成极具感染力、情绪饱满、"
                "有梗且富有角色灵魂的对白/旁白。坚决拒绝平淡无味的AI假大空说教腔。必须输出严格 JSON。"
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
                    "character_state": scene.get("character_state"),
                    "scene_continuity": scene.get("scene_continuity"),
                    "transition_note": scene.get("transition_note"),
                    "audio_hint": scene.get("audio_hint"),
                    "camera_motion": scene.get("camera_motion"),
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
如果分镜提供 previous_segment_context，将其作为上一段已经写好的剧情和计划收尾画面。按当前分镜设计新的首帧，承接仍相关的人物状态、道具和剧情因果。允许按分镜切换场景或机位，不重演上一段已经完成的动作，不把计划收尾当作实际尾帧。只输出当前分镜的提示词和逐秒描述。

image_prompt 要求：
- 首帧的角色姿态、道具状态和相对位置以 start_state 为唯一时序依据，与第0秒的起始画面一致。visual_description 仅提供静态场景与角色信息，剥离动作过程、后续姿态、接触结果及事后痕迹。只描述此刻已经存在、能直接画出来的画面。
- 必须注明画面风格（{style_hint}），确保全片风格统一。
- 只描述首帧中实际可见的角色和外貌特征。空镜和纯物品特写保持无人，手部特写仅描述入画部位。禁止为了重复全片角色设定而新增人物。
- 包含角色表情、动作姿态、场景环境、道具、构图、光线、色调。
- 必须继承 scene_continuity 和 character_state，保证相邻镜头角色一致。
- 不要出现 4K、8K、超高清、极致细节、超高解析度。
- 不要要求图片里生成大段文字；如需要文字，只允许短词、标签、数字或图标。

全片叙事与动作编排：
- 在 continuity_note 中重复固定角色造型、主道具状态和场景方向。每个10秒部分的第一段写明上一部分留下的线索如何引出本部分，使用视线、动作结果或声音引导切换。
- 先在内部排好整段动作因果链，再把动作连续切分到逐秒描述。同一个动作可以跨多秒，按时间写出准备、进行、完成与反应。动作仍在进行时，下一秒先续上同一运动方向和接触状态。每秒可以保持动作或构图，画面重点随事件推进。
- 对照已定的对白时间安排画面：台词所依赖的线索先进入可辨认的景别，揭晓句说出时保留说话人或听者的可见反应，句末留出反应的画面时间。片段的结尾优先停在事件结果或人物反应上。
- 生图和视频提示词仅描述可拍摄的画面。对白用来安排动作与反应的时机，不把台词原文、字幕、标题、气泡或黑底字卡写入画面提示词。情节所需的道具内文字可单独描述。
{legacy_camera_rules}

video_prompt 要求（参考 video-shotcraft 与 Seedance 2.0 工业级运镜规范）：
- 这是给图生视频模型的动作提示词，必须基于首帧图片（@首帧）自然演进，严格保持人物面孔、服装颜色及光影一致。
- 先确定观众需要依次看清的起因、关键交互和结果，再分配具体秒点。动作幅度、速度和停顿服从剧情。情绪通过当前景别能看见的表情、视线、手势或姿态呈现。
- 运镜写清起始构图、拍摄侧、跟随目标、方向和落点。注明是哪一个可见动作触发了取景或焦点变化。关键接触期间保留双方和接触点，构图稳定时写清画面要保持的范围。
- 每秒只突出一个观看重点。人物互动时允许焦点沿因果转到另一人或关键道具，保持各人的左右位置、视线与拍摄轴线。只有动作确实影响环境时才写可见的环境反馈。
- 逐秒 action 描述从哪里到哪里、哪一侧肢体发力、接触点、速度和方向、接触后的道具反馈、视线或重心如何变化。没有接触的动作写清位移或姿态变化。
- start_state/end_state 写出画面内位置、朝向、支撑脚或持物手，以及重要道具的位置与状态。下一秒延用上一秒的结束状态。
- action 的第一个动作必须能从该秒 start_state 直接发生，最后一个动作必须产生该秒 end_state。重要道具的位置、持物人、持物手、朝向和接触面发生变化时，把靠近、接触、承重转移与松手按实际顺序写入对应秒；秒末仍在接触就写明保持的接触点。人物转身、出画或改变视线时交代可见的过程。
- camera 的起始景别、拍摄侧和取景目标承接上一秒的结束构图。运镜跨秒时，写清当前秒推进到哪里及下一秒从哪里接上。关键交互和揭晓期间保持接触点或反应主体可见，不安排突然切镜或让关键主体无故出画。
- continue_action 的首秒延续上一段尾部的姿态、运动方向、光线和机位，从未完成的动作继续。已经落下、打开或离开画面的东西保持其结果，不要复位。
- video_prompt 只概括首帧继承、镜头运动、关键动作目标与结尾衔接。逐秒时间和动作只写在 motion_beats，避免同一时间轴在两个字段里重复或冲突。
- 不要只写"缓慢运动、自然运动、画面更生动"。
- 不要突然换场景、换主体、换服装、换道具，不要加入无关角色。
- **动作描述必须委婉、暗示性**，禁用暴力/战争词汇："杀"、"斩"、"刺"、"砍"、"箭穿"、"纵火"、"焚烧"、"挥剑"、"拔剑"、"打斗"、"激战"、"厮杀"、"受伤"、"流血"。用"手势变化"、"快速转身"、"高举手臂"、"用力投出"、"握紧拳头"、"抬手示意"、"身体前倾"等中性动作描述代替。

每个分镜返回字段：
- scene_id
- image_prompt
- image_negative_prompt: 针对该首帧最可能出现的结构、位置或材质错误，列出具体可见缺陷。只排除不需要的额外肢体、错误接触、文字、水印或风格偏差，按当前画面取舍。
- video_negative_prompt: 针对该镜头的动作和运镜，列出具体的时序缺陷，包括道具瞬移、接触穿模、角色外观漂移、动作倒放或非计划的抖动；排除画面浮现字幕、标题或字卡，保留情节所需的道具内文字。不要排除分镜要求的静止、运动或效果。两个负面词字段用简短短语，不堆砌画质评分词。
- video_prompt
- motion_beats: 对象数组，每条包含 start、end、subject、camera、start_state、action、end_state。按片段本地时间从0秒逐秒写到 duration，时间连续且不重叠。每条都写完整可见画面：camera 写景别、机位、取景重点及运镜；start_state 写人物在画面中的方位、姿态、视线和相关道具的位置；action 写动作方向、肢体和接触点如何变化；end_state 写秒末可见结果，下一条从该结果继续。停顿也写清保持的画面。输出只描述当前分镜的实际内容，不包含示例、类比、占位词或模板说明。
- motion_beats 的 start 和 end 必须使用 JSON 数字，单位为秒，不加引号或单位后缀。
- 原生10秒段落必须返回十条逐秒描述。每秒的 camera、start_state、action、end_state 合计以50–90个汉字为写作参考，优先补足构图、走位、接触过程和秒末状态。共同的外貌、服装、场景和光线放在 continuity_note 中，逐秒字段无需重复堆砌。每秒只写当前段落能拍到的内容。
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
