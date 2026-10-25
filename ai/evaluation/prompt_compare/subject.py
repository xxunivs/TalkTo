"""비교 실험의 대상자 설정. 대상자 이름·가족·데이터 위치는 개인 정보라 data/subject.json(git 제외)에 둔다.

data/subject.json 예:
{
  "name": "박영자",                       # 대상자 폴더 이름(input/<name>/, output/<name>/)
  "manual_dir": "박영자_manual",           # 사람이 고친 설문 산출물 폴더(output/ 아래). 없으면 name
  "export_tag": "261011",                  # 운영 DB export 파일 꼬리표(memories_<name>_<tag>.json, personas_<tag>.json,
                                           #   memory_embeddings_<tag>_p*.json)
  "items_file": "items.jsonl",             # 실제 통화에서 뽑은 원본 문항(fact·absent·loo)
  "judge_name": "고인 박영자",             # 판정기에게 알려 줄 대상자(사실 판정용)
  "judge_intro": "고인 박영자(할머니)",     # 판정기에게 알려 줄 대상자 소개(탐침 판정용)
  "judge_family": "남편 이만석, 아들 정우, 손자 도윤"   # 판정기에게 알려 줄 가족(호칭 탐침 판정용)
}
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=1)
def subject() -> dict:
    f = DATA / "subject.json"
    if not f.exists():
        raise SystemExit(f"{f} 가 없다 — subject.py 머리말의 예를 보고 만든다")
    s = json.loads(f.read_text(encoding="utf-8"))
    s.setdefault("manual_dir", s["name"])
    s.setdefault("items_file", "items.jsonl")
    return s
