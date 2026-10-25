"""관리자 '페르소나 가져오기'(웹 /admin/import). 처리는 app/services/persona_import.py.

대상 계정 고르기는 GET /admin/users?q= (admin.py) 로 한다(이메일만 믿지 않는다 — 카카오 계정은
이메일이 자리표시일 수 있다). 미리보기(dryRun) → 가져오기 → 필요하면 되돌리기.
"""

from fastapi import APIRouter

from app.api.deps import DB, AdminUser
from app.schemas.common import Envelope
from app.schemas.persona_import import PersonaImportRequest
from app.services import persona_import

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post("/personas/import", response_model=Envelope[dict])
async def import_persona(payload: PersonaImportRequest, admin: AdminUser, db: DB):
    """묶음을 고른 계정의 대화 상대로 넣는다. dryRun(기본 true)이면 쓰지 않고 미리보기만.

    돌려주는 값에는 지침·기억 본문을 싣지 않는다(건수·해시·임베딩 모델·경고만).
    """
    return Envelope(data=await persona_import.run(db, admin, payload))


@router.delete("/personas/{persona_id}/import", response_model=Envelope[dict])
async def undo_import(persona_id: str, admin: AdminUser, db: DB):
    """가져온 대화 상대(origin=import)만 지운다. 만들기 흐름으로 만든 대화 상대는 409."""
    return Envelope(data=await persona_import.undo(db, admin, persona_id))
