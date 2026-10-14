"""④ 프롬프트 조립.

LegacyAssembler: 기존 generate_persona_response 와 같은 메시지를 만든다(동일성 테스트로 고정).
  [system 페르소나 지시문] [system 참고 기억 블록(있으면)] [history…] [user 발화]
사전 판정 directive·델타 기억이 있으면 별도 system 절로 붙인다(둘 다 없으면 기존과 같다).
StructuredAssembler: 공통 규칙 → 페르소나 → 말투 → 기억 → 델타 → 세션 상태 순으로 조립한다.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

from orchestrator.contracts import (
    MemoryItem, MemorySelection, PrecheckResult, PromptBundle, PromptSection, SessionState, TurnInput,
)
from pipeline.s3_synthesis.c_build_system_prompt import build_memory_block
from orchestrator.guard import HEADER_RULE, header_block
from orchestrator.restricted import brief_rule, redact_instructions
from pipeline.persona_prompt.layers import SHOTS_MODES, RuntimeContext, runtime_section
from pipeline.s3_synthesis.common_rules import common_rule_lines, render_common_rules, render_rules


def legacy_memory_block(memories: list[MemoryItem]) -> str:
    return build_memory_block([SimpleNamespace(title=m.title, content=m.text) for m in memories])


def delta_block(delta: list[MemoryItem]) -> str:
    if not delta:
        return ""
    lines = []
    for m in delta:
        who = f"({m.told_by}{'·' + m.relation if m.relation else ''}이 알려 줌) " if m.told_by else ""
        lines.append(f"- {who}{m.text.strip()}")
    return "이번 통화에서 새로 들은 것:\n" + "\n".join(lines)


class LegacyAssembler:
    impl = "legacy_v0"

    def assemble(self, turn: TurnInput, state: SessionState, selection: MemorySelection,
                 precheck: PrecheckResult) -> PromptBundle:
        sections = [PromptSection(name="persona", role="system", content=turn.persona_instructions)]
        block = legacy_memory_block(selection.memories)
        if block:
            sections.append(PromptSection(name="memories", role="system", content=block))
        dblock = delta_block(selection.delta)
        if dblock:
            sections.append(PromptSection(name="delta", role="system", content=dblock))
        if precheck.directive:
            sections.append(PromptSection(name="directive", role="system", content=precheck.directive))
        for i, t in enumerate(turn.history):
            sections.append(PromptSection(name=f"history{i}", role=t.role, content=t.content))
        sections.append(PromptSection(name="message", role="user", content=turn.message))
        return PromptBundle(sections=sections)


# ---------------------------------------------------------------- structured_v1

def dedupe_rules(instructions: str, rule_lines: set[str]) -> str:
    """페르소나 지시문에서 공통 규칙과 글자 그대로 같은 줄을 지우고, 비게 된 제목 줄도 지운다."""
    lines = instructions.splitlines()
    kept = [ln for ln in lines if ln.strip() not in rule_lines]
    if len(kept) == len(lines):
        return instructions  # 지운 게 없으면 원문 그대로(손으로 쓴 프롬프트)
    out: list[str] = []
    for i, ln in enumerate(kept):
        if ln.lstrip().startswith("#"):
            nxt = next((x for x in kept[i + 1:] if x.strip()), None)
            if nxt is None or nxt.lstrip().startswith("#"):
                continue  # 내용 없는 제목
        out.append(ln)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def shots_text(shots, self_label: str | None) -> str:
    who = self_label or "대상자"
    lines = [f"# 실제 통화에서 {who}가 한 말 (말투만 참고, 내용은 따라 하지 말 것)"]
    for s in shots:
        lines.append(f"가족: {s.user}\n{who}: {s.persona}")
    return "\n".join(lines)


class StructuredAssembler:
    """공통 규칙 md → 페르소나(중복 제거) → 말투 카드·실제 발화 → 기억 → 델타 → 지시 → 세션 → 최근 턴 → 발화.

    첫 system 메시지는 "# 공통 규칙 …" + "# 페르소나" + 지시문이고, 말투 카드·실제 발화도 같은 메시지 뒤에 붙인다.
    정적인 것을 앞에 둬 프롬프트 캐시를 살린다.
    """
    impl = "structured_v1"

    def __init__(self, profiles, *, max_recent_turns: int = 6, header: bool = False, rules: str = "legacy",
                 shots_mode: str = "system") -> None:
        self.profiles = profiles
        # shots_mode="messages": 실제 주고받기를 지시문 글 대신 기억 뒤 user/assistant 메시지로 넣는다
        if shots_mode not in SHOTS_MODES:
            raise ValueError(f"shots_mode must be one of {SHOTS_MODES}")
        self.shots_mode = shots_mode
        # rules="layered": 4층 L1 모듈(prompts/rules layered 세트). 사인 정책은 별도 절 대신 모듈 변형으로 넣는다.
        self.rules = rules
        self.max_recent_turns = max_recent_turns
        # header=True(ORCHESTRATOR_GUARD=v1): 기억에 (m번호) id 를 붙이고 첫 줄 근거 헤더를 요구한다
        self.header = header
        if header:
            self.impl = "structured_v1+header"
        if rules == "layered":
            self.impl += "+layered"
        if shots_mode == "messages":
            self.impl += "+shotmsgs"

    def assemble(self, turn: TurnInput, state: SessionState, selection: MemorySelection,
                 precheck: PrecheckResult) -> PromptBundle:
        prof = self.profiles.profile_for(turn)
        flags = dict(public=prof.public, deceased=prof.deceased, has_family=prof.has_family)
        instructions, _ = redact_instructions(turn.persona_instructions, prof.restricted)
        if self.rules == "layered":
            r = prof.restricted
            rules = render_rules("layered", **flags,
                                 death_policy=(r.policy if r is not None and prof.deceased else "deny"),
                                 vars={"death_answer": (r.brief_answer if r is not None else None) or "그렇게 됐지",
                                       "family_list": "아래 가족 표"})
            rule_lines = {ln.strip() for ln in rules.splitlines() if ln.strip().startswith("- ")}
            extra = None  # 사인 정책은 L1 사망 모듈 변형이 맡는다
        else:
            rules = render_common_rules(**flags)
            rule_lines = common_rule_lines(**flags)
            extra = brief_rule(prof.restricted)
        persona = dedupe_rules(instructions, rule_lines)
        # legacy 머리말은 측정한 프롬프트와 글자까지 같게 둔다
        label = "prompts/rules" if self.rules == "layered" else "shared_rules.md"
        head = f"# 공통 규칙 (모든 페르소나 공통, {label})\n" + rules + "\n\n# 페르소나\n" + persona
        if extra:
            head += "\n\n" + extra
        # 지시문(4층 생성기 결과 등)에 이미 들어 있으면 다시 붙이지 않는다
        if prof.style_card and prof.style_card.splitlines()[0] not in persona:
            head += "\n\n" + prof.style_card
        shots_in_text = bool(prof.shots) and f"가족: {prof.shots[0].user}\n" in persona
        if prof.shots and self.shots_mode == "system" and not shots_in_text:
            head += "\n\n" + shots_text(prof.shots, prof.self_label)
        sections = [PromptSection(name="rules+persona", role="system", content=head)]

        if self.header:
            head += "\n\n" + HEADER_RULE
            sections = [PromptSection(name="rules+persona", role="system", content=head)]
            block = header_block(selection)  # 델타도 같은 번호 체계 안에 넣는다
            if block:
                sections.append(PromptSection(name="memories", role="system", content=block))
        else:
            block = legacy_memory_block(selection.memories)
            if block:
                sections.append(PromptSection(name="memories", role="system", content=block))
            dblock = delta_block(selection.delta)
            if dblock:
                sections.append(PromptSection(name="delta", role="system", content=dblock))
        if prof.shots and self.shots_mode == "messages" and not shots_in_text:
            for i, sh in enumerate(prof.shots):
                sections += [PromptSection(name=f"shot{i}u", role="user", content=sh.user),
                             PromptSection(name=f"shot{i}a", role="assistant", content=sh.persona)]
        if precheck.directive:
            sections.append(PromptSection(name="directive", role="system", content=precheck.directive))
        history = turn.history or state.recent_turns[-self.max_recent_turns:]
        session = self.session_text(prof, state, history)
        if session:
            sections.append(PromptSection(name="session", role="system", content=session))
        for i, t in enumerate(history):
            sections.append(PromptSection(name=f"history{i}", role=t.role, content=t.content))
        sections.append(PromptSection(name="message", role="user", content=turn.message))
        return PromptBundle(sections=sections)

    def session_text(self, prof, state: SessionState, history) -> str:
        """턴마다 바뀌는 절. layered 이고 등록부에 명세가 있으면 layers.json 의 런타임 모듈로 만든다.
        명세가 없으면(손으로 쓴 지시문) 가족 표를 모르니 화자 이름만 알린다."""
        if self.rules == "layered" and prof.persona_spec is not None:
            ctx = RuntimeContext(speaker=state.speaker, history=[(t.role, t.content) for t in history])
            return runtime_section(prof.persona_spec, ctx)
        return f"# 지금 상황\n- 지금 말하는 사람: {state.speaker}" if state.speaker else ""
