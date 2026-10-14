"""발화 분해 v1 — 보수적 분해 + 페르소나별 호칭 사전. 사전 판정 v1.1·가드 v1.1·TieredSelector 가 쓴다.

들어온 가족 발화를 조각으로 나눈다. 원문은 그대로 LLM 에 가고, 검색 질의(cue)만 바뀐다.
  address  부르는 말("할머니", "엄마")          → 답의 호칭
  speaker  자기소개에서 나온 이름("나 도윤이야") → 화자 식별(세션 상태)
  greeting 안부·인사 문장("잘 지냈어?")          → 답의 흐름. 검색에 안 넣음
  filler   맞장구·군말 문장("어.", "응.")
  cue      나머지 = 기억 검색 질의. 비면 검색 생략.

원칙: 확실할 때만 뗀다. 틀렸을 때 비용이 비대칭이라(못 떼면 운영과 같음, 잘못 떼면 검색 손실).
  - 문장 앞 호칭은 뒤에 문장부호가 오거나, 문장 나머지가 자기소개·인사뿐일 때만 뗀다.
    "할머니 고향이 어디였지?"의 "할머니"는 화제일 수 있어 남긴다.
  - 군말·접속어 뒤 호칭은 쉼표가 붙었을 때만 뗀다("근데 할머니, 그거 뭐지?" → "근데 그거 뭐지?").
  - 문장 끝 호칭은 쉼표 뒤("…돼, 할머니?") 또는 공백 뒤 마지막 낱말일 때만 뗀다.
  - 자기소개는 "나/저 + (사전에 있는 이름) + 야/예요…" 꼴만. 이름이 문장 화제로 쓰이면("도윤이 고등학교…") 안 뗀다.
  - 인사는 문장 전체가 인사 사전 꼴일 때만.
사전: intake M2-01 표(가족 이름·그분을 부르는 호칭) + P1-03 + 흔한 변이형. 일반 규칙이 아니라 페르소나별.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 호칭 변이형(사투리·STT 흔들림). 사전에 있는 호칭에만 붙인다.
ADDR_VARIANTS = {
    "할머니": ["할머니", "할머이", "할매", "할무니", "할머님"],
    "엄마": ["엄마", "엄마아", "어무이", "어매"],
    "어머니": ["어머니", "어머님"],
    "아빠": ["아빠", "아버지", "아버님"],
}
SURNAMES = set("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용")
RELATION_WORDS = {"며느리", "사위", "조카", "조카들", "큰오빠", "오빠", "형", "누나", "언니", "친정"}

FILLER = re.compile(r"^(어+|응+|음+|아+|네|예|그래|으응|어어|엉|웅|흠)[\s.,!?~…]*$")
GREETING = re.compile(
    r"^(?:잘\s*(?:지냈|지내|있었|있니|있어)\S*|밥\s*(?:은|잘|좀)?\s*(?:먹었|드셨|먹)\S*|식사\s*(?:하셨|했|하)\S*|"
    r"별일\s*없\S*|보고\s*싶었\S*|사랑해\S*|나\s*왔\S*|저\s*왔\S*|여보세요\S*|안녕\S*|오랜만\S*|건강\S*|잘\s*자\S*|"
    r"뭐\s*해\S*|뭐\s*하고\s*있\S*)[\s.,!?~…]*$")


@dataclass
class Lexicon:
    addresses: list[str]                  # 가족이 그분을 부르는 말(변이형 포함)
    names: list[str]                      # 자기소개에 나올 수 있는 이름(성 뗀 것 + 원형)
    source: str = ""


@dataclass
class Split:
    text: str
    cue: str
    address: list[str] = field(default_factory=list)
    speaker: str | None = None
    greeting: list[str] = field(default_factory=list)
    filler: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return _norm(self.cue) != _norm(self.text)

    @property
    def removed(self) -> list[str]:
        return [*(f"호칭:{a}" for a in self.address), *([f"화자:{self.speaker}"] if self.speaker else []),
                *(f"인사:{g}" for g in self.greeting), *(f"군말:{f}" for f in self.filler)]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _given(name: str) -> list[str]:
    name = name.strip()
    out = [name]
    if len(name) == 3:
        out.append(name[1:])
    elif len(name) == 2 and name[0] in SURNAMES:
        out.append(name[1:])
    return out


def lexicon_from_intake(path: Path) -> Lexicon:
    """intake 작성본 md 의 M2-01 표·P1-03 에서 호칭·이름 사전을 만든다."""
    md = path.read_text(encoding="utf-8")
    addr, names = set(), set()
    m = re.search(r"\*\*P1-03[^\n]*\n+\s*<u>([^<]+)</u>", md)
    if m:
        addr.add(m.group(1).strip())
    sec = md.split("**M2-01", 1)[1].split("**M2-02", 1)[0] if "**M2-01" in md else ""
    for line in sec.splitlines():
        if not line.startswith("| ") or line.startswith("| 이름") or line.startswith("|---"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        name = cells[0]
        if name and " " not in name and name not in RELATION_WORDS:
            names.update(_given(name))
        call = re.search(r"<u>([^<]+)</u>", cells[2])          # 그분이 이 사람을 부르는 호칭 ("도윤이", "정우야")
        if call:
            c = re.sub(r"(야|아)$", "", call.group(1).strip())
            if c and c not in {"여보"}:
                names.add(c)
        to = re.search(r"<u>([^<]+)</u>", cells[4])            # 이 사람이 그분을 부르는 호칭
        if to:
            addr.add(to.group(1).strip())
    full = set()
    for a in addr:
        full.update(ADDR_VARIANTS.get(a, [a]))
    names = {n for n in names if len(n) >= 1}
    return Lexicon(addresses=sorted(full, key=len, reverse=True), names=sorted(names, key=len, reverse=True),
                   source=str(path))


def lexicon_from_intake_json(intake: dict | None) -> Lexicon | None:
    """등록부 intake.json(pipeline.s0_intake 결과)의 familyMembers 에서 사전을 만든다. 가족이 없으면 None."""
    members = ((intake or {}).get("subjectContext") or {}).get("familyMembers") or []
    addr, names = set(), set()
    for m in members:
        name = str(m.get("name") or "").strip()
        if name and " " not in name and name not in RELATION_WORDS:
            names.update(_given(name))
        for a in m.get("addressTerms") or []:
            if a:
                addr.add(str(a).strip())
    if not addr and not names:
        return None
    full = set()
    for a in addr:
        full.update(ADDR_VARIANTS.get(a, [a]))
    return Lexicon(addresses=sorted(full, key=len, reverse=True), names=sorted(names, key=len, reverse=True),
                   source="intake.json")


def lexicon_from_terms(addresses: list[str], names: list[str]) -> Lexicon | None:
    """PersonaProfile.address_terms / family_names 로 사전을 만든다."""
    if not addresses and not names:
        return None
    full = set()
    for a in addresses:
        full.update(ADDR_VARIANTS.get(a, [a]))
    given = set()
    for n in names:
        given.update(_given(n))
    return Lexicon(addresses=sorted(full, key=len, reverse=True), names=sorted(given, key=len, reverse=True),
                   source="profile")


class Splitter:
    impl = "split_v1"

    def __init__(self, lex: Lexicon):
        self.lex = lex
        A = "|".join(map(re.escape, lex.addresses))
        N = "|".join(map(re.escape, lex.names))
        self._lead = re.compile(rf"^\s*({A})(?=$|[\s,.!?~…])(\s*[,.!~…]+)?\s*")
        self._trail = re.compile(rf"(?:,\s*|\s+)({A})\s*([?.!~…]*)\s*$")
        self._intro = re.compile(rf"(?:^|(?<=[\s,]))(?:나|저)\s*,?\s*({N})(?:이)?(?:야|예요|에요|이에요|이야|요|임)?(?=$|[\s,.!?~…])[\s,.!~…]*")
        # 군말·접속어 뒤 쉼표 부름: "근데 할머니, …" / "어, 할머니, …" → 호칭만 뗀다(쉼표가 있을 때만)
        self._lead_marked = re.compile(rf"^\s*((?:어|아|응|음|근데|아니|그럼|그러니까|그래서|아무튼|암튼|맞아)[,\s]+)({A})\s*,\s*")
        self._intro_bare = re.compile(r"^(?:나야|나예요|저예요|저에요|접니다|나)[\s.,!~…]*$")

    def _canon(self, name: str) -> str:
        if name.endswith("이") and name[:-1] in self.lex.names:
            name = name[:-1]
        return name[1:] if len(name) == 3 and name[1:] in self.lex.names else name

    def _sentences(self, text: str) -> list[str]:
        return [s for s in re.findall(r"[^.?!~…\n]+[.?!~…]*|[.?!~…]+", text) if s.strip()]

    def split(self, text: str) -> Split:
        out = Split(text=text, cue="")
        cues = []
        for s in self._sentences(text):
            s = s.strip()
            if FILLER.match(s):
                out.filler.append(s)
                continue
            addr, punct = None, False
            mm = self._lead_marked.match(s)
            if mm:
                out.address.append(mm.group(2))
                s = (mm.group(1).strip(" ,") + " " + s[mm.end():]).strip()
            m = self._lead.match(s)
            rest = s
            if m:
                addr, punct, rest = m.group(1), bool(m.group(2)), s[m.end():]
            mi = self._intro.search(rest)
            if mi:
                out.speaker = out.speaker or self._canon(mi.group(1))
                rest = (rest[:mi.start()] + " " + rest[mi.end():]).strip(" ,")
                m2 = self._lead.match(rest)
                if m2 and not rest[m2.end():].strip(" .,!?~…"):
                    addr, punct, rest = addr or m2.group(1), True, ""
            elif self._intro_bare.match(rest.strip()) and rest.strip():
                rest = ""
            rest = _norm(rest)
            if not rest or re.fullmatch(r"[\s.,!?~…]*", rest):
                if addr:
                    out.address.append(addr)
                continue
            if GREETING.match(rest) and len(rest) <= 16:
                if addr:
                    out.address.append(addr)
                out.greeting.append(rest)
                continue
            if addr and (punct or mi):
                out.address.append(addr)            # 부름이 확실함 — 떼고 나머지만
            elif addr:
                rest = s[m.start():m.end()].strip() + " " + rest if not mi else rest   # 화제일 수 있어 남긴다
            mt = self._trail.search(rest)
            if mt and mt.start() > 0:
                out.address.append(mt.group(1))
                rest = rest[:mt.start()].rstrip(" ,") + (mt.group(2) or "")
            cues.append(_norm(rest))
        out.cue = _norm(" ".join(cues))
        return out
