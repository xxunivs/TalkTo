"""법률 자문 반영(2026-10-22) Phase 0: 새 표·새 칸, 설정, 나이 셈(ages), 앱 안 알림(notices),
고객 메일(mailer.send_user), 게이트 오류 모양, 하루 정리 고리, 시험 도우미(legal_gates)."""

import asyncio
import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from legal_gates import declare_bereaved, guardian_ok, pass_subject_consent
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.dialects import postgresql
from test_persona_setup import _signup

from app import main as main_module
from app.core.config import MAIL_ORDER_NO, Settings, get_settings, missing_business_settings
from app.db import session as db_session
from app.db.base import Base
from app.db.models import (
    BereavedDeclaration,
    OwnerNotice,
    Person,
    Persona,
    PersonaMinorCheck,
    PersonaObjection,
    SubjectConsentRecording,
    User,
)
from app.db.session import SessionLocal
from app.main import app
from app.services import (
    ages,
    bereaved,
    deletion,
    gate,
    legal,
    mailer,
    minors,
    notices,
    sms,
    subject_consent,
)

API = "/api/v1"
ROOT = Path(__file__).resolve().parent.parent

NEW_TABLES = {
    "subject_consent_recordings",
    "persona_minor_checks",
    "bereaved_declarations",
    "persona_objections",
    "owner_notices",
    "deletion_records",
}
NEW_COLUMNS = [
    ("personas", "hold_reason"),
    ("personas", "hold_at"),
    ("personas", "delete_after"),
    ("payments", "first_chat_at"),
    ("payments", "first_chat_user_id"),
    ("payments", "first_chat_member"),
    ("payments", "trial_used"),
    ("payments", "contract_sent_at"),
    ("payments", "refunded_at"),
    ("payments", "refund_amount"),
    ("payments", "refund_note"),
]


@pytest.fixture(scope="module", autouse=True)
def _schema():
    """API 없이 DB 를 바로 쓰는 시험이 먼저 돌아도 표가 있게(서버를 켜면 lifespan 이 만든다)."""
    asyncio.run(db_session.create_schema())


def _db(fn):
    async def go():
        async with SessionLocal() as db:
            return await fn(db)

    return asyncio.run(go())


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _user_and_persona(basics: dict | None = None) -> tuple[str, str]:
    """API 없이 계정 하나와 대화 상대 하나(SQLite 시험 DB)."""

    async def go(db):
        user = User(email=f"p0-{uuid4().hex[:10]}@example.com", name="가", password_hash="x")
        db.add(user)
        await db.flush()
        person = Person(owner_user_id=user.id, name="나말순")
        db.add(person)
        await db.flush()
        persona = Persona(person_id=person.id, name="나말순", basics=basics or {})
        db.add(persona)
        await db.commit()
        return user.id, persona.id

    return _db(go)


# ── 새 표·새 칸 ──


def test_new_tables_and_columns_are_created(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'new.db').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        assert NEW_TABLES <= tables
        for table, column in NEW_COLUMNS:
            assert column in {c["name"] for c in inspect(connection).get_columns(table)}
        uniques = {
            tuple(u["column_names"])
            for u in inspect(connection).get_unique_constraints("owner_notices")
        }
        assert ("user_id", "dedupe_key") in uniques
        uniques = {
            tuple(u["column_names"])
            for u in inspect(connection).get_unique_constraints("subject_consent_recordings")
        }
        assert ("persona_id", "attempt") in uniques
    engine.dispose()


def test_new_columns_on_old_tables_are_nullable_and_listed_in_added():
    """운영 PostgreSQL 은 ADDED 에 있는 칸만 붙인다. 빈칸 허용·기본값 없음이어야 붙는다."""
    for table, column in NEW_COLUMNS:
        assert (table, column) in db_session.ADDED, (table, column)
        col = Base.metadata.tables[table].columns[column]
        assert col.nullable, (table, column)
        assert col.default is None and col.server_default is None, (table, column)
        assert not col.index and not col.unique, (table, column)
        # PostgreSQL 에서 ALTER TABLE … ADD COLUMN 할 타입 글이 나온다
        assert col.type.compile(dialect=postgresql.dialect())


def test_missing_new_columns_are_added_to_an_old_db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for table, column in NEW_COLUMNS:
            connection.exec_driver_sql(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        db_session._add_missing_columns(connection)
        for table, column in NEW_COLUMNS:
            assert column in {c["name"] for c in inspect(connection).get_columns(table)}
    engine.dispose()


def test_backup_days_match_the_rds_backup_retention():
    """지울 때 알림의 '서버 백업에는 ○월 ○일까지'는 RDS 자동 백업 보관 기간과 같아야 한다."""
    stack = (ROOT / "deploy" / "aws" / "stack.yaml").read_text(encoding="utf-8")
    found = re.findall(r"BackupRetentionPeriod:\s*(\d+)", stack)
    assert found and {int(v) for v in found} == {Settings().deletion_backup_days}


# ── 설정 ──


def test_new_settings_defaults():
    s = Settings(_env_file=None)
    # 대표 답(2026-10-23): 만 14세 미만 확인 문자는 당분간 막는다
    assert s.sms_provider == "off"
    assert s.sms_manual_code_hours == 24
    assert s.user_mail_enabled is False
    assert s.persona_auto_delete is True
    assert s.holds_max_deletes_per_sweep == 3
    assert (s.subject_consent_max_attempts, s.subject_consent_max_seconds) == (5, 900)
    assert s.subject_consent_max_mb == 32
    assert (s.hold_delete_days, s.minor_reconsent_months, s.deletion_backup_days) == (7, 6, 7)
    # 통신판매업 신고 번호는 공개 정보라 기본값. .env 에 비워 둬도 그 값
    assert s.mail_order_no == MAIL_ORDER_NO == "제2026-서울용산-1112호"
    assert Settings(_env_file=None, mail_order_no="  ").mail_order_no == MAIL_ORDER_NO
    assert Settings(_env_file=None, mail_order_no="다른 번호").mail_order_no == "다른 번호"


def test_under14_is_closed_while_sms_is_off(monkeypatch):
    off = get_settings().model_copy(update={"sms_provider": "off"})
    monkeypatch.setattr(sms, "get_settings", lambda: off)
    assert sms.get_provider() is None and sms.under14_open() is False
    blank = get_settings().model_copy(update={"sms_provider": ""})
    monkeypatch.setattr(sms, "get_settings", lambda: blank)
    assert sms.under14_open() is False


def test_missing_business_settings_names_only():
    empty = Settings(_env_file=None)
    assert missing_business_settings(empty) == [
        "BUSINESS_ADDRESS",
        "BUSINESS_PHONE",
        "BUSINESS_REG_NO",
    ]
    filled = Settings(
        _env_file=None,
        business_address="예시시 예시구 1",
        business_phone="000-0000-0000",
        business_reg_no="000-00-00000",
    )
    assert missing_business_settings(filled) == []


# ── 나이 셈 ──


def test_ages_example_from_the_spec():
    # 2012-03생 → 알림 달 2026-03, 새 동의 필요 2026-04-01
    assert ages.parse_ym("2012-03") == (2012, 3)
    assert ages.effective_birth("2012-03") == date(2012, 4, 1)
    assert ages.effective_birth("2012-12") == date(2013, 1, 1)
    assert ages.turning_month("2012-03", 14) == date(2026, 3, 1)
    assert ages.reconsent_from("2012-03", 14) == date(2026, 4, 1)
    assert ages.reconsent_from("2012-03", 19) == date(2031, 4, 1)
    # 태어난 달의 다음 달 1일에 나이가 오른다(늦게 본다)
    assert ages.age_on("2012-03", date(2026, 3, 31)) == 13
    assert ages.age_on("2012-03", date(2026, 4, 1)) == 14
    # 아직 태어나지 않음(미래) → 0 보다 작다
    assert ages.age_on("2030-01", date(2026, 10, 23)) < 0
    for bad in ("2012-13", "2012-3", "12-03", "", "abcd-ef", None):
        try:
            ages.parse_ym(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_today_kst_turns_at_korean_midnight():
    assert ages.today_kst(datetime(2026, 3, 31, 14, 59, tzinfo=UTC)) == date(2026, 3, 31)
    assert ages.today_kst(datetime(2026, 3, 31, 15, 0, tzinfo=UTC)) == date(2026, 4, 1)
    assert ages.today_kst(datetime(2026, 3, 31, 15, 0)) == date(2026, 4, 1)  # 시간대 없음 = UTC
    assert isinstance(ages.today_kst(), date)


def test_consent_mode_and_minor_now():
    today = date(2026, 10, 23)
    alive = {"status": "alive", "relation": "딸"}
    assert ages.consent_mode({"status": "deceased", "relation": "할머니"}, today) is None
    assert ages.consent_mode({"status": "alive", "relation": "나 자신"}, today) is None
    assert ages.consent_mode({"status": "unable", "relation": "어머니"}, today) is None
    assert ages.consent_mode({"status": "alive", "relation": "어머니"}, today) == "adult"
    assert ages.consent_mode({**alive, "minor": False}, today) == "adult"
    child = {**alive, "minor": True, "birthYm": "2016-01"}  # 만 10세
    teen = {**alive, "minor": True, "birthYm": "2010-05"}  # 만 16세
    grown = {**alive, "minor": True, "birthYm": "2006-01"}  # 만 20세
    assert ages.consent_mode(child, today) == "minor_child"
    assert ages.consent_mode(teen, today) == "minor_teen"
    assert ages.consent_mode(grown, today) == "adult"
    # 미성년이라는데 생년월을 읽지 못하면 가장 엄격하게
    assert ages.consent_mode({**alive, "minor": True}, today) == "minor_child"
    assert ages.is_minor_now(child, today) and ages.is_minor_now(teen, today)
    assert not ages.is_minor_now(grown, today)
    assert not ages.is_minor_now({**alive, "minor": False}, today)
    # 떠나신 자녀는 고인 규칙(미성년 규칙을 켜지 않고 동의 녹음도 없다)
    gone = {**child, "status": "deceased"}
    assert not ages.is_minor_now(gone, today) and ages.consent_mode(gone, today) is None


# ── 앱 안 알림 ──


def test_persona_label_uses_the_address_term_in_quotes():
    named = Persona(person_id="x", name="나말순", basics={"addressTerm": " 할머니 "})
    assert notices.persona_label(named) == "'할머니' 대화 상대"
    assert notices.persona_label(Persona(person_id="x", name="나말순", basics={})) == (
        "'나말순' 대화 상대"
    )
    assert notices.persona_label(None) == "'그분' 대화 상대"


def test_notice_dedupe_mail_and_cleanup(monkeypatch):
    uid, pid = _user_and_persona({"addressTerm": "할머니"})
    sent: list[tuple] = []
    monkeypatch.setattr(
        mailer, "send_user", lambda to, subject, text: sent.append((to, subject, text)) or True
    )
    action = {"label": "다시 녹음하기", "route": f"/p/{pid}/consent"}

    async def make(db):
        first = await notices.create(
            db,
            uid,
            "subject_consent_rerequest",
            "제목",
            "본문",
            persona_id=pid,
            action=action,
            dedupe_key=f"t:{pid}",
            mail=True,
        )
        again = await notices.create(db, uid, "x", "제목2", "본문2", dedupe_key=f"t:{pid}")
        plain = await notices.create(db, uid, "x", "제목3", "본문3")
        await db.commit()
        return first.id, first.mailed_at, again, plain.mailed_at

    first_id, mailed_at, again, plain_mailed = _db(make)
    assert again is None and mailed_at is not None and plain_mailed is None
    assert len(sent) == 1
    to, subject, text = sent[0]
    assert to.endswith("@example.com") and subject == "[TalkTo] 제목"
    assert text.startswith("본문\n\n앱에서 확인해 주세요: ")
    assert text.endswith(f"{get_settings().frontend_url.rstrip('/')}/#/p/{pid}/consent")

    now = datetime.now(UTC)

    async def aged(db):
        rows = [
            OwnerNotice(
                user_id=uid,
                kind="x",
                title="오래 읽음",
                body="b",
                read_at=now - timedelta(days=181),
            ),
            OwnerNotice(
                user_id=uid,
                kind="x",
                title="최근 읽음",
                body="b",
                read_at=now - timedelta(days=179),
            ),
            OwnerNotice(
                user_id=uid,
                kind="x",
                title="안 읽음",
                body="b",
                created_at=now - timedelta(days=400),
            ),
            OwnerNotice(
                user_id=uid,
                kind="contract",
                title="계약",
                body="b",
                read_at=now - timedelta(days=400),
            ),
        ]
        db.add_all(rows)
        await db.commit()
        removed = await notices.cleanup(db, now)
        left = await db.scalars(select(OwnerNotice.title).where(OwnerNotice.user_id == uid))
        return removed, set(left)

    removed, left = _db(aged)
    assert removed >= 1
    assert "오래 읽음" not in left
    assert {"최근 읽음", "안 읽음", "계약", "제목", "제목3"} <= left


def test_notice_mail_is_skipped_without_settings_or_address():
    """설정이 꺼져 있으면(시험 기본) 메일 없이 알림만 남는다."""

    async def go(db):
        user = User(
            email=f"k-{uuid4().hex[:8]}@social.talkto.invalid", name="카", password_hash="x"
        )
        db.add(user)
        await db.flush()
        notice = await notices.create(db, user.id, "x", "제목", "본문", mail=True)
        await db.commit()
        return notice.mailed_at

    assert _db(go) is None


def test_notices_api_lists_reads_and_hides_others():
    with TestClient(app) as client:
        h = _signup(client, f"n-{uuid4().hex[:8]}@example.com")
        other = _signup(client, f"o-{uuid4().hex[:8]}@example.com")
        me = client.get(f"{API}/auth/me", headers=h).json()["data"]["id"]
        base = datetime.now(UTC)

        async def seed(db):
            rows = [
                OwnerNotice(
                    user_id=me,
                    kind="x",
                    title=f"알림 {n}",
                    body="b",
                    created_at=base + timedelta(seconds=n),
                )
                for n in range(3)
            ]
            rows[0].read_at = base
            db.add_all(rows)
            await db.commit()
            return [r.id for r in rows]

        ids = _db(seed)
        assert client.get(f"{API}/notices").status_code == 401
        listed = client.get(f"{API}/notices", headers=h).json()
        assert [n["title"] for n in listed["data"]] == ["알림 2", "알림 1", "알림 0"]
        assert listed["meta"]["unread"] == 2
        first = listed["data"][0]
        assert set(first) == {
            "id",
            "kind",
            "title",
            "body",
            "action",
            "personaId",
            "createdAt",
            "readAt",
        }
        unread = client.get(f"{API}/notices?unread=1", headers=h).json()["data"]
        assert {n["id"] for n in unread} == {ids[1], ids[2]}
        # 다른 사람의 알림은 없는 것과 같다
        assert client.post(f"{API}/notices/{ids[1]}/read", headers=other).status_code == 404
        assert client.get(f"{API}/notices", headers=other).json()["data"] == []
        assert client.post(f"{API}/notices/{ids[1]}/read", headers=h).json()["data"] == {"ok": True}
        assert client.get(f"{API}/notices", headers=h).json()["meta"]["unread"] == 1
        assert client.post(f"{API}/notices/read-all", headers=h).status_code == 200
        assert client.get(f"{API}/notices", headers=h).json()["meta"]["unread"] == 0
        assert client.post(f"{API}/notices/nope/read", headers=h).status_code == 404


# ── 고객 메일 ──


def _user_mail_on(monkeypatch):
    on = get_settings().model_copy(
        update={
            "environment": "production",
            "mail_from": "noreply@example.com",
            "user_mail_enabled": True,
        }
    )
    monkeypatch.setattr(mailer, "get_settings", lambda: on)


def test_send_user_needs_settings_and_a_real_address(monkeypatch):
    assert mailer.user_mail_configured() is False
    assert mailer.send_user("a@example.com", "s", "t") is False
    sent: list[dict] = []

    class FakeSES:
        def send_email(self, **kwargs):
            sent.append(kwargs)

    _user_mail_on(monkeypatch)
    monkeypatch.setattr(mailer, "_client", lambda: FakeSES())
    assert mailer.user_mail_configured() is True

    async def go():
        results = [
            mailer.send_user(None, "s", "t"),
            mailer.send_user("  ", "s", "t"),
            mailer.send_user("x@social.talkto.invalid", "s", "t"),
            mailer.send_user("gone@deleted.talkto.invalid", "s", "t"),
            mailer.send_user("a@example.com", "[TalkTo] 제목", "본문"),
        ]
        await mailer.wait_all()
        return results

    assert asyncio.run(go()) == [False, False, False, False, True]
    assert len(sent) == 1
    assert sent[0]["Destination"] == {"ToAddresses": ["a@example.com"]}
    assert sent[0]["FromEmailAddress"] == "noreply@example.com"
    assert sent[0]["Content"]["Simple"]["Subject"]["Data"] == "[TalkTo] 제목"
    # 꺼 두면(SES 운영 권한 전) 보내지 않는다
    off = get_settings().model_copy(
        update={"environment": "production", "mail_from": "noreply@example.com"}
    )
    monkeypatch.setattr(mailer, "get_settings", lambda: off)
    assert mailer.send_user("a@example.com", "s", "t") is False


def test_send_user_failure_logs_only_the_kind(monkeypatch, caplog):
    class BrokenSES:
        def send_email(self, **kwargs):
            raise RuntimeError("거절 secret@example.com")

    _user_mail_on(monkeypatch)
    monkeypatch.setattr(mailer, "_client", lambda: BrokenSES())

    async def go():
        assert mailer.send_user("secret@example.com", "제목", "비밀 본문") is True
        await mailer.wait_all()

    with caplog.at_level(logging.INFO, logger="app.services.mailer"):
        asyncio.run(go())
    assert "user mail failed: RuntimeError" in caplog.text
    assert "secret@example.com" not in caplog.text and "비밀 본문" not in caplog.text


# ── 게이트 오류 모양 ──


def test_gate_error_becomes_detail_and_code():
    at = datetime(2026, 10, 30, 3, 0, tzinfo=UTC)
    error = gate.GateError(409, "stopped", "이 대화 상대는 지금 멈췄어요.", deleteAt=at)
    response = asyncio.run(main_module.gate_error(None, error))
    assert response.status_code == 409
    assert json.loads(response.body) == {
        "detail": "이 대화 상대는 지금 멈췄어요.",
        "code": "stopped",
        "deleteAt": "2026-10-30T03:00:00+00:00",
    }
    member = gate.GateError(403, "share_paused", "지금은 이 대화 상대와 이야기할 수 없어요.")
    assert asyncio.run(main_module.gate_error(None, member)).status_code == 403


def test_gate_contract_shapes():
    """게이트 계약(§2.2): 막히면 block 은 Block, require 는 GateError(409), check_job 은 ValueError.
    그분 동의 녹음이 통과하면 열린다(자세한 표는 test_gates)."""
    _, pid = _user_and_persona({"status": "alive", "relation": "어머니", "minor": False})

    async def blocked(db):
        persona = await db.get(Persona, pid)
        found = await gate.block(db, persona, "build")
        assert found is not None and found.code == "subject_consent"
        with pytest.raises(gate.GateError) as error:
            await gate.require(db, persona, "voice")
        assert error.value.status_code == 409 and error.value.code == "subject_consent"
        with pytest.raises(ValueError):
            await gate.check_job(db, pid, "audio")
        return await gate.summary(db, persona)

    summary = _db(blocked)
    assert summary["build"] is False and summary["chat"] is True
    assert summary["code"] == "subject_consent"
    pass_subject_consent(None, pid)

    async def opened(db):
        persona = await db.get(Persona, pid)
        assert await gate.block(db, persona, "build") is None
        await gate.require(db, persona, "voice")
        await gate.check_job(db, pid, "audio")
        assert await gate.actions(db, persona) == []
        return await gate.summary(db, persona)

    assert _db(opened)["build"] is True


# ── 하루 정리 고리 ──


def test_cleanup_daily_runs_legal_steps_even_if_one_fails(monkeypatch):
    now = datetime.now(UTC)
    called: list[str] = []

    async def fake_minors(db, at):
        called.append("minors")
        return 0

    async def broken_deletion(db, at):
        raise RuntimeError("boom")

    monkeypatch.setattr(minors, "sweep", fake_minors)
    monkeypatch.setattr(deletion, "cleanup", broken_deletion)
    pid = str(uuid4())

    async def seed(db):
        db.add_all(
            [
                PersonaObjection(
                    persona_id=pid,
                    reason="objection",
                    received_at=now,
                    outcome="deleted",
                    outcome_at=now - timedelta(days=3 * 365 + 1),
                ),
                PersonaObjection(
                    persona_id=pid,
                    reason="prior_wish",
                    received_at=now,
                    outcome="cancelled",
                    outcome_at=now - timedelta(days=30),
                ),
                PersonaObjection(
                    persona_id=pid, reason="other_parent", received_at=now - timedelta(days=4 * 365)
                ),
            ]
        )
        await db.commit()

    _db(seed)
    monkeypatch.setattr(main_module, "_last_cleanup", None)
    assert asyncio.run(main_module.cleanup_daily(now)) is True
    assert called == ["minors"]

    async def left(db):
        rows = await db.scalars(
            select(PersonaObjection.reason).where(PersonaObjection.persona_id == pid)
        )
        return set(rows)

    # 결과가 정해진 지 3년 지난 것만 지운다(아직 열린 이의는 남긴다)
    assert _db(left) == {"prior_wish", "other_parent"}


# ── 계약으로 채워 둔 것과 시험 도우미 ──


def test_subject_consent_module_never_touches_the_ai_server():
    source = (ROOT / "app" / "services" / "subject_consent.py").read_text(encoding="utf-8")
    assert "ai_service" not in source and "app.services.ai" not in source
    assert "from app.services import ai" not in source


def test_legal_gate_helpers_fill_the_new_tables():
    uid, pid = _user_and_persona({"status": "deceased", "relation": "할머니"})
    assert declare_bereaved(None, pid)
    assert pass_subject_consent(None, pid)
    assert guardian_ok(None, pid)

    async def check(db):
        persona = await db.get(Persona, pid)
        decl = await bereaved.get(db, pid)
        assert decl is not None and decl.none_left and decl.declared_by == uid
        assert await subject_consent.passed(db, persona, "adult")
        assert not await subject_consent.passed(db, persona, "minor_teen")
        assert not await subject_consent.passed(db, persona, None)
        for prefix in ("guardian_attest", "guardian_consent", "guardian_overseas"):
            assert await legal.has(db, uid, legal.persona_type(prefix, pid))
        assert await legal.has(db, uid, legal.persona_type("bereaved_pledge", pid))
        check_row = await db.scalar(
            select(PersonaMinorCheck).where(PersonaMinorCheck.persona_id == pid)
        )
        assert check_row.sms_verified_at is not None
        # 재동의는 시작(그다음 달 1일) 전에는 지금 나이의 통과를 그대로 세고, 시작한 뒤에는 그 뒤에
        # 검수한 통과만 센다
        check_row.reconsent_from = datetime.now(UTC) + timedelta(days=1)
        await db.commit()
        assert await subject_consent.passed(db, persona, "adult")
        check_row.reconsent_from = datetime.now(UTC)
        await db.commit()
        assert not await subject_consent.passed(db, persona, "adult")
        await pass_subject_consent(db, pid, at=datetime.now(UTC) + timedelta(seconds=1))
        assert await subject_consent.passed(db, persona, "adult")
        attempts = await db.scalars(
            select(SubjectConsentRecording.attempt).where(SubjectConsentRecording.persona_id == pid)
        )
        assert sorted(attempts) == [1, 2]
        # 명단이 비고 '적을 분이 없어요'도 아니면 선언으로 보지 않는다
        row = await db.scalar(
            select(BereavedDeclaration).where(BereavedDeclaration.persona_id == pid)
        )
        row.none_left = False
        await db.commit()
        assert await bereaved.get(db, pid) is None
        await declare_bereaved(db, pid, members=[{"name": "나", "relation": "son"}])
        decl = await bereaved.get(db, pid)
        return decl.none_left, decl.members

    none_left, members = _db(check)
    assert none_left is False and members == [{"name": "나", "relation": "son"}]


def test_record_attest_writes_the_guardian_attest_record():
    uid, pid = _user_and_persona({"status": "alive", "relation": "딸", "minor": True})

    async def go(db):
        user = await db.get(User, uid)
        persona = await db.get(Persona, pid)
        await minors.record_attest(db, user, persona)
        await db.commit()
        # 법정대리인 확인은 채워졌고(태어난 해·달이 없으면 가장 어린 쪽으로 본다) 나머지가 남는다
        state = await minors.state(db, persona)
        assert "attest" not in state.missing and not state.guardian_ok
        return await legal.has(db, uid, legal.persona_type("guardian_attest", pid))

    assert _db(go) is True


def test_deleting_a_persona_removes_its_new_rows():
    uid, pid = _user_and_persona({"status": "alive", "relation": "어머니"})
    declare_bereaved(None, pid)
    pass_subject_consent(None, pid)
    guardian_ok(None, pid)

    async def go(db):
        persona = await db.get(Persona, pid)
        person = await db.get(Person, persona.person_id)
        await db.delete(person)
        await db.commit()
        counts = []
        for model in (SubjectConsentRecording, PersonaMinorCheck, BereavedDeclaration):
            rows = await db.scalars(select(model.id).where(model.persona_id == pid))
            counts.append(len(list(rows)))
        return counts

    assert _db(go) == [0, 0, 0]


def test_phase0_stubs_are_safe_to_call_from_the_periodic_loop():
    async def go(db):
        now = datetime.now(UTC)
        return [
            await minors.sweep(db, now),
            await deletion.cleanup(db, now),
        ]

    assert _db(go) == [0, 0]
    from app.services import contract_mail, holds

    assert _db(lambda db: holds.sweep(db, datetime.now(UTC))) == 0
    assert asyncio.run(contract_mail.after_payment("nope")) is None
