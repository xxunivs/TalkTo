"""공통 규칙 L1 조립 (원본 prompts/rules/ 모듈, 순서 manifest.json, 세트 legacy/layered)"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parents[2] / "prompts" / "rules"
DEATH_POLICIES = ("deny", "brief", "ask_only", "open")
TOKENS = {"public", "private", "deceased", "alive", "family", "nofamily", "minor"} | {
    f"death_{p}" for p in DEATH_POLICIES
}
VAR = re.compile(r"\{\{(\w+)\}\}")
# 전 세트 기본값 (legacy 는 예전 템플릿 순서대로 가족 목록이 규칙 위)
DEFAULT_VARS = {"family_list": "위 가족 목록"}


def _flags(
    *,
    public: bool,
    deceased: bool,
    has_family: bool,
    death_policy: str = "deny",
    minor: bool = False,
) -> set[str]:
    if death_policy not in DEATH_POLICIES:
        raise ValueError(f"unknown death policy {death_policy!r}")
    flags = {
        "public" if public else "private",
        "deceased" if deceased else "alive",
        "family" if has_family else "nofamily",
        f"death_{death_policy}",
    }
    # 미성년 규칙은 살아 계신 분만 (떠나신 자녀는 사망 규칙, 아니면 골든 그대로)
    if minor and not deceased:
        flags.add("minor")
    return flags


@lru_cache(maxsize=64)
def _load(path: Path) -> tuple[str, ...]:
    return tuple(path.read_text(encoding="utf-8").splitlines())


@lru_cache(maxsize=4)
def _manifest(rules_dir: Path) -> dict:
    return json.loads((rules_dir / "manifest.json").read_text(encoding="utf-8"))


def render_block_file(path: Path, flags: set[str], vars: dict[str, str] | None = None) -> list[str]:
    """모듈 파일 하나 -> 조건에 맞는 줄 목록"""
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


def render_rules(
    set_name: str,
    *,
    public: bool,
    deceased: bool,
    has_family: bool,
    death_policy: str = "deny",
    vars: dict[str, str] | None = None,
    rules_dir: Path = RULES_DIR,
    minor: bool = False,
) -> str:
    flags = _flags(
        public=public,
        deceased=deceased,
        has_family=has_family,
        death_policy=death_policy,
        minor=minor,
    )
    vars = {**DEFAULT_VARS, **(vars or {})}
    sections = []
    for title, modules in _manifest(rules_dir)["sets"][set_name]:
        lines: list[str] = []
        for name in modules:
            lines += render_block_file(rules_dir / f"{name}.md", flags, vars)
        if lines:
            sections.append("\n".join([title, *lines]))
    return "\n\n".join(sections)


def render_common_rules(
    *, public: bool, deceased: bool, has_family: bool, minor: bool = False
) -> str:
    """legacy 세트 공통 규칙 (조립기, 중간계층용, 골든 8개 고정, minor 면 미성년 규칙 추가)"""
    return render_rules(
        "legacy", public=public, deceased=deceased, has_family=has_family, minor=minor
    )


def common_rule_lines(*, public: bool, deceased: bool, has_family: bool) -> set[str]:
    """변형별 규칙 줄 집합 (중복 제거용, 제목과 빈 줄 제외)"""
    text = render_common_rules(public=public, deceased=deceased, has_family=has_family)
    return {ln.strip() for ln in text.splitlines() if ln.strip().startswith("- ")}


def family_guide(rows: Iterable) -> str:
    """가족 표 아래 안내 한 줄 (웹앱 추가 칸 있을 때만, 없으면 빈 문자열, isOwner/isMember 는 공유 가족 조립본만)"""
    rows = list(rows)
    others = [r for r in rows if not getattr(r, "is_listener", None)]
    parts = []
    if len(others) < len(rows):
        parts.append("아래 가족 중 '지금 대화하는 사람'으로 적힌 사람이 지금 대화하는 상대다.")
    if any(getattr(r, "listener_calls", None) for r in others):
        parts.append(
            "사용자가 '아빠'·'엄마'처럼 가족을 부르면 '사용자는 …라고 부름'을 보고 "
            "누구를 말하는지 찾는다."
        )
    if any(getattr(r, "deceased", None) for r in others):
        parts.append(
            "'돌아가심'으로 적힌 분은 이미 세상을 떠났으니 연락해 보라고 하거나 "
            "연락할 사람으로 짚지 않는다."
        )
    owner = next((r for r in others if getattr(r, "is_owner", None)), None)
    if owner is not None:
        # 순환 import 회피 (render 가 이 모듈 import)
        from pipeline.persona_prompt.render import particle

        n = owner.name
        parts.append(
            f"가족 표의 부르는 말·상황별 말·실제로 하던 말은 '이 대화 상대를 만든 가족'인 "
            f"{n}{particle(n, '이', '가')} 적었다. 그 속의 애칭이나 옛일은 {n}의 것이니, "
            "지금 대화하는 사람에게는 그 사람 줄의 부르는 말과 말 높이를 쓰고 "
            f"{n}{particle(n, '과', '와')}의 옛일을 지금 대화하는 사람과 함께한 일처럼 말하지 않는다."
        )
    listener = next((r for r in rows if getattr(r, "is_listener", None)), None)
    shared = owner is not None or getattr(listener, "is_member", None)  # 공유 가족 조립본
    if shared and (getattr(listener, "speech_level", None) or "").strip() == "존댓말":
        parts.append(
            "지금 대화하는 사람에게는 예시 문장이 반말로 적혀 있어도 어미를 존댓말로 바꿔 말한다."
        )
    return " ".join(parts)
