"""Persona Intake(설문) -> 사망, 임종 질문 정책 (restricted.json policy 는 intake 답이 없을 때만 대체값)"""

from __future__ import annotations

from dataclasses import dataclass

GRADE_TO_POLICY = {"금지": "deny", "짧게 넘김": "brief", "물으면만": "ask_only", "자유": "open"}
DEATH_TOPIC_KEYS = ("사망", "임종")  # M9-01 사망, 장례, 임종 줄 탐색어
# T1-02 분류 -> 물으면 할 기본 한마디 (restricted.json brief_answer 우선)
# T1-02 말하고 싶지 않음은 등급보다 우선해 deny (설문 안내 문구의 약속)
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
    note: str | None = None  # M9-01 단서 (작성자 원문)
    default_answer: str | None = None


def _ctx(intake: dict) -> dict:
    """b_intake_prompt.json 전체, intakeContext 단독 모두 허용"""
    return intake.get("intakeContext", intake) if isinstance(intake, dict) else {}


def death_policy(intake: dict | None) -> DeathPolicy | None:
    """intake 기반 사망, 임종 정책 (근거 없으면 None -> restricted.json 대체값)"""
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
                return DeathPolicy(
                    policy=policy, source="M9-01", note=t.get("note"), default_answer=answer
                )
    return None
