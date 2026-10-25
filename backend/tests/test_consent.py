"""동의: 첫 로그인 게이트, 동의문, 대화 상대별 확인(녹음·남긴 글·민감 범주·결제·가족 공유), 더 받지
않는 동의, 회원 탈퇴, 나 자신 대화 상대."""

import asyncio
import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_billing import AGREE, API, FRONT, _bare_ready, _pay, _ready_persona
from test_persona_setup import (  # noqa: F401
    AGREEMENTS,
    ANSWERS,
    BASICS,
    FakeAI,
    _signup,
    _wait,
    _wav,
    fake_ai,
)

from app.core.config import get_settings
from app.db.models import (
    Consent,
    ConsentRecord,
    Payment,
    Persona,
    SocialAccount,
    User,
    UserStatus,
)
from app.db.session import SessionLocal
from app.main import app
from app.services import intake, legal, persona_builder
from app.services.storage import get_storage


@pytest.fixture(autouse=True)
def payments_open(monkeypatch):
    """이 파일은 결제가 열린 뒤의 흐름(체험·결제)을 본다. 결제 전 모드는 test_prelaunch.py."""
    monkeypatch.setattr(get_settings(), "payments_open", True)


def _db(fn):
    async def go():
        async with SessionLocal() as db:
            return await fn(db)

    return asyncio.run(go())


def _user(email: str) -> User | None:
    return _db(lambda db: db.scalar(select(User).where(User.email == email)))


def _records(user_id: str, consent_type: str | None = None) -> list[ConsentRecord]:
    async def go(db):
        query = select(ConsentRecord).where(ConsentRecord.user_id == user_id)
        if consent_type:
            query = query.where(ConsentRecord.consent_type == consent_type)
        return (await db.scalars(query.order_by(ConsentRecord.created_at))).all()

    return _db(go)


def _bare_signup(client: TestClient, email: str, agreements=None) -> dict:
    body = {"email": email, "password": "password123", "name": "홍길동"}
    if agreements is not None:
        body["agreements"] = agreements
    response = client.post(f"{API}/auth/signup", json=body)
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['data']['bearerToken']}"}


def _detail(client: TestClient, h: dict, pid: str) -> dict:
    response = client.get(f"{API}/personas/{pid}", headers=h)
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ────────────────────────── 계정 필수 동의 ──────────────────────────


def test_gate_until_account_consents_are_accepted():
    with TestClient(app) as client:
        h = _bare_signup(client, "gate@example.com")
        blocked = client.get(f"{API}/personas", headers=h)
        assert blocked.status_code == 403
        # 아홉 가지 모두 가입할 때 받는다(대화 상대를 만들기 전에 따로 받는 단계가 없다)
        assert blocked.json() == {
            "detail": "서비스 이용 동의가 필요해요.",
            "code": "consent_required",
            "missing": legal.ACCOUNT_TYPES,
        }

        # /auth/me 는 동의 전에도 열리고, 남은 동의를 알려 준다
        me = client.get(f"{API}/auth/me", headers=h)
        assert me.status_code == 200
        assert me.json()["data"]["consentsNeeded"] == legal.ACCOUNT_TYPES
        assert "creatorConsentsNeeded" not in me.json()["data"]
        assert client.get(f"{API}/consents/me", headers=h).json()["data"] == []

        # 일부만 받으면 아직 막힌다. 모르는 종류는 무시한다
        partial = client.post(
            f"{API}/consents/accept", headers=h, json={"types": ["age14", "terms", "nope"]}
        )
        assert "age14" not in partial.json()["data"]["consentsNeeded"]
        assert client.get(f"{API}/personas", headers=h).status_code == 403
        done = client.post(f"{API}/consents/accept", headers=h, json={"types": AGREEMENTS})
        assert done.json()["data"] == {"consentsNeeded": []}
        assert client.get(f"{API}/personas", headers=h).status_code == 200
        assert client.get(f"{API}/auth/me", headers=h).json()["data"]["consentsNeeded"] == []

        rows = client.get(f"{API}/consents/me", headers=h).json()["data"]
        assert [r["type"] for r in rows] == legal.ACCOUNT_TYPES
        assert rows[2]["title"] == "이용약관에 동의해요"
        assert rows[2]["version"] == legal.version_of("terms")
        assert rows[0]["personaId"] is None and rows[0]["agreedAt"]

        # 필수 동의는 거둘 수 없다(회원 탈퇴로)
        refused = client.post(
            f"{API}/consents",
            headers=h,
            json={"consentType": "terms", "agreed": False, "version": legal.version_of("terms")},
        )
        assert refused.status_code == 400 and "회원 탈퇴" in refused.json()["detail"]
        # (예전 API) 하나씩 받는 곳도 남은 필수 동의를 싣는다
        saved = client.post(
            f"{API}/consents",
            headers=h,
            json={"consentType": "voice_data", "agreed": True, "version": "1.0"},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["consentType"] == "voice_data"
        assert saved.json()["data"]["consentsNeeded"] == []
        assert "creatorConsentsNeeded" not in saved.json()["data"]
        # 로그아웃도 동의 전에 열린다(AuthedUser)
        h2 = _bare_signup(client, "gate-logout@example.com")
        assert client.post(f"{API}/auth/logout", headers=h2).status_code == 200


def test_all_account_consents_are_taken_at_signup():
    # 아홉 가지 모두 가입할 때(첫 로그인 게이트) 받는다. 받는 때를 가르는 칸(stage)은 없다.
    # 표 순서 그대로
    assert legal.ACCOUNT_TYPES == [
        "age14",
        "service",
        "terms",
        "privacy",
        "overseas",
        "overseas_fish",
        "voice_data",
        "voice_synthesis",
        "overseas_elevenlabs",
    ]
    assert [i["type"] for i in legal.account_items()] == legal.ACCOUNT_TYPES
    assert all(set(i) == {"type", "label", "doc", "kind"} for i in legal.account_items())
    for name in ("SIGNUP_TYPES", "CREATOR_TYPES", "missing_creator", "missing_owner"):
        assert not hasattr(legal, name), name
    # 체크 문구(화면에 그대로 보인다)
    assert {i["type"]: i["label"] for i in legal.account_items()} == {
        "age14": "만 14세 이상이에요",
        "service": "TalkTo의 대화 상대는 AI가 만든 것이라는 점을 이해했어요",
        "terms": "이용약관에 동의해요",
        "privacy": "개인정보 수집·이용 안내를 확인했어요",
        "overseas": (
            "해외 회사에 맡기는 처리(대답을 만드는 OpenAI)와 서버를 빌려 쓰는 곳(AWS 서울)을 "
            "확인했어요"
        ),
        "overseas_fish": (
            "대답을 그분 목소리로 읽어 주고 그분 목소리를 만드는 Fish Audio(미국)로 보내는 데 "
            "동의해요"
        ),
        "voice_data": "녹음 파일 처리에 동의해요",
        "voice_synthesis": "그분 목소리 만들기에 동의해요",
        "overseas_elevenlabs": (
            "녹음과 말로 적은 답의 받아쓰기, 그리고 예전에 만든 목소리를 다시 만들기 전까지 "
            "대답 읽기를 하는 ElevenLabs(미국)로 보내는 데 동의해요"
        ),
    }


def test_every_account_is_gated_on_all_nine():
    # 초대받은 가족처럼 대화 상대를 만들지 않는 계정도 아홉 가지 모두 받아야 앱을 쓴다
    # (예전 0.7·0.8 의 가입 단계 여섯 가지만 받은 계정은 남은 셋을 첫 화면에서 받는다)
    former_signup = ["age14", "service", "terms", "privacy", "overseas", "overseas_fish"]
    rest = ["voice_data", "voice_synthesis", "overseas_elevenlabs"]
    with TestClient(app) as client:
        h = _bare_signup(client, "six-only@example.com", former_signup)
        blocked = client.get(f"{API}/personas", headers=h)
        assert blocked.status_code == 403
        assert blocked.json()["missing"] == rest and "stage" not in blocked.json()
        me = client.get(f"{API}/auth/me", headers=h).json()["data"]
        assert me["consentsNeeded"] == rest and "creatorConsentsNeeded" not in me
        user = _user("six-only@example.com")
        assert _db(lambda db: legal.missing_required(db, user)) == rest
        done = client.post(f"{API}/consents/accept", headers=h, json={"types": rest})
        assert done.json()["data"] == {"consentsNeeded": []}
        assert client.get(f"{API}/personas", headers=h).status_code == 200


def test_creating_a_persona_has_no_separate_consent_stage(fake_ai: FakeAI):  # noqa: F811
    # 가입 때 아홉 가지를 받았으면 대화 상대를 만들 때 더 묻지 않는다
    with TestClient(app) as client:
        h = _bare_signup(client, "create-direct@example.com", AGREEMENTS)
        created = client.post(f"{API}/personas", headers=h, json=BASICS)
        assert created.status_code == 201, created.text
        user = _user("create-direct@example.com")
        assert {r.consent_type for r in _records(user.id)} == set(legal.ACCOUNT_TYPES)
        # 하나라도 빠지면 만들기도 다른 요청처럼 게이트에서 막힌다(따로 쓰는 모양이 없다)
        h2 = _bare_signup(
            client, "create-missing@example.com", [t for t in AGREEMENTS if t != "voice_data"]
        )
        blocked = client.post(f"{API}/personas", headers=h2, json=BASICS)
        assert blocked.status_code == 403
        assert blocked.json() == {
            "detail": "서비스 이용 동의가 필요해요.",
            "code": "consent_required",
            "missing": ["voice_data"],
        }


def test_a_changed_voice_document_regates_owners_and_members_alike(
    fake_ai: FakeAI,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    # 녹음 파일 처리 글이 바뀌면 대화 상대를 가졌는지와 상관없이 모두 다시 받는다
    with TestClient(app) as client:
        owner = _signup(client, "voice-doc-owner@example.com")
        assert client.post(f"{API}/personas", headers=owner, json=BASICS).status_code == 201
        member = _bare_signup(client, "voice-doc-member@example.com", AGREEMENTS)
        bumped = dict(legal.docs())
        bumped["voice_data"] = replace(bumped["voice_data"], version="9.9")
        monkeypatch.setattr(legal, "docs", lambda: bumped)

        for h in (owner, member):
            blocked = client.get(f"{API}/personas", headers=h)
            assert blocked.status_code == 403
            assert blocked.json()["missing"] == ["voice_data"] and "stage" not in blocked.json()
            me = client.get(f"{API}/auth/me", headers=h).json()["data"]
            assert me["consentsNeeded"] == ["voice_data"]
            accepted = client.post(
                f"{API}/consents/accept", headers=h, json={"types": ["voice_data"]}
            )
            assert accepted.json()["data"] == {"consentsNeeded": []}
            assert client.get(f"{API}/personas", headers=h).status_code == 200


def test_version_bump_regates(monkeypatch: pytest.MonkeyPatch):
    with TestClient(app) as client:
        h = _signup(client, "bump@example.com")
        assert client.get(f"{API}/personas", headers=h).status_code == 200
        bumped = dict(legal.docs())
        bumped["terms"] = replace(bumped["terms"], version="9.9")
        monkeypatch.setattr(legal, "docs", lambda: bumped)
        blocked = client.get(f"{API}/personas", headers=h)
        assert blocked.status_code == 403 and blocked.json()["missing"] == ["terms"]
        client.post(f"{API}/consents/accept", headers=h, json={"types": ["terms"]})
        assert client.get(f"{API}/personas", headers=h).status_code == 200


def test_signup_records_agreements():
    with TestClient(app) as client:
        h = _bare_signup(client, "signup-agree@example.com", ["age14", "terms", "zzz"])
        user = _user("signup-agree@example.com")
        records = _records(user.id)
        assert {r.consent_type for r in records} == {"age14", "terms"}
        terms = next(r for r in records if r.consent_type == "terms")
        assert terms.doc == "terms" and terms.version == legal.version_of("terms") and terms.agreed
        assert terms.text_sha256 == legal.text_sha256("terms") and len(terms.text_sha256) == 64
        age = next(r for r in records if r.consent_type == "age14")
        assert age.doc is None and age.text_sha256 is None and age.channel == "web"
        needed = client.get(f"{API}/auth/me", headers=h).json()["data"]["consentsNeeded"]
        assert "terms" not in needed and "privacy" in needed


def test_admin_is_not_gated():
    settings = get_settings()
    with TestClient(app) as client:
        login = client.post(
            f"{API}/auth/login",
            json={
                "email": settings.bootstrap_admin_email,
                "password": settings.bootstrap_admin_password,
            },
        )
        h = {"Authorization": f"Bearer {login.json()['data']['bearerToken']}"}
        assert client.get(f"{API}/personas", headers=h).status_code == 200
        assert client.get(f"{API}/admin/users", headers=h).status_code == 200
        me = client.get(f"{API}/auth/me", headers=h).json()["data"]
        assert me["consentsNeeded"] == [] and "creatorConsentsNeeded" not in me


def test_account_consents_name_where_recordings_go():
    # 해외 회사로 보내는 동의는 받는 회사를 적는다(개인정보 보호법 제28조의8 제2항 제3호)
    items = {item["type"]: item for item in legal.account_items()}
    assert items["overseas_fish"] == {
        "type": "overseas_fish",
        "label": (
            "대답을 그분 목소리로 읽어 주고 그분 목소리를 만드는 Fish Audio(미국)로 보내는 데 "
            "동의해요"
        ),
        "doc": "overseas",
        "kind": "consent",
    }
    # ElevenLabs 는 받아쓰기(올린 녹음과 답 칸의 말로 적기 — 0.9)와, 다시 만들기 전의 예전 목소리만
    eleven = items["overseas_elevenlabs"]
    assert eleven["doc"] == "overseas" and eleven["kind"] == "consent"
    assert "받아쓰기" in eleven["label"] and "ElevenLabs(미국)" in eleven["label"]
    assert "말로 적은 답" in eleven["label"]
    assert "예전에 만든 목소리를 다시 만들기 전까지" in eleven["label"]
    # MiniMax 는 더 받지 않는다(예전 목소리는 예전에 받은 동의로 읽는다)
    assert "overseas_minimax" not in legal.ACCOUNT_TYPES
    assert all("MiniMax" not in item["label"] for item in items.values())


def test_retired_consents_keep_their_titles():
    # 게이트에서 뺀 MiniMax 동의는 다시 묻지 않고, 새 Fish Audio 동의만 받는다.
    # 예전 줄은 지우지 않고 내 동의 내역에 제 이름으로 남는다
    with TestClient(app) as client:
        h = _bare_signup(client, "retired@example.com")
        user = _user("retired@example.com")

        async def old_rows(db):
            now = datetime.now(UTC)
            for consent_type in [t for t in legal.ACCOUNT_TYPES if t != "overseas_fish"]:
                version = legal.current_version(consent_type)
                db.add(
                    Consent(
                        user_id=user.id,
                        consent_type=consent_type,
                        agreed=True,
                        version=version,
                        agreed_at=now,
                    )
                )
            for consent_type in ("overseas_minimax", "pvc_transfer:old-persona"):
                db.add(
                    Consent(
                        user_id=user.id,
                        consent_type=consent_type,
                        agreed=True,
                        version="0.4",
                        agreed_at=now,
                    )
                )
            await db.commit()

        _db(old_rows)
        me = client.get(f"{API}/auth/me", headers=h).json()["data"]
        assert me["consentsNeeded"] == ["overseas_fish"]
        assert client.get(f"{API}/personas", headers=h).status_code == 403
        done = client.post(f"{API}/consents/accept", headers=h, json={"types": ["overseas_fish"]})
        assert done.json()["data"]["consentsNeeded"] == []
        assert client.get(f"{API}/personas", headers=h).status_code == 200

        rows = client.get(f"{API}/consents/me", headers=h).json()["data"]
        titles = {r["type"]: r["title"] for r in rows}
        assert [r["type"] for r in rows][: len(legal.ACCOUNT_TYPES)] == legal.ACCOUNT_TYPES
        assert titles["overseas_minimax"] == legal.RETIRED_TITLES["overseas_minimax"]
        assert "MiniMax" in titles["overseas_minimax"]
        assert titles["pvc_transfer:old-persona"] == legal.RETIRED_TITLES["pvc_transfer"]
        assert "똑 닮은 목소리" in titles["pvc_transfer:old-persona"]
    # 기록만 남은 상담 연락처 동의도 이름으로 보인다
    contact = legal.title_of("contact_interview:old-persona")
    assert contact == legal.RETIRED_TITLES["contact_interview"]
    assert legal.title_of("regular_price") == "원래 가격 동의"


# ────────────────────────── 동의문 ──────────────────────────


# 서버가 채우지 않고 화면이 바꾸는 자리표시(호칭, 그리고 동의 녹음 대본의 확인 단어·보호자 — 0.10)
SCREEN_SLOTS = ("{{호칭}}", "{{확인 단어}}", "{{보호자}}")


def _screen_slots_removed(text: str) -> str:
    for slot in SCREEN_SLOTS:
        text = text.replace(slot, "")
    return text


def test_every_document_fills_its_slots():
    # 서버가 아는 자리표시는 모두 채운다. {{호칭}}(그리고 대본의 {{확인 단어}}·{{보호자}})만 화면이
    # 바꾼다
    for key in legal.docs():
        body = legal.body(key)
        assert body is not None and "{{" not in _screen_slots_removed(body), key
    for item in legal.account_items():
        assert "{{" not in item["label"], item["type"]
    # 쓰지 않는 자리표시를 남기지 않는다(요금제를 없애면 그 가격 자리표시도 함께 뺀다)
    raw = "".join(d.body for d in legal.docs().values())
    raw += "".join(item["label"] for item in legal.ACCOUNT_REQUIRED)
    assert [k for k in legal.placeholders() if "{{" + k + "}}" not in raw] == []
    # 개인정보 처리방침·환불 정책·바뀐 점은 공개하는 문서다(계정 필수 동의에 넣지 않는다)
    for key in ("privacy_policy", "refund", "changes"):
        assert legal.doc(key).kind == "notice"
        assert key not in {item["doc"] for item in legal.ACCOUNT_REQUIRED}


def test_documents_state_their_own_version():
    # 부칙·적용일 줄과 바뀐 점이 앞머리 버전과 어긋나지 않게. 0.10 부터는 '0.9 다음 판'을 덧붙인다
    terms = legal.version_of("terms")
    assert re.search(rf"이 약관\(버전 {re.escape(terms)}[,)]", legal.body("terms"))
    policy = legal.version_of("privacy_policy")
    assert re.search(rf"이 처리방침\(버전 {re.escape(policy)}[,)]", legal.body("privacy_policy"))
    newest = max(
        (legal.version_of(item["doc"]) for item in legal.ACCOUNT_REQUIRED if item["doc"]),
        key=lambda v: tuple(int(n) for n in v.split(".")),
    )
    assert f"(버전 {newest})" in legal.body("changes")


def test_no_document_offers_the_removed_premium_plan():
    # 똑 닮은 목소리 요금제는 없앴다. 바뀐 점(이력)에만 남는다. 돌아가신 분의 유족 확인(0.10)은
    # 사망진단서 같은 서류를 받지 않는다고만 적는다
    for key in legal.docs():
        if key == "changes":
            continue
        body = legal.body(key).replace("사망진단서 같은 서류는 받지 않아요", "")
        for word in ("똑 닮은", "PVC", "사망진단서", "상담 연락처", "전화 인터뷰"):
            assert word not in body, (key, word)
    assert "똑 닮은 목소리 요금제" in legal.body("changes")


def test_retired_documents_are_kept_but_not_served():
    # 더 받지 않는 문서는 글을 그대로 retired/ 에 둔다(예전 동의 기록의 증빙)
    with TestClient(app) as client:
        keys = {d["key"] for d in client.get(f"{API}/legal").json()["data"]["docs"]}
        for key, title in (
            ("pvc_transfer", "똑 닮은 목소리 — 서류와 녹음 보내기 동의"),
            ("contact_interview", "똑 닮은 목소리 상담 연락처 수집·이용 동의"),
        ):
            assert legal.doc(key) is None and key not in keys
            assert client.get(f"{API}/legal/{key}").status_code == 404
            path = legal.RETIRED_DIR / f"{key}.md"
            meta, body = legal._parse(path.read_text(encoding="utf-8"))
            assert meta["key"] == key and meta["title"] == title and body.strip()
            assert key in legal.RETIRED_TITLES


def test_consent_documents_leave_vendor_names_to_the_overseas_page():
    # 목소리를 맡는 회사가 바뀌어도 '해외 회사로 보내는 정보'와 처리방침만 고치면 되게
    for key in ("voice_data", "voice_synthesis", "sensitive_health", "sensitive_religion"):
        body = legal.body(key)
        for vendor in ("ElevenLabs", "MiniMax", "Fish"):
            assert vendor not in body, (key, vendor)
        assert "해외 회사로 보내는 정보" in body, key
    # 가족 공유 안내는 대답을 만드는 회사 이름도 적지 않는다(가족도 가입할 때 같은 곳에 동의했다)
    share = legal.body("family_share")
    for vendor in ("ElevenLabs", "MiniMax", "Fish", "OpenAI", "AWS", "토스"):
        assert vendor not in share, vendor
    assert "해외 회사로 보내는 정보" in share
    # 목소리를 만들 때 쓰는 녹음 분량
    assert "(보통 1분 안팎, 적어도 10초)" in legal.body("voice_synthesis")


def _between(text: str, start: str, stop: str) -> str:
    begin = text.index(start)
    end = text.find(stop, begin + len(start))
    return text[begin : end if end != -1 else len(text)]


def test_fish_audio_is_disclosed_with_its_default_terms():
    # Fish Audio 와 따로 맺은 약속이 없는 동안은 기본 약관대로 적는다(개선·학습에 쓸 수 있고,
    # 목소리를 지워도 만든 음성과 백업은 남을 수 있다)
    overseas = legal.body("overseas")
    fish = _between(overseas, "### Fish Audio (미국)", "\n## ")
    for text in (
        "Hanabi AI, Inc.(Fish Audio)",
        "support@fish.audio",
        "그분 목소리로 고른 녹음 부분",
        "대답할 글, 만든 목소리의 아이디",
        "목소리를 만들거나 다시 만들 때",
        "미국",
        "일본(도쿄)",
        "비공개",
        "이미 만든 음성",
        "백업",
        "나아지게 하는 데 쓸 수 있",
        "AI 모델",
        "동의하지 않으면",
    ):
        assert text in fish, text
    # 예전 목소리를 읽는 MiniMax 는 바꾸는 동안 계속 알린다
    old = _between(overseas, "## 3. 예전에 만든 목소리", "\n이 동의를 거두시려면")
    assert "Nanonoble Pte. Ltd.(MiniMax)" in old and "api@minimax.io" in old

    # 처리방침의 국외 이전: 항목, 나라·때·방법, 받는 곳과 연락처, 목적, 보관, 거부 방법과 결과
    policy = legal.body("privacy_policy")
    transfer = _between(policy, "## 5. 해외로 옮기는 개인정보", "\n## 6.")
    assert "인터넷으로 암호화(HTTPS)" in transfer
    row = _between(transfer, "- **Hanabi AI, Inc.(Fish Audio)**", "\n- **")
    for text in (
        "미국 회사",
        "일본(도쿄)",
        "support@fish.audio",
        "보내는 것:",
        "언제:",
        "목적:",
        "보관:",
        "백업",
    ):
        assert text in row, text
    assert "Nanonoble Pte. Ltd.(MiniMax)" in transfer
    assert "ElevenLabs·Fish Audio 동의" in transfer
    assert "**Fish Audio**" in _between(policy, "## 4. 처리를 맡기는 곳", "\n## 5.")


def test_refund_policy_says_what_the_terms_say():
    # 환불 정책은 이용약관 5번을 모은 것이다. 한쪽만 고치면 이 시험이 깨진다
    terms = legal.body("terms").replace("**", "")
    refund = legal.body("refund").replace("**", "")
    for rule in (
        "7일 안에 그 대화 상대와 한 번도 대화하지 않으셨다면",
        # 0.10(법률 자문): 맞춤 콘텐츠라는 까닭 없이 '결제하면 바로 유료 이용 시작 + 단순 변심 제한'
        (
            "- 결제하시면 바로 유료 이용이 시작돼요. 결제한 뒤 그 대화 상대와 대화를 시작하면"
            "(초대한 가족이 시작해도 같아요) 단순 변심에 의한 결제 취소(청약철회)가 제한돼요"
            "(전자상거래법 제17조 제2항 제5호). 결제하기 전에 따로 한 번 더 확인을 받아요."
        ),
        (
            "- 무료 체험을 쓰지 않은 대화 상대는 먼저 써 보실 수 없었으니, 그 대화 상대를 처음 "
            "결제한 날부터 7일 안에 말씀하시면 대화하셨더라도 전액 돌려드려요."
        ),
        (
            "- 표시·광고나 안내한 내용, 계약 내용과 다르게 드렸을 때(예: 전혀 다른 사람 목소리로 "
            "만들어졌거나, 요금제에 적힌 것을 드리지 못한 경우)는 전액 돌려드려요. 받으신 날부터 "
            "3개월 안에, 그 사실을 아셨거나 아실 수 있었던 날부터 30일 안에 요청하실 수 있어요"
            "(전자상거래법 제17조 제3항)."
        ),
        "한 번 결제한 이용 기간은 결제일부터 다음 달 같은 날의 전날까지예요.",
        "이메일({{문의 이메일}}), 전화({{전화}}), 앱의 '고객센터'(카카오톡 채널)로 알려 주세요.",
        "남은 날만큼 날짜로 나눠 돌려드려요",
        # 잘못 없이 멈출 때도(대화 상대는 남은 날만큼, 가족 공유만이면 요금제 차액)
        "대화 상대를 멈추거나 지우게 되면",
        "가족 공유만 멈추게 되면, 남은 날만큼 가족 요금제와 추억 요금제의 차액을",
        "이미 결제한 이용 기간의 요금을 날짜로 나눠 돌려드리지 않아요",
        "해지해도 이미 결제한 기간이 끝날 때까지는 쓸 수 있고",
        "해지하거나 무료 체험이 끝나도 만든 대화 상대와 녹음·대화 기록은 바로 지우지 않고",
        "회원님이 대화 상대를 지우시면 그 대화 상대의 구독도 바로 끝나고, 남은 기간은 쓸 수 없어요",
    ):
        rule = legal.fill(rule)
        assert rule in terms and rule in refund, rule


def test_consent_documents_say_you_may_refuse():
    # 동의마다 거부할 권리가 있다는 것과 거부했을 때의 결과를 함께 적는다
    # (개인정보 보호법 제15조 제2항 제4호·제17조 제2항 제5호)
    right = "- 이 동의는 하지 않으실 수 있어요(거부할 권리가 있어요)."
    for key in ("voice_data", "voice_synthesis"):
        refuse = _between(legal.body(key), "## 동의하지 않으면", "\n## ")
        assert refuse.splitlines()[1] == right, key
    # 해외 회사로 보내는 동의는 Fish Audio·ElevenLabs 두 가지
    assert legal.body("overseas").count(f"{right}\n- 동의하지 않으면:") == 2
    share = _between(legal.body("family_share"), "## 4. 서로 보게 되는 개인정보", "\n## 5.")
    assert share.count(f"{right}\n- 동의하지 않으시면:") == 2


def test_refund_policy_beta_notice_follows_payments(monkeypatch: pytest.MonkeyPatch):
    # 결제가 열리기 전에만 '지금은 결제를 받지 않아요'가 보이고, 열리면 저절로 빠진다
    for opened, shown in ((False, True), (True, False)):
        s = get_settings().model_copy(update={"payments_open": opened})
        monkeypatch.setattr(legal, "get_settings", lambda s=s: s)
        assert ("지금은 결제를 받지 않아요" in legal.body("refund")) is shown


def test_prelaunch_limits_come_from_settings(monkeypatch: pytest.MonkeyPatch):
    limits = get_settings().model_copy(
        update={"prelaunch_personas_per_account": 3, "prelaunch_conversations": 200}
    )
    monkeypatch.setattr(legal, "get_settings", lambda: limits)
    terms = legal.body("terms")
    assert "계정 하나에 대화 상대 3명까지" in terms and "30일마다 대화 200번" in terms


def test_share_limits_in_documents_follow_settings(monkeypatch: pytest.MonkeyPatch):
    # 가족 공유의 초대 가족 수와 초대 링크가 열려 있는 때는 설정 값으로 채운다(문서에 숫자를 박지
    # 않는다). 날로 나눠떨어지지 않으면 시간으로 적는다
    for hours, period in ((168, "7일"), (48, "2일"), (36, "36시간")):
        s = get_settings().model_copy(
            update={"share_members_per_persona": 3, "share_link_hours": hours}
        )
        monkeypatch.setattr(legal, "get_settings", lambda s=s: s)
        assert legal.placeholders()["share_members"] == "3"
        assert legal.placeholders()["share_link_days"] == period
        for key in ("terms", "family_share"):
            body = legal.body(key).replace("**", "")
            assert "가족을 3명까지" in body and f"한 사람만, {period} 안에" in body, key
        assert f"가족 수(3명)와 초대 링크(한 사람, {period})" in legal.body("changes")
    for key in ("terms", "family_share"):
        raw = legal.doc(key).body
        assert "5명까지" not in raw and "7일 안에 들어올" not in raw, key


def test_legal_documents_fill_placeholders(monkeypatch: pytest.MonkeyPatch):
    # 로컬 .env 값과 상관없이 정해 둔다
    blank = {
        key: None
        for key in type(get_settings()).model_fields
        if key.startswith(("business_", "legal_")) or key == "mail_order_no"
    }
    filled = get_settings().model_copy(update=blank | {"business_name": "토크투 주식회사"})
    monkeypatch.setattr(legal, "get_settings", lambda: filled)
    with TestClient(app) as client:
        index = client.get(f"{API}/legal").json()["data"]
        keys = {d["key"] for d in index["docs"]}
        assert {"terms", "privacy", "payment", "pledge_deceased", "pledge_self"} <= keys
        assert [a["type"] for a in index["account"]] == legal.ACCOUNT_TYPES
        assert all("stage" not in a for a in index["account"])
        fish = next(a for a in index["account"] if a["type"] == "overseas_fish")
        assert "Fish Audio(미국)" in fish["label"]
        assert index["account"][0] == {
            "type": "age14",
            "label": "만 14세 이상이에요",
            "doc": None,
            "kind": "confirm",
        }
        assert index["business"]["name"] == "토크투 주식회사"
        assert index["business"]["owner"] is None
        assert index["business"]["hosting"] == "Amazon Web Services(AWS)"

        terms = client.get(f"{API}/legal/terms").json()["data"]
        assert terms["title"] == "이용약관" and terms["version"] == legal.version_of("terms")
        assert terms["kind"] == "consent"
        assert "토크투 주식회사(이하 'TalkTo')" in terms["body"]
        assert "추억 월 9,900원" in terms["body"] and "대표 (준비 중)" in terms["body"]
        assert "{{" not in terms["body"]
        assert "---" not in terms["body"].splitlines()[0]
        overseas = client.get(f"{API}/legal/overseas").json()["data"]["body"]
        assert "Hanabi AI, Inc.(Fish Audio)" in overseas and "확인 중" in overseas
        # {{호칭}} 은 화면이 바꾼다
        pledge = client.get(f"{API}/legal/pledge_living").json()["data"]["body"]
        assert "{{호칭}}" in pledge
        assert client.get(f"{API}/legal/pledge_self").status_code == 200
        assert client.get(f"{API}/legal/nope").status_code == 404


# ────────────────────────── 대화 상대별 ──────────────────────────


def test_recording_and_material_pledges(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "pledge@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        detail = _detail(client, h, pid)
        assert detail["consents"] == {
            "voice": False,
            "recordingPledge": False,
            "materialPledge": False,
            "sensitiveReligion": False,
            "sensitiveHealth": False,
        }
        assert detail["pledgeDoc"] == "pledge_deceased"
        url = f"{API}/personas/{pid}/recordings"
        files = [("files", ("통화.wav", _wav(), "audio/wav"))]
        refused = client.post(url, headers=h, files=files)
        assert refused.status_code == 400
        assert refused.json()["detail"] == "녹음을 올리기 전에 확인이 필요해요."
        ok = client.post(url, headers=h, files=files, data={"pledge": "true"})
        assert ok.status_code == 200, ok.text
        assert _detail(client, h, pid)["consents"]["recordingPledge"] is True
        # 한 번 확인했으면 다음부터는 묻지 않는다
        assert client.post(url, headers=h, files=files).status_code == 200
        user = _user("pledge@example.com")
        rows = _records(user.id, f"recording_pledge:{pid}")
        assert len(rows) == 1 and rows[0].persona_id == pid and rows[0].doc == "recording_pledge"

        murl = f"{API}/personas/{pid}/materials"
        refused = client.post(murl, headers=h, data={"kind": "sms", "text": "할머니: 밥 먹었니"})
        assert refused.status_code == 400
        assert refused.json()["detail"] == "남긴 글을 올리기 전에 확인이 필요해요."
        ok = client.post(
            murl, headers=h, data={"kind": "sms", "text": "할머니: 밥 먹었니", "pledge": "true"}
        )
        assert ok.status_code == 200, ok.text
        assert _detail(client, h, pid)["consents"]["materialPledge"] is True
        assert client.post(murl, headers=h, data={"kind": "sms", "text": "또"}).status_code == 200

        # 내 동의 내역에는 대화 상대별 줄이 호칭과 함께 보인다
        mine = client.get(f"{API}/consents/me", headers=h).json()["data"]
        row = next(r for r in mine if r["type"] == f"recording_pledge:{pid}")
        assert row["personaId"] == pid and row["personaTitle"] == "할머니"
        assert row["title"] == "녹음 올리기 전 확인"


def test_changed_upload_pledge_is_asked_again(
    fake_ai: FakeAI,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    # 올리기 전 확인의 글이 바뀌면(버전이 오르면) 다음에 올릴 때 다시 받는다
    with TestClient(app) as client:
        h = _signup(client, "pledge-again@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        url = f"{API}/personas/{pid}/recordings"
        files = [("files", ("통화.wav", _wav(), "audio/wav"))]
        assert client.post(url, headers=h, files=files, data={"pledge": "true"}).status_code == 200
        bumped = dict(legal.docs())
        bumped["recording_pledge"] = replace(bumped["recording_pledge"], version="9.9")
        monkeypatch.setattr(legal, "docs", lambda: bumped)
        assert _detail(client, h, pid)["consents"]["recordingPledge"] is False
        assert client.post(url, headers=h, files=files).status_code == 400
        assert client.post(url, headers=h, files=files, data={"pledge": "true"}).status_code == 200
        assert _detail(client, h, pid)["consents"]["recordingPledge"] is True


def test_sensitive_answers_need_consent(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "sensitive@example.com")
        pid = _ready_persona(client, h)
        detail = _detail(client, h, pid)
        # 만들 때 받은 목소리 확인은 그분 상태의 문서로 남는다
        assert detail["consents"]["voice"] is True and detail["voiceConsented"] is True
        user = _user("sensitive@example.com")
        assert _records(user.id, f"voice_clone:{pid}")[-1].doc == "pledge_deceased"

        meta = client.get(f"{API}/intake/meta").json()["data"]
        questions = meta["questions"] + meta["moreQuestions"]
        flagged = {q["id"]: q["sensitive"] for q in questions if q.get("sensitive")}
        assert flagged == {
            "M1-09": "religion",
            "M1-10": "religion",
            "M4-07": "religion",
            "T1-02": "health",
        }

        base = dict(detail["answers"])
        put = lambda answers: client.put(  # noqa: E731
            f"{API}/personas/{pid}", headers=h, json={"basics": BASICS, "answers": answers}
        )
        refused = put(base | {"M1-09": "불교"})
        assert refused.status_code == 400
        assert refused.json()["detail"] == "종교 이야기를 적으려면 먼저 동의해 주세요."
        health = put(base | {"T1-02": "노환으로 편안하게"})
        assert health.status_code == 400 and "건강 이야기를" in health.json()["detail"]

        agreed = client.post(
            f"{API}/personas/{pid}/consents",
            headers=h,
            json={"type": "sensitive_religion", "agreed": True},
        )
        assert agreed.status_code == 200, agreed.text
        assert agreed.json()["data"]["consents"]["sensitiveReligion"] is True
        assert agreed.json()["data"]["updating"] is False
        saved = put(base | {"M1-09": "불교", "M4-07": "죽으면 다 흙으로 간다고 하셨다"})
        assert saved.status_code == 200, saved.text
        _wait(client, h, pid, "UPDATE")
        answers = _detail(client, h, pid)["answers"]
        assert answers["M1-09"] == "불교" and "M4-07" in answers

        # 거두면 그 범주 답을 지우고 다시 익힌다
        withdrawn = client.post(
            f"{API}/personas/{pid}/consents",
            headers=h,
            json={"type": "sensitive_religion", "agreed": False},
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["data"]["updating"] is True
        assert withdrawn.json()["data"]["consents"]["sensitiveReligion"] is False
        _wait(client, h, pid, "UPDATE")
        answers = _detail(client, h, pid)["answers"]
        assert "M1-09" not in answers and "M4-07" not in answers
        assert answers["C-05"] == base["C-05"]
        assert put(answers | {"M1-10": "새벽 기도"}).status_code == 400
        assert [r.agreed for r in _records(user.id, f"sensitive_religion:{pid}")] == [True, False]
        bad = client.post(
            f"{API}/personas/{pid}/consents", headers=h, json={"type": "terms", "agreed": True}
        )
        assert bad.status_code == 422

        # 그분 상태를 바꾸면 확인 문서가 달라져 목소리 확인을 다시 받는다(살아 계신 분은 만 19세
        # 미만인지도 함께 답한다 — 0.10)
        alive = client.put(
            f"{API}/personas/{pid}",
            headers=h,
            json={"basics": BASICS | {"status": "alive", "minor": False}, "answers": answers},
        )
        assert alive.status_code == 200, alive.text
        _wait(client, h, pid, "UPDATE")
        detail = _detail(client, h, pid)
        assert detail["pledgeDoc"] == "pledge_living" and detail["consents"]["voice"] is False


def test_legacy_sensitive_answers_are_kept_on_resave(fake_ai: FakeAI):  # noqa: F811
    """이 기능 전에 동의 없이 저장된 민감 답은 같은 값으로 다시 보내도 막지 않는다.

    (정보 고치기 화면은 지금 답을 합쳐 다시 보낸다.) 값을 바꾸면 막는다."""
    with TestClient(app) as client:
        h = _signup(client, "legacy-sensitive@example.com")
        pid = _ready_persona(client, h)
        answers = dict(_detail(client, h, pid)["answers"]) | {"M1-09": "불교", "T1-02": "노환으로"}

        async def put_legacy(db):
            persona = await db.get(Persona, pid)
            persona.answers = answers
            await db.commit()

        _db(put_legacy)
        resave = client.put(
            f"{API}/personas/{pid}",
            headers=h,
            json={"basics": BASICS, "answers": answers | {"M3-01": "우리 강아지니?"}},
        )
        assert resave.status_code == 200, resave.text
        _wait(client, h, pid, "UPDATE")
        changed = client.put(
            f"{API}/personas/{pid}",
            headers=h,
            json={"basics": BASICS, "answers": answers | {"M1-09": "천주교"}},
        )
        assert changed.status_code == 400 and "종교" in changed.json()["detail"]


def test_checkout_needs_agreements(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "pay-agree@example.com")
        pid = _bare_ready(client, h)
        body = {"personaId": pid, "plan": "memories", "returnTo": FRONT}
        refused = client.post(f"{API}/billing/checkout", headers=h, json=body)
        assert refused.status_code == 400
        assert refused.json()["detail"] == "결제 전 확인 두 가지에 체크해 주세요."
        one = client.post(
            f"{API}/billing/checkout", headers=h, json=body | {"agreements": ["recurring"]}
        )
        assert one.status_code == 400
        ok = client.post(f"{API}/billing/checkout", headers=h, json=body | {"agreements": AGREE})
        assert ok.status_code == 200, ok.text
        user = _user("pay-agree@example.com")
        rows = _records(user.id)
        assert {r.consent_type for r in rows if r.persona_id == pid} == {
            f"recurring:{pid}",
            f"withdrawal_limit:{pid}",
        }
        assert all(r.doc == "payment" for r in rows if r.persona_id == pid)
        # 이력만 쌓는다(지금 상태 줄은 만들지 않는다)
        consents = _db(
            lambda db: db.scalars(select(Consent.consent_type).where(Consent.user_id == user.id))
        ).all()
        assert f"recurring:{pid}" not in consents


def test_regular_price_consent_is_recorded(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "regular@example.com")
        assert client.post(f"{API}/billing/regular-price-consent", headers=h).status_code == 200
        user = _user("regular@example.com")
        rows = _records(user.id, "regular_price")
        assert len(rows) == 1 and rows[0].doc == "payment"


# ────────────────────────── 가족 공유 ──────────────────────────

SHARE_THREE = ["family_share", "family_share_subject", "family_share_sensitive"]
_SLOTS = re.compile(r"\{\{([^{}]+)\}\}")


def test_family_share_document():
    # 주인과 들어오는 가족이 함께 읽는 대화 상대별 동의문. 계정 필수 동의에는 넣지 않는다
    item = legal.doc("family_share")
    assert (item.key, item.title, item.version, item.kind) == (
        "family_share",
        "가족 공유 안내와 동의",
        "0.10",
        "consent",
    )
    assert "family_share" not in {i["doc"] for i in legal.ACCOUNT_REQUIRED}
    # 자리표시는 서버가 아는 것과 {{호칭}} 뿐이고, {{호칭}} 은 주인이 읽는 2번에만 둔다
    # (가족 화면은 호칭을 넘기지 않아 '그분'으로 보인다)
    slots = set(_SLOTS.findall(item.body))
    assert "호칭" in slots and slots - {"호칭"} <= set(legal.placeholders())
    owner = _between(item.body, "## 2. 만든 분이 확인할 것", "\n## 3.")
    assert "{{호칭}}" not in item.body.replace(owner, "")

    body = legal.body("family_share")
    assert "{{" not in body.replace("{{호칭}}", "")
    # 계정 문서(이용약관·'TalkTo는 이런 서비스예요')가 가족 공유를 적어 예외 문장을 두지 않는다(0.7)
    assert "'TalkTo는 이런 서비스예요'에는" not in body and "이용약관" in body
    service = legal.body("service")
    assert "만든 계정에서만 대화할 수 있어요" not in service
    assert "가족 공유로 초대한 가족" in service and "'가족 공유 안내와 동의'" in service
    assert "## 2-1. 가족 공유" in legal.body("terms")
    # 한 대화 상대에 초대할 수 있는 가족 수와 링크가 열려 있는 때는 설정에서 채운다
    # (쓰지 않은 링크도 센다 — test_share_limits_in_documents_follow_settings)
    assert "가족을 {{share_members}}명까지" in item.body
    assert "한 사람만, {{share_link_days}} 안에" in item.body
    s = get_settings()
    assert f"가족을 {s.share_members_per_persona}명까지" in body
    # 초대받은 가족도 가입할 때 모든 분께 받는 확인과 동의만 쓴다(0.9 — 동의를 가입할 때 한 번에
    # 받으니 '가족은 녹음·목소리 동의를 받지 않는다'는 예전 문장은 없다)
    member_side = _between(body, "## 3. 초대받은 가족이 확인할 것", "\n## 4.")
    assert "가입할 때 모든 분께 받는 확인과 동의에 따른 것이라 따로 받지 않아요" in member_side
    assert "녹음 파일 처리" not in member_side and "동의를 받지 않아요" not in member_side
    # 가족이 고쳐 보낸 것과 '더 알려 주실 기억'의 답은 만든 분이 확인해야 반영되고, 그때 만든 분에게
    # 보인다. 가족 대화에서 나온 질문은 대화 내용 없이 질문만(D1·D2)
    for text in (
        "만든 분이 확인해 넣어야 반영돼요",
        "내 이름·관계와 함께 만든 분에게 보이고",
        "만든 분이 아직 확인하지 않은 것은 내가 나가면 지워요",
        "대화 내용은 빼고 '더 알려 주실 기억' 질문만",
        "'말로 적기'",
    ):
        assert text in member_side, text
    assert "알림(푸시)은 보내지 않아요" in body
    # 가족은 지금 목소리(Fish)로만 듣고, 예전 목소리는 다시 만들 때까지 글로 받는다(D2)
    assert "예전에 만든 목소리" in body
    for text in (
        "결제가 열린 뒤 무료 체험 기간 포함",
        "결제가 열리기 전 베타 기간에는 무료로 쓸 수 있고",
        "대화 기록은 대화한 사람만 볼 수 있어요.",
        "만든 분도 가족의 대화를 볼 수 없고",
        "누가 몇 번 이야기했는지는 보이지 않아요",
        "만 14세 미만",
        # 그분 상태마다 더 받는 확인(살아 계신 분은 그분 허락과 건강·종교 이야기를 따로)
        "반대하는 분이 없어요",
        "{{호칭}}께서 **직접 괜찮다고 하셨어요.**",
        "{{호칭}}께 **따로** 말씀드리고 허락을 받았어요",
        # 링크를 연 사람에게 보이는 것
        "'맞는지 확인'으로 보여요",
        "그분 성함은 보이지 않아요",
        # 다른 사람이 적은 내 정보의 출처와 고치기·지우기(제20조·제36조)
        "이 내용은 만든 분이 적은 거예요",
        "고치거나 지워 달라고 하실 수 있어요",
        "내 이메일과 대화 내용은 보이지 않아요",
    ):
        assert text in body, text

    # 서로에게 전하는 정보마다 받는 사람·목적·항목·기간·거부와 그 결과(제17조 제2항)
    both = _between(body, "## 4. 서로 보게 되는 개인정보", "\n## 5.")
    for word in ("받는 사람:", "목적:", "항목:", "기간:", "동의하지 않으시면:"):
        assert both.count(word) == 2, word
    member = both[both.index("**초대받은 가족의 동의") :]
    assert "계정 이름" in member and "말 높이" in member and "나가거나 내보내질 때까지" in member

    ending = _between(body, "## 5. 그만두기와 지우는 때", "\n## 6.")
    for text in (
        "'함께 쓰기 그만두기'",
        "들어올 때 적은 정보를 바로 지워요",
        "만든 분이 내보낼 때",
        "만든 분이 가족 공유를 그만둘 때",
        "지난 대화는 볼 수 있고, 바로 지우지 않아요",
        "대화 상대를 지우거나 만든 분이 탈퇴할 때",
        "가족이 탈퇴할 때",
        "쓰지 않고 기한이 지난 초대 링크는 하루 안에 지워요",
        "다시 채워지지 않아요",
    ):
        assert text in ending, text
    # 한 사람을 내보내도 다른 링크는 그대로다(쓰지 않은 링크까지 막는 것은 그만두기뿐)
    lines = ending.splitlines()
    removal = next(line for line in lines if "만든 분이 내보낼 때" in line)
    stop = next(line for line in lines if "만든 분이 가족 공유를 그만둘 때" in line)
    assert "초대 링크" not in removal
    assert "아직 쓰지 않은 초대 링크도 쓸 수 없게 돼요" in stop

    with TestClient(app) as client:
        keys = {d["key"] for d in client.get(f"{API}/legal").json()["data"]["docs"]}
        assert "family_share" in keys
        served = client.get(f"{API}/legal/family_share").json()["data"]
        assert served["title"] == "가족 공유 안내와 동의" and served["kind"] == "consent"
        assert "{{호칭}}" in served["body"] and "{{문의 이메일}}" not in served["body"]


def test_family_share_consent_types():
    # 문서는 하나, 종류는 넷. 모두 대화 상대별이라 계정 게이트에 들지 않고,
    # 글을 고쳐도(버전이 올라도) 다시 묻지 않는다(함께 쓰는 가족이 멈추지 않게)
    assert legal.SHARE_DOC == "family_share"
    assert legal.SHARE_MEMBER_TYPE == "family_share_member"
    assert legal.SHARE_PREFIXES == (*SHARE_THREE, "family_share_member")
    assert {p: legal.PERSONA_TYPES[p] for p in legal.SHARE_PREFIXES} == {
        "family_share": ("family_share", "가족 공유 켜기"),
        "family_share_subject": ("family_share", "가족 공유 — 그분 허락 확인"),
        "family_share_sensitive": ("family_share", "가족 공유 — 건강·종교 이야기 따로 허락 확인"),
        "family_share_member": ("family_share", "가족 공유로 함께 이야기하기"),
    }
    assert not set(legal.SHARE_PREFIXES) & (legal.REASK_ON_CHANGE | set(legal.ACCOUNT_TYPES))
    pid = str(uuid.uuid4())
    longest = Consent.__table__.c.consent_type.type.length
    for prefix in legal.SHARE_PREFIXES:
        kind = legal.persona_type(prefix, pid)
        assert legal.split_type(kind) == (prefix, pid)
        assert legal.default_doc(kind) == "family_share"
        assert legal.current_version(kind) == legal.version_of("family_share")
        assert legal.title_of(kind) == legal.PERSONA_TYPES[prefix][1]
        assert len(kind) <= longest, kind


def test_family_share_labels_by_subject():
    # 그분 상태(목소리 확인 문서와 같은 갈래)마다 주인이 받는 확인
    assert set(legal.SHARE_OWNER_LABELS) == set(persona_builder.PLEDGE_DOCS) | {"self"}
    assert legal.share_owner_types("deceased") == ["family_share"]
    assert legal.share_owner_types("self") == ["family_share"]
    assert legal.share_owner_types("alive") == SHARE_THREE
    assert legal.share_owner_types("unable") == SHARE_THREE
    # 모르는 상태는 가장 많이 받는 쪽으로
    assert legal.share_owner_types("nope") == SHARE_THREE

    labels = legal.SHARE_OWNER_LABELS
    # 돌아가신 분은 만들 때 적은 유족 명단의 가족이 기준이다(0.10 — 법률 자문)
    assert labels["deceased"] == {
        "family_share": (
            "가족 공유 안내를 읽었고, {{호칭}}의 대화 상대를 초대한 가족과 함께 쓰는 데 동의해요. "
            "만들 때 적은 유족 명단의 가족 가운데 반대하는 분이 없어요."
        )
    }
    living = {
        "family_share": (
            "가족 공유 안내를 읽었고, {{호칭}}의 대화 상대를 초대한 가족과 함께 쓰는 데 동의해요."
        ),
        "family_share_subject": (
            "{{호칭}}께 초대할 가족도 이 대화 상대와 이야기하게 된다고 직접 말씀드렸고, "
            "{{호칭}}께서 괜찮다고 하셨어요."
        ),
        "family_share_sensitive": (
            "건강·종교 이야기도 가족과의 대화에서 나올 수 있다는 것까지 "
            "{{호칭}}께 따로 허락을 받았어요."
        ),
    }
    assert labels["alive"] == living and labels["unable"] == living
    assert labels["self"] == {
        "family_share": (
            "가족 공유 안내를 읽었고, 제 목소리와 이야기로 만든 대화 상대와 "
            "제가 초대한 가족이 이야기하는 데 동의해요."
        )
    }
    assert legal.SHARE_MEMBER_LABEL == (
        "가족 공유 안내를 읽었고, 만든 분께 내 계정 이름과 내가 적은 이름·관계·부르는 말·말 높이, "
        "들어온 날이 보이는 데 동의해요."
    )
    # 자리표시는 {{호칭}}(주인의 호칭, 화면이 채운다)이나 서버가 아는 것만.
    # 들어오는 가족과 나 자신의 글에는 호칭을 쓰지 않는다
    known = set(legal.placeholders()) | {"호칭"}
    every = [text for by_type in labels.values() for text in by_type.values()]
    for text in [*every, legal.SHARE_MEMBER_LABEL]:
        assert set(_SLOTS.findall(text)) <= known, text
    assert "{{" not in legal.SHARE_MEMBER_LABEL and "{{" not in labels["self"]["family_share"]


def test_family_share_consent_is_kept_when_the_document_changes(monkeypatch: pytest.MonkeyPatch):
    # 가족 공유 동의는 그 대화 상대에만 남는다. 글이 바뀌어도(버전이 올라도) 다시 묻지 않고,
    # 계정 게이트도 그대로다
    with TestClient(app) as client:
        h = _signup(client, "share-member@example.com")
        user = _user("share-member@example.com")
        pid = str(uuid.uuid4())
        kind = legal.persona_type(legal.SHARE_MEMBER_TYPE, pid)

        async def agree(db):
            await legal.record(db, user, kind, True, persona_id=pid)
            await db.commit()
            return await legal.has(db, user.id, kind)

        assert _db(agree) is True
        [row] = _records(user.id, kind)
        assert row.persona_id == pid and row.agreed and row.doc == "family_share"
        assert row.version == legal.version_of("family_share")
        assert row.text_sha256 == legal.text_sha256("family_share")

        bumped = dict(legal.docs())
        bumped["family_share"] = replace(bumped["family_share"], version="9.9")
        monkeypatch.setattr(legal, "docs", lambda: bumped)
        assert _db(lambda db: legal.has(db, user.id, kind)) is True
        assert _db(lambda db: legal.missing_required(db, user)) == []
        assert client.get(f"{API}/personas", headers=h).status_code == 200
        mine = client.get(f"{API}/consents/me", headers=h).json()["data"]
        assert next(r for r in mine if r["type"] == kind)["title"] == "가족 공유로 함께 이야기하기"


def test_family_share_notices_do_not_regate():
    # 계정 게이트가 보는 문서는 이 여섯뿐이다(버전이 오르면 다시 받는다 —
    # test_version_bump_regates).
    # 가족 공유 안내·처리방침·바뀐 점·결제 전 확인·환불 정책은 고쳐도 계정 게이트를 다시 열지 않고,
    # 나 자신 확인은 문서 이름으로만 본다
    account = {item["doc"] for item in legal.ACCOUNT_REQUIRED if item["doc"]}
    assert account == {"overseas", "privacy", "service", "terms", "voice_data", "voice_synthesis"}
    notices = {"family_share", "privacy_policy", "changes", "payment", "refund", "pledge_self"}
    assert not notices & account
    assert not notices & legal.REASK_ON_CHANGE
    assert "voice_clone" not in legal.REASK_ON_CHANGE
    # 버전은 글을 고친 문서만 올린다(0.7. ElevenLabs 기록 지우기로 overseas·처리방침·바뀐 점은
    # 0.8. 2026-10-21 동의를 가입할 때 한 번에·새 기능으로 계정 문서 여섯과 처리방침·바뀐 점·
    # 가족 공유는 0.9 — 결제 전 확인·환불 정책·확인서는 글이 그대로라 올리지 않는다.
    # 2026-10-22 법률 자문으로 service 를 뺀 계정 문서 다섯과 처리방침·바뀐 점·가족 공유는 0.10,
    # 결제 전 확인·환불 정책은 0.8, 녹음 올리기 전 확인은 0.5 — 0.10 은 0.9 다음 판이다).
    # 계정 문서를 올리면 모든 회원이 다음에 들어올 때 다시
    # 동의하고, 올리기 전 확인(REASK_ON_CHANGE)을 올리면 모든 만든 분이 다음에 올릴 때 다시
    # 확인한다. 문서를 일부러 고쳐 버전을 올릴 때 이 줄도 함께 고친다
    assert {key: legal.version_of(key) for key in sorted(account)} == {
        "overseas": "0.10",
        "privacy": "0.10",
        "service": "0.9",
        "terms": "0.10",
        "voice_data": "0.10",
        "voice_synthesis": "0.10",
    }
    assert {
        key: legal.version_of(key)
        for key in (
            "privacy_policy",
            "changes",
            "family_share",
            "payment",
            "refund",
            "pledge_self",
            *sorted(legal.REASK_ON_CHANGE),
        )
    } == {
        "privacy_policy": "0.10",
        "changes": "0.10",
        "family_share": "0.10",
        "payment": "0.8",
        "refund": "0.8",
        "pledge_self": "0.4",
        "material_pledge": "0.4",
        "recording_pledge": "0.5",
    }

    policy = legal.body("privacy_policy")
    assert "대화 상대의 대답은 그 계정에서만 볼 수 있고" not in policy
    assert "'그분 목소리'에 적은 때까지" not in policy
    for section, texts in (
        ("## 1.", ["- 가족 공유: 초대 링크 만들기"]),
        (
            "## 2.",
            [
                "- **가족 공유**(계약 이행",
                "그 대화 상대를 만든 분에게 보여요('가족 공유 안내와 동의' · 제17조 제1항 제1호)",
                "쓰지 않고 기한이 지난 초대 링크는 하루 안에 지워요",
                "만든 분이 가족 공유를 그만둘 때에도 지워요",
                # 대답 음성은 대화 기록이다(초대받은 가족의 것은 그 가족의 대화 기록과 함께 지운다)
                "그분 목소리로 만든 대답 음성, 대답에 남긴 평가",
                "(대답 음성은 목소리를 다시 만들 때에도 지워요)",
                "초대받은 가족의 대화 기록(말로 보낸 음성 메시지와 대답 음성 포함)",
            ],
        ),
        (
            "## 3.",
            ["- **가족 공유**: 회원님이 가족 공유를 켜시면", "서로의 대화 기록은 보이지 않아요"],
        ),
        ("## 5.", ["가족 공유로 초대받은 분이 대화할 때도", "MiniMax로는 보내지 않아요"]),
        ("## 6.", ["가족 공유에서 나가거나 내보내지거나"]),
        (
            "## 7.",
            [
                "대화 기록은 대화한 계정에서만 볼 수 있고",
                "대화 상대를 만든 분도 볼 수 없어요",
                "그분 본인께 따로 말씀드리고 허락받으셔야 해요",
            ],
        ),
        ("## 9.", ["'함께 쓰기 그만두기'", "어디서 왔는지 알려 드리고"]),
        (
            "## 11.",
            [
                "초대 열쇠와 초대한 분 이름(저장한 때와 함께)을 브라우저에 잠시 저장해요",
                "하루가 지난 것은 쓰지 않고, 다음에 TalkTo를 열 때 지워요",
            ],
        ),
        ("## 14.", [f"이 처리방침(버전 {legal.version_of('privacy_policy')}, ", "예전 판(버전"]),
    ):
        part = _between(policy, section, "\n## ")
        for text in texts:
            assert text in part, (section, text)

    changes = legal.body("changes")
    entry = _between(changes, "## 2026년 10월 18일 — 가족 공유", "\n## 2026년 10월 17일")
    for title in ("가족 공유 안내와 동의", "개인정보 처리방침", legal.doc("pledge_self").title):
        assert title in entry, title
    assert "결제 전 확인·환불 정책" in entry and "다시 받지 않고" in entry

    for key in ("payment", "refund"):
        body = legal.body(key).replace("**", "")
        for text in (
            "가족 공유로 초대한 가족이 대화한 것도 대화한 것으로 봐요",
            "가족 공유는 가족 요금제(무료 체험 기간 포함)에서 쓸 수 있",
            "추억 요금제로 내리거나 해지하면 이번 결제 기간이 끝난 뒤부터",
            "가족 요금제로 돌아오면 다시 함께 써요",
            "초대한 가족도 더는 대화할 수 없어요",
        ):
            assert text in body, (key, text)
    assert "대화 횟수는 초대한 가족과 함께 써요" in legal.body("payment")

    pledge = legal.body("pledge_self")
    assert "가족 공유를 켜지 않으면 **이 계정에서만**" in pledge
    assert "가족 공유는 제가 직접 켜야 쓰여요" in pledge and "가족에게 보여 주거나" not in pledge


# ────────────────────────── 회원 탈퇴 ──────────────────────────


def test_account_deletion(fake_ai: FakeAI):  # noqa: F811
    email = "leaving@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _ready_persona(client, h)
        _pay(client, h, pid, "memories")
        cid = client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
            "conversationId"
        ]
        reply = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": "할머니"}
        ).json()["data"]["assistantMessage"]["id"]
        assert client.post(f"{API}/messages/{reply}/speech", headers=h).status_code == 200
        storage = get_storage()
        assert storage.path(f"conversations/{cid}").exists()
        assert storage.path(f"personas/{pid}").exists()
        voice = _db(lambda db: db.get(Persona, pid))
        assert voice.voice_id and voice.voice_provider == "fish"
        user = _user(email)
        before = len(_records(user.id))

        wrong = client.request("DELETE", f"{API}/auth/me", headers=h, json={"confirm": "네"})
        assert wrong.status_code == 400
        gone = client.request("DELETE", f"{API}/auth/me", headers=h, json={"confirm": "탈퇴"})
        assert gone.status_code == 200, gone.text
        # 응답에는 지운 일시와 백업이 지워지는 날도 실린다(0.10 — test_deletion_notice.py)
        assert gone.json()["data"]["deleted"] is True

        # 대화 상대·대화·저장소는 지우고, 목소리를 만든 회사에도 지워 달라고 한다
        assert _db(lambda db: db.get(Persona, pid)) is None
        assert not storage.path(f"conversations/{cid}").exists()
        assert not storage.path(f"personas/{pid}").exists()
        deleted = {"voiceId": voice.voice_id, "provider": "fish"}
        assert deleted in fake_ai.calls["/v1/voice/delete"]
        # 결제 기록·동의 이력은 남는다
        payments = _db(
            lambda db: db.scalars(select(Payment).where(Payment.user_id == user.id))
        ).all()
        assert len(payments) == 1
        assert len(_records(user.id)) == before
        # 계정은 알아볼 수 없게
        left = _db(lambda db: db.get(User, user.id))
        assert left.status == UserStatus.DELETED
        assert left.email == f"deleted-{user.id}@deleted.talkto.invalid"
        assert left.name == "탈퇴한 회원"
        assert (
            _db(lambda db: db.scalars(select(Consent).where(Consent.user_id == user.id))).all()
            == []
        )
        assert (
            _db(
                lambda db: db.scalars(select(SocialAccount).where(SocialAccount.user_id == user.id))
            ).all()
            == []
        )
        # 더는 쓸 수 없고, 같은 이메일로 다시 가입할 수 있다
        assert client.get(f"{API}/auth/me", headers=h).status_code == 401
        login = client.post(f"{API}/auth/login", json={"email": email, "password": "password123"})
        assert login.status_code == 401
        again = _signup(client, email)
        assert client.get(f"{API}/personas", headers=again).json()["data"] == []


def test_account_deletion_waits_for_running_work(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "leaving-busy@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]

        async def running(db):
            from app.db.models import PersonaJob

            db.add(PersonaJob(persona_id=pid, kind="TRANSCRIBE", status="RUNNING"))
            await db.commit()

        _db(running)
        busy = client.request("DELETE", f"{API}/auth/me", headers=h, json={"confirm": "탈퇴"})
        assert busy.status_code == 409 and "지금 하고 있는 일" in busy.json()["detail"]
        assert _user("leaving-busy@example.com").status == UserStatus.ACTIVE


# ────────────────────────── 나 자신 ──────────────────────────


def test_self_persona(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "myself@example.com")
        meta = client.get(f"{API}/intake/meta").json()["data"]
        assert meta["relations"][0] == "나 자신"
        created = client.post(
            f"{API}/personas",
            headers=h,
            json={
                "name": "",
                "relation": "나 자신",
                "status": "deceased",
                "myName": "홍길동",
                "myRelation": "손자",
                "calledMe": "길동아",
            },
        )
        assert created.status_code == 201, created.text
        pid = created.json()["data"]["id"]
        detail = _detail(client, h, pid)
        assert detail["basics"] == {
            "name": "홍길동",
            "relation": "나 자신",
            "addressTerm": "나",
            "status": "alive",
            "myName": "홍길동",
            "myRelation": None,
            "calledMe": None,
        }
        assert detail["lifeStatus"] == "LIVING"
        assert detail["pledgeDoc"] == "pledge_self"
        empty = client.post(
            f"{API}/personas",
            headers=h,
            json={"name": " ", "relation": "나 자신", "status": "alive", "myName": ""},
        )
        assert empty.status_code == 400

    built = intake.build_intake(detail["basics"], {"C-05": "성실한 사람"})
    assert built["intakeContext"]["basicProfile"]["relation"] == "본인"
    assert built["intakeContext"]["basicProfile"]["status"] == "생존"
    me = built["intakeContext"]["familyMap"][0]
    assert me["relation"].startswith("본인") and "addressTermTo" not in me
    assert built["subjectContext"]["familyMembers"][0]["relationToSubject"] == "본인"
    # 다른 대화 상대는 그대로
    other = intake.build_intake(intake.validate_basics(BASICS), {})
    assert other["intakeContext"]["familyMap"][0]["relation"] == "손자"


def test_elevenlabs_records_are_deleted_after_use():
    """ElevenLabs 계정에 남는 받아쓰기·대답 읽기 기록은 다 쓰면 바로 지운다(AI 서버
    infra/external_api/elevenlabs_records.py). '지울 때까지 남아요'처럼 끝없는 보관을
    다시 적지 않는다."""
    for key in ("overseas", "privacy_policy"):
        body = legal.body(key)
        assert "TalkTo가 지울 때까지" not in body
        assert "바로 지워요" in body
        assert "2026년 10월 25일까지" in body
    assert "ElevenLabs 기록 지우기" in legal.body("changes")


# ────────────────────────── 0.9 (2026-10-21 개편) ──────────────────────────

# 계정 동의를 받는 때를 둘로 나눴던 0.7·0.8 의 글(바뀐 점의 이력에만 남는다)
_STAGE_WORDING = (
    "처음 대화 상대를 만들 때",
    "가입할 때가 아니라",
    "두 번에 나눠",
    "이 세 가지 동의를 하지 않아도",
    "ElevenLabs로 보내는 것이 없어요",
)


def test_documents_take_every_account_consent_at_signup():
    # 아홉 가지 계정 동의는 다시 모두 가입할 때 받는다(§8). 받는 때를 가르는 글이 남지 않게
    for key in legal.docs():
        if key == "changes":
            continue
        body = legal.body(key)
        for words in _STAGE_WORDING:
            assert words not in body, (key, words)
    for key in ("terms", "privacy", "overseas", "voice_data", "voice_synthesis"):
        assert "가입할 때 모든 분께" in legal.body(key).replace("**", ""), key
    policy = legal.body("privacy_policy").replace("**", "")
    assert "가입할 때 모든 분께 한 번에 받아요" in policy
    # ElevenLabs 동의는 이제 모든 분께 받는다(만드는 분만이 아니다)
    overseas = legal.body("overseas")
    assert "### ElevenLabs (미국) — 녹음과 말로 적은 답 받아쓰기 (가입할 때, 모든 분)" in overseas
    row = _between(policy, "- Eleven Labs Inc.(미국, dpo@elevenlabs.io)", "\n- ")
    assert "만드는 분만" not in row
    # 동의하지 않으면 가입을 마칠 수 없다(가족 대화만 하는 길은 없다)
    for key in ("voice_data", "voice_synthesis"):
        refuse = _between(legal.body(key), "## 동의하지 않으면", "\n## ")
        assert "가입을 마칠 수 없고 TalkTo를 쓸 수 없어요" in refuse, key
        assert "가족이 가족 공유로 초대한 대화 상대와는 이야기할 수 있어요" not in refuse, key
    eleven = _between(overseas, "### ElevenLabs (미국)", "\n## ")
    assert (
        "- 동의하지 않으면: 녹음과 말로 적은 답을 글로 옮길 수 없어 가입을 마칠 수 없고" in eleven
    )
    # 바뀐 점이 0.7·0.8 때 여섯 가지만 받은 분이 남은 셋을 받는 까닭을 알린다
    entry = _between(
        legal.body("changes"), "## 2026년 10월 21일 (버전 0.9)", "\n## 2026년 10월 18일"
    )
    assert "여섯 가지만 받으신 분" in entry and "아홉 가지" in entry


def test_more_page_is_called_fill_in_memories():
    # 화면 이름 '질문 더 답하기' → '기억 더 채우기'(§1·§9). 바뀐 점의 이력에만 예전 이름이 남는다
    for key in legal.docs():
        if key == "changes":
            continue
        assert "질문 더 답하기" not in legal.body(key), key
    assert "'기억 더 채우기'" in legal.body("service")
    policy = legal.body("privacy_policy")
    assert "그 대화 상대의 '기억 더 채우기'에서 언제든 거둘 수 있어요" in policy


def test_spoken_answers_are_disclosed_as_never_kept():
    # 답 칸의 🎤(말로 적기)는 ElevenLabs 가 받아쓰고, 녹음은 TalkTo·ElevenLabs 어디에도 남지 않으며
    # 목소리 만들기에 쓰지 않는다(§2 — app/api/v1/transcriptions.py, AI d_answer_transcription.py)
    eleven = _between(legal.body("overseas"), "### ElevenLabs (미국)", "\n## ")
    for text in (
        "'말로 적기'로 말씀하신 녹음(3분까지)",
        "말로 적은 답의 녹음은 TalkTo도 저장하지 않아요",
        "목소리를 만드는 데 쓰지 않아요",
        "받아쓰기 기록(보낸 녹음과 받아쓴 글)을 바로 지우고",
    ):
        assert text in eleven, text
    privacy = legal.body("privacy")
    assert "녹음은 글로 옮기자마자 지우고 TalkTo에 저장하지 않으며" in privacy
    policy = legal.body("privacy_policy")
    spoken = _between(policy, "- **말로 적은 답**", "\n- ")
    assert "저장하지 않아요" in spoken and "목소리를 만드는 데 쓰지 않아요" in spoken
    # 초대받은 가족의 말로 적은 답도 ElevenLabs 로 간다(대답 읽기는 여전히 Fish 만)
    transfer = _between(policy, "## 5. 해외로 옮기는 개인정보", "\n## 6.")
    assert (
        "초대받은 가족이 답 칸에서 말로 적으시면 그 녹음은 받아쓰기를 맡는 ElevenLabs" in transfer
    )
    assert "초대받은 가족이 듣는 대답 글은 Fish Audio로만 보내고" in transfer
    assert "'말로 적기'" in legal.body("family_share")


def test_feedback_gaps_and_style_examples_are_disclosed():
    # §4·§5: 평가·고친 말·대화 한마디, '더 알려 주실 기억', 말투 예시. 평가를 남긴 대답은 운영자가
    # 본다(관리자 평가 목록이 대답 글을 함께 보인다 — app/api/v1/admin.py feedback_list)
    policy = legal.body("privacy_policy")
    items = _between(policy, "## 2. 처리하는 개인정보와 보관 기간", "\n## 3.")
    for text in (
        "'그분이라면 어떻게 말하셨을까요?'에 적은 한 줄), 대화에 남긴 한마디",
        (
            "평가를 남긴 대답(그 대답 글과 고른 것, 적은 한 줄)과 대화에 남긴 한마디는 "
            "서비스를 고치려고 운영자가 봐요"
        ),
        "- **대화 고침과 알려 주신 기억**",
        "말투 예시는 글 예시로만 쓰고 그분 목소리로 읽지 않아요",
        "만든 분이 확인해 넣기 전에는 반영하지 않고",
    ):
        assert text in items, text
    third = _between(policy, "## 3. 다른 회사에 넘기는 일", "\n## 4.")
    assert "- **가족이 보낸 고친 말과 답**" in third and "대화 내용은 빼고 질문만" in third
    openai = _between(policy, "- **OpenAI OpCo, LLC**", "\n- **")
    assert "말투 예시(고친 대답과 고친 말)" in openai
    assert "'더 알려 주실 기억' 질문 만들기" in openai
    privacy = legal.body("privacy")
    assert "TalkTo 운영자가 봐요" in privacy
    assert "- **대화 상대를 고친 것과 알려 주신 기억**" in privacy
    # 고친 말은 글 예시로만 — 그분 목소리로 아무 문장이나 읽히지 않는다(잠금). '내용이 틀려요'에
    # 적은 말은 기억이 된다(privacy 와 같게 — corrections.apply)
    synthesis = legal.body("voice_synthesis")
    assert "적어 주신 글을 그대로 그분 목소리로 읽어 드리지 않아요" in synthesis
    assert "'말투가 달라요'에 적어 주신 말은 대답을 고치는 글 예시로만" in synthesis
    assert "'내용이 틀려요'에 적어 주신 말은 대화 상대의 기억이 돼요" in synthesis
    # 함께 쓰는 가족과의 대화로는 질문을 만들지 않는다(가족이 '내용이 틀려요'라고 한 대답만)
    for key in ("family_share", "privacy", "privacy_policy"):
        doc = legal.body(key)
        assert "'내용이 틀려요'라고 알려 준 대답" in doc, key
        assert "가족과의 대화에서 나온 것도" not in doc, key
        assert "나와의 대화에서 대화 상대가 잘 모르는 이야기가 나오면" not in doc, key
    # 대화 중에는 묻지 않고, 알림(푸시)은 없다
    service = legal.body("service")
    assert "대화 중에는 묻지 않고" in service and "알림(푸시)은 보내지 않아요" in service
    # 초대받은 가족이 보낸 것은 만든 분이 확인한 뒤에만(이용약관 2-1)
    terms = legal.body("terms").replace("**", "")
    assert "만든 분이 확인해야 대화 상대에 반영돼요" in terms


def test_interview_document_and_disclosures():
    # §7·§9: 기억 인터뷰 신청 안내와 동의(신청서에서만 받는 대화 상대별 동의)
    item = legal.doc("interview")
    assert (item.title, item.version, item.kind) == (
        "기억 인터뷰 신청 안내와 동의",
        "0.9",
        "consent",
    )
    assert "interview" not in {i["doc"] for i in legal.ACCOUNT_REQUIRED}
    assert legal.PERSONA_TYPES["interview_use"][0] == "interview"
    assert legal.PERSONA_TYPES["interview_recording"][0] == "interview"
    body = legal.body("interview")
    assert "{{" not in body
    for text in (
        # 인터뷰 받는 분은 만든 분이고, 그분 본인은 절대 아니다
        "인터뷰는 신청하신 만든 분과만 해요",
        "그분(대화 상대가 된 분) 본인께는 연락드리지 않고, 인터뷰하지 않아요",
        "'나 자신'으로 만든 대화 상대는 만든 분이 곧 그분이라 신청할 수 없어요",
        # 받는 것·보관
        "전화번호",
        "통화 방법(전화·영상 통화)",
        "30일",
        # 녹음은 올리지 않고 글만 넣는다
        "녹음 파일은 TalkTo 서비스에 올리지 않아요",
        "녹음으로 그분이나 회원님의 목소리를 만들지 않아요",
        "운영자가 글로 옮겨",
        "'해외 회사로 보내는 정보'",
        "이름이나 전화번호는 알림에 넣지 않아요",
        "종교·건강 이야기는 그 대화 상대에 따로 동의하신 경우에만",
    ):
        assert text in body, text
    refuse = _between(body, "## 동의하지 않으면", "\n## ")
    assert refuse.splitlines()[1] == "- 이 동의는 하지 않으실 수 있어요(거부할 권리가 있어요)."
    for word in ("상담", "전화 인터뷰"):
        assert word not in body, word
    # 처리방침·수집 안내·이용약관에도 적는다
    policy = legal.body("privacy_policy")
    assert "- **기억 인터뷰**(기억 인터뷰 신청 동의" in policy
    assert "기억 인터뷰 신청의 이름·전화번호·바라는 요일과 시간은" in policy
    assert "'기억 인터뷰 신청 안내와 동의'" in legal.body("privacy")
    assert "## 2-2. 기억 인터뷰" in legal.body("terms")
    assert "기억 인터뷰 신청 안내와 동의**를 새로 펴냈어요" in legal.body("changes")


def test_customer_center_inquiries_are_disclosed():
    # §6: 카카오톡 채널 문의를 받는 곳(위탁)과 문의 기록
    policy = legal.body("privacy_policy")
    assert "- **주식회사 카카오**: 카카오톡 채널로 받는 문의" in _between(
        policy, "## 4. 처리를 맡기는 곳", "\n## 5."
    )
    assert "- **문의 기록**" in policy and "문의 코드" in policy
    assert "'고객센터'" in legal.body("terms")
