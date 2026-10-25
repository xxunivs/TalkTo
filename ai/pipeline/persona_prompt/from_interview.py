"""G2 가족 인터뷰 txt → 페르소나 명세 후보. LLM 이 뽑고 코드가 근거를 검증한다. 적용은 사람이 후보 파일을 확인한 뒤.

- 입력은 줄 번호를 붙여 넘기고(L1, L2 …), 모든 후보에 근거 줄 번호(evidence)와 그 줄에 글자 그대로 있는
  짧은 구절(quote)을 요구한다. 코드가 줄 번호가 있는지, quote 가 그 줄들에 정말 있는지 확인해 아니면 버린다.
- 사실(생몰·직장·주소·병력 같은 기억)은 뽑지 않는다 — 그건 기억 DB 몫이고 프롬프트에 넣지 않는다.
- 적용(apply_candidates)은 설문이 비운 칸만 채운다(사실·정책은 설문 > 인터뷰 > 전사). 목록 칸은 없는 값만 덧붙인다.
"""
from __future__ import annotations

import json
import re

from pipeline.persona_prompt.spec import PersonaSpec, Relation, Situation, Topic, is_death_topic
from pipeline.persona_prompt.textnorm import is_address_term, mostly_covered, same_topic, split_quoted, unquote

CHAR_FIELDS = ("traits", "values", "affection_style", "advice_style")
CHAR_SCALARS = ("comfort_style", "conflict_style")
STYLE_LISTS = ("interjections", "sentence_endings", "words", "avoid_styles")
STYLE_SCALARS = ("manner", "dialect")
REL_FIELDS = ("relation", "calls_them", "self_ref", "they_call", "speech_level", "tone")
ADDRESS_FIELDS = ("calls_them", "self_ref", "they_call")

INTERVIEW_PROMPT = """너는 가족 인터뷰 정리본에서 페르소나(세상을 떠났거나 기록으로 남길 분)의 '말하는 방식과 관계'를 뽑는 분석기다.
줄마다 L번호가 붙어 있다. JSON 하나로만 답하라.

뽑을 것(근거가 인터뷰에 분명히 있는 것만, 없으면 빈 목록):
- relations: 가족 한 사람씩 {"name", "relation"(그분의 ○○), "calls_them"(그분이 이 사람을 부르던 말), "self_ref"(이 사람 앞에서 그분이 자기를 가리키던 말),
  "they_call"(이 사람이 그분을 부르던 말), "speech_level"(반말/존댓말), "tone"(대하던 태도 한 구절), "worries"(늘 걱정하던 것 목록)}
- character: {"field": traits|values|affection_style|advice_style|comfort_style|conflict_style, "value": 짧은 구절}
- style: {"field": interjections|sentence_endings|words|avoid_styles|manner|dialect, "value": 실제 말·어휘 또는 짧은 서술}
  (words = 실제로 쓰던 특이한 어휘·말버릇, avoid_styles = 그분이 쓰지 않던 말투)
- situations: {"situation": "가족이 ~하면", "response": "그분이 할 법한 짧은 말"}
- topics: {"topic": 말하기 조심할 주제, "grade": 금지|짧게 넘김|물으면만|자유}
모든 항목에 "evidence": [근거 줄 번호 정수], "quote": "근거 줄에 글자 그대로 있는 짧은 구절(30자 이내)" 를 붙인다.
뽑지 말 것: 생년·주소·직장·학교·병명·재산 같은 사실(기억 DB 몫), [추정]·[확인 필요] 줄만 근거인 것, 인터뷰어 말투.
형식: {"relations": [...], "character": [...], "style": [...], "situations": [...], "topics": [...]}"""


def numbered(text: str) -> tuple[str, list[str]]:
    lines = text.splitlines()
    return "\n".join(f"L{i}: {ln}" for i, ln in enumerate(lines, 1) if ln.strip()), lines


def _as_list(v) -> list:
    """LLM 이 목록 대신 값 하나를 주는 경우가 있다."""
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


def _scalar(v) -> str:
    """칸 하나에 목록이 오면 첫 값만 쓴다."""
    v = _as_list(v)
    return unquote(str(v[0])) if v else ""


def _norm(s: str) -> str:
    return re.sub(r"[\s\"'“”‘’]", "", s or "")


def verify(items: list[dict], lines: list[str]) -> tuple[list[dict], int]:
    kept, dropped = [], 0
    for it in items or []:
        if not isinstance(it, dict):
            dropped += 1
            continue
        ev = [e for e in _as_list(it.get("evidence")) if isinstance(e, int) and 1 <= e <= len(lines)]
        quote = str(it.get("quote") or "")
        body = _norm(" ".join(lines[e - 1] for e in ev))
        if not ev or not quote or _norm(quote) not in body:
            dropped += 1
            continue
        kept.append({**it, "evidence": ev})
    return kept, dropped


def extract_candidates(text: str, *, client, model: str) -> dict:
    """LLM 호출 한 번 → 검증된 후보. 반환: {"relations": [...], …, "dropped": n, "model": model}"""
    body, lines = numbered(text)
    r = client.chat.completions.create(
        model=model, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": INTERVIEW_PROMPT}, {"role": "user", "content": body}])
    payload = json.loads(r.choices[0].message.content or "{}")
    out: dict = {"model": model, "reviewed": False, "dropped": 0}
    for key in ("relations", "character", "style", "situations", "topics"):
        out[key], d = verify(payload.get(key) or [], lines)
        out["dropped"] += d
    return out


def _src(it: dict) -> str:
    return "interview:" + ",".join(f"L{e}" for e in it.get("evidence", []))


def _add_items(target: list[str], value: str, known: list[str]) -> list[str]:
    """목록 칸에 값 넣기. 따옴표 친 말 여러 개는 하나씩 나누고, 이미 있는 말(따옴표·띄어쓰기 무시)은 건너뛴다."""
    seen = {_norm(x) for x in known}
    added = []
    for item in split_quoted(value):
        if _norm(item) and _norm(item) not in seen:
            target.append(item)
            seen.add(_norm(item))
            added.append(item)
    return added


def apply_candidates(spec: PersonaSpec, cands: dict) -> list[str]:
    """설문이 비운 칸만 채운다. 바꾼 내용과 건너뛴 이유를 한 줄씩 돌려준다(사람 확인용)."""
    log: list[str] = []
    for it in cands.get("relations") or []:
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        r = spec.relation_for(name)
        if r is None:
            r = Relation(name=name, source=_src(it))
            spec.relations.append(r)
            log.append(f"가족 추가: {name} ({_src(it)})")
        for f in REL_FIELDS:
            v = _scalar(it.get(f))
            if not v or getattr(r, f):
                continue
            if f in ADDRESS_FIELDS and not is_address_term(v):
                log.append(f"건너뜀: {r.name}.{f} '{v}' 는 호칭이 아니라 서술 ({_src(it)})")
                continue
            setattr(r, f, v)
            log.append(f"{r.name}.{f} = {v} ({_src(it)})")
        parts = {_norm(p) for x in r.worries for p in re.split(r"[·,/()]", x) if _norm(p)}
        for w in _as_list(it.get("worries")):
            w = unquote(str(w))
            if w and _norm(w) not in parts and not mostly_covered(w, r.worries, ratio=1.0):
                r.worries.append(w)
                parts.add(_norm(w))
    for it in cands.get("character") or []:
        f, v = it.get("field"), unquote(str(it.get("value") or ""))
        if not v:
            continue
        if f in CHAR_FIELDS:
            have = getattr(spec.character, f)
            if mostly_covered(v, have):
                log.append(f"건너뜀: character.{f} '{v}' 는 이미 있는 값과 같은 내용 ({_src(it)})")
            elif v not in have:
                have.append(v)
                log.append(f"character.{f} += {v} ({_src(it)})")
        elif f in CHAR_SCALARS and not getattr(spec.character, f):
            setattr(spec.character, f, v)
            log.append(f"character.{f} = {v} ({_src(it)})")
    said = [q.text for q in spec.style.quotes]
    for it in cands.get("style") or []:
        f, v = it.get("field"), str(it.get("value") or "").strip()
        if not v or (f == "sentence_endings" and "사랑" in v):
            continue
        if f == "avoid_styles":  # "장황한 조언, 과한 사투리" 처럼 여러 개가 한 줄로 오면 나눠서 겹치는 것을 뺀다
            have = spec.style.avoid_styles
            for item in (unquote(x) for x in v.split(",")):
                if not item:
                    continue
                if mostly_covered(item, have):
                    log.append(f"건너뜀: style.avoid_styles '{item}' 는 이미 있는 값과 같은 내용 ({_src(it)})")
                    continue
                have.append(item)
                log.append(f"style.avoid_styles += {item} ({_src(it)})")
        elif f in STYLE_LISTS:
            have = getattr(spec.style, f)
            for item in _add_items(have, v, have + (said if f in ("words", "interjections") else [])):
                log.append(f"style.{f} += {item} ({_src(it)})")
        elif f in STYLE_SCALARS and not getattr(spec.style, f):
            setattr(spec.style, f, unquote(v))
            log.append(f"style.{f} = {unquote(v)} ({_src(it)})")
    known_sit = {s.situation for s in spec.situations}
    for it in cands.get("situations") or []:
        sit, resp = str(it.get("situation") or "").strip(), unquote(str(it.get("response") or ""))
        if sit and resp and sit not in known_sit:
            spec.situations.append(Situation(situation=sit, response=resp, source=_src(it)))
            log.append(f"상황 추가: {sit} ({_src(it)})")
    for it in cands.get("topics") or []:
        topic, grade = str(it.get("topic") or "").strip(), str(it.get("grade") or "").strip()
        if not topic or not grade or is_death_topic(topic):
            continue
        same = next((t for t in spec.topics if same_topic(topic, t.topic, {spec.name})), None)
        if same is not None:
            log.append(f"건너뜀: 주제 '{topic}'({grade}) 는 이미 있는 '{same.topic}'({same.grade}) 와 같은 주제 ({_src(it)})")
            continue
        spec.topics.append(Topic(topic=topic, grade=grade, source=_src(it)))
        log.append(f"주제 추가: {topic}={grade} ({_src(it)})")
    if any(not x.startswith("건너뜀") for x in log):
        spec.add_material("interview")
    return log
