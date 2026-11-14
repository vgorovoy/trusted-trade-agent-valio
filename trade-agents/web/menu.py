"""Review-only menu extraction. Product ordering remains an explicit separate action."""
from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from trade_agents.buyer_agent.profiles import PROFILES, get_profile
from trade_agents.llm import get_llm

router = APIRouter()
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class MenuDraftRequest(BaseModel):
    text: str = Field(default="", max_length=12_000)
    guests: int = Field(ge=1, le=10_000, strict=True)
    buyer: str = "school"
    image_base64: str | None = Field(default=None, max_length=4 * ((MAX_IMAGE_BYTES + 2) // 3))
    mime: str | None = None


def _image(body: MenuDraftRequest) -> bytes | None:
    if body.image_base64 is None:
        if body.mime is not None:
            raise HTTPException(400, "Attach an image when specifying its type")
        return None
    if body.mime not in ("image/jpeg", "image/png"):
        raise HTTPException(400, "Use a JPEG or PNG image, at most 5 MB")
    try:
        data = base64.b64decode(body.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, "Image must be valid base64") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "Use a JPEG or PNG image, at most 5 MB")
    signature = b"\xff\xd8\xff" if body.mime == "image/jpeg" else b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise HTTPException(400, "Image content does not match its JPEG/PNG type")
    return data


@router.post("/ui/menu/draft")
async def menu_draft(body: MenuDraftRequest) -> dict:
    from trade_agents.buyer_agent.menu_draft import MenuDraftError, draft_menu

    if body.buyer not in PROFILES:
        raise HTTPException(400, "Unknown kitchen profile")
    image = _image(body)
    if not body.text.strip() and image is None:
        raise HTTPException(400, "Describe a dish/menu or attach its image")
    try:
        llm = get_llm()
        if image is not None and not getattr(llm, "vision", False):
            raise HTTPException(400, "This model cannot read images. Use a vision-capable provider "
                                "or enter the recipe as text; offline mode does not support image drafts.")
        profile = get_profile(body.buyer)
        return await run_in_threadpool(draft_menu, llm, body.text.strip(), body.guests,
                                       profile, image, body.mime)
    except HTTPException:
        raise
    except MenuDraftError as exc:
        raise HTTPException(400, "Could not produce a valid menu draft. Add a clearer recipe, "
                            "portion count and any dietary requirements, then try again.") from exc
    except Exception as exc:
        # SDK errors may include request details or credentials. Never forward their text.
        raise HTTPException(502, "Menu model is unavailable. No order was created; "
                            "check provider configuration or try later.") from exc
