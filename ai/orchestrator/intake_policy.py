"""Persona Intake(설문) → 사망·임종 질문 정책.

민감 정보에 대한 답변 방식은 Persona Intake 로 정한다.
그래서 restricted.json 의 policy 는 intake 에 답이 없을 때만 쓰는 대체값이고,
intake 에 답이 있으면 아래 규칙으로 정책을 정한다.

    M9-01 "그분의 사망·장례·임종" 등급     → policy
      금지        → deny      (직접 물어도 말하지 않음)
      짧게 넘김   → brief     (한두 마디로 받고 넘어감)
      물으면만    → ask_only  (먼저 꺼내지 않고, 물으면 담담하게 짧게)
      자유        → open      (먼저 꺼내도 됨. 의료 세부 금지는 공통 규칙대로)
    T1-02 "말하고 싶지 않음"                → deny (설문 안내 문구의 약속, 등급보다 우선)

사실(T1-01 날짜·T1-02 경위·T1-03 곁에 있던 가족)은 여기서 프롬프트로 보내지 않는다.
답할 때 쓰는 말은 restricted.json 의 brief_answer 가 있으면 그것, 없으면 T1-02 분류별 기본 문장.
"""
from __future__ import annotations

from dataclasses import dataclass

GRADE_TO_POLICY = {"금지": "deny", "짧게 넘김": "brief", "물으면만": "ask_only", "자유": "open"}
DEATH_TOPIC_KEYS = ("사망", "임종")  # M9-01 의 "그분의 사망·장례·임종" 줄을 찾는 말
# T1-02 분류 → 물으면 할 법한 기본 한마디(손으로 고칠 수 있게 restricted.json brief_answer 가 우선)
DEFAULT_ANSWER = {
    "오래 투병": "오래 아프다가 그렇게 갔지",
    "수술·치료": "병원에서 치료받다가 그렇게 갔지",
    "갑작스럽게": "갑자기 그렇게 됐지",
    "노환": "나이 들어서 편하게 갔지",
}
DECLINED = "말하고 싶지 않음"


@dataclass(frozen=True)
class DeathPolicy:
    policy: str  # deny | brief | ask_only | open
    source: str  # "M9-01" | "T1-02"
    note: str | None = None  # M9-01 단서(사람이 적은 말 그대로)
    default_answer: str | None = None


def _ctx(intake: dict) -> dict:
    """b_intake_prompt.json 전체든 intakeContext 만이든 받는다."""
    return intake.get("intakeContext", intake) if isinstance(intake, dict) else {}


def death_policy(intake: dict | None) -> DeathPolicy | None:
    """intake 에서 사망·임종 정책을 읽는다. 정할 근거가 없으면 None(→ restricted.json 대체값)."""
    if not intake:
        return None
    ctx = _ctx(intake)
    death_context = (ctx.get("basicProfile") or {}).get("deathContext") or ""
    if DECLINED in death_context:
        return DeathPolicy(policy="deny", source="T1-02")
    answer = next((v for k, v in DEFAULT_ANSWER.items() if k in death_context), None)
    for t in ctx.get("tabooTopics") or []:
        if not isinstance(t, dict) or t.get("sourceQuestion") != "M9-01":
            continue
        topic = t.get("topic") or ""
        if any(k in topic for k in DEATH_TOPIC_KEYS):
            policy = GRADE_TO_POLICY.get((t.get("grade") or "").strip())
            if policy:
                return DeathPolicy(policy=policy, source="M9-01", note=t.get("note"), default_answer=answer)
    return None
