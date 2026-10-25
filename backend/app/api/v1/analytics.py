"""화면이 보내는 사용 통계(화면 열기·단추 누름). 허용한 이름과 작은 값만 받고 나머지는 무시한다.

동의 화면·약관 화면을 연 것도 세려고 동의 게이트 앞(AuthedUser)에 둔다.
"""

from typing import Any

from fastapi import APIRouter, Response, status
from pydantic import Field

from app.api.deps import DB, AuthedUser
from app.core import ratelimit
from app.schemas.common import APIModel
from app.services import analytics

router = APIRouter(prefix="/analytics", tags=["Analytics"])


class ClientEvent(APIModel):
    name: str = Field(max_length=50)
    props: dict[str, Any] | None = None


@router.post("/events", status_code=status.HTTP_204_NO_CONTENT)
async def client_event(payload: ClientEvent, user: AuthedUser, db: DB) -> Response:
    """허용한 이름(screen_view·more_tile_clicked·plans_viewed·legal_doc_opened)만 남긴다.
    모르는 이름은 조용히 무시한다(204, 예전 화면이 보내던 pvc_tile_clicked 도)."""
    ratelimit.ANALYTICS.check(user.id)
    allowed = analytics.CLIENT_EVENTS.get(payload.name)
    if allowed is not None:
        props = analytics.sanitize(payload.props, allowed)
        await analytics.track(db, payload.name, user_id=user.id, **props)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
