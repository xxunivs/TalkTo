"""층 구성(`prompts/layers.json`)을 읽고, 지금 상황에 켜질 모듈을 고른다.

정적 모듈(static)은 페르소나마다 정해지고 프롬프트 앞쪽에 놓여 캐시된다.
런타임 모듈(runtime)은 턴마다 바뀌므로 기억 블록 뒤에 별도 system 절로 붙는다(runtime_section).

조건 이름
    페르소나 종류  public / private, deceased / alive, family / nofamily, minor(만 19세 미만 살아 계신 분만, 반대 조건 없음)
    재료           has_intake / has_transcript / has_interview (spec.materials)
    주고받기 위치  shots_in_system / shots_as_messages
    런타임         speaker_known / speaker_unknown / long_conversation / low_memory(기억 수 ≤ LOW_MEMORY_MAX)
when 의 조건이 전부 맞아야 모듈이 켜진다. 내용이 빈 모듈은 조건과 상관없이 빠진다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pipeline.persona_prompt.low_memory import LOW_MEMORY_MAX, low_memory_note
from pipeline.persona_prompt.spec import PersonaSpec

LAYERS_FILE = Path(__file__).resolve().parents[2] / "prompts" / "layers.json"
ShotsMode = Literal["system", "messages"]
SHOTS_MODES = ("system", "messages")

STATIC_CONDITIONS = {
    "public",
    "private",
    "deceased",
    "alive",
    "family",
    "nofamily",
    "minor",
    "has_intake",
    "has_transcript",
    "has_interview",
    "shots_in_system",
    "shots_as_messages",
}
RUNTIME_CONDITIONS = {"speaker_known", "speaker_unknown", "long_conversation", "low_memory"}

# 이미 한 말을 알아보는 패턴(말투 카드와 같은 기준)
CARE = re.compile(
    r"밥\s*(은|잘|챙|먹|꼭|거르)|챙겨\s*먹|끼니|건강\s*(해|하|챙|조심)|몸\s*(조심|챙|상하|잘)|조심\s*해|감기\s*조심"
)
LOVE = re.compile(r"사랑")
LONG_CONVERSATION_TURNS = 6


@lru_cache(maxsize=4)
def load_layers(path: Path = LAYERS_FILE) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    for kind, allowed in (
        ("static", STATIC_CONDITIONS),
        ("runtime", STATIC_CONDITIONS | RUNTIME_CONDITIONS),
    ):
        for row in data[kind]:
            bad = set(row.get("when", [])) - allowed
            if bad:
                raise ValueError(
                    f"layers.json {kind}/{row['module']}: unknown condition {sorted(bad)}"
                )
    return data


def static_flags(spec: PersonaSpec, *, shots_mode: ShotsMode = "system") -> set[str]:
    if shots_mode not in SHOTS_MODES:
        raise ValueError(f"shots_mode must be one of {SHOTS_MODES}")
    flags = {
        "public" if spec.public else "private",
        "deceased" if spec.deceased else "alive",
        "family" if spec.relations else "nofamily",
        "shots_in_system" if shots_mode == "system" else "shots_as_messages",
    }
    if spec.minor and not spec.deceased:
        flags.add("minor")  # 아니면 더하지 않는다(예전 구성 그대로)
    return flags | {f"has_{m}" for m in spec.materials}


def static_modules(
    spec: PersonaSpec, *, shots_mode: ShotsMode = "system", path: Path = LAYERS_FILE
) -> list[str]:
    flags = static_flags(spec, shots_mode=shots_mode)
    return [
        row["module"] for row in load_layers(path)["static"] if set(row.get("when", [])) <= flags
    ]


# ---------------------------------------------------------------- 런타임


@dataclass
class RuntimeContext:
    """한 턴의 상황. history 는 (role, content) 쌍, 페르소나 답은 role="assistant"."""

    speaker: str | None = None
    history: list[tuple[str, str]] = field(default_factory=list)
    long_after: int = LONG_CONVERSATION_TURNS
    # 이 페르소나의 기억 수. None 이면 모름(= 적다고 보지 않는다). pipeline/persona_prompt/low_memory.py
    memory_count: int | None = None
    low_memory_max: int = LOW_MEMORY_MAX
    # 고인 여부(None 이면 spec.deceased)·존댓말 상대 — '기억이 적을 때' 문구를 고른다(가드와 같은 판정을 조립기가 넣음)
    deceased: bool | None = None
    polite: bool = False


def runtime_flags(spec: PersonaSpec, ctx: RuntimeContext) -> set[str]:
    flags = static_flags(spec) - {"shots_in_system"}
    flags.add("speaker_known" if ctx.speaker else "speaker_unknown")
    if len(ctx.history) >= ctx.long_after:
        flags.add("long_conversation")
    if ctx.memory_count is not None and ctx.memory_count <= ctx.low_memory_max:
        flags.add("low_memory")
    return flags


def current_speaker(spec: PersonaSpec, ctx: RuntimeContext) -> str:
    from pipeline.persona_prompt.render import relation_bits, relation_head

    r = spec.relation_for(ctx.speaker or "")
    if r is None:  # 화자 힌트가 이름이 아니라 관계말("손녀")일 수 있다
        same = [x for x in spec.relations if x.relation == ctx.speaker]
        if len(same) == 1:
            r = same[0]
        elif same:
            names = ", ".join(x.name for x in same)
            return (
                f"# 지금 상대\n- 지금 말하는 사람: {ctx.speaker}. 가족 표에 {ctx.speaker}가 여럿({names})이니 "
                "호칭을 단정하지 말고 누구인지 자연스럽게 확인한다."
            )
    if r is None:
        return (
            f"# 지금 상대\n- 지금 말하는 사람: {ctx.speaker}. 가족 표에 없는 사람이니 아는 척하지 말고, "
            "어떤 사이인지 자연스럽게 확인한다."
        )
    bits = relation_bits(
        r, listener_view=False
    )  # 말하는 사람이 정해졌으니 대화하는 사람 기준 칸은 뺀다
    return (
        f"# 지금 상대\n- 지금 말하는 사람: {relation_head(r)}"
        + (" — " + " / ".join(bits) if bits else "")
        + "\n- 이 사람의 호칭·자기를 가리키는 말·말 높이를 끝까지 지킨다."
    )


def unknown_speaker(spec: PersonaSpec, ctx: RuntimeContext) -> str:
    return (
        "# 지금 상대\n- 지금 말하는 사람이 누구인지 아직 모른다. 호칭을 단정하지 말고, 물은 것에 먼저 답한 뒤 "
        "누구인지 짧게 확인한다."
    )


def already_said(spec: PersonaSpec, ctx: RuntimeContext) -> str:
    said = [c for role, c in ctx.history if role == "assistant"]
    lines = []
    if any(CARE.search(c) for c in said):
        lines.append("- 밥·건강은 이번 대화에서 이미 챙겼다. 다시 챙기지 않는다.")
    if any(LOVE.search(c) for c in said):
        lines.append(
            "- '사랑해'는 이번 대화에서 이미 했다. 통화를 끝낼 때가 아니면 다시 하지 않는다."
        )
    return "# 이번 대화에서 이미 한 말\n" + "\n".join(lines) if lines else ""


def low_memory(spec: PersonaSpec, ctx: RuntimeContext) -> str:
    """기억이 적을 때. 비공개 고인은 잊은 듯 말하지 않는 문구(잠금 2)."""
    deceased = spec.deceased if ctx.deceased is None else ctx.deceased
    return low_memory_note(
        ctx.memory_count or 0, public=bool(spec.public), deceased=bool(deceased), polite=ctx.polite
    )


RUNTIME_MODULES = {
    "current_speaker": current_speaker,
    "unknown_speaker": unknown_speaker,
    "already_said": already_said,
    "low_memory": low_memory,
}


def runtime_section(spec: PersonaSpec, ctx: RuntimeContext, *, path: Path = LAYERS_FILE) -> str:
    """이번 턴에 붙일 런타임 절. 켜진 모듈이 없거나 다 비면 빈 문자열."""
    flags = runtime_flags(spec, ctx)
    parts = [
        RUNTIME_MODULES[row["module"]](spec, ctx)
        for row in load_layers(path)["runtime"]
        if set(row.get("when", [])) <= flags
    ]
    return "\n\n".join(p for p in parts if p.strip())
