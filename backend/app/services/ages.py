"""만 19세 미만 대화 상대의 나이 셈(2026-10-22 법률 자문 · WO 4).

만든 분은 아이의 태어난 해와 달("YYYY-MM")만 적는다(basics.birthYm). 날짜를 모르니 **태어난 달의
다음 달 1일**에 태어난 것으로 본다 — 나이를 늦게(어리게) 보아 보호를 길게 둔다.
예: 2012-03생 → 14세 알림 달 2026-03, 새 동의 필요 2026-04-01, 기한(+6개월) 2026-10-01(KST 0시).

이 파일은 intake 를 import 하지 않는다(intake 가 이것을 쓴다 — 서로 부르면 import 가 꼬인다).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Literal

# 한국 시간(일광 절약 시간이 없다). billing.KST 와 같다(Windows 에는 tzdata 가 없어 ZoneInfo 를
# 쓰지 않는다)
KST = timezone(timedelta(hours=9))

Mode = Literal["adult", "minor_teen", "minor_child"]

# intake.SELF_RELATION 과 같다(import 하지 않으려고 따로 둔다)
_SELF_RELATION = "나 자신"
_YM = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


def parse_ym(value: str) -> tuple[int, int]:
    """태어난 해와 달 → (년, 월). 2012-03 → (2012, 3). 형식이 틀리면 ValueError."""
    match = _YM.match((value or "").strip()) if isinstance(value, str) else None
    if match is None:
        raise ValueError("birthYm must be YYYY-MM")
    return int(match.group(1)), int(match.group(2))


def _add_years(day: date, years: int) -> date:
    # 늘 1일이라 윤달 걱정이 없다
    return day.replace(year=day.year + years)


def effective_birth(ym: str) -> date:
    """나이 셈에 쓰는 생일: 태어난 달의 다음 달 1일(보수적으로 늦게 본다)."""
    year, month = parse_ym(ym)
    return date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)


def age_on(ym: str, day: date) -> int:
    """effective_birth 부터 day 까지의 만 나이. 아직 태어나기 전(미래)이면 0 보다 작다(검사가
    그것으로 미래 생년월을 막는다)."""
    born = effective_birth(ym)
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def turning_month(ym: str, years: int) -> date:
    """그 나이가 되는 달의 1일(알림을 보내는 달). 2012-03, 14 → 2026-03-01."""
    year, month = parse_ym(ym)
    return date(year + years, month, 1)


def reconsent_from(ym: str, years: int) -> date:
    """새 동의가 필요해지는 날 = effective_birth + years 년. 2012-03, 14 → 2026-04-01."""
    return _add_years(effective_birth(ym), years)


def today_kst(now: datetime | None = None) -> date:
    """한국 날짜. now 가 시간대 없는 값이면 UTC 로 본다."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(KST).date()


def _living_other(basics: dict) -> bool:
    """살아 계신 분이고 나 자신이 아님(그분 동의 녹음이 필요한 대화 상대)."""
    basics = basics or {}
    return basics.get("status") == "alive" and basics.get("relation") != _SELF_RELATION


def _age(basics: dict, today: date) -> int | None:
    """minor 대화 상대의 나이. 생년월이 없거나 틀리면 None(부르는 쪽이 가장 어리게 본다)."""
    try:
        return age_on(basics.get("birthYm") or "", today)
    except ValueError:
        return None


def consent_mode(basics: dict, today: date) -> Mode | None:
    """그분 동의 녹음의 종류. 돌아가신 분·대화가 어려우신 분(unable)·나 자신이면 None(필요 없음).

    minor 가 True 이고 만 14세 미만 → minor_child, 만 19세 미만 → minor_teen, 그 밖 → adult.
    minor 인데 생년월을 읽지 못하면 가장 엄격한 minor_child 로 본다. minor 키가 없는 예전 대화
    상대는 adult(녹음을 시작하기 전에 미성년 여부를 먼저 묻는 것은 subject_consent 가 한다).
    """
    if not _living_other(basics):
        return None
    if basics.get("minor") is not True:
        return "adult"
    age = _age(basics, today)
    if age is None or age < 14:
        return "minor_child"
    if age < 19:
        return "minor_teen"
    return "adult"


def is_minor_now(basics: dict, today: date) -> bool:
    """지금 만 19세 미만 아이의 대화 상대인가(AI 미성년 안전 규칙을 켠다). 살아 계신 분만 —
    떠나신 자녀는 고인 규칙을 따른다. 생년월을 읽지 못하면 아이로 본다."""
    if not _living_other(basics) or basics.get("minor") is not True:
        return False
    age = _age(basics, today)
    return age is None or age < 19
