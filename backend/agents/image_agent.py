import asyncio
from datetime import UTC, datetime
from pathlib import Path
from services.visual_style import lock_visual_style, PHOTO_EXCLUSIONS
from services.character_binding import frozen_characters, reference_choices_for_scene
from services.generation_dispatch import dispatch_generation
from services.generation_usage import GenerationLimitExceeded, record_reuse
from services.image.siliconflow_image_service import is_black_or_collapsed_image
from uuid import uuid4

import anyio


def _is_usable_image_file(path_obj: Path) -> bool:
    """Return True if path exists cleanly on disk and is not a collapsed pure-black frame."""
    if not path_obj.is_file() or path_obj.stat().st_size <= 1024:
        return False
    try:
        return not is_black_or_collapsed_image(path_obj.read_bytes())
    except OSError:
        return False


def _build_implicit_turnaround_prompt(char_info: dict, style: str | None) -> str:
    """为未绑定角色资产库的剧组流任务生成 16:9 纯灰无阴影 4 视图人物设定卡。"""
    name = str(char_info.get("name") or "主角").strip()
    gender = "男" if str(char_info.get("gender") or "").lower() == "male" else (
        "女" if str(char_info.get("gender") or "").lower() == "female" else ""
    )
    appearance = str(char_info.get("appearance") or char_info.get("face_dna") or "").strip()
    style_hint = "写实电影级影视剧照质感，真实皮肤纹理" if style == "短剧" else "高清统一漫画/插画设定线稿上色风格"
    return (
        f"角色设定原画4视图，16:9横版构图，无多余元素的浅灰纯色影棚背景（#EFEFEF），角色无阴影，{style_hint}。"
        f"角色信息：{name}（{gender}），外貌特征：{appearance or '五官清晰自然'}。"
        f"画面布局严格分区：左区为角色正脸大特写（面部清晰占满左区，五官/发型/配饰极度清晰，无身体入镜）；"
        f"右区为标准全身三视图横向并排（侧视图、正视图、背视图），从头到脚完整无遮挡、人物高度统一占右区高度80%。"
        f"核心约束：特写与三视图100%为同一人，85mm焦距无畸变平视，中性平静表情（无喜怒哀乐），"
        f"自然站立，双手自然下垂，空手（无手持物），身上无背负物，无文字无水印。"
    )


def _match_cast_card_paths(scene: dict, cast_cards: list[dict], max_refs: int) -> list[str]:
    """按分镜实际出场人名或台词归属，精准匹配对应的纯底人物设定卡（≤2人）。"""
    if not cast_cards or max_refs <= 0:
        return []
    shot_label = str(scene.get("shot_type") or "")
    text_pool = " ".join([
        str(scene.get("visual_description") or ""),
        str(scene.get("image_prompt") or ""),
        str(scene.get("speaker") or ""),
        " ".join(str(line.get("speaker") or "") for line in (scene.get("dialogue_lines") or [])),
    ])
    if "空镜" in shot_label and not any(c["name"] in text_pool for c in cast_cards if c.get("name")):
        return []
    matched: list[str] = []
    for card in cast_cards:
        cname = str(card.get("name") or "").strip()
        if not cname:
            continue
        short = cname.lstrip("小老") if len(cname) >= 3 else cname
        if cname in text_pool or (len(short) >= 2 and short in text_pool):
            matched.append(card["path"])
    if not matched:
        # 若该镜明确写明了无设定卡的新说话人（如配角/家长），不再强行塞入主角1的设定卡防串脸
        speaker = str(scene.get("speaker") or "").strip()
        explicit_other = speaker and speaker not in {"旁白", "解说", "无", "narrator"}
        if not explicit_other and any(k in text_pool for k in ("主角", "男主", "女主", "他", "她", "少年", "女孩", "男孩")):
            matched = [cast_cards[0]["path"]]
    return matched[: min(2, max_refs)]


def resolve_scene_image_inputs(
    task: dict,
    scene: dict,
    image_prompt: str,
    image_service: object,
    *,
    implicit_cast_cards: list[dict] | None = None,
    anchor_path: str | None = None,
) -> tuple[str, list[str], list[dict]]:
    """Resolve safe reference images and prompt directives for generating or rerunning a scene keyframe."""
    from services.media_quality import resolve_media

    supports_refs = bool(getattr(image_service, "supports_reference_images", False))
    max_refs = int(getattr(image_service, "max_reference_images", 0) or 0) if supports_refs else 0
    choices = (
        reference_choices_for_scene(task or {}, scene, max_refs=min(2, max_refs))
        if max_refs > 0
        else []
    )
    ref_paths = [item["path"] for item in choices]
    skip_studio_cast_edit = bool(getattr(image_service, "prefers_t2i_for_studio_cast_sheets", False))
    # Edit models (e.g. Qwen/Qwen-Image-Edit*) paint black bars or collapse when composing >=2 studio portraits
    # into a brand-new multi-character scene; keep choices metadata but route directly to full-bleed T2I.
    if skip_studio_cast_edit and len(ref_paths) >= 2:
        ref_paths = []

    env_anchor = str(scene.get("scene_anchor_path") or "").strip()
    if env_anchor and len(ref_paths) < max_refs:
        if resolve_media(env_anchor).is_file() and env_anchor not in ref_paths:
            ref_paths.append(env_anchor)
            image_prompt += "\n[环境参考锚定] 仅继承最后一张场景图的空间装修、材质与主光源方向，严格按本镜机位构图重排。"
    elif not ref_paths and not choices and implicit_cast_cards and max_refs >= 1 and not skip_studio_cast_edit:
        matched = [
            p for p in _match_cast_card_paths(scene, implicit_cast_cards, max_refs)
            if resolve_media(p).is_file()
        ]
        if matched:
            ref_paths = matched
            image_prompt += "\n[角色外貌定妆参考] 严格保持参考图人物的五官、发型、脸型与辨识特征一致，将人物置入全新实景电影镜头中，按本镜完整场景、构图景别、服装与戏剧光影重新拍摄。"
    elif not ref_paths and not choices and anchor_path and max_refs >= 1:
        if resolve_media(anchor_path).is_file():
            ref_paths = [anchor_path]
            image_prompt += "\n[同片场造型与环境锚定] 严格保持参考图1中人物五官、发型、服装款式与房间装修色调一致；仅继承人物造型与片场光色，人物姿态、走位距离、表情与机位景别必须完全按本镜新分镜重新构图。"
    return image_prompt, ref_paths, choices


def build_end_frame_prompt(scene: dict, custom_prompt: str | None = None) -> str:
    """基于首帧人物、机位和场景，构造同镜头结束定格态尾帧提示词。"""
    end_goal = str(
        custom_prompt
        or scene.get("end_state")
        or scene.get("last_frame_for_transition")
        or scene.get("visual_description")
        or ""
    ).strip()
    shot_type = str(scene.get("shot_type") or "").strip()
    style_cam = f" 保持景别：{shot_type}。" if shot_type else ""
    return (
        f"[尾帧动作定格] 严格继承参考首帧中的同一人物五官、服装、场景装修、拍摄轴线与主光色温，"
        f"仅将人物姿态、视线与手中道具推进并定格在本镜结束状态：{end_goal or '动作自然收束停稳'}。{style_cam}无文字无水印。"
    )

from agents.agent_utils import (
    ASSETS_COLLECTION,
    SCENES_COLLECTION,
    mark_agent_failed,
    mark_agent_start,
    mark_agent_success,
    with_document_id,
)
from config import settings
from core.database import mongodb
from models.asset_model import Asset
from models.task_model import VideoTaskStatus
from services.provider_factory import get_image_service


class ImageAgent:
    agent_name = "ImageAgent"

    async def run(self, task_id: str) -> list[dict[str, object]]:
        try:
            await mark_agent_start(task_id, self.agent_name, progress=75)

            scenes = await mongodb.find_many(SCENES_COLLECTION, {"task_id": task_id}, limit=100)
            task_scenes = [scene for scene in scenes]
            task_scenes.sort(key=lambda scene: int(scene.get("scene_index", 0)))
            if not task_scenes:
                raise ValueError(f"Scenes not found for task: {task_id}")

            from services.generation_contract import STORYBOARD_FLOW_MODE, needs_video
            from services.model_config import generation_limits
            task_scenes = [scene for scene in task_scenes
                           if not (needs_video(scene) and scene.get("transition_type") == "continue_action")]
            task = await mongodb.find_one("video_tasks", {"_id": task_id})
            limits = generation_limits(settings)
            max_images = int(limits.get("max_images_per_task", 20))
            task_gen_limit = (task or {}).get("generation_limit")
            if isinstance(task_gen_limit, int) and task_gen_limit > 0 and max_images > 0:
                max_images = max(max_images, task_gen_limit)
            if max_images > 0 and len(task_scenes) > max_images:
                raise ValueError(
                    f"分镜共需 {len(task_scenes)} 张首帧图，超过当前上限 {max_images} 张；"
                    "请前往「API 配置 → 生成额度上限」调高或设为 0（不限制），或缩短目标时长后重试"
                )
            image_service = get_image_service()
            is_storyboard_flow = (task or {}).get("generation_mode") == STORYBOARD_FLOW_MODE
            supports_refs = bool(getattr(image_service, "supports_reference_images", False))
            if frozen_characters(task or {}) and not supports_refs:
                print(
                    f"[ImageAgent] Task {task_id}: image model {getattr(image_service, 'model', '')} "
                    "does not accept reference images; using character text DNA & style locks.",
                    flush=True,
                )
            task_ratio = str(task.get("ratio", "9:16")) if task else "9:16"

            assets: list[dict[str, object]] = []
            now = datetime.now(UTC)
            backend_dir = Path(__file__).resolve().parents[1]

            # ── 收集需要生图的分镜（跳过已有图片的） ──────────────
            scenes_to_generate = []
            for scene in task_scenes:
                scene_id = str(scene["scene_id"])

                disk_img = backend_dir / "assets" / task_id / "images" / f"{scene_id}.png"
                existing_path = scene.get("image_path")
                existing_file = (
                    (backend_dir / str(existing_path).replace("backend/", ""))
                    if existing_path
                    else None
                )
                has_valid_img = (
                    (existing_file is not None and _is_usable_image_file(existing_file))
                    or _is_usable_image_file(disk_img)
                )

                if has_valid_img:
                    rel_path = f"backend/assets/{task_id}/images/{scene_id}.png"
                    existing_asset = await mongodb.find_one(
                        ASSETS_COLLECTION,
                        {"task_id": task_id, "scene_id": scene_id, "asset_type": "image"}
                    )
                    if not existing_asset:
                        asset = Asset(
                            asset_id=f"asset_{uuid4().hex[:12]}",
                            task_id=task_id,
                            scene_id=scene_id,
                            asset_type="image",
                            path=rel_path,
                            source=str(image_service.__class__.__name__),
                            status="success",
                            created_at=now,
                            updated_at=now,
                        )
                        await mongodb.replace_one(
                            ASSETS_COLLECTION,
                            {"_id": asset.asset_id},
                            with_document_id(asset.model_dump(), asset.asset_id),
                            upsert=True,
                        )
                        existing_asset = asset.model_dump()
                    choices = (
                        reference_choices_for_scene(task or {}, scene)
                        if bool(getattr(image_service, "supports_reference_images", False))
                        else []
                    )
                    preserved_status = "video_done" if scene.get("video_path") else "image_done"
                    reuse_fields: dict[str, object] = {
                        "image_path": rel_path,
                        "reference_choices": choices,
                        "status": preserved_status,
                    }
                    if scene.get("status") == "static_fallback" and not scene.get("video_path"):
                        reuse_fields["need_dynamic_video"] = True
                    if scene.get("image_path") != rel_path or not scene.get("updated_at"):
                        reuse_fields["updated_at"] = now
                    await mongodb.update_one(
                        SCENES_COLLECTION,
                        {"_id": f"{task_id}_{scene_id}"},
                        reuse_fields,
                    )
                    assets.append(existing_asset)
                    await record_reuse(task or {"task_id": task_id}, "image", scene_id)
                    print(f"[ImageAgent] Image already exists for {scene_id}, reused existing file.")
                    continue

                scenes_to_generate.append(scene)

            if not scenes_to_generate:
                await mark_agent_success(
                    task_id,
                    self.agent_name,
                    progress=85,
                    status=VideoTaskStatus.IMAGE_GENERATING,
                    message="All images already exist, skipping generation",
                    extra_fields={"metadata.image_count": len(assets)},
                )
                return assets

            # ── 并行生成所有图片 ──
            # 1) 剧组自由流模式且未选角色库：隐式先绘纯灰底 4 视图角色设定卡，绝不把实景第1镜直接当参考锁定机位；
            # 2) 其他模式且无角色库：优先画第1个人物镜作为片场锚点。
            print(f"[ImageAgent] Generating {len(scenes_to_generate)} images...", flush=True)
            dynamic_anchor_path: str | None = None
            implicit_cast_cards: list[dict] = []
            has_frozen_refs = bool(frozen_characters(task or {}))
            can_anchor = (
                not has_frozen_refs
                and getattr(image_service, "supports_reference_images", False)
                and int(getattr(image_service, "max_reference_images", 0) or 0) >= 1
            )
            sem = anyio.Semaphore(4)  # 限制最多 4 个并发，避免打满线程池

            if is_storyboard_flow and can_anchor:
                from services.media_quality import resolve_media
                raw_plan_chars = [
                    dict(c) for c in ((task or {}).get("metadata", {}).get("task_plan") or {}).get("characters") or []
                    if isinstance(c, dict) and str(c.get("name") or "").strip()
                ]
                seen_names: dict[str, int] = {}
                for sc in task_scenes:
                    sc_pool = f"{sc.get('visual_description') or ''} {sc.get('image_prompt') or ''}"
                    for ch in raw_plan_chars:
                        cname = str(ch.get("name") or "").strip()
                        if cname and cname in sc_pool:
                            seen_names[cname] = seen_names.get(cname, 0) + 1
                    for spk in [sc.get("speaker")] + [l.get("speaker") for l in (sc.get("dialogue_lines") or [])]:
                        s = str(spk or "").strip()
                        if s and s not in {"旁白", "解说", "无", "narrator"}:
                            seen_names[s] = seen_names.get(s, 0) + 1
                known_by_name = {str(c.get("name") or "").strip(): c for c in raw_plan_chars}
                order_rank: dict[str, int] = {str(c.get("name") or "").strip(): i for i, c in enumerate(raw_plan_chars)}
                for n in seen_names:
                    if n not in known_by_name:
                        known_by_name[n] = {"name": n}
                    order_rank.setdefault(n, len(order_rank))
                ordered_names = sorted(
                    known_by_name.keys(),
                    key=lambda nm: (-seen_names.get(nm, 0), order_rank.get(nm, 999)),
                )
                headroom = 4 if max_images <= 0 else max(0, max_images - len(task_scenes))
                max_cards = min(4, len(ordered_names), headroom) if headroom > 0 else 0
                card_targets = [known_by_name[nm] for nm in ordered_names[:max_cards]]

                async def _prepare_cast_card(idx: int, ch: dict) -> dict | None:
                    card_id = f"cast_ref_{idx + 1}"
                    expected_rel = f"backend/assets/{task_id}/images/{card_id}.png"
                    disk_card = resolve_media(expected_rel)
                    card_prompt = _build_implicit_turnaround_prompt(ch, (task or {}).get("style"))
                    if _is_usable_image_file(disk_card):
                        card_path = expected_rel
                    else:
                        try:
                            async with sem:
                                card_res = await dispatch_generation(
                                    task or {"task_id": task_id},
                                    "image",
                                    card_id,
                                    lambda _cid=card_id, _cp=card_prompt: image_service.generate_image(
                                        task_id=task_id,
                                        scene_id=_cid,
                                        image_prompt=_cp,
                                        negative_prompt="复杂场景, 阴影, 道具, 手持物, 背包, 变形, 乱码文字",
                                        ratio="16:9",
                                    ),
                                )
                            if not (isinstance(card_res, dict) and card_res.get("status") == "success" and card_res.get("asset_path")):
                                return None
                            card_path = str(card_res["asset_path"])
                        except GenerationLimitExceeded:
                            raise
                        except Exception as exc:
                            print(f"[ImageAgent] Implicit cast card skipped safely for {ch['name']}: {exc}", flush=True)
                            return None
                    card_now = datetime.now(UTC)
                    card_asset = Asset(
                        asset_id=f"asset_{task_id}_{card_id}",
                        task_id=task_id,
                        scene_id=card_id,
                        asset_type="character_ref",
                        path=card_path,
                        source=str(image_service.__class__.__name__),
                        status="success",
                        created_at=card_now,
                        updated_at=card_now,
                    )
                    await mongodb.replace_one(
                        ASSETS_COLLECTION,
                        {"_id": card_asset.asset_id},
                        with_document_id(card_asset.model_dump(), card_asset.asset_id),
                        upsert=True,
                    )
                    return {
                        "card_id": card_id,
                        "name": str(ch.get("name") or "").strip(),
                        "gender": str(ch.get("gender") or "").strip(),
                        "appearance": str(ch.get("appearance") or ch.get("face_dna") or "").strip(),
                        "path": card_path,
                        "prompt": card_prompt,
                        "updated_at": card_now.isoformat(),
                    }

                if card_targets:
                    card_results = await asyncio.gather(
                        *(_prepare_cast_card(idx, ch) for idx, ch in enumerate(card_targets)),
                        return_exceptions=True,
                    )
                    for cr in card_results:
                        if isinstance(cr, GenerationLimitExceeded):
                            raise cr
                        if isinstance(cr, dict) and cr.get("path"):
                            implicit_cast_cards.append(cr)
                    if implicit_cast_cards:
                        try:
                            from services.character_service import character_service

                            await character_service.sync_cast_cards_to_library(
                                implicit_cast_cards,
                                style=str((task or {}).get("style") or "短剧"),
                            )
                        except Exception as sync_exc:
                            print(f"[ImageAgent] Cast card auto-sync skipped safely: {sync_exc}", flush=True)
                        await mongodb.update_one(
                            "video_tasks",
                            {"_id": task_id},
                            {"metadata.implicit_cast_cards": implicit_cast_cards},
                        )
            elif can_anchor and assets:
                # 非自由流模式下，若前序镜头已就绪，复用其作为首镜视觉锚点
                for existing in assets:
                    cand = str(existing.get("path") or "").strip()
                    if cand:
                        dynamic_anchor_path = cand
                        break

            async def _generate_one(scene: dict, anchor_path: str | None = None) -> dict | None:
                scene_id = str(scene["scene_id"])
                image_prompt = lock_visual_style(str(scene.get("image_prompt") or ""), (task or {}).get("style"))
                negative_prompt = (
                    scene.get("image_negative_prompt") or scene.get("negative_prompt")
                    if isinstance(scene.get("image_negative_prompt") or scene.get("negative_prompt"), str)
                    else None
                )
                if (task or {}).get("style") == "短剧":
                    negative_prompt = PHOTO_EXCLUSIONS + "，" + (negative_prompt or "")
                image_prompt, ref_paths, choices = resolve_scene_image_inputs(
                    task or {},
                    scene,
                    image_prompt,
                    image_service,
                    implicit_cast_cards=implicit_cast_cards,
                    anchor_path=anchor_path,
                )
                reference_options = {"reference_images": ref_paths} if ref_paths else {}
                try:
                    result = await dispatch_generation(task or {"task_id": task_id}, "image", scene_id,
                        lambda _sid=scene_id, _ip=image_prompt, _np=negative_prompt, _ratio=task_ratio, _ro=reference_options:
                            image_service.generate_image(
                                task_id=task_id,
                                scene_id=_sid,
                                image_prompt=_ip,
                                negative_prompt=_np,
                                ratio=_ratio,
                                **_ro,
                            )
                    )
                    if isinstance(result, dict):
                        result["reference_choices"] = choices
                    print(f"[ImageAgent] Scene {scene_id} image generated successfully.")
                    return result
                except GenerationLimitExceeded:
                    raise
                except Exception as e:
                    print(f"[ImageAgent] Scene {scene_id} image generation failed: {e}")
                    return None

            async def _generate_and_save(scene: dict, anchor_path: str | None = None) -> str | None:
                scene_id = str(scene["scene_id"])
                async with sem:
                    image_result = await _generate_one(scene, anchor_path=anchor_path)
                if not image_result or image_result.get("status") != "success" or not image_result.get("asset_path"):
                    return scene_id

                saved_at = datetime.now(UTC)
                asset = Asset(
                    asset_id=f"asset_{uuid4().hex[:12]}",
                    task_id=task_id,
                    scene_id=scene_id,
                    asset_type="image",
                    path=str(image_result["asset_path"]),
                    source=str(image_result.get("source", "qwen")),
                    status=str(image_result.get("status", "success")),
                    created_at=saved_at,
                    updated_at=saved_at,
                )
                await mongodb.replace_one(
                    ASSETS_COLLECTION,
                    {"_id": asset.asset_id},
                    with_document_id(asset.model_dump(), asset.asset_id),
                    upsert=True,
                )
                scene_update = {
                    "image_path": asset.path,
                    "reference_choices": image_result.get("reference_choices") or [],
                    "image_version": uuid4().hex[:8],
                    "status": "image_done",
                    "updated_at": saved_at,
                }
                if scene.get("need_end_frame") is True and getattr(image_service, "supports_reference_images", False):
                    end_scene_id = f"{scene_id}_end"
                    end_prompt = lock_visual_style(build_end_frame_prompt(scene), (task or {}).get("style"))
                    try:
                        async with sem:
                            end_res = await dispatch_generation(
                                task or {"task_id": task_id},
                                "image",
                                end_scene_id,
                                lambda _eid=end_scene_id, _ep=end_prompt, _ref=asset.path: image_service.generate_image(
                                    task_id=task_id,
                                    scene_id=_eid,
                                    image_prompt=_ep,
                                    negative_prompt=PHOTO_EXCLUSIONS if (task or {}).get("style") == "短剧" else None,
                                    ratio=task_ratio,
                                    reference_images=[_ref],
                                ),
                            )
                        if isinstance(end_res, dict) and end_res.get("status") == "success" and end_res.get("asset_path"):
                            scene_update["last_frame_target_path"] = str(end_res["asset_path"])
                            scene_update["updated_at"] = datetime.now(UTC)
                    except GenerationLimitExceeded:
                        raise
                    except Exception as end_exc:
                        print(f"[ImageAgent] Optional end frame skipped for {scene_id}: {end_exc}", flush=True)

                await mongodb.update_one(
                    SCENES_COLLECTION,
                    {"_id": f"{task_id}_{scene_id}"},
                    scene_update,
                )
                assets.append(asset.model_dump())

                return None

            remaining_scenes = list(scenes_to_generate)
            if can_anchor and not is_storyboard_flow and not dynamic_anchor_path and len(remaining_scenes) > 1:
                anchor_idx = 0
                for idx, candidate in enumerate(remaining_scenes):
                    shot_label = str(candidate.get("shot_type") or "").strip()
                    has_person_cues = bool(
                        candidate.get("character_ids")
                        or candidate.get("dialogue_lines")
                        or candidate.get("reaction_focus")
                        or candidate.get("character_state")
                    )
                    if has_person_cues and "空镜" not in shot_label:
                        anchor_idx = idx
                        break
                first_scene = remaining_scenes.pop(anchor_idx)
                first_err = await _generate_and_save(first_scene)
                if first_err is not None:
                    raise RuntimeError(f"Missing generated images: {first_err}")
                if assets:
                    dynamic_anchor_path = str(assets[-1].get("path") or "").strip() or None

            results = await asyncio.gather(
                *(_generate_and_save(scene, anchor_path=dynamic_anchor_path) for scene in remaining_scenes),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            failed = [result for result in results if result is not None]
            if failed:
                raise RuntimeError(f"Missing generated images: {', '.join(failed)}")

            await mark_agent_success(
                task_id,
                self.agent_name,
                progress=85,
                status=VideoTaskStatus.IMAGE_GENERATING,
                message="Images generated successfully",
                extra_fields={"metadata.image_count": len(assets)},
            )
            return assets
        except Exception as exc:
            await mark_agent_failed(task_id, self.agent_name, exc)
            raise
