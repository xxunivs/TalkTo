"""이메일 가입 때 '메일을 받을 수 있는 주소인지'를 본다(인증 메일은 보내지 않는다).

본다: 자주 틀리는 주소(gmial.com 등), 잠깐 쓰고 버리는 메일, '@' 뒤 도메인이 메일을 받는지(DNS MX).
못 본다: 그 메일함(예: abc123@gmail.com)이 정말 있는지. 그건 메일을 보내 봐야만 안다.
DNS 가 잠깐 안 되면 막지 않고 통과시킨다(가입을 막는 것보다 낫다).
운영에서만 켠다(EMAIL_DOMAIN_CHECK).
"""

import asyncio
import logging
import time

import dns.asyncresolver
import dns.exception
import dns.resolver

logger = logging.getLogger(__name__)

# 자주 틀리는 도메인 → 맞는 도메인
TYPOS = {
    "gmial.com": "gmail.com",
    "gmali.com": "gmail.com",
    "gamil.com": "gmail.com",
    "gmai.com": "gmail.com",
    "gmail.co": "gmail.com",
    "gmail.con": "gmail.com",
    "gmail.cm": "gmail.com",
    "gmaill.com": "gmail.com",
    "naver.con": "naver.com",
    "naver.co": "naver.com",
    "naver.cm": "naver.com",
    "nave.com": "naver.com",
    "navr.com": "naver.com",
    "daum.ner": "daum.net",
    "daum.com": "daum.net",
    "hanmail.ner": "hanmail.net",
    "hanmail.com": "hanmail.net",
    "nate.con": "nate.com",
    "kakao.co": "kakao.com",
    "hotmial.com": "hotmail.com",
    "hotmail.co": "hotmail.com",
    "outlok.com": "outlook.com",
    "icloud.co": "icloud.com",
    "naver.coom": "naver.com",
}
# 잠깐 쓰고 버리는 메일(가입만 하고 연락이 안 된다)
DISPOSABLE = {
    "mailinator.com",
    "guerrillamail.com",
    "guerrillamail.net",
    "sharklasers.com",
    "10minutemail.com",
    "temp-mail.org",
    "tempmail.com",
    "tempmail.net",
    "yopmail.com",
    "trashmail.com",
    "getnada.com",
    "dispostable.com",
    "maildrop.cc",
    "throwawaymail.com",
    "fakeinbox.com",
    "mohmal.com",
    "emailondeck.com",
    "burnermail.io",
    "mail.tm",
    "tempr.email",
    "spambox.us",
}
# 늘 메일을 받는 큰 곳은 DNS 를 묻지 않는다(빠르게)
KNOWN = {
    "gmail.com",
    "naver.com",
    "daum.net",
    "hanmail.net",
    "kakao.com",
    "nate.com",
    "icloud.com",
    "me.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "yahoo.com",
    "yahoo.co.kr",
}

NO_MAIL = "이 이메일 주소로는 메일을 받을 수 없어요. '@' 뒤 주소를 다시 확인해 주세요."
DISPOSABLE_MESSAGE = (
    "잠깐 쓰고 버리는 이메일로는 가입할 수 없어요. 평소에 쓰는 이메일을 넣어 주세요."
)
TIMEOUT_SECONDS = 3.0
CACHE_SECONDS = 3600
MAX_CACHE = 5000
_cache: dict[str, tuple[float, bool]] = {}


def typo_hint(local: str, domain: str) -> str | None:
    fixed = TYPOS.get(domain)
    if fixed is None:
        return None
    return f"혹시 {local}@{fixed} 아닌가요? 이메일 주소를 다시 확인해 주세요."


async def _receives_mail(domain: str) -> bool | None:
    """도메인이 메일을 받는가. True/False, 알 수 없으면(DNS 문제) None."""
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = TIMEOUT_SECONDS
    try:
        answer = await resolver.resolve(domain, "MX")
        # 'MX 0 .'(null MX, RFC 7505)은 메일을 받지 않는다고 밝힌 것이다
        return any(str(r.exchange) not in (".", "") for r in answer)
    except dns.resolver.NXDOMAIN:
        return False
    except dns.resolver.NoAnswer:
        # MX 가 없으면 A/AAAA 로 받는다(RFC 5321). 그것도 없으면 못 받는다.
        for kind in ("A", "AAAA"):
            try:
                await resolver.resolve(domain, kind)
                return True
            except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                continue
            except dns.exception.DNSException:
                return None
        return False
    except dns.exception.DNSException:
        return None


async def problem(email: str) -> str | None:
    """가입을 막아야 할 까닭(화면에 보일 한 문장). 괜찮으면 None."""
    local, _, domain = email.strip().lower().rpartition("@")
    if not local or not domain:
        return NO_MAIL
    hint = typo_hint(local, domain)
    if hint:
        return hint
    if domain in DISPOSABLE:
        return DISPOSABLE_MESSAGE
    if domain in KNOWN:
        return None
    now = time.monotonic()
    cached = _cache.get(domain)
    if cached and now - cached[0] < CACHE_SECONDS:
        return None if cached[1] else NO_MAIL
    try:
        ok = await asyncio.wait_for(_receives_mail(domain), TIMEOUT_SECONDS + 1)
    except TimeoutError:
        ok = None
    if ok is None:
        # 확인을 못 했으면 막지 않는다. 기록만 남긴다(주소는 남기지 않는다).
        logger.warning("email domain check skipped (dns unavailable)")
        return None
    if len(_cache) >= MAX_CACHE:
        _cache.clear()
    _cache[domain] = (now, ok)
    return None if ok else NO_MAIL
