"""3-A 기억 조각 R2 채점 — 근거 뒷받침(faithfulness) 판정.

방법론: RAGAS faithfulness (https://github.com/explodinggradients/ragas) 방식을
그대로 구현한다 — ① 기억 문장을 검증 가능한 최소 주장(claim)으로 분해하고
② 각 주장을 인용된 원문 근거 구간과 대조해 지지/부분지지/불지지로 개별 판정.
통짜 문장 판정이 놓치는 "대부분 맞고 하나 틀린" 케이스를 잡기 위함.

라이브러리를 직접 쓰지 않는 이유: langchain 의존성이 무겁고, 우리 판정 체계
(지지/부분/불지지 3단, 노션 합격선 지지≥95%·불지지 0)와 출력 형식이 달라서
방식만 차용한다. judge 모델은 채점 대상(분석 모델)보다 강한 모델을 쓴다.

노션 평가 계획 R2: memoryText 내용이 원문 구간에서 확인되는가.

Usage:
    python -m evaluation.memory_segments.faithfulness [--only ...] [--source aihub경로]
"""

import argparse
import json

from openai import OpenAI

from app.core.config import load_settings
from evaluation.common import REPO_ROOT, RESULTS_DIR, load_segments, result_stems

MEMORY_DIR = RESULTS_DIR / "memory"
# 근거 창 기본값: 인용 세그먼트 앞뒤로 이만큼 더 본다 (인용 불완전 보정).
# --window 0 으로 좁히면 '인용이 완전한가'를 검증할 수 있다
DEFAULT_WINDOW = 8

JUDGE_SYSTEM_PROMPT = """너는 기억 문장이 통화 원문과 확정 정보에 의해 뒷받침되는지 판정하는 채점기다.

주어지는 것: ① 근거 원문(화자 라벨 포함) ② 확정 정보(대상자가 누구인지, 등장 인물의 관계 — 화자식별·설문으로 이미 확정된 사실).

절차:
1. 기억 문장을 검증 가능한 최소 주장(claim)들로 분해한다. 예: "신금자는 병원에서 주사를 맞고 85만원을 냈다" → ["신금자가 병원에서 주사를 맞았다", "비용이 85만원이었다"].
2. 각 주장을 판정한다:
   - 발화 내용에 관한 주장: 근거 원문에서 확인되면 supported.
   - "누가 말했다/누구의 행동이다"에 관한 주장: 근거 원문의 화자 라벨 + 확정 정보로 판단한다 (예: 원문이 SPK_0 발화이고 확정 정보가 'SPK_0=신금자'이면 "신금자가 말했다"는 supported).
   - 인물 관계 주장(예: 'A는 B의 배우자'): 확정 정보에 있으면 supported.
   verdict: supported / partial(일부만 확인) / unsupported(원문·확정정보 어디에도 없음).
3. 한 발화에서 나온 여러 표현(예: "딱 좋다"+"잘 먹겠다"+"감사")은 하나의 주장으로 묶는다. 사소한 부속 표현을 별도 주장으로 잘게 쪼개지 않는다.
4. 비대상자 화자의 발화 내용을 그 통화 상대방(확정 정보에 명시된 인물)의 것으로 귀속하는 것은 supported로 본다.
5. 외부 지식·추측은 쓰지 않는다. 주어진 원문과 확정 정보만 근거로 삼는다.

다음 JSON으로만 답한다:
{"claims": [{"claim": "주장", "verdict": "supported|partial|unsupported", "evidence": "근거"}]}"""


def build_judge_prompt(memory_text: str, source_lines: list[str], context: str) -> str:
    sources = "\n".join(source_lines)
    return (
        f"확정 정보:\n{context}\n\n"
        f"근거 원문 (인용된 세그먼트, [화자] 포함):\n{sources}\n\n"
        f"기억 문장:\n{memory_text}"
    )


def build_context(stem) -> str:
    from evaluation.common import RESULTS_DIR, load_context_fixture
    from evaluation.common import load_segments as _load
    from evaluation.speaker_id.lab import gold_speaker_texts, subject_label_by_text

    lines = []
    subject, _ = load_context_fixture()
    subject_name = subject.subject.name if subject.subject else "대상자"
    segments = _load(stem)
    label = subject_label_by_text(segments, gold_speaker_texts(stem))
    other_labels = sorted({s.speaker_label for s in segments} - {label})
    if label:
        lines.append(f"- 대상자 본인 = {subject_name} = 전사문의 {label} 화자 (셋이 같은 사람)")
    else:
        lines.append(f"- 대상자 본인 = {subject_name}")
    persons_path = RESULTS_DIR / "analysis" / f"{stem}.persons.json"
    persons = []
    if persons_path.exists():
        persons = json.loads(persons_path.read_text(encoding="utf-8")).get("persons", [])
        for p in persons:
            rel = p.get("relationToSubject") or "관계 미상"
            lines.append(f"- {p['name']}: 대상자의 {rel} (전사문 지칭: {', '.join(p.get('mentions', []))})")
    # 통화 상대 확정: 대상자가 전사문에서 어떤 인물을 이름/호칭으로 직접 부르면
    # (그 지칭이 대상자 발화에 등장), 그 인물이 통화 상대다. 없으면 미확정.
    subject_texts = " ".join(
        (seg.corrected_text or seg.transcript_text)
        for seg in segments if seg.speaker_label == label
    )
    addressed = None
    for person in persons:
        if any(m and m in subject_texts for m in person.get("mentions", [])):
            addressed = person["name"]
            break
    if len(other_labels) == 1:
        if addressed:
            lines.append(f"- {other_labels[0]} 화자 = 통화 상대방 = {addressed} (대상자가 전사문에서 직접 부름)")
        else:
            lines.append(f"- {other_labels[0]} 화자 = 통화 상대방(대상자가 아닌 발화의 주체, 이름 미확정)")
    return "\n".join(lines) or "(추가 확정 정보 없음)"


def score_file(client, model, stem, window):
    memory = json.loads((MEMORY_DIR / f"{stem}.memory.json").read_text(encoding="utf-8"))
    by_index = {s.segment_index: s for s in load_segments(stem)}
    context = build_context(stem)

    claims_total = {"supported": 0, "partial": 0, "unsupported": 0}
    unsupported_examples = []
    ordered = sorted(by_index)
    for seg in memory["memorySegments"]:
        # faithfulness는 "내용이 통화에 실제로 있나"를 본다 — 인용 정확도가
        # 아니라. 인용 세그먼트 ± WINDOW 범위를 근거로 주어, 인용이 좁아도
        # 내용이 대화에 있으면 지지로 판정되게 한다
        cited = [i for i in seg["sourceSegmentIds"] if i in by_index]
        lo, hi = min(cited), max(cited)
        window_ids = [i for i in ordered if lo - window <= i <= hi + window]
        source_lines = [
            f"[{by_index[i].speaker_label}] {by_index[i].corrected_text or by_index[i].transcript_text}"
            for i in window_ids
        ]
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": build_judge_prompt(seg["memoryText"], source_lines, context)},
            ],
        )
        claims = json.loads(response.choices[0].message.content or "{}").get("claims", [])
        for claim in claims:
            verdict = claim.get("verdict")
            if verdict in claims_total:
                claims_total[verdict] += 1
            if verdict == "unsupported":
                unsupported_examples.append((seg["memoryText"], claim.get("claim", "")))
    return claims_total, unsupported_examples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", type=str, default=None)
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    args = parser.parse_args()

    settings = load_settings(REPO_ROOT / ".env")
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value())
    model = settings.openai_judge_model
    print(f"judge 모델: {model}")

    stems = [s for s in result_stems() if (MEMORY_DIR / f"{s}.memory.json").exists()]
    if args.only:
        wanted = {n.strip() for n in args.only.split(",")}
        stems = [s for s in stems if s in wanted]

    grand = {"supported": 0, "partial": 0, "unsupported": 0}
    all_unsupported = []
    for stem in stems:
        totals, unsupported = score_file(client, model, stem, args.window)
        for k in grand:
            grand[k] += totals[k]
        all_unsupported.extend(unsupported)
        n = sum(totals.values())
        rate = totals["supported"] / n if n else 0
        print(f"  {stem}: 주장 {n}개, 지지 {rate:.0%}, 불지지 {totals['unsupported']}")

    total = sum(grand.values())
    support_rate = grand["supported"] / total if total else 0
    print(f"\n[R2 근거 뒷받침] 총 주장 {total}개")
    print(f"  지지 {support_rate:.1%} (합격선 ≥95%) / 부분 {grand['partial']} / 불지지 {grand['unsupported']} (합격선 0)")
    verdict = "합격" if support_rate >= 0.95 and grand["unsupported"] == 0 else "미달"
    print(f"  판정: {verdict}")
    if all_unsupported:
        print("\n불지지 주장:")
        for memory_text, claim in all_unsupported[:15]:
            print(f"  · [{claim}] ← {memory_text[:45]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
