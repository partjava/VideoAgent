"""Local media checks. No model calls or semantic quality claims."""
import subprocess
from pathlib import Path


def write_aspect_frame(source, destination, ratio):
    """Image-to-video providers infer aspect ratio from their first image."""
    from PIL import Image, ImageOps
    dimensions = {"9:16": (720, 1280), "16:9": (1280, 720)}
    if ratio not in dimensions:
        raise ValueError(f"Unsupported aspect ratio: {ratio}")
    with Image.open(source) as frame:
        oriented = ImageOps.exif_transpose(frame).convert("RGB")
        ImageOps.fit(oriented, dimensions[ratio], method=Image.Resampling.LANCZOS).save(destination)


def compare_join_frames(tail, opening):
    from PIL import Image, ImageChops, ImageStat

    with Image.open(resolve_media(tail)) as before, Image.open(resolve_media(opening)) as after:
        size = (64, 64)
        difference = ImageChops.difference(
            before.convert("RGB").resize(size), after.convert("RGB").resize(size)
        )
    score = sum(ImageStat.Stat(difference).mean) / (3 * 255)
    return {"difference": round(score, 3), "review_suggested": score >= 0.14}


def resolve_media(path):
    p = Path(path)
    if p.is_absolute():
        return p
    value = str(path).replace("\\", "/")
    if value.startswith("backend/"):
        value = value[8:]
    return Path(__file__).resolve().parents[1] / value


def inspect_video(path, expected_duration=None, ratio=None, require_audio=False):
    import imageio_ffmpeg
    from moviepy.editor import VideoFileClip
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Missing or empty video: {path}")
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-xerror", "-i", str(path),
                             "-f", "null", "-"], capture_output=True, timeout=180)
    if result.returncode:
        raise ValueError(f"Video decoding failed: {result.stderr.decode(errors='replace')[-1000:]}")
    with VideoFileClip(str(path)) as clip:
        if not clip.duration or clip.duration <= 0:
            raise ValueError("Video duration is invalid")
        if expected_duration is not None and abs(clip.duration - expected_duration) > max(0.15, 2 / (clip.fps or 24)):
            raise ValueError(f"Video duration mismatch: {clip.duration} vs {expected_duration}")
        if ratio:
            w, h = map(float, ratio.split(":"))
            if abs(clip.w / clip.h - w / h) > 0.025:
                raise ValueError("Video aspect ratio mismatch")
        if require_audio and clip.audio is None:
            raise ValueError("Video audio track is missing")
        return {"duration": clip.duration, "width": clip.w, "height": clip.h, "has_audio": clip.audio is not None}


def extract_tail(path, destination, display_duration=None):
    from moviepy.editor import VideoFileClip
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with VideoFileClip(str(resolve_media(path))) as clip:
        end = min(clip.duration, display_duration) if display_duration is not None else clip.duration
        clip.save_frame(str(destination), t=max(0, end - 1 / (clip.fps or 24)))
    return str(destination)
