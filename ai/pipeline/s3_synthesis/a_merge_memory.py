"""출처가 다른 기억을 한 장으로 모은다.

설문(b_intake_memory), 전사(e_recording_memory), 따로 넣은 것(extra_memory) 이
같은 자리에 흩어져 있다. 뒤 단계가 글롭으로 직접 모으면 같은 기억을 두 번 세거나
어디서 온 것인지 알 수 없다. 여기서 한 번 모아 source 를 붙이고 겹치는 것을
표시해 두면, 사람이 파일을 열어 확인할 수 있다.

겹치는 것은 지우지 않는다. 어느 쪽을 남길지는 사람이 정해야 한다.
"""

import json
import re
from pathlib import Path

# 파일 이름 앞머리 → 출처
SOURCE_BY_PREFIX = {
    "b_intake": "intake",
    "e_recording": "recording",
    "extra": "extra",
}
# 두 기억이 같은 것으로 볼 낱말 겹침 비율
DUPLICATE_RATIO = 0.6
# 낱말이 이보다 적으면 견주지 않는다. "영화" 같은 한 낱말짜리가 아무거나 물어온다.
DUPLICATE_MIN_WORDS = 4


def _source_of(filename: str) -> str:
    for prefix, source in SOURCE_BY_PREFIX.items():
        if filename.startswith(prefix):
            return source
    return "unknown"


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[\s,.·…!?\"'()\[\]]+", text) if len(w) > 1}


def _looks_same(a: str, b: str) -> bool:
    """겹치는 낱말 비율로 본다. 같은 일을 다르게 적은 것을 잡으려는 것이라 느슨하다."""
    wa, wb = _words(a), _words(b)
    if min(len(wa), len(wb)) < DUPLICATE_MIN_WORDS:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= DUPLICATE_RATIO


def merge_memory(extraction_dir: Path) -> list[dict]:
    """s2_extraction 의 *_memory.json 을 모아 source·duplicateOf 를 붙인다."""
    merged: list[dict] = []
    for path in sorted(extraction_dir.glob("*_memory.json")):
        if path.name == "a_memory.json":  # 이 도구가 낸 것은 다시 읽지 않는다
            continue
        source = _source_of(path.name)
        for row in json.loads(path.read_text(encoding="utf-8")):
            row = dict(row)
            row["source"] = source
            row["id"] = f"{source}-{row.get('id')}"
            merged.append(row)

    for i, row in enumerate(merged):
        text = row.get("memoryText") or row.get("memory_text") or ""
        for earlier in merged[:i]:
            if earlier.get("duplicateOf"):
                continue
            other = earlier.get("memoryText") or earlier.get("memory_text") or ""
            if _looks_same(text, other):
                row["duplicateOf"] = earlier["id"]
                break
    return merged
