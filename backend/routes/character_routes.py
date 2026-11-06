from fastapi import APIRouter
from fastapi.responses import JSONResponse
from models.character_model import (
    CharacterFields,
    CharacterUpdate,
    ReferenceUpload,
    PortraitRequest,
    TurntableRequest,
    VoicePreviewRequest,
)
from models.response_model import ApiResponse
from services.character_service import character_service

router = APIRouter(prefix="/api/characters", tags=["characters"])


async def respond(operation):
    try:
        return JSONResponse(content={"status": "success", "data": await operation})
    except LookupError as exc:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(exc)})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})


@router.get("", response_model=ApiResponse)
async def list_characters(include_archived: bool = False):
    return await respond(character_service.list(include_archived=include_archived))


@router.post("", response_model=ApiResponse)
async def create_character(payload: CharacterFields):
    return await respond(character_service.create(payload))


@router.get("/{character_id}", response_model=ApiResponse)
async def get_character(character_id: str):
    return await respond(character_service.get(character_id))


@router.put("/{character_id}", response_model=ApiResponse)
async def update_character(character_id: str, payload: CharacterUpdate):
    return await respond(character_service.update(character_id, payload))


@router.post("/{character_id}/reference", response_model=ApiResponse)
async def upload_reference(character_id: str, payload: ReferenceUpload):
    return await respond(character_service.reference(character_id, payload.image_base64, payload.view))


@router.post("/{character_id}/confirm", response_model=ApiResponse)
async def confirm_character(character_id: str):
    return await respond(character_service.confirm(character_id))


@router.post("/{character_id}/portrait", response_model=ApiResponse)
async def generate_portrait(character_id: str, payload: PortraitRequest = PortraitRequest()):
    return await respond(
        character_service.portrait(
            character_id,
            payload.prompt,
            payload.view,
            payload.style,
            payload.include_turntable,
        )
    )


@router.post("/{character_id}/turntable", response_model=ApiResponse)
async def generate_turntable(character_id: str, payload: TurntableRequest = TurntableRequest()):
    return await respond(character_service.turntable(character_id, payload.duration))


@router.post("/{character_id}/voice-preview", response_model=ApiResponse)
async def preview_voice(character_id: str, payload: VoicePreviewRequest = VoicePreviewRequest()):
    return await respond(character_service.voice_preview(character_id, payload.text))


@router.delete("/{character_id}", response_model=ApiResponse)
async def archive_character(character_id: str):
    return await respond(character_service.archive(character_id))


@router.post("/{character_id}/restore", response_model=ApiResponse)
async def restore_character(character_id: str):
    return await respond(character_service.restore(character_id))
