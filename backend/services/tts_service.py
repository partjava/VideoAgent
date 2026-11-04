import asyncio
import os
import edge_tts
from mutagen.mp3 import MP3

VOICE_MAP = {
    "narrator": "zh-CN-YunyangNeural",      # 专业解说男声（播音级）
    "male_young": "zh-CN-YunxiNeural",       # 活力青年男声（如李白、书生、少年）
    "male_mature": "zh-CN-YunjianNeural",    # 沉稳/威严男声（如考官、将军、老者）
    "female_young": "zh-CN-XiaoyiNeural",    # 清亮少女女声
    "female_warm": "zh-CN-XiaoxiaoNeural",   # 亲切温和女声（传统默认）
    "default": "zh-CN-YunyangNeural",
}


def resolve_voice_for_speaker(speaker: str | None) -> str:
    """根据说话人名称自动推断最适合的 edge-tts 音色。"""
    if not speaker:
        return VOICE_MAP["narrator"]

    s = speaker.strip().lower()
    if any(k in s for k in ("旁白", "解说", "讲述", "主持人", "narrator", "voiceover")):
        return VOICE_MAP["narrator"]

    if any(k in s for k in ("李白", "少年", "青年", "书生", "小哥", "徒弟", "男孩", "学生", "少爷", "同学")):
        return VOICE_MAP["male_young"]

    if any(k in s for k in ("考官", "老师", "教授", "大人", "将军", "皇上", "陛下", "掌柜", "老者", "老人", "父亲", "大叔", "长老", "师傅", "官员")):
        return VOICE_MAP["male_mature"]

    if any(k in s for k in ("少女", "女孩", "妹妹", "小姐", "丫鬟", "闺秀", "姑娘", "学妹")):
        return VOICE_MAP["female_young"]

    if any(k in s for k in ("母亲", "夫人", "妇女", "阿姨", "妈妈", "奶奶", "师娘", "大娘")):
        return VOICE_MAP["female_warm"]

    if any(k in s for k in ("男", "哥", "爷", "汉")):
        return VOICE_MAP["male_young"]
    if any(k in s for k in ("女", "姐", "妹", "娘")):
        return VOICE_MAP["female_young"]

    return VOICE_MAP["narrator"]


class TTSService:
    async def generate_voice(
        self,
        text: str,
        output_path: str,
        voice: str = "zh-CN-YunyangNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        max_retries: int = 3,
    ) -> float:
        """
        使用 edge-tts 根据文本生成 MP3 配音。
        支持原生语速(rate)、音调(pitch)调整与连接重试。
        返回生成音频的时长，单位为秒。
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        text = text.strip()
        if not text:
            raise ValueError("TTS text content is empty")

        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                communicate = edge_tts.Communicate(
                    text=text,
                    voice=voice,
                    rate=rate,
                    volume=volume,
                    pitch=pitch,
                )
                await communicate.save(output_path)

                if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                    raise FileNotFoundError(f"Failed to generate audio file at {output_path}")

                audio = MP3(output_path)
                return float(audio.info.length)
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(0.8 * attempt)

        if last_exc:
            raise last_exc
        raise RuntimeError(f"TTS generation failed after {max_retries} attempts")


tts_service = TTSService()
