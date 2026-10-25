"""생성기들이 같이 쓰는 글 다듬기: 따옴표 정리, 같은 주제·같은 내용 알아보기, 호칭 후보 거르기."""

from __future__ import annotations

import re

QUOTES = "\"'“”‘’「」『』"
_QUOTED_ITEM = re.compile(r"[\"“”'‘’]([^\"“”'‘’]+)[\"“”'‘’]")
_SPLIT = re.compile(r"[\s·,/()\[\]~]+")
_PARTICLE = re.compile(r"(에\s?대한|에게|으로|로|의|과|와|을|를|은|는|이|가|도|에)$")
# 주제·성향을 비교할 때 뜻이 없는 말
STOP = {
    "이야기",
    "얘기",
    "문제",
    "정보",
    "민감한",
    "관련",
    "상황",
    "내용",
    "것",
    "등",
    "표현",
    "표현함",
    "애정",
    "중시함",
    "중요하게",
    "여김",
    "함",
    "편",
    "말",
    "먼저",
    "꺼내기",
    "길게",
    "가족",
    "사람",
}
# 주제 앞에 붙는 '그분' 쪽 말(같은 주제인지 볼 때 뺀다)
SUBJECT_REFS = {"그분", "본인", "할머니", "할아버지", "엄마", "아빠", "어머니", "아버지", "대상자"}


def unquote(text: str) -> str:
    return text.strip().strip(QUOTES).strip()


def split_quoted(value: str) -> list[str]:
    """'“아유”' → ['아유'], '“밥 먹었냐?”, “잘될 거야”' → ['밥 먹었냐?', '잘될 거야'].
    따옴표 친 말만으로 된 값이 아니면(설명이 섞이면) 따옴표만 곧은 것으로 바꿔 한 덩어리로 둔다."""
    value = value.strip()
    items = _QUOTED_ITEM.findall(value)
    rest = _QUOTED_ITEM.sub("", value).strip(" ,·")
    if items and not rest:
        return [x.strip() for x in items if x.strip()]
    return [re.sub(r"[“”]", '"', re.sub(r"[‘’]", "'", value))] if value else []


def tokens(text: str, extra_refs: set[str] = frozenset()) -> set[str]:
    out = set()
    for t in _SPLIT.split(unquote(text)):
        t = t.strip(QUOTES)
        if len(t) > 2:
            t = _PARTICLE.sub("", t)
        if len(t) >= 2 and t not in STOP and t not in SUBJECT_REFS and t not in extra_refs:
            out.add(t)
    return out


def _stem_hit(t: str, pool: set[str]) -> bool:
    return any(t == p or t[:2] == p[:2] for p in pool)


def same_topic(a: str, b: str, extra_refs: set[str] = frozenset()) -> bool:
    """'할머니의 건강 문제' 와 '그분의 건강·병력' 처럼 핵심어가 하나라도 겹치면 같은 주제로 본다."""
    ta, tb = tokens(a, extra_refs), tokens(b, extra_refs)
    return bool(ta and tb and (ta & tb or any(_stem_hit(t, tb) for t in ta)))


def mostly_covered(candidate: str, existing: list[str], ratio: float = 0.5) -> bool:
    """후보의 핵심어 절반 이상이 이미 있는 값들에 있으면 같은 내용으로 본다."""
    tc = tokens(candidate)
    pool = set().union(*(tokens(e) for e in existing)) if existing else set()
    if not tc or not pool:
        return False
    return sum(_stem_hit(t, pool) for t in tc) / len(tc) >= ratio


# 호칭 칸에 들어온 서술(예: "이름으로 불렀던 것으로 정리됨")을 알아본다
_DESCRIPTIVE = re.compile(r"것으로|정리|불렀|부르던|부름|이름으로|라고|했다|했음|됨$|함$|음$")


def is_address_term(value: str) -> bool:
    v = unquote(value)
    return bool(v) and len(v) <= 10 and len(v.split()) <= 2 and not _DESCRIPTIVE.search(v)
