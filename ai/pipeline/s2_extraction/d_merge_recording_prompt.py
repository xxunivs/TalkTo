"""통화별 a(인물), b(민감도), c(말투) -> 설문(b_intake_prompt.json)과 같은 칸의 프롬프트 재료 한 장 (칸: app/schema/subject.py IntakeContext)"""

from collections import Counter

# 감탄사로 볼 최대 길이 (더 길면 말투 요약 쪽)
INTERJECTION_MAX = 6
# 습관으로 볼 최소 등장 횟수
INTERJECTION_MIN_COUNT = 3
# 민감도 종류 -> 금기 주제 이름
TABOO_TOPIC = {
    "health": "건강·병",
    "asset": "재산·돈",
    "conflict": "가족 갈등",
    "death": "죽음",
    "privacy": "사생활",
}


def _family_map(persons_by_recording: dict[str, dict], address_terms: list[dict]) -> list[dict]:
    """인물 분석 + 말투 호칭 -> 가족 표"""
    called: dict[str, str] = {}
    for row in address_terms:
        person, term = row.get("person"), row.get("term")
        if person and term:
            called.setdefault(person, term)

    out: dict[str, dict] = {}
    for result in persons_by_recording.values():
        for person in result.get("persons", []):
            name = person.get("name")
            if not name:
                continue
            note = out.setdefault(name, {"name": name})
            if person.get("relationToSubject"):
                note.setdefault("relation", person["relationToSubject"])
            mentions = person.get("mentions") or []
            term = called.get(name) or (mentions[0] if mentions else None)
            if term:
                note.setdefault("addressTermTo", term)
    return list(out.values())


def _taboo_topics(sensitivity_by_recording: dict[str, dict]) -> list[dict]:
    """민감도 플래그 -> 금기 후보 (사람이 정하기 전 등급은 '물으면만')"""
    out: dict[str, dict] = {}
    for result in sensitivity_by_recording.values():
        for flag in result.get("sensitivityFlags", []):
            kind = flag.get("type") or "기타"
            topic = TABOO_TOPIC.get(kind, kind)
            out.setdefault(
                topic,
                {
                    "topic": topic,
                    "grade": "물으면만",
                    "note": flag.get("description"),
                    "sourceQuestion": None,
                },
            )
    return list(out.values())


def _speech_style(style_by_recording: dict[str, dict]) -> dict:
    """반복어 중 짧은 것은 감탄사, 문장 특징은 요약 한 줄"""
    counts: Counter[str] = Counter()
    patterns: list[str] = []
    for style in style_by_recording.values():
        for row in style.get("recurringPhrases", []):
            phrase = (row.get("phrase") or "").strip()
            if phrase:
                counts[phrase] += len(row.get("sourceSegmentIds") or [1])
        for pattern in style.get("sentencePatterns", []):
            if pattern not in patterns:
                patterns.append(pattern)

    interjections = [
        phrase
        for phrase, n in counts.most_common()
        if len(phrase) <= INTERJECTION_MAX and n >= INTERJECTION_MIN_COUNT
    ]
    out: dict = {"interjections": interjections[:10]}
    if patterns:
        out["summary"] = " / ".join(patterns)
    return out


def _address_terms(style_by_recording: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for style in style_by_recording.values():
        rows.extend(style.get("addressTerms", []))
    return rows


def merge_recording_prompt(
    *,
    persons: dict[str, dict],
    sensitivity: dict[str, dict],
    linguistic_style: dict[str, dict],
    speech_examples: list[str],
) -> dict:
    """전사 프롬프트 재료 (설문과 같은 칸 이름)"""
    return {
        "familyMap": _family_map(persons, _address_terms(linguistic_style)),
        "speechStyle": _speech_style(linguistic_style),
        "speechExamples": [
            {
                "text": text,
                "situation": None,
                "listener": None,
                "inferred": False,
                "sourceQuestion": None,
            }
            for text in speech_examples
        ],
        "tabooTopics": _taboo_topics(sensitivity),
    }
