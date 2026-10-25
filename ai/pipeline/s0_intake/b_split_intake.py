"""작성본 답 -> persona_intake.json(A, 프롬프트 슬롯) + 기억 조각(B) (분류: docs/Persona_Intake_문항분류.md)"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline.s0_intake.a_read_intake import UNKNOWN_CELL_MARK, Answer, parse

# 선지는 프롬프트(A)로 가고 서술 답만 기억에 넣는 문항
VALUES_ONLY = {"M4-01", "M1-09", "M6-03", "M1-06"}

# B 문항: 기억 한 줄 앞에 붙일 말
MEMORY_LEAD = {
    "P1-06": "이 시점을 남기려는 이유",
    "C-08": "애정을 표현했던 장면",
    "M4-01": "성격이 드러난 일화",
    "M1-01": "태어난 해",
    "M1-02": "고향",
    "M1-03": "고향은 어떤 곳이었나",
    "M1-04": "자란 동네와 살던 집",
    "M1-06": "학교",
    "M1-07": "어떤 일을 하며 살아왔나",
    "M1-08": "가족 안에서의 역할",
    "M1-09": "종교",
    "M1-10": "기도하거나 의지하던 습관",
    "M4-03": "돈에 대한 태도",
    "M4-07": "신앙이나 죽음에 대해",
    "M5-01": "어린 시절 살던 집",
    "M5-02": "학창·청년 시절",
    "M5-03": "배우자와 처음 만났을 때",
    "M5-04": "자녀가 어릴 때",
    "M5-05": "가장 애썼던 시절",
    "M5-06": "가장 행복했던 때",
    "M5-07A": "힘들었던 시기",
    "M5-08": "말년의 하루",
    "M5-09": "더 남기고 싶은 장면",
    "M6-02": "음식으로 사람을 챙기던 방식",
    "M6-03": "자주 주던 선물이나 챙김",
    "T1-01": "언제 돌아가셨나",
    "T1-04": "그 무렵 이야기",
    "T2-01": "미래의 가족에게 남기고 싶은 말",
    "T4-01": "이 시점을 남기려는 계기",
    "T4-02": "지금의 그분을 대표하는 것",
    "T4-03": "몇 년 뒤의 가족에게 하고 싶은 말",
}
# 표로 답한 B 문항 (한 행이 기억 하나)
MEMORY_TABLES = {
    "M1-05": "살아온 곳",
    "M2-03": "가족과의 기억",
    "M6-01": "대표 음식",
    "M6-04": "자주 계시던 장소",
    "M6-05": "떠올리게 하는 것",
    "M6-07": "취미",
    "M7-01": "기억 카드",
    "T1-03": "마지막 시기에 곁에 있던 가족",
}
# M6-08 (2026-10-21 개정 신설, 문항분류.md 에 없음): "<앞말>: <떠오르는 것> - <까닭>" 기억 하나
# 기억은 1인칭으로 읽혀 '그분' 표현 없음 (BE app/services/intake.py M6_08_LEAD, M6_08_TAG 와 동일)
M6_08_LEAD = "가족이 나를 떠올리면 생각나는 것"
M6_08_TAG = "떠오르는 것"
SCOPES = ("자유", "물으면만", "회상 참고만")


def _scope(a: Answer) -> str:
    for c in a.checked:
        for s in SCOPES:
            if c.startswith(s):
                return s
    return "자유"


def _choices(answers: dict[str, Answer], qid: str) -> list[str]:
    """고른 선지만 (하위질문 서술 답은 기억 쪽)"""
    a = answers.get(qid)
    if not a or a.skipped:
        return []
    out = list(a.checked)
    # "직접 입력 -> 값" 은 선지 자리라 값도 특성으로 취급
    for i, c in enumerate(out):
        m = re.match(r"직접 입력\s*→\s*(.+)$", c)
        if m:
            out[i] = m.group(1).strip()
    return [c for c in out if c and not c.startswith("직접 입력")]


# M5 문항 답 아래 "시기, 감정" 줄: 기억 본문이 아닌 속성이라 period, emotion 으로
META_HINT = re.compile(r"^(19|20)\d\d|^(모름|평생|말년|고교|미래|—)$|^\d{4}[~년]")
EMOTIONS = {"기쁨", "그리움", "자부심", "웃김", "슬픔", "평온", "사랑", "걱정", "의지", "모름"}


def _split_meta(texts: list[str]) -> tuple[list[str], str | None, str | None]:
    body, period, emotion = [], None, None
    for text in texts:
        if text in EMOTIONS and len(text) <= 4:
            emotion = emotion or (None if text == "모름" else text)
        elif len(text) <= 12 and META_HINT.match(text):
            period = period or (None if text == "—" else text)
        else:
            body.append(text)
    return body, period, emotion


def _texts(a: Answer) -> list[str]:
    """답으로 볼 선지, 값 (사용 범위, 추정 표시 제외)"""
    out = [v for v in a.values if v != "__추정__"]
    out += [c for c in a.checked if not any(c.startswith(s) for s in SCOPES)]
    return [t for t in out if t]


def to_memories(answers: dict[str, Answer]) -> list[dict]:
    rows: list[dict] = []

    def add(
        qid: str,
        text: str,
        *,
        tag: str,
        a: Answer,
        period: str | None = None,
        emotion: str | None = None,
    ) -> None:
        text = text.strip()
        if not text:
            return
        rows.append(
            {
                "id": f"{qid}-{len([r for r in rows if r['sourceQuestion'] == qid]) + 1}",
                "memoryText": text,
                "tags": [tag],
                "importanceScore": 5,
                "relatedPeople": [],
                "confidence": "inferred" if "__추정__" in a.values else "confirmed",
                "sourceQuestion": qid,
                "period": period,
                "emotion": emotion,
                "usageScope": _scope(a),
            }
        )

    for qid, lead in MEMORY_LEAD.items():
        a = answers.get(qid)
        if not a or a.skipped or not a:
            continue
        period = None
        for c in a.checked:
            if re.match(r"^(19|20)\d\d", c):
                period = c
        texts = [v for v in a.values if v != "__추정__"] if qid in VALUES_ONLY else _texts(a)
        if qid in VALUES_ONLY:  # 선지는 프롬프트 쪽이라 서술만
            chosen = set(_choices(answers, qid))
            texts = [t for t in texts if t not in chosen]
        texts, found_period, emotion = _split_meta(texts)
        for text in texts:
            body = text if len(text) > 30 else f"{lead}: {text}"
            add(qid, body, tag=lead, a=a, period=period or found_period, emotion=emotion)

    card = answers.get("M7-01")
    if card and card.rows:
        for cells in card.rows:
            cells = cells + [""] * 7
            title, body, people, when, feel, inferred, scope = cells[:7]
            text = f"{title}: {body}" if title and body else (body or title)
            if not text:
                continue
            rows.append(
                {
                    "id": f"M7-01-{len([r for r in rows if r['sourceQuestion'] == 'M7-01']) + 1}",
                    "memoryText": text,
                    "tags": ["기억 카드"],
                    "importanceScore": 5,
                    "relatedPeople": [p for p in [people] if p],
                    "confidence": "inferred" if inferred == "■" else "confirmed",
                    "sourceQuestion": "M7-01",
                    "period": when or None,
                    "emotion": feel or None,
                    "usageScope": scope if scope in SCOPES else "자유",
                }
            )

    thing = answers.get("M6-08")
    if thing and not thing.skipped:
        pairs = [
            (_plain(c[0]) if c else None, _plain(c[1]) if len(c) > 1 else None) for c in thing.rows
        ]
        if not thing.rows:  # 빈칸 답이면 첫 값이 떠오르는 것, 둘째가 까닭
            values = [v for v in thing.values if v != "__추정__"]
            pairs = [(values[0] if values else None, values[1] if len(values) > 1 else None)]
        for what, why in pairs:
            if what:
                add(
                    "M6-08",
                    f"{M6_08_LEAD}: {what}" + (f" — {why}" if why else ""),
                    tag=M6_08_TAG,
                    a=thing,
                )

    for qid, tag in MEMORY_TABLES.items():
        a = answers.get(qid)
        if qid == "M7-01" or not a or a.skipped or not a.rows:
            continue
        for cells in a.rows:
            cells = [UNKNOWN_CELL_MARK.sub("", c).strip() for c in cells]  # 모름 표시 제거
            parts = [c for c in cells if c and c not in ("□", "■") and not c.startswith("_")]
            if not parts:
                continue
            add(qid, " · ".join(parts), tag=tag, a=a)
    return rows


def to_intake(answers: dict[str, Answer], *, name: str | None = None) -> dict:
    def one(qid: str) -> str | None:
        a = answers.get(qid)
        if not a or a.skipped:
            return None
        return a.choice or a.value

    def many(qid: str) -> list[str]:
        a = answers.get(qid)
        return [] if not a or a.skipped else _texts(a)

    subject_name = name or one("P1-01") or "대상자"
    audience = one("P1-07")
    is_public = bool(audience and "공개" in audience)
    address = one("P1-07B") if is_public else one("P1-07A")

    family: list[dict] = []
    fam = answers.get("M2-01")
    if fam and not fam.skipped:
        for cells in fam.rows:
            cells = cells + [""] * (8 - len(cells))
            if not cells[0]:
                continue
            family.append(
                {
                    "name": cells[0],
                    "relation": cells[1] or None,
                    "addressTermTo": _plain(cells[2]),
                    "selfReference": _plain(cells[3]),
                    "addressTermFrom": _plain(cells[4]),
                    "tone": cells[5] or None,
                    "speechLevel": cells[6] or None,
                    "talksWithPersona": cells[7].startswith("예") if cells[7] else None,
                }
            )
    worries = answers.get("M2-02")
    if worries and worries.rows:
        by_name = {f["name"]: f for f in family}
        for cells in worries.rows:
            if len(cells) >= 2 and cells[0] in by_name:
                by_name[cells[0]].setdefault("worries", []).append(cells[1])

    examples: list[dict] = []
    for qid in ("C-09", "M4-04", "M4-06"):
        a = answers.get(qid)
        if not a or a.skipped:
            continue
        for cells in a.rows:
            cells = cells + [""] * 4
            if not cells[0]:
                continue
            examples.append(
                {
                    "text": cells[0],
                    "situation": cells[1] or None,
                    "listener": cells[2] or None,
                    "inferred": cells[3] == "■",
                    "sourceQuestion": qid,
                }
            )
        if not a.rows:
            for text in _texts(a):
                examples.append({"text": text, "sourceQuestion": qid, "inferred": False})

    taboo: list[dict] = []
    for topic in many("C-10"):
        taboo.append({"topic": topic, "grade": "금지", "sourceQuestion": "C-10"})
    grades = answers.get("M9-01")
    if grades and grades.rows:
        for cells in grades.rows:
            cells = cells + [""] * 3
            topic, grade = _plain(cells[0]), _plain(cells[1])
            if topic and grade:
                taboo.append(
                    {
                        "topic": topic,
                        "grade": grade,
                        "note": _plain(cells[2]),
                        "sourceQuestion": "M9-01",
                    }
                )
    hide = answers.get("M9-03")
    if hide and hide.rows:
        for cells in hide.rows:
            topic = _plain(cells[0]) if cells else None
            if topic:
                taboo.append(
                    {
                        "topic": topic,
                        "grade": (_plain(cells[1]) if len(cells) > 1 else None) or "물으면만",
                        "sourceQuestion": "M9-03",
                    }
                )
    for topic in many("T2-02"):
        taboo.append({"topic": topic, "grade": "금지", "sourceQuestion": "T2-02"})

    situational: list[dict] = []
    for i in list(range(1, 12)):
        qid = f"M8-{i:02d}"
        a = answers.get(qid)
        if not a or a.skipped:
            continue
        if qid == "M8-11":
            for cells in a.rows:
                if cells and cells[0]:
                    situational.append(
                        {
                            "situation": f"먼저 말을 걸 때 ({cells[1] if len(cells) > 1 else '대화 중'})",
                            "response": cells[0],
                        }
                    )
            continue
        attitude = a.choice
        said = next((v for v in a.values if v != "__추정__"), None)
        if attitude or said:
            situational.append(
                {
                    "situation": a.question,
                    "response": said or attitude or "",
                    "avoid": None if said else None,
                }
            )

    return {
        "_comment": "Persona Intake 작성본에서 자동 변환 (pipeline.s0_intake).",
        "subjectContext": {
            "subject": {"name": subject_name, "addressTerm": address},
            "familyMembers": [
                {
                    "name": f["name"],
                    "relationToSubject": f.get("relation"),
                    "addressTerms": [t for t in (f.get("addressTermFrom"),) if t],
                }
                for f in family
            ],
            "glossaryTerms": sorted({f["name"] for f in family if f["name"]}),
        },
        "intakeContext": {
            "basicProfile": {
                "status": "생존" if (one("P1-04") or "").startswith("예") else "사망",
                "relation": one("P1-02"),
                "period": one("P1-05"),
                "oneLine": one("C-05"),
                "introduction": one("C-06"),
                "deathContext": one("T1-02"),
            },
            "audience": audience,
            "visitorTone": one("P1-07C"),
            "visitorContext": one("P1-07D"),
            "speechStyle": {
                "length": one("C-01"),
                "manner": one("C-02"),
                "whenAngry": one("M3-03"),
                "interjections": [t for t in re.split(r"\s*/\s*", one("M3-04") or "") if t],
                "dialect": one("M3-05"),
                "dialectExample": (
                    answers.get("M3-05").values[0]
                    if answers.get("M3-05") and answers["M3-05"].values
                    else None
                ),
                "avoidStyles": many("M3-06"),
                "sentenceEndings": [
                    c[0] for c in (answers.get("M3-07").rows if answers.get("M3-07") else [])
                ],
                "callOpen": one("M3-01"),
                "callClose": one("M3-02"),
            },
            "speechExamples": examples,
            "personality": {
                "oneLine": one("C-05"),
                "introduction": one("C-06"),
                "traits": _choices(answers, "M4-01"),
                "values": many("M4-02"),
                "affectionStyle": many("C-07"),
                "comfortStyle": one("C-03"),
                "adviceStyle": many("C-04"),
                "conflictStyle": one("M4-05"),
                "rememberedAs": one("T2-03"),
                "wishListenerFeels": one("T2-04"),
            },
            "familyMap": family,
            "situationalReactions": situational,
            "tabooTopics": taboo,
            "sttHints": {"names": [], "voiceSampleRef": None},
        },
    }


def _plain(cell: str) -> str | None:
    # 모름 계열 표시 제거 (체크했으면 모른다는 뜻이라 빈칸과 같음)
    cell = UNKNOWN_CELL_MARK.sub("", cell).strip()
    # 직접 입력 행 표시 제거 (양식 메모라 프롬프트 유입 방지)
    cell = re.sub(r"^\(?직접\s*(입력|적기)\)?\s*(\(0~\d행\))?\s*", "", cell)
    cell = cell.strip("_ ").strip()
    return cell or None


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="작성본 md → persona_intake.json + 기억 조각")
    ap.add_argument("--subject", required=True)
    ap.add_argument("--input-root", default="input")
    ap.add_argument("--out-root", default="output")
    ap.add_argument("--subject-out", help="산출물 폴더명 (기본 <대상자>_manual)")
    ap.add_argument("--write", action="store_true", help="파일로 저장 (없으면 요약만)")
    args = ap.parse_args()
    args.subject_out = args.subject_out or f"{args.subject}_manual"

    base = Path(args.input_root) / args.subject
    sheet = next(base.glob("Persona_Intake_*.md"))
    answers = parse(sheet)
    intake = to_intake(answers, name=args.subject)
    memories = to_memories(answers)

    ic = intake["intakeContext"]
    print(
        f"{sheet.name}\n"
        f"  말투 칸 {sum(1 for v in ic['speechStyle'].values() if v)}개 채움\n"
        f"  발화 예시 {len(ic['speechExamples'])}건\n"
        f"  성격 칸 {sum(1 for v in ic['personality'].values() if v)}개 채움\n"
        f"  가족 {len(ic['familyMap'])}명 / 상황 {len(ic['situationalReactions'])}건 / "
        f"금기 {len(ic['tabooTopics'])}건\n"
        f"  기억 {len(memories)}건"
    )
    if args.write:
        out = Path(args.out_root) / args.subject_out / "s2_extraction"
        out.mkdir(parents=True, exist_ok=True)
        (out / "b_intake_prompt.json").write_text(
            json.dumps(intake, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out / "b_intake_memory.json").write_text(
            json.dumps(memories, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n저장: {out}/b_intake_prompt.json, {out}/b_intake_memory.json")
