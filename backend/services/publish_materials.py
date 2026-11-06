import json
import os
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat


def write_notes(path, project, episode, platform):
    from services.provider_factory import get_llm_service

    result = get_llm_service()._call_api(
        "根据本集已确认的剧情生成发布文案。只返回 JSON，字段 title、body、tags。"
        "tags 为最多五个相关话题的字符串数组。不得杜撰事实或声称话题是实时热门。",
        json.dumps({"series_title": project["title"], "episode": {
            key: episode[key] for key in ("episode_num", "title", "opening_hook", "core_event", "ending_hook")
        }, "confirmed_ending": episode.get("confirmed_ending"), "platform": platform}, ensure_ascii=False),
        1200,
    )
    title, body, tags = result.get("title"), result.get("body"), result.get("tags")
    if not all(isinstance(value, str) and value.strip() for value in (title, body)):
        raise ValueError("发布文案缺少标题或正文")
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("发布文案话题格式有误")
    tags = [tag.strip().lstrip("#").strip() for tag in tags[:5] if tag.strip().lstrip("#").strip()]
    temp = path.with_name(f"{uuid4().hex}.tmp")
    try:
        temp.write_text(f"{title.strip()}\n\n{body.strip()}\n\n" + " ".join(f"#{tag}" for tag in tags), encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return {"title": title.strip(), "body": body.strip(), "tags": tags}


def _draw_title(image, title):
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    fonts = [
        windows / "msyh.ttc",
        windows / "simhei.ttf",
        Path("/mnt/c/Windows/Fonts/msyh.ttc"),
        Path("/mnt/c/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    font_path = next((path for path in fonts if path.is_file()), None)
    if font_path is None:
        raise ValueError("缺少中文字体，可生成无标题封面")
    font = ImageFont.truetype(str(font_path), max(16, image.width // 20))
    draw = ImageDraw.Draw(image)
    lines = []
    line = ""
    for character in title:
        if line and draw.textlength(line + character, font=font) > image.width * 0.86:
            lines.append(line)
            line = ""
        line += character
    lines.append(line)
    draw.multiline_text(
        (image.width / 2, image.height * 0.78), "\n".join(lines), anchor="mm",
        align="center", fill="#ffe36b", font=font, stroke_width=max(2, image.width // 240),
        stroke_fill="#101010",
    )


def write_cover(video, path, second, title):
    from moviepy.editor import VideoFileClip

    with VideoFileClip(str(video), audio=False) as clip:
        end = max(0.0, min(3.0, clip.duration - 1 / (clip.fps or 24)))
        times = [min(second, end)] if second is not None else sorted({min(t, end) for t in (0.4, 1.3, 2.2, 2.9)})
        candidates = [(t, Image.fromarray(clip.get_frame(t)).convert("RGB")) for t in times]
    def detail(candidate):
        image = candidate[1].convert("L")
        image.thumbnail((320, 320))
        return ImageStat.Stat(image.filter(ImageFilter.FIND_EDGES)).mean[0]
    chosen, image = max(candidates, key=detail)
    if title.strip():
        _draw_title(image, " ".join(title.split()))
    temp = path.with_name(f"{uuid4().hex}.tmp")
    try:
        image.save(temp, format="JPEG", quality=92)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return chosen
