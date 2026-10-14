"""공통 규칙(4층 프롬프트의 L1) — 원본은 `prompts/rules/` 모듈 파일들, 순서는 `prompts/rules/manifest.json`.

모든 페르소나가 같은 파일에서 규칙을 받는다(손으로 쓴 프롬프트마다 안전 규칙이 달라지는 문제 방지).
세트 두 개:
    legacy   모듈로 나누기 전 공통 규칙과 같은 출력(골든 8개). 지금 조립기(assemble_persona_prompt)·중간계층이 쓴다.
    layered  4층 프롬프트의 L1. legacy + 사용자 식별·나이 맞춤·전해 들은 소식·사후 응답·기억 활용·말하는 형식.
             페르소나 이름이 없어 모든 페르소나가 같은 앞머리가 된다(프롬프트 캐시 공유).
모듈 안의 변형 블록 문법:
    ::: private family        ← 공백으로 나눈 조건이 전부 맞을 때만 안의 줄을 넣는다
    - 규칙 줄
    :::
조건: public / private(=not public), deceased / alive, family / nofamily,
      death_deny / death_brief / death_ask_only / death_open(사망·임종 정책, 기본 deny — 설문 M9-01 에서 고름).
`<!-- -->` 로 시작하는 줄은 주석(출력 안 함). 블록 밖 줄은 항상 넣는다. `{{이름}}` 은 vars 로 채운다.
섹션 안 모듈이 전부 비면 그 섹션 제목도 뺀다.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parents[2] / "prompts" / "rules"
DEATH_POLICIES = ("deny", "brief", "ask_only", "open")
TOKENS = {"public", "private", "deceased", "alive", "family", "nofamily"} | {f"death_{p}" for p in DEATH_POLICIES}
VAR = re.compile(r"\{\{(\w+)\}\}")
# 모든 세트에 들어가는 기본값. legacy 는 가족 목록이 규칙보다 위에 있다(예전 템플릿 순서).
DEFAULT_VARS = {"family_list": "위 가족 목록"}


def _flags(*, public: bool, deceased: bool, has_family: bool, death_policy: str = "deny") -> set[str]:
    if death_policy not in DEATH_POLICIES:
        raise ValueError(f"unknown death policy {death_policy!r}")
    return {
        "public" if public else "private",
        "deceased" if deceased else "alive",
        "family" if has_family else "nofamily",
        f"death_{death_policy}",
    }


@lru_cache(maxsize=64)
def _load(path: Path) -> tuple[str, ...]:
    return tuple(path.read_text(encoding="utf-8").splitlines())


@lru_cache(maxsize=4)
def _manifest(rules_dir: Path) -> dict:
    return json.loads((rules_dir / "manifest.json").read_text(encoding="utf-8"))


def render_block_file(path: Path, flags: set[str], vars: dict[str, str] | None = None) -> list[str]:
    """모듈 파일 하나를 조건에 맞게 줄 목록으로."""
    out: list[str] = []
    cond: set[str] | None = None
    for no, line in enumerate(_load(path), 1):
        if line.startswith("<!--"):
            continue
        if line.startswith(":::"):
            tokens = set(line[3:].split())
            if cond is None:
                if not tokens or not tokens <= TOKENS:
                    raise ValueError(f"{path.name}:{no} bad block condition {tokens}")
                cond = tokens
            else:
                if tokens:
                    raise ValueError(f"{path.name}:{no} nested block")
                cond = None
            continue
        if cond is None or cond <= flags:
            out.append(line)
    if cond is not None:
        raise ValueError(f"{path.name}: unclosed block")
    text = "\n".join(out)
    missing = [m for m in VAR.findall(text) if not vars or m not in vars]
    if missing:
        raise ValueError(f"{path.name}: missing vars {missing}")
    if vars:
        text = VAR.sub(lambda m: vars[m.group(1)], text)
    return [ln for ln in text.split("\n") if ln.strip()] if text else []


def render_rules(set_name: str, *, public: bool, deceased: bool, has_family: bool,
                 death_policy: str = "deny", vars: dict[str, str] | None = None,
                 rules_dir: Path = RULES_DIR) -> str:
    flags = _flags(public=public, deceased=deceased, has_family=has_family, death_policy=death_policy)
    vars = {**DEFAULT_VARS, **(vars or {})}
    sections = []
    for title, modules in _manifest(rules_dir)["sets"][set_name]:
        lines: list[str] = []
        for name in modules:
            lines += render_block_file(rules_dir / f"{name}.md", flags, vars)
        if lines:
            sections.append("\n".join([title, *lines]))
    return "\n\n".join(sections)


def render_common_rules(*, public: bool, deceased: bool, has_family: bool) -> str:
    """예전 출력(legacy 세트) — 조립기·중간계층이 쓰는 공통 규칙. 골든 8개로 고정."""
    return render_rules("legacy", public=public, deceased=deceased, has_family=has_family)


def common_rule_lines(*, public: bool, deceased: bool, has_family: bool) -> set[str]:
    """중복 제거용: 이 변형에서 나오는 규칙 줄(제목·빈 줄 제외)."""
    text = render_common_rules(public=public, deceased=deceased, has_family=has_family)
    return {ln.strip() for ln in text.splitlines() if ln.strip().startswith("- ")}


def family_guide(rows: Iterable) -> str:
    """가족 표 제목 바로 아래 안내 한 줄(조립기·4층 공통). 행은 FamilyNote 나 Relation.

    웹앱이 더 보내는 칸(isListener·listenerCalls·deceased)에 맞는 문장만 붙인다. 하나도 없으면
    빈 문자열이라 예전 출력이 그대로다. 돌아가신 분은 자해 규칙에서도 연락할 사람으로 짚지 않게 한다.
    """
    rows = list(rows)
    others = [r for r in rows if not getattr(r, "is_listener", None)]
    parts = []
    if len(others) < len(rows):
        parts.append("아래 가족 중 '지금 대화하는 사람'으로 적힌 사람이 지금 대화하는 상대다.")
    if any(getattr(r, "listener_calls", None) for r in others):
        parts.append("사용자가 '아빠'·'엄마'처럼 가족을 부르면 '사용자는 …라고 부름'을 보고 "
                     "누구를 말하는지 찾는다.")
    if any(getattr(r, "deceased", None) for r in others):
        parts.append("'돌아가심'으로 적힌 분은 이미 세상을 떠났으니 연락해 보라고 하거나 "
                     "연락할 사람으로 짚지 않는다.")
    return " ".join(parts)
