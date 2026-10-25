"""운영 알림을 디스코드 채널로 보낸다(웹후크). 새 가입·구독 신청·대화 상대 완성·오류 등.

디스코드는 해외(미국) 회사라 **개인 정보는 보내지 않는다**: 이름·전화번호·대화 내용·질문 답
없이 '무슨 일이 몇 번' 정도만 보낸다. 하나 예외로, 새 가입·구독 신청·인터뷰 신청에는 팀원인지
알아보게 그 계정 이메일의 **앞 두 글자와 도메인만** 싣는다(al***@gmail.com, masked_email).
전체 이메일은 보내지 않고, 가린 이메일도 통계(analytics_events)·로그에는 남기지 않는다.
연락처 같은 자세한 내용은 관리자 화면에서 본다.

운영(ENVIRONMENT=production)이고 웹후크 주소가 있을 때만 보낸다(로컬·시험은 보내지 않는다).
보내기는 요청과 따로 돈다: 디스코드가 느리거나 막혀도 요청이 늦어지거나 깨지지 않는다.
같은 오류가 쏟아지면 10분에 한 번만 보내고, 그사이 몇 번 더 났는지 함께 적는다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.db.models import SOCIAL_PLACEHOLDER_EMAIL_SUFFIX

logger = logging.getLogger(__name__)

WEBHOOK_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")
TIMEOUT_SECONDS = 5
ERROR_EVERY_SECONDS = 600
MAX_PER_MINUTE = 20  # 디스코드 웹후크 한도(분당 30번)보다 넉넉히 아래

PLAN_NAMES = {"memories": "추억", "family": "가족"}
METHOD_NAMES = {"email": "이메일", "kakao": "카카오", "google": "구글"}
NO_EMAIL = "이메일 없음"

_tasks: set[asyncio.Task] = set()
_sent: list[float] = []
_errors: dict[str, tuple[float, int]] = {}  # 오류 종류 → (마지막으로 보낸 때, 그 뒤로 더 난 수)


def _production() -> bool:
    return get_settings().environment == "production"


def _url(channel: str) -> str | None:
    settings = get_settings()
    if not _production():
        return None
    url = (settings.discord_alerts_webhook_url if channel == "alerts" else None) or (
        settings.discord_events_webhook_url
    )
    if not url or not url.startswith(WEBHOOK_PREFIXES):
        return None
    return url


def configured() -> dict[str, bool]:
    """알림을 보낼 수 있는지(채널마다)."""
    return {"events": _url("events") is not None, "alerts": _url("alerts") is not None}


def send(text: str, *, channel: str = "events") -> None:
    """한 줄을 보낸다(기다리지 않는다). 설정이 없으면 아무것도 하지 않는다."""
    url = _url(channel)
    if url is None:
        return
    now = time.monotonic()
    _sent[:] = [t for t in _sent if now - t < 60]
    if len(_sent) >= MAX_PER_MINUTE:
        logger.warning("discord notify skipped (too many this minute)")
        return
    _sent.append(now)
    try:
        task = asyncio.get_running_loop().create_task(_post(url, text[:1900]))
    except RuntimeError:  # 돌고 있는 이벤트 루프가 없다(시험 스크립트 등)
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _post(url: str, text: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                # 멘션(@everyone 등)은 막는다
                json={"content": text, "allowed_mentions": {"parse": []}},
                headers={"User-Agent": "DiscordBot (https://app.talkto.co.kr, 1.0)"},
            )
        if response.status_code >= 400:
            logger.warning("discord notify failed: %s", response.status_code)
    except Exception as error:  # noqa: BLE001 — 알림 때문에 서비스가 깨지면 안 된다
        logger.warning("discord notify failed: %s", type(error).__name__)


def error(kind: str, where: str) -> None:
    """오류 알림(오류 채널). 같은 kind 는 10분에 한 번만, 그사이 더 난 수를 다음 알림에 적는다."""
    now = time.monotonic()
    last, more = _errors.get(kind, (0.0, 0))
    if last and now - last < ERROR_EVERY_SECONDS:
        _errors[kind] = (last, more + 1)
        return
    _errors[kind] = (now, 0)
    extra = f" (지난 알림 뒤로 {more}번 더)" if more else ""
    send(f"🚨 오류 · {where} · {kind}{extra}", channel="alerts")


def ai_provider(code: str) -> None:
    """AI 회사(OpenAI) 문제. 대화·익히기가 멈추므로 바로 알린다(같은 문제는 10분에 한 번)."""
    text = {
        "AI_PROVIDER_QUOTA": "OpenAI 크레딧이 떨어져 대화 대답과 익히기가 멈췄어요. "
        "platform.openai.com → Settings → Billing 에서 충전해 주세요.",
        "AI_PROVIDER_AUTH": "OpenAI 키가 막혔어요. 키를 확인해 주세요.",
    }.get(code, "OpenAI 요청이 몰려 일부 대답이 실패하고 있어요.")
    error(code, text)


def voice_provider(code: str) -> None:
    """목소리 회사(Fish Audio) 문제. 목소리 만들기·대답 읽어 주기가 멈춘다(글 대화는 된다).

    같은 문제는 10분에 한 번. 예전 목소리(ElevenLabs·MiniMax)로 대답하는 대화 상대는 그대로다.
    """
    text = {
        "VOICE_PROVIDER_CREDITS": "Fish Audio 크레딧이 떨어져 목소리 만들기와 대답 읽어 주기가 "
        "멈췄어요. fish.audio 에서 충전해 주세요(글 대화는 그대로 돼요).",
        "VOICE_PROVIDER_KEY": "Fish Audio 키가 막혔어요. AI 서버의 FISH_AUDIO_API_KEY 를 "
        "확인해 주세요.",
        "VOICE_PROVIDER_RATE_LIMIT": "Fish Audio 요청이 몰려 일부 목소리 대답이 실패하고 있어요.",
    }.get(code, "Fish Audio 가 답하지 않아 목소리 만들기와 대답 읽어 주기가 실패하고 있어요.")
    error(code, text)


def masked_email(email: str | None) -> str:
    """알림에 싣는 계정 이메일: 앞 두 글자 + *** + @도메인(al***@gmail.com).

    앞부분이 1~2글자면 한 글자만. +태그도 앞 두 글자 뒤라 가려진다(ab+tag@x.com → ab***@x.com).
    이메일 없이 가입한 소셜 계정(빈 자리 주소)·없는 값은 '이메일 없음'.
    """
    text = (email or "").strip().lower()
    local, at, domain = text.rpartition("@")
    if not at or not local or not domain or text.endswith(SOCIAL_PLACEHOLDER_EMAIL_SUFFIX):
        return NO_EMAIL
    return f"{local[:2] if len(local) > 2 else local[:1]}***@{domain}"


def _event_text(name: str, props: dict[str, Any], email: str | None = None) -> str | None:
    plan = PLAN_NAMES.get(str(props.get("plan")), props.get("plan"))
    # 누가(가린 이메일). 부르는 쪽이 이메일을 준 이벤트에만 붙인다
    who = f" · {masked_email(email)}" if email is not None else ""
    if name == "signup":
        method = METHOD_NAMES.get(str(props.get("method")), props.get("method") or "")
        return f"🎉 새 가입 · {method}".rstrip(" ·") + who
    if name == "subscription_requested":
        return f"📝 구독 신청 · {plan}{who}"
    if name == "subscription_request_canceled":
        return f"↩️ 구독 신청 취소 · {plan}"
    if name == "build_done":
        minutes = round((props.get("seconds") or 0) / 60)
        return f"✅ 대화 상대를 다 만들었어요 · {minutes}분 걸림"
    if name == "build_failed":
        return "❌ 대화 상대 만들기 실패 — 관리자 화면 오류 기록을 확인해 주세요"
    if name == "payment_succeeded":
        amount = props.get("amount")
        return f"💳 결제 성공 · {plan}" + (f" · {amount:,}원" if isinstance(amount, int) else "")
    if name == "account_deleted":
        return "👋 회원 탈퇴"
    if name == "share_joined":
        return "🏠 가족 공유 참여"
    if name == "interview_requested":
        if props.get("repeat"):
            return None  # 같은 대화 상대로 하루 안에 다시 신청함(신청·취소 되풀이) — 한 번만 알린다
        # 이름·연락처·대화 상대는 싣지 않는다(관리자 화면에서 본다). 신청한 계정은 가린 이메일만
        return f"🎙️ 새 기억 인터뷰 신청 1건{who} — 관리자 화면에서 확인해 주세요"
    return None


def event(name: str, props: dict[str, Any], email: str | None = None) -> None:
    """통계 이벤트 가운데 알릴 것만 보낸다(analytics.track 이 부른다).

    email 은 알림 글에 가려서만 싣는다(masked_email). props 와 따로 받아 통계에는 남지 않는다.
    """
    try:
        text = _event_text(name, props, email)
    except Exception:  # noqa: BLE001
        return
    if text:
        send(text)
