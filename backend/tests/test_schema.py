"""시작할 때 DB 를 고치는 일(새 칸 붙이기·만든 날 채우기·없앤 요금제 정리)과 진행률 막대 규칙."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.db import session as db_session
from app.db.base import Base
from app.db.models import Persona, PersonaJob
from app.services.jobs import next_percent


def _columns(connection, table: str) -> set[str]:
    return {c["name"] for c in inspect(connection).get_columns(table)}


def test_missing_columns_are_added_and_made_date_is_filled(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        # ready_at·percent 가 생기기 전의 DB 처럼
        connection.exec_driver_sql('ALTER TABLE "personas" DROP COLUMN "ready_at"')
        connection.exec_driver_sql('ALTER TABLE "persona_jobs" DROP COLUMN "percent"')
        db_session._add_missing_columns(connection)
        assert "ready_at" in _columns(connection, "personas")
        assert "percent" in _columns(connection, "persona_jobs")

    made = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    built = made + timedelta(days=2)
    with Session(engine) as db:  # 이 엔진은 외래 키를 켜지 않아 사람·사용자 없이 넣는다
        built_one = Persona(person_id="p1", name="가", setup_step="READY", created_at=made)
        old_one = Persona(person_id="p2", name="나", setup_step="READY", created_at=made)
        draft = Persona(person_id="p3", name="다", setup_step="QUESTIONS", created_at=made)
        db.add_all([built_one, old_one, draft])
        db.flush()
        db.add_all(
            [
                PersonaJob(persona_id=built_one.id, kind="BUILD", status="DONE", updated_at=built),
                PersonaJob(
                    persona_id=built_one.id,
                    kind="BUILD",
                    status="DONE",
                    updated_at=built + timedelta(days=5),
                ),
            ]
        )
        db.commit()
        ids = built_one.id, old_one.id, draft.id

    with engine.begin() as connection:
        db_session._backfill_ready_at(connection)
        db_session._backfill_ready_at(connection)  # 두 번 해도 같다
    with Session(engine) as db:
        got = {p.id: p.ready_at for p in db.query(Persona)}
    as_utc = lambda value: value.replace(tzinfo=UTC) if value.tzinfo is None else value  # noqa: E731
    assert as_utc(got[ids[0]]) == built  # 처음 '만들기'가 끝난 때
    assert as_utc(got[ids[1]]) == made  # 만들기 기록이 없으면 만든 때
    assert got[ids[2]] is None  # 아직 다 만들지 않았다
    engine.dispose()


def test_retired_premium_rows_are_fixed_once(tmp_path):
    """없앤 똑 닮은 목소리(premium) 요금제의 남은 줄: 신청은 취소하고 연락처를 지우고, 구독은 가족
    요금제로 옮기고(낸 것은 기간까지, 밀린 것은 끝냄), 열린 결제 창은 닫는다. 기록은 남긴다."""
    from app.db.models import CheckoutSession, Payment, Subscription, SubscriptionRequest

    engine = create_engine(f"sqlite:///{(tmp_path / 'premium.db').as_posix()}")
    Base.metadata.create_all(engine)
    later = datetime.now(UTC) + timedelta(days=10)
    with Session(engine) as db:  # 외래 키를 켜지 않은 엔진이라 사용자·대화 상대 없이 넣는다
        db.add_all(
            [
                SubscriptionRequest(
                    id="r1",
                    user_id="u",
                    persona_id="p1",
                    plan="premium",
                    status="REQUESTED",
                    contact_phone="010-1234-5678",
                    contact_time="저녁",
                ),
                SubscriptionRequest(id="r2", user_id="u", persona_id="p2", plan="family"),
                SubscriptionRequest(
                    id="r3", user_id="u", persona_id="p3", plan="premium", status="CANCELED"
                ),
            ]
        )

        def sub(sid: str, plan: str, status: str, **kw) -> Subscription:
            return Subscription(
                id=sid,
                user_id="u",
                persona_id=sid,
                plan=plan,
                status=status,
                provider="mock",
                customer_key="ck",
                current_period_end=later,
                **kw,
            )

        db.add_all(
            [
                sub("s-active", "premium", "ACTIVE"),
                sub("s-pastdue", "premium", "PAST_DUE"),
                sub("s-ended", "premium", "CANCELED"),
                sub("s-down", "memories", "ACTIVE", next_plan="premium"),
                sub("s-family", "family", "ACTIVE"),
            ]
        )
        for cid, plan, status in (("c1", "premium", "OPEN"), ("c2", "family", "OPEN")):
            db.add(
                CheckoutSession(
                    id=cid,
                    user_id="u",
                    plan=plan,
                    status=status,
                    customer_key="ck",
                    return_to="https://app",
                    expires_at=later,
                )
            )
        db.add(
            Payment(
                user_id="u",
                plan="premium",
                provider="mock",
                order_id="o1",
                amount=54900,
                status="DONE",
            )
        )
        db.commit()

    with engine.begin() as connection:
        db_session._retire_premium(connection)
        db_session._retire_premium(connection)  # 두 번 해도 같다
    with Session(engine) as db:
        requests = {r.id: r for r in db.query(SubscriptionRequest)}
        assert (requests["r1"].status, requests["r1"].contact_phone) == ("CANCELED", None)
        assert requests["r1"].contact_time is None and requests["r1"].plan == "premium"  # 기록
        assert requests["r2"].status == "REQUESTED"
        subs = {s.id: s for s in db.query(Subscription)}
        assert {s.plan for s in subs.values()} == {"family", "memories"}
        assert subs["s-active"].status == "ACTIVE" and subs["s-active"].cancel_at_period_end
        assert subs["s-pastdue"].status == "CANCELED"
        assert subs["s-down"].next_plan is None and subs["s-down"].plan == "memories"
        assert not subs["s-family"].cancel_at_period_end
        sessions = {c.id: c.status for c in db.query(CheckoutSession)}
        assert sessions == {"c1": "EXPIRED", "c2": "OPEN"}
        assert db.query(Payment).one().plan == "premium"  # 낸 기록은 그대로
    engine.dispose()


def test_production_database_only_gets_listed_columns(monkeypatch):
    """운영(PostgreSQL)은 ADDED 에 적은 칸만 붙이고, 잠금을 오래 기다리지 않는다."""
    missing = {("personas", "ready_at"), ("users", "beta_until")}
    tables = {t.name: t for t in Base.metadata.sorted_tables}
    assert tables["users"].c.beta_until.nullable  # 빈칸 허용이어도 목록에 없으면 안 붙인다

    class FakeInspector:
        def has_table(self, name):
            return True

        def get_columns(self, name):
            return [{"name": c.name} for c in tables[name].columns if (name, c.name) not in missing]

    ran: list[str] = []
    connection = SimpleNamespace(
        dialect=postgresql.dialect(), exec_driver_sql=lambda sql: ran.append(sql)
    )
    monkeypatch.setattr(db_session, "inspect", lambda _connection: FakeInspector())
    db_session._add_missing_columns(connection)
    assert ran == [
        "SET LOCAL lock_timeout = '5s'",
        'ALTER TABLE "personas" ADD COLUMN IF NOT EXISTS "ready_at" TIMESTAMP WITH TIME ZONE',
    ]


def test_production_database_gets_the_speech_text_column(monkeypatch):
    """음성 감정(AI speechText): 운영 messages 표에 빈칸 허용 TEXT 칸 하나만 붙인다
    (표를 다시 쓰지 않는다)."""
    tables = {t.name: t for t in Base.metadata.sorted_tables}
    assert tables["messages"].c.speech_text.nullable

    class FakeInspector:
        def has_table(self, name):
            return True

        def get_columns(self, name):
            return [
                {"name": c.name}
                for c in tables[name].columns
                if (name, c.name) != ("messages", "speech_text")
            ]

    ran: list[str] = []
    connection = SimpleNamespace(
        dialect=postgresql.dialect(), exec_driver_sql=lambda sql: ran.append(sql)
    )
    monkeypatch.setattr(db_session, "inspect", lambda _connection: FakeInspector())
    db_session._add_missing_columns(connection)
    assert ran == [
        "SET LOCAL lock_timeout = '5s'",
        'ALTER TABLE "messages" ADD COLUMN IF NOT EXISTS "speech_text" TEXT',
    ]


def test_progress_bar_never_goes_back_or_says_done_early():
    assert next_percent(None, 12.4) == 12
    assert next_percent(50, 30) == 50  # 뒤로 가지 않는다
    assert next_percent(10, 150) == 99  # 끝나기 전에는 100 이 아니다
    assert next_percent(None, -5) == 0
    assert next_percent(99, 99.6) == 99


def test_sqlite_still_adds_any_nullable_column(tmp_path):
    """개발용 SQLite 는 예전처럼 빈칸 허용 칸을 다 붙인다."""
    engine = create_engine(f"sqlite:///{(tmp_path / 'dev.db').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql('ALTER TABLE "users" DROP COLUMN "beta_until"')
        db_session._add_missing_columns(connection)
        assert "beta_until" in _columns(connection, "users")
        assert connection.execute(text("SELECT COUNT(*) FROM users")).scalar() == 0
    engine.dispose()


def test_long_ai_calls_creep_the_bar_and_stop_cleanly(monkeypatch):
    """녹음 하나를 오래 옮기는 동안에도 막대가 조금씩 움직이고, 끝나면 더 적지 않는다."""
    import asyncio

    from app.services import persona_builder

    monkeypatch.setattr(persona_builder, "BEAT_SECONDS", 0.01)
    seen: list[float] = []

    async def progress(detail, percent=None):
        await asyncio.sleep(0)
        seen.append(percent)

    async def run():
        async with persona_builder._creep(progress, "옮기는 중", 10, 50, expect_seconds=0.05):
            await asyncio.sleep(0.12)
        count = len(seen)
        await asyncio.sleep(0.05)
        return count

    count = asyncio.run(run())
    assert count >= 3 and len(seen) == count  # 빠져나온 뒤에는 적지 않는다
    # 윈도의 time.monotonic 은 15.6ms 단위라 컴퓨터가 바쁘면 첫 박자가 아직 0초로 읽힐 수 있다
    # (그때 첫 값은 꼭 lo). 막대가 움직였는지는 마지막 값으로 본다.
    assert seen == sorted(seen) and 10 <= seen[0] and 10 < seen[-1] <= 10 + 40 * 0.9

    async def failing():
        async with persona_builder._creep(progress, "옮기는 중", 0, 10, expect_seconds=1):
            raise ValueError("AI 오류")

    try:
        asyncio.run(failing())
    except ValueError as error:
        assert str(error) == "AI 오류"  # 원래 오류가 그대로 올라온다
    else:
        raise AssertionError("오류가 사라졌다")


def test_family_share_table_comes_from_create_all_and_no_column_is_added(tmp_path):
    """가족 공유는 새 표(persona_shares) 하나다. 운영은 create_all 이 만들고, 있는 표에는 칸·색인을
    더하지 않는다(ADDED 에 가족 공유 칸은 없다). 그 뒤 음성 감정으로 messages.speech_text
    하나가 늘었다."""
    engine = create_engine(f"sqlite:///{(tmp_path / 'share.db').as_posix()}")
    Base.metadata.create_all(engine)
    with engine.connect() as connection:
        inspector = inspect(connection)
        assert inspector.has_table("persona_shares")
        assert _columns(connection, "persona_shares") == {
            "id", "persona_id", "token_hash", "expires_at", "prefill", "user_id", "joined_at",
            "profile", "instructions", "instructions_base", "created_at", "updated_at",
        }  # fmt: skip
        uniques = {
            tuple(u["column_names"]) for u in inspector.get_unique_constraints("persona_shares")
        }
        indexes = {
            tuple(i["column_names"]): bool(i["unique"])
            for i in inspector.get_indexes("persona_shares")
        }
        assert ("persona_id", "user_id") in uniques
        assert indexes.get(("token_hash",)) is True
        assert ("persona_id",) in indexes and ("user_id",) in indexes
        foreign = {
            fk["referred_table"]: fk["options"].get("ondelete")
            for fk in inspector.get_foreign_keys("persona_shares")
        }
        assert foreign == {"personas": "CASCADE", "users": "CASCADE"}
    engine.dispose()
    assert db_session.ADDED == [
        ("personas", "ready_at"),
        ("personas", "origin"),
        ("persona_jobs", "percent"),
        ("subscription_requests", "contact_phone"),
        ("subscription_requests", "contact_time"),
        ("messages", "speech_text"),
        # 대화 고치기(2026-10-21): 평가의 고침·반영 상태, '내용이 틀려요' 기억 표시
        ("feedback", "correction"),
        ("feedback", "state"),
        ("feedback", "persona_id"),
        ("feedback", "memory_id"),
        ("feedback", "applied_at"),
        ("feedback", "decided_at"),
        ("memories", "flagged_wrong"),
        # 법률 자문 반영(2026-10-22): 멈춤·자동 삭제 예약, 청약철회·환불 판단(test_phase0_schema)
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


def test_feedback_tables_and_columns_come_without_hand_migration(tmp_path):
    """대화 고치기·'더 알려 주실 기억'의 새 표(gap_questions·style_examples·conversation_feedback)는
    create_all 이 만들고, 있던 표(feedback·memories)의 새 칸은 모두 빈칸 허용이라 저절로 붙는다."""
    engine = create_engine(f"sqlite:///{(tmp_path / 'feedback.db').as_posix()}")
    Base.metadata.create_all(engine)
    added = [pair for pair in db_session.ADDED if pair[0] in ("feedback", "memories")]
    with engine.begin() as connection:
        # 예전 DB 처럼(그때는 feedback.persona_id 칸도 그 색인도 없었다)
        connection.exec_driver_sql('DROP INDEX IF EXISTS "ix_feedback_persona_id"')
        for table, column in added:
            connection.exec_driver_sql(f'ALTER TABLE "{table}" DROP COLUMN "{column}"')
        db_session._add_missing_columns(connection)
        for table, column in added:
            assert column in _columns(connection, table), (table, column)
        inspector = inspect(connection)
        for table in ("gap_questions", "style_examples", "conversation_feedback"):
            assert inspector.has_table(table), table
    engine.dispose()
    for table, column in added:
        assert Base.metadata.tables[table].columns[column].nullable, (table, column)
