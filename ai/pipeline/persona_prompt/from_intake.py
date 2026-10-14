"""설문(IntakeContext) → 페르소나 명세. 규칙만 쓰고 LLM 은 부르지 않는다.

설문 칸을 프롬프트로 옮길 때 지키는 것
  - 호칭: subject.addressTerm 이 가족 표의 '그분이 이 사람을 부르는 말'과 같으면(설문 P1-07A, 페르소나가 작성자를
    부르는 말) 그분을 부르는 말로 쓰지 않는다.
  - 사망·임종 정책은 한 곳에서 정한다: 설문 M9-01 등급·T1-02(orchestrator.intake_policy.death_policy) → spec.death_policy.
    L1 의 사망 모듈 변형 하나로만 나가고, 주제 등급 목록에서는 사망·임종·장례 주제를 뺀다.
  - C-10 의 '먼저 꺼내기' 항목은 금지가 아니라 '물으면만'으로, '~말투' 항목은 피할 말투로 옮긴다.
  - 기억에서 뽑은 통찰(persona_insights)은 성격이 아니라 사실이라 명세에 넣지 않는다.
  - '사랑'이 든 말끝은 말끝 칸에서 빼고, 끝인사는 '통화를 끝낼 때만'으로 둔다.
  - 말투 예시는 설문에 적힌 실제 표현(추정 제외)과 통화 주고받기만 쓴다. 길이로 고른 전사 발화는 쓰지 않는다.
  - 이름이 같은 주제가 여러 문항에 있으면 가장 엄한 등급 하나로 합친다.
"""
from __future__ import annotations

import re
from collections import Counter

from infra.schema.subject import IntakeContext, SubjectContext
from orchestrator.intake_policy import death_policy
from pipeline.persona_prompt.spec import (
    Character, PersonaSpec, Quote, Relation, Situation, Style, Topic, is_death_topic,
)
from pipeline.persona_prompt.textnorm import is_address_term, same_topic

GRADES = ("금지", "짧게 넘김", "물으면만", "자유")
FIRST_MENTION = re.compile(r"\s*(을|를)?\s*먼저\s*(길게\s*)?꺼내기\s*$")


def _period(text: str | None) -> str | None:
    """'특정 시기 → 시기: 돌아가시기 전 …' 같은 선지·값 붙은 원문을 값만 남긴다."""
    if not text:
        return None
    if "시기:" in text:
        text = text.rsplit("시기:", 1)[1]
    return text.strip(" →") or None


QUOTE_MAX = 40


def _clean_quote(text: str) -> str:
    return text.strip().strip('"“”').strip()


def _cell(v: str | None) -> str | None:
    """표 칸에 남은 체크 상자 기호(□ ■)는 값이 아니다."""
    v = (v or "").replace("□", "").replace("■", "").strip()
    return v or None


def split_c10(topic: str) -> tuple[str, str]:
    """C-10 '피해야 할 것' 한 줄 → (종류, 값). 종류: style(피할 말투) / ask_only(먼저 꺼내지 않기) / taboo(금지)."""
    t = topic.strip()
    if t.endswith("말투") or "말투" in t[-6:]:
        return "style", t
    if FIRST_MENTION.search(t) or "먼저" in t:
        bare = FIRST_MENTION.sub("", t).strip()
        long_ = "길게" in t
        return "ask_only", bare + (" (먼저 길게 늘어놓지 않는다)" if long_ else "")
    return "taboo", t


def _relations(ic: IntakeContext) -> list[Relation]:
    out = []
    for f in ic.family_map:
        out.append(Relation(
            name=f.name, relation=f.relation, calls_them=f.address_term_to, self_ref=f.self_reference,
            they_call=f.address_term_from, speech_level=f.speech_level, tone=f.tone,
            worries=list(f.worries), talks_with_persona=f.talks_with_persona, source="intake:M2-01"))
    return out


def _family_calls_subject(sc: SubjectContext, rels: list[Relation], public: bool) -> tuple[str | None, list[str]]:
    notes = []
    term = sc.subject.address_term if sc.subject else None
    if public or not term:
        return None, notes
    if any(term == r.calls_them for r in rels):
        notes.append(f"호칭: subject.addressTerm '{term}' 은 가족 표에서 그분이 부르는 말과 같아(설문 P1-07A) 그분을 부르는 말로 쓰지 않음")
        return None, notes
    called = Counter(r.they_call for r in rels if r.they_call)
    if len(called) > 1:  # 사람마다 다르게 부르면(할머니 / 엄마) 가족 표에 맡긴다
        notes.append(f"가족이 그분을 부르는 말이 여럿({', '.join(called)}) — 가족 표에 사람마다 둠")
        return None, notes
    return term, notes


def spec_from_intake(subject_context: SubjectContext, intake: IntakeContext | None) -> PersonaSpec:
    ic = intake or IntakeContext()
    sc = subject_context
    name = (sc.subject.name if sc.subject and sc.subject.name else None) or "대상자"
    bp = ic.basic_profile or {}
    public = "공개" in (ic.audience or "")
    deceased = bp.get("status") == "사망"
    rels = _relations(ic)
    calls, notes = _family_calls_subject(sc, rels, public)
    spec = PersonaSpec(
        name=name, public=public, deceased=deceased, period=_period(bp.get("period")),
        family_calls_subject=calls,
        visitor_address=(sc.subject.address_term if public and sc.subject else None),
        visitor_tone=ic.visitor_tone, visitor_context=ic.visitor_context, relations=rels, notes=notes)
    if intake is not None:
        spec.add_material("intake")

    # 사망 정책
    if deceased:
        dp = death_policy(ic.model_dump(by_alias=True))
        if dp is not None:
            spec.death_policy, spec.death_policy_source = dp.policy, dp.source
            spec.death_answer = dp.default_answer
        else:
            spec.death_policy_source = "default"
        if spec.death_policy in ("brief", "ask_only") and not spec.death_answer:
            spec.death_answer = "그렇게 됐지"
            spec.notes.append("사망 정책 답 문장이 없어 기본 '그렇게 됐지'")

    # 성격(문자열로 온 예전 계약도 받는다)
    p = ic.personality
    if isinstance(p, str):
        spec.character = Character(introduction=p)
    elif p is not None:
        spec.character = Character(
            one_line=p.one_line or bp.get("oneLine"), introduction=p.introduction or bp.get("introduction"),
            traits=list(p.traits), values=list(p.values), affection_style=list(p.affection_style),
            comfort_style=p.comfort_style, advice_style=list(p.advice_style), conflict_style=p.conflict_style,
            remembered_as=p.remembered_as, wish_listener_feels=p.wish_listener_feels)
    elif bp.get("oneLine") or bp.get("introduction"):
        spec.character = Character(one_line=bp.get("oneLine"), introduction=bp.get("introduction"))

    spec.situations = [Situation(situation=r.situation.strip(), response=_clean_quote(r.response),
                                 avoid=r.avoid, source="intake:M8") for r in ic.situational_reactions]

    # 말투
    s = ic.speech_style
    style = Style()
    if isinstance(s, str):
        style.summary = s
    elif s is not None:
        endings = [_clean_quote(e) for e in s.sentence_endings]
        dropped = [e for e in endings if "사랑" in e]
        if dropped:
            spec.notes.append(f"말끝: '사랑' 이 든 말끝 {dropped} 은 말끝 칸에서 뺌(끝인사로만)")
        style = Style(
            length=s.length, manner=s.manner, when_angry=s.when_angry, interjections=list(s.interjections),
            dialect=s.dialect, dialect_example=s.dialect_example, avoid_styles=list(s.avoid_styles),
            sentence_endings=[e for e in endings if "사랑" not in e], call_open=_clean_quote(s.call_open or "") or None,
            call_close=_clean_quote(s.call_close or "") or None, summary=s.summary)
    for e in ic.speech_examples:
        text = _clean_quote(e.text)
        if not text or e.inferred:
            continue
        if len(text) > QUOTE_MAX:  # 실제 표현이 아니라 서술(M4-06 등)이면 말투 예시로 쓰지 않는다
            spec.notes.append(f"말투 예시: 설문 표현 '{text[:20]}…' 은 {len(text)}자 서술이라 말투 예시에서 뺌")
            continue
        style.quotes.append(Quote(text=text, situation=_cell(e.situation), listener=_cell(e.listener),
                                  source=f"intake:{e.source_question or '?'}"))
    spec.style = style

    # 주제 등급
    topics: list[Topic] = []
    for t in ic.taboo_topics:
        if isinstance(t, str):
            topic, grade, note, src = t, "금지", None, None
        else:
            topic, grade, note, src = t.topic, (t.grade or "금지").strip(), t.note, t.source_question
        if src == "C-10":
            kind, value = split_c10(topic)
            if kind == "style":
                spec.style.avoid_styles.append(value)
                spec.notes.append(f"C-10: '{topic}' → 피할 말투")
                continue
            if kind == "ask_only":
                spec.notes.append(f"C-10: '{topic}' → 물으면만")
                topic, grade = value, "물으면만"
        if is_death_topic(topic):
            spec.notes.append(f"사망 정책: 주제 '{topic}'({grade}) 는 사망 정책({spec.death_policy}) 모듈이 맡아 목록에서 뺌")
            continue
        dup = next((t for t in topics if t.topic == topic), None)
        if dup is not None:
            stricter = grade in GRADES and dup.grade in GRADES and GRADES.index(grade) < GRADES.index(dup.grade)
            if stricter:
                dup.grade = grade
            dup.note = dup.note or note
            dup.source = f"{dup.source},{src}" if src else dup.source
            spec.notes.append(f"주제 '{topic}' 이 여러 문항에 있어 하나로 합침(등급 {dup.grade})")
            continue
        topics.append(Topic(topic=topic, grade=grade, note=note, source=f"intake:{src}" if src else "intake"))
    spec.topics = topics
    return spec


def merge_recording(spec: PersonaSpec, recording: dict | None) -> PersonaSpec:
    """전사 재료(d_recording_prompt.json)로 설문이 비운 칸만 채운다. 발화 예시(speechExamples)는 쓰지 않는다."""
    if not recording:
        return spec
    for row in recording.get("familyMap") or []:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        term = row.get("addressTermTo")
        term = term if term and is_address_term(term) else None
        r = spec.relation_for(name)
        if r is None:
            spec.relations.append(Relation(name=name, relation=row.get("relation"), calls_them=term, source="transcript"))
        elif not r.calls_them and term:
            r.calls_them = term
    st = recording.get("speechStyle") or {}
    spec.style.interjections += [w for w in st.get("interjections") or [] if w not in spec.style.interjections]
    spec.style.summary = spec.style.summary or st.get("summary")
    for row in recording.get("tabooTopics") or []:
        topic = (row.get("topic") or "").strip()
        if not topic or is_death_topic(topic):
            continue
        if any(same_topic(topic, t.topic, {spec.name}) for t in spec.topics):
            continue  # 설문에 같은 주제가 있으면 설문 등급을 따른다
        spec.topics.append(Topic(topic=topic, grade=row.get("grade") or "물으면만", note=None, source="transcript"))
    spec.add_material("transcript")
    return spec


def apply_death_toggle(spec: PersonaSpec, death_talk: bool | None, source: str = "setting") -> PersonaSpec:
    """사인·임종 질문 켜기/끄기(설정). None 이면 설문(M9-01·T1-02) 그대로.
    False(끔) → deny: 물어도 말하지 않는다. True(켬) → 설문 등급이 brief·ask_only·open 이면 그대로, 아니면 ask_only."""
    if not spec.deceased or death_talk is None:
        return spec
    before = spec.death_policy
    if death_talk is False:
        spec.death_policy = "deny"
    elif spec.death_policy == "deny":
        spec.death_policy = "ask_only"
    spec.death_answer = spec.death_answer or "그렇게 됐지"
    spec.death_policy_source = f"{source}:{'on' if death_talk else 'off'}"
    spec.notes.append(f"사망 정책 {before} → {spec.death_policy} ({spec.death_policy_source})")
    return spec
