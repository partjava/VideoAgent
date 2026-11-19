from __future__ import annotations
"""Versioned character assets. Stored snapshots and anchor files are never overwritten."""
import asyncio
import base64
import binascii
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import re
import shutil
from uuid import uuid4

from PIL import Image
from core.database import mongodb
from models.character_model import CharacterFields, CharacterUpdate, PortraitView, ReferenceView
from services.drama_craft_service import (
    STYLE_QUALITY_BLOCKS,
    STYLE_TURNAROUND_BLOCKS,
    build_character_sheet_4grid_prompt,
    build_portrait_negative_prompt,
    ensure_shoes_in_clothing,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
MAX_IMAGE_BYTES = 10 * 1024 * 1024
REFERENCE_FILENAMES = {
    "front": "anchor.png",
    "profile": "profile.png",
    "whole": "whole.png",
    "sheet": "sheet.png",
}
VIEW_PROMPTS = {
    "front": (
        "角色定妆照（正面平视半身特写，胸口以上构图），单一人物正视镜头，表情平静自然（neutral expression），"
        "双肩端平，纯白无接缝影棚背景（#FFFFFF），45度柔光箱均匀受光"
    ),
    "profile": (
        "角色定妆照（严格90度正侧面轮廓像，strict 90-degree side profile view），"
        "单人胸口以上中近景，脸部与身体完全朝向画右，仅见单侧鼻梁、下颌线、单侧耳朵与后脑勺发型，视线平视前方不看镜头，"
        "纯白无接缝影棚背景（#FFFFFF），45度柔光箱均匀受光"
    ),
    "whole": (
        "角色定妆照（广角全身直立正面照，35mm full-body standing front shot, head to toe fully visible），"
        "单一人物从头顶到脚底鞋尖完整入画，人物占画面高度75%，画面上下各留10%纯白留白，"
        "双腿自然站直，双手自然垂放身体两侧且空手无持物，纯白无接缝影棚背景（#FFFFFF），脚底极淡自然落地投影"
    ),
}


def _profile_snapshot(profile):
    snapshot = deepcopy(profile)
    images = snapshot.get("reference_images") or {}
    if snapshot.get("reference_path") and not images.get("front"):
        images["front"] = snapshot["reference_path"]
    snapshot["reference_images"] = images
    snapshot["reference_path"] = images.get("front")
    return snapshot


def validate_image(data: bytes) -> Image.Image:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Reference image must be at most 10 MB")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG"}:
                raise ValueError("Reference image must be PNG or JPEG")
            if min(image.size) < 64 or max(image.size) > 4096:
                raise ValueError("Reference dimensions must be between 64 and 4096 pixels")
            image.verify()
        with Image.open(BytesIO(data)) as image:
            return image.convert("RGB")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid reference image") from exc


def crop_headshot_from_turnaround(raw_bytes: bytes) -> bytes:
    """Crop the left portrait panel from a 16:9 4-view turnaround sheet as PNG bytes."""
    validate_image(raw_bytes)
    with Image.open(BytesIO(raw_bytes)) as img:
        rgb = img.convert("RGB")
        w, h = rgb.size
        crop_w = int(w * 0.43)
        crop_h = int(h * 0.84)
        if w > h * 1.25 and crop_w >= 64 and crop_h >= 64:
            panel = rgb.crop((0, 0, crop_w, crop_h))
        else:
            panel = rgb
        out = BytesIO()
        panel.save(out, format="PNG")
    result = out.getvalue()
    validate_image(result)
    return result


def local_asset(path: str) -> Path:
    if not isinstance(path, str) or not path.startswith("backend/assets/") or "\\" in path or ".." in Path(path).parts:
        raise ValueError("Reference must be a server-relative asset path")
    root = BACKEND_DIR / "assets"
    candidate = (BACKEND_DIR.parent / path).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("Reference must be a server asset path")
    return candidate


class CharacterService:
    def __init__(self):
        self._locks = {}

    def _lock(self, character_id):
        return self._locks.setdefault(character_id, asyncio.Lock())

    async def _document(self, character_id):
        if not re.fullmatch(r"char_[a-f0-9]{12}", character_id):
            raise ValueError("Invalid character ID")
        doc = await mongodb.find_one("characters", {"_id": character_id})
        if not doc or doc.get("archived"):
            raise LookupError("Character not found")
        return doc

    async def get(self, character_id):
        return _profile_snapshot((await self._document(character_id))["versions"][-1])

    async def list(self, include_archived: bool = False):
        query = {} if include_archived else {"archived": False}
        docs = await mongodb.find_many("characters", query, limit=1000, sort_field="_id")
        if not include_archived:
            return [_profile_snapshot(doc["versions"][-1]) for doc in docs]
        items = []
        for doc in docs:
            snap = _profile_snapshot(doc["versions"][-1])
            snap["archived"] = bool(doc.get("archived"))
            items.append(snap)
        return items

    async def create(self, payload: CharacterFields):
        character_id = "char_" + uuid4().hex[:12]
        profile = {**payload.model_dump(), "character_id": character_id, "revision": 1,
                   "reference_path": None, "reference_images": {}, "confirmed": False}
        await mongodb.insert_one("characters", {"_id": character_id, "revision": 1,
            "archived": False, "versions": [deepcopy(profile)]})
        return profile

    async def _append(self, doc, profile):
        previous_revision = doc["revision"]
        doc = deepcopy(doc)
        profile = deepcopy(profile)
        profile["revision"] = previous_revision + 1
        profile["confirmed"] = False
        doc["revision"] = profile["revision"]
        doc["versions"].append(profile)
        changed = await mongodb.replace_one("characters", {"_id": doc["_id"], "revision": previous_revision}, doc)
        if not changed:
            raise ValueError("Character changed concurrently; reload and retry")
        return deepcopy(profile)

    async def update(self, character_id, payload: CharacterUpdate):
        async with self._lock(character_id):
            doc = await self._document(character_id)
            profile = _profile_snapshot(doc["versions"][-1])
            fields = {key: profile[key] for key in CharacterFields.model_fields}
            fields.update(payload.model_dump(exclude_unset=True))
            profile.update(CharacterFields.model_validate(fields).model_dump())
            return await self._append(doc, profile)

    async def reference(self, character_id, encoded, view: ReferenceView = "front"):
        if view not in REFERENCE_FILENAMES:
            raise ValueError("Reference view must be front, profile, or whole")
        if encoded.startswith("data:"):
            if not re.match(r"^data:image/(png|jpeg);base64,", encoded):
                raise ValueError("Reference must be PNG or JPEG base64")
            encoded = encoded.split(",", 1)[1]
        if len(encoded) > 14000000:
            raise ValueError("Reference image must be at most 10 MB")
        try:
            image = validate_image(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        async with self._lock(character_id):
            doc = await self._document(character_id)
            profile = _profile_snapshot(doc["versions"][-1])
            revision = doc["revision"] + 1
            filename = REFERENCE_FILENAMES[view]
            path = f"backend/assets/characters/{character_id}/v{revision}/{filename}"
            destination = local_asset(path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            png = BytesIO()
            image.save(png, format="PNG")
            if png.tell() > MAX_IMAGE_BYTES:
                raise ValueError("Reference image exceeds 10 MB after conversion")
            # Exclusive creation preserves anchors even across processes.
            with destination.open("xb") as output:
                output.write(png.getbuffer())
            profile["reference_images"][view] = path
            profile["reference_path"] = profile["reference_images"].get("front")
            return await self._append(doc, profile)

    async def confirm(self, character_id):
        async with self._lock(character_id):
            doc = await self._document(character_id)
            profile = _profile_snapshot(doc["versions"][-1])
            if not profile["reference_path"] or not local_asset(profile["reference_path"]).is_file():
                raise ValueError("请先上传正面参考图或生成定妆照，再确认角色")
            for path in profile["reference_images"].values():
                validate_image(local_asset(path).read_bytes())
            if not profile["confirmed"]:
                doc = deepcopy(doc)
                doc["versions"][-1]["confirmed"] = True
                changed = await mongodb.replace_one("characters", {"_id": character_id, "revision": doc["revision"]}, doc)
                if not changed:
                    raise ValueError("Character changed concurrently; reload and retry")
            return _profile_snapshot(doc["versions"][-1])

    async def archive(self, character_id):
        async with self._lock(character_id):
            await self._document(character_id)
            await mongodb.update_one("characters", {"_id": character_id}, {"archived": True})
        return {"character_id": character_id, "archived": True}

    async def delete_permanent(self, character_id: str) -> dict:
        if not re.fullmatch(r"char_[a-f0-9]{12}", character_id):
            raise ValueError("Invalid character ID")
        async with self._lock(character_id):
            doc = await mongodb.find_one("characters", {"_id": character_id})
            if not doc:
                raise LookupError("Character not found")
            latest = (doc.get("versions") or [{}])[-1]
            if not doc.get("archived") and latest.get("confirmed"):
                bound_series = await mongodb.find_one(
                    "series_projects",
                    {"archived": {"$ne": True}, "character_ids": character_id},
                )
                bound_task = await mongodb.find_one(
                    "video_tasks",
                    {"metadata.character_ids": character_id},
                )
                if bound_series or bound_task:
                    raise ValueError("该已确认角色已被连载或视频任务绑定，请使用「归档」隐藏，避免破坏历史项目引用")
            await mongodb.delete_one("characters", {"_id": character_id})
            shutil.rmtree(BACKEND_DIR / "assets" / "characters" / character_id, ignore_errors=True)
        return {"character_id": character_id, "deleted": True}

    async def restore(self, character_id):
        if not re.fullmatch(r"char_[a-f0-9]{12}", character_id):
            raise ValueError("Invalid character ID")
        async with self._lock(character_id):
            doc = await mongodb.find_one("characters", {"_id": character_id})
            if not doc:
                raise LookupError("Character not found")
            await mongodb.update_one("characters", {"_id": character_id}, {"archived": False})
            snap = _profile_snapshot(doc["versions"][-1])
            snap["archived"] = False
            return snap

    def _build_portrait_prompt(
        self,
        profile: dict,
        view: ReferenceView,
        custom_prompt: str = "",
        style: str = "realistic",
        has_reference: bool = False,
    ) -> str:
        if view == "sheet":
            return build_character_sheet_4grid_prompt(
                profile,
                style=style,
                custom_prompt=custom_prompt,
                has_reference=has_reference,
            )
        if custom_prompt and view == "front" and style == "realistic":
            return custom_prompt
        name = (profile.get("name") or "").strip()
        face_dna = (profile.get("face_dna") or "").strip("。 ")
        clothing = (profile.get("signature_clothing") or "").strip("。 ")
        if view == "whole":
            clothing = ensure_shoes_in_clothing(clothing, face_dna)

        if has_reference and view == "profile":
            ref_header = (
                "保持参考图中同一人物的五官、性别、年龄、发型发色与服装完全不变"
                "（same person, identical identity and outfit as reference image），"
                "仅将人物旋转为严格90度纯正侧面像（strict 90-degree side profile view），"
                "脸部完全朝向画右，仅见单侧鼻梁、下颌轮廓与单侧耳朵，不看镜头，中近景胸口以上，纯白影棚背景。"
            )
        elif has_reference and view == "whole":
            ref_header = (
                "保持参考图中同一人物的五官、性别、年龄、发型与上身服装完全不变"
                "（same person, identical face, hair and outfit as reference image），"
                "拉远为35mm广角全身直立定妆照（full-body head-to-toe standing front shot），"
                "头顶至鞋底完整入画，人物占画面高度75%，双腿自然直立，双手自然垂放身体两侧，空手无道具，纯白影棚背景。"
            )
        else:
            ref_header = VIEW_PROMPTS.get(view, VIEW_PROMPTS["front"])

        subject_desc = "，".join(
            part for part in (
                f"人物：{name}" if name else "",
                face_dna,
                f"身穿{clothing}" if clothing else "",
            )
            if part
        )
        style_block = (
            STYLE_QUALITY_BLOCKS.get(style, STYLE_QUALITY_BLOCKS["realistic"])
            if view == "front"
            else STYLE_TURNAROUND_BLOCKS.get(style, STYLE_TURNAROUND_BLOCKS["realistic"])
        )
        parts = [
            ref_header,
            subject_desc,
            (custom_prompt or "").strip(),
            style_block,
        ]
        return "。".join(p.strip("。 ") for p in parts if p and p.strip("。 "))

    def _build_portrait_negative_prompt(
        self,
        profile: dict,
        view: ReferenceView,
        style: str = "realistic",
    ) -> str:
        return build_portrait_negative_prompt(profile, view=view, style=style)

    async def preview_prompts(self, character_id: str, style: str = "realistic") -> dict:
        from services.provider_factory import get_image_service

        profile = await self.get(character_id)
        try:
            img_svc = get_image_service()
            caps = {
                "model": str(getattr(img_svc, "model", "") or ""),
                "supports_reference_images": bool(getattr(img_svc, "supports_reference_images", False)),
                "max_reference_images": int(getattr(img_svc, "max_reference_images", 0) or 0),
            }
        except Exception:
            caps = {"model": "", "supports_reference_images": False, "max_reference_images": 0}
        has_front = bool((profile.get("reference_images") or {}).get("front") or profile.get("reference_path"))
        can_ref = caps["supports_reference_images"] and has_front
        return {
            "character_id": character_id,
            "name": profile.get("name"),
            "style": style,
            "prompts": {
                "front": self._build_portrait_prompt(profile, "front", style=style, has_reference=False),
                "profile": self._build_portrait_prompt(profile, "profile", style=style, has_reference=can_ref),
                "whole": self._build_portrait_prompt(profile, "whole", style=style, has_reference=can_ref),
                "sheet": self._build_portrait_prompt(profile, "sheet", style=style, has_reference=can_ref),
            },
            "negative_prompts": {
                v: self._build_portrait_negative_prompt(profile, v, style=style)
                for v in ("front", "profile", "whole", "sheet")
            },
            "image_capabilities": caps,
        }

    async def batch_create(self, items: list[CharacterFields]) -> list[dict]:
        created = []
        for item in items:
            created.append(await self.create(item))
        return created

    async def _generate_single_view(
        self,
        character_id: str,
        view: ReferenceView,
        prompt: str = "",
        style: str = "realistic",
    ) -> dict:
        from services.provider_factory import get_image_service
        if view not in REFERENCE_FILENAMES:
            raise ValueError("Reference view must be front, profile, whole, or sheet")
        profile = await self.get(character_id)
        image_service = get_image_service()
        front_path = (profile.get("reference_images") or {}).get("front") or profile.get("reference_path")
        supports_refs = bool(getattr(image_service, "supports_reference_images", False))
        use_ref = (
            view != "front"
            and bool(front_path)
            and local_asset(front_path).is_file()
            and supports_refs
        )
        full_prompt = self._build_portrait_prompt(profile, view, prompt, style=style, has_reference=use_ref)
        negative_prompt = self._build_portrait_negative_prompt(profile, view, style=style)
        target_ratio = "16:9" if view == "sheet" else "9:16"

        def _invoke():
            if use_ref:
                try:
                    return image_service.generate_image(
                        "character_portraits",
                        f"{uuid4().hex}_{view}",
                        full_prompt,
                        negative_prompt=negative_prompt,
                        ratio=target_ratio,
                        reference_images=[front_path],
                    )
                except (TypeError, ValueError):
                    try:
                        return image_service.generate_image(
                            "character_portraits",
                            f"{uuid4().hex}_{view}",
                            full_prompt,
                            ratio=target_ratio,
                            reference_images=[front_path],
                        )
                    except (TypeError, ValueError):
                        pass
            try:
                return image_service.generate_image(
                    "character_portraits",
                    f"{uuid4().hex}_{view}",
                    full_prompt,
                    negative_prompt=negative_prompt,
                    ratio=target_ratio,
                )
            except TypeError:
                try:
                    return image_service.generate_image(
                        "character_portraits",
                        f"{uuid4().hex}_{view}",
                        full_prompt,
                        ratio=target_ratio,
                    )
                except TypeError:
                    return image_service.generate_image(
                        "character_portraits",
                        f"{uuid4().hex}_{view}",
                        full_prompt,
                    )

        result = await asyncio.to_thread(_invoke)
        if result.get("status") != "success" or not result.get("asset_path"):
            raise ValueError("Image provider did not return a successful image")
        data = local_asset(result["asset_path"]).read_bytes()
        validate_image(data)
        saved = await self.reference(character_id, base64.b64encode(data).decode(), view)
        if view != "front" and not use_ref:
            cur_model = str(getattr(image_service, "model", "") or "当前图片模型")
            if not supports_refs:
                saved["consistency_warning"] = (
                    f"当前图片模型（{cur_model}）不支持参考图输入，因此无法锁定正面照五官来生成同一人的{view}视图。"
                    "请前往「API 配置」将图片模型切换为 Qwen/Qwen-Image-Edit-2509 或 black-forest-labs/FLUX.1-Kontext-dev 后重新生成。"
                )
            else:
                saved["consistency_warning"] = "尚未找到可用的正面定妆照，因此未能锁脸生成；建议先生成或上传「正面」照再重绘其他视图。"
        return saved

    async def portrait(
        self,
        character_id,
        prompt="",
        view: PortraitView = "front",
        style: str = "realistic",
        include_turntable: bool = False,
    ):
        if view == "all":
            latest = None
            warning = ""
            for v in ("front", "profile", "whole"):
                latest = await self._generate_single_view(character_id, v, prompt, style=style)
                if isinstance(latest, dict) and latest.get("consistency_warning"):
                    warning = latest["consistency_warning"]
            if include_turntable:
                try:
                    latest = await self.turntable(character_id, duration=5)
                except Exception as exc:
                    if isinstance(latest, dict):
                        latest = {**latest, "turntable_warning": str(exc)}
            if isinstance(latest, dict) and warning:
                latest["consistency_warning"] = warning
            return latest
        return await self._generate_single_view(character_id, view, prompt, style=style)

    async def turntable(self, character_id: str, duration: int = 5) -> dict:
        from services.provider_factory import get_video_service

        profile = await self.get(character_id)
        images = profile.get("reference_images") or {}
        source_path = images.get("whole") or images.get("front") or profile.get("reference_path")
        if not source_path or not local_asset(source_path).is_file():
            raise ValueError("请先生成或上传角色正面/全身定妆照，再生成 360° 3D 转台视频")
        video_prompt = (
            "3D角色展示转台（Character 360 Turntable），画面中的人物保持标准站立姿态在原地平稳顺滑地360度旋转一周，"
            "全方位立体展示正面、侧面、背面与全身服装细节，纯色摄影棚柔和恒定布光，镜头固定不动，人物五官与服装始终保持一致。"
            f"{profile.get('face_dna') or ''}。{profile.get('signature_clothing') or ''}"
        )
        video_service = get_video_service()
        result = await asyncio.to_thread(
            video_service.generate_video,
            f"char_turntable_{character_id}",
            f"turntable_v{profile.get('revision', 1)}",
            source_path,
            video_prompt,
            duration,
            "9:16",
        )
        video_path = result.get("asset_path")
        if not video_path:
            raise ValueError("视频服务未返回有效的 360° 转台视频路径")
        async with self._lock(character_id):
            doc = await self._document(character_id)
            doc = deepcopy(doc)
            doc["versions"][-1]["turntable_video"] = video_path
            await mongodb.replace_one(
                "characters", {"_id": character_id, "revision": doc["revision"]}, doc
            )
            return _profile_snapshot(doc["versions"][-1])

    async def voice_preview(self, character_id, text):
        from services.tts_service import tts_service
        profile = await self.get(character_id)
        path = f"backend/assets/characters/{character_id}/previews/{uuid4().hex}.mp3"
        duration = await tts_service.generate_voice(text, str(local_asset(path)),
            voice=profile["tts_voice"], rate=profile["rate"], pitch=profile["pitch"])
        return {"asset_path": path, "duration": duration}

    async def get_confirmed_profiles(self, ids: list[str]) -> list[dict]:
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate character IDs")
        profiles = []
        for character_id in ids:
            profile = await self.get(character_id)
            if not profile["confirmed"] or not profile["reference_path"]:
                raise ValueError(f"Character {character_id} must have a confirmed reference")
            for path in profile["reference_images"].values():
                validate_image(local_asset(path).read_bytes())
            profiles.append(deepcopy(profile))
        return profiles

    async def sync_cast_cards_to_library(
        self,
        cards: list[dict],
        *,
        style: str = "短剧",
        overwrite_existing: bool = False,
    ) -> list[dict]:
        from services.image.siliconflow_image_service import is_black_or_collapsed_image
        from services.media_quality import resolve_media

        if not cards:
            return []
        active_chars = await self.list(include_archived=False)
        by_name: dict[str, dict] = {}
        for ch in active_chars:
            key = str(ch.get("name") or "").strip().lower()
            if not key:
                continue
            prev = by_name.get(key)
            prev_has_front = bool(prev and ((prev.get("reference_images") or {}).get("front") or prev.get("reference_path")))
            cur_has_front = bool((ch.get("reference_images") or {}).get("front") or ch.get("reference_path"))
            if prev is None or (not prev_has_front and cur_has_front):
                by_name[key] = ch

        summary: list[dict] = []
        for card in cards:
            if not isinstance(card, dict):
                continue
            name = str(card.get("name") or "").strip()
            path_str = str(card.get("path") or "").strip()
            if not name or not path_str:
                continue
            img_path = resolve_media(path_str)
            if not img_path.is_file():
                continue
            raw_bytes = img_path.read_bytes()
            if is_black_or_collapsed_image(raw_bytes):
                continue

            sheet_b64 = base64.b64encode(raw_bytes).decode()
            front_bytes = crop_headshot_from_turnaround(raw_bytes)
            front_b64 = base64.b64encode(front_bytes).decode()

            key = name.lower()
            existing = by_name.get(key)
            if existing:
                cid = str(existing["character_id"])
                has_front = bool((existing.get("reference_images") or {}).get("front") or existing.get("reference_path"))
                if has_front and not overwrite_existing:
                    card["saved_character_id"] = cid
                    summary.append({"card_id": card.get("card_id"), "name": name, "character_id": cid, "action": "linked_existing"})
                    continue
                await self.reference(cid, front_b64, "front")
                updated = await self.reference(cid, sheet_b64, "sheet")
                by_name[key] = updated
                action = "updated_existing" if has_front else "populated_existing"
            else:
                appearance = str(card.get("appearance") or "").strip()
                gender_raw = str(card.get("gender") or "").lower()
                is_female = gender_raw == "female" or any(tok in appearance for tok in ("女性", "女孩", "女主", "女，", "女,"))
                tts_voice = "zh-CN-XiaoxiaoNeural" if is_female else "zh-CN-YunxiNeural"
                face_dna = appearance
                clothing = "剧集设定造型"
                if "默认服装：" in appearance:
                    head, tail = appearance.split("默认服装：", 1)
                    face_dna = head.strip("；; ")
                    clothing = tail.strip() or clothing
                elif "；" in appearance or ";" in appearance:
                    sep = "；" if "；" in appearance else ";"
                    parts = [p.strip() for p in appearance.split(sep, 1)]
                    if len(parts) == 2 and parts[0] and parts[1]:
                        face_dna, clothing = parts[0], parts[1]
                payload = CharacterFields(
                    name=name[:100],
                    face_dna=face_dna[:4000],
                    signature_clothing=clothing[:2000],
                    tts_voice=tts_voice,
                )
                created = await self.create(payload)
                cid = str(created["character_id"])
                await self.reference(cid, front_b64, "front")
                saved = await self.reference(cid, sheet_b64, "sheet")
                by_name[key] = saved
                action = "created"

            card["saved_character_id"] = cid
            summary.append({"card_id": card.get("card_id"), "name": name, "character_id": cid, "action": action})
        return summary


character_service = CharacterService()


async def get_confirmed_profiles(ids: list[str]) -> list[dict]:
    return await character_service.get_confirmed_profiles(ids)
