"""출처별 기억(설문, 전사, 추가분) 통합 (source 표시, 중복은 삭제 없이 duplicateOf 표시만)"""

import json
import re
from pathlib import Path

# 파일 이름 앞머리 -> 출처
SOURCE_BY_PREFIX = {
    "b_intake": "intake",
    "e_recording": "recording",
    "extra": "extra",
}
# 같은 기억으로 볼 낱말 겹침 비율
DUPLICATE_RATIO = 0.6
# 최소 낱말 수 (한 낱말짜리 기억의 오탐 방지)
DUPLICATE_MIN_WORDS = 4


def _source_of(filename: str) -> str:
    for prefix, source in SOURCE_BY_PREFIX.items():
        if filename.startswith(prefix):
            return source
    return "unknown"


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[\s,.·…!?\"'()\[\]]+", text) if len(w) > 1}


def _looks_same(a: str, b: str) -> bool:
    """낱말 겹침 비율 비교 (다르게 적은 같은 일을 잡도록 느슨하게)"""
    wa, wb = _words(a), _words(b)
    if min(len(wa), len(wb)) < DUPLICATE_MIN_WORDS:
        return False
    return len(wa & wb) / min(len(wa), len(wb)) >= DUPLICATE_RATIO


def merge_memory(extraction_dir: Path) -> list[dict]:
    """s2_extraction 의 *_memory.json 병합 + source, duplicateOf 부여"""
    merged: list[dict] = []
    for path in sorted(extraction_dir.glob("*_memory.json")):
        if path.name == "a_memory.json":  # 자기 산출물 제외
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
