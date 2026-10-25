"""개발자 화면 · 사람별 진행 단계(/admin/stats 의 users·userStages): 단계·도달 수·관리자만."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_billing import API, _admin, _bare_ready, _set_persona
from test_persona_setup import BASICS, FakeAI, fake_ai  # noqa: F401
from test_persona_setup import _signup as _signup_once

from app.core import ratelimit
from app.core.config import get_settings
from app.db.models import (
    SOCIAL_PLACEHOLDER_EMAIL_SUFFIX,
    Conversation,
    Message,
    PersonaShare,
    Recording,
    SetupStep,
    SocialAccount,
    Subscription,
    User,
)
from app.db.session import SessionLocal
from app.main import app

ROW_KEYS = {
    "createdAt", "email", "name", "method", "stage", "stageLabel", "personaCount", "readyCount",
    "conversationCount", "messageCount", "lastChatAt", "lastActiveAt", "requestStatus",
    "requestPlan", "subscriptionPlan", "role", "memberOf", "familyJoined",
}  # fmt: skip
STAGE_KEYS = [
    "signed_up", "consented", "persona_started", "materials", "voice", "building", "ready",
    "chatting", "requested", "subscribed", "family_member",
]  # fmt: skip


@pytest.fixture(autouse=True)
def prelaunch(monkeypatch):
    monkeypatch.setattr(get_settings(), "payments_open", False)
    monkeypatch.setattr(get_settings(), "prelaunch_conversations", None)


def _db(fn):
    async def go():
        async with SessionLocal() as db:
            result = await fn(db)
            await db.commit()
            return result

    return asyncio.run(go())


def _user_id(email: str) -> str:
    return _db(lambda db: db.scalar(select(User.id).where(User.email == email)))


def _stats(client: TestClient, h: dict) -> dict:
    response = client.get(f"{API}/admin/stats", headers=h, params={"days": 7})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _chat(client: TestClient, h: dict, pid: str, n: int) -> None:
    cid = client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
        "conversationId"
    ]
    for i in range(n):
        sent = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": f"안녕 {i}"}
        )
        assert sent.status_code == 200, sent.text


def _signup(client: TestClient, email: str) -> dict:
    # 한 시험에서 열 명 넘게 가입한다(같은 주소의 가입 횟수 제한을 비운다)
    ratelimit.reset_all()
    return _signup_once(client, email)


def _persona(client: TestClient, h: dict) -> str:
    return client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]


def test_user_stages_list_funnel_and_admin_only(fake_ai: FakeAI):  # noqa: F811
    e = {k: f"stage-{k}@example.com" for k in STAGE_KEYS + ["deleted"]}
    kakao_name = "단계카카오"
    with TestClient(app) as client:
        admin = _admin(client)
        before = {s["key"]: s for s in _stats(client, admin)["userStages"]}

        # 가입만: 카카오가 이메일을 주지 않았고 필수 동의도 아직
        async def kakao(db):
            user = User(
                email=f"kakao-stage{SOCIAL_PLACEHOLDER_EMAIL_SUFFIX}",
                name=kakao_name,
                password_hash="x",
            )
            db.add(user)
            await db.flush()
            db.add(SocialAccount(user_id=user.id, provider="kakao", provider_user_id="stage-1"))

        _db(kakao)
        # 동의 완료(이메일 가입은 가입 때 동의한다)
        _signup(client, e["consented"])
        # 만들기 시작: 대화 상대만 있고 녹음·자료는 없다
        _persona(client, _signup(client, e["persona_started"]))
        # 녹음 올림
        materials_pid = _persona(client, _signup(client, e["materials"]))

        async def recording(db):
            db.add(
                Recording(
                    persona_id=materials_pid, original_name="a.m4a", storage_key="k", size_bytes=1
                )
            )

        _db(recording)
        # 목소리 고름(질문 답하는 중) · 만드는 중
        _set_persona(_persona(client, _signup(client, e["voice"])), setup_step=SetupStep.QUESTIONS)
        _set_persona(
            _persona(client, _signup(client, e["building"])), setup_step=SetupStep.BUILDING
        )
        # 완성: 다시 만드는 중이어도 한 번 다 만들었으면 완성
        _set_persona(
            _bare_ready(client, _signup(client, e["ready"])),
            setup_step=SetupStep.BUILDING,
            ready_at=datetime.now(UTC),
        )
        # 대화 중
        chat = _signup(client, e["chatting"])
        chat_pid = _bare_ready(client, chat)
        _chat(client, chat, chat_pid, 2)
        # 구독 신청(대화 전이어도 신청이 더 뒤 단계)
        req = _signup(client, e["requested"])
        body = {"personaId": _bare_ready(client, req), "plan": "family"}
        assert client.post(f"{API}/billing/requests", headers=req, json=body).status_code == 200
        # 결제함
        paid_pid = _bare_ready(client, _signup(client, e["subscribed"]))
        paid_id = _user_id(e["subscribed"])

        async def subscribe(db):
            db.add(
                Subscription(
                    user_id=paid_id,
                    persona_id=paid_pid,
                    plan="family",
                    provider="mock",
                    customer_key="stage-ck",
                )
            )

        _db(subscribe)
        # 가족: 제 대화 상대 없이 '대화 중' 사람의 대화 상대에 들어와 한 번 이야기했다
        _signup(client, e["family_member"])
        member_id = _user_id(e["family_member"])

        async def join(db):
            db.add(
                PersonaShare(
                    persona_id=chat_pid,
                    token_hash=uuid4().hex + uuid4().hex,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                    user_id=member_id,
                    joined_at=datetime.now(UTC),
                    profile={"myName": "가족"},
                )
            )
            conversation = Conversation(user_id=member_id, persona_id=chat_pid)
            db.add(conversation)
            await db.flush()
            db.add(Message(conversation_id=conversation.id, role="user", content="안녕"))

        _db(join)
        # 탈퇴한 사람은 목록에 없다
        gone = _signup(client, e["deleted"])
        _persona(client, gone)
        left = client.request("DELETE", f"{API}/auth/me", headers=gone, json={"confirm": "탈퇴"})
        assert left.status_code == 200

        stats = _stats(client, admin)
        users = stats["users"]
        by_email = {u["email"]: u for u in users if u["email"]}
        assert all(set(u) == ROW_KEYS for u in users)
        assert e["deleted"] not in by_email
        assert not any((u["email"] or "").startswith("deleted-") for u in users)
        assert get_settings().bootstrap_admin_email not in by_email  # 관리자는 세지 않는다
        # 최근 가입 순
        stamps = [u["createdAt"] for u in users]
        assert stamps == sorted(stamps, reverse=True)

        stages = {
            key: by_email[email]["stage"]
            for key, email in e.items()
            if key not in ("deleted", "signed_up")
        }
        kakao_row = next(u for u in users if u["name"] == kakao_name)
        stages["signed_up"] = kakao_row["stage"]
        assert stages == {key: key for key in STAGE_KEYS}
        assert kakao_row["email"] is None and kakao_row["method"] == "kakao"
        assert kakao_row["stageLabel"] == "가입만" and kakao_row["role"] is None

        started = by_email[e["persona_started"]]
        assert started["stageLabel"] == "만들기 시작"
        assert (started["personaCount"], started["readyCount"], started["role"]) == (1, 0, "owner")
        assert by_email[e["ready"]]["readyCount"] == 1
        chatting = by_email[e["chatting"]]
        assert chatting["messageCount"] == 2 and chatting["conversationCount"] == 1
        assert chatting["lastChatAt"] and chatting["lastActiveAt"] >= chatting["lastChatAt"]
        assert chatting["familyJoined"] == 1 and chatting["memberOf"] == 0
        requested = by_email[e["requested"]]
        assert (requested["requestStatus"], requested["requestPlan"]) == ("REQUESTED", "family")
        assert requested["messageCount"] == 0
        assert by_email[e["subscribed"]]["subscriptionPlan"] == "family"
        member = by_email[e["family_member"]]
        assert member["stageLabel"] == "가족 초대로 들어옴" and member["role"] == "member"
        assert (member["memberOf"], member["personaCount"], member["messageCount"]) == (1, 0, 1)
        assert by_email[e["consented"]]["lastActiveAt"]

        # 단계별 사람 수: 도달(뒤 단계에 온 사람은 앞 단계도 센다)·지금. 가족은 갈래
        after = {s["key"]: s for s in stats["userStages"]}
        assert [s["key"] for s in stats["userStages"]] == STAGE_KEYS
        assert after["signed_up"]["label"] == "가입만"
        assert [s["branch"] for s in stats["userStages"]] == [False] * 10 + [True]
        reached = {k: after[k]["reached"] - before[k]["reached"] for k in STAGE_KEYS}
        current = {k: after[k]["current"] - before[k]["current"] for k in STAGE_KEYS}
        assert reached == {
            "signed_up": 11, "consented": 10, "persona_started": 8, "materials": 7, "voice": 6,
            "building": 5, "ready": 4, "chatting": 3, "requested": 2, "subscribed": 1,
            "family_member": 1,
        }  # fmt: skip
        assert current == dict.fromkeys(STAGE_KEYS, 1)

        # 관리자만 본다
        assert client.get(f"{API}/admin/stats", headers=chat).status_code == 403
        assert client.get(f"{API}/admin/stats").status_code == 401
