"""제한 정보(사인·임종 경위 등) — 페르소나 프롬프트·기억과 따로 둔 파일 하나로 관리한다.

위치: <ORCHESTRATOR_PERSONA_DIR>/<이름>/restricted.json (개인 정보 → git 제외 경로)
{
  "policy": "brief",                      # deny: 직접 물어도 말하지 않음(공통 규칙 그대로)
                                          # brief: 직접 물으면 brief_answer 정도만 말하고 넘어감
                                          # ask_only: 먼저 꺼내지 않고 물으면 담담하게 한두 마디
                                          # open: 공통 규칙 그대로
                                          # ※ intake.json(M9-01·T1-02)이 있으면 그 값이 우선(intake_policy.py)
  "brief_answer": "몸이 안 좋아서 그렇게 갔지",
  "facts": {...},                         # 원본 사실(모델에게는 안 보냄, 사람·가드용 기록)
  "replace": [{"from": "원문 문장", "to": "바꿀 문장(빈 문자열이면 삭제)"}],   # 페르소나 지시문에서
  "memory_ids": ["..."],                  # 프롬프트에 넣지 않을 기억 id
  "leak_terms": ["폐렴", ...]          # 이 말이 든 기억은 빼고, 가드가 출력에서 검사
}
적용: structured 경로에서만(legacy 는 기존과 동일). 기억 거르기는 RestrictedSelector, 지시문 바꾸기는 조립기·사전 판정.
"""
from __future__ import annotations

import logging
import re

from orchestrator.contracts import MemoryItem, MemorySelection, RestrictedFacts, SessionState, TurnInput

log = logging.getLogger(__name__)


def redact_instructions(instructions: str, restricted: RestrictedFacts | None) -> tuple[str, list[str]]:
    """지시문에서 제한 문장을 바꾼다. (결과, 원문에서 못 찾은 from 목록) — 못 찾으면 프롬프트가 바뀐 것."""
    if restricted is None:
        return instructions, []
    text, missing, changed = instructions, [], False
    for r in restricted.replace:
        if r.from_ in text:
            text = text.replace(r.from_, r.to)
            changed = True
        else:
            missing.append(r.from_)
    if missing and (changed or not _generated(instructions)):  # 손으로 쓴 지시문이 바뀌었다는 신호
        log.warning("restricted replace not found in instructions: %d item(s)", len(missing))
    elif missing:  # 4층 생성기 지시문에는 그 문장이 애초에 없으니 조용히 넘어간다
        log.debug("restricted replace: none of %d sentence(s) in generated instructions", len(missing))
    if changed:  # 문장을 지운 자리에 남은 이중 공백만 정리(들여쓰기·줄바꿈은 그대로)
        text = "\n".join(_tidy(ln) for ln in text.split("\n"))
    return text, missing


def _generated(instructions: str) -> bool:
    """4층 생성기(pipeline/persona_prompt)가 만든 지시문인가 — L2 의 주제 등급·L3 가족 표 머리로 알아본다."""
    return "# 주제별로 어디까지 말할지" in instructions or "# 가족 (이 표에 있는 사람만 안다" in instructions


def _tidy(line: str) -> str:
    indent = len(line) - len(line.lstrip(" "))
    body = re.sub(r" {2,}", " ", line[indent:]).rstrip()
    body = re.sub(r" ([,.)])", r"\1", body)
    return line[:indent] + body


def is_restricted_memory(m: MemoryItem, restricted: RestrictedFacts) -> bool:
    if m.id in restricted.memory_ids:
        return True
    body = f"{m.title} {m.text}"
    return any(t in body for t in restricted.leak_terms)


def brief_rule(restricted: RestrictedFacts | None) -> str | None:
    """페르소나별 사망·임종 질문 규칙. deny·open 은 공통 규칙 그대로라 따로 붙이지 않는다."""
    if restricted is None or restricted.policy not in ("brief", "ask_only") or not restricted.brief_answer:
        return None
    head = "# 돌아가신 경위 질문 (이 페르소나 설정 — 공통 규칙의 사망·임종 항목보다 우선)\n"
    detail = "병명·병원·시술·날짜·임종 자리에 있던 사람 같은 세부는 말하지 않는다. 먼저 꺼내지 않는다."
    if restricted.policy == "brief":
        body = (f"돌아가신 경위를 직접 물으면 \"{restricted.brief_answer}\" 정도로만 짧게 답하고 바로 가족 안부로 넘어간다. "
                + detail)
    else:  # ask_only: 먼저 꺼내지 않고, 물으면 담담하게 한두 마디
        body = (f"돌아가신 일은 먼저 꺼내지 않는다. 가족이 물으면 피하지 말고 \"{restricted.brief_answer}\" 정도로 "
                "담담하게 한두 마디 답한다. 슬픔을 키우지 말고 가족 안부로 이어 간다. " + detail)
    if restricted.policy_note:
        body += f"\n(설문 단서: {restricted.policy_note})"
    return head + body


class RestrictedSelector:
    """안쪽 선택기 결과에서 제한 기억을 뺀다(사전 판정·조립 모두 걸러진 기억을 본다)."""

    def __init__(self, inner, profiles) -> None:
        self.inner = inner
        self.profiles = profiles
        self.impl = f"{inner.impl}+restricted"

    def select(self, turn: TurnInput, state: SessionState, store) -> MemorySelection:
        sel = self.inner.select(turn, state, store)
        restricted = self.profiles.profile_for(turn).restricted
        if restricted is None:
            return sel
        kept = [m for m in sel.memories if not is_restricted_memory(m, restricted)]
        delta = [m for m in sel.delta if not is_restricted_memory(m, restricted)]
        dropped = len(sel.memories) - len(kept) + len(sel.delta) - len(delta)
        return MemorySelection(memories=kept, delta=delta,
                               strategy=sel.strategy + (f"|restricted-{dropped}" if dropped else ""))
