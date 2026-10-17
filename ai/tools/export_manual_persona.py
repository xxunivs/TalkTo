"""손으로 다듬은 페르소나 폴더(output/<대상자>_manual) → 관리자 '페르소나 가져오기' 묶음 JSON.

BE 관리자 화면(/admin/import)에 올릴 묶음을 만든다. 지침은 BE 가 가져올 때 조립한다(방식 guarded·verbatim,
사망 정책, 사망 한마디, 지금 대화하는 사람을 화면에서 고른다). 그래서 묶음에는 재료만 담는다.
묶음 모양의 정본은 BE `app/schemas/persona_import.py`(PersonaBundle)이다. 이 도구는 그 모양을 따른다.

    .venv\\Scripts\\python.exe tools\\export_manual_persona.py --src output\\<대상자>_manual ^
        --voice-id-file <Fish 모델ID.txt> --out <git 레포 밖>\\<대상자>_bundle.json

읽는 것(이것만):
    <src>/s3_synthesis/c_system_prompt.txt        옛 프롬프트. 줄바꿈만 LF 로 맞추고 한 글자도 바꾸지 않는다
    <src>/s2_extraction/e_recording_memory.json   장기기억 전부(단기기억·설문 기억은 읽지 않는다)
    <src>/s3_synthesis/d_memory_embedding.json    옛 벡터 중 확인용 3건(파일이 없으면 건너뛴다). 모양은 a_chat 이
                                                  읽는 셋을 다 받지만, 확인용으로 싣는 것은 옛 목록 모양뿐이다(아래)
    --voice-id-file                               Fish 모델 id 한 줄(32자 hex) → "fish_" + id
파이프라인은 부르지 않는다(run_pipeline 은 정본 c_system_prompt.txt 를 덮어쓴다). 원본은 읽기만 한다.
쓰는 곳은 --out 하나다. git 레포 안이면 멈춘다(가족 자료라 커밋하지 않는다).
화면에는 건수·글자 수·sha·출력 경로만 찍는다. 본문·제목·옛 id 는 찍지 않는다.

묶음(format "talkto-persona-bundle", version 1):
    source     {subject, promptSha256, builtAt, promptRev, promptBlob, toolCommit}
    persona    {name, relation, addressTerm, status: "deceased"}
    prompt     legacy      옛 프롬프트(LF). promptSha256 = sha256(legacy, UTF-8)
               rules       지금 L1 공통 규칙(layered·비공개·고인·가족 있음, 가족 목록은 '아래').
                           사망 정책 줄 자리는 {{death_rule}} 한 줄
               deathRules  {deny|brief|ask_only|open: 그 정책의 규칙 줄}. brief·ask_only 에는
                           '"{{death_answer}}" 정도로(만) ' 구절이 있다
               bridge      연결 문단. 마지막 줄이 "- {{listener_line}}"
    memories   [{legacyId, category, title, content, sensitive}]  — title 은 옛 제목 그대로
               (' (물으면만)' 표시는 BE 가 붙인다)
    probeEmbeddings [{legacyId, embedding}]  — 옛 벡터 3건(legacyId 순). 옛 입력은 f"{title}: {content}"
               였다(지금 BE 는 본문만 임베딩). 같은 임베딩 모델인지 보는 참고값. BE 가 그 입력을 다시
               임베딩해 견주므로 옛 embeddings.py 의 목록 모양([{id, embedding}])일 때만 싣는다. 지금
               파이프라인의 {"embeddings": [...]} 는 memoryText 를, {id: 벡터} 는 알 수 없는 글을 임베딩한
               것이라 견주면 같은 모델이어도 낮게 나온다 → 싣지 않는다(0건)
    voice      {voiceId: "fish_<32 hex>"} 또는 null

지침 조립(BE 가 가져올 때. compose_instructions 가 같은 규칙의 확인용 구현):
    verbatim  legacy 그대로
    guarded   rules({{death_rule}} ← deathRules[정책]. {{death_answer}} ← 한마디(공백은 한 칸으로),
              한마디가 비면 그 구절을 통째로 뺀다) + 빈 줄 + bridge({{listener_line}} ← '지금 대화하는
              사람' 한 줄, 내 이름이 없으면 그 줄째 뺀다) + 빈 줄 + legacy. rules·bridge 는 앞뒤 공백을
              걷는다(BE 와 같다). 결과에 {{ 가 남거나 50,000자를 넘으면 안 된다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pipeline.s3_synthesis.common_rules import DEATH_POLICIES, render_rules

FORMAT, VERSION = "talkto-persona-bundle", 1
PROMPT_FILE = Path("s3_synthesis") / "c_system_prompt.txt"
MEMORY_FILE = Path("s2_extraction") / "e_recording_memory.json"
EMBEDDING_FILE = Path("s3_synthesis") / "d_memory_embedding.json"

DEFAULT_RELATION = "할머니"
DEFAULT_DEATH_POLICY = "ask_only"  # 대표가 2026-09 설문 M9-01 에서 고른 등급
# 옛 본문의 가족 목록은 L1 '아래'에 온다(common_rules 기본값 '위 가족 목록' 이면 틀린다)
FAMILY_LIST = "아래 가족 목록"
SENSITIVE_MARK = " (물으면만)"  # BE 가 붙인다. 여기서는 길이 확인에만 쓴다
DEATH_SLOT = "{{death_rule}}"
ANSWER_SLOT = "{{death_answer}}"
LISTENER_SLOT = "{{listener_line}}"
# 한마디가 비면 규칙에서 빼는 구절(BE 도 같은 구절을 뺀다). 규칙 문장이 바뀌면 여기서 멈춘다.
ANSWER_CLAUSE = re.compile(r'"\{\{death_answer\}\}" 정도로(?:만)? ')
BRIDGE = (
    "# 아래 페르소나 설명과 함께 쓰는 법\n"
    "- 아래 글은 예전에 손으로 다듬은 대상자의 페르소나 설명이다. 말투·가족·옛일은 아래 글을 따른다.\n"
    "- 대화마다 따로 붙는 '참고 기억' 블록이 아래 글이 말하는 장기기억이다. 아래 글이 sensitive 로 "
    "표시한다고 한 민감 기억은 그 블록에서 제목 끝에 '(물으면만)'이 붙어 있다.\n"
    "- 아래 글과 위 규칙이 어긋나면 위 규칙을 따른다. '안전 가드레일 (최우선)'은 언제나 아래 글보다 앞선다.\n"
    # 옛 글에는 사인을 묻는 질문에 답할 말·예시가 따로 적혀 있을 수 있다(L1 사망 규칙과 어긋난다). 옛 글은
    # 고치지 않으니 여기서 그 대목을 짚어 둔다.
    "- 돌아가신 경위·사인·임종을 물으면 아래 글에 그에 대한 답이나 예시 문장이 있어도 쓰지 않고, 위 "
    "'안전 가드레일 (최우선)'의 사망·임종 규칙대로만 답한다.\n"
    "- " + LISTENER_SLOT  # BE 가 '지금 대화하는 사람' 한 줄로 채우고, 내 이름이 없으면 이 줄째 뺀다
)

MEMORY_KEYS = {"id", "category", "title", "content", "tags"}
KNOWN_TAGS = {"sensitive"}
PROBE_COUNT = 3
MAX_PROBE_DIMENSIONS = 4_096  # BE ProbeEmbedding.embedding 한도
# BE PersonaBundle 한도
MAX_INSTRUCTIONS = 50_000
MAX_RULES, MAX_BRIDGE, MAX_DEATH_RULE = 30_000, 3_000, 2_000
MAX_MEMORIES, MAX_ID, MAX_CATEGORY, MAX_TITLE, MAX_CONTENT = 200, 100, 30, 100, 5_000
MAX_NAME, MAX_RELATION, MAX_ADDRESS = 40, 40, 30
MAX_MEMORY_TITLE = 120  # BE Memory.title — 표시를 붙인 뒤
# BE 요청 본문 상한 2 MiB. 묶음을 감싸는 칸 몫을 남긴다.
MAX_BUNDLE_BYTES = 2 * 1024 * 1024 - 64 * 1024
# 확인용 최대값(BE ImportPromptOptions·ImportOverrides 한도에 맞춘 가짜 글)
SAMPLE_ANSWER = "가" * 80
SAMPLE_LISTENER = "가" * 200


class BundleError(ValueError):
    """묶음을 만들 수 없음. 메시지에는 본문을 넣지 않는다(위치·건수만)."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_prompt(path: Path) -> str:
    """옛 프롬프트. 줄바꿈만 LF 로 맞추고 나머지는 그대로."""
    text = path.read_bytes().decode("utf-8").removeprefix("﻿")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise BundleError("prompt: empty")
    if "{{" in text:
        raise BundleError("prompt: contains '{{' (unfilled placeholder)")
    if len(text) > MAX_INSTRUCTIONS:
        raise BundleError(f"prompt: {len(text)} chars > {MAX_INSTRUCTIONS}")
    return text


def rules_template(family_list: str = FAMILY_LIST) -> tuple[str, dict[str, str]]:
    """L1 layered(비공개·고인·가족 있음) 규칙 틀과 사망 정책별 줄.

    정책마다 지금 규칙 코드로 렌더해 서로 다른 줄만 {{death_rule}} 로 뺀다(규칙 글을 여기서 새로 쓰지 않는다).
    """
    texts = {p: render_rules("layered", public=False, deceased=True, has_family=True, death_policy=p,
                             vars={"family_list": family_list, "death_answer": ANSWER_SLOT})
             for p in DEATH_POLICIES}
    lines = {p: t.split("\n") for p, t in texts.items()}
    base = lines[DEATH_POLICIES[0]]
    shortest = min(len(ls) for ls in lines.values())
    head = 0
    while head < shortest and all(ls[head] == base[head] for ls in lines.values()):
        head += 1
    tail = 0
    while tail < shortest - head and all(ls[-1 - tail] == base[-1 - tail] for ls in lines.values()):
        tail += 1
    template = "\n".join([*base[:head], DEATH_SLOT, *base[len(base) - tail:]])
    rules: dict[str, str] = {}
    for p, ls in lines.items():
        rule = "\n".join(ls[head:len(ls) - tail])
        if not rule.strip():
            raise BundleError(f"rules: no death rule line for policy {p}")
        if template.replace(DEATH_SLOT, rule) != texts[p]:
            raise BundleError(f"rules: death policy {p} differs outside one block")
        if ANSWER_SLOT in rule and "{{" in ANSWER_CLAUSE.sub("", rule):
            raise BundleError(f"rules: death policy {p} answer clause not found (rule wording changed?)")
        if len(rule) > MAX_DEATH_RULE:
            raise BundleError(f"rules: death policy {p} line {len(rule)} chars > {MAX_DEATH_RULE}")
        rules[p] = rule
    if "{{" in template.replace(DEATH_SLOT, ""):
        raise BundleError("rules: unexpected placeholder")
    return template, rules


def convert_memories(items: object) -> list[dict]:
    """옛 장기기억 → 묶음 기억. 모르는 칸·태그는 멈춘다(말없이 버리지 않는다)."""
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_MEMORIES:
        raise BundleError(f"memories: expected a list of 1..{MAX_MEMORIES}")
    limits = {"id": MAX_ID, "category": MAX_CATEGORY, "title": MAX_TITLE, "content": MAX_CONTENT}
    out: list[dict] = []
    seen: set[str] = set()
    for i, m in enumerate(items, 1):
        where = f"memory #{i}"
        if not isinstance(m, dict):
            raise BundleError(f"{where}: not an object")
        extra = set(m) - MEMORY_KEYS
        if extra:
            raise BundleError(f"{where}: unknown fields {sorted(extra)}")
        for key, limit in limits.items():
            value = m.get(key)
            if not isinstance(value, str) or not value.strip():
                raise BundleError(f"{where}: '{key}' missing or empty")
            if len(value) > limit:
                raise BundleError(f"{where}: '{key}' {len(value)} chars > {limit}")
        tags = m.get("tags") or []
        if not isinstance(tags, list) or not set(tags) <= KNOWN_TAGS:
            raise BundleError(f"{where}: unknown tags (only {sorted(KNOWN_TAGS)} are known)")
        if m["id"] in seen:
            raise BundleError(f"{where}: duplicate id")
        seen.add(m["id"])
        sensitive = "sensitive" in tags
        if len(m["title"]) + (len(SENSITIVE_MARK) if sensitive else 0) > MAX_MEMORY_TITLE:
            raise BundleError(f"{where}: title too long with the sensitive mark")
        out.append({"legacyId": m["id"], "category": m["category"], "title": m["title"],
                    "content": m["content"], "sensitive": sensitive})
    return out


def probe_embeddings(memories: list[dict], raw: object, count: int = PROBE_COUNT) -> list[dict]:
    """옛 벡터 확인용 몇 건(legacyId 순으로 고정). 벡터가 없는 기억은 건너뛴다.

    파일 모양은 pipeline/s5_chat/a_chat.load_embeddings 가 읽는 셋을 다 받는다. 그중 옛 목록 모양
    ([{id, embedding}], 입력 "제목: 본문")만 싣고, 지금 파이프라인의 {"embeddings": [...]}(입력 memoryText)·
    {id: 벡터}(입력 모름)는 BE 가 같은 글로 다시 임베딩할 수 없어 싣지 않는다(빈 목록).
    """
    if isinstance(raw, dict):
        if not isinstance(raw.get("embeddings", []), list):
            raise BundleError("embeddings: 'embeddings' must be a list")
        return []
    if not isinstance(raw, list):
        raise BundleError("embeddings: expected a list or an object")
    vectors = {r["id"]: r["embedding"] for r in raw
               if isinstance(r, dict) and isinstance(r.get("id"), str) and isinstance(r.get("embedding"), list)}
    picked = sorted(m["legacyId"] for m in memories if m["legacyId"] in vectors)[:count]
    if len({len(vectors[k]) for k in picked}) > 1:
        raise BundleError("embeddings: probe vectors differ in length")
    for k in picked:
        vec = vectors[k]
        if not vec or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in vec):
            raise BundleError("embeddings: probe vector is empty or not numeric")
        if len(vec) > MAX_PROBE_DIMENSIONS:
            raise BundleError(f"embeddings: probe vector {len(vec)} > {MAX_PROBE_DIMENSIONS} dimensions")
    return [{"legacyId": k, "embedding": vectors[k]} for k in picked]


def read_voice_id(path: Path) -> str:
    raw = path.read_text(encoding="utf-8-sig").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", raw):
        raise BundleError("voice id file: expected one 32-character hex id")
    return "fish_" + raw


def _one_line(value: str | None) -> str:
    """BE persona_import._one_line 과 같다: 공백(줄바꿈 포함)을 한 칸으로. '{{' 는 짠 뒤 한 번에 본다."""
    return re.sub(r"\s+", " ", value or "").strip()


def compose_instructions(prompt: dict, *, mode: str = "guarded", death_policy: str = DEFAULT_DEATH_POLICY,
                         death_answer: str | None = None, listener_line: str | None = None) -> str:
    """BE 가 가져올 때 짜는 지침과 같은 규칙(확인용). listener_line 은 BE 가 myName 으로 만든 한 줄."""
    if mode == "verbatim":
        return prompt["legacy"]
    if mode != "guarded":
        raise BundleError(f"mode: expected guarded or verbatim, got {mode!r}")
    if death_policy not in prompt["deathRules"]:
        raise BundleError(f"death policy: unknown {death_policy!r}")
    answer = _one_line(death_answer)
    rule = prompt["deathRules"][death_policy].strip()
    rule = rule.replace(ANSWER_SLOT, answer) if answer else ANSWER_CLAUSE.sub("", rule)
    rules = prompt["rules"].replace(DEATH_SLOT, rule)
    listener = _one_line(listener_line)
    bridge = "\n".join(line.replace(LISTENER_SLOT, listener) for line in prompt["bridge"].split("\n")
                       if listener or LISTENER_SLOT not in line)
    text = "\n\n".join([rules.strip(), bridge.strip(), prompt["legacy"]])
    if "{{" in text:
        raise BundleError("instructions: placeholder left after composing")
    if len(text) > MAX_INSTRUCTIONS:
        raise BundleError(f"instructions: {len(text)} chars > {MAX_INSTRUCTIONS}")
    return text


def check_compositions(prompt: dict) -> dict[str, int]:
    """고를 수 있는 조합을 모두 짜 본다(정책 4 × 한마디 있음·없음 × 대화하는 사람 있음·없음 + verbatim)."""
    if len(prompt["rules"]) > MAX_RULES or len(prompt["bridge"]) > MAX_BRIDGE:
        raise BundleError("rules or bridge too long")
    if prompt["rules"].count(DEATH_SLOT) != 1 or prompt["bridge"].count(LISTENER_SLOT) != 1:
        raise BundleError("rules/bridge must hold exactly one slot each")
    rules_head, bridge_head = prompt["rules"].split("\n", 1)[0], prompt["bridge"].split("\n", 1)[0]
    longest = 0
    for policy in prompt["deathRules"]:
        for answer in (None, SAMPLE_ANSWER):
            for listener in (None, SAMPLE_LISTENER):
                text = compose_instructions(prompt, death_policy=policy, death_answer=answer,
                                            listener_line=listener)
                body = text[:len(text) - len(prompt["legacy"])]
                if not (text.endswith("\n\n" + prompt["legacy"]) and body.startswith(rules_head)
                        and bridge_head in body):
                    raise BundleError("instructions: guarded order is not rules → bridge → legacy")
                longest = max(longest, len(text))
    return {
        "default": len(compose_instructions(prompt)),
        "longest": longest,
        "verbatim": len(compose_instructions(prompt, mode="verbatim")),
    }


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True,
                              timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None


def _limited(label: str, value: str | None, limit: int) -> str | None:
    value = (value or "").strip() or None
    if value is not None and len(value) > limit:
        raise BundleError(f"{label}: {len(value)} chars > {limit}")
    return value


def build_bundle(src: Path, *, name: str | None = None, relation: str = DEFAULT_RELATION,
                 address_term: str | None = None, voice_id_file: Path | None = None,
                 built_at: str | None = None, provenance: bool = True) -> dict:
    """원본 폴더 → 묶음 dict. 원본은 읽기만 한다."""
    prompt_path = src / PROMPT_FILE
    legacy = read_prompt(prompt_path)
    memories = convert_memories(json.loads((src / MEMORY_FILE).read_text(encoding="utf-8")))
    embedding_path = src / EMBEDDING_FILE
    probes = (probe_embeddings(memories, json.loads(embedding_path.read_text(encoding="utf-8")))
              if embedding_path.exists() else [])
    rules, death_rules = rules_template()
    prompt = {"legacy": legacy, "rules": rules, "deathRules": death_rules, "bridge": BRIDGE}
    check_compositions(prompt)

    name = _limited("name", name or src.name.removesuffix("_manual"), MAX_NAME)
    relation = _limited("relation", relation, MAX_RELATION)
    if not name or not relation:
        raise BundleError("persona: name and relation are required")
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "format": FORMAT,
        "version": VERSION,
        "source": {
            "subject": src.name,
            "promptSha256": sha256_text(legacy),
            "builtAt": built_at or now,
            "promptRev": "disk",
            "promptBlob": _git("hash-object", str(prompt_path)) if provenance else None,
            "toolCommit": _git("rev-parse", "HEAD") if provenance else None,
        },
        "persona": {
            "name": name,
            "relation": relation,
            "addressTerm": _limited("addressTerm", address_term, MAX_ADDRESS) or relation,
            "status": "deceased",
        },
        "prompt": prompt,
        "memories": memories,
        "probeEmbeddings": probes,
        "voice": {"voiceId": read_voice_id(voice_id_file)} if voice_id_file else None,
    }


def inside_git_repo(path: Path) -> bool:
    return any((p / ".git").exists() for p in (path, *path.parents))


def write_bundle(bundle: dict, out: Path, *, src: Path) -> tuple[int, str]:
    """git 레포·원본 폴더 밖에만 쓴다. 반환: (바이트 수, sha256)."""
    out = out.resolve()
    if out.suffix.lower() != ".json":
        raise BundleError("--out must be a .json file")
    if inside_git_repo(out.parent):
        raise BundleError("--out is inside a git repository (family data must stay out of repos)")
    if out.is_relative_to(src.resolve()):
        raise BundleError("--out must not be inside the source folder")
    if out.is_dir():
        raise BundleError("--out is a directory")
    data = (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleError(f"bundle: {len(data)} bytes > {MAX_BUNDLE_BYTES}")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, out)
    return len(data), hashlib.sha256(data).hexdigest()


def summary(bundle: dict) -> list[str]:
    """건수·글자 수·sha 만. 본문·제목·id 는 넣지 않는다."""
    mems = bundle["memories"]
    prompt = bundle["prompt"]
    sizes = check_compositions(prompt)
    probes = bundle["probeEmbeddings"]
    dims = sorted({len(p["embedding"]) for p in probes})
    marked = max(len(m["title"]) + (len(SENSITIVE_MARK) if m["sensitive"] else 0) for m in mems)
    lengths = [len(m["content"]) for m in mems]
    return [
        (f"memories   {len(mems)} (sensitive {sum(m['sensitive'] for m in mems)}, "
         f"categories {len({m['category'] for m in mems})}, title max {marked} chars with mark, "
         f"content {min(lengths)}-{max(lengths)} chars)"),
        f"legacy     {len(prompt['legacy'])} chars, sha256 {bundle['source']['promptSha256'][:16]}…",
        (f"rules      {len(prompt['rules'])} chars (layered L1, private·deceased·family), "
         f"death policies {len(prompt['deathRules'])}"),
        f"bridge     {len(prompt['bridge'])} chars",
        (f"guarded    {sizes['default']} chars ({DEFAULT_DEATH_POLICY}, no answer, no listener), "
         f"longest {sizes['longest']} / {MAX_INSTRUCTIONS}"),
        f"verbatim   {sizes['verbatim']} chars",
        (f"probes     {len(probes)} × {'/'.join(map(str, dims))}" if probes else
         "probes     0 (only the old list-form embedding file, input 'title: content', is comparable)"),
        f"voice      {'fish_ + 32 hex' if bundle['voice'] else 'none'}",
        (f"provenance promptBlob {'yes' if bundle['source']['promptBlob'] else 'no'}, "
         f"toolCommit {(bundle['source']['toolCommit'] or 'none')[:12]}"),
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="손으로 다듬은 페르소나 → 관리자 가져오기 묶음(JSON)")
    ap.add_argument("--src", required=True, type=Path, help="output/<대상자>_manual 폴더")
    ap.add_argument("--out", required=True, type=Path, help="묶음 .json 경로(git 레포 밖)")
    ap.add_argument("--voice-id-file", type=Path, help="Fish 모델 id 한 줄 파일(없으면 목소리 없음)")
    ap.add_argument("--name", help="그분 성함(기본: 폴더 이름에서 _manual 을 뺀 것)")
    ap.add_argument("--relation", default=DEFAULT_RELATION, help=f"그분이 나에게 어떤 분인지(기본 {DEFAULT_RELATION})")
    ap.add_argument("--address-term", help="그분을 부르는 말(기본: relation)")
    args = ap.parse_args(argv)
    try:
        bundle = build_bundle(args.src, name=args.name, relation=args.relation,
                              address_term=args.address_term, voice_id_file=args.voice_id_file)
        size, digest = write_bundle(bundle, args.out, src=args.src)
        lines = summary(bundle)
    except BundleError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: {type(e).__name__}: cannot read or write ({getattr(e, 'filename', None) or 'json'})",
              file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    print(f"bundle     {size} bytes, sha256 {digest}")
    print(f"written    {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
