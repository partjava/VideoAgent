import json
from typing import Any

from config import settings
from services.llm.base import BaseLLMService

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage


GLOBAL_QUALITY_RULES = """
统一画质规格：1080P高清竖屏9:16，30fps，标准短视频发布画质。
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
        if not settings.deepseek_api_key:
            raise ValueError("未找到 DEEPSEEK_API_KEY，请在环境变量中配置。")

        # 使用 LangChain ChatOpenAI 调用 DeepSeek（DeepSeek API 兼容 OpenAI 格式）
        llm = ChatOpenAI(
            model=settings.deepseek_model,
            api_key=settings.deepseek_api_key,
            base_url="https://api.deepseek.com/v1",
            temperature=0.65,
            max_tokens=max_tokens,
            timeout=300,
        )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        response = llm.invoke(messages)
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
                "你是短剧/漫剧编剧导演。你的任务是把用户的一句话创意扩展成一个完整的剧情方案，"
                "包含角色设定、冲突线、情节节拍和视觉世界观。"
                "你必须输出严格 JSON，不要输出解释文字。"
            )
            user_prompt = f"""\
用户原始需求：{user_input}
目标时长：{duration or 60} 秒
内容类型：{resolved_style}（漫剧=漫画插画风格剧情，短剧=写实影视风格剧情）
发布平台：{platform or "抖音/视频号"}
画面比例：{ratio}
生成模式：{generation_mode}

{GLOBAL_QUALITY_RULES}

风格参考：
画面：{style_rules["visual"]}
配音：{style_rules["voice"]}
字幕：{style_rules["subtitle"]}

请把用户需求优化成一个完整的剧情项目设定，要求：
- 必须有明确的故事主线：起因->发展->冲突/高潮->结局（或悬念/反转）。
- 必须设定 1-3 个角色，每个角色有名字、外貌描述（发型、服装、体型特征）、性格标签。
- 角色设定必须具体到可以画出来，禁止"一个年轻人""一位女子"这种模糊描述。
- 场景世界观统一：所有场景必须在同一个视觉世界里，不能前一秒古代后一秒现代。
- 冲突和转折要具体：不要写"遇到了困难"，要写具体发生了什么事。
- 情绪节奏明确：标注哪里紧张、哪里舒缓、哪里爆发、哪里收束。
- 结尾要有悬念钩子或情绪高点，让观众想看下一集或想点赞转发。
- 不要空泛鸡汤，不要写"震撼、治愈、引发思考"。

返回 JSON 字段：
- topic: 剧情主题（一句话概括核心冲突）
- optimized_brief: 完整剧情梗概（200字以内的故事大纲）
- characters: 数组，每个角色包含 name, appearance（具体外貌描述）, personality, role_in_story
- duration: 整数秒
- style: "{resolved_style}"
- platform: 发布平台
- ratio: 画面比例
- generation_mode: 生成模式
- target_audience: 目标观众
- objective: 视频目标（吸引关注/引发讨论/情感共鸣/悬念追更）
- visual_world: 全片统一视觉世界设定（时代、地点、色调、氛围）
- plot_beats: 数组，3-5个情节节拍，每个包含 beat_name 和 description
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
            result.setdefault("duration", duration or 60)
            result.setdefault("style", resolved_style)
            result.setdefault("platform", platform or "抖音")
            result.setdefault("ratio", ratio)
            result.setdefault("generation_mode", generation_mode)
            return result
        except Exception as e:
            raise RuntimeError(f"DeepSeek plan_task failed: {e}") from e

    def generate_script(self, task_plan: dict[str, Any]) -> dict[str, Any]:
        try:
            target_duration = int(task_plan.get("duration") or 60)
            resolved_style = str(task_plan.get("style") or DEFAULT_STYLE)
            characters_json = json.dumps(task_plan.get("characters", []), ensure_ascii=False, default=str)
            min_chars = max(100, int(target_duration * 5.0))
            max_chars = max(min_chars + 30, int(target_duration * 6.6))
            system_prompt = (
                "你是短剧/漫剧编剧。你要把导演的剧情方案写成具体的角色对白和旁白脚本，"
                "每句台词都要推动剧情，每个场景都要能被画面表达。必须输出严格 JSON。"
            )
            user_prompt = f"""\
项目方案：{json.dumps(task_plan, ensure_ascii=False, default=str)}

角色设定：{characters_json}

内容类型：{resolved_style}

脚本要求：
- 这是一个{resolved_style}剧本，不是口播旁白。
- content 是完整剧本，包含旁白叙述和角色对白，总长度控制在 {min_chars} 到 {max_chars} 个中文字符。
- 角色对白格式：【角色名】："台词内容"
- 旁白叙述格式：（旁白）内容
- 全片必须围绕核心冲突推进：铺垫->升级->爆发->收束。
- 每 1-2 句必须推动一个剧情事件或情绪变化，禁止水词和重复信息。
- 角色说话要符合性格：狠角色说话要狠，弱势角色说话要带犹豫或恐惧。
- 关键转折点要有力度：突然的反转、意外的信息、情绪爆发。
- 结尾必须有钩子：悬念、反转、或情绪高点，让人想看下一集。
- 不要写空泛鸡汤，不要说教，不要喊口号。

返回 JSON 字段：
- title: 本集标题（吸引眼球的短句）
- hook: 开头钩子（前3秒抓住观众的台词或画面描述）
- content: 完整剧本（旁白+对白混排）
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
                "full_dynamic": "所有分镜 need_dynamic_video 都设为 true。",
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
- 生成 6-10 个连续分镜，所有 duration 相加接近 {target_duration or 60} 秒，误差不超过 3 秒。
- 每个分镜服务同一条剧情线，按故事时间顺序推进。
- visual_description 必须写具体可见画面：角色的表情、动作、姿态、所处场景、关键道具。
- visual_description 必须包含角色的外貌特征（发型、服装、体型），确保每个镜头角色可识别。
- character_state 写角色此刻的情绪状态、身体姿态和面部表情。
- scene_continuity 必须说明如何继承上一镜头的角色位置、表情、道具、场景和色调。
- transition_note 必须说明最后一帧怎么接下一镜头（动作延续/切景/表情变化）。
- shot_type 用影视镜头语言：特写、近景、中景、全景、过肩、俯拍、仰拍等。
- voiceover 写这个镜头对应的旁白或角色台词，这是直接送入 TTS 配音的文本。
- voiceover 禁止出现“李白：”“旁白：”“某某说：”这类角色标签或朗读提示，不能让 TTS 把说话人名字读出来。
- voiceover 按 duration 控制密度：每秒约 2.5-4 个中文字符，4 秒镜头至少 10 个可朗读中文字符，3 秒至少 8 个，2 秒至少 6 个；不要出现“快答题！”这种撑不起镜头时长的极短句。
- subtitle 可以比 voiceover 更短，但 voiceover 必须能撑住该镜头的配音时长。
- 禁止空泛词：震撼、电影感、高质量、氛围感、未来感大片、引发思考。

每个分镜返回字段：
- scene_id
- scene_index
- duration
- shot_type
- voiceover
- subtitle
- visual_description
- character_state
- scene_continuity
- transition_note
- audio_hint
- camera_motion
- need_dynamic_video

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
                    "speaker": scene.get("speaker"),
                    "voiceover": scene.get("voiceover"),
                    "subtitle": scene.get("subtitle"),
                    "visual_description": scene.get("visual_description"),
                    "character_state": scene.get("character_state"),
                }
                for scene in scenes
            ]
            system_prompt = (
                "你是短视频台词润色导演。你只负责把每个分镜的配音文本调整成可直接朗读、节奏自然、"
                "时长匹配的 voiceover/subtitle，不改分镜顺序、画面描述、镜头时长或剧情事实。"
                "必须输出严格 JSON。"
            )
            user_prompt = f"""\
分镜列表：{json.dumps(clean_scenes, ensure_ascii=False, default=str)}
目标总时长：{target_duration or "跟随分镜 duration"} 秒

润色要求：
- 每个输入分镜都必须返回一条结果，scene_id 必须保持不变。
- speaker 单独填写说话人，例如“李白”“旁白”“考官”；不确定时填“旁白”。
- voiceover 是直接送入 TTS 的文本，禁止出现“李白：”“旁白：”“某某说：”等会被朗读出来的标签。
- voiceover 按 duration 控制密度：每秒约 2.5-4 个中文字符，4 秒镜头至少 10 个可朗读中文字符，3 秒至少 8 个，2 秒至少 6 个。
- 不要把每条都写很长；只补足节奏，避免“快答题！”这种撑不起镜头时长的极短句。
- subtitle 可以与 voiceover 相同，也可以更短，但不能丢失关键剧情。
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
                }
                for scene in scenes
            ]
            system_prompt = (
                "你是 AI 图片提示词导演和图生视频动作导演。你的输出会直接交给 Qwen-Image 图片模型和当前配置的视频生成模型。"
                "必须把每个分镜写成清楚、可生成、可运动、可衔接的提示词。必须输出严格 JSON。"
            )
            style_hint = "漫画/插画风格" if resolved_style == "漫剧" else "写实影视风格"
            user_prompt = f"""\
分镜列表：{json.dumps(clean_scenes, ensure_ascii=False, default=str)}

{GLOBAL_QUALITY_RULES}

内容类型：{resolved_style}
画面风格：{style_hint}
风格模板：
画面：{style_rules["visual"]}
配音：{style_rules["voice"]}
字幕：{style_rules["subtitle"]}
构图：{ratio_desc}

请为每个分镜生成给 Qwen-Image 和当前配置的视频生成模型使用的提示词。

image_prompt 要求：
- 只描述首帧图片，必须能直接画出来。
- 必须注明画面风格（{style_hint}），确保全片风格统一。
- 角色外貌描述必须在每个 image_prompt 中重复写明（发型、服装、体型、瞳色），不能省略。
- 包含角色表情、动作姿态、场景环境、道具、构图、光线、色调。
- 必须继承 scene_continuity 和 character_state，保证相邻镜头角色一致。
- 不要出现 4K、8K、超高清、极致细节、超高解析度。
- 不要要求图片里生成大段文字；如需要文字，只允许短词、标签、数字或图标。

video_prompt 要求：
- 这是给图生视频模型的动作提示词，必须基于首帧图片继续运动。
- 每 0.5-1 秒必须有一个明确小变化，不能让一个画面静止超过 1 秒。
- 动作来自角色表情变化、肢体动作、镜头运动、环境变化（风吹、光影、物体移动）。
- 必须包含"首帧继承、动作节拍、镜头运动、结尾衔接"。
- 不要只写"缓慢运动、自然运动、画面更生动"。
- 不要突然换场景、换主体、换服装、换道具，不要加入无关角色。

每个分镜返回字段：
- scene_id
- image_prompt
- negative_prompt
- video_prompt
- motion_beats: 数组，至少 3 条，格式如"0-1秒：......"
- first_frame_focus: 首帧最重要的可见主体
- last_frame_for_transition: 最后一帧停在哪里，方便接下一镜
- continuity_note: 需要保持一致的角色外貌/道具/场景/色调说明

返回 JSON：{{"prompts": [...]}}
"""
            result = self._call_api(system_prompt, user_prompt, max_tokens=9000)
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
