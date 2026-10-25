"""글 자료 더하기(카카오톡 대화·문자·메일·블로그·일기·메모·SNS).

넣을지 말지는 사용자 선택이다. 지금은 모아 두기만 한다 — 기억 체계와 지침 만들기 과정이 바뀌면
'넣기'가 켜진 자료를 기억 DB·지침 재료로 쓴다(아직 대화에는 쓰지 않는다).

파일은 글 파일만 받는다(.txt .csv .eml .md .html .xml .mbox .json). 압축 파일(zip)은 풀어서 안의 글
파일을 올리게 안내한다. 휴대폰 문자처럼 파일로 못 꺼내는 것은 복사해서 붙여넣는다.
"""

import asyncio
import html
import re
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import Field
from sqlalchemy import func, select

from app.api.deps import DB, CurrentUser
from app.api.v1.persona_setup import _truthy, owned_persona
from app.core import ratelimit
from app.db.models import PersonaMaterial
from app.schemas.common import APIModel, Envelope
from app.services import analytics, legal
from app.services.storage import get_storage

router = APIRouter(tags=["Persona materials"])

KINDS = {
    "kakaotalk": "카카오톡 대화",
    "sms": "문자",
    "email": "메일",
    "blog": "블로그",
    "diary": "일기·메모",
    "sns": "SNS",
    "other": "기타",
}
FILE_TYPES = {".txt", ".csv", ".eml", ".md", ".html", ".htm", ".xml", ".mbox", ".json"}
MAX_FILE_MB = 30
MAX_FILES = 20
MAX_REQUEST_MB = 100  # 한 번에 올리는 파일들의 합
PREVIEW_SOURCE_CHARS = 8000
MAX_PASTE_CHARS = 200_000
MAX_TOTAL_CHARS = 60_000_000  # 페르소나 하나에 모을 수 있는 글자 수(대략 150MB)


class MaterialUpdate(APIModel):
    include: bool | None = None
    title: str | None = Field(default=None, max_length=120)


def _view(item: PersonaMaterial) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "kindLabel": KINDS.get(item.kind, "기타"),
        "title": item.title,
        "source": item.source,
        "filename": item.filename,
        "charCount": item.char_count,
        "preview": item.preview,
        "include": item.include,
        "createdAt": item.created_at,
    }


def _decode(raw: bytes) -> str | None:
    """글 파일을 읽는다. 카카오톡 PC판은 BOM 이 붙고, 오래된 윈도 파일은 CP949 일 수 있다."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates = ["utf-16"]
    else:
        candidates = ["utf-8-sig", "cp949"]
    for encoding in candidates:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" in text:
            return None  # 글이 아닌 파일(사진·압축 등)
        return text.replace("\r\n", "\n").replace("\r", "\n")
    return None


_TAG = re.compile(r"<[^>]+>")


def _preview(text: str, suffix: str) -> str:
    if suffix in (".html", ".htm", ".xml"):
        text = html.unescape(_TAG.sub(" ", text))
    return " ".join(text.split())[:160]


async def _items(db: DB, persona_id: str) -> list[PersonaMaterial]:
    return list(
        (
            await db.scalars(
                select(PersonaMaterial)
                .where(PersonaMaterial.persona_id == persona_id)
                .order_by(PersonaMaterial.created_at.desc())
            )
        ).all()
    )


async def _listing(db: DB, persona_id: str) -> dict:
    items = await _items(db, persona_id)
    return {
        "items": [_view(x) for x in items],
        "includedCount": sum(1 for x in items if x.include),
        "kinds": KINDS,
        "fileTypes": sorted(FILE_TYPES),
        "maxFileMb": MAX_FILE_MB,
        "maxRequestMb": MAX_REQUEST_MB,
        # 아직 대화에 쓰지 않는다(기억 체계가 바뀌면 쓴다)
        "usedInConversation": False,
    }


@router.get("/personas/{persona_id}/materials", response_model=Envelope[dict])
async def list_materials(persona_id: str, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    return Envelope(data=await _listing(db, persona.id))


@router.post("/personas/{persona_id}/materials", response_model=Envelope[dict])
async def add_materials(
    persona_id: str,
    user: CurrentUser,
    db: DB,
    kind: Annotated[str, Form(max_length=20)],
    title: Annotated[str | None, Form(max_length=120)] = None,
    text: Annotated[str | None, Form(max_length=MAX_PASTE_CHARS + 1000)] = None,
    files: Annotated[list[UploadFile] | None, File()] = None,
    pledge: Annotated[str | None, Form()] = None,
):
    """파일 여러 개 또는 붙여넣은 글 하나를 더한다.

    처음 올릴 때 '남긴 글 올리기 전 확인'(pledge=true)을 받는다.
    """
    ratelimit.UPLOADS.check(f"{user.id}:materials")
    persona = await owned_persona(db, persona_id, user.id)
    pledge_type = legal.persona_type("material_pledge", persona.id)
    if _truthy(pledge):
        await legal.record(db, user, pledge_type, True, persona_id=persona.id)
    elif not await legal.has(db, user.id, pledge_type):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "남긴 글을 올리기 전에 확인이 필요해요.")
    if kind not in KINDS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "어떤 글인지 골라 주세요 (카카오톡·문자 등)."
        )
    uploads = [f for f in (files or []) if f.filename]
    pasted = (text or "").strip()
    if not uploads and not pasted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "파일을 고르거나 글을 붙여넣어 주세요.")
    if len(uploads) > MAX_FILES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"한 번에 {MAX_FILES}개까지 올릴 수 있어요."
        )
    if len(pasted) > MAX_PASTE_CHARS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"붙여넣은 글이 너무 길어요 ({MAX_PASTE_CHARS:,}자까지). 나눠서 넣어 주세요.",
        )

    # 이름·형식부터 다 본다(파일을 읽기 전에 거른다)
    for upload in uploads:
        name = Path(upload.filename).name[:200]
        suffix = Path(name).suffix.lower()
        if suffix == ".zip":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{name}: 압축 파일은 먼저 풀어서 안에 있는 글 파일(.txt·.html 등)을 올려 주세요.",
            )
        if suffix not in FILE_TYPES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{name}: 이 파일은 올릴 수 없어요. 카카오톡 대화 파일(.txt)처럼 글로 된 "
                "파일만 올릴 수 있어요. PDF·사진은 안의 글을 복사해 붙여넣어 주세요.",
            )

    used = int(
        await db.scalar(
            select(func.coalesce(func.sum(PersonaMaterial.char_count), 0)).where(
                PersonaMaterial.persona_id == persona.id
            )
        )
        or 0
    )
    storage = get_storage()
    stored: list[str] = []
    rows: list[PersonaMaterial] = []
    total_bytes = 0

    async def keep(item_title: str, filename: str | None, suffix: str, content: str) -> None:
        """한 개씩 저장하고 글은 바로 놓아 준다(여러 개를 한꺼번에 메모리에 두지 않게).

        저장소에 올리기는 따로 돌린다(몇십 MB 를 올리는 동안 다른 요청이 멈추지 않게).
        """
        nonlocal used
        if used + len(content) > MAX_TOTAL_CHARS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "올린 글이 너무 많아요. 필요 없는 글을 지운 뒤 올려 주세요.",
            )
        used += len(content)
        material_id = str(uuid4())
        key = f"personas/{persona.id}/materials/{material_id}{suffix}"
        await asyncio.to_thread(
            storage.put, key, content.encode("utf-8"), "text/plain; charset=utf-8"
        )
        stored.append(key)
        rows.append(
            PersonaMaterial(
                id=material_id,
                persona_id=persona.id,
                kind=kind,
                title=item_title[:120],
                source="file" if filename else "text",
                filename=filename,
                storage_key=key,
                char_count=len(content),
                # 미리 보기는 앞부분만으로 만든다(큰 파일 전체를 쪼개지 않게)
                preview=_preview(content[:PREVIEW_SOURCE_CHARS], suffix) or None,
                include=True,
            )
        )

    try:
        for upload in uploads:
            name = Path(upload.filename).name[:200]
            suffix = Path(name).suffix.lower()
            raw = await upload.read(MAX_FILE_MB * 1024 * 1024 + 1)
            if len(raw) > MAX_FILE_MB * 1024 * 1024:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{name}: 파일이 너무 커요 (최대 {MAX_FILE_MB}MB).",
                )
            total_bytes += len(raw)
            if total_bytes > MAX_REQUEST_MB * 1024 * 1024:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"한 번에 {MAX_REQUEST_MB}MB까지 올릴 수 있어요. 나눠서 올려 주세요.",
                )
            # 글 읽기는 큰 파일이면 오래 걸려 다른 요청을 막지 않게 따로 돌린다
            decoded = await asyncio.to_thread(_decode, raw)
            del raw
            if not decoded or not decoded.strip():
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{name}: 글을 읽지 못했어요.")
            await keep((title or "").strip() or Path(name).stem[:120], name, suffix, decoded)
            del decoded
        if pasted:
            first = " ".join(pasted[:200].split())[:30]
            await keep((title or "").strip() or first or KINDS[kind], None, ".txt", pasted)
    except BaseException:
        # 중간에 막히면 앞서 저장한 파일도 지운다(표에 없는 파일이 남지 않게)
        for key in stored:
            await asyncio.to_thread(storage.delete_prefix, key)
        raise
    db.add_all(rows)
    await db.commit()
    await analytics.track(
        db, "material_added", user_id=user.id, persona_id=persona.id, kind=kind, count=len(rows)
    )
    return Envelope(data=await _listing(db, persona.id))


async def _owned_material(db: DB, persona_id: str, material_id: str) -> PersonaMaterial:
    item = await db.get(PersonaMaterial, material_id)
    if item is None or item.persona_id != persona_id:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "이 글을 찾지 못했어요. 화면을 새로고침해 주세요."
        )
    return item


@router.patch("/personas/{persona_id}/materials/{material_id}", response_model=Envelope[dict])
async def update_material(
    persona_id: str, material_id: str, payload: MaterialUpdate, user: CurrentUser, db: DB
):
    persona = await owned_persona(db, persona_id, user.id)
    item = await _owned_material(db, persona.id, material_id)
    if payload.include is not None:
        item.include = payload.include
    if payload.title is not None and payload.title.strip():
        item.title = payload.title.strip()[:120]
    await db.commit()
    return Envelope(data=await _listing(db, persona.id))


@router.delete("/personas/{persona_id}/materials/{material_id}", response_model=Envelope[dict])
async def delete_material(persona_id: str, material_id: str, user: CurrentUser, db: DB):
    persona = await owned_persona(db, persona_id, user.id)
    item = await _owned_material(db, persona.id, material_id)
    key = item.storage_key
    await db.delete(item)
    await db.commit()
    await asyncio.to_thread(get_storage().delete_prefix, key)
    return Envelope(data=await _listing(db, persona.id))
