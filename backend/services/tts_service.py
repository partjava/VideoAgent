import os
import edge_tts
from mutagen.mp3 import MP3

class TTSService:
    async def generate_voice(self, text: str, output_path: str, voice: str = "zh-CN-XiaoxiaoNeural") -> float:
        """
        使用 edge-tts 根据文本生成 MP3 配音。
        返回生成音频的时长，单位为秒。
        """
        # 确保输出目录存在
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # 简单清理文本首尾空白
        text = text.strip()
        if not text:
            raise ValueError("TTS text content is empty")

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)
        
        # 确认文件已生成，并读取音频时长
        if not os.path.exists(output_path):
            raise FileNotFoundError(f"Failed to generate audio file at {output_path}")

        audio = MP3(output_path)
        duration = float(audio.info.length)
        return duration

tts_service = TTSService()
