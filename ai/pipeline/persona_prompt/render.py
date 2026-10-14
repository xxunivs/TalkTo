"""페르소나 명세 → 프롬프트. 템플릿만 쓰고 LLM 은 부르지 않는다.

층과 모듈 순서는 `prompts/layers.json` 이 정한다(layers.py). 이 파일은 모듈 하나하나의 글을 만든다.
    L1 공통 규칙       prompts/rules 의 layered 세트(사망 정책 변형 포함)
    L2 개인 정보·정책  정체, 기본 정보, 성격·가치관, 상황별 반응, 주제 등급
    L3 관계·호칭       비공개: 가족 표 / 공개: 방문자 안내
    L4 개인 말투       말투 서술, 말투 카드, 실제로 하던 말, 실제 주고받기
기억은 넣지 않는다. 기억 블록은 대화할 때 따로 붙는다.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from pipeline.persona_prompt.layers import ShotsMode, static_modules
from pipeline.persona_prompt.spec import PersonaSpec, Relation
from pipeline.s3_synthesis.common_rules import render_rules

# 주제 등급 → 지시. 금지 문구는 상대에 따라 다르다(가족 / 공개 방문자).
GRADE_TEXT = {
    "금지": ("**말하지 않는다.** 물어도 내용을 말하지 말고 \"그런 얘기는 하지 말자\" 하고 화제를 돌린다",
             "**말하지 않는다.** 물어도 내용을 말하지 말고 \"그 부분은 말씀드리기 어려워요\" 하고 화제를 돌린다"),
    "짧게 넘김": ("**한두 마디로 받고 넘어간다.** 물어도 길게 풀지 않는다",) * 2,
    "물으면만": ("**먼저 꺼내지 않는다.** 물으면 답한다",) * 2,
    "자유": ("**먼저 꺼내도 된다**",) * 2,
}
ORDER = ("금지", "짧게 넘김", "물으면만", "자유")


def particle(name: str, with_batchim: str, without: str) -> str:
    """이름 끝 글자의 받침에 맞는 조사(이/가 등)."""
    if not name or not ("가" <= name[-1] <= "힣"):
        return without
    return with_batchim if (ord(name[-1]) - 0xAC00) % 28 else without


def _join(v) -> str:
    return ", ".join(x for x in v if x) if isinstance(v, list) else (v or "")


def _line(label: str, value) -> str | None:
    text = _join(value)
    return f"- {label}: {text}" if text else None


def _block(*lines: str | None) -> str:
    return "\n".join(x for x in lines if x)


def relation_head(r: Relation) -> str:
    """가족 표 한 줄의 머리. 이름 칸에 관계말만 있으면(이름을 모르는 며느리 등) 관계말 하나만 쓴다."""
    if not r.relation or r.relation == r.name:
        return r.name
    return f"{r.name} ({r.relation})"


def relation_bits(r: Relation) -> list[str]:
    bits = []
    if r.calls_them:
        bits.append(f"부르는 말 \"{r.calls_them}\"")
    if r.self_ref:
        bits.append(f"이 사람 앞에서 나는 \"{r.self_ref}\"")
    if r.they_call:
        bits.append(f"이 사람은 나를 \"{r.they_call}\"라고 부름")
    if r.speech_level:
        bits.append(r.speech_level)
    if r.tone:
        bits.append(f"톤: {r.tone}")
    if r.worries:
        bits.append(f"늘 걱정하던 것: {'·'.join(r.worries)}")
    if r.talks_with_persona is False:
        bits.append("이 사람과는 대화하지 않음")
    return bits


# ---------------------------------------------------------------- L1

def common_rules(spec: PersonaSpec) -> str:
    return render_rules("layered", public=spec.public, deceased=spec.deceased, has_family=bool(spec.relations),
                        death_policy=spec.death_policy if spec.deceased else "deny",
                        vars={"death_answer": spec.death_answer or "그렇게 됐지", "family_list": "아래 가족 표"})


# ---------------------------------------------------------------- L2

def opening(spec: PersonaSpec) -> str:
    n = spec.name
    ga = particle(n, "이", "가")
    if spec.public and spec.deceased:
        return (f"당신은 {n}의 페르소나다. 이 서비스는 {n}{ga} 남긴 이야기를 {n}의 말투로 방문자에게 전하는 서비스다. "
                f"당신은 {n} 본인이 아니며, {n}처럼 자연스럽게 말하되 정체를 직접 물어도 본인이라고 우기지도, "
                f"AI라고 밝히지도 않고 자연스럽게 흘린다.")
    if not spec.public:
        return (f"당신은 {n}의 페르소나다. 이 서비스는 \"{n}{ga} 하실 만한 이야기를 가족에게 대신 전달하는\" 서비스다. "
                f"당신은 {n} 본인이 아니며, {n}{ga} 했을 법한 이야기를 {n}의 말투로 전달한다. 대화에서는 {n}처럼 "
                f"자연스럽게 말하고, 정체를 직접 물어도 본인이라고 우기지도, 본인이 아니라거나 AI라고 밝히지도 않고 자연스럽게 흘린다.")
    return (f"당신은 {n} 본인의 말투로 말하는 페르소나다. {n}{ga} 직접 답한 내용과 실제 녹음으로 만들었고, "
            f"그를 대신해 방문자와 이야기한다. 1인칭으로 말한다.")


def basic_info(spec: PersonaSpec) -> str:
    c = spec.character
    return _block(
        "# 기본 정보",
        f"- 이름: {spec.name}" + (f" — {c.one_line}" if c.one_line else ""),
        f"- 가족이 부르는 말: {spec.family_calls_subject}" if spec.family_calls_subject else None,
        f"- 방문자를 부르는 말: {spec.visitor_address}" if spec.public and spec.visitor_address else None,
        f"- 어느 때의 모습인가: {spec.period}" if spec.period else None,
        f"- 이 페르소나가 여기 있는 이유: {spec.visitor_context}" if spec.public and spec.visitor_context else None,
    )


def character(spec: PersonaSpec) -> str:
    c = spec.character
    rows = [_line("본인 소개", c.introduction), _line("성격", c.traits), _line("중요하게 여기는 것", c.values),
            _line("애정 표현", c.affection_style), _line("힘들어하는 사람을 만나면", c.comfort_style),
            _line("고민을 들으면", c.advice_style), _line("갈등이 생기면", c.conflict_style),
            _line("기억되고 싶은 모습", c.remembered_as), _line("듣는 사람이 느꼈으면 하는 것", c.wish_listener_feels)]
    rows = [r for r in rows if r]
    return _block("# 성격·가치관", *rows) if rows else ""


def situations(spec: PersonaSpec) -> str:
    if not spec.situations:
        return ""
    lines = ["# 상황별로 이렇게 (말은 그대로 읽지 말고 이 방향으로)"]
    for s in spec.situations:
        lines.append(f"- {s.situation} → \"{s.response}\"" + (f" (피할 것: {s.avoid})" if s.avoid else ""))
    return "\n".join(lines)


def topics(spec: PersonaSpec) -> str:
    if not spec.topics:
        return ""
    grouped: dict[str, list[str]] = {}
    for t in spec.topics:
        grouped.setdefault(t.grade, []).append(t.topic + (f" ({t.note})" if t.note else ""))
    idx = 1 if spec.public else 0
    lines = ["# 주제별로 어디까지 말할지"]
    for g in ORDER:
        if g in grouped:
            lines.append(f"- {GRADE_TEXT[g][idx]}: {', '.join(grouped.pop(g))}")
    for g, items in grouped.items():  # 알 수 없는 등급도 버리지 않고 그대로 보인다
        lines.append(f"- [{g}]: {', '.join(items)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- L3

FAMILY_HEAD = ("# 가족 (이 표에 있는 사람만 안다. 상대가 누군지 밝히면 그 사람 줄을 따른다)\n"
               "자기를 가리키는 말이 비어 있으면 \"내가\" 정도로 짧게 말한다. 반말로 적힌 사람에게는 예시 문장이 존대로 "
               "적혀 있어도 어미를 반말로 바꿔 말한다.")


def family_table(spec: PersonaSpec) -> str:
    if not spec.relations:
        return "# 가족\n- (아직 파악된 가족 정보 없음) 상대가 밝히는 관계에 맞춰 말하되, 모르는 사람을 아는 척하지 않는다."
    rows = []
    for r in spec.relations:
        bits = relation_bits(r)
        rows.append(f"- {relation_head(r)}" + (": " + " / ".join(bits) if bits else ""))
    return FAMILY_HEAD + "\n" + "\n".join(rows)


def visitors(spec: PersonaSpec) -> str:
    lines = ["# 방문자 (누가 올지 모른다)", "- 처음 보는 사람으로 대한다. 방문자가 누구인지 넘겨짚지 않는다.",
             "- 처음 보는 사람에게 하듯 존댓말로 말한다. '~요'로 끝나는 편안한 구어체가 기본이다."]
    if spec.visitor_tone:
        lines.append(f"- 방문자를 대하는 느낌: {spec.visitor_tone}")
    if spec.relations:
        lines += ["", "# 이야기에 나오는 가족"] + [f"- {relation_head(r)}" for r in spec.relations]
    return "\n".join(lines)


# ---------------------------------------------------------------- L4

def style(spec: PersonaSpec) -> str:
    s = spec.style
    rows = [_line("말의 길이", s.length), _line("이야기하는 방식", s.manner), _line("자주 쓰는 감탄사", s.interjections),
            _line("자주 쓰는 말끝", s.sentence_endings), _line("사투리·억양", s.dialect),
            _line("사투리 예시", s.dialect_example), _line("실제로 쓰던 어휘", s.words), _line("화가 나면", s.when_angry),
            _line("전화 첫마디", s.call_open),
            _line("통화를 끝낼 때만 (대화 중간에는 쓰지 않는다)", s.call_close),
            _line("쓰지 않는 말투", s.avoid_styles), _line("통화에서 보인 문장 특징", s.summary)]
    if s.length and "길게" in s.length:
        rule = "- 끊어서 짧게 답하지 않는다. 예를 들어가며 자세히 말하되 훈계하지 않고, 매 턴 되묻지 않는다."
    else:
        rule = ("- 기본은 1~2문장으로 짧게. 상담사처럼 풀어 설명하지 않고, 매 턴 되묻지 않는다. "
                "요리법·옛 추억만은 콤마로 이어지는 만연체로 길게 풀어도 자연스럽다.")
    return _block("# 말투", *rows, rule)


def style_card(spec: PersonaSpec) -> str:
    return spec.style.card or ""


def quotes(spec: PersonaSpec) -> str:
    if not spec.style.quotes:
        return ""
    lines = ["# 실제로 하던 말 (가족이 적어 준 것 — 어휘·어투 참고, 매번 그대로 쓰지 않는다)"]
    for q in spec.style.quotes:
        where = " / ".join(x for x in (q.situation, q.listener) if x)
        lines.append(f"- \"{q.text}\"" + (f" ({where})" if where else ""))
    return "\n".join(lines)


def shots(spec: PersonaSpec) -> str:
    if not spec.style.shots:
        return ""
    ga = particle(spec.name, "이", "가")
    lines = [f"# 실제 통화에서 {spec.name}{ga} 한 말 (말투만 참고, 내용은 따라 하지 말 것)"]
    lines += [f"가족: {sh.user}\n{spec.name}: {sh.persona}" for sh in spec.style.shots]
    return "\n".join(lines)


MODULES: dict[str, Callable[[PersonaSpec], str]] = {
    "common_rules": common_rules, "opening": opening, "basic_info": basic_info, "character": character,
    "situations": situations, "topics": topics, "family_table": family_table, "visitors": visitors,
    "style": style, "style_card": style_card, "quotes": quotes, "shots": shots,
}


def render_layered_prompt(spec: PersonaSpec, *, include_common: bool = True, shots_mode: ShotsMode = "system",
                          drop: Iterable[str] = ()) -> str:
    """정적 층(L1~L4)을 한 글로.

    include_common=False  L1 을 뺀다(중간계층이 공통 규칙을 따로 붙일 때).
    shots_mode="messages" 주고받기를 글에서 빼고, shot_messages() 로 대화 메시지가 되게 한다.
    drop                  뺄 모듈 이름(비교 실험용).
    """
    skip = set(drop) | (set() if include_common else {"common_rules"})
    parts = [MODULES[m](spec) for m in static_modules(spec, shots_mode=shots_mode) if m not in skip]
    return "\n\n".join(p for p in parts if p.strip())


def shot_messages(spec: PersonaSpec) -> list[dict[str, str]]:
    """실제 주고받기를 앞선 대화(user/assistant 메시지) 모양으로. shots_mode="messages" 와 같이 쓴다."""
    out: list[dict[str, str]] = []
    for sh in spec.style.shots:
        out += [{"role": "user", "content": sh.user}, {"role": "assistant", "content": sh.persona}]
    return out
