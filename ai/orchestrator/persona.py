"""페르소나 메타(PersonaProfile) 찾기.

1) 등록부: <persona_dir>/<이름>/profile.json (+ style_card.md, shots.jsonl, restricted.json, intake.json 선택)
   intake.json: pipeline.s0_intake 가 만든 b_intake_prompt.json. 있으면 사망·임종 정책을 여기서 정한다
   (M9-01 등급·T1-02 → restricted.policy, orchestrator/intake_policy.py).
   profile.json: {"subject_ids": [...], "match_name": "박영자", "public": false, "deceased": true,
                  "has_family": true, "self_label": "할머니"}
   subject_id 가 맞거나, 없으면 지시문 첫 문장의 이름이 match_name 과 같을 때 쓴다.
2) 추정: 지시문 문장으로 public·deceased·has_family 를 추정한다(등록이 없을 때만).
3) 기본: 비공개·생존·가족 있음.
등록부의 말투 카드·실제 발화는 개인 데이터일 수 있으니 git 제외 경로(ORCHESTRATOR_PERSONA_DIR)에 두는 것을 권장.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from orchestrator.contracts import PersonaProfile, RestrictedFacts, Shot, TurnInput
from orchestrator.intake_policy import death_policy
from orchestrator.split import lexicon_from_intake_json
from pipeline.persona_prompt.spec import PersonaSpec

log = logging.getLogger(__name__)

_NAME = re.compile(r"당신은\s*([^\s.,]+?)(?:\s*본인의|의\s*페르소나)")
_PUBLIC_MARKS = ("본인의 말투로 말하는 페르소나", "# 방문자")
_DECEASED_MARKS = ("사후 페르소나", "별세", "사망", "돌아가신", "사후 응답")
_FAMILY_HEAD = re.compile(r"^#+\s*(가족|.*가족\s*\d+명)", re.M)
# 4층 생성기 지시문의 표지. L1 의 사후 절은 고인일 때만 들어간다.
_LAYERED_MARKS = ("# 말하는 형식", "# 기억을 쓰는 법")
_LAYERED_AFTERLIFE = "# 세상을 떠난 뒤의 대화"


def subject_name_from(instructions: str) -> str | None:
    """정체 문장의 이름. 4층 지시문은 공통 규칙(L1)이 앞에 있어 정체 문장이 뒤에 온다."""
    m = _NAME.search(instructions[:300]) or _NAME.search(instructions)
    return m.group(1) if m else None


def has_layered_rules(instructions: str) -> bool:
    """4층 생성기가 L1(공통 규칙)까지 넣어 만든 지시문인가. 두 제목은 L1 에만 있다."""
    return any(k in instructions for k in _LAYERED_MARKS)


def infer_profile(instructions: str) -> PersonaProfile:
    public = any(k in instructions for k in _PUBLIC_MARKS)
    if has_layered_rules(instructions):  # 4층 지시문: 공통 규칙의 사망 문구로 오판하지 않게
        deceased = _LAYERED_AFTERLIFE in instructions
    elif public:
        deceased = "사후 페르소나" in instructions
    else:
        deceased = any(k in instructions for k in _DECEASED_MARKS)
    has_family = bool(_FAMILY_HEAD.search(instructions)) if public else True
    name = subject_name_from(instructions)
    return PersonaProfile(subject_name=name, public=public, deceased=deceased, has_family=has_family,
                          source="inferred", self_label=name)


def _load_package(folder: Path) -> tuple[dict, PersonaProfile]:
    meta = json.loads((folder / "profile.json").read_text(encoding="utf-8"))
    card = folder / "style_card.md"
    shots_file = folder / "shots.jsonl"
    shots = []
    if shots_file.exists():
        for line in shots_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                shots.append(Shot.model_validate(json.loads(line)))
    rfile = folder / "restricted.json"
    restricted = (RestrictedFacts.model_validate_json(rfile.read_text(encoding="utf-8"))
                  if rfile.exists() else None)
    ifile = folder / "intake.json"
    intake = json.loads(ifile.read_text(encoding="utf-8")) if ifile.exists() else None
    restricted = merge_death_policy(restricted, intake)
    # profile.json "death_talk": false/true — 사인·임종 질문 설정이 설문보다 우선(끔 = deny, 켬 = deny 면 ask_only)
    if meta.get("death_talk") is not None and meta.get("deceased"):
        base = restricted or RestrictedFacts()
        pol = "deny" if meta["death_talk"] is False else ("ask_only" if base.policy == "deny" else base.policy)
        restricted = base.model_copy(update={"policy": pol, "policy_source": f"setting:{'on' if meta['death_talk'] else 'off'}",
                                             "brief_answer": base.brief_answer or "그렇게 됐지"})
    lex = lexicon_from_intake_json(intake)
    sfile = folder / "spec.json"
    spec = PersonaSpec.model_validate_json(sfile.read_text(encoding="utf-8")) if sfile.exists() else None
    if spec is not None:  # 말투 카드·주고받기 파일이 따로 없으면 명세의 것을 쓴다
        shots = shots or [Shot(user=s.user, persona=s.persona) for s in spec.style.shots]
    style_card = card.read_text(encoding="utf-8").strip() if card.exists() else (spec.style.card if spec else None)
    if spec is not None and spec.deceased and intake is None and meta.get("death_talk") is None \
            and (restricted is None or restricted.policy_source == "restricted.json"):
        # 설문이 없으면 명세에 정해 둔 사망 정책을 쓴다(restricted.json 의 정책보다 명세가 최신)
        base = restricted or RestrictedFacts()
        restricted = base.model_copy(update={"policy": spec.death_policy, "policy_source": "spec.json",
                                             "brief_answer": base.brief_answer or spec.death_answer})
    profile = PersonaProfile(
        subject_name=meta.get("match_name"), public=bool(meta.get("public", False)),
        deceased=bool(meta.get("deceased", False)), has_family=bool(meta.get("has_family", True)),
        source="registry", self_label=meta.get("self_label") or meta.get("match_name"),
        style_card=style_card,
        shots=shots[: int(meta.get("max_shots", 30))],
        restricted=restricted,
        address_terms=sorted({a for m in ((intake or {}).get("subjectContext") or {}).get("familyMembers") or []
                              for a in (m.get("addressTerms") or []) if a}) if lex else [],
        family_names=[n for n in lex.names] if lex else [],
        persona_spec=spec,
    )
    return meta, profile


def merge_death_policy(restricted: RestrictedFacts | None, intake: dict | None) -> RestrictedFacts | None:
    """intake 의 사망·임종 정책을 restricted 에 얹는다. intake 가 우선, 없으면 restricted.json 값 그대로."""
    dp = death_policy(intake)
    if dp is None:
        if restricted is not None and restricted.policy_source is None:
            restricted = restricted.model_copy(update={"policy_source": "restricted.json"})
        return restricted
    base = restricted or RestrictedFacts()
    return base.model_copy(update={
        "policy": dp.policy, "policy_source": dp.source, "policy_note": dp.note,
        "brief_answer": base.brief_answer or dp.default_answer,
    })


class PersonaRegistry:
    impl = "registry_v1"

    def __init__(self, persona_dir: Path | None = None) -> None:
        self.by_id: dict[str, PersonaProfile] = {}
        self.by_name: dict[str, PersonaProfile] = {}
        if persona_dir and Path(persona_dir).is_dir():
            for folder in sorted(Path(persona_dir).iterdir()):
                if not (folder / "profile.json").exists():
                    continue
                try:
                    meta, profile = _load_package(folder)
                except (OSError, ValueError) as e:
                    log.warning("persona package %s skipped: %s", folder.name, e)
                    continue
                for sid in meta.get("subject_ids", []):
                    self.by_id[str(sid)] = profile
                if meta.get("match_name"):
                    self.by_name[meta["match_name"]] = profile

    def profile_for(self, turn: TurnInput) -> PersonaProfile:
        if turn.subject_id in self.by_id:
            return self.by_id[turn.subject_id]
        name = subject_name_from(turn.persona_instructions)
        if name and name in self.by_name:
            return self.by_name[name]
        profile = infer_profile(turn.persona_instructions)
        if turn.deceased is not None and profile.deceased != turn.deceased:
            # BE 가 아는 고인 여부(persona.deceased)가 지시문 글자 추정보다 정확하다. 예전 지시문은 '돌아가신
            # 할아버지'처럼 다른 사람 이야기만 있어도 고인으로, 그런 말이 없는 고인은 생존으로 추정됐다.
            profile = profile.model_copy(update={"deceased": turn.deceased})
        return profile
