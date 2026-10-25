import logging
from collections.abc import AsyncIterator

from sqlalchemy import event, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import Base

settings = get_settings()
logger = logging.getLogger(__name__)


def normalize_database_url(database_url: str) -> str:
    """Use SQLAlchemy's async PostgreSQL driver for provider connection URLs."""
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)

    if not database_url.startswith("postgresql+asyncpg://"):
        return database_url

    url = make_url(database_url)
    ssl_mode = url.query.get("sslmode")
    url = url.difference_update_query(["sslmode", "channel_binding"])
    if ssl_mode:
        url = url.update_query_dict({"ssl": ssl_mode})
    return url.render_as_string(hide_password=False)


def pool_options(database_url: str) -> dict:
    """연결 풀 크기. 운영(PostgreSQL)은 정해 둔다: 뒤에서 도는 만들기 작업·결제 확인과 대화 요청이
    한 풀을 나눠 쓴다(기본 5+10 이면 대답을 기다리는 대화가 몰릴 때 모자랐다). RDS 연결 상한
    (db.t4g.micro 기준 수십 개) 안이다."""
    if database_url.startswith("postgresql+asyncpg://"):
        return {"pool_size": 10, "max_overflow": 10, "pool_timeout": 30}
    return {}


_url = normalize_database_url(settings.database_url)
# hide_parameters: DB 오류 글에 넣은 값(이메일·비밀번호 해시 등)이 실려 로그·오류 기록에 남지 않게
engine = create_async_engine(
    _url, pool_pre_ping=True, hide_parameters=True, **pool_options(_url)
)

if engine.dialect.name == "sqlite":
    # SQLite 는 외래 키를 꺼 둔 채로 연다. 켜야 사람을 지울 때 녹음·기억·대화가 같이 지워진다.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def create_schema() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_add_missing_columns)
        await connection.run_sync(_widen_columns)
        await connection.run_sync(_backfill_ready_at)
        await connection.run_sync(_retire_premium)


# 운영 DB(PostgreSQL)에 저절로 붙여도 되는 새 칸(표, 칸). 빈칸 허용·기본값 없는 칸만 넣는다.
# 여기 없는 칸이 모자라면 붙이지 않고 기록만 남긴다(따로 옮긴다).
ADDED = [
    ("personas", "ready_at"),
    ("personas", "origin"),
    ("persona_jobs", "percent"),
    ("subscription_requests", "contact_phone"),
    ("subscription_requests", "contact_time"),
    ("messages", "speech_text"),
    # 대화 고치기(2026-10-21): 말풍선 평가의 고침 한 줄·반영 상태와 '내용이 틀려요' 기억 표시
    ("feedback", "correction"),
    ("feedback", "state"),
    ("feedback", "persona_id"),
    ("feedback", "memory_id"),
    ("feedback", "applied_at"),
    ("feedback", "decided_at"),
    ("memories", "flagged_wrong"),
    # 법률 자문 반영(2026-10-22): 대화 상대 멈춤·자동 삭제 예약과 결제의 청약철회·환불 판단
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


def _add_missing_columns(connection) -> None:
    """새로 생긴 빈칸 허용 칸을 있는 표에 붙인다. create_all 은 있는 표를 고치지 않는다.

    마이그레이션 도구를 들이기 전까지의 임시 방편이다. SQLite(개발)는 빈칸 허용 칸을 다 붙이고,
    PostgreSQL(운영)은 ADDED 에 적은 칸만 붙인다. 기본값 없이 붙여서 표를 다시 쓰지 않고 바로
    끝나며, 다른 일이 표를 오래 잡고 있으면 5초 만에 포기한다(서버가 다시 뜨며 또 해 본다).
    """
    dialect = connection.dialect.name
    if dialect not in ("sqlite", "postgresql"):
        return
    inspector = inspect(connection)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        have = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            if dialect == "postgresql" and (table.name, column.name) not in ADDED:
                logger.warning("column %s.%s is missing; add it by hand", table.name, column.name)
                continue
            if not column.nullable:
                continue
            kind = column.type.compile(dialect=connection.dialect)
            if dialect == "postgresql":
                connection.exec_driver_sql("SET LOCAL lock_timeout = '5s'")
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS "{column.name}" {kind}'
                )
            else:
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}'
                )


def _backfill_ready_at(connection) -> None:
    """ready_at 이 생기기 전에 다 만든 대화 상대: 처음 '만들기'가 끝난 때(없으면 만든 때)로 채운다.

    한 번 채우면 다시 할 일이 없다(새로 다 만든 대화 상대는 만들 때 채운다).
    """
    connection.exec_driver_sql(
        "UPDATE personas SET ready_at = COALESCE("
        "(SELECT MIN(j.updated_at) FROM persona_jobs j WHERE j.persona_id = personas.id"
        " AND j.kind = 'BUILD' AND j.status = 'DONE'), personas.created_at)"
        " WHERE ready_at IS NULL AND setup_step = 'READY'"
    )


# 똑 닮은 목소리(premium) 요금제를 없앴다(2026-10-17). 남은 줄을 켤 때마다 고친다(한 번 고치면
# 할 일이 없다). 기록(결제·신청·동의)은 지우지 않는다. 결제 기록의 plan 은 그때 낸 그대로 둔다.
RETIRE_PREMIUM = [
    # 신청 중인 똑 닮은 목소리 구독 신청은 취소한다
    (
        "premium requests canceled",
        "UPDATE subscription_requests SET status = 'CANCELED'"
        " WHERE plan = 'premium' AND status = 'REQUESTED'",
    ),
    # 인터뷰 연락처는 똑 닮은 목소리 신청에만 받았다. 그 이용이 끝나면 지운다고 했다
    # (contact_interview 문서)
    (
        "request contacts cleared",
        "UPDATE subscription_requests SET contact_phone = NULL, contact_time = NULL"
        " WHERE contact_phone IS NOT NULL OR contact_time IS NOT NULL",
    ),
    (
        "premium next plans cleared",
        "UPDATE persona_subscriptions SET next_plan = NULL WHERE next_plan = 'premium'",
    ),
    # 결제가 밀린(값을 내지 않은) 구독은 끝낸다
    (
        "unpaid premium subscriptions canceled",
        "UPDATE persona_subscriptions SET plan = 'family', status = 'CANCELED'"
        " WHERE plan = 'premium' AND status = 'PAST_DUE'",
    ),
    # 낸 기간은 가족 요금제로 그대로 쓴다. 고르지 않은 요금제로 갱신 결제하지 않게 기간이 끝나면
    # 멈춘다(요금제 화면에서 다시 고른다)
    (
        "active premium subscriptions moved",
        "UPDATE persona_subscriptions SET plan = 'family', cancel_at_period_end = TRUE"
        " WHERE plan = 'premium' AND status = 'ACTIVE'",
    ),
    (
        "ended premium subscriptions relabelled",
        "UPDATE persona_subscriptions SET plan = 'family' WHERE plan = 'premium'",
    ),
    (
        "premium checkouts expired",
        "UPDATE checkout_sessions SET status = 'EXPIRED'"
        " WHERE plan = 'premium' AND status = 'OPEN'",
    ),
]


def _retire_premium(connection) -> None:
    """없앤 똑 닮은 목소리 요금제의 남은 줄을 고친다. 고친 줄 수만 남긴다(내용·번호는 빼고).

    요금제 목록(billing.plans)에 premium 이 없어서, 남은 줄이 있으면 요금제 보기·갱신이 깨진다.
    서버가 켜질 때 처음 요금제를 보기 전에 돈다(lifespan → create_schema).
    """
    for what, sql in RETIRE_PREMIUM:
        changed = connection.exec_driver_sql(sql).rowcount
        if changed and changed > 0:
            logger.info("retired premium plan: %s %s", changed, what)


# 길이를 늘린 글자 칸(표, 칸, 새 길이). create_all 은 있는 칸을 고치지 않는다.
WIDENED = [("consents", "consent_type", 80)]


def _widen_columns(connection) -> None:
    """PostgreSQL 에서 늘린 글자 칸을 고친다. SQLite 는 길이를 따지지 않아 그대로 둔다."""
    if connection.dialect.name != "postgresql":
        return
    inspector = inspect(connection)
    for table, column, length in WIDENED:
        if not inspector.has_table(table):
            continue
        for info in inspector.get_columns(table):
            current = getattr(info["type"], "length", None)
            if info["name"] == column and current is not None and current < length:
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE VARCHAR({length})'
                )
