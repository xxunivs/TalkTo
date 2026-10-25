"""확장 세트 — 규칙·관계 탐침 질문(실제 통화에는 거의 없는 상황을 일부러 만든 것).

원본 세트(실제 통화 가족 질문)로는 호칭·사인 질문·자해 암시·모르는 사람 같은 규칙 상황을 볼 수 없어서 만든다.
그래서 이 세트의 점수는 '원본 데이터만 쓴 결과'와 따로 보고한다.

필드
  pid      문항 id. 그룹 머리글자: R 관계·호칭 / D 사후 응답 / S 안전 / U 모르는 일 / F 형식·기타 / M 여러 턴
  turns    가족이 하는 말(차례대로). 여러 턴이면 페르소나 답을 history 로 이어 붙여 한 턴씩 생성한다.
  rubric   판정기(LLM)가 '모두 지켰나'를 보는 기준. 운영 수제 프롬프트·공통 규칙·설문 어느 한쪽 문구가 아니라
           셋이 공통으로 요구하는 행동만 적었다(한쪽 프롬프트에 유리하지 않게).
  must_any 마지막 답(여러 턴이면 아무 턴)에 이 중 하나는 있어야 함(코드 검사)
  must_not 어느 답에도 있으면 안 됨(코드 검사)
  korean   답의 한글 비율 0.6 이상이어야 함

문항은 대상자 가족 이름이 들어가 개인 데이터라 data/probes.json(git 제외)에 둔다.
    {"leak_terms": [...], "drug_terms": [...], "probes": [{"pid": "R1", "turns": [...], "rubric": [...], ...}, ...]}
"""
from __future__ import annotations

import json
from pathlib import Path

PROBES_FILE = Path(__file__).resolve().parent / "data" / "probes.json"


def load_probes() -> list[dict]:
    if not PROBES_FILE.exists():
        raise SystemExit(f"{PROBES_FILE} 가 없다 — 위 형식으로 탐침 문항을 만든다")
    return json.loads(PROBES_FILE.read_text(encoding="utf-8"))["probes"]
