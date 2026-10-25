from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 통신판매업 신고 번호(2026-10-16, 용산구청). 공개 정보다
MAIL_ORDER_NO = "제2026-서울용산-1112호"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "TalkTo API"
    environment: str = "local"
    api_v1_prefix: str = "/api/v1"
    database_url: str = "sqlite+aiosqlite:///./talkto.db"
    jwt_secret: str = Field("local-development-secret-change-me", min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14
    allow_signup: bool = True
    ai_server_url: str | None = None
    ai_server_token: str | None = None
    ai_server_timeout_seconds: float = 45
    # 전사·녹음 분석은 녹음 길이만큼 걸린다. AI 서버 STT_TIMEOUT_SECONDS 와 맞춘다.
    ai_server_long_timeout_seconds: float = 1800
    # 녹음 저장소. local 은 디스크, s3 는 S3 버킷(운영).
    storage_backend: str = Field("local", pattern="^(local|s3)$")
    local_media_dir: Path = Path("storage/media")
    local_media_public_path: str = "/media"
    # AI 서버와 브라우저가 녹음을 내려받을 이 서버의 바깥 주소. 로컬은 그대로 두면 된다.
    public_base_url: str = "http://localhost:8000"
    s3_bucket: str | None = None
    s3_region: str = "ap-northeast-2"
    signed_url_ttl_seconds: int = 3600
    max_upload_mb: int = 300
    # 녹음 하나의 길이 상한(분). 파일 크기(max_upload_mb)만 보면 압축이 센 파일은 몇십 시간이 될 수
    # 있어, 소리를 뽑을 때 길이로도 막는다(넘으면 그 녹음만 실패로 두고 나눠 올리게 한다).
    max_recording_minutes: int = 180
    # 대화에서 말로 보내는 녹음 하나의 길이 상한(초). 넘으면 글로 옮기기(돈이 든다) 전에 돌려보낸다.
    max_voice_message_seconds: int = 300
    # 성격 정리(/v1/persona/reflection)를 부를지. AI 서버가 4층 조립기
    # (PERSONA_PROMPT_BUILDER=layered, 운영 기본)면 그 결과를 쓰지 않아 끈다. 예전 조립기(legacy)로
    # 돌릴 때만 켠다.
    persona_reflection: bool = False
    # 로그인을 유지하는 가장 긴 기간(일). 토큰을 새로 받아도 처음 로그인한 때부터 센다.
    max_session_days: int = 90
    # 브라우저에서 부르는 프론트 주소. 로컬은 flutter run 포트가 매번 바뀌어 localhost 를 다 연다.
    cors_origins: list[str] = Field(default_factory=list)
    cors_allow_localhost: bool = True
    # 소셜 로그인이 끝나고 돌아갈 웹 주소. CORS_ORIGINS 에 없어도 이 주소로는 돌아갈 수 있다.
    frontend_url: str = "http://localhost:8090"
    # 소셜 로그인. id·secret 이 둘 다 있어야 그 버튼이 켜진다.
    kakao_client_id: str | None = None  # Kakao Developers 의 REST API 키
    kakao_client_secret: str | None = None
    # 카카오에 더 받을 동의항목(예: account_email). 카카오 콘솔에서 그 항목을 켠 뒤에만 넣는다
    # (안 켠 항목을 넣으면 카카오가 KOE205 오류를 낸다). 이미 가입한 사람에게도 동의를 다시 묻는다.
    kakao_extra_scope: str | None = None
    # 이메일 가입 때 '@' 뒤 도메인이 메일을 받는지 DNS 로 본다.
    # 운영에서 켠다(인증 메일은 안 보낸다).
    email_domain_check: bool = False
    # 운영 알림(디스코드 웹후크 주소). 비밀번호처럼 .env 에만 둔다.
    # events = 가입·구독 신청 등, alerts = 오류·AI 회사·목소리 회사 문제(없으면 events 로).
    # 배포 알림(DISCORD_DEPLOY_WEBHOOK_URL)은 deploy.py 가 로컬에서만 쓴다(서버에 올리지 않는다).
    discord_events_webhook_url: str | None = None
    discord_alerts_webhook_url: str | None = None
    # 운영 알림 메일(app/services/mailer.py, AWS SES v2). 둘 다 있고 운영일 때만 보낸다.
    # 받는 곳(예: asktalktoteam@gmail.com)과 보내는 주소(noreply@<도메인>, SES 에서 확인한 도메인).
    # 개인 정보는 싣지 않는다('새 기억 인터뷰 신청 1건'처럼 무슨 일이 있었는지만).
    ops_email_to: str | None = None
    mail_from: str | None = None
    mail_region: str = "ap-northeast-2"
    # 고객에게 메일을 보내도 되나(mailer.send_user — 계약 내용·동의 녹음 결과·탈퇴 안내).
    # SES 운영 권한(샌드박스 해제)을 받은 뒤 대표가 켠다. 끄면 앱 안 알림만 남는다.
    user_mail_enabled: bool = False
    google_client_id: str | None = None
    google_client_secret: str | None = None
    # 요금제(원, 부가세 포함). 2026-10-21 요금 변경 기준(운영도 이 기본값을 쓴다), .env 로 바꿀 수
    # 있다. 추억 9,900원·한 달 50번, 가족 29,900원·대화 상대마다 300번, 첫 100가족 12개월
    # 4,900원·14,900원. 요금은 동의문에 {{plan_*_price}} 로 보인다(legal.py).
    # 대화 횟수 = 한 번 말하고 한 번 대답 듣는 것(한 달 = 결제일부터 다음 결제일 전까지).
    # 무료 체험은 계정 하나에 대화 상대 1개(다 만든 뒤부터 7일). 상한은 지운 것도 센다.
    trial_days: int = 7
    trial_conversations: int = 30
    trial_personas_per_account: int = 1
    plan_memories_price: int = 9900
    plan_memories_beta_price: int | None = 4900
    plan_memories_conversations: int = 50
    plan_family_price: int = 29900
    plan_family_beta_price: int | None = 14900
    plan_family_conversations: int = 300
    # 대화 횟수를 다 쓰면 끊지 않고 글로 대답한다(하드캡 금지). 그 글 대답의 한 달 공정 사용 상한.
    text_only_fair_use: int = 1000
    # 초기 가족 요금: 첫 N가족이 M개월 동안 낮은 값으로 낸다.
    # 끄면 새로 받지 않는다(이미 받은 가족은 기간이 끝날 때까지 유지).
    beta_pricing_open: bool = True
    beta_families: int = 100
    beta_months: int = 12
    # 결제가 열렸는가. False 면 '결제 전 모드': 바로 결제하지 않고 구독 신청만 받으며,
    # 다 만든 대화 상대는 무료 체험 자리를 쓰지 않고 한도 없이 쓴다(PRELAUNCH).
    payments_open: bool = False
    # 결제 시험 계정(이메일, 쉼표로 여러 개). 결제가 닫혀 있어도 이 계정만 결제를 연다.
    # 결제 회사가 시험 결제일 때(토스 test_ 키, 로컬 시험 결제)만 열려 실제 돈은 나가지 않는다.
    # 토스페이먼츠·카드사 심사 담당자가 결제창까지 가 보는 데 쓴다.
    payments_test_emails: str | None = None
    # 결제 전 모드의 대화 상대마다 30일 대화 상한(다 만든 날부터 30일마다 다시 채운다).
    # 비우면 제한 없음.
    prelaunch_conversations: int | None = None
    # 결제 전 모드에서 계정 하나가 갖고 있을 수 있는 대화 상대 수(관리자는 빼고). 비우면 제한 없음.
    prelaunch_personas_per_account: int | None = 3
    # 가족 공유(app/services/sharing.py). 대화 상대 하나에 들어온 가족 + 쓸 수 있는 링크(주인 빼고).
    # 아래 둘은 이용약관·가족 공유 안내에 {{share_members}}·{{share_link_days}} 로 보인다(legal.py).
    # 바꾸면 동의받은 글이 바뀌므로 그 문서의 버전도 올린다.
    share_members_per_persona: int = 5
    # 초대 링크가 열려 있는 시간(7일). 들어온 뒤에는 보지 않는다.
    share_link_hours: int = 168
    # 끄면 새 링크·다시 보내기·참여만 막는다(이미 함께하는 가족은 그대로 대화한다)
    share_open: bool = True
    # '더 알려 주실 기억'(app/services/gaps.py): 대화에 기억 빈틈 표시(flagGaps)를 부탁하고,
    # 대답 뒤에 가족에게 물을 질문을 만든다. 끄면 표시를 부탁하지 않고 새 질문도 만들지 않는다
    # (있는 질문·답하기는 그대로). AI 서버가 flagGaps 를 모르면(예전 AI 서버) 대화가 422 로
    # 막히므로 AI 서버를 먼저 올린다.
    gap_questions_enabled: bool = True
    # 로그인·가입·대화 보내기 같은 요청 횟수 제한(app/core/ratelimit.py). 끄면 세지 않는다.
    rate_limit_enabled: bool = True
    # 말로 답하기(🎤 → 글)를 서버 전체에서 최근 24시간 동안 받아쓸 수 있는 길이(초, 기본 20시간).
    # 다 쓰면 '잠시 뒤 다시'(503). 0 이하면 끈다. rate_limit_enabled 가 꺼져 있으면 세지 않는다.
    transcription_daily_seconds: int = 72_000
    # 결제(토스페이먼츠 자동결제). 비우면 로컬에서는 시험 결제, 운영에서는 결제를 열지 않는다.
    toss_client_key: str | None = None
    toss_secret_key: str | None = None
    # 예전 목소리(ElevenLabs·MiniMax)를 Fish drama-3 로 하나씩 다시 만들기(voice_retrain).
    # 기본은 꺼 둔다: 다시 만들면 예전 목소리를 그 회사에서 지워 되돌릴 수 없다. 배포 뒤 관리자
    # 대화 상대 하나로 만들기·듣기·지우기를 확인하고 귀로 들어 본 다음 켠다. 끄면 새로 걸지 않는다
    # (돌고 있는 것은 끝까지 간다).
    voice_retrain_enabled: bool = False
    # 사업자 정보와 동의문 자리표시 값({{상호}} …).
    # 비우면 동의문에 '(준비 중)'·'(확인 중)'으로 보인다.
    business_name: str | None = None
    business_owner: str | None = None
    business_email: str | None = None
    business_phone: str | None = None
    business_reg_no: str | None = None
    business_address: str | None = None
    # 통신판매업 신고 번호(2026-10-16 용산구청). 공개 정보라 기본값으로 둔다(.env 가 비어도
    # '신고 진행 중'이 뜨지 않게 — 빈 값은 이 값으로 읽는다)
    mail_order_no: str | None = MAIL_ORDER_NO
    legal_data_region: str | None = None  # 회원 정보·대화 기록을 둔 데이터 서버 위치
    # ── 법률 자문 반영(2026-10-22) ──
    # 만 14세 미만 아이 대화 상대의 법정대리인 확인 문자(app/services/sms.py).
    # off·빈 값 = 만 14세 미만 아이의 대화 상대를 만들 수 없다(기본, 대표 답 2026-10-23).
    # manual = 대표가 관리자 화면에 뜬 번호로 자기 휴대전화에서 직접 보낸다(새 수탁자 없음).
    # log = 개발용(운영에서는 쓰지 않는다). 그 밖 = 대표가 정한 문자 업체(정하면 처리방침도 고친다)
    sms_provider: str | None = "off"
    # manual 일 때 확인 번호가 유효한 시간(대표가 손으로 보내므로 10분은 짧다)
    sms_manual_code_hours: int = 24
    # 문자 업체 키와 등록한 발신번호(업체를 정했을 때만)
    sms_api_key: str | None = None
    sms_api_secret: str | None = None
    sms_sender: str | None = None
    # 멈춘 대화 상대를 기한이 지나면 지운다(holds.sweep). 끄면 지우지 않고 운영 알림만(비상 스위치)
    persona_auto_delete: bool = True
    # holds.sweep 한 번에 지우는 대화 상대 수 상한. 넘으면 이만큼만 지우고 운영 알림(대량 삭제 방지)
    holds_max_deletes_per_sweep: int = 3
    # 그분 동의 녹음(app/services/subject_consent.py): 검수로 보내는 녹음 수·길이(초)·크기(MB) 상한
    subject_consent_max_attempts: int = 5
    subject_consent_max_seconds: int = 900
    subject_consent_max_mb: int = 32
    # 멈춘 뒤 지우기까지(일)
    hold_delete_days: int = 7
    # 아이가 14·19세가 된 뒤 본인 동의를 새로 받는 기한(개월)
    minor_reconsent_months: int = 6
    # 지운 자료가 서버 백업에 남는 날 수. RDS BackupRetentionPeriod(deploy/aws/stack.yaml)와 같아야
    # 한다(지울 때 알림의 '서버 백업에는 ○월 ○일까지')
    deletion_backup_days: int = 7
    # 고객센터(GET /api/v1/support). 카카오톡 채널 1:1 채팅 주소와 문의를 받는 때(화면에 그대로).
    # 이메일은 business_email 을 쓴다. 채널 주소는 pf.kakao.com 채널 주소만 내보내고(http 는 https
    # 로, 그 밖은 단추를 숨긴다 — support.channel_url), 비우면 그 단추를 숨긴다. 기본값은 대표님이
    # 주신 TalkTo 채널(2026-10-21)이다.
    kakao_channel_chat_url: str | None = "https://pf.kakao.com/_nNwwX/chat"
    support_hours: str = "평일 10시~18시"
    bootstrap_admin_email: str = "admin@talkto.dev"
    bootstrap_admin_password: str = "Admin1234!"

    @field_validator(
        "plan_memories_beta_price",
        "plan_family_beta_price",
        "prelaunch_conversations",
        "prelaunch_personas_per_account",
        mode="before",
    )
    @classmethod
    def _empty_beta_price(cls, value):
        """`PLAN_*_BETA_PRICE=` 처럼 비워 두면 초기 요금 없음(None). 빈 글자는 숫자로 못 읽는다."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("mail_order_no", mode="before")
    @classmethod
    def _empty_mail_order_no(cls, value):
        """`MAIL_ORDER_NO=` 처럼 비워 두면 신고 번호 기본값. 공개 정보라 비워 둘 까닭이 없다."""
        if value is None or (isinstance(value, str) and not value.strip()):
            return MAIL_ORDER_NO
        return value


# 예시 파일(.env.docker.example)에 적힌 관리자 비밀번호. 운영에서 그대로 쓰면 안 된다.
EXAMPLE_ADMIN_PASSWORD = "change-this-admin-password"


def unsafe_production_settings(settings: Settings) -> list[str]:
    """운영(ENVIRONMENT=production)에서 쓰면 안 되는 설정. 값은 적지 않고 이름과 까닭만."""
    problems = []
    secret = settings.jwt_secret
    if len(secret) < 32 or secret.startswith(("local-", "change-")):
        problems.append("JWT_SECRET(32자 이상의 임의 값)")
    default_password = Settings.model_fields["bootstrap_admin_password"].default
    if settings.bootstrap_admin_password in (default_password, EXAMPLE_ADMIN_PASSWORD):
        problems.append("BOOTSTRAP_ADMIN_PASSWORD(기본값 그대로)")
    if settings.cors_allow_localhost:
        problems.append("CORS_ALLOW_LOCALHOST(false 로)")
    if not settings.ai_server_token:
        problems.append("AI_SERVER_TOKEN(비어 있음)")
    if settings.storage_backend != "s3":
        problems.append("STORAGE_BACKEND(s3 로)")
    return problems


def missing_business_settings(settings: Settings) -> list[str]:
    """운영에서 비어 있으면 안 되는 사업자 표시 값(전자상거래법 제10조 — 사업장 주소는 신고증대로
    동·호수까지). 켜기는 막지 않고 알리기만 한다. 값은 적지 않고 이름만."""
    names = {
        "business_address": "BUSINESS_ADDRESS",
        "business_phone": "BUSINESS_PHONE",
        "business_reg_no": "BUSINESS_REG_NO",
    }
    return [env for field, env in names.items() if not (getattr(settings, field) or "").strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
