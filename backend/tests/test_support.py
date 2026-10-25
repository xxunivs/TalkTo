"""고객센터: 문의 방법(GET /support)과 관리자 문의 코드 찾기(GET /admin/support/lookup).

시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다)."""

import asyncio
import importlib.util
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_persona_setup import AGREEMENTS, BASICS

from app.core.config import Settings, get_settings
from app.db.models import ORIGIN_IMPORT, Person, Persona, PersonaShare, User
from app.db.session import SessionLocal
from app.main import app
from app.services import support

API = "/api/v1"
CODE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$")


@pytest.fixture(autouse=True)
def support_settings(monkeypatch):
    """로컬 .env 값과 상관없이 정해 둔다."""
    s = get_settings()
    monkeypatch.setattr(s, "kakao_channel_chat_url", None)
    monkeypatch.setattr(s, "support_hours", "평일 10시~18시")
    monkeypatch.setattr(s, "business_email", "help@example.com")
    return s


def _signup(client: TestClient, agreements=AGREEMENTS) -> tuple[dict, str]:
    email = f"support-{uuid4().hex[:10]}@example.com"
    response = client.post(
        f"{API}/auth/signup",
        json={
            "email": email,
            "password": "password123",
            "name": "홍길동",
            "agreements": agreements,
        },
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['data']['bearerToken']}"}
    return headers, client.get(f"{API}/auth/me", headers=headers).json()["data"]["id"]


def _admin(client: TestClient) -> dict:
    s = get_settings()
    login = client.post(
        f"{API}/auth/login",
        json={"email": s.bootstrap_admin_email, "password": s.bootstrap_admin_password},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['data']['bearerToken']}"}


def test_codes_are_short_stable_and_readable():
    a, b = str(uuid4()), str(uuid4())
    assert CODE.match(support.inquiry_code(a))
    assert support.inquiry_code(a) == support.inquiry_code(a)
    assert support.inquiry_code(a) != support.inquiry_code(b)
    # 코드에 id 가 드러나지 않는다
    assert support.inquiry_code(a).replace("-", "").lower() not in a.replace("-", "")
    # 옮겨 적다 틀리기 쉬운 글자도 고쳐 읽는다(O→0, I·L→1), 소문자·붙여 쓴 것도
    assert support.normalize("k7m3q9xa") == "K7M3-Q9XA"
    assert support.normalize("K7M3 Q9XA") == "K7M3-Q9XA"
    assert support.normalize("O1L3-Q9XA") == "0113-Q9XA"
    assert support.normalize("K7M3-Q9X") is None and support.normalize("UUUU-UUUU") is None
    assert support.persona_code("3f2a9c1d-0000-4000-8000-000000000000") == "3F2A9C"
    # 화면이 복사해 주는 줄을 통째로 붙여 넣어도 읽는다
    line = "문의 코드 K7M3-Q9XA · 대화 상대 3F2A9C · 앱 20261021093000"
    assert support.parse(line) == ("K7M3-Q9XA", "3F2A9C")
    assert support.parse("k7m3q9xa") == ("K7M3-Q9XA", None)
    assert support.parse("안녕하세요 앱 20261021093000") == (None, None)


def test_support_info_and_hidden_kakao_button(support_settings):
    with TestClient(app) as client:
        assert client.get(f"{API}/support").status_code == 401
        # 필수 동의 전에도 열린다(막힌 분도 물어볼 수 있게)
        h, user_id = _signup(client, agreements=[])
        assert client.get(f"{API}/personas", headers=h).status_code == 403
        data = client.get(f"{API}/support", headers=h).json()["data"]
        assert data == {
            "kakaoChannelUrl": None,  # 비어 있으면 화면이 단추를 숨긴다
            "hours": "평일 10시~18시",
            "email": "help@example.com",
            "inquiryCode": support.inquiry_code(user_id),
        }
        assert CODE.match(data["inquiryCode"])

        support_settings.kakao_channel_chat_url = " https://pf.kakao.com/_test/chat "
        data = client.get(f"{API}/support", headers=h).json()["data"]
        assert data["kakaoChannelUrl"] == "https://pf.kakao.com/_test/chat"
        # 대표님이 주신 모양(http)은 https 로 바꿔 내보낸다(단추가 사라지지 않게)
        support_settings.kakao_channel_chat_url = "http://pf.kakao.com/_nNwwX/chat"
        data = client.get(f"{API}/support", headers=h).json()["data"]
        assert data["kakaoChannelUrl"] == "https://pf.kakao.com/_nNwwX/chat"
        # 카카오톡 채널 주소가 아니면 내보내지 않는다(화면이 다른 곳을 열지 않게)
        for bad in ("javascript:alert(1)", "https://evil.example.com/x", "pf.kakao.com/x", ""):
            support_settings.kakao_channel_chat_url = bad
            data = client.get(f"{API}/support", headers=h).json()["data"]
            assert data["kakaoChannelUrl"] is None, bad
        support_settings.business_email = "  "
        support_settings.support_hours = ""
        data = client.get(f"{API}/support", headers=h).json()["data"]
        assert data["email"] is None and data["hours"] is None


@pytest.mark.parametrize(
    ("raw", "url"),
    [
        ("https://pf.kakao.com/_nNwwX/chat", "https://pf.kakao.com/_nNwwX/chat"),
        ("http://pf.kakao.com/_nNwwX/chat", "https://pf.kakao.com/_nNwwX/chat"),
        ("  HTTP://PF.KAKAO.COM/_nNwwX/chat ", "https://pf.kakao.com/_nNwwX/chat"),
        ("https://pf.kakao.com/_nNwwX", "https://pf.kakao.com/_nNwwX"),
        ("https://pf.kakao.com/_nNwwX/chat/", "https://pf.kakao.com/_nNwwX/chat/"),
        # 다른 곳·속이는 주소·다른 길·덧붙인 것
        ("https://evil.example.com/_nNwwX/chat", None),
        ("https://pf.kakao.com.evil.example/_nNwwX/chat", None),
        ("https://pf.kakao.com@evil.example/_nNwwX/chat", None),
        ("https://evil.example@pf.kakao.com/_nNwwX/chat", None),
        ("https://pf.kakao.com:8443/_nNwwX/chat", None),
        ("https://pf.kakao.com/_nNwwX/chat?next=https://evil.example", None),
        ("https://pf.kakao.com/_nNwwX/chat#x", None),
        ("https://pf.kakao.com/_nNwwX/../../evil", None),
        ("https://pf.kakao.com/", None),
        ("https://kakao.com/_nNwwX/chat", None),
        ("javascript:alert(1)//pf.kakao.com/_x/chat", None),
        ("ftp://pf.kakao.com/_nNwwX/chat", None),
        ("//pf.kakao.com/_nNwwX/chat", None),
        ("pf.kakao.com/_nNwwX/chat", None),
        ("https://[pf.kakao.com/_nNwwX/chat", None),
        ("", None),
        (None, None),
    ],
)
def test_only_kakao_channel_addresses_are_sent(raw, url):
    """화면은 이 주소를 그대로 연다(카카오톡 인앱이면 그 창에서). pf.kakao.com 의 채널 주소만."""
    assert support.channel_url(raw) == url


def test_deploy_always_ships_the_kakao_channel():
    """운영 배포는 로컬 .env 값이 비었거나 채널 주소가 아니어도 TalkTo 채널을 올린다
    (그대로 올리면 운영에서 단추가 소리 없이 사라진다). 다른 채널 주소면 그것을 올린다."""
    spec = importlib.util.spec_from_file_location(
        "talkto_deploy", Path(__file__).parents[1] / "deploy" / "aws" / "deploy.py"
    )
    deploy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(deploy)
    talkto = "https://pf.kakao.com/_nNwwX/chat"
    assert Settings.model_fields["kakao_channel_chat_url"].default == talkto
    assert deploy.kakao_channel({}) == talkto
    assert deploy.kakao_channel({"KAKAO_CHANNEL_CHAT_URL": ""}) == talkto
    assert deploy.kakao_channel({"KAKAO_CHANNEL_CHAT_URL": "http://pf.kakao.com/_nNwwX/chat"}) == (
        talkto
    )
    assert deploy.kakao_channel({"KAKAO_CHANNEL_CHAT_URL": "https://evil.example/x"}) == talkto
    other = "https://pf.kakao.com/_other/chat"
    assert deploy.kakao_channel({"KAKAO_CHANNEL_CHAT_URL": other}) == other


def test_admin_finds_the_member_by_inquiry_code():
    with TestClient(app) as client:
        owner, owner_id = _signup(client)
        pid = client.post(f"{API}/personas", headers=owner, json=BASICS).json()["data"]["id"]
        member, member_id = _signup(client)

        async def join():
            async with SessionLocal() as db:
                db.add(
                    PersonaShare(
                        persona_id=pid,
                        token_hash=uuid4().hex * 2,
                        expires_at=datetime.now(UTC) + timedelta(days=7),
                        user_id=member_id,
                        joined_at=datetime.now(UTC),
                        profile={"addressTerm": "어머님"},
                    )
                )
                await db.commit()

        asyncio.run(join())
        admin = _admin(client)
        code = client.get(f"{API}/support", headers=owner).json()["data"]["inquiryCode"]

        # 관리자만
        refused = client.get(f"{API}/admin/support/lookup", headers=owner, params={"q": code})
        assert refused.status_code == 403

        found = client.get(f"{API}/admin/support/lookup", headers=admin, params={"q": code})
        assert found.status_code == 200, found.text
        data = found.json()["data"]
        assert data["code"] == code and data["personaCode"] is None
        assert [u["id"] for u in data["users"]] == [owner_id]
        user = data["users"][0]
        assert user["emailMasked"].startswith("s***@") and "support-" not in str(user)
        assert user["personas"] == [
            {
                "id": pid,
                "code": support.persona_code(pid),
                "title": "할머니",  # 성함은 싣지 않는다
                "role": "owner",
                "step": "UPLOAD",
                "matched": False,
            }
        ]
        assert BASICS["name"] not in found.text

        # 화면이 복사해 준 줄 통째로(함께 쓰는 가족) — 대화 상대도 짚어 준다
        member_code = support.inquiry_code(member_id)
        short = support.persona_code(pid)
        line = f"문의 코드 {member_code.lower()} · 대화 상대 {short} · 앱 20261021093000"
        found = client.get(f"{API}/admin/support/lookup", headers=admin, params={"q": line})
        data = found.json()["data"]
        assert data["code"] == member_code and data["personaCode"] == support.persona_code(pid)
        assert [u["id"] for u in data["users"]] == [member_id]
        assert data["users"][0]["personas"] == [
            {
                "id": pid,
                "code": support.persona_code(pid),
                "title": "어머님",
                "role": "member",
                "step": "UPLOAD",
                "matched": True,
            }
        ]

        # 코드 모양이 아니면 400, 맞는 회원이 없으면 빈 목록
        bad = client.get(f"{API}/admin/support/lookup", headers=admin, params={"q": "안녕하세요"})
        assert bad.status_code == 400

        async def taken():
            async with SessionLocal() as db:
                return {support.inquiry_code(i) for i in (await db.scalars(select(User.id))).all()}

        codes = asyncio.run(taken())
        free = next(
            c
            for c in (f"{n:04d}-ZZZZ" for n in range(10000))
            if c not in codes and support.normalize(c) == c
        )
        none = client.get(f"{API}/admin/support/lookup", headers=admin, params={"q": free})
        assert none.status_code == 200 and none.json()["data"]["users"] == []


def test_lookup_hides_imported_persona_names():
    """가져온 대화 상대(관리자가 넣은 것)의 성함도 문의 코드 찾기에 나오지 않는다."""
    with TestClient(app) as client:
        owner, owner_id = _signup(client)

        async def imported() -> str:
            async with SessionLocal() as db:
                person = Person(
                    owner_user_id=owner_id,
                    name="문옥순가져옴",
                    relationship_label="할머니",
                    address_term="할머니",
                )
                db.add(person)
                await db.flush()
                persona = Persona(
                    person_id=person.id,
                    name="문옥순가져옴",
                    basics={"name": "문옥순가져옴", "addressTerm": "할머니"},
                    origin=ORIGIN_IMPORT,
                )
                db.add(persona)
                await db.commit()
                return persona.id

        pid = asyncio.run(imported())
        admin = _admin(client)
        code = support.inquiry_code(owner_id)
        found = client.get(f"{API}/admin/support/lookup", headers=admin, params={"q": code})
        assert found.status_code == 200, found.text
        user = found.json()["data"]["users"][0]
        assert "importedPersonas" not in user
        assert user["personaCount"] == 1
        assert [p["id"] for p in user["personas"]] == [pid]
        assert user["personas"][0]["title"] == "할머니"
        assert "문옥순가져옴" not in found.text
