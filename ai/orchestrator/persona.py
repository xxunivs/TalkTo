"""페르소나 메타(PersonaProfile) 탐색 (등록부 -> 지시문 추정 -> 기본값)"""

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
# 4층 생성기 지시문 표지 (L1 사후 절은 사망 대상자일 때만)
_LAYERED_MARKS = ("# 말하는 형식", "# 기억을 쓰는 법")
_LAYERED_AFTERLIFE = "# 세상을 떠난 뒤의 대화"


def subject_name_from(instructions: str) -> str | None:
    """정체 문장의 이름 (4층 지시문은 L1 이 앞에 있어 정체 문장이 뒤쪽)"""
    m = _NAME.search(instructions[:300]) or _NAME.search(instructions)
    return m.group(1) if m else None


def has_layered_rules(instructions: str) -> bool:
    """4층 생성기가 L1(공통 규칙)까지 넣은 지시문 여부 (두 제목은 L1 전용)"""
    return any(k in instructions for k in _LAYERED_MARKS)


def infer_profile(instructions: str) -> PersonaProfile:
    public = any(k in instructions for k in _PUBLIC_MARKS)
    if has_layered_rules(instructions):  # 4층 지시문: 공통 규칙 사망 문구로 인한 오판 방지
        deceased = _LAYERED_AFTERLIFE in instructions
    elif public:
        deceased = "사후 페르소나" in instructions
    else:
        deceased = any(k in instructions for k in _DECEASED_MARKS)
    has_family = bool(_FAMILY_HEAD.search(instructions)) if public else True
    name = subject_name_from(instructions)
    return PersonaProfile(
        subject_name=name,
        public=public,
        deceased=deceased,
        has_family=has_family,
        source="inferred",
        self_label=name,
    )


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
    restricted = (
        RestrictedFacts.model_validate_json(rfile.read_text(encoding="utf-8"))
        if rfile.exists()
        else None
    )
    ifile = folder / "intake.json"
    intake = json.loads(ifile.read_text(encoding="utf-8")) if ifile.exists() else None
    restricted = merge_death_policy(restricted, intake)
    # profile.json death_talk 설정이 설문보다 우선 (false = deny, true = deny 면 ask_only)
    if meta.get("death_talk") is not None and meta.get("deceased"):
        base = restricted or RestrictedFacts()
        pol = (
            "deny"
            if meta["death_talk"] is False
            else ("ask_only" if base.policy == "deny" else base.policy)
        )
        restricted = base.model_copy(
            update={
                "policy": pol,
                "policy_source": f"setting:{'on' if meta['death_talk'] else 'off'}",
                "brief_answer": base.brief_answer or "그렇게 됐지",
            }
        )
    lex = lexicon_from_intake_json(intake)
    sfile = folder / "spec.json"
    spec = (
        PersonaSpec.model_validate_json(sfile.read_text(encoding="utf-8"))
        if sfile.exists()
        else None
    )
    if spec is not None:  # 말투 카드, 주고받기 파일 없으면 명세 값 사용
        shots = shots or [Shot(user=s.user, persona=s.persona) for s in spec.style.shots]
    style_card = (
        card.read_text(encoding="utf-8").strip()
        if card.exists()
        else (spec.style.card if spec else None)
    )
    if (
        spec is not None
        and spec.deceased
        and intake is None
        and meta.get("death_talk") is None
        and (restricted is None or restricted.policy_source == "restricted.json")
    ):
        # 설문 없으면 명세의 사망 정책 사용 (restricted.json 보다 명세가 최신)
        base = restricted or RestrictedFacts()
        restricted = base.model_copy(
            update={
                "policy": spec.death_policy,
                "policy_source": "spec.json",
                "brief_answer": base.brief_answer or spec.death_answer,
            }
        )
    profile = PersonaProfile(
        subject_name=meta.get("match_name"),
        public=bool(meta.get("public", False)),
        deceased=bool(meta.get("deceased", False)),
        has_family=bool(meta.get("has_family", True)),
        source="registry",
        self_label=meta.get("self_label") or meta.get("match_name"),
        style_card=style_card,
        shots=shots[: int(meta.get("max_shots", 30))],
        restricted=restricted,
        address_terms=sorted(
            {
                a
                for m in ((intake or {}).get("subjectContext") or {}).get("familyMembers") or []
                for a in (m.get("addressTerms") or [])
                if a
            }
        )
        if lex
        else [],
        family_names=[n for n in lex.names] if lex else [],
        persona_spec=spec,
    )
    return meta, profile


def merge_death_policy(
    restricted: RestrictedFacts | None, intake: dict | None
) -> RestrictedFacts | None:
    """intake 사망, 임종 정책을 restricted 에 병합 (intake 우선, 없으면 restricted.json 값)"""
    dp = death_policy(intake)
    if dp is None:
        if restricted is not None and restricted.policy_source is None:
            restricted = restricted.model_copy(update={"policy_source": "restricted.json"})
        return restricted
    base = restricted or RestrictedFacts()
    return base.model_copy(
        update={
            "policy": dp.policy,
            "policy_source": dp.source,
            "policy_note": dp.note,
            "brief_answer": base.brief_answer or dp.default_answer,
        }
    )


class PersonaRegistry:
    """<persona_dir>/<이름>/profile.json 등록부 (subject_id 또는 지시문 이름 match_name 일치, 개인 데이터라 git 제외 경로)"""

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
            # BE persona.deceased 가 지시문 추정보다 정확 (다른 사람의 사망 언급만으로 오판하던 문제)
            profile = profile.model_copy(update={"deceased": turn.deceased})
        return profile
