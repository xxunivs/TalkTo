"""동의문·약관(로그인 없이 읽는다). 원문은 app/static/legal/*.md."""

from fastapi import APIRouter, HTTPException, status

from app.schemas.common import Envelope
from app.services import legal

router = APIRouter(prefix="/legal", tags=["Legal"])


@router.get("", response_model=Envelope[dict])
async def legal_index():
    """문서 목록 · 첫 로그인 필수 동의 항목(화면 순서) · 사업자 정보 · 결제 직전 안내와 체크 글 ·
    법정대리인 체크 글."""
    return Envelope(
        data={
            "docs": [
                {"key": d.key, "title": d.title, "version": d.version, "kind": d.kind}
                for d in legal.docs().values()
            ],
            "account": legal.account_items(),
            "business": legal.business(),
            "payment": {
                "notice": legal.PAYMENT_NOTICE,
                "items": [
                    {"type": kind, "label": label} for kind, label in legal.PAYMENT_LABELS.items()
                ],
            },
            "guardian": dict(legal.GUARDIAN_LABELS),
        }
    )


@router.get("/{key}", response_model=Envelope[dict])
async def legal_doc(key: str):
    """문서 하나. 본문의 자리표시는 채워서 보낸다({{호칭}} 은 화면이 바꾼다)."""
    item = legal.doc(key)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "문서를 찾지 못했어요.")
    return Envelope(
        data={
            "key": item.key,
            "title": item.title,
            "version": item.version,
            "kind": item.kind,
            "body": legal.body(key),
        }
    )
