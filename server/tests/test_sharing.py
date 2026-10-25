"""가족 공유: 초대 링크 → 미리 보기·확인 → 함께하기 → 가족의 대화(지침·기억·목소리) → 결제는 주인 것
→ 나가기·내보내기·그만두기·탈퇴 정리.

모든 자료는 지어낸 시험용이다(실제 가족 자료를 쓰지 않는다). AI 서버는 가짜(httpx MockTransport)다.
"""

import asyncio
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from legal_gates import guardian_ok
from sqlalchemy import func, select

from app import main as main_module
from app.api.v1 import stats as stats_module
from app.core import ratelimit
from app.core.config import get_settings
from app.db.models import (
    AnalyticsEvent,
    Consent,
    ConsentRecord,
    Conversation,
    DeletionRecord,
    Feedback,
    Memory,
    Message,
    Person,
    Persona,
    PersonaShare,
    SetupStep,
    Subscription,
    UsageEvent,
    User,
    UserRole,
)
from app.db.session import SessionLocal
from app.main import app
from app.services import ai as ai_module
from app.services import analytics, billing, gate, intake, legal, persona_builder, sharing
from app.services.storage import get_storage

API = "/api/v1"
AGREEMENTS = [
    "age14",
    "service",
    "terms",
    "privacy",
    "voice_data",
    "voice_synthesis",
    "overseas",
    "overseas_elevenlabs",
    "overseas_fish",
]
# 주인이 하는 가족 공유 확인 셋(그분 상태에 맞는 것만 기록된다)
ALL_OWNER = ["family_share", "family_share_subject", "family_share_sensitive"]

# ── 지어낸 가족 ──
# 그분(오말순, 할머니) · 만든 사람(지은, 손녀) · 초대받는 가족(철수, 아들)
OWNER_BASICS = {
    "name": "오말순",
    "relation": "할머니",
    "addressTerm": "할머니",
    "status": "deceased",
    "myName": "지은",
    "myRelation": "손녀",
    "calledMe": "우리 강아지",
}
ANSWERS = {
    "C-01": "한두 문장으로 짧게",
    "C-05": "시장에서 오래 장사하신 부지런한 분",
    "M4-01": ["부지런함"],
    "M2-01": [
        {
            "name": "철수",
            "relation": "아들",
            "toMe": "아빠",
            "calledBy": "철수야",
            "status": "alive",
            "note": "서울 삶",
        },
        {"name": "영희", "relation": "딸", "toMe": "고모", "calledBy": "영희야", "status": "alive"},
        {"name": "만복", "relation": "남편", "status": "deceased"},
    ],
    "M2-01-self": "할머니가",
    "M2-01-level": "반말",
    "M2-01-tone": "다정하게",
}
OWNER_INSTRUCTIONS = (
    "# 기본 정보\n- 지금 대화하는 사람(지은)이 부르는 말: 할머니\n(시험용 주인 지침)"
)
SELF_BASICS = intake.validate_basics({"name": "한바다", "relation": "나 자신"})
PROFILE = {
    "myName": "철수",
    "myRelation": "아들",
    "addressTerm": "엄마",
    "calledMe": "철수야",
    "speechLevel": "반말",
    "selfReference": "엄마가",
    "callsOwner": "지은이",
}
# 가져온 대화 상대의 지침: 규칙 → 연결 문단(마지막 줄이 주인) → 옛 본문
RULES = "# 안전 가드레일 (최우선)\n- 시험용 규칙 하나\n- 시험용 규칙 둘"
OWNER_LINE = (
    "- 지금 대화하는 사람은 아래 가족 목록의 '지은'(손녀)이다. 누구인지 다시 확인하지 않아도 "
    "된다. 이 사람을 부를 때는 '우리 강아지'라고 부른다."
)
BRIDGE = (
    "# 아래 페르소나 설명과 함께 쓰는 법\n"
    "- 아래 글은 예전에 손으로 다듬은 대상자의 페르소나 설명이다.\n"
    "- 아래 글과 위 규칙이 어긋나면 위 규칙을 따른다.\n" + OWNER_LINE
)
LEGACY = "당신은 시험용 할머니 페르소나다.\n# 가족\n- 철수: 아들\n- 지은: 손녀(우리 강아지)\n"
IMPORTED = f"{RULES}\n\n{BRIDGE}\n\n{LEGACY}"


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    """.env 와 상관없이 결제 전 모드·공유 켬·AI 서버 없음으로 시작한다.

    시험 안에서 바꾸기도 한다(monkeypatch 라 시험이 끝나면 돌아온다).
    """
    s = get_settings()
    monkeypatch.setattr(s, "payments_open", False)
    monkeypatch.setattr(s, "prelaunch_conversations", None)
    monkeypatch.setattr(s, "payments_test_emails", "")
    monkeypatch.setattr(s, "share_open", True)
    monkeypatch.setattr(s, "share_members_per_persona", 5)
    monkeypatch.setattr(s, "share_link_hours", 168)
    monkeypatch.setattr(s, "ai_server_url", "")
    monkeypatch.setattr(s, "rate_limit_enabled", True)
    return s


class FakeAI:
    """AI 서버 대역. 조립은 '조립본 · {듣는 사람} · {몇 번째}'를 돌려준다."""

    def __init__(self) -> None:
        self.calls: dict[str, list[dict]] = defaultdict(list)
        self.fail_assembly = False
        # 목소리용 글(AI speechText). None 이면 칸을 보내지 않는다
        self.speech_text: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}")
        self.calls[path].append(body)
        if path == "/v1/persona/assembly":
            if self.fail_assembly:
                return httpx.Response(500, json={"detail": "시험용 실패"})
            listener = body["intakeContext"]["familyMap"][0]["name"]
            n = len(self.calls[path])
            return httpx.Response(200, json={"instructions": f"조립본 · {listener} · {n}"})
        if path == "/v1/persona/responses":
            return httpx.Response(
                200,
                json={
                    "content": "그래, 왔구나.",
                    "retrievedMemoryIds": [],
                    **({"speechText": self.speech_text} if self.speech_text else {}),
                    "provider": "fake",
                    "model": "fake",
                },
            )
        if path == "/v1/voice/speech":
            return httpx.Response(200, content=b"ID3-fake-audio")
        return httpx.Response(404, json={"detail": "없음"})

    def assemblies(self) -> list[dict]:
        return self.calls["/v1/persona/assembly"]

    def replies(self) -> list[dict]:
        return self.calls["/v1/persona/responses"]


@pytest.fixture
def fake_ai(monkeypatch):
    fake = FakeAI()
    monkeypatch.setattr(get_settings(), "ai_server_url", "https://ai.example.test")
    transport = httpx.MockTransport(fake)
    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr(ai_module.httpx, "AsyncClient", client_factory)
    return fake


# ── 도우미 ──


def _run(coro):
    return asyncio.run(coro)


def _db(fn):
    """fn(db) 를 따로 연 세션으로 돌린다."""

    async def go():
        async with SessionLocal() as db:
            return await fn(db)

    return _run(go())


def _signup(
    client: TestClient, name: str = "가족", agreements: list[str] = AGREEMENTS
) -> tuple[dict, str, str]:
    """동의를 마친 새 계정. (머리글, 사용자 id, 이메일). 기본은 아홉 가지 모두(만드는 분)."""
    email = f"share-{uuid4().hex[:10]}@example.com"
    response = client.post(
        f"{API}/auth/signup",
        json={"email": email, "password": "password123", "name": name, "agreements": agreements},
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['data']['bearerToken']}"}
    user_id = client.get(f"{API}/auth/me", headers=headers).json()["data"]["id"]
    return headers, user_id, email


def _admin(client: TestClient) -> tuple[dict, str]:
    s = get_settings()
    response = client.post(
        f"{API}/auth/login",
        json={"email": s.bootstrap_admin_email, "password": s.bootstrap_admin_password},
    )
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['data']['bearerToken']}"}
    return headers, client.get(f"{API}/auth/me", headers=headers).json()["data"]["id"]


def _persona(
    owner_id: str,
    *,
    basics: dict = OWNER_BASICS,
    answers: dict = ANSWERS,
    instructions: str | None = OWNER_INSTRUCTIONS,
    origin: str | None = None,
    voice_id: str | None = None,
    voice_provider: str | None = None,
) -> str:
    """다 만든 대화 상대(녹음 없이 DB 에 바로)."""

    async def go(db):
        person = Person(
            owner_user_id=owner_id,
            name=basics["name"],
            relationship_label=basics["relation"],
            address_term=basics.get("addressTerm"),
            life_status=intake.LIFE_STATUS[basics["status"]],
        )
        db.add(person)
        await db.flush()
        persona = Persona(
            person_id=person.id,
            name=basics["name"],
            status="READY",
            setup_step=SetupStep.READY,
            basics=dict(basics),
            answers=json.loads(json.dumps(answers)),
            instructions=instructions,
            origin=origin,
            voice_id=voice_id,
            voice_provider=voice_provider,
            subject_voices=[],
            ready_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        db.add(persona)
        await db.commit()
        return persona.id

    return _db(go)


def _set(model, row_id: str, **values) -> None:
    async def go(db):
        row = await db.get(model, row_id)
        for key, value in values.items():
            setattr(row, key, value)
        await db.commit()

    _db(go)


def _get(model, row_id: str):
    async def go(db):
        return await db.get(model, row_id)

    return _db(go)


def _all(query):
    async def go(db):
        return list((await db.scalars(query)).all())

    return _db(go)


def _count(query) -> int:
    async def go(db):
        return int(await db.scalar(query) or 0)

    return _db(go)


def _consent(user_id: str, consent_type: str) -> Consent | None:
    async def go(db):
        return await db.scalar(
            select(Consent).where(Consent.user_id == user_id, Consent.consent_type == consent_type)
        )

    return _db(go)


def _link(
    client: TestClient,
    h: dict,
    pid: str,
    *,
    prefill: dict | None = None,
    family_row: str | None = None,
    agreements: list[str] = ALL_OWNER,
) -> tuple[str, dict]:
    """초대 링크 하나. (열쇠, ShareItem)."""
    body: dict = {"agreements": agreements}
    if prefill is not None:
        body["prefill"] = prefill
    if family_row is not None:
        body["familyRow"] = family_row
    response = client.post(f"{API}/personas/{pid}/shares", headers=h, json=body)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    return data["url"].rsplit("/", 1)[1], data["share"]


def _accept(client: TestClient, h: dict, token: str, profile: dict = PROFILE):
    return client.post(
        f"{API}/invites/accept", headers=h, json={"token": token, "agree": True, "profile": profile}
    )


@dataclass
class Family:
    owner: dict
    owner_id: str
    owner_email: str
    pid: str
    member: dict
    member_id: str
    member_email: str
    share_id: str


def _family(client: TestClient, profile: dict = PROFILE, **persona_kw) -> Family:
    """주인 + 다 만든 대화 상대 + 들어온 가족 한 사람. 가족도 가입할 때 아홉 가지 모두 동의했다."""
    owner, owner_id, owner_email = _signup(client, "김지은")
    pid = _persona(owner_id, **persona_kw)
    token, _ = _link(client, owner, pid)
    member, member_id, member_email = _signup(client, "박철수")
    joined = _accept(client, member, token, profile)
    assert joined.status_code == 201, joined.text
    assert joined.json()["data"]["already"] is False
    return Family(
        owner,
        owner_id,
        owner_email,
        pid,
        member,
        member_id,
        member_email,
        joined.json()["data"]["shareId"],
    )


def _room(client: TestClient, h: dict, pid: str) -> str:
    response = client.post(f"{API}/personas/{pid}/conversation", headers=h)
    assert response.status_code == 200, response.text
    return response.json()["data"]["conversationId"]


def _say(client: TestClient, h: dict, cid: str, text: str = "안녕"):
    return client.post(
        f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": text}
    )


def _preview(client: TestClient, token: str) -> dict:
    response = client.post(f"{API}/invites/preview", json={"token": token})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _resolve(client: TestClient, h: dict, token: str) -> dict:
    response = client.post(f"{API}/invites/resolve", headers=h, json={"token": token})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _usage(pid: str) -> int:
    return _count(select(func.count(UsageEvent.id)).where(UsageEvent.persona_id == pid))


def _withdraw_owner(owner_id: str, pid: str) -> None:
    async def go(db):
        owner = await db.get(User, owner_id)
        await sharing.withdraw_owner_consents(db, owner, pid)
        await db.commit()

    _db(go)


# ────────────────────────── 1. 링크 ──────────────────────────


def test_owner_confirms_once_by_subject_kind():
    """그분 상태마다 받는 확인.

    돌아가신 분·나 자신·가져온 것은 1개, 살아 계신 분·대화가 어려우신 분은 3개.
    """
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        cases = {
            "deceased": (_persona(uid), ["family_share"]),
            "alive": (_persona(uid, basics=OWNER_BASICS | {"status": "alive"}), ALL_OWNER),
            "unable": (_persona(uid, basics=OWNER_BASICS | {"status": "unable"}), ALL_OWNER),
            "self": (_persona(uid, basics=SELF_BASICS, answers={}), ["family_share"]),
            "import": (
                _persona(uid, origin="import", answers={}, instructions=IMPORTED),
                ["family_share"],
            ),
        }
        for kind, (pid, want) in cases.items():
            refused = client.post(f"{API}/personas/{pid}/shares", headers=h, json={})
            assert refused.status_code == 409, kind
            body = refused.json()
            assert body == {
                "detail": "가족과 함께 쓰기 전에 확인할 것이 있어요.",
                "code": "share_consent_needed",
                "need": want,
            }
            page = client.get(f"{API}/personas/{pid}/shares", headers=h).json()["data"]
            assert page["subjectKind"] == ("deceased" if kind == "import" else kind)
            assert page["need"] == want and [i["type"] for i in page["consentItems"]] == want
            assert all(i["label"] for i in page["consentItems"])
            assert page["allowed"] is True and page["reason"] is None and page["prelaunch"] is True
            # 모르는 종류는 무시하고, 그분 상태에 맞는 것만 남긴다
            _link(client, h, pid, agreements=[*ALL_OWNER, "terms", "unknown"])
            for t in ALL_OWNER:
                row = _consent(uid, f"{t}:{pid}")
                assert (row is not None and row.agreed) == (t in want), (kind, t)
            assert (
                client.get(f"{API}/personas/{pid}/shares", headers=h).json()["data"]["need"] == []
            )
        # 살아 계신 분: 하나만 확인하면 나머지 둘을 묻는다(받은 하나는 남는다)
        pid = _persona(uid, basics=OWNER_BASICS | {"status": "alive"})
        partial = client.post(
            f"{API}/personas/{pid}/shares", headers=h, json={"agreements": ["family_share"]}
        )
        assert partial.status_code == 409
        assert partial.json()["need"] == ["family_share_subject", "family_share_sensitive"]
        assert _consent(uid, f"family_share:{pid}").agreed


def test_cap_counts_open_and_joined_links(settings):
    with TestClient(app) as client:
        fam = _family(client)
        tokens = [_link(client, fam.owner, fam.pid)[0] for _ in range(4)]
        full = client.post(f"{API}/personas/{fam.pid}/shares", headers=fam.owner, json={})
        assert full.status_code == 409
        assert full.json() == {
            "detail": "함께 쓸 수 있는 가족은 5명까지예요. 쓰지 않은 링크를 취소하거나 가족을 "
            "내보낸 뒤 다시 만들어 주세요.",
            "code": "share_full",
            "cap": 5,
        }
        page = client.get(f"{API}/personas/{fam.pid}/shares", headers=fam.owner).json()["data"]
        assert (page["cap"], page["used"]) == (5, 5)
        assert [i["status"] for i in page["items"]] == ["joined", "open", "open", "open", "open"]
        # 쓰지 않은 링크 하나를 취소하면 다시 만들 수 있다
        open_ids = [i["id"] for i in page["items"] if i["status"] == "open"]
        canceled = client.delete(
            f"{API}/personas/{fam.pid}/shares/{open_ids[0]}", headers=fam.owner
        )
        assert canceled.status_code == 200 and canceled.json()["data"] == {"ended": True}
        assert _preview(client, tokens[0])["state"] == "invalid"
        _link(client, fam.owner, fam.pid)
        # 기한이 지난 링크는 자리를 차지하지 않는다(만들 때 지운다)
        _set(PersonaShare, open_ids[1], expires_at=datetime.now(UTC) - timedelta(seconds=1))
        _link(client, fam.owner, fam.pid)
        assert _get(PersonaShare, open_ids[1]) is None
        # 자리 수는 설정으로 바꾼다
        settings.share_members_per_persona = 2
        again = client.post(f"{API}/personas/{fam.pid}/shares", headers=fam.owner, json={})
        assert again.json()["code"] == "share_full" and again.json()["cap"] == 2


def test_family_rows_prefill_and_deceased_row():
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid)
        page = client.get(f"{API}/personas/{pid}/shares", headers=h).json()["data"]
        # 살아 계신 가족만(돌아가신 분·만든 사람 자신은 빼고), 관계·불리던 말을 옮긴다
        assert page["familyRows"] == [
            {
                "familyRow": "철수",
                "myName": "철수",
                "myRelation": "아들",
                "calledMe": "철수야",
                "taken": False,
            },
            {
                "familyRow": "영희",
                "myName": "영희",
                "myRelation": "딸",
                "calledMe": "영희야",
                "taken": False,
            },
        ]
        assert page["addressSuggestions"]["아들"] == ["엄마", "어머니"]
        assert page["addressSuggestions"]["사위"] == ["장모님", "어머님"]
        assert page["addressSuggestions"]["친구"] == []
        dead = client.post(
            f"{API}/personas/{pid}/shares",
            headers=h,
            json={"agreements": ALL_OWNER, "familyRow": "만복"},
        )
        assert dead.status_code == 400
        assert dead.json() == {
            "detail": "돌아가신 분께는 초대 링크를 보낼 수 없어요.",
            "code": "share_deceased_row",
        }
        token, item = _link(client, h, pid, family_row="철수", prefill={"addressTerm": "엄마"})
        assert item["status"] == "open" and item["label"] == "철수"
        assert item["prefill"] == {
            "myName": "철수",
            "myRelation": "아들",
            "addressTerm": "엄마",
            "calledMe": "철수야",
            "familyRow": "철수",
        }
        rows = client.get(f"{API}/personas/{pid}/shares", headers=h).json()["data"]["familyRows"]
        assert [r["taken"] for r in rows] == [True, False]
        # 미리 적은 칸 확인(틀리면 칸 이름과 한국어 한 줄)
        bad = client.post(
            f"{API}/personas/{pid}/shares",
            headers=h,
            json={"prefill": {"calledMe": "가" * 16}},
        )
        assert bad.status_code == 400
        assert bad.json() == {
            "detail": "그분이 나를 부르던 말 칸은 15자까지 적을 수 있어요.",
            "code": "share_bad_profile",
            "field": "calledMe",
        }
        # 받는 분에게는 가족 표 줄 이름·메모·'나에게는'이 가지 않는다
        other, _, _ = _signup(client, "박철수")
        resolved = _resolve(client, other, token)
        assert resolved["state"] == "join" and resolved["fromOwner"] is True
        assert resolved["prefill"] == {
            "myName": "철수",
            "myRelation": "아들",
            "addressTerm": "엄마",
            "calledMe": "철수야",
        }
        text = json.dumps(resolved, ensure_ascii=False)
        for hidden in ("familyRow", "서울 삶", "아빠", "toMe", "note", "오말순"):
            assert hidden not in text
        # 가져온 대화 상대는 가족 표가 없다(이름은 자유롭게 적는다)
        imported = _persona(uid, origin="import", answers={}, instructions=IMPORTED)
        page = client.get(f"{API}/personas/{imported}/shares", headers=h).json()["data"]
        assert page["familyRows"] == []
        _, item = _link(client, h, imported, family_row="철수")
        assert item["prefill"] == {"familyRow": "철수"} and item["label"] is None


def test_token_is_hashed_shown_once_and_renew_keeps_the_seat():
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid)
        response = client.post(
            f"{API}/personas/{pid}/shares", headers=h, json={"agreements": ALL_OWNER}
        )
        url = response.json()["data"]["url"]
        token = url.rsplit("/", 1)[1]
        assert url == f"{get_settings().frontend_url.rstrip('/')}/#/join/{token}"
        assert len(token) >= 32
        share_id = response.json()["data"]["share"]["id"]
        row = _get(PersonaShare, share_id)
        assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
        stored = json.dumps(
            [row.prefill, row.profile, row.token_hash, row.instructions], ensure_ascii=False
        )
        assert token not in stored
        listing = client.get(f"{API}/personas/{pid}/shares", headers=h).text
        assert token not in listing and row.token_hash not in listing
        # 다시 보내기: 같은 자리에 새 열쇠(예전 링크는 열리지 않는다)
        renewed = client.post(f"{API}/personas/{pid}/shares/{share_id}/renew", headers=h)
        assert renewed.status_code == 200, renewed.text
        new_token = renewed.json()["data"]["url"].rsplit("/", 1)[1]
        assert new_token != token and renewed.json()["data"]["share"]["id"] == share_id
        assert _preview(client, token)["state"] == "invalid"
        assert _preview(client, new_token)["state"] == "open"
        # 기한이 지난 링크도 다시 보낼 수 있다(자리를 그대로 쓴다)
        _set(PersonaShare, share_id, expires_at=datetime.now(UTC) - timedelta(hours=1))
        assert _preview(client, new_token)["state"] == "expired"
        third = client.post(f"{API}/personas/{pid}/shares/{share_id}/renew", headers=h)
        assert third.status_code == 200
        latest = third.json()["data"]["url"].rsplit("/", 1)[1]
        assert _preview(client, latest)["state"] == "open"
        # 들어온 자리는 다시 보내지 않는다
        member, _, _ = _signup(client, "박철수")
        assert _accept(client, member, latest).status_code == 201
        joined = client.post(f"{API}/personas/{pid}/shares/{share_id}/renew", headers=h)
        assert joined.status_code == 404 and joined.json()["code"] == "share_not_found"


def test_rate_limits_are_registered():
    assert ratelimit.SHARE_LINKS in ratelimit.ALL and ratelimit.INVITES_IP in ratelimit.ALL
    assert (ratelimit.SHARE_LINKS.limit, ratelimit.SHARE_LINKS.window) == (20, ratelimit.HOUR)
    assert (ratelimit.INVITES_IP.limit, ratelimit.INVITES_IP.window) == (60, 5 * ratelimit.MINUTE)
    with TestClient(app) as client:
        for _ in range(60):
            assert client.post(f"{API}/invites/preview", json={"token": "x"}).status_code == 200
        assert client.post(f"{API}/invites/preview", json={"token": "x"}).status_code == 429


def test_kill_switch_stops_new_links_and_joins_but_not_members(settings):
    with TestClient(app) as client:
        fam = _family(client)
        token, item = _link(client, fam.owner, fam.pid)
        settings.share_open = False
        closed = client.post(f"{API}/personas/{fam.pid}/shares", headers=fam.owner, json={})
        assert closed.status_code == 409
        assert closed.json() == {
            "detail": "잠시 가족 초대를 멈췄어요. 조금 뒤 다시 해 주세요.",
            "code": "share_closed",
        }
        renew = client.post(
            f"{API}/personas/{fam.pid}/shares/{item['id']}/renew", headers=fam.owner
        )
        assert renew.json()["code"] == "share_closed"
        page = client.get(f"{API}/personas/{fam.pid}/shares", headers=fam.owner).json()["data"]
        assert page["allowed"] is False and page["reason"] == "closed"
        assert _preview(client, token)["state"] == "unavailable"
        other, _, _ = _signup(client, "박영희")
        assert _resolve(client, other, token)["state"] == "unavailable"
        refused = _accept(client, other, token)
        assert refused.status_code == 409 and refused.json()["code"] == "share_closed"
        # 이미 함께하는 가족은 그대로 이야기한다
        cid = _room(client, fam.member, fam.pid)
        assert _say(client, fam.member, cid).status_code == 200


# ────────────────────────── 2. 미리 보기·확인 ──────────────────────────


def test_preview_shows_only_who_invited():
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid)
        token, item = _link(client, h, pid, family_row="철수", prefill={"addressTerm": "엄마"})
        opened = _preview(client, token)
        assert set(opened) == {
            "state",
            "ownerName",
            "ownerTitle",
            "isSelf",
            "addressTerm",
            "expiresAt",
        }
        assert (opened["state"], opened["ownerName"], opened["ownerTitle"]) == (
            "open",
            "지은",
            "할머니",
        )
        assert opened["isSelf"] is False and opened["addressTerm"] == "엄마"
        text = json.dumps(opened, ensure_ascii=False)
        for hidden in (pid, "오말순", "철수", "familyRow", "prefill"):
            assert hidden not in text
        assert _preview(client, "없는-열쇠") == {"state": "invalid"}
        assert _preview(client, "") == {"state": "invalid"}
        # 주인의 확인이 빠지면(거둠) 함께할 수 없다
        _withdraw_owner(uid, pid)
        assert _preview(client, token) == {"state": "unavailable"}
        _link(client, h, pid)  # 다시 확인
        assert _preview(client, token)["state"] == "open"
        # 주인이 멈춘 계정이면
        _set(User, uid, status="SUSPENDED")
        assert _preview(client, token) == {"state": "unavailable"}
        _set(User, uid, status="ACTIVE")
        # 기한이 지남
        _set(PersonaShare, item["id"], expires_at=datetime.now(UTC) - timedelta(seconds=1))
        assert _preview(client, token) == {"state": "expired"}
        _set(PersonaShare, item["id"], expires_at=datetime.now(UTC) + timedelta(days=1))
        # 들어온 뒤에는 '다시 오셨네요'(책갈피)
        member, _, _ = _signup(client, "박철수")
        assert _accept(client, member, token).status_code == 201
        used = _preview(client, token)
        assert used == {
            "state": "used",
            "ownerName": "지은",
            "ownerTitle": "할머니",
            "isSelf": False,
        }
        # 나 자신 대화 상대: 호칭 없이
        self_pid = _persona(uid, basics=SELF_BASICS, answers={})
        self_token, _ = _link(client, h, self_pid)
        mine = _preview(client, self_token)
        assert (
            mine["isSelf"] is True and mine["ownerTitle"] is None and mine["ownerName"] == "한바다"
        )


def test_resolve_states_and_bookmark():
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid)
        token, item = _link(client, h, pid)
        second, _ = _link(client, h, pid)
        owner_view = _resolve(client, h, token)
        assert owner_view["state"] == "owner" and owner_view["personaId"] == pid
        member, _, _ = _signup(client, "박철수")
        join = _resolve(client, member, token)
        assert join["state"] == "join" and "personaId" not in join
        assert join["prefill"] == {} and join["fromOwner"] is False
        assert join["relations"] == intake.MY_RELATIONS
        assert join["addressSuggestions"]["손녀"] == ["할머니"]
        assert join["consent"] == {
            "type": "family_share_member",
            "doc": "family_share",
            "label": legal.SHARE_MEMBER_LABEL,
        }
        assert join["voiceShareable"] is True and join["origin"] is None
        assert (join["ownerName"], join["ownerTitle"], join["isSelf"]) == ("지은", "할머니", False)
        assert _accept(client, member, token).status_code == 201
        # 같은 링크(책갈피)나 같은 대화 상대의 다른 링크로 오면 바로 대화로
        for t in (token, second):
            again = _resolve(client, member, t)
            assert again["state"] == "member" and again["personaId"] == pid
        _set(PersonaShare, item["id"], expires_at=datetime.now(UTC) - timedelta(days=30))
        assert _resolve(client, member, token)["state"] == "member"
        # 다른 사람이 쓴 링크
        other, _, _ = _signup(client, "박영희")
        used = _resolve(client, other, token)
        assert used["state"] == "used" and "personaId" not in used
        assert _resolve(client, other, "없는-열쇠") == {"state": "invalid"}
        # 다른 분의 링크는 아직 열려 있다(같은 대화 상대에 이미 있는 분이 링크를 쓰지 않았다)
        assert _resolve(client, other, second)["state"] == "join"


# ────────────────────────── 3. 함께하기 ──────────────────────────


def test_accept_rules(monkeypatch):
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid)
        token, item = _link(client, h, pid, family_row="철수")
        member, member_id, _ = _signup(client, "박철수")
        kind = f"family_share_member:{pid}"

        def accept(profile=PROFILE, agree=True, t=token, who=member):
            return client.post(
                f"{API}/invites/accept",
                headers=who,
                json={"token": t, "agree": agree, "profile": profile},
            )

        own = accept(who=h)
        assert own.status_code == 409 and own.json()["code"] == "invite_own"
        refused = accept(agree=False)
        assert refused.status_code == 400
        assert refused.json() == {
            "detail": "가족 공유 안내에 동의해 주세요.",
            "code": "invite_agree_required",
        }
        for profile, field, detail in (
            ({**PROFILE, "addressTerm": ""}, "addressTerm", "그분을 부르는 말을 적어 주세요."),
            (
                {k: v for k, v in PROFILE.items() if k != "myName"},
                "myName",
                "내 이름을 적어 주세요.",
            ),
            ({**PROFILE, "addressTerm": "나"}, "addressTerm", None),
            ({**PROFILE, "speechLevel": "하오체"}, "speechLevel", None),
            ({**PROFILE, "selfReference": "가" * 16}, "selfReference", None),
        ):
            bad = accept(profile=profile)
            assert bad.status_code == 400, profile
            assert bad.json()["code"] == "share_bad_profile" and bad.json()["field"] == field
            if detail:
                assert bad.json()["detail"] == detail
        assert _consent(member_id, kind) is None
        # 자리를 잡지 못하면 동의도 남지 않는다(같은 트랜잭션)
        real_claim = sharing.claim_seat

        async def lost(*args, **kwargs):
            return False

        monkeypatch.setattr(sharing, "claim_seat", lost)
        race = accept()
        assert race.status_code == 410 and race.json()["code"] == "invite_used"
        assert _consent(member_id, kind) is None
        assert (
            _count(select(func.count(ConsentRecord.id)).where(ConsentRecord.consent_type == kind))
            == 0
        )
        monkeypatch.setattr(sharing, "claim_seat", real_claim)
        # 함께하기
        joined = accept()
        assert joined.status_code == 201
        assert joined.json()["data"] == {"personaId": pid, "shareId": item["id"], "already": False}
        consent = _consent(member_id, kind)
        assert consent.agreed
        record = _all(select(ConsentRecord).where(ConsentRecord.consent_type == kind))
        assert [(r.agreed, r.doc, r.user_id) for r in record] == [(True, "family_share", member_id)]
        seat = _get(PersonaShare, item["id"])
        assert seat.user_id == member_id and seat.joined_at is not None
        assert {k: seat.profile.get(k) for k in PROFILE} == PROFILE
        assert seat.profile["familyRow"] == "철수" and seat.profile["editedBy"] == "member"
        # 두 번째 사람은 같은 링크로 들어오지 못한다
        other, other_id, _ = _signup(client, "박영희")
        second = accept(who=other)
        assert second.status_code == 410
        assert second.json() == {
            "detail": "이미 다른 분이 쓴 초대 링크예요. 보내 준 가족에게 내 링크를 따로 부탁해 "
            "주세요.",
            "code": "invite_used",
        }
        # 이미 함께하는 분은 다른 링크를 써도 그 링크를 쓰지 않는다(200 already)
        spare, spare_item = _link(client, h, pid)
        again = accept(t=spare)
        assert again.status_code == 200
        assert again.json()["data"] == {"personaId": pid, "shareId": item["id"], "already": True}
        assert _get(PersonaShare, spare_item["id"]).user_id is None
        # 기한이 지난 링크
        _set(PersonaShare, spare_item["id"], expires_at=datetime.now(UTC) - timedelta(seconds=1))
        late = accept(who=other, t=spare)
        assert late.status_code == 410 and late.json()["code"] == "invite_expired"
        # 멈춘 대화 상대에는 들어오지 못한다(멈춘 채 들어온 가족을 만들지 않는다)
        _set(PersonaShare, spare_item["id"], expires_at=datetime.now(UTC) + timedelta(days=1))
        _withdraw_owner(uid, pid)
        paused = accept(who=other, t=spare)
        assert paused.status_code == 409 and paused.json()["code"] == "invite_unavailable"
        missing = accept(who=other, t="없는-열쇠")
        assert missing.status_code == 404 and missing.json()["code"] == "invite_invalid"
        assert _consent(other_id, kind) is None


# ────────────────────────── 4. 가족이 보는 것 ──────────────────────────


def test_member_sees_only_their_side():
    with TestClient(app) as client:
        fam = _family(client)
        mine = client.get(f"{API}/personas", headers=fam.member).json()["data"]
        assert len(mine) == 1
        item = mine[0]
        assert set(item) == {
            "id", "role", "shareId", "addressTerm", "relation", "lifeStatus", "step", "hasVoice",
            "origin", "ownerName", "updatedAt", "lastMessage", "lastMessageRole", "lastMessageAt",
        }  # fmt: skip
        assert (item["id"], item["role"], item["shareId"]) == (fam.pid, "member", fam.share_id)
        assert (item["addressTerm"], item["ownerName"], item["relation"]) == ("엄마", "지은", None)
        assert item["lifeStatus"] == "DECEASED" and item["step"] == "READY"
        assert "오말순" not in json.dumps(mine, ensure_ascii=False)
        owned = client.get(f"{API}/personas", headers=fam.owner).json()["data"]
        assert [(p["id"], p["role"], p["members"]) for p in owned] == [(fam.pid, "owner", 1)]
        assert owned[0]["name"] == "오말순" and owned[0]["addressTerm"] == "할머니"

        detail = client.get(f"{API}/personas/{fam.pid}", headers=fam.member).json()["data"]
        for hidden in (
            "basics", "answers", "recordings", "consents", "pledgeDoc", "voiceConsented",
            "sensitiveMemories", "voices", "subjectVoices", "name", "personId", "voiceProvider",
        ):  # fmt: skip
            assert hidden not in detail
        assert detail["jobs"] == {} and detail["voicePending"] is False
        assert detail["me"] == {
            "shareId": fam.share_id,
            **PROFILE,
            "joinedAt": detail["me"]["joinedAt"],
            "editedBy": "member",
        }
        owner_detail = client.get(f"{API}/personas/{fam.pid}", headers=fam.owner).json()["data"]
        assert owner_detail["role"] == "owner" and owner_detail["members"] == 1
        assert owner_detail["basics"]["name"] == "오말순"
        # 만드는 중으로 돌아간 대화 상대는 가족 목록에서 빠진다(상세는 '준비 중'으로 보인다)
        _set(Persona, fam.pid, setup_step=SetupStep.BUILDING)
        assert client.get(f"{API}/personas", headers=fam.member).json()["data"] == []
        assert (
            client.get(f"{API}/personas/{fam.pid}", headers=fam.member).json()["data"]["step"]
            == "BUILDING"
        )


def test_member_detail_has_edit_sheet_hints():
    """'나에 대한 정보 고치기' 화면이 쓰는 isSelf·addressSuggestions(참여 화면과 같은 값).
    주인 상세에는 싣지 않는다(주인 화면은 그대로)."""
    with TestClient(app) as client:
        fam = _family(client)
        detail = client.get(f"{API}/personas/{fam.pid}", headers=fam.member).json()["data"]
        assert detail["isSelf"] is False
        suggestions = detail["addressSuggestions"]
        assert list(suggestions) == intake.MY_RELATIONS
        # 그분이 할머니(여자분)라 여자분 쪽 말만
        assert suggestions["아들"] == ["엄마", "어머니"]
        assert suggestions["사위"] == ["장모님", "어머님"]
        assert suggestions == sharing.address_suggestions(_get(Persona, fam.pid))
        owner_detail = client.get(f"{API}/personas/{fam.pid}", headers=fam.owner).json()["data"]
        assert "isSelf" not in owner_detail and "addressSuggestions" not in owner_detail

        # 나 자신 대화 상대: 여자분인지 남자분인지 모르니 둘 다
        mine = {"myName": "한하늘", "myRelation": "딸", "addressTerm": "아빠"}
        own = _family(client, profile=mine, basics=SELF_BASICS, answers={})
        detail = client.get(f"{API}/personas/{own.pid}", headers=own.member).json()["data"]
        assert detail["isSelf"] is True
        assert detail["addressSuggestions"]["딸"] == ["엄마", "어머니", "아빠", "아버지"]
        assert detail["me"]["addressTerm"] == "아빠"


def test_member_gets_strangers_404_on_owner_routes(settings):
    with TestClient(app) as client:
        fam = _family(client)
        second, _ = _link(client, fam.owner, fam.pid)
        other, _, _ = _signup(client, "박영희")
        other_share = _accept(client, other, second).json()["data"]["shareId"]
        pid = fam.pid
        not_found = "대화 상대를 찾지 못했어요."
        wav = {"files": [("files", ("a.wav", b"RIFF", "audio/wav"))]}
        persona_routes = [
            ("PUT", f"/personas/{pid}", {"json": {"basics": OWNER_BASICS}}),
            ("DELETE", f"/personas/{pid}", {}),
            ("POST", f"/personas/{pid}/recordings", {"data": {"pledge": "true"}, **wav}),
            ("DELETE", f"/personas/{pid}/recordings/x", {}),
            ("POST", f"/personas/{pid}/recordings/apply", {}),
            ("POST", f"/personas/{pid}/transcribe", {}),
            ("POST", f"/personas/{pid}/speakers", {"json": {"voiceKeys": ["VOICE_1"]}}),
            ("POST", f"/personas/{pid}/back-to-upload", {}),
            ("POST", f"/personas/{pid}/back-to-speakers", {}),
            ("POST", f"/personas/{pid}/back-to-questions", {}),
            ("GET", f"/personas/{pid}/phrase-suggestions", {}),
            ("PUT", f"/personas/{pid}/answers", {"json": {}}),
            ("POST", f"/personas/{pid}/build", {"json": {"answers": {}}}),
            ("POST", f"/personas/{pid}/voice", {"json": {}}),
            (
                "POST",
                f"/personas/{pid}/consents",
                {"json": {"type": "sensitive_health", "agreed": False}},
            ),
            ("GET", f"/personas/{pid}/materials", {}),
            ("POST", f"/personas/{pid}/materials", {"data": {"kind": "diary", "text": "시험"}}),
            ("PATCH", f"/personas/{pid}/materials/x", {"json": {"include": False}}),
            ("DELETE", f"/personas/{pid}/materials/x", {}),
            ("GET", f"/personas/{pid}/shares", {}),
            ("POST", f"/personas/{pid}/shares", {"json": {"agreements": ALL_OWNER}}),
            ("POST", f"/personas/{pid}/shares/confirm", {"json": {"agreements": ALL_OWNER}}),
            ("POST", f"/personas/{pid}/shares/{fam.share_id}/renew", {}),
            ("POST", f"/personas/{pid}/shares/stop", {}),
            ("POST", "/billing/requests", {"json": {"personaId": pid, "plan": "family"}}),
            ("DELETE", f"/billing/requests/{pid}", {}),
            ("POST", "/billing/cancel", {"json": {"personaId": pid}}),
            ("POST", "/billing/resume", {"json": {"personaId": pid}}),
        ]
        for method, path, kwargs in persona_routes:
            response = client.request(method, f"{API}{path}", headers=fam.member, **kwargs)
            assert response.status_code == 404, (method, path, response.text)
            assert response.json() == {"detail": not_found}, (method, path)
        settings.payments_open = True
        checkout = client.post(
            f"{API}/billing/checkout",
            headers=fam.member,
            json={"personaId": pid, "plan": "family", "agreements": ["recurring"]},
        )
        assert checkout.status_code == 404 and checkout.json() == {"detail": not_found}
        settings.payments_open = False
        # 기억 API 는 관리자만(가족·주인 모두 403)
        person_id = _get(Persona, pid).person_id
        assert client.get(f"{API}/memories", headers=fam.member).status_code == 403
        made = client.post(
            f"{API}/memories",
            headers=fam.member,
            json={"personId": person_id, "title": "x", "bodyMarkdown": "x"},
        )
        assert made.status_code == 403
        assert client.get(f"{API}/memories", headers=fam.owner).status_code == 403
        # 다른 가족의 자리는 고치거나 끝내지 못한다
        for method in ("PATCH", "DELETE"):
            response = client.request(
                method,
                f"{API}/personas/{pid}/shares/{other_share}",
                headers=fam.member,
                **({"json": {"myName": "남"}} if method == "PATCH" else {}),
            )
            assert response.status_code == 404 and response.json()["code"] == "share_not_found"
        # 모르는 사람도 같다
        stranger, _, _ = _signup(client, "남남")
        assert client.get(f"{API}/personas/{pid}", headers=stranger).json() == {"detail": not_found}
        assert (
            client.post(f"{API}/personas/{pid}/conversation", headers=stranger).status_code == 404
        )
        assert client.get(f"{API}/billing/personas/{pid}", headers=stranger).status_code == 404


def test_member_and_owner_edit_the_profile(fake_ai: FakeAI):
    with TestClient(app) as client:
        fam = _family(client)
        edited = client.patch(
            f"{API}/personas/{fam.pid}/shares/{fam.share_id}",
            headers=fam.member,
            json={"addressTerm": "어머니", "speechLevel": "존댓말", "familyRow": "영희"},
        )
        assert edited.status_code == 200, edited.text
        me = edited.json()["data"]
        assert me["addressTerm"] == "어머니" and me["speechLevel"] == "존댓말"
        assert me["editedBy"] == "member" and "familyRow" not in me
        seat = _get(PersonaShare, fam.share_id)
        # 가족 표 줄은 주인만 고친다
        assert "familyRow" not in seat.profile and seat.instructions_base is None
        blank = client.patch(
            f"{API}/personas/{fam.pid}/shares/{fam.share_id}",
            headers=fam.member,
            json={"myName": ""},
        )
        assert blank.status_code == 400 and blank.json()["field"] == "myName"
        by_owner = client.patch(
            f"{API}/personas/{fam.pid}/shares/{fam.share_id}",
            headers=fam.owner,
            json={"profile": {"calledMe": "우리 철수"}, "familyRow": "철수"},
        )
        assert by_owner.status_code == 200
        item = by_owner.json()["data"]
        assert item["status"] == "joined" and item["editedBy"] == "owner"
        assert item["accountName"] == "박철수" and item["profile"]["calledMe"] == "우리 철수"
        assert item["profile"]["familyRow"] == "철수"
        # 쓰지 않은 링크: 주인이 미리 적은 내용을 고친다
        _, open_item = _link(client, fam.owner, fam.pid, prefill={"myName": "영희"})
        fixed = client.patch(
            f"{API}/personas/{fam.pid}/shares/{open_item['id']}",
            headers=fam.owner,
            json={"myRelation": "딸", "familyRow": "영희"},
        )
        assert fixed.json()["data"]["prefill"] == {
            "myName": "영희",
            "myRelation": "딸",
            "familyRow": "영희",
        }
        events = _all(
            select(AnalyticsEvent).where(
                AnalyticsEvent.name == "share_profile_updated",
                AnalyticsEvent.persona_id == fam.pid,
            )
        )
        assert sorted(e.props["by"] for e in events) == ["member", "owner", "owner"]


# ────────────────────────── 5. 대화방은 따로 ──────────────────────────


def test_rooms_are_private_both_ways():
    with TestClient(app) as client:
        fam = _family(client)
        owner_room = _room(client, fam.owner, fam.pid)
        member_room = _room(client, fam.member, fam.pid)
        assert owner_room != member_room
        assert _room(client, fam.member, fam.pid) == member_room  # 한 사람에 방 하나
        assert _say(client, fam.owner, owner_room, "할머니 저 왔어요").status_code == 200
        assert _say(client, fam.member, member_room, "엄마 나야").status_code == 200
        rooms = client.get(f"{API}/conversations", headers=fam.member).json()["data"]
        assert [(r["id"], r["title"]) for r in rooms] == [(member_room, "엄마")]
        assert client.get(f"{API}/conversations", headers=fam.owner).json()["data"][0]["title"] == (
            "오말순"
        )
        made = client.post(
            f"{API}/conversations",
            headers=fam.member,
            json={"personaId": fam.pid, "channel": "TEXT", "title": "오말순"},
        )
        assert made.status_code == 201 and made.json()["data"]["title"] == "엄마"
        for who, room in ((fam.owner, member_room), (fam.member, owner_room)):
            assert client.get(f"{API}/conversations/{room}", headers=who).status_code == 404
            assert _say(client, who, room).status_code == 404
        owner_reply = client.get(f"{API}/conversations/{owner_room}", headers=fam.owner).json()[
            "data"
        ]["messages"][-1]["id"]
        heard = client.post(f"{API}/messages/{owner_reply}/speech", headers=fam.member)
        assert heard.status_code == 404
        member_rating = client.post(
            f"{API}/messages/{owner_reply}/feedback", headers=fam.member, json={"rating": "UP"}
        )
        assert member_rating.status_code == 404
        # 목록의 마지막 말은 내 방의 것
        owner_list = client.get(f"{API}/personas", headers=fam.owner).json()["data"][0]
        member_list = client.get(f"{API}/personas", headers=fam.member).json()["data"][0]
        assert owner_list["lastMessageRole"] == member_list["lastMessageRole"] == "assistant"
        owner_history = client.get(f"{API}/conversations/{owner_room}", headers=fam.owner).json()
        assert "엄마 나야" not in json.dumps(owner_history, ensure_ascii=False)


# ────────────────────────── 6. 지침 ──────────────────────────


def test_owner_request_is_unchanged(fake_ai: FakeAI):
    """주인이 말하면 보내는 것은 예전과 같다(speakerHint 없음, 주인 지침 그대로, 조립 없음)."""
    with TestClient(app) as client:
        fam = _family(client)
        assembled = len(fake_ai.assemblies())  # 가족이 들어올 때 미리 조립한 것
        cid = _room(client, fam.owner, fam.pid)
        assert _say(client, fam.owner, cid, "할머니 보고 싶어요").status_code == 200
        person_id = _get(Persona, fam.pid).person_id
        assert fake_ai.replies()[-1] == {
            "message": "할머니 보고 싶어요",
            "history": [],
            "memories": [],
            "sessionId": cid,
            # '더 알려 주실 기억' 빈틈 표시 부탁(주인·가족 모두 같다, gap_questions_enabled)
            "flagGaps": True,
            # 그분 기억 총수(기억이 적은 페르소나 가리기). 주인·가족 모두 같다
            "memoryTotal": 0,
            "persona": {
                "subjectId": person_id,
                "instructions": OWNER_INSTRUCTIONS,
                "voiceId": None,
                "deceased": True,
            },
        }
        assert len(fake_ai.assemblies()) == assembled
    # 주인 조립 재료에는 isOwner·isMember 가 없다(가족이 생겨도 주인 지침은 그대로)
    # (나 자신 대화 상대도 — 주인 조립본의 듣는 사람 줄에는 isMember 를 달지 않는다)
    owner_context = persona_builder.build_prompt_material(OWNER_BASICS, ANSWERS, [], set())[1]
    self_context = persona_builder.build_prompt_material(SELF_BASICS, {}, [], set())[1]
    assert owner_context["familyMap"][0]["name"] == "지은"
    for family in (owner_context["familyMap"], self_context["familyMap"]):
        assert family[0]["isListener"] is True
        assert all("isOwner" not in row and "isMember" not in row for row in family)


def test_member_gets_their_own_assembly(fake_ai: FakeAI):
    with TestClient(app) as client:
        fam = _family(client)
        # 들어오자마자 미리 조립해 둔다
        assert len(fake_ai.assemblies()) == 1
        payload = fake_ai.assemblies()[0]
        assert payload["personaInsights"] == []
        family = payload["intakeContext"]["familyMap"]
        assert family[0] == {
            "name": "철수",
            "relation": "아들",
            "addressTermTo": "철수야",
            "addressTermFrom": "엄마",
            "selfReference": "엄마가",
            "speechLevel": "반말",
            "talksWithPersona": True,
            "isListener": True,
            "isMember": True,
            "note": "서울 삶",
        }
        assert family[1] == {
            "name": "지은",
            "relation": "손녀",
            "addressTermTo": "우리 강아지",
            "addressTermFrom": "할머니",
            "selfReference": "할머니가",
            "speechLevel": "반말",
            "tone": "다정하게",
            "listenerCalls": "지은이",
            "talksWithPersona": True,
            "isOwner": True,
        }
        assert family[2:] == [
            {"name": "영희", "relation": "딸", "addressTermTo": "영희야"},
            {"name": "만복", "relation": "남편", "deceased": True},
        ]
        text = json.dumps(payload, ensure_ascii=False)
        assert "toMe" not in text and "아빠" not in text and "고모" not in text
        assert [r["name"] for r in family].count("철수") == 1
        assert payload["subjectContext"]["subject"] == {"name": "오말순", "addressTerm": "엄마"}
        assert payload["subjectContext"]["familyMembers"][0]["name"] == "철수"
        topics = [t["topic"] for t in payload["intakeContext"]["tabooTopics"]]
        assert topics.count("다른 가족의 사생활") == 1
        seat = _get(PersonaShare, fam.share_id)
        assert seat.instructions == "조립본 · 철수 · 1" and len(seat.instructions_base) == 64
        # 가족이 말하면 그 가족 지침과 이름(speakerHint)을 보낸다
        cid = _room(client, fam.member, fam.pid)
        sent = _say(client, fam.member, cid, "엄마 나 왔어")
        assert sent.status_code == 200, sent.text
        body = fake_ai.replies()[-1]
        assert body["speakerHint"] == "철수"
        assert body["persona"]["instructions"] == "조립본 · 철수 · 1"
        assert "fixedPrompt" not in body["persona"]
        assert len(fake_ai.assemblies()) == 1  # 바탕이 같으면 다시 조립하지 않는다
        sent_events = _all(
            select(AnalyticsEvent).where(
                AnalyticsEvent.name == "message_sent", AnalyticsEvent.persona_id == fam.pid
            )
        )
        assert [e.props.get("shared") for e in sent_events] == [True]


def test_self_persona_has_no_owner_row(fake_ai: FakeAI):
    with TestClient(app) as client:
        profile = {"myName": "한하늘", "myRelation": "딸", "addressTerm": "아빠"}
        fam = _family(client, profile=profile, basics=SELF_BASICS, answers={})
        family = fake_ai.assemblies()[-1]["intakeContext"]["familyMap"]
        assert [r["name"] for r in family] == ["한하늘"]
        assert family[0]["isListener"] is True and family[0]["addressTermFrom"] == "아빠"
        # 주인 줄이 없어도 AI 서버가 함께 쓰는 가족의 조립본임을 알게 듣는 사람 줄에 단다
        assert family[0]["isMember"] is True
        assert all("isOwner" not in r for r in family)
        item = client.get(f"{API}/personas", headers=fam.member).json()["data"][0]
        assert item["addressTerm"] == "아빠" and item["ownerName"] == "한바다"


def test_member_assembly_matches_short_and_long_names(monkeypatch):
    """같은 사람을 둘로 그리지 않는다.

    - 녹음이 주인을 짧은 이름으로 부르면('지은' ↔ '홍지은') 주인 줄로 합친다(주인과 이야기할 때
      주인 줄로 합쳐지는 것과 같다). 이름이 비슷한 다른 가족 줄('지은이 아빠')로 가지 않는다.
    - '다른 가족'으로 들어오며 이름을 길게 적은 가족('홍철수' ↔ 가족 표 '철수')은 관계까지 같으면
      그 줄로 본다.
    """
    sent: list[dict] = []

    async def assemble(self, **body):
        sent.append(body)
        return "조립본"

    heard = SimpleNamespace(
        analysis={
            "speechStyle": {
                "addressTerms": [
                    {"person": "지은", "term": "우리 강아지"},
                    {"person": "영희", "term": "영희야"},
                ]
            }
        },
        segments=[],
    )

    async def recordings(db, persona_id):
        return [heard]

    # 클래스에 건다(인스턴스에 걸면 되돌릴 때 묶인 메서드가 인스턴스에 남아 뒤 시험의 가짜를 가린다)
    monkeypatch.setattr(type(sharing.ai_service), "assemble", assemble)
    monkeypatch.setattr(sharing.persona_builder, "load_recordings", recordings)
    # 만들 때 그분이 나를 부르던 말을 비워 둔 주인(홍지은)
    basics = OWNER_BASICS | {"myName": "홍지은", "calledMe": None}
    persona = Persona(id="p-test", basics=basics, answers=ANSWERS, subject_voices=[])
    profile = {"myName": "홍철수", "myRelation": "아들", "addressTerm": "엄마"}
    _run(sharing.assemble_for_member(None, persona, profile))
    family = sent[-1]["intakeContext"]["familyMap"]
    names = [r["name"] for r in family]
    assert names == ["홍철수", "홍지은", "영희", "만복"]
    # 가족 표 '철수' 줄을 이 가족으로 보았다: 그 줄의 불리던 말과 메모를 듣는 사람 줄로
    assert family[0]["addressTermTo"] == "철수야" and family[0]["note"] == "서울 삶"
    # 녹음 속 '지은'은 주인 줄로 합쳐진다(따로 '다른 가족' 줄을 두지 않는다). 주인과 이야기할 때처럼
    # 녹음이 주인 줄의 부르는 말을 채우지는 않는다
    assert family[1]["isOwner"] is True and "addressTermTo" not in family[1]
    # isMember 는 듣는 사람 줄에만(주인 줄·다른 가족 줄에는 없다)
    assert [r.get("isMember") for r in family] == [True, None, None, None]
    _, owner_context, _ = persona_builder.build_prompt_material(basics, ANSWERS, [heard], set())
    owner_family = owner_context["familyMap"]
    assert [r["name"] for r in owner_family] == ["홍지은", "철수", "영희", "만복"]
    assert "addressTermTo" not in owner_family[0]
    assert all("isOwner" not in r and "isMember" not in r for r in owner_family)
    # 관계가 다르면 이름이 비슷해도 그 줄로 보지 않는다
    _run(
        sharing.assemble_for_member(
            None, persona, {"myName": "홍철수", "myRelation": "손자", "addressTerm": "할머니"}
        )
    )
    other = sent[-1]["intakeContext"]["familyMap"]
    assert [r["name"] for r in other] == ["홍철수", "홍지은", "철수", "영희", "만복"]
    assert "note" not in other[0]
    # 주인이 만들 때 적은 말이 있으면 그 말이 앞선다
    persona.basics = OWNER_BASICS | {"myName": "홍지은", "calledMe": "지은아"}
    _run(sharing.assemble_for_member(None, persona, PROFILE))
    family = sent[-1]["intakeContext"]["familyMap"]
    assert [r["name"] for r in family] == ["철수", "홍지은", "영희", "만복"]
    assert family[1]["addressTermTo"] == "지은아"
    # 가족 표에 직접 적은 줄은 주인 이름이 들어 있어도('지은이 아빠') 그대로 두고, 녹음이 주인을
    # 부른 말('지은' → '우리 강아지')이 그 줄로 가지 않는다
    persona.basics = OWNER_BASICS
    persona.answers = ANSWERS | {
        "M2-01": [*ANSWERS["M2-01"], {"name": "지은이 아빠", "relation": "아들", "status": "alive"}]
    }
    _run(sharing.assemble_for_member(None, persona, PROFILE))
    family = sent[-1]["intakeContext"]["familyMap"]
    assert [r["name"] for r in family] == ["철수", "지은", "영희", "만복", "지은이 아빠"]
    assert family[1]["addressTermTo"] == "우리 강아지" and "addressTermTo" not in family[-1]


def test_member_prompt_rebuilds_when_its_inputs_change(fake_ai: FakeAI):
    with TestClient(app) as client:
        fam = _family(client)
        cid = _room(client, fam.member, fam.pid)

        def chat_and_count() -> int:
            assert _say(client, fam.member, cid).status_code == 200
            return len(fake_ai.assemblies())

        assert chat_and_count() == 1
        _set(Persona, fam.pid, instructions=OWNER_INSTRUCTIONS + "\n(고친 지침)")
        assert chat_and_count() == 2
        _set(Persona, fam.pid, basics=OWNER_BASICS | {"calledMe": "강아지"})
        assert chat_and_count() == 3
        _set(Persona, fam.pid, answers=ANSWERS | {"C-05": "웃음이 많은 분"})
        assert chat_and_count() == 4
        patched = client.patch(
            f"{API}/personas/{fam.pid}/shares/{fam.share_id}",
            headers=fam.member,
            json={"speechLevel": "존댓말"},
        )
        assert patched.status_code == 200
        assert chat_and_count() == 5
        assert fake_ai.assemblies()[-1]["intakeContext"]["familyMap"][0]["speechLevel"] == "존댓말"
        assert chat_and_count() == 5
        assert fake_ai.replies()[-1]["persona"]["instructions"] == "조립본 · 철수 · 5"


def test_assembly_failure_uses_the_last_one_or_releases_the_conversation(fake_ai: FakeAI):
    with TestClient(app, raise_server_exceptions=False) as client:
        fam = _family(client)
        cid = _room(client, fam.member, fam.pid)
        fake_ai.fail_assembly = True
        _set(Persona, fam.pid, answers=ANSWERS | {"C-05": "바뀐 한 줄"})
        stale = _say(client, fam.member, cid)
        assert stale.status_code == 200
        assert fake_ai.replies()[-1]["persona"]["instructions"] == "조립본 · 철수 · 1"
        used = _usage(fam.pid)
        messages = _count(select(func.count(Message.id)).where(Message.conversation_id == cid))
        # 예전 조립본도 없으면 대답하지 않고 대화 횟수를 돌려준다(주인 지침으로 대신하지 않는다)
        _set(PersonaShare, fam.share_id, instructions=None, instructions_base=None)
        failed = _say(client, fam.member, cid)
        assert failed.status_code == 503
        assert _usage(fam.pid) == used
        assert (
            _count(select(func.count(Message.id)).where(Message.conversation_id == cid)) == messages
        )
        assert all(
            r["persona"]["instructions"] != OWNER_INSTRUCTIONS
            for r in fake_ai.replies()
            if r.get("speakerHint")
        )
        fake_ai.fail_assembly = False
        assert _say(client, fam.member, cid).status_code == 200


def _add_memories(pid: str, owner_id: str) -> None:
    async def go(db):
        persona = await db.get(Persona, pid)
        for source, title in (
            ("INTAKE", "기억 카드"),
            ("MANUAL", "직접 적은 기억"),
            ("RECORDING", "녹음 속 기억"),
            ("IMPORT", "가져온 기억 (물으면만)"),
        ):
            db.add(
                Memory(
                    person_id=persona.person_id,
                    created_by_user_id=owner_id,
                    title=title,
                    body_markdown=f"{title} 내용",
                    tags=[],
                    source=source,
                )
            )
        await db.commit()

    _db(go)


def test_owner_written_memories_are_marked_for_members(fake_ai: FakeAI):
    with TestClient(app) as client:
        fam = _family(client)
        _add_memories(fam.pid, fam.owner_id)
        member_room = _room(client, fam.member, fam.pid)
        assert _say(client, fam.member, member_room).status_code == 200
        titles = {m["title"] for m in fake_ai.replies()[-1]["memories"]}
        assert titles == {
            "지은이 적어 준 이야기 · 기억 카드",
            "지은이 적어 준 이야기 · 직접 적은 기억",
            "녹음 속 기억",
            "가져온 기억 (물으면만)",
        }
        owner_room = _room(client, fam.owner, fam.pid)
        assert _say(client, fam.owner, owner_room).status_code == 200
        assert {m["title"] for m in fake_ai.replies()[-1]["memories"]} == {
            "기억 카드",
            "직접 적은 기억",
            "녹음 속 기억",
            "가져온 기억 (물으면만)",
        }
        # 나 자신 대화 상대는 주인 자신의 기억이라 그대로
        self_fam = _family(
            client,
            profile={"myName": "한하늘", "myRelation": "딸", "addressTerm": "아빠"},
            basics=SELF_BASICS,
            answers={},
        )
        _add_memories(self_fam.pid, self_fam.owner_id)
        room = _room(client, self_fam.member, self_fam.pid)
        assert _say(client, self_fam.member, room).status_code == 200
        assert "기억 카드" in {m["title"] for m in fake_ai.replies()[-1]["memories"]}
    assert sharing.josa_ga("지은") == "이" and sharing.josa_ga("철수") == "가"
    assert sharing.josa_ga("Jane") == "가"


# ────────────────────────── 7. 가져온 대화 상대 ──────────────────────────


def test_imported_member_line_swaps_only_the_listener_line():
    text = sharing.imported_for_member(IMPORTED, PROFILE, "지은")
    assert text.endswith(f"\n\n{LEGACY}") and text.startswith(f"{RULES}\n\n")
    assert OWNER_LINE not in text and "'지은'(손녀)이다" not in text
    line = (
        "- 지금 대화하는 사람은 아래 가족 목록의 '철수'(아들)이다. 누구인지 다시 확인하지 않아도 "
        "된다. 이 사람을 부를 때는 '철수야'라고 부른다. 이 사람은 나를 '엄마'라고 부른다. 이 사람 "
        "앞에서 나를 가리킬 때는 '엄마가'라고 한다. 이 사람에게는 반말로 말한다. 아래 글에서 "
        "'지은'에게 하던 부르는 말과 '지은'과의 옛일은 '지은'의 것이니 이 사람에게 그대로 쓰지 "
        "않는다."
    )
    assert text == IMPORTED.replace(OWNER_LINE, line)
    # 예전 목록에 없는 가족은 낯선 사람으로 대하지 않되 옛일을 지어내지 않는다
    new = sharing.imported_for_member(
        IMPORTED, {"myName": "민수", "myRelation": "사위", "addressTerm": "장모님"}, "지은"
    )
    assert "'민수'(사위)이다. 아래 가족 목록에 없어도" in new
    assert "옛일은 지어내지 않는다" in new and new.endswith(f"\n\n{LEGACY}")
    # 조사는 받침에 맞춘다(사위·며느리처럼 받침 있는 호칭이 많다)
    son_in_law = {
        "myName": "박민호",
        "myRelation": "사위",
        "addressTerm": "장모님",
        "calledMe": "박 서방",
        "selfReference": "장모",
        "speechLevel": "존댓말",
    }
    polite = sharing.imported_for_member(IMPORTED, son_in_law, "홍지은")
    for text in (
        "이 사람을 부를 때는 '박 서방'이라고 부른다.",
        "이 사람은 나를 '장모님'이라고 부른다.",
        "나를 가리킬 때는 '장모'라고 한다.",
        "이 사람에게는 존댓말로 말한다.",
        "'홍지은'과의 옛일은",
    ):
        assert text in polite, text
    assert "'민호'와의 옛일은" in sharing.member_line(son_in_law, in_list=False, owner_name="민호")
    # 목록 속 이름을 골랐으면(familyRow) 그 이름으로
    picked = sharing.imported_for_member(
        IMPORTED, {"myName": "박민수", "familyRow": "철수", "addressTerm": "엄마"}, "지은"
    )
    assert "아래 가족 목록의 '철수'이다" in picked
    # 주인 이름이 없으면 주인 몫 안내도 없다
    nameless = sharing.imported_for_member(IMPORTED, PROFILE, None)
    assert "의 것이니" not in nameless


def test_imported_without_listener_line_or_bridge():
    bridge_only = BRIDGE.replace("\n" + OWNER_LINE, "")
    text = f"{RULES}\n\n{bridge_only}\n\n{LEGACY}"
    out = sharing.imported_for_member(text, PROFILE, "지은")
    assert out.endswith(f"\n\n{LEGACY}")
    assert f"{bridge_only}\n- 지금 대화하는 사람은 아래 가족 목록의 '철수'(아들)이다." in out
    # 예전 가져오기(빈칸 없이 줄만 붙은 것)도 한 줄만 바꾼다
    raw = text.replace(bridge_only, bridge_only + "\n" + OWNER_LINE.removeprefix("- "))
    swapped = sharing.imported_for_member(raw, PROFILE, "지은")
    assert "'지은'(손녀)이다" not in swapped and swapped.count("지금 대화하는 사람은") == 1
    # 연결 문단이 없는 verbatim 가져오기: 끝에 앞서는 마디를 붙인다. 가족 목록과 옛 글이 이 줄의
    # 위에 있으니 '위'라고 가리킨다
    verbatim = sharing.imported_for_member(LEGACY, PROFILE, "지은")
    assert verbatim.startswith(LEGACY.rstrip())
    assert (
        "\n\n# 지금 대화하는 사람 (이 대화에서는 이것이 앞선다)\n- 지금 대화하는 사람은 위 가족 "
        "목록의 '철수'(아들)이다." in verbatim
    )
    assert "위 글에서 '지은'에게 하던" in verbatim
    tail = verbatim[len(LEGACY.rstrip()) :]
    assert "아래" not in tail
    stranger = sharing.imported_for_member(
        LEGACY, {"myName": "민수", "myRelation": "사위", "addressTerm": "장모님"}, "지은"
    )
    assert "'민수'(사위)이다. 위 가족 목록에 없어도" in stranger


def test_imported_member_chat_and_long_guard(fake_ai: FakeAI):
    with TestClient(app, raise_server_exceptions=False) as client:
        fam = _family(client, origin="import", answers={}, instructions=IMPORTED)
        assert fake_ai.assemblies() == []  # 가져온 것은 조립하지 않는다
        cid = _room(client, fam.member, fam.pid)
        assert _say(client, fam.member, cid).status_code == 200
        body = fake_ai.replies()[-1]
        assert "speakerHint" not in body and body["persona"]["fixedPrompt"] is True
        assert body["persona"]["instructions"] == sharing.imported_for_member(
            IMPORTED, PROFILE, "지은"
        )
        assert _get(PersonaShare, fam.share_id).instructions is None  # 저장하지 않는다
        owner_room = _room(client, fam.owner, fam.pid)
        assert _say(client, fam.owner, owner_room).status_code == 200
        assert fake_ai.replies()[-1]["persona"]["instructions"] == IMPORTED
        # 바꾼 글이 AI 서버 상한(50,000자)을 넘으면 대답하지 않고 횟수를 돌려준다
        long_text = IMPORTED + "가" * (50_000 - len(IMPORTED))
        _set(Persona, fam.pid, instructions=long_text)
        used = _usage(fam.pid)
        refused = _say(client, fam.member, cid)
        assert refused.status_code == 503
        assert refused.json()["detail"] == "지금은 대답을 만들 수 없어요. 잠시 뒤 다시 해 주세요."
        assert _usage(fam.pid) == used
        assert _say(client, fam.owner, owner_room).status_code == 200


# ────────────────────────── 8. 결제·횟수는 주인 것 ──────────────────────────


def test_family_shares_one_counter(settings):
    settings.prelaunch_conversations = 3
    settings.text_only_fair_use = 1
    with TestClient(app) as client:
        fam = _family(client)
        owner_room = _room(client, fam.owner, fam.pid)
        member_room = _room(client, fam.member, fam.pid)
        assert _say(client, fam.owner, owner_room).json()["data"]["conversationsLeft"] == 2
        assert _say(client, fam.member, member_room).json()["data"]["conversationsLeft"] == 1
        for h in (fam.owner, fam.member):
            view = client.get(f"{API}/billing/personas/{fam.pid}", headers=h).json()["data"]
            assert (view["conversationsUsed"], view["conversationsLimit"]) == (2, 3)
        member_view = client.get(f"{API}/billing/personas/{fam.pid}", headers=fam.member).json()[
            "data"
        ]
        assert member_view["status"] == "PRELAUNCH" and member_view["paused"] is None
        assert _say(client, fam.member, member_room).json()["data"]["conversationsLeft"] == 0
        # 다 쓰면 말은 받지 않고 글로만(함께 쓰는 공정 사용 상한까지)
        voice = client.post(
            f"{API}/conversations/{member_room}/messages/voice",
            headers=fam.member,
            files={"file": ("v.webm", b"fake-voice", "audio/webm")},
        )
        assert voice.status_code == 409
        assert voice.json()["detail"].startswith("가족이 함께 쓰는 대화 횟수를 다 써서")
        text_only = _say(client, fam.member, member_room).json()["data"]
        assert text_only["textOnly"] is True
        exhausted = _say(client, fam.member, member_room)
        assert exhausted.status_code == 402
        assert exhausted.json()["detail"].startswith(
            "가족이 함께 쓰는 무료 대화를 이번에 모두 썼어요."
        )
        assert "요금제" not in exhausted.json()["detail"]
        assert "에 다시 채워져요." in exhausted.json()["detail"]
        # 주인은 예전 글 그대로
        owner_out = _say(client, fam.owner, owner_room)
        assert owner_out.status_code == 402
        assert owner_out.json()["detail"].startswith("결제 전 무료로 쓸 수 있는 대화를 모두 썼어요")
        # 대화 횟수 기록은 말한 사람으로 남는다
        actors = _all(select(UsageEvent.user_id).where(UsageEvent.persona_id == fam.pid))
        assert set(actors) == {fam.owner_id, fam.member_id}


def test_last_slot_is_never_overshot():
    """주인과 가족이 번갈아 마지막 자리를 잡아도 횟수를 넘지 않는다(대화 상대 줄 자물쇠)."""

    async def go():
        async with SessionLocal() as db:
            owner = User(
                email=f"race-o-{uuid4().hex[:6]}@example.com", name="가", password_hash="x"
            )
            member = User(
                email=f"race-m-{uuid4().hex[:6]}@example.com", name="나", password_hash="x"
            )
            db.add_all([owner, member])
            await db.flush()
            person = Person(owner_user_id=owner.id, name="오말순", relationship_label="할머니")
            db.add(person)
            await db.flush()
            persona = Persona(person_id=person.id, name="오말순", setup_step=SetupStep.READY)
            db.add(persona)
            await db.commit()
            ent = await billing.entitlement(db, owner, persona)
            ent.conversations_limit = 2
            got = [
                await billing.reserve_conversation(db, who, persona, ent)
                for who in (owner, member, owner, member)
            ]
            actors = list(
                (
                    await db.scalars(
                        select(UsageEvent.user_id)
                        .where(UsageEvent.persona_id == persona.id)
                        .order_by(UsageEvent.created_at)
                    )
                ).all()
            )
            return got, actors, owner.id, member.id

    got, actors, owner_id, member_id = _run(go())
    assert [g is not None for g in got] == [True, True, False, False]
    assert actors == [owner_id, member_id]


def test_member_never_starts_the_owners_trial(settings):
    with TestClient(app) as client:
        fam = _family(client)
        room = _room(client, fam.member, fam.pid)
        settings.payments_open = True
        waiting = _say(client, fam.member, room)
        assert waiting.status_code == 403
        assert waiting.json() == {
            "detail": "지금은 이야기할 수 없어요. "
            "지은님이 TalkTo에 들어와 확인하면 다시 이야기할 수 있어요.",
            "code": "share_paused",
            "reason": "waiting_owner",
        }
        view = client.get(f"{API}/billing/personas/{fam.pid}", headers=fam.member).json()["data"]
        assert view["status"] == "PAUSED" and view["paused"]["reason"] == "waiting_owner"
        assert view["paymentsOpen"] is True and view["active"] is False
        owner = _get(User, fam.owner_id)
        assert (owner.trials_used or 0) == 0 and _get(Persona, fam.pid).trial_ends_at is None
        assert (_get(User, fam.member_id).trials_used or 0) == 0
        # 주인이 들어와 보면 주인의 체험이 열리고(F3) 가족도 이어서 이야기한다
        owner_view = client.get(f"{API}/billing/personas/{fam.pid}", headers=fam.owner).json()[
            "data"
        ]
        assert owner_view["status"] == "TRIAL" and owner_view["sharingAllowed"] is True
        assert owner_view["members"] == 1
        assert _say(client, fam.member, room).status_code == 200
        assert (_get(User, fam.member_id).trials_used or 0) == 0
        member_view = client.get(f"{API}/billing/personas/{fam.pid}", headers=fam.member).json()[
            "data"
        ]
        assert member_view["status"] == "TRIAL" and member_view["conversationsUsed"] == 1


def test_entitlement_is_always_the_owners(settings):
    settings.prelaunch_conversations = 7
    with TestClient(app) as client:
        fam = _family(client)
        admin, admin_id = _admin(client)
        second, _ = _link(client, fam.owner, fam.pid)
        assert _accept(client, admin, second).status_code == 201
        # 관리자가 고객의 대화 상대에 들어와도 고객의 한도를 따른다
        admin_view = client.get(f"{API}/billing/personas/{fam.pid}", headers=admin).json()["data"]
        assert admin_view["status"] == "PRELAUNCH" and admin_view["conversationsLimit"] == 7
        # 결제 시험 계정인 가족이 결제를 열지 않는다
        settings.payments_test_emails = fam.member_email
        assert billing.payments_open(_get(User, fam.member_id)) is True
        member_view = client.get(f"{API}/billing/personas/{fam.pid}", headers=fam.member).json()[
            "data"
        ]
        assert member_view["paymentsOpen"] is False and member_view["status"] == "PRELAUNCH"
        room = _room(client, fam.member, fam.pid)
        assert _say(client, fam.member, room).status_code == 200
        settings.payments_test_emails = ""
        # 관리자가 만든 대화 상대: 가족에게는 제한 없는 'ACTIVE'(관리자라고 보이지 않는다)
        admin_pid = _persona(admin_id)
        token, _ = _link(client, admin, admin_pid)
        guest, _, _ = _signup(client, "박손님")
        assert _accept(client, guest, token).status_code == 201
        guest_view = client.get(f"{API}/billing/personas/{admin_pid}", headers=guest).json()["data"]
        assert guest_view["status"] == "ACTIVE" and guest_view["conversationsLimit"] is None
        assert guest_view["ownerName"] == "지은"


def test_plan_decides_whether_family_can_talk(settings):
    with TestClient(app) as client:
        fam = _family(client)
        room = _room(client, fam.member, fam.pid)
        settings.payments_open = True
        now = datetime.now(UTC)

        async def subscribe(db):
            db.add(
                Subscription(
                    user_id=fam.owner_id,
                    persona_id=fam.pid,
                    plan="memories",
                    status="ACTIVE",
                    provider="mock",
                    customer_key="ck-test",
                    current_period_start=now - timedelta(days=1),
                    current_period_end=now + timedelta(days=20),
                )
            )
            await db.commit()

        _db(subscribe)
        sub_id = _all(select(Subscription.id).where(Subscription.persona_id == fam.pid))[0]
        paused = _say(client, fam.member, room)
        assert paused.status_code == 403 and paused.json()["reason"] == "plan"
        assert "가족 요금제로 바꾸면" in paused.json()["detail"]
        page = client.get(f"{API}/personas/{fam.pid}/shares", headers=fam.owner).json()["data"]
        assert page["reason"] == "plan" and page["prelaunch"] is False
        assert (
            client.post(f"{API}/personas/{fam.pid}/shares", headers=fam.owner, json={}).json()[
                "code"
            ]
            == "share_plan"
        )
        owner_view = client.get(f"{API}/billing/personas/{fam.pid}", headers=fam.owner).json()[
            "data"
        ]
        assert owner_view["sharingAllowed"] is False and owner_view["members"] == 1
        # 가족 요금제(해지 예정·결제 확인 중 포함)는 함께 쓴다
        _set(Subscription, sub_id, plan="family")
        assert _say(client, fam.member, room).status_code == 200
        _set(Subscription, sub_id, cancel_at_period_end=True)
        assert _say(client, fam.member, room).status_code == 200
        view = client.get(f"{API}/billing/personas/{fam.pid}", headers=fam.member).json()["data"]
        assert view["status"] == "ACTIVE"
        _set(
            Subscription,
            sub_id,
            cancel_at_period_end=False,
            status="PAST_DUE",
            current_period_end=now - timedelta(days=1),
        )
        assert _say(client, fam.member, room).status_code == 200
        view = client.get(f"{API}/billing/personas/{fam.pid}", headers=fam.member).json()["data"]
        assert view["status"] == "ACTIVE"
        # 끝난 구독 → 'ended'
        _set(Subscription, sub_id, status="CANCELED")
        _set(Persona, fam.pid, trial_ends_at=now - timedelta(days=30))
        ended = _say(client, fam.member, room)
        assert ended.status_code == 403 and ended.json()["reason"] == "ended"
        assert "요금제를 다시 시작하면" in ended.json()["detail"]
        # 지난 대화는 그대로 본다
        assert client.get(f"{API}/conversations/{room}", headers=fam.member).status_code == 200


def test_sharing_allowed_by_status():
    def ent(status: str, plan: str = "family", active: bool = True):
        return billing.Entitlement("p", plan, status, active, "ivc", None, None, None, 30, 0)

    for status in ("PRELAUNCH", "ADMIN", "TRIAL", "ACTIVE", "CANCELING", "PAST_DUE"):
        assert sharing.sharing_allowed(ent(status)), status
    for status in ("ACTIVE", "CANCELING", "PAST_DUE"):
        assert not sharing.sharing_allowed(ent(status, plan="memories")), status
    for status in ("EXPIRED", "NO_TRIAL", "WAITING_OWNER"):
        assert not sharing.sharing_allowed(ent(status, plan="none", active=False))
    assert sharing.SHARE_STATUSES == {"PRELAUNCH", "ADMIN", "TRIAL"}


def test_owner_suspended_or_status_changed_pauses_members():
    with TestClient(app) as client:
        fam = _family(client)
        room = _room(client, fam.member, fam.pid)
        assert _say(client, fam.member, room).status_code == 200
        _set(User, fam.owner_id, status="SUSPENDED")
        stopped = _say(client, fam.member, room)
        assert stopped.status_code == 403 and stopped.json()["reason"] == "owner"
        assert stopped.json()["detail"] == (
            "지금은 이 대화 상대와 이야기할 수 없어요. 나눈 대화는 그대로 볼 수 있어요."
        )
        assert client.get(f"{API}/conversations/{room}", headers=fam.member).status_code == 200
        assert client.get(f"{API}/personas", headers=fam.member).json()["data"][0]["id"] == fam.pid
        _set(User, fam.owner_id, status="ACTIVE")
        assert _say(client, fam.member, room).status_code == 200
        # 그분 상태를 바꾸면 주인의 확인이 거둬지고, 다시 확인할 때까지 가족은 멈춘다
        changed = client.put(
            f"{API}/personas/{fam.pid}",
            headers=fam.owner,
            json={"basics": OWNER_BASICS | {"status": "alive"}},
        )
        assert changed.status_code == 200, changed.text
        assert _consent(fam.owner_id, f"family_share:{fam.pid}").agreed is False
        paused = _say(client, fam.member, room)
        assert paused.status_code == 403 and paused.json()["reason"] == "consent"
        assert paused.json()["detail"].startswith("지은님이 가족 공유를 다시 확인하면")
        view = client.get(f"{API}/billing/personas/{fam.pid}", headers=fam.member).json()["data"]
        assert view["paused"] == {"reason": "consent", "text": paused.json()["detail"]}
        page = client.get(f"{API}/personas/{fam.pid}/shares", headers=fam.owner).json()["data"]
        assert page["subjectKind"] == "alive" and page["need"] == ALL_OWNER
        # 이력에는 주인이 거둔 것이 아니라 그분 상태를 바꿔 거둬졌다고 남는다
        records = _all(
            select(ConsentRecord)
            .where(
                ConsentRecord.user_id == fam.owner_id,
                ConsentRecord.consent_type == f"family_share:{fam.pid}",
            )
            .order_by(ConsentRecord.created_at)
        )
        assert [(r.agreed, r.channel) for r in records] == [(True, "web"), (False, "share_status")]
        # 다시 확인(셋)만 한다 — 새 링크는 만들지 않는다
        confirmed = client.post(
            f"{API}/personas/{fam.pid}/shares/confirm",
            headers=fam.owner,
            json={"agreements": ALL_OWNER},
        )
        assert confirmed.status_code == 200 and confirmed.json()["data"] == {"need": []}
        assert _say(client, fam.member, room).status_code == 200
        assert (
            _count(select(func.count(PersonaShare.id)).where(PersonaShare.persona_id == fam.pid))
            == 1
        )
        # 같은 상태로 고치면 거두지 않는다
        client.put(
            f"{API}/personas/{fam.pid}",
            headers=fam.owner,
            json={"basics": OWNER_BASICS | {"status": "alive", "calledMe": "강아지"}},
        )
        assert _consent(fam.owner_id, f"family_share:{fam.pid}").agreed is True


def test_owner_reconfirms_without_a_link_even_when_every_seat_is_used(settings):
    """그분 상태를 바꿔 멈춘 가족은 주인이 확인만 다시 하면 이어서 이야기한다(자리가 다 차도)."""
    with TestClient(app) as client:
        fam = _family(client)
        tokens = [_link(client, fam.owner, fam.pid)[0] for _ in range(4)]
        room = _room(client, fam.member, fam.pid)
        seats = select(func.count(PersonaShare.id)).where(PersonaShare.persona_id == fam.pid)
        assert _count(seats) == 5
        client.put(
            f"{API}/personas/{fam.pid}",
            headers=fam.owner,
            json={"basics": OWNER_BASICS | {"status": "alive"}},
        )
        assert _say(client, fam.member, room).json()["reason"] == "consent"
        confirm = f"{API}/personas/{fam.pid}/shares/confirm"
        # 다시 보내기로는 확인할 수 없다(확인은 이 길로만)
        renewed = client.post(
            f"{API}/personas/{fam.pid}/shares/{_get_open(fam.pid)}/renew", headers=fam.owner
        )
        assert renewed.status_code == 409 and renewed.json()["code"] == "share_consent_needed"
        # 하나만 확인하면 남은 둘을 돌려주고, 가족은 아직 멈춰 있다(모르는 종류는 무시)
        partial = client.post(
            confirm, headers=fam.owner, json={"agreements": ["family_share", "terms", "x"]}
        )
        assert partial.status_code == 200
        assert partial.json()["data"] == {
            "need": ["family_share_subject", "family_share_sensitive"]
        }
        assert _say(client, fam.member, room).status_code == 403
        assert _consent(fam.owner_id, f"terms:{fam.pid}") is None
        # 잠시 초대를 멈춘 때에도 확인은 받는다(이미 함께하는 가족은 그대로 쓴다)
        settings.share_open = False
        done = client.post(confirm, headers=fam.owner, json={"agreements": ALL_OWNER})
        assert done.status_code == 200 and done.json()["data"] == {"need": []}
        assert _say(client, fam.member, room).status_code == 200
        assert _count(seats) == 5  # 새 자리(링크)를 만들지 않았다
        assert all(_preview(client, t)["state"] == "unavailable" for t in tokens)
        settings.share_open = True
        assert all(_preview(client, t)["state"] == "open" for t in tokens)
        page = client.get(f"{API}/personas/{fam.pid}/shares", headers=fam.owner).json()["data"]
        assert page["need"] == [] and page["used"] == 5
        # 확인할 것이 없어도 그대로 남은 것(없음)을 돌려준다
        again = client.post(confirm, headers=fam.owner, json={})
        assert again.status_code == 200 and again.json()["data"] == {"need": []}
        # 다 만들지 않은 대화 상대는 받지 않는다
        draft = _persona(fam.owner_id)
        _set(Persona, draft, setup_step=SetupStep.QUESTIONS)
        refused = client.post(
            f"{API}/personas/{draft}/shares/confirm",
            headers=fam.owner,
            json={"agreements": ALL_OWNER},
        )
        assert refused.status_code == 409 and refused.json()["code"] == "share_not_ready"
        assert _consent(fam.owner_id, f"family_share:{draft}") is None


def _get_open(pid: str) -> str:
    """쓰지 않은 링크 하나의 자리 id."""
    rows = _all(
        select(PersonaShare).where(PersonaShare.persona_id == pid, PersonaShare.user_id.is_(None))
    )
    return rows[0].id


def test_member_billing_view_has_no_owner_fields():
    with TestClient(app) as client:
        fam = _family(client)
        view = client.get(f"{API}/billing/personas/{fam.pid}", headers=fam.member).json()["data"]
        assert set(view) == {
            "personaId", "role", "status", "active", "conversationsLimit", "conversationsUsed",
            "conversationsLeft", "conversationsResetAt", "madeAt", "textOnly", "step", "title",
            "relation", "deceased", "ownerName", "paymentsOpen", "paused",
        }  # fmt: skip
        assert (view["role"], view["title"], view["ownerName"]) == ("member", "엄마", "지은")
        assert view["deceased"] is True and view["relation"] is None
        owner_view = client.get(f"{API}/billing/personas/{fam.pid}", headers=fam.owner).json()[
            "data"
        ]
        for field in ("plan", "planName", "subscription", "upgradeQuotes", "request", "canChange"):
            assert field in owner_view and field not in view
        assert owner_view["title"] == "할머니"
        # /billing/me 는 내가 만든 대화 상대만
        mine = client.get(f"{API}/billing/me", headers=fam.member).json()["data"]
        assert mine["personas"] == []


# ────────────────────────── 9. 예전 목소리(MiniMax·ElevenLabs) ──────────────────────────

LEGACY_VOICE_TEXT = (
    "예전에 만든 목소리라 함께 쓰는 가족에게는 아직 글로만 드려요. "
    "만든 분이 목소리를 새로 만들면 들을 수 있어요."
)


@pytest.mark.parametrize(
    ("voice_id", "voice_provider"),
    [("TalkTo_old_voice", "minimax"), ("eleven_old_voice", "elevenlabs")],
)
def test_members_hear_only_fish_voices(fake_ai: FakeAI, voice_id: str, voice_provider: str):
    # 가족에게는 Fish 목소리로만 읽어 준다. 예전 목소리는 다시 만들 때까지 주인만 듣는다
    with TestClient(app) as client:
        fam = _family(client, voice_id=voice_id, voice_provider=voice_provider)
        item = client.get(f"{API}/personas", headers=fam.member).json()["data"][0]
        assert item["hasVoice"] is False
        page = client.get(f"{API}/personas/{fam.pid}/shares", headers=fam.owner).json()["data"]
        assert page["voiceShareable"] is False
        room = _room(client, fam.member, fam.pid)
        _say(client, fam.member, room)
        reply = client.get(f"{API}/conversations/{room}", headers=fam.member).json()["data"][
            "messages"
        ][-1]["id"]
        refused = client.post(f"{API}/messages/{reply}/speech", headers=fam.member)
        assert refused.status_code == 409
        assert refused.json() == {"detail": LEGACY_VOICE_TEXT, "code": "share_voice_unavailable"}
        assert "/v1/voice/speech" not in fake_ai.calls
        # 주인은 그대로 듣는다
        owner_room = _room(client, fam.owner, fam.pid)
        _say(client, fam.owner, owner_room)
        owner_reply = client.get(f"{API}/conversations/{owner_room}", headers=fam.owner).json()[
            "data"
        ]["messages"][-1]["id"]
        assert (
            client.post(f"{API}/messages/{owner_reply}/speech", headers=fam.owner).status_code
            == 200
        )
        assert [c["voiceId"] for c in fake_ai.calls["/v1/voice/speech"]] == [voice_id]


def test_member_voice_follows_the_voice_id_prefix():
    # AI 서버는 회사 칸이 아니라 id 앞글자로 회사를 고른다('fish_' → Fish, 'TalkTo' → MiniMax,
    # 그 밖 → ElevenLabs). 가져온 목소리는 회사 칸이 비어 있기도 하다
    assert sharing.member_voice_ok(Persona(voice_id="fish_x", voice_provider="fish"))
    assert sharing.member_voice_ok(Persona(voice_id="fish_x", voice_provider=None))
    assert not sharing.member_voice_ok(Persona(voice_id="TalkTo_x", voice_provider=None))
    assert not sharing.member_voice_ok(Persona(voice_id="TalkTo-x", voice_provider="fish"))
    assert not sharing.member_voice_ok(Persona(voice_id="eleven-x", voice_provider="elevenlabs"))
    assert not sharing.member_voice_ok(Persona(voice_id="eleven-x", voice_provider="fish"))
    assert not sharing.member_voice_ok(Persona(voice_id=None, voice_provider=None))
    # 주인 화면의 voiceShareable 은 예전 목소리일 때만 False(목소리가 아직 없으면 막을 것이 없다)
    assert sharing.voice_shareable(Persona(voice_id=None, voice_provider=None))
    assert sharing.voice_shareable(Persona(voice_id="fish_x", voice_provider="fish"))
    assert not sharing.voice_shareable(Persona(voice_id="eleven-x", voice_provider="elevenlabs"))
    assert not sharing.voice_shareable(Persona(voice_id="TalkTo_x", voice_provider="minimax"))


def test_members_hear_other_voices(fake_ai: FakeAI):
    with TestClient(app) as client:
        fam = _family(client, voice_id="fish_voice_test", voice_provider="fish")
        assert client.get(f"{API}/personas", headers=fam.member).json()["data"][0]["hasVoice"]
        room = _room(client, fam.member, fam.pid)
        _say(client, fam.member, room)
        reply = client.get(f"{API}/conversations/{room}", headers=fam.member).json()["data"][
            "messages"
        ][-1]["id"]
        heard = client.post(f"{API}/messages/{reply}/speech", headers=fam.member)
        assert heard.status_code == 200 and heard.json()["data"]["audioUrl"]
        played = _all(
            select(AnalyticsEvent).where(
                AnalyticsEvent.name == "reply_played", AnalyticsEvent.persona_id == fam.pid
            )
        )
        assert [e.props.get("shared") for e in played] == [True]
        # 멈춘 가족은 새로 읽지 못하고, 이미 만든 소리는 다시 듣는다
        _withdraw_owner(fam.owner_id, fam.pid)
        again = client.post(f"{API}/messages/{reply}/speech", headers=fam.member)
        assert again.status_code == 200
        assert _say(client, fam.member, room).status_code == 403


def test_member_reply_is_read_with_speech_text(fake_ai: FakeAI):
    """함께 쓰는 가족의 대답도 AI 가 준 목소리용 글(speechText, 연기 표시 포함)로 읽는다.

    화면·다음 대화 기록에는 대답 글(content)만 간다.
    """
    with TestClient(app) as client:
        fam = _family(client, voice_id="fish_voice_test", voice_provider="fish")
        room = _room(client, fam.member, fam.pid)
        fake_ai.speech_text = "[웃으며] 그래, 왔구나."
        sent = _say(client, fam.member, room)
        assert sent.status_code == 200, sent.text
        reply = sent.json()["data"]["assistantMessage"]
        assert reply["content"] == "그래, 왔구나." and "speechText" not in reply
        assert _get(Message, reply["id"]).speech_text == "[웃으며] 그래, 왔구나."
        heard = client.post(f"{API}/messages/{reply['id']}/speech", headers=fam.member)
        assert heard.status_code == 200, heard.text
        assert fake_ai.calls["/v1/voice/speech"][-1]["text"] == "[웃으며] 그래, 왔구나."
        _say(client, fam.member, room, "또 왔어요")
        assert [m["content"] for m in fake_ai.replies()[-1]["history"]][-1] == "그래, 왔구나."


# ────────────────────────── 10. 정리 ──────────────────────────


def _member_trace(client: TestClient, fam: Family) -> tuple[str, str]:
    """가족의 방에 말·평가·저장소 파일을 남긴다. (방 id, 저장소 키)."""
    room = _room(client, fam.member, fam.pid)
    assert _say(client, fam.member, room).status_code == 200
    reply = client.get(f"{API}/conversations/{room}", headers=fam.member).json()["data"][
        "messages"
    ][-1]["id"]
    rated = client.post(
        f"{API}/messages/{reply}/feedback", headers=fam.member, json={"rating": "UP"}
    )
    assert rated.status_code == 200, rated.text
    key = f"conversations/{room}/voice/test.webm"
    get_storage().put(key, b"voice", "audio/webm")
    return room, key


def _gone(room: str, key: str) -> None:
    assert _get(Conversation, room) is None
    assert _count(select(func.count(Message.id)).where(Message.conversation_id == room)) == 0
    assert get_storage().exists(key) is False


def _member_consent_withdrawn(member_id: str, pid: str, channel: str = "web") -> None:
    """가족 동의의 지금 상태 줄은 지워지고, 이력에는 들어온 것과 끝난 것(누가 끝냈는지)이 남는다."""
    kind = f"family_share_member:{pid}"
    assert _consent(member_id, kind) is None
    records = _all(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == member_id, ConsentRecord.consent_type == kind)
        .order_by(ConsentRecord.created_at)
    )
    assert [(r.agreed, r.channel) for r in records] == [(True, "web"), (False, channel)]


def test_leaving_removes_the_members_room_and_audio():
    with TestClient(app) as client:
        fam = _family(client)
        room, key = _member_trace(client, fam)
        feedback = _count(select(func.count(Feedback.id)).where(Feedback.user_id == fam.member_id))
        assert feedback == 1
        used = _usage(fam.pid)
        left = client.delete(f"{API}/personas/{fam.pid}/shares/{fam.share_id}", headers=fam.member)
        assert left.status_code == 200 and left.json()["data"]["ended"] is True
        # 지운 대화가 있으면 지운 일시를 남기고 백업 날짜까지 알린다(WO 7)
        notice = left.json()["data"]["deletion"]
        assert notice["deleted"] is True and notice["vendorOk"] is None
        assert "서버 백업에는" in notice["message"] and notice["backupUntil"]
        records = _all(select(DeletionRecord).where(DeletionRecord.persona_id == fam.pid))
        assert [(r.kind, r.user_id, r.requested_by) for r in records] == [
            ("share_member", fam.member_id, "owner")
        ]
        assert records[0].detail == {"conversations": 1}
        _gone(room, key)
        assert _count(select(func.count(Feedback.id)).where(Feedback.user_id == fam.member_id)) == 0
        assert _get(PersonaShare, fam.share_id) is None
        _member_consent_withdrawn(fam.member_id, fam.pid)
        assert _usage(fam.pid) == used  # 내보냈다 다시 불러 횟수를 되살리지 못한다
        assert client.get(f"{API}/personas", headers=fam.member).json()["data"] == []
        assert client.get(f"{API}/personas/{fam.pid}", headers=fam.member).status_code == 404
        assert client.get(f"{API}/conversations/{room}", headers=fam.member).status_code == 404
        ended = _all(
            select(AnalyticsEvent).where(
                AnalyticsEvent.name == "share_ended", AnalyticsEvent.persona_id == fam.pid
            )
        )
        assert [e.props for e in ended] == [{"by": "member"}]


def test_owner_removes_a_member_and_cancels_a_link():
    with TestClient(app) as client:
        fam = _family(client)
        room, key = _member_trace(client, fam)
        owner_room = _room(client, fam.owner, fam.pid)
        _say(client, fam.owner, owner_room)
        token, item = _link(client, fam.owner, fam.pid)
        removed = client.delete(
            f"{API}/personas/{fam.pid}/shares/{fam.share_id}", headers=fam.owner
        )
        assert removed.status_code == 200
        _gone(room, key)
        # 이력에는 가족이 스스로 거둔 것이 아니라 주인이 내보냈다고 남는다
        _member_consent_withdrawn(fam.member_id, fam.pid, "share_owner")
        assert _get(Conversation, owner_room) is not None  # 주인 방은 그대로
        # 쓰지 않은 링크 취소
        canceled = client.delete(f"{API}/personas/{fam.pid}/shares/{item['id']}", headers=fam.owner)
        assert canceled.status_code == 200
        assert _preview(client, token)["state"] == "invalid"
        # 내보낸 가족이 쥔 방 번호로 다시 말해도 404
        assert _say(client, fam.member, room).status_code == 404
        ended = _all(
            select(AnalyticsEvent).where(
                AnalyticsEvent.name == "share_ended", AnalyticsEvent.persona_id == fam.pid
            )
        )
        assert [e.props for e in ended] == [{"by": "owner"}]


def test_stop_ends_everyone_and_withdraws_owner_consents():
    with TestClient(app) as client:
        fam = _family(client, basics=OWNER_BASICS | {"status": "alive"})
        room, key = _member_trace(client, fam)
        token, _ = _link(client, fam.owner, fam.pid)
        stopped = client.post(f"{API}/personas/{fam.pid}/shares/stop", headers=fam.owner)
        assert stopped.status_code == 200 and stopped.json()["data"]["removed"] == 2
        assert "서버 백업에는" in stopped.json()["data"]["deletion"]["message"]
        records = _all(select(DeletionRecord).where(DeletionRecord.persona_id == fam.pid))
        # 대화가 있던 가족 한 분만(쓰지 않은 링크는 지운 것이 없다)
        assert [(r.kind, r.user_id) for r in records] == [("share_member", fam.member_id)]
        _gone(room, key)
        _member_consent_withdrawn(fam.member_id, fam.pid, "share_stop")
        assert (
            _count(select(func.count(PersonaShare.id)).where(PersonaShare.persona_id == fam.pid))
            == 0
        )
        for t in ALL_OWNER:
            assert _consent(fam.owner_id, f"{t}:{fam.pid}").agreed is False
            records = _all(
                select(ConsentRecord)
                .where(
                    ConsentRecord.user_id == fam.owner_id,
                    ConsentRecord.consent_type == f"{t}:{fam.pid}",
                )
                .order_by(ConsentRecord.created_at)
            )
            assert [(r.agreed, r.channel) for r in records][-1] == (False, "share_stop"), t
        assert _preview(client, token)["state"] == "invalid"
        page = client.get(f"{API}/personas/{fam.pid}/shares", headers=fam.owner).json()["data"]
        assert page["need"] == ALL_OWNER and page["items"] == []


def test_persona_delete_takes_the_family_with_it():
    with TestClient(app) as client:
        fam = _family(client)
        room, key = _member_trace(client, fam)
        assert client.delete(f"{API}/personas/{fam.pid}", headers=fam.owner).status_code == 200
        _gone(room, key)
        assert _get(PersonaShare, fam.share_id) is None
        assert _consent(fam.member_id, f"family_share_member:{fam.pid}") is None
        assert client.get(f"{API}/personas", headers=fam.member).json()["data"] == []


def test_member_account_deletion_leaves_every_seat():
    with TestClient(app) as client:
        fam = _family(client)
        room, key = _member_trace(client, fam)
        gone = client.request(
            "DELETE", f"{API}/auth/me", headers=fam.member, json={"confirm": "탈퇴"}
        )
        assert gone.status_code == 200, gone.text
        _gone(room, key)
        _member_consent_withdrawn(fam.member_id, fam.pid, "share_account")
        assert _get(PersonaShare, fam.share_id) is None
        assert _get(Persona, fam.pid) is not None  # 주인의 대화 상대는 그대로
        page = client.get(f"{API}/personas/{fam.pid}/shares", headers=fam.owner).json()["data"]
        assert page["items"] == [] and page["used"] == 0


def test_owner_account_deletion_also_leaves_seats_elsewhere():
    with TestClient(app) as client:
        fam = _family(client)
        room, key = _member_trace(client, fam)
        # 가족(철수)도 자기 대화 상대를 만들어 주인(지은)을 초대했다
        other_pid = _persona(
            fam.member_id, basics=OWNER_BASICS | {"myName": "철수", "myRelation": "아들"}
        )
        token, _ = _link(client, fam.member, other_pid)
        joined = _accept(
            client,
            fam.owner,
            token,
            {"myName": "지은", "myRelation": "손녀", "addressTerm": "할머니"},
        )
        assert joined.status_code == 201
        seat = joined.json()["data"]["shareId"]
        owner_room = _room(client, fam.owner, other_pid)
        _say(client, fam.owner, owner_room)
        gone = client.request(
            "DELETE", f"{API}/auth/me", headers=fam.owner, json={"confirm": "탈퇴"}
        )
        assert gone.status_code == 200, gone.text
        # 지은이 만든 대화 상대와 그 가족의 방이 지워지고, 지은이 들어가 있던 자리에서도 빠진다
        assert _get(Persona, fam.pid) is None
        _gone(room, key)
        assert _get(PersonaShare, seat) is None and _get(Conversation, owner_room) is None
        assert _get(Persona, other_pid) is not None


def test_expired_links_are_purged_daily(monkeypatch):
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid)
        _, old = _link(client, h, pid)
        _, fresh = _link(client, h, pid)
    _set(PersonaShare, old["id"], expires_at=datetime.now(UTC) - timedelta(hours=2))
    monkeypatch.setattr(main_module, "_last_cleanup", None)
    assert _run(main_module.cleanup_daily(datetime.now(UTC))) is True
    assert _get(PersonaShare, old["id"]) is None
    assert _get(PersonaShare, fresh["id"]) is not None


# ────────────────────────── 11. 동의 ──────────────────────────


def test_share_consents_are_not_withdrawn_generically_and_show_the_members_title():
    with TestClient(app) as client:
        fam = _family(client)
        for who, kind in (
            (fam.member, f"family_share_member:{fam.pid}"),
            (fam.owner, f"family_share:{fam.pid}"),
        ):
            refused = client.post(
                f"{API}/consents", headers=who, json={"consentType": kind, "agreed": False}
            )
            assert refused.status_code == 400
            assert refused.json()["detail"] == "이 동의는 대화 상대 화면에서 해 주세요."
        assert _consent(fam.member_id, f"family_share_member:{fam.pid}").agreed
        rows = client.get(f"{API}/consents/me", headers=fam.member).json()["data"]
        row = next(r for r in rows if r["type"] == f"family_share_member:{fam.pid}")
        assert row["personaId"] == fam.pid and row["personaTitle"] == "엄마"
        assert row["title"] == "가족 공유로 함께 이야기하기"
        owner_rows = client.get(f"{API}/consents/me", headers=fam.owner).json()["data"]
        owner_row = next(r for r in owner_rows if r["type"] == f"family_share:{fam.pid}")
        assert owner_row["personaTitle"] == "할머니"
        # 계정 동의를 다시 묻지 않는다
        assert (
            client.get(f"{API}/auth/me", headers=fam.member).json()["data"]["consentsNeeded"] == []
        )


# ────────────────────────── 12. 통계 ──────────────────────────


def test_member_only_users_stay_out_of_the_creator_funnel():
    with TestClient(app) as client:
        owner, owner_id, _ = _signup(client, "김지은")
        pid = _persona(owner_id)
        token, _ = _link(client, owner, pid)
        start = datetime.now(UTC)
        member, _, _ = _signup(client, "박철수")
        assert _accept(client, member, token).status_code == 201
        room = _room(client, member, pid)
        for _ in range(2):
            assert _say(client, member, room).status_code == 200

    async def funnel(db):
        return {row["key"]: row["users"] for row in await stats_module._funnel(db, start)}

    counts = _db(funnel)
    assert counts["signed_up"] == 1 and counts["consented"] == 1
    for key in ("persona_created", "recordings_uploaded", "build_done", "first_message", "engaged"):
        assert counts[key] == 0, key


def test_funnel_consented_step_needs_every_account_consent():
    # 깔때기의 '동의'는 아홉 가지를 모두 받은 회원이다(모두 가입할 때 받는다). 예전 가입 단계
    # 여섯 가지만 받은 회원은 아직 동의 전이다. 통계 이벤트가 없던 때의 회원도 동의 줄로 센다
    start = datetime.now(UTC)
    former_signup = ["age14", "service", "terms", "privacy", "overseas", "overseas_fish"]

    async def go(db):
        everything = User(email=f"f1-{uuid4().hex[:6]}@example.com", name="모두", password_hash="x")
        six_only = User(email=f"f2-{uuid4().hex[:6]}@example.com", name="여섯만", password_hash="x")
        db.add_all([everything, six_only])
        await db.flush()
        for kind in legal.ACCOUNT_TYPES:
            await legal.record(db, everything, kind, True)
        for kind in former_signup:
            await legal.record(db, six_only, kind, True)
        await db.commit()
        return {row["key"]: row["users"] for row in await stats_module._funnel(db, start)}

    counts = _db(go)
    assert counts["signed_up"] == 2 and counts["consented"] == 1


def test_share_events_are_allowlisted_and_carry_no_profile_text():
    assert {"share_link_created", "share_joined", "share_profile_updated", "share_ended"} <= (
        analytics.SERVER_EVENTS
    )
    assert analytics.CLIENT_EVENTS["share_link_sent"] == frozenset({"method"})
    assert analytics.CLIENT_EVENTS["join_declined"] == frozenset({"reason"})
    with TestClient(app) as client:
        fam = _family(client)
        token, _ = _link(
            client, fam.owner, fam.pid, family_row="영희", prefill={"addressTerm": "엄마"}
        )
        client.patch(
            f"{API}/personas/{fam.pid}/shares/{fam.share_id}",
            headers=fam.member,
            json={"calledMe": "우리 아들"},
        )
        sent = client.post(
            f"{API}/analytics/events",
            headers=fam.owner,
            json={"name": "share_link_sent", "props": {"method": "copy", "token": token}},
        )
        assert sent.status_code in (200, 201, 202, 204)
        client.delete(f"{API}/personas/{fam.pid}/shares/{fam.share_id}", headers=fam.owner)
    events = _all(
        select(AnalyticsEvent).where(
            AnalyticsEvent.persona_id == fam.pid, AnalyticsEvent.name.like("share_%")
        )
    )
    names = {e.name for e in events}
    assert {"share_link_created", "share_joined", "share_profile_updated", "share_ended"} <= names
    created = [e.props for e in events if e.name == "share_link_created"]
    assert {"prefilled": True, "origin": "web", "renew": False} in created
    joined = next(e.props for e in events if e.name == "share_joined")
    assert joined == {"prefilled": False, "edited": True}
    text = json.dumps([e.props for e in events], ensure_ascii=False)
    for word in ("철수", "엄마", "아들", "영희", "지은", token):
        assert word not in text
    client_events = _all(select(AnalyticsEvent).where(AnalyticsEvent.name == "share_link_sent"))
    assert client_events and all(e.props == {"method": "copy"} for e in client_events)


def test_share_error_shape_and_member_texts_have_no_plan_wording():
    for code, (status_code, _) in sharing.ERRORS.items():
        error = sharing.fail(code, cap=5, need=["family_share"])
        assert error.status_code == status_code and error.code == code
        assert "{" not in error.detail and error.detail
    for reason in (
        "plan",
        "ended",
        "waiting_owner",
        "owner",
        "consent",
        "unavailable",
        "stopped",
        "minor_scope",
    ):
        text = sharing.paused_text(reason, "지은")
        assert text and "{" not in text
        assert "요금제를 고르" not in text and "요금제 보기" not in text
    for vendor in ("MiniMax", "Fish", "ElevenLabs", "OpenAI"):
        assert vendor not in json.dumps(sharing.ERRORS, ensure_ascii=False)
        assert vendor not in json.dumps(sharing.PAUSED_TEXTS, ensure_ascii=False)


def test_existing_owner_never_sees_new_account_consents():
    """가족 공유는 대화 상대별 종류라 기존 회원의 계정 동의 게이트를 다시 열지 않는다."""

    async def go(db):
        user = User(email=f"old-{uuid4().hex[:6]}@example.com", name="예전 회원", password_hash="x")
        db.add(user)
        await db.flush()
        for kind in legal.ACCOUNT_TYPES:
            await legal.record(db, user, kind, True)
        await db.commit()
        return await legal.missing_required(db, user)

    assert _db(go) == []
    assert all(":" not in t for t in legal.ACCOUNT_TYPES)
    assert UserRole.ADMIN  # 관리자는 게이트를 지나지 않는다(deps.get_current_user)


def test_joined_personas_do_not_count_toward_the_prelaunch_cap(settings, monkeypatch):
    """결제 전 '대화 상대 3명까지'는 내가 만든 것만 센다(함께 쓰는 대화 상대는 세지 않는다)."""
    monkeypatch.setattr(settings, "prelaunch_personas_per_account", 1)
    with TestClient(app) as client:
        fam = _family(client)
        mine = client.post(f"{API}/personas", headers=fam.member, json=OWNER_BASICS)
        assert mine.status_code == 201, mine.text
        capped = client.post(f"{API}/personas", headers=fam.member, json=OWNER_BASICS)
        assert capped.status_code == 409
        # 이미 상한까지 만든 사람도 초대받아 들어올 수 있다
        token, _ = _link(client, fam.owner, fam.pid)
        guest_h, full_id, _ = _signup(client, "박영희")
        _persona(full_id)  # 이미 상한(1명)까지 만들었다
        assert _accept(client, guest_h, token).status_code == 201
        listed = client.get(f"{API}/personas", headers=guest_h).json()["data"]
        assert sorted(p["role"] for p in listed) == ["member", "owner"]


# ────────────────────────── 13. 아이의 대화 상대·처리 게이트(법률 자문) ──────────────────────────

CHILD_BASICS = {
    "name": "김민지",
    "relation": "딸",
    "addressTerm": "민지",
    "status": "alive",
    "myName": "지은",
    "myRelation": "어머니",
    "calledMe": "엄마",
    "minor": True,
    "birthYm": "2010-01",
    "guardianBasis": "both_agreed",
}


def test_minor_persona_shares_only_with_direct_family():
    """아이의 대화 상대는 법정대리인과 직계가족(부모·조부모)만(WO 4 '이용 범위')."""
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid, basics=CHILD_BASICS, answers={})
        guardian_ok(None, pid)
        page = client.get(f"{API}/personas/{pid}/shares", headers=h).json()["data"]
        assert page["minorScope"] is True
        assert page["relations"] == list(intake.MINOR_SHARE_RELATIONS)
        scope = {
            "detail": "아이의 대화 상대는 법정대리인과 직계가족(부모·조부모)만 함께 쓸 수 있어요. "
            "관계를 목록에서 골라 주세요.",
            "code": "share_minor_scope",
            "field": "myRelation",
        }
        wrong = client.post(
            f"{API}/personas/{pid}/shares",
            headers=h,
            json={"agreements": ALL_OWNER, "prefill": {"myRelation": "삼촌"}},
        )
        assert wrong.status_code == 400 and wrong.json() == scope
        token, share = _link(client, h, pid, prefill={"myRelation": "할머니"})
        open_token, open_share = _link(client, h, pid)
        assert share["prefill"]["myRelation"] == "할머니"
        edited = client.patch(
            f"{API}/personas/{pid}/shares/{open_share['id']}",
            headers=h,
            json={"myRelation": "이모"},
        )
        assert edited.status_code == 400 and edited.json()["code"] == "share_minor_scope"
        member, _, _ = _signup(client, "박할머니")
        resolved = _resolve(client, member, token)
        assert resolved["minorScope"] is True
        assert resolved["relations"] == list(intake.MINOR_SHARE_RELATIONS)
        refused = _accept(client, member, open_token, PROFILE)
        assert refused.status_code == 400 and refused.json()["code"] == "share_minor_scope"
        joined = _accept(client, member, token, PROFILE | {"myRelation": "할머니"})
        assert joined.status_code == 201, joined.text
        # 아이가 아닌 대화 상대는 그대로(직접 입력·모든 관계)
        other = _persona(uid)
        plain = client.get(f"{API}/personas/{other}/shares", headers=h).json()["data"]
        assert plain["minorScope"] is False and plain["relations"] == intake.MY_RELATIONS


def test_sharing_asks_the_processing_gate(monkeypatch):
    """링크 만들기·다시 보내기는 invite, 들어오기·미리 보기는 join(가족) 게이트를 지난다."""
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid)
        token, _ = _link(client, h, pid, agreements=["family_share"])
        asked: list[tuple[str, bool]] = []

        async def blocked(db, persona, action, *, member=False):
            asked.append((action, member))
            return gate.Block("guardian", "먼저 법정대리인 확인을 마쳐 주세요.")

        monkeypatch.setattr(gate, "block", blocked)
        refused = client.post(f"{API}/personas/{pid}/shares", headers=h, json={})
        assert refused.status_code == 409 and refused.json() == {
            "detail": "먼저 법정대리인 확인을 마쳐 주세요.",
            "code": "guardian",
        }
        assert ("invite", False) in asked
        member, _, _ = _signup(client, "박철수")
        assert _preview(client, token)["state"] == "unavailable"
        stopped = _accept(client, member, token)
        assert stopped.status_code == 409 and stopped.json()["code"] == "invite_unavailable"
        assert ("join", True) in asked
