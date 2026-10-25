"""④ 프롬프트 조립 (Legacy: 기존 generate_persona_response 와 동일 메시지, Structured: 공통 규칙 기반)"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from types import SimpleNamespace

from orchestrator.contracts import (
    MemoryItem,
    MemorySelection,
    PrecheckResult,
    PromptBundle,
    PromptSection,
    SessionState,
    TurnInput,
)
from orchestrator.guard import HEADER_RULE, _deceased, header_block
from orchestrator.persona import has_layered_rules, infer_profile
from orchestrator.precheck import sent_memories
from orchestrator.restricted import brief_rule, redact_instructions
from orchestrator.text import listener_speech_level
from orchestrator.voice import VoiceMarks
from pipeline.persona_prompt.layers import SHOTS_MODES, RuntimeContext, runtime_section
from pipeline.persona_prompt.low_memory import LOW_MEMORY_MAX, low_memory_note, memory_count
from pipeline.s3_synthesis.c_build_system_prompt import build_memory_block, late_style_note
from pipeline.s3_synthesis.common_rules import (
    DEATH_POLICIES,
    RULES_DIR,
    render_block_file,
    render_common_rules,
    render_rules,
)
from pipeline.s3_synthesis.e_memory_gap import extra_system
from pipeline.safety.minor_guard import MINOR_NOTE


def legacy_memory_block(memories: list[MemoryItem]) -> str:
    return build_memory_block(
        [SimpleNamespace(id=m.id, title=m.title, content=m.text) for m in memories]
    )


def delta_block(delta: list[MemoryItem]) -> str:
    if not delta:
        return ""
    lines = []
    for m in delta:
        who = (
            f"({m.told_by}{'·' + m.relation if m.relation else ''}이 알려 줌) " if m.told_by else ""
        )
        lines.append(f"- {who}{m.text.strip()}")
    return "이번 통화에서 새로 들은 것:\n" + "\n".join(lines)


def voice_section(voice: VoiceMarks | None, turn: TurnInput) -> list[PromptSection]:
    """음성 지침 system 한 개 (FISH_EMOTION cue, inline 이고 Fish 목소리일 때, 정적이라 지시문 바로 뒤)"""
    instruction = voice.instruction(turn) if voice is not None else None
    return [PromptSection(name="voice", role="system", content=instruction)] if instruction else []


def extra_sections(turn: TurnInput) -> list[PromptSection]:
    """빈틈 표시 지침, 고친 말투, 미성년 규칙 system 절 (기존 경로와 같은 자리, 정적이라 기억 앞에 두어 캐시 유지)"""
    names = ("gap", "style") if turn.flag_gaps else ("style",)
    texts = extra_system(turn.flag_gaps, turn.style_examples)
    sections = [
        PromptSection(name=name, role="system", content=text) for name, text in zip(names, texts)
    ]
    # 이미 조립된 지침(L1 에 규칙 없던 시절 생성)에도 적용되도록 매 턴 추가
    if turn.minor:
        sections.append(PromptSection(name="minor", role="system", content=MINOR_NOTE))
    return sections


def turn_memory_count(
    turn: TurnInput, selection: MemorySelection, low_max: int | None
) -> int | None:
    """페르소나 기억 수 (BE memoryTotal, 없으면 받은 수로 추정, low_max=None 이면 None)"""
    if low_max is None:
        return None
    return memory_count(sent_memories(turn, selection), turn.memory_total)


def low_memory_text(turn: TurnInput, prof, n: int | None, low_max: int | None) -> str:
    """기억이 적을 때 절 (기억 수 low_max 이하만, 사망 판정은 가드와 동일, 비공개 사망 대상자는 잠금 2 문구)"""
    if n is None or low_max is None or n > low_max:
        return ""
    return low_memory_note(
        n,
        public=prof.public,
        deceased=_deceased(turn, prof),
        polite=listener_speech_level(turn.persona_instructions) == "존댓말",
    )


class LegacyAssembler:
    impl = "legacy_v0"

    def __init__(
        self,
        *,
        late_note: bool = False,
        voice: VoiceMarks | None = None,
        low_memory_max: int | None = LOW_MEMORY_MAX,
    ) -> None:
        # STYLE_LATE_NOTE=on: 기존 경로처럼 마지막 말 앞 말투 덧붙임
        self.late_note = late_note
        # FISH_EMOTION: 기존 경로와 같은 자리(지시문 바로 뒤) 음성 지침
        self.voice = voice
        self.low_memory_max = low_memory_max  # None = LOW_MEMORY_NOTE=off

    def assemble(
        self,
        turn: TurnInput,
        state: SessionState,
        selection: MemorySelection,
        precheck: PrecheckResult,
    ) -> PromptBundle:
        sections = [PromptSection(name="persona", role="system", content=turn.persona_instructions)]
        sections += voice_section(self.voice, turn)
        sections += extra_sections(turn)
        block = legacy_memory_block(selection.memories)
        if block:
            sections.append(PromptSection(name="memories", role="system", content=block))
        dblock = delta_block(selection.delta)
        if dblock:
            sections.append(PromptSection(name="delta", role="system", content=dblock))
        if precheck.directive:
            sections.append(
                PromptSection(name="directive", role="system", content=precheck.directive)
            )
        for i, t in enumerate(turn.history):
            sections.append(PromptSection(name=f"history{i}", role=t.role, content=t.content))
        # 기억이 적을 때 절: 기존 경로와 같은 자리(앞 대화 뒤, 말투 덧붙임 앞), 등록부 없어 지시문으로 추정
        lm = low_memory_text(
            turn,
            infer_profile(turn.persona_instructions),
            turn_memory_count(turn, selection, self.low_memory_max),
            self.low_memory_max,
        )
        if lm:
            sections.append(PromptSection(name="low_memory", role="system", content=lm))
        note = late_style_note(turn.persona_instructions) if self.late_note else ""
        if note:
            sections.append(PromptSection(name="late_style", role="system", content=note))
        sections.append(PromptSection(name="message", role="user", content=turn.message))
        return PromptBundle(sections=sections)


# ---------------------------------------------------------------- structured_v1


def dedupe_rules(instructions: str, rule_lines: set[str]) -> str:
    """지시문에서 공통 규칙과 같은 줄, 비게 된 제목 줄 제거"""
    lines = instructions.splitlines()
    kept = [ln for ln in lines if ln.strip() not in rule_lines]
    if len(kept) == len(lines):
        return instructions  # 지운 것 없으면 원문 유지 (손으로 쓴 프롬프트)
    out: list[str] = []
    for i, ln in enumerate(kept):
        if ln.lstrip().startswith("#"):
            nxt = next((x for x in kept[i + 1 :] if x.strip()), None)
            if nxt is None or nxt.lstrip().startswith("#"):
                continue  # 내용 없는 제목
        out.append(ln)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


@lru_cache(maxsize=1)
def death_rule_lead() -> str:
    """공통 규칙 death 모듈 네 변형의 공통 앞부분 (이 글로 시작하면 사망, 임종 규칙 줄)"""
    lines = [
        ln.strip()
        for p in DEATH_POLICIES
        for ln in render_block_file(
            RULES_DIR / "death.md", {"deceased", f"death_{p}"}, {"death_answer": "-"}
        )
    ]
    lead = os.path.commonprefix(lines) if lines else ""
    # 변형끼리 너무 다르면 미사용 (엉뚱한 줄 매칭 방지)
    return lead if len(lead) >= 20 else ""


def adopt_death_rule(rules: str, instructions: str) -> str:
    """지시문의 사망, 임종 규칙 줄로 공통 규칙의 그 줄 교체 (dedupe 후 서로 다른 사망 규칙 두 줄 방지)"""
    lead = death_rule_lead()
    own = list(
        dict.fromkeys(
            ln.strip() for ln in instructions.splitlines() if lead and ln.strip().startswith(lead)
        )
    )
    if not own:
        return rules
    out: list[str] = []
    placed = False
    for ln in rules.split("\n"):
        if ln.strip().startswith(lead):
            if not placed:
                out.extend(own)
                placed = True
            continue
        out.append(ln)
    return "\n".join(out)


def shots_text(shots, self_label: str | None) -> str:
    who = self_label or "대상자"
    lines = [f"# 실제 통화에서 {who}가 한 말 (말투만 참고, 내용은 따라 하지 말 것)"]
    for s in shots:
        lines.append(f"가족: {s.user}\n{who}: {s.persona}")
    return "\n".join(lines)


class StructuredAssembler:
    """공통 규칙 -> 페르소나 -> 말투 -> 기억 -> 델타 -> 지시 -> 세션 -> 최근 턴 -> 발화 (정적인 것 앞, 캐시 유지)"""

    impl = "structured_v1"

    def __init__(
        self,
        profiles,
        *,
        max_recent_turns: int = 6,
        header: bool = False,
        rules: str = "legacy",
        shots_mode: str = "system",
        late_note: bool = False,
        voice: VoiceMarks | None = None,
        low_memory_max: int | None = LOW_MEMORY_MAX,
    ) -> None:
        self.profiles = profiles
        # None = LOW_MEMORY_NOTE=off (런타임 모듈 low_memory 도 같은 값)
        self.low_memory_max = low_memory_max
        # STYLE_LATE_NOTE=on: 마지막 말 앞 말투 덧붙임 (등록부 카드도 head 기준 판정)
        self.late_note = late_note
        self.voice = voice  # FISH_EMOTION: 첫 system(규칙+페르소나) 바로 뒤 음성 지침
        # shots_mode="messages": 실제 주고받기를 지시문 대신 기억 뒤 user, assistant 메시지로
        if shots_mode not in SHOTS_MODES:
            raise ValueError(f"shots_mode must be one of {SHOTS_MODES}")
        self.shots_mode = shots_mode
        # rules="layered": 4층 L1 모듈 (prompts/rules layered 세트, 사인 정책은 모듈 변형으로)
        self.rules = rules
        self.max_recent_turns = max_recent_turns
        # header=True(ORCHESTRATOR_GUARD=v1): 기억에 (m번호) id 부여, 첫 줄 근거 헤더 요구
        self.header = header
        if header:
            self.impl = "structured_v1+header"
        if rules == "layered":
            self.impl += "+layered"
        if shots_mode == "messages":
            self.impl += "+shotmsgs"

    def assemble(
        self,
        turn: TurnInput,
        state: SessionState,
        selection: MemorySelection,
        precheck: PrecheckResult,
    ) -> PromptBundle:
        prof = self.profiles.profile_for(turn)
        # 미성년 규칙은 L1 안전 절 self_harm 뒤에도 추가 (사망 대상자면 공통 규칙이 제외)
        flags = dict(
            public=prof.public, deceased=prof.deceased, has_family=prof.has_family, minor=turn.minor
        )
        instructions, _ = redact_instructions(turn.persona_instructions, prof.restricted)
        if (
            self.rules == "layered"
            and prof.source == "inferred"
            and has_layered_rules(instructions)
        ):
            # 등록부 없는 4층 지시문: L1(사망 정책 포함) 이미 포함, 다시 붙이면 사망 정책 두 벌
            # (앞 deny + 저장 ask_only 등), 추정 가족 유무로 다른 줄도 어긋남
            rules, extra = None, None
        elif self.rules == "layered":
            r = prof.restricted
            rules = render_rules(
                "layered",
                **flags,
                death_policy=(r.policy if r is not None and prof.deceased else "deny"),
                vars={
                    "death_answer": (r.brief_answer if r is not None else None) or "그렇게 됐지",
                    "family_list": "아래 가족 표",
                },
            )
            extra = None  # 사인 정책은 L1 사망 모듈 변형 담당
        else:
            rules = render_common_rules(**flags)
            extra = brief_rule(prof.restricted)
        if rules is not None and prof.restricted is None:
            # 등록부 사망 정책 없으면 지시문이 고른 사망 규칙 사용 (위 deny 는 기본값)
            rules = adopt_death_rule(rules, instructions)
        rule_lines = {
            ln.strip() for ln in (rules or "").splitlines() if ln.strip().startswith("- ")
        }
        persona = dedupe_rules(instructions, rule_lines)
        # legacy 머리말은 측정한 프롬프트와 글자까지 동일하게
        label = "prompts/rules" if self.rules == "layered" else "shared_rules.md"
        head = (
            persona
            if rules is None
            else f"# 공통 규칙 (모든 페르소나 공통, {label})\n"
            + rules
            + "\n\n# 페르소나\n"
            + persona
        )
        if extra:
            head += "\n\n" + extra
        # 지시문(4층 생성기 결과 등)에 이미 있으면 생략
        if prof.style_card and prof.style_card.splitlines()[0] not in persona:
            head += "\n\n" + prof.style_card
        shots_in_text = bool(prof.shots) and f"가족: {prof.shots[0].user}\n" in persona
        if prof.shots and self.shots_mode == "system" and not shots_in_text:
            head += "\n\n" + shots_text(prof.shots, prof.self_label)
        sections = [PromptSection(name="rules+persona", role="system", content=head)]

        if self.header:
            head += "\n\n" + HEADER_RULE
            sections = [PromptSection(name="rules+persona", role="system", content=head)]
            block = header_block(selection)  # 델타도 같은 번호 체계
            if block:
                sections.append(PromptSection(name="memories", role="system", content=block))
        else:
            block = legacy_memory_block(selection.memories)
            if block:
                sections.append(PromptSection(name="memories", role="system", content=block))
            dblock = delta_block(selection.delta)
            if dblock:
                sections.append(PromptSection(name="delta", role="system", content=dblock))
        # 음성 지침, 빈틈 표시, 고친 말투는 첫 system 과 분리 (지시문 불변 -> 말투 덧붙임, 존댓말 판정 유지)
        sections[1:1] = voice_section(self.voice, turn) + extra_sections(turn)
        if prof.shots and self.shots_mode == "messages" and not shots_in_text:
            for i, sh in enumerate(prof.shots):
                sections += [
                    PromptSection(name=f"shot{i}u", role="user", content=sh.user),
                    PromptSection(name=f"shot{i}a", role="assistant", content=sh.persona),
                ]
        if precheck.directive:
            sections.append(
                PromptSection(name="directive", role="system", content=precheck.directive)
            )
        history = turn.history or state.recent_turns[-self.max_recent_turns :]
        session = self.session_text(prof, state, history, turn=turn, selection=selection)
        if session:
            sections.append(PromptSection(name="session", role="system", content=session))
        for i, t in enumerate(history):
            sections.append(PromptSection(name=f"history{i}", role=t.role, content=t.content))
        note = late_style_note(head) if self.late_note else ""
        if note:
            sections.append(PromptSection(name="late_style", role="system", content=note))
        sections.append(PromptSection(name="message", role="user", content=turn.message))
        return PromptBundle(sections=sections)

    def session_text(
        self,
        prof,
        state: SessionState,
        history,
        *,
        turn: TurnInput | None = None,
        selection: MemorySelection | None = None,
    ) -> str:
        """턴마다 바뀌는 절 (layered + 명세 있으면 런타임 모듈, 없으면 화자 이름과 기억 적을 때 절만)"""
        n = None
        if turn is not None and selection is not None:
            n = turn_memory_count(turn, selection, self.low_memory_max)
        if self.rules == "layered" and prof.persona_spec is not None:
            ctx = RuntimeContext(
                speaker=state.speaker,
                history=[(t.role, t.content) for t in history],
                memory_count=n,
                low_memory_max=self.low_memory_max or 0,
            )
            # 사망, 말 높이 판정은 가드와 동일 (BE persona.deceased 우선, 듣는 사람 줄)
            if turn is not None:
                ctx.deceased = _deceased(turn, prof)
                ctx.polite = listener_speech_level(turn.persona_instructions) == "존댓말"
            return runtime_section(prof.persona_spec, ctx)
        parts = [f"# 지금 상황\n- 지금 말하는 사람: {state.speaker}"] if state.speaker else []
        lm = low_memory_text(turn, prof, n, self.low_memory_max) if turn is not None else ""
        if lm:
            parts.append(lm)
        return "\n\n".join(parts)
