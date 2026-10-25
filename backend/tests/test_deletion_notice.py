"""지울 때 알림(2026-10-22 법률 자문 · WO 7 · SPEC §9): 서버 백업에 남는 날짜, 목소리 회사 삭제
결과에 따른 문구, 지운 일시 기록, 회원 탈퇴 응답·메일·앱 알림 정리, 목소리 동의 거두기 응답.

모든 자료는 지어낸 시험용이다. AI 서버는 가짜(test_persona_setup.FakeAI)다.
"""

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_persona_setup import FakeAI, _signup, fake_ai  # noqa: F401
from test_sharing import _persona

from app.db.models import ConsentRecord, DeletionRecord, OwnerNotice, Persona, User
from app.db.session import SessionLocal
from app.main import app
from app.services import ai as ai_module
from app.services import deletion, mailer, notices, persona_delete

API = "/api/v1"
KST = timezone(timedelta(hours=9))
TAIL = "해외 회사에 남는 자료는 '해외 회사로 보내는 정보'에 적은 대로예요."


def _db(fn):
    async def go():
        async with SessionLocal() as db:
            return await fn(db)

    return asyncio.run(go())


def _me_id(client: TestClient, h: dict) -> str:
    return client.get(f"{API}/auth/me", headers=h).json()["data"]["id"]


def _records(**where) -> list[DeletionRecord]:
    async def go(db):
        query = select(DeletionRecord)
        for key, value in where.items():
            query = query.where(getattr(DeletionRecord, key) == value)
        return list((await db.scalars(query.order_by(DeletionRecord.done_at))).all())

    return _db(go)


def _kst_date(days: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).astimezone(KST).date().isoformat()


def _md(iso_date: str) -> str:
    y, m, d = (int(x) for x in iso_date.split("-"))
    return f"{m}월 {d}일"


def test_messages_are_the_final_wording_and_backup_date_is_kst():
    # 한국 10월 24일 새벽 1시에 지웠다 → 백업은 7일 뒤(한국 10월 31일)까지
    done = datetime(2026, 10, 23, 16, 0, tzinfo=UTC)
    result = deletion.DeletionResult(done, done + timedelta(days=7), True, "")
    assert deletion.backup_date(result) == "10월 31일"
    assert deletion.message(result, scope="persona") == (
        "TalkTo 서버에서 지웠고, 목소리를 만든 회사에서도 만든 목소리를 지웠어요. "
        "서버 백업에는 10월 31일까지 남았다가 저절로 지워지고, " + TAIL
    )
    result.vendor_ok = None
    assert deletion.message(result, scope="account") == (
        "TalkTo 서버에서 지웠어요. 서버 백업에는 10월 31일까지 남았다가 저절로 지워지고, " + TAIL
    )
    result.vendor_ok = False
    assert deletion.message(result, scope="voice") == (
        "TalkTo 서버에서 지웠어요. 목소리를 만든 회사에서는 아직 지우지 못해 TalkTo가 따로 확인해 "
        "지울게요. 서버 백업에는 10월 31일까지 남았다가 저절로 지워지고, " + TAIL
    )
    assert deletion.message(result, scope="sensitive") == (
        "동의를 거두고 적은 답을 TalkTo 서버에서 지웠어요. 서버 백업에는 10월 31일까지 남았다가 "
        "저절로 지워지고, " + TAIL
    )
    # 지울 수 없는 곳까지 지웠다고 하지 않는다(대답을 만드는 회사의 30일 보관 — 변호사 p.14)
    for vendor_ok in (True, None, False):
        result.vendor_ok = vendor_ok
        for scope in ("persona", "account", "voice", "sensitive"):
            text = deletion.message(result, scope=scope)
            assert "운영 시스템" not in text and "삭제 완료" not in text
    result.message = "글"
    assert deletion.response(result) == {
        "deleted": True,
        "doneAt": "2026-10-23T16:00:00+00:00",
        "backupUntil": "2026-10-31",
        "vendorOk": False,
        "message": "글",
    }
    # 기록 종류 → 문구 종류(멈춘 까닭으로 지운 것도 대화 상대 지우기)
    assert deletion.scope_of("objection") == "persona"
    # 가족 공유를 그만두거나 가족이 나가 그 가족의 대화를 지운 것도 같은 문구(sharing.end_share)
    assert deletion.scope_of("share_member") == "persona"
    assert deletion.scope_of("voice_consent") == "voice"
    assert deletion.scope_of("sensitive_consent") == "sensitive"
    assert deletion.combine([]) is None
    assert deletion.combine([result]) is False


def test_record_keeps_only_when_and_cleanup_after_five_years():
    with TestClient(app):  # 표를 만든다(앱을 켤 때 create_all)
        pass
    pid = str(uuid4())

    async def go(db):
        old = await deletion.record(
            db,
            kind="persona",
            user_id=None,
            persona_id=pid,
            requested_by="system",
            vendor_ok=None,
            at=datetime.now(UTC) - timedelta(days=5 * 365 + 2),
        )
        new = await deletion.record(
            db,
            kind="objection",
            user_id=None,
            persona_id=pid,
            requested_by="system",
            vendor_ok=True,
            detail={"personas": 1},
        )
        return old, new

    old, new = _db(go)
    assert new.backup_until - new.done_at == timedelta(days=7)
    assert new.message.startswith("TalkTo 서버에서 지웠고")
    rows = _records(persona_id=pid)
    assert [r.kind for r in rows] == ["persona", "objection"]
    assert rows[1].requested_by == "system" and rows[1].detail == {"personas": 1}
    assert rows[1].vendor_ok is True

    removed = _db(lambda db: deletion.cleanup(db, datetime.now(UTC)))
    assert removed >= 1
    assert [r.kind for r in _records(persona_id=pid)] == ["objection"]


def _remove(pid: str, **kw) -> deletion.DeletionResult:
    async def go(db):
        persona = await db.get(Persona, pid)
        message, sub = await persona_delete.claim(db, persona)
        assert message is None
        return await persona_delete.remove(db, persona, **kw)

    return _db(go)


def test_persona_delete_records_and_says_what_was_deleted(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, f"del-{uuid4().hex[:8]}@example.com")
        owner = _me_id(client, h)
        # 목소리 회사에 둔 목소리가 있다 → 회사에서도 지웠다
        voiced = _persona(owner, voice_id=f"v-{uuid4().hex}", voice_provider="fish")
        result = _remove(voiced)
        assert result.vendor_ok is True
        assert result.message.startswith("TalkTo 서버에서 지웠고, 목소리를 만든 회사에서도")
        body = deletion.response(result)
        assert body["deleted"] is True and body["vendorOk"] is True
        # 서버 백업에는 지운 날부터 7일(한국 날짜)
        assert body["backupUntil"] == _kst_date(7)
        assert f"서버 백업에는 {_md(_kst_date(7))}까지" in body["message"]
        assert fake_ai.calls["/v1/voice/delete"][-1]["provider"] == "fish"
        [row] = _records(persona_id=voiced)
        assert (row.kind, row.user_id, row.requested_by) == ("persona", owner, "owner")
        assert row.backup_until - row.done_at == timedelta(days=7)

        # 목소리가 없다 → TalkTo 서버에서만 지웠다고 알린다
        plain = _persona(owner)
        result = _remove(plain, requested_by="system", kind="objection")
        assert result.vendor_ok is None
        assert result.message.startswith("TalkTo 서버에서 지웠어요. 서버 백업에는")
        [row] = _records(persona_id=plain)
        assert (row.kind, row.requested_by, row.vendor_ok) == ("objection", "system", None)

        # 목소리 회사에서 지우지 못했다 → 따로 확인해 지운다고 알린다
        async def broken(self, **kw):
            raise ai_module.AIServerError(503)

        monkeypatch.setattr(ai_module.AIService, "delete_voice", broken)
        failed = _persona(owner, voice_id=f"v-{uuid4().hex}", voice_provider="fish")
        result = _remove(failed)
        assert result.vendor_ok is False
        assert "아직 지우지 못해 TalkTo가 따로 확인해 지울게요" in result.message
        assert _records(persona_id=failed)[0].vendor_ok is False
        assert _db(lambda db: db.get(Persona, failed)) is None


def test_shared_voice_kept_for_another_persona_is_not_called_deleted(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, f"del-shared-{uuid4().hex[:8]}@example.com")
        owner = _me_id(client, h)
        # 관리자가 같은 목소리를 두 대화 상대에 넣었다 → 하나를 지워도 회사의 목소리는 남는다
        voice = f"v-{uuid4().hex}"
        first = _persona(owner, voice_id=voice, voice_provider="fish")
        second = _persona(owner, voice_id=voice, voice_provider="fish")
        before = len(fake_ai.calls.get("/v1/voice/delete", []))
        result = _remove(first)
        assert result.vendor_ok is None
        assert result.message.startswith("TalkTo 서버에서 지웠어요. 서버 백업에는")
        assert len(fake_ai.calls.get("/v1/voice/delete", [])) == before
        # 마지막 대화 상대를 지우면 그때 회사에서도 지운다
        result = _remove(second)
        assert result.vendor_ok is True
        assert fake_ai.calls["/v1/voice/delete"][-1]["voiceId"] == voice


def test_withdrawal_answers_with_backup_date_mails_the_old_address_and_drops_notices(
    fake_ai: FakeAI,  # noqa: F811
    monkeypatch,
):
    sent = []
    monkeypatch.setattr(
        mailer, "send_user", lambda to, subject, text: sent.append((to, subject, text))
    )
    email = f"bye-{uuid4().hex[:8]}@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        uid = _me_id(client, h)
        pid = _persona(uid, voice_id=f"v-{uuid4().hex}", voice_provider="fish")
        _persona(uid)

        async def notes(db):
            # 계약 내용 알림(하루 정리에서 지우지 않는 것)과 보통 알림
            await notices.create(db, uid, "contract", "결제 내용", "본문", dedupe_key="contract:x")
            await notices.create(db, uid, "resumed", "다시 쓸 수 있어요", "본문")
            await db.commit()

        _db(notes)
        gone = client.request("DELETE", f"{API}/auth/me", headers=h, json={"confirm": "탈퇴"})
        assert gone.status_code == 200, gone.text
        data = gone.json()["data"]
        assert data["deleted"] is True
        assert data["backupUntil"] == _kst_date(7)
        assert data["vendorOk"] is True  # 목소리 하나를 회사에서도 지웠다
        assert data["message"].startswith("TalkTo 서버에서 지웠고, 목소리를 만든 회사에서도")
        assert f"서버 백업에는 {_md(_kst_date(7))}까지" in data["message"]
        assert datetime.fromisoformat(data["doneAt"]) <= datetime.now(UTC)

    # 대화 상대마다 한 줄 + 계정 한 줄(개수만)
    rows = _records(user_id=uid)
    assert sorted(r.kind for r in rows) == ["account", "persona", "persona"]
    account = next(r for r in rows if r.kind == "account")
    assert account.detail == {"personas": 2} and account.persona_id is None
    assert account.requested_by == "owner" and account.vendor_ok is True
    assert pid in {r.persona_id for r in rows}
    # 탈퇴는 계정 줄을 남기므로(익명) 앱 알림을 따로 지운다 — 계약 내용 알림까지
    left = _db(lambda db: db.scalars(select(OwnerNotice).where(OwnerNotice.user_id == uid)))
    assert list(left.all()) == []
    user = _db(lambda db: db.get(User, uid))
    assert user.email.endswith("@deleted.talkto.invalid")
    # 메일은 주소를 바꾸기 전에 잡아 둔 원래 주소로(설정이 켜져 있을 때만 실제로 간다)
    assert sent == [
        (
            email,
            "[TalkTo] 회원 탈퇴를 마쳤어요",
            data["message"] + "\n\n그동안 TalkTo와 함께해 주셔서 고마워요.",
        )
    ]


def test_withdrawal_without_personas_and_without_mail_settings(monkeypatch):
    started = []
    monkeypatch.setattr(mailer, "_start", lambda *a: started.append(a) or True)
    with TestClient(app) as client:
        h = _signup(client, f"bye-empty-{uuid4().hex[:8]}@example.com")
        uid = _me_id(client, h)
        gone = client.request("DELETE", f"{API}/auth/me", headers=h, json={"confirm": "탈퇴"})
        assert gone.status_code == 200, gone.text
        data = gone.json()["data"]
        assert data["vendorOk"] is None
        assert data["message"].startswith("TalkTo 서버에서 지웠어요. 서버 백업에는")
    [row] = _records(user_id=uid)
    assert row.kind == "account" and row.detail == {"personas": 0}
    # 고객 메일이 꺼져 있으면(기본) 보내지 않는다
    assert started == []


def test_voice_consent_withdrawal_answers_with_deletion(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr(
        mailer, "send_user", lambda to, subject, text: sent.append((to, subject, text))
    )
    email = f"voice-off-{uuid4().hex[:8]}@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        uid = _me_id(client, h)
        pid = _persona(uid, voice_id=f"v-{uuid4().hex}", voice_provider="fish")
        off = client.post(
            f"{API}/consents",
            headers=h,
            json={"consentType": f"voice_clone:{pid}", "agreed": False},
        )
        assert off.status_code == 200, off.text
        data = off.json()["data"]
        assert data["agreed"] is False
        removed = data["deletion"]
        assert removed["deleted"] is True and removed["backupUntil"] == _kst_date(7)
        # 목소리 회사 삭제 결과(remove_voice 가 돌려줌)를 그대로 알린다 — 회사에서도 지웠다
        assert removed["vendorOk"] is True
        assert removed["message"].startswith("TalkTo 서버에서 지웠고, 목소리를 만든 회사에서도")
        assert f"서버 백업에는 {_md(_kst_date(7))}까지" in removed["message"]
        # 처리방침 6번: 받을 수 있는 이메일이 있으면 메일로도(설정이 켜져 있을 때만 실제로 간다)
        assert sent == [(email, "[TalkTo] 그분 목소리를 지웠어요", removed["message"])]
        assert _db(lambda db: db.get(Persona, pid)).voice_id is None
        [row] = _records(persona_id=pid)
        assert (row.kind, row.user_id, row.requested_by) == ("voice_consent", uid, "owner")
        # 지울 목소리가 없으면 deletion 은 null(기록도 남기지 않는다)
        again = client.post(
            f"{API}/consents",
            headers=h,
            json={"consentType": f"voice_clone:{pid}", "agreed": False},
        )
        assert again.status_code == 200 and again.json()["data"]["deletion"] is None
        assert len(_records(persona_id=pid)) == 1


@pytest.mark.parametrize(
    "prefix",
    [
        "subject_consent",
        "guardian_attest",
        "guardian_consent",
        "guardian_overseas",
        "guardian_sms",
        "bereaved_pledge",
        "recurring",
        "withdrawal_limit",
        "sensitive_health",
        "family_share",
    ],
)
def test_consent_without_withdrawal_handling_is_not_withdrawn_here(prefix):
    # 여기서 '거둠'만 적으면 처리(그분 동의 녹음 검수 통과 등)는 그대로인데 기록만 거둔 것이 된다
    email = f"no-withdraw-{uuid4().hex[:8]}@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        uid = _me_id(client, h)
        pid = _persona(uid)
        off = client.post(
            f"{API}/consents",
            headers=h,
            json={"consentType": f"{prefix}:{pid}", "agreed": False},
        )
        assert off.status_code == 400, off.text
        assert off.json()["detail"] == "이 동의는 대화 상대 화면에서 해 주세요."

    async def lines(db):
        return list(
            (await db.scalars(select(ConsentRecord).where(ConsentRecord.persona_id == pid))).all()
        )

    assert _db(lines) == []


def test_persona_delete_answers_and_mails_the_same_message(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr(
        mailer, "send_user", lambda to, subject, text: sent.append((to, subject, text))
    )
    email = f"del-api-{uuid4().hex[:8]}@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        pid = _persona(_me_id(client, h))
        gone = client.delete(f"{API}/personas/{pid}", headers=h)
        assert gone.status_code == 200, gone.text
        data = gone.json()["data"]
        assert data["deleted"] is True and data["vendorOk"] is None
        assert data["backupUntil"] == _kst_date(7)
    # 처리방침 6번: 화면과 같은 글을 받을 수 있는 이메일로도(설정이 켜져 있을 때만 실제로 간다)
    assert sent == [(email, "[TalkTo] 대화 상대를 지웠어요", data["message"])]
    [row] = _records(persona_id=pid)
    assert (row.kind, row.requested_by) == ("persona", "owner")


@pytest.mark.parametrize("hours", [0, 15])
def test_backup_date_follows_korean_calendar(hours):
    # 한국 날짜로 센다: UTC 15시는 한국 다음 날 0시
    done = datetime(2026, 12, 28, hours, 0, tzinfo=UTC)
    result = deletion.DeletionResult(done, done + timedelta(days=7), None, "")
    expected = "2027-01-04" if hours == 0 else "2027-01-05"
    assert deletion.response(result)["backupUntil"] == expected
    assert deletion.backup_date(result) == _md(expected)
