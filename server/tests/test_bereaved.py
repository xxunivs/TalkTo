"""돌아가신 분의 신청 자격과 유족 명단(2026-10-22 법률 자문 · WO 5 · SPEC §7.2 · §7.5).

검사 글·'적을 분이 없어요' 규칙·확약 기록·덮어쓰기 이력·멈춘 대화 상대·직접 알림 문안(관리자 수동).
모든 자료는 지어낸 시험용이다(실제 가족 자료를 쓰지 않는다).
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from legal_gates import pass_subject_consent
from sqlalchemy import select
from test_sharing import (  # noqa: F401 — settings 는 이 파일에도 그대로 걸린다(autouse)
    API,
    OWNER_BASICS,
    PROFILE,
    FakeAI,
    _accept,
    _admin,
    _all,
    _db,
    _get,
    _link,
    _persona,
    _set,
    _signup,
    fake_ai,
    settings,
)

from app.db.models import BereavedDeclaration, ConsentRecord, Persona
from app.main import app
from app.services import bereaved, gate, holds, legal

MEMBERS = [
    {"name": "오철수", "relation": "son", "contact": "010-1111-2222"},
    {"name": "오영희", "relation": "daughter", "contact": "younghee@example.com"},
    {"name": "오만복", "relation": "spouse"},
]
DECLARE = {"applicantRelation": "grandchild", "members": MEMBERS, "noneLeft": False, "pledge": True}


def _put(client, h, pid, body):
    return client.put(f"{API}/personas/{pid}/bereaved", headers=h, json=body)


def test_bereaved_validation_messages():
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid)
        row = {"name": "오철수", "relation": "son"}
        cases = [
            ({**DECLARE, "applicantRelation": None}, "고인과 어떤 관계인지 골라 주세요."),
            (
                {**DECLARE, "applicantRelation": "other"},
                "동의해 준 유족(배우자·자녀·손주·부모 가운데 한 분)의 이름과 관계를 적어 주세요.",
            ),
            (
                {
                    **DECLARE,
                    "applicantRelation": "other",
                    "consentingName": "오철수",
                    "consentingRelation": "friend",
                },
                bereaved.CONSENTING_REQUIRED,
            ),
            (
                {**DECLARE, "members": []},
                "고인의 배우자·자녀(안 계시면 부모)를 적거나 '적을 분이 없어요'를 골라 주세요.",
            ),
            ({**DECLARE, "noneLeft": True}, bereaved.MEMBERS_REQUIRED),
            ({**DECLARE, "members": [{**row, "relation": "uncle"}]}, bereaved.MEMBERS_REQUIRED),
            ({**DECLARE, "members": [{**row, "name": "가" * 21}]}, "이름은 20자까지 적어 주세요."),
            (
                {**DECLARE, "members": [{**row, "contact": "1" * 61}]},
                "연락처는 60자까지 적어 주세요.",
            ),
            ({**DECLARE, "members": [row] * 13}, "12분까지 적을 수 있어요."),
            ({**DECLARE, "pledge": False}, "확인할 내용에 체크해 주세요."),
        ]
        for body, message in cases:
            refused = _put(client, h, pid, body)
            assert refused.status_code == 400, body
            assert refused.json()["detail"] == message, body
        assert _get(Persona, pid) is not None
        assert client.get(f"{API}/personas/{pid}/bereaved", headers=h).json()["data"] is None


def test_bereaved_save_overwrite_history_and_record():
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid)
        # 빈 줄(화면의 빈 칸)은 버린다
        saved = _put(client, h, pid, {**DECLARE, "members": [*MEMBERS, {"name": " "}]})
        assert saved.status_code == 200, saved.text
        data = saved.json()["data"]
        assert data["applicantRelation"] == "grandchild" and data["noneLeft"] is False
        assert data["members"][2] == {"name": "오만복", "relation": "spouse", "contact": None}
        assert data["pledgeVersion"] == legal.version_of("pledge_deceased")
        got = client.get(f"{API}/personas/{pid}/bereaved", headers=h).json()["data"]
        assert got["members"] == data["members"]
        # 적을 분이 없어요 + 그 밖의 친족·지인(동의해 준 유족)
        other = {
            "applicantRelation": "other",
            "consentingName": "오철수",
            "consentingRelation": "child",
            "members": [],
            "noneLeft": True,
            "pledge": True,
        }
        assert _put(client, h, pid, other).status_code == 200
        rows = _all(select(BereavedDeclaration).where(BereavedDeclaration.persona_id == pid))
        assert len(rows) == 1
        row = rows[0]
        assert row.none_left is True and row.members == [] and row.consenting_name == "오철수"
        # 덮기 전 선언은 이력에 남는다(이의가 왔을 때 그때 명단을 지우지 않게)
        assert [h_["members"] for h_ in row.history] == [data["members"]]
        assert row.history[0]["applicantRelation"] == "grandchild"
        records = _all(
            select(ConsentRecord).where(ConsentRecord.consent_type == f"bereaved_pledge:{pid}")
        )
        assert [(r.agreed, r.doc) for r in records] == [(True, "pledge_deceased")] * 2


def test_bereaved_only_for_deceased_owner_and_not_while_stopped():
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        living = _persona(uid, basics=OWNER_BASICS | {"status": "alive", "minor": False})
        refused = _put(client, h, living, DECLARE)
        assert refused.status_code == 409 and refused.json() == {
            "detail": "돌아가신 분의 대화 상대에서만 적어요.",
            "code": "not_deceased",
        }
        pid = _persona(uid)
        token, _ = _link(client, h, pid, agreements=["family_share"])
        member, _, _ = _signup(client, "박철수")
        assert _accept(client, member, token, PROFILE).status_code == 201
        # 가족·모르는 사람은 같은 404
        assert client.get(f"{API}/personas/{pid}/bereaved", headers=member).status_code == 404
        assert _put(client, member, pid, DECLARE).status_code == 404
        assert _put(client, h, pid, DECLARE).status_code == 200
        # 멈춘 대화 상대는 고치지 못한다(이의를 보낸 분을 명단에서 빼는 일을 막는다)
        _set(
            Persona,
            pid,
            hold_reason="objection",
            hold_at=datetime.now(UTC),
            delete_after=datetime.now(UTC) + timedelta(days=7),
        )
        stopped = _put(client, h, pid, {**DECLARE, "members": MEMBERS[:1]})
        assert stopped.status_code == 409 and stopped.json()["code"] == "stopped"
        assert stopped.json()["detail"] == holds.blocked_message(_get(Persona, pid))
        assert stopped.json()["detail"].startswith("이 대화 상대는 지금 멈췄어요.")
        _set(Persona, pid, hold_reason=None, hold_at=None, delete_after=None)


def test_templates_for_manual_notice(settings, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "business_name", "톡투")
    monkeypatch.setattr(settings, "business_owner", "대표이름")
    monkeypatch.setattr(settings, "business_email", "help@example.com")
    monkeypatch.setattr(settings, "business_phone", "010-0000-0000")
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid)
        assert _put(client, h, pid, DECLARE).status_code == 200
        admin, _ = _admin(client)
        view = client.get(f"{API}/admin/personas/{pid}/legal", headers=admin)
        assert view.status_code == 200, view.text
        data = view.json()["data"]
        assert data["bereaved"]["members"][0]["contact"] == "010-1111-2222"
        templates = data["templates"]
        # 연락처가 있는 분만, 문자·메일을 연락처로 가른다(자동 발송 없음)
        assert [(t["memberIndex"], t["channel"]) for t in templates] == [(0, "sms"), (1, "email")]
        sms_text = templates[0]["text"]
        assert sms_text == (
            "[TalkTo] 안녕하세요, 오철수님. TalkTo(톡투) 대표 대표이름이에요. 고 오말순님의 손주 "
            "지은님께서 고인의 목소리와 이야기로 TalkTo에서 AI 대화 상대를 만드셨고, 신청하실 때 "
            "오철수님께 알렸다고 적어 주셨어요. 원하지 않으시면 이 문자에 답하시거나 "
            "help@example.com, 010-0000-0000로 알려 주세요. 알려 주시면 바로 대화를 멈추고 7일 "
            "안에 지워요."
        )
        mail = templates[1]
        assert mail["subject"] == "[TalkTo] 고 오말순님의 AI 대화 상대를 만든 사실을 알려 드려요"
        assert "이 메일에 답하시거나" in mail["text"] and "이 문자에" not in mail["text"]
        assert mail["text"].endswith("톡투 · 대표 대표이름 · help@example.com · 010-0000-0000")
        assert f"{settings.frontend_url.rstrip('/')}/#/legal/privacy_policy" in mail["text"]
        assert "고인의 이름으로 먼저 연락하지 않아요" in mail["text"]


def test_living_changed_to_deceased_needs_the_declaration(fake_ai: FakeAI):  # noqa: F811
    """다 만든 살아 계신 분을 돌아가신 분으로 바꾸면(gate.BEREAVED_REQUIRED) 대표 답 #3(배포
    전부터 고인이던 것은 막지 않음)이 아니라 새 고인처럼 본다: 유족 선언 전에는 녹음 처리·목소리·
    만들기가 막히고(그분 녹음을 상태 바꾸기로 해외로 보내지 못하게) 대화는 그대로. 선언을 적으면
    풀리고 관리자 목록(status_changed)에서도 빠진다."""
    living = OWNER_BASICS | {"status": "alive", "minor": False}
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid, basics=living, voice_id="fish_old", voice_provider="fish")
        _set(Persona, pid, subject_voices=["VOICE_1"])
        pass_subject_consent(None, pid)
        saved = client.put(f"{API}/personas/{pid}", headers=h, json={"basics": OWNER_BASICS})
        assert saved.status_code == 200, saved.text
        assert _get(Persona, pid).basics[gate.BEREAVED_REQUIRED] is True
        admin, _ = _admin(client)

        def listed() -> dict:
            rows = client.get(f"{API}/admin/personas/unable", headers=admin).json()["data"]
            return {row["personaId"]: row["kind"] for row in rows}

        assert listed()[pid] == "status_changed"

        async def blocks(db):
            persona = await db.get(Persona, pid)
            out = {}
            for action in ("audio", "voice", "build", "chat"):
                found = await gate.block(db, persona, action)
                out[action] = found.code if found else None
            return out

        assert _db(blocks) == {
            "audio": "bereaved",
            "voice": "bereaved",
            "build": "bereaved",
            "chat": None,
        }
        for path in ("voice", "recordings/apply"):
            blocked = client.post(f"{API}/personas/{pid}/{path}", headers=h)
            assert blocked.status_code == 409, path
            assert blocked.json()["code"] == "bereaved", path
        assert not fake_ai.calls.get("/v1/voice/clone")
        assert _put(client, h, pid, DECLARE).status_code == 200
        assert set(_db(blocks).values()) == {None}
        assert pid not in listed()
