"""전사에서 뽑은 것들을 프롬프트 재료 한 장으로 합친다.

a(인물)·b(민감도)·c(말투) 는 통화별로 따로 나오고 모양도 제각각이다.
그걸 설문(b_intake_prompt.json) 과 같은 칸 이름으로 옮겨 담아, s3 가 두 장만
읽으면 되게 한다. 설문에 없는 칸을 전사가 채우는 식이다.

칸 이름은 infra/schema/subject.py 의 IntakeContext 를 따른다.
"""

from collections import Counter

# 감탄사로 볼 만한 짧은 말. 이보다 길면 문장에 가까워 말투 요약으로 보낸다.
INTERJECTION_MAX = 6
# 몇 번 이상 나와야 습관으로 볼지
INTERJECTION_MIN_COUNT = 3
# 민감도 종류를 금기 주제 이름으로
TABOO_TOPIC = {
    "health": "건강·병",
    "asset": "재산·돈",
    "conflict": "가족 갈등",
    "death": "죽음",
    "privacy": "사생활",
}


def _family_map(persons_by_recording: dict[str, dict], address_terms: list[dict]) -> list[dict]:
    """인물 분석 + 말투의 호칭을 합쳐 가족 표를 만든다."""
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
    """민감도 플래그를 금기 후보로. 등급은 사람이 정하기 전까지 '물으면만'."""
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
    """되풀이하는 말 중 짧은 것은 감탄사, 문장 특징은 요약 한 줄로."""
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
    """전사에서 나온 프롬프트 재료를 설문과 같은 칸 이름으로 담는다."""
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
