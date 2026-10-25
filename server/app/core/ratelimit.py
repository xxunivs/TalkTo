"""요청 횟수 제한(최근 N초 동안 M번, 미끄러지는 창).

서버 한 대의 메모리에서만 센다(다시 켜면 처음부터). 여러 대로 늘리면 Redis 같은 곳으로 옮긴다.
가족이 평소에 쓰다가 걸리지 않게 넉넉하게 잡고, 마구 두드리는 것만 막는다.
"""

import ipaddress
import logging
import math
import time
from collections import deque
from threading import Lock

from fastapi import HTTPException, Request, status

from app.core.config import get_settings

logger = logging.getLogger(__name__)

TOO_MANY = "요청이 너무 많아요. 잠시 뒤 다시 해 주세요."


def wait_text(seconds: float) -> str:
    """다시 되기까지 남은 때를 말로('1분'·'7분'·'2시간'·'2시간 30분'). 1분보다 짧아도 '1분'."""
    minutes = max(1, math.ceil(seconds / 60))
    if minutes < 60:
        return f"{minutes}분"
    hours, rest = divmod(minutes, 60)
    return f"{hours}시간 {rest}분" if rest else f"{hours}시간"


# 제한마다 기억하는 열쇠(IP·사용자) 수 상한. 넘으면 지난 것부터 비운다(메모리가 끝없이 늘지 않게).
# 열쇠 하나에 최대 limit 개의 시각을 둔다. 1만 × 60개여도 수십 MB 안이다.
MAX_KEYS = 10_000
# 창이 지나지 않은 열쇠로 꽉 차면 이만큼(비율)을 비운다. 적게 센 열쇠부터: 새 열쇠를 마구 만들어
# 많이 두드린 열쇠(비밀번호 맞히기 중인 이메일 등)의 기록을 지우지 못하게.
EVICT_SHARE = 0.1


class RateLimit:
    def __init__(self, name: str, limit: int, window_seconds: float) -> None:
        self.name = name
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def hit(self, key: str, now: float | None = None) -> float | None:
        """한 번 센다. 넘었으면 세지 않고 몇 초 뒤에 다시 되는지 돌려준다(되면 None)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= MAX_KEYS:
                    self._evict(now)
                hits = self._hits[key] = deque()
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(hits[0] + self.window - now, 0.0) if hits else self.window
            hits.append(now)
            return None

    def _evict(self, now: float) -> None:
        """창이 지난 열쇠를 지운다. 그래도 많으면 적게 센(오래전에 센) 열쇠부터 일부를 비운다.

        예전에는 다 비웠다: 새 열쇠를 1만 개 만들면 이메일마다 쌓인 로그인 실패 기록까지 지워져
        다시 맞혀 볼 수 있었다.
        """
        cutoff = now - self.window
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for key in stale:
            del self._hits[key]
        if len(self._hits) >= MAX_KEYS:
            drop = max(1, int(len(self._hits) * EVICT_SHARE))
            lightest = sorted(self._hits.items(), key=lambda item: (len(item[1]), item[1][-1]))
            for key, _ in lightest[:drop]:
                del self._hits[key]

    def undo(self, key: str) -> None:
        """방금 센 한 번을 되돌린다(로그인에 성공한 것은 실패 횟수에 넣지 않는다)."""
        with self._lock:
            hits = self._hits.get(key)
            if hits:
                hits.pop()

    def check(self, key: str, message: str | None = None) -> None:
        """넘었으면 429. RATE_LIMIT_ENABLED=false 면 세지 않는다.

        message 를 주면 그 글을 보인다. 글 속 {wait} 는 다시 되기까지 남은 때('7분')로 바뀐다."""
        if not get_settings().rate_limit_enabled:
            return
        wait = self.hit(key)
        if wait is not None:
            # 누가(IP·이메일)인지는 남기지 않는다
            logger.warning("rate limit hit: %s", self.name)
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                message.format(wait=wait_text(wait)) if message else TOO_MANY,
                headers={"Retry-After": str(max(1, math.ceil(wait)))},
            )

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class Budget:
    """서버 전체가 최근 window 초 동안 쓸 수 있는 양(받아쓴 초 같은 것). 열쇠 없이 하나로 센다.

    쓴 양은 (시각, 양)으로 두고 창이 지나면 뺀다. RATE_LIMIT_ENABLED=false 거나 상한이 0 이하면
    세지도 막지도 않는다.
    """

    def __init__(self, name: str, window_seconds: float) -> None:
        self.name = name
        self.window = window_seconds
        self._spent: deque[tuple[float, float]] = deque()
        self._total = 0.0
        self._lock = Lock()

    def _expire(self, now: float) -> None:
        while self._spent and self._spent[0][0] <= now - self.window:
            self._total -= self._spent.popleft()[1]
        if not self._spent:
            self._total = 0.0  # 소수를 더하고 빼며 남은 오차를 지운다

    def used(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._expire(now)
            return self._total

    def used_up(self, cap: float, now: float | None = None) -> bool:
        """상한(cap)까지 다 썼는지. 다 썼으면 그 일만 남긴다(누가 걸렸는지는 남기지 않는다)."""
        if not get_settings().rate_limit_enabled or cap <= 0:
            return False
        if self.used(now) < cap:
            return False
        logger.warning("budget used up: %s", self.name)
        return True

    def spend(self, amount: float, now: float | None = None) -> None:
        if not get_settings().rate_limit_enabled or amount <= 0:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            self._expire(now)
            self._spent.append((now, amount))
            self._total += amount

    def reset(self) -> None:
        with self._lock:
            self._spent.clear()
            self._total = 0.0


MINUTE, HOUR = 60, 3600
LOGIN_IP = RateLimit("login_ip", 20, 5 * MINUTE)
# 이메일별은 (이메일, IP) 로 센다: 다른 사람이 틀린 비밀번호를 쌓아 내 로그인을 막지 못하게.
# 여러 IP 에서 한 이메일로 몰리는 것은 시간당 100번까지(비밀번호 맞히기 막기).
LOGIN_EMAIL = RateLimit("login_email", 10, 15 * MINUTE)
LOGIN_EMAIL_ANY_IP = RateLimit("login_email_any_ip", 100, HOUR)
SIGNUP_IP = RateLimit("signup_ip", 10, HOUR)
# 토큰 갱신은 실패하면 앱이 로그아웃한다. 통신사 공용 IP 뒤의 여러 사람을 생각해 넉넉히.
REFRESH_IP = RateLimit("refresh_ip", 300, 5 * MINUTE)
OAUTH_EXCHANGE_IP = RateLimit("oauth_exchange_ip", 60, 5 * MINUTE)
# 글·목소리 메시지 보내기는 함께 센다
MESSAGES = RateLimit("messages", 30, MINUTE)
SPEECH = RateLimit("speech", 30, MINUTE)
# 말로 답하기(질문 답·기억 채우기·고쳐 쓰기 칸의 🎤 → 글). 열쇠는 사용자 id. 받아쓰기에 돈이 들어
# 마구 보내는 것만 막는다. 만들 때 질문이 수십 개라 다 말로 답해도 걸리지 않게 넉넉히
# (2026-10-25 대표가 30번/10분에 걸림 → 올림). 돈의 상한은 서버 전체 TRANSCRIPTION_SECONDS 가
# 맡는다.
TRANSCRIPTIONS = RateLimit("transcriptions", 60, 10 * MINUTE)
# 하루 상한(한 계정이 서버 전체 받아쓰기 상한을 혼자 다 쓰지 못하게. 30초씩이면 1시간 40분)
TRANSCRIPTIONS_DAILY = RateLimit("transcriptions_daily", 200, 24 * HOUR)
# 같은 IP 하루 상한(한 곳에서 계정을 여럿 만들어 돌려 쓰는 것 막기). 통신사 공용 IP 뒤의
# 한 가족이 걸리지 않게 넉넉히.
TRANSCRIPTIONS_IP = RateLimit("transcriptions_ip", 300, 24 * HOUR)
# 서버 전체 최근 24시간 받아쓴 초. 상한은 설정(transcription_daily_seconds).
TRANSCRIPTION_SECONDS = Budget("transcription_seconds", 24 * HOUR)
# 녹음·남긴 글 올리기. 열쇠는 '사용자:어떤 올리기'라 서로 따로 센다.
# 남긴 글은 문자 하나씩 붙여 넣기도 해서 넉넉히.
UPLOADS = RateLimit("uploads", 120, HOUR)
ANALYTICS = RateLimit("analytics", 120, MINUTE)
# 가족 공유: 초대 링크 만들기·다시 보내기(주인 id)와 초대 열기·확인·참여(IP).
# 열쇠는 32바이트 임의 값이라 맞히기는 어렵다. 마구 두드리는 것만 막는다.
SHARE_LINKS = RateLimit("share_links", 20, HOUR)
INVITES_IP = RateLimit("invites_ip", 60, 5 * MINUTE)
# AI 비용이 드는 작업 걸기(전사·분석·만들기·고치기·녹음 더하기·목소리 만들기). 열쇠는
# '대화 상대:작업'. 이미 돌고 있으면 409 라 보통은 닿지 않는다. 끝나자마자 다시 거는 것을 막는다.
JOB_STARTS = RateLimit("job_starts", 20, HOUR)
# 비밀번호 바꾸기(지금 비밀번호 맞히기 막기). 열쇠는 사용자 id.
PASSWORD_CHANGE = RateLimit("password_change", 10, HOUR)
# 기억 인터뷰 신청·취소(사용자 id). 대표가 직접 연락하는 일이라 마구 넣지 못하게.
INTERVIEW_REQUESTS = RateLimit("interview_requests", 10, HOUR)
# 신청만 따로 하루 3번까지(신청·취소를 되풀이해 운영 메일·알림을 쏟아내지 못하게)
INTERVIEW_APPLY_DAILY = RateLimit("interview_apply_daily", 3, 24 * HOUR)
# 대답 평가·고침, 대화 한 줄 남기기, '더 알려 주실 기억' 답·확인(사용자 id). 기억 정리(임베딩)에
# 돈이 들어 마구 보내는 것만 막는다.
FEEDBACK = RateLimit("feedback", 60, 10 * MINUTE)
# 그분 동의 녹음(app/services/subject_consent.py): 확인 단어 발급은 대화 상대마다 하루 10번,
# 녹음 올리기는 사람마다 하루 20번(다시 보내기 포함). 검수로 보내는 수 상한은 따로 있다
SUBJECT_CONSENT_START = RateLimit("subject_consent_start", 10, 24 * HOUR)
SUBJECT_CONSENT_UPLOAD = RateLimit("subject_consent_upload", 20, 24 * HOUR)

ALL = [
    LOGIN_IP,
    LOGIN_EMAIL,
    LOGIN_EMAIL_ANY_IP,
    SIGNUP_IP,
    REFRESH_IP,
    OAUTH_EXCHANGE_IP,
    MESSAGES,
    SPEECH,
    TRANSCRIPTIONS,
    TRANSCRIPTIONS_DAILY,
    TRANSCRIPTIONS_IP,
    UPLOADS,
    ANALYTICS,
    SHARE_LINKS,
    INVITES_IP,
    JOB_STARTS,
    PASSWORD_CHANGE,
    INTERVIEW_REQUESTS,
    INTERVIEW_APPLY_DAILY,
    FEEDBACK,
    SUBJECT_CONSENT_START,
    SUBJECT_CONSENT_UPLOAD,
]


def reset_all() -> None:
    for limit in ALL:
        limit.reset()
    TRANSCRIPTION_SECONDS.reset()


def _strip_port(value: str) -> str:
    """'1.2.3.4:5678'·'[2001:db8::1]:443'·'2001:db8::1:443'(CloudFront 꼴) → 주소만."""
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    if value.count(":") > 1:
        # 괄호 없는 IPv6 는 마지막 콜론 뒤가 포트다. 포트가 없는 주소면 그대로 둔다.
        host = value.rsplit(":", 1)[0]
        for candidate in (host, value):
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
        return host
    return value


def _bucket(host: str) -> str:
    """IPv6 는 한 집·한 서버가 /64 를 통째로 받아 주소를 마음대로 바꿀 수 있다. /64 로 묶어 센다."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if address.version == 6:
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def client_ip(request: Request) -> str:
    """요청한 사람의 IP. CloudFront 가 붙인 값을 쓴다.

    request.client.host 는 uvicorn --proxy-headers 가 X-Forwarded-For 맨 앞(고객이 마음대로 적을 수
    있는 값)으로 바꿔 둔 것이라 믿지 않는다. CloudFront 는 진짜 주소를 맨 뒤에 붙인다.
    """
    viewer = (request.headers.get("cloudfront-viewer-address") or "").strip()
    if viewer:
        host = _strip_port(viewer)
        if host:
            return _bucket(host)
    forwarded = request.headers.get("x-forwarded-for") or ""
    last = forwarded.split(",")[-1].strip()
    if last:
        return _bucket(last)
    return _bucket(request.client.host) if request.client else "unknown"
