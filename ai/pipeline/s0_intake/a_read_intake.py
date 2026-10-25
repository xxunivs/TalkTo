"""작성본 md -> 문항별 답 (고른 선지, 빈칸 값, 표 행, 모름, dim 문항)"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# 문항 머리: ID, 질문, 형식 (앞뒤 필수 표시 〔…〕 는 버림)
QID = re.compile(
    r"^\*\*([A-Z0-9-]+) · (.*?)\*\*"
    r"(?:\s*〔[^〕]*〕)?(?:\s+`(.+?)`)?(?:\s*〔[^〕]*〕)?\s*$"
)
# 답 없음 표시: 선지로 넣으면 프롬프트, 기억으로 샘 (돌아가신 분은 '기억이 안 난다'고 말하지 않음)
UNKNOWN_MARKS = ("모름", "기억이 안 나", "따로 없었")
# 표 칸 속 모름 표시 (체크 여부와 무관하게 삭제, b_split_intake._plain)
UNKNOWN_CELL_MARK = re.compile(r"[□■]\s*(?:모름|기억이\s*안\s*나요?|따로\s*없었어요?)")
UNDERLINED = re.compile(r"<u>(.*?)</u>")
CHECKED = re.compile(r"■\s*([^■□\n]+)")
TABLE_SEP = re.compile(r"^\|[\s:\|]*-[\s:\-\|]*\|$")


@dataclass
class Answer:
    qid: str
    question: str
    fmt: str | None = None
    skipped: bool = False  # 이 사람에게는 뜨지 않는 문항
    checked: list[str] = field(default_factory=list)  # 고른 선지
    values: list[str] = field(default_factory=list)  # 빈칸에 적은 값
    rows: list[list[str]] = field(default_factory=list)  # 표 (머리행 제외)
    header: list[str] = field(default_factory=list)  # 표 머리행
    unknown: bool = False  # 모름에 체크

    @property
    def value(self) -> str | None:
        return self.values[0] if self.values else None

    @property
    def choice(self) -> str | None:
        return self.checked[0] if self.checked else None

    def __bool__(self) -> bool:
        return bool(self.checked or self.values or self.rows)


def _clean(text: str) -> str:
    text = re.sub(r"</?u>", "", text)
    text = re.sub(r"\*\*", "", text)
    return " ".join(text.split()).strip()


def parse(path: Path | str) -> dict[str, Answer]:
    out: dict[str, Answer] = {}
    cur: Answer | None = None
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        m = QID.match(line)
        if m:
            cur = Answer(qid=m.group(1), question=_clean(m.group(2)), fmt=m.group(3))
            out[cur.qid] = cur
            continue
        if line.startswith("#"):
            cur = None
            continue
        if cur is None or not line.strip():
            continue
        if line.strip() == "<!--dim-->":
            cur.skipped = True
            continue
        if line.startswith(">"):  # 예시, 가이드 줄은 답 아님
            continue
        if line.startswith("|"):
            if TABLE_SEP.match(line.strip()):
                continue
            cells = [_clean(c) for c in line.strip().strip("|").split("|")]
            if not cur.header:
                cur.header = [c.split("<br>")[0].strip() for c in cells]
            elif any(c and not c.startswith("_") and c != "□" for c in cells):
                cur.rows.append(cells)
            continue
        for value in UNDERLINED.findall(line):
            value = _clean(value)
            if value:
                cur.values.append(value)
        for hit in CHECKED.findall(line):
            hit = _clean(hit)
            hit = re.sub(r"^[①-⑩]\s*", "", hit)
            if not hit:
                continue
            if hit.startswith(UNKNOWN_MARKS):
                cur.unknown = True
            elif hit.startswith("실제로 들은 말은 아니"):
                cur.values.append("__추정__")
            else:
                cur.checked.append(hit)
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="작성본 md 를 문항별로 읽어 확인용으로 찍는다")
    ap.add_argument("path")
    args = ap.parse_args()
    answers = parse(args.path)
    filled = [a for a in answers.values() if a and not a.skipped]
    print(f"문항 {len(answers)}개 / 답 있는 것 {len(filled)}개\n")
    for a in filled:
        bits = []
        if a.checked:
            bits.append("고름: " + " / ".join(a.checked))
        if a.values:
            bits.append("값: " + " / ".join(a.values))
        if a.rows:
            bits.append(f"표 {len(a.rows)}행")
        print(f"{a.qid:8} {a.question[:26]:28} {' | '.join(bits)[:90]}")
