from __future__ import annotations
"""Versioned character assets. Stored snapshots and anchor files are never overwritten."""
import asyncio
import base64
import binascii
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import re
from uuid import uuid4

from PIL import Image
from core.database import mongodb
from models.character_model import CharacterFields, CharacterUpdate, PortraitView, ReferenceView
from services.drama_craft_service import (
    STYLE_QUALITY_BLOCKS,
    build_character_sheet_4grid_prompt,
    ensure_shoes_in_clothing,
    infer_anti_drift_negatives,
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
        "【角色设定图 - 正面平视锚点图】角色正面半身至胸口上方定妆照，真人正常7头身比例（严禁Q版大头），"
        "正视镜头，面部五官清晰对称，平静中性表情（neutral expression），双肩端平，双手自然垂放身体两侧，"
        "空手不持握任何独立道具（no props in hand, no pose），纯白无接缝影棚背景（#FFFFFF），45度柔光箱均匀受光，无文字无水印"
    ),
    "profile": (
        "【角色设定图 - 90度正侧面定妆图】同一角色标准90度正侧面定妆照（strict 90-degree side profile view），"
        "鼻梁、下颌线、后脑勺发型层次与肩背侧面轮廓清晰完整，五官特征与服装材质与正面保持完全一致，"
        "身体自然挺直，双手自然垂放身体两侧，空手不持物，纯白无接缝影棚背景（#FFFFFF），无文字无水印"
    ),
    "whole": (
        "【角色设定图 - 正面全身站立标准图】同一角色正面全身直立定妆照（full-body standing front view, head to toe fully visible），"
        "头顶至鞋底完整入画，画面上下各留5%白边（严禁截断小腿或脚部），真人正常7头身比例（严禁Q版或大头娃娃比例），"
        "双腿自然直立，双手自然垂放于身体两侧，空手不持握任何独立道具（no props in hand, no pose），"
        "纯白无接缝影棚背景（#FFFFFF），脚底仅有极淡自然落地投影，无文字无水印"
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
    ) -> str:
        if view == "sheet":
            return build_character_sheet_4grid_prompt(profile, style=style, custom_prompt=custom_prompt)
        if custom_prompt and view == "front" and style == "realistic":
            return custom_prompt
        style_prefix = STYLE_QUALITY_BLOCKS.get(style, STYLE_QUALITY_BLOCKS["realistic"])
        base_desc = VIEW_PROMPTS.get(view, VIEW_PROMPTS["front"])
        face_dna = (profile.get("face_dna") or "").strip()
        clothing = (profile.get("signature_clothing") or "").strip()
        if view == "whole":
            clothing = ensure_shoes_in_clothing(clothing, face_dna)
        anti_drift = infer_anti_drift_negatives(face_dna, clothing, profile.get("name") or "")
        parts = [
            style_prefix,
            base_desc,
            (custom_prompt or "").strip(),
            f"面部五官锚点：{face_dna}" if face_dna else "",
            f"标志服装与鞋履：{clothing}" if clothing else "",
            f"禁止项：{anti_drift}",
        ]
        return "。".join(p.strip("。 ") for p in parts if p and p.strip("。 "))

    async def preview_prompts(self, character_id: str, style: str = "realistic") -> dict:
        profile = await self.get(character_id)
        return {
            "character_id": character_id,
            "name": profile.get("name"),
            "style": style,
            "prompts": {
                "front": self._build_portrait_prompt(profile, "front", style=style),
                "profile": self._build_portrait_prompt(profile, "profile", style=style),
                "whole": self._build_portrait_prompt(profile, "whole", style=style),
                "sheet": self._build_portrait_prompt(profile, "sheet", style=style),
            },
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
        full_prompt = self._build_portrait_prompt(profile, view, prompt, style=style)
        front_path = (profile.get("reference_images") or {}).get("front") or profile.get("reference_path")
        use_ref = (
            view != "front"
            and bool(front_path)
            and local_asset(front_path).is_file()
            and bool(getattr(image_service, "supports_reference_images", False))
        )
        target_ratio = "16:9" if view == "sheet" else "9:16"

        def _invoke():
            if use_ref:
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
        return await self.reference(character_id, base64.b64encode(data).decode(), view)

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
            for v in ("front", "profile", "whole"):
                latest = await self._generate_single_view(character_id, v, prompt, style=style)
            if include_turntable:
                try:
                    latest = await self.turntable(character_id, duration=5)
                except Exception as exc:
                    if isinstance(latest, dict):
                        latest = {**latest, "turntable_warning": str(exc)}
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


character_service = CharacterService()


async def get_confirmed_profiles(ids: list[str]) -> list[dict]:
    return await character_service.get_confirmed_profiles(ids)
