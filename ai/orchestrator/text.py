"""한국어 형태소 토큰(사전 판정·가드 공용). kiwipiepy 필요(pip install .[orchestrator]).

사전 판정 임계 0.6 은 아래 품사·불용어 정의로 잰 값이라, 정의를 바꾸면 임계도 다시 재야 한다.
"""
from __future__ import annotations

import threading
from functools import lru_cache

KEEP_TAGS = ("NNG", "NNP", "NR", "SN", "VV", "VA", "XR", "SL", "MAG")
# 이름은 여기 두지 않고 호출 쪽이 extra_stop 으로 더한다.
GENERIC_STOP = frozenset({"할머니", "할아버지", "나", "우리", "때", "거", "것", "기억", "이야기", "얘기",
                          "뭐", "어디", "몇", "누구", "처음", "예전", "같이"})

_lock = threading.Lock()
_kiwi = None


def _get_kiwi():
    global _kiwi
    if _kiwi is None:
        with _lock:
            if _kiwi is None:
                try:
                    from kiwipiepy import Kiwi
                except ImportError as e:  # 설정으로 켰는데 패키지가 없으면 바로 알린다
                    raise RuntimeError("kiwipiepy is required for orchestrator precheck/guard "
                                       "(pip install '.[orchestrator]')") from e
                _kiwi = Kiwi()
    return _kiwi


def morphs(text: str) -> list[str]:
    kiwi = _get_kiwi()
    with _lock:
        tokens = kiwi.tokenize(text)
    return [t.form for t in tokens if t.tag.startswith(KEEP_TAGS)]


def proper_nouns(text: str) -> list[str]:
    """고유명사(NNP, 2글자 이상) — 가드 R1b 용."""
    kiwi = _get_kiwi()
    with _lock:
        tokens = kiwi.tokenize(text)
    out = []
    for i, t in enumerate(tokens):
        if t.tag != "NNP" or len(t.form) < 2:
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if nxt is not None and nxt.tag.startswith("E") and nxt.start == t.start + t.len:
            continue  # 바로 어미가 붙으면 용언을 고유명사로 잘못 본 것("도우면서")
        out.append(t.form)
    return out


@lru_cache(maxsize=8192)
def morph_set(text: str) -> frozenset[str]:
    return frozenset(morphs(text))
