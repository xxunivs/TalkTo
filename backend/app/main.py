import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError

from app.api.deps import ConsentRequired
from app.api.v1.router import api_router
from app.core.body_limit import BodySizeLimit
from app.core.config import (
    get_settings,
    missing_business_settings,
    unsafe_production_settings,
)
from app.core.security import hash_password, run_hashing
from app.db.models import (
    ErrorLog,
    Person,
    Persona,
    PersonaObjection,
    PersonaShare,
    User,
    UserRole,
    UserStatus,
)
from app.db.session import SessionLocal, create_schema
from app.services import (
    analytics,
    billing,
    deletion,
    gate,
    holds,
    interview,
    jobs,
    minors,
    notices,
    notify,
    persona_builder,
    sharing,
    voice_retrain,
)
from app.services.ai import PROVIDER_CODES, VOICE_PROVIDER_CODES, AIServerError
from app.services.storage import LocalStorage, get_storage, verify_signature

settings = get_settings()
logger = logging.getLogger(__name__)


class AccessLogPrivacy(logging.Filter):
    """uvicorn 접근 기록에서 주소의 물음표 뒤(query)를 뺀다.

    소셜 로그인 콜백(?code=…&state=…), 결제 콜백(authKey·customerKey), 관리자 검색(?q=이메일)처럼
    한 번 쓰는 열쇠나 개인정보가 CloudWatch 에 남지 않게. 경로·상태 코드는 그대로 남긴다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        # uvicorn: '%s - "%s %s HTTP/%s" %d' (client, method, full_path, http_version, status)
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str):
            path = args[2].split("?", 1)[0]
            record.args = (args[0], args[1], path, args[3], args[4])
        return True


def install_access_log_filter() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessLogPrivacy) for f in access.filters):
        access.addFilter(AccessLogPrivacy())


install_access_log_filter()


async def bootstrap() -> None:
    async with SessionLocal() as db:
        admin = await db.scalar(
            select(User).where(User.email == settings.bootstrap_admin_email.lower())
        )
        if admin is None:
            admin = User(
                email=settings.bootstrap_admin_email.lower(),
                name="TalkTo Admin",
                password_hash=await run_hashing(
                    hash_password, settings.bootstrap_admin_password
                ),
                role=UserRole.ADMIN,
                status=UserStatus.ACTIVE,
            )
            db.add(admin)
            await db.flush()
            person = Person(
                owner_user_id=admin.id, name="TalkTo Persona", relationship_label="가족"
            )
            db.add(person)
            await db.flush()
            db.add(
                Persona(person_id=person.id, name="TalkTo Persona", status="READY", is_active=True)
            )
            await db.commit()


CLEANUP_EVERY = timedelta(days=1)
_last_cleanup: datetime | None = None


ERROR_LOG_RETENTION = timedelta(days=365)  # 개인정보 수집·이용 안내: 오류 기록은 1년
# 개인정보 처리방침: 이의 접수 기록은 결과가 정해진 뒤 3년
OBJECTION_RETENTION = timedelta(days=3 * 365)


async def cleanup_daily(at: datetime | None = None) -> bool:
    """하루 한 번: 1년 지난 사용 통계 이벤트와 오류 기록, 쓰지 않고 기한이 지난 가족 초대 링크,
    끝난 지 30일 지난 기억 인터뷰 신청의 연락처를 지운다. 지웠으면(돌았으면) True.

    법률 자문 반영(2026-10-22): 아이 대화 상대의 나이 바뀜(minors.sweep), 읽고 180일 지난 알림,
    지운 지 5년 지난 삭제 기록, 결과가 정해진 지 3년 지난 이의 기록도 여기서 한다. 하나가 실패해도
    나머지는 한다.
    """
    global _last_cleanup
    current = at or datetime.now(UTC)
    if _last_cleanup is not None and current - _last_cleanup < CLEANUP_EVERY:
        return False
    async with SessionLocal() as db:
        removed = await analytics.cleanup(db, current)
        errors = await db.execute(
            delete(ErrorLog).where(ErrorLog.created_at < current - ERROR_LOG_RETENTION)
        )
        # 개인정보 처리방침: 쓰지 않고 기한이 지난 초대 링크는 하루 안에 지운다
        links = await db.execute(
            delete(PersonaShare).where(
                PersonaShare.user_id.is_(None), PersonaShare.expires_at < current
            )
        )
        await db.commit()
        # 기억 인터뷰: 끝나거나 취소한 지 30일 지난 신청의 이름·연락처·희망 일정을 지운다
        wiped = await interview.wipe_closed(db, current)
    await _legal_daily(current)
    _last_cleanup = current
    if wiped:
        logger.info("wiped contact details of %s closed interview requests", wiped)
    if removed:
        logger.info("removed %s analytics events older than a year", removed)
    if errors.rowcount:
        logger.info("removed %s error logs older than a year", errors.rowcount)
    if links.rowcount:
        logger.info("removed %s expired family invite links", links.rowcount)
    return True


async def _legal_daily(current: datetime) -> None:
    """하루 정리의 법률 자문 반영 몫. 일마다 따로 세션을 열고, 실패는 기록만 남긴다."""

    async def objections(db) -> int:
        result = await db.execute(
            delete(PersonaObjection).where(
                PersonaObjection.outcome_at.is_not(None),
                PersonaObjection.outcome_at < current - OBJECTION_RETENTION,
            )
        )
        await db.commit()
        return int(result.rowcount or 0)

    steps = (
        # 아이가 14·19세가 되는 달 알림·기한 미리 알림·기한 넘김 멈춤
        ("minor age changes", lambda db: minors.sweep(db, current)),
        # 읽고 180일 지난 앱 알림(계약 내용은 남긴다)
        ("read notices removed", lambda db: notices.cleanup(db, current)),
        # 지운 지 5년 지난 삭제 기록
        ("deletion records removed", lambda db: deletion.cleanup(db, current)),
        ("objection records removed", objections),
    )
    for what, step in steps:
        try:
            async with SessionLocal() as db:
                done = await step(db)
            if done:
                logger.info("daily legal cleanup: %s %s", done, what)
        except Exception:  # 하나가 실패해도 다른 정리는 한다(다음 날 다시 한다)
            logger.exception("daily legal cleanup failed: %s", what)


async def periodic() -> None:
    """10분마다: 지울 때가 된 멈춘 대화 상대 지우기, 기간이 끝난 구독 갱신(결제)·종료, 예전 목소리
    하나를 Fish 로 다시 만들기. 하루 한 번: 오래된 기록 지우기·아이 대화 상대 나이 바뀜
    (cleanup_daily).

    서버가 한 대일 때를 가정한다. 여러 대로 늘리면 한 곳에서만 돌게 바꾼다.
    켤 때 한 번(AI 서버가 뜬 뒤): 끊긴 목소리 만들기와 만들다 만 똑 닮은 목소리가 회사에 남긴
    목소리를 지운다.
    """
    await asyncio.sleep(60)
    try:
        await persona_builder.clean_interrupted_clones()
        async with SessionLocal() as db:
            await voice_retrain.drop_pvc_voices(db)
    except Exception:  # 정리가 실패해도 결제 확인은 한다(다음에 켤 때 다시 한다)
        logger.exception("vendor voice cleanup failed")
    while True:
        try:
            await cleanup_daily()
        except Exception:  # 통계 정리가 실패해도 결제 확인은 한다
            logger.exception("analytics cleanup failed")
        try:
            async with SessionLocal() as db:
                # 멈춘 대화 상대 가운데 지울 때가 된 것(이의 접수·원하지 않음 7일 뒤)
                await holds.sweep(db, datetime.now(UTC))
        except Exception:  # 지우기가 실패해도 결제 확인은 한다(다음 번에 다시 한다)
            logger.exception("periodic hold sweep failed")
        try:
            async with SessionLocal() as db:
                # 결과를 모르는 결제를 먼저 정한다(정해지기 전에는 같은 구독을 다시 결제하지 않는다)
                await billing.settle_unknown(db)
            async with SessionLocal() as db:
                await billing.renew_due(db)
        except Exception:  # 한 번 실패해도 다음 번에 다시 한다
            logger.exception("periodic billing check failed")
        try:
            async with SessionLocal() as db:
                # 한 번에 하나만 건다. 이미 돌고 있으면 그냥 지나간다
                await voice_retrain.sweep(db)
        except Exception:  # 다시 만들기가 멈춰도 다음 결제 확인은 한다
            logger.exception("periodic voice retrain failed")
        await asyncio.sleep(600)


def check_production() -> None:
    """운영에서 안전하지 않은 설정이면 켜지 않는다(DB 를 만지기 전에 멈춘다).

    사업자 표시 값(주소·전화·사업자등록번호)이 비었으면 켜기는 하되 기록과 운영 알림을 남긴다
    (전자상거래법 제10조 — 값은 적지 않고 이름만).
    """
    if settings.environment != "production":
        return
    problems = unsafe_production_settings(settings)
    if problems:
        raise RuntimeError("운영 설정을 고쳐야 켤 수 있어요: " + ", ".join(problems))
    missing = missing_business_settings(settings)
    if missing:
        logger.warning("business info is empty: %s", ", ".join(missing))
        notify.send(
            "⚠️ 사업자 정보가 비어 있어요: " + ", ".join(missing) + " — 운영 .env 를 채워 주세요",
            channel="alerts",
        )


# asyncio.to_thread 가 쓰는 스레드 수. 기본값(CPU 수 + 4, 운영 2 vCPU 면 6개)이면 녹음 올리기(S3)·
# 소리 바꾸기(ffmpeg)처럼 몇 분 걸리는 일 몇 개가 다 차지해, 대화마다 도는 기억 찾기·저장소 호출이
# 그 뒤에 줄을 서서 CloudFront(120초)에 끊길 수 있다. 대부분 기다리기만 하는 스레드라 넉넉히 둔다.
IO_THREADS = 32


def use_io_threads() -> None:
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=IO_THREADS, thread_name_prefix="io")
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    use_io_threads()
    check_production()
    await create_schema()
    await bootstrap()
    await jobs.fail_interrupted()
    try:
        await voice_retrain.drop_pvc_documents()
    except Exception:  # 서류 정리가 실패해도 서버는 켠다(다음에 켤 때 다시 한다)
        logger.exception("premium voice document cleanup failed")
    settings.local_media_dir.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(periodic())
    yield
    task.cancel()
    await jobs.wait_all()


def docs_options(environment: str) -> dict:
    """운영은 API 문서(/docs·/redoc·/openapi.json)를 열지 않는다."""
    if environment == "production":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {}


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
    **docs_options(settings.environment),
)
app.include_router(api_router, prefix=settings.api_v1_prefix)
# 본문 크기 상한은 CORS 바로 안쪽에 둔다(본문을 읽는 어떤 것보다 먼저 보고, 413 에도 CORS 머리글이
# 붙어 웹 화면이 '파일이 너무 커요'를 읽을 수 있게). CORS 는 본문을 읽지 않는다.
app.add_middleware(BodySizeLimit)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # 로컬은 flutter run 이 포트를 매번 바꿔서 localhost 를 다 연다. 운영은 끈다.
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?"
    if settings.cors_allow_localhost
    else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get(f"{settings.local_media_public_path}/{{key:path}}", include_in_schema=False)
async def signed_media(key: str, exp: int, sig: str):
    """서명 주소로만 파일을 준다. 녹음은 함부로 열리면 안 되는 자료다."""
    if not verify_signature(key, exp, sig):
        raise HTTPException(403, "이 주소는 이제 열 수 없어요. 화면을 새로고침해 주세요.")
    storage = get_storage()
    if isinstance(storage, LocalStorage):
        path = storage.path(key)
        if not path.is_file():
            raise HTTPException(404, "파일을 찾지 못했어요.")
        return FileResponse(path)
    return RedirectResponse(storage.signed_url(key))


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}


FIELD_MESSAGES = {
    "email": "이메일 주소를 확인해 주세요.",
    "password": "비밀번호는 8자 이상이어야 해요.",
    "newPassword": "새 비밀번호는 8자 이상이어야 해요.",
    "currentPassword": "지금 비밀번호를 적어 주세요.",
    "name": "이름을 적어 주세요.",
}


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """화면에 그대로 보여줄 수 있게 첫 번째 틀린 칸을 한국어 한 줄로. 자세한 내용은 errors 에.

    보낸 값(input·ctx)은 돌려주지 않는다. 비밀번호 같은 값이 응답·로그에 남지 않게.
    """
    errors = exc.errors()
    field = str(errors[0]["loc"][-1]) if errors and errors[0].get("loc") else ""
    return JSONResponse(
        status_code=422,
        content={
            "detail": FIELD_MESSAGES.get(field, "입력한 값을 다시 확인해 주세요."),
            "errors": [
                {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
                for e in errors
            ],
        },
    )


@app.exception_handler(ConsentRequired)
async def consent_required(request: Request, exc: ConsentRequired):
    """계정 필수 동의가 빠졌다. 화면은 이것을 받으면 동의 화면으로 간다."""
    return JSONResponse(
        status_code=403,
        content={
            "detail": "서비스 이용 동의가 필요해요.",
            "code": "consent_required",
            "missing": exc.missing,
        },
    )


@app.exception_handler(sharing.ShareError)
async def share_error(request: Request, exc: sharing.ShareError):
    """가족 공유 오류. 화면은 code(와 reason·need·cap·field)로 가르고, detail 을 그대로 보인다."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code, **exc.extra},
    )


@app.exception_handler(gate.GateError)
async def gate_error(request: Request, exc: gate.GateError):
    """처리 게이트에 막혔다(멈춤·유족 확인·법정대리인·그분 동의 녹음). 화면은 code 로 가르고 detail
    을 그대로 보인다. extra 의 날짜 같은 값은 JSON 글로 바꾼다."""
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder({"detail": exc.detail, "code": exc.code, **exc.extra}),
    )


@app.exception_handler(AIServerError)
async def ai_server_error(request: Request, exc: AIServerError):
    """AI 서버가 오류로 답했다(대화 대답 등). 문장은 고객에게 보여도 되는 것이다."""
    if exc.code not in PROVIDER_CODES | VOICE_PROVIDER_CODES:  # AI·목소리 회사 문제는 이미 알렸다
        notify.error(f"AI 서버 오류 {exc.status_code}", _route(request))
    return JSONResponse(
        status_code=503, content={"detail": str(exc), "code": exc.code or "ai_error"}
    )


def _route(request: Request) -> str:
    """알림에 적을 경로. 대화 상대 번호 같은 id 는 빼서 같은 오류끼리 묶는다."""
    path = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", ":id", request.url.path)
    return f"{request.method} {path}"


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    # 디스코드 오류 채널에도 알린다(종류와 경로만, 보낸 값·원문 없이)
    notify.error(type(exc).__name__, _route(request))
    # 원문은 오류 기록(관리자 화면)과 서버 로그에만. 고객에게는 차분한 한 문장.
    # 기록에는 종류와 앞부분만 남긴다(긴 SQL·보낸 값이 통째로 쌓이지 않게).
    if isinstance(exc, DBAPIError):
        # DB 오류 원문(DETAIL)에는 이메일 같은 보낸 값이 들어간다. 종류만 남긴다.
        message = f"{type(exc).__name__}: {type(exc.orig).__name__}"
    else:
        message = f"{type(exc).__name__}: {str(exc)[:300]}"
    try:
        async with SessionLocal() as db:
            db.add(
                ErrorLog(
                    path=request.url.path, message=message, detail={}, created_at=datetime.now(UTC)
                )
            )
            await db.commit()
    except Exception:
        pass
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "code": "internal_error",
                "message": "잠시 문제가 생겼어요. 잠시 뒤 다시 해 주세요.",
            },
        },
    )
