"""G3 통화 전사 → 말투 재료. 통계만 쓰고 LLM 은 부르지 않는다(형태소 분석 kiwipiepy, 없으면 카드 생략).

- 말투 카드: 대상자 발화의 문장 길이·종결어미 분포·존댓말 비율·묻는 말 비율·감탄사·챙김/'사랑' 빈도.
  실제 말투와의 어미 분포 거리를 줄이는 데 효과가 가장 컸던 재료다.
- 실제 주고받기: 가족 말 → 바로 뒤 대상자 답 쌍. 통화별로 돌아가며 고르게, 시드 고정(다시 돌려도 같다).
  건강·의료·돈·죽음 같은 말이 든 쌍은 뺀다(사실이 프롬프트에 상시로 새지 않게).
"""
from __future__ import annotations

import math
import random
import re
from collections import Counter

from pipeline.persona_prompt.spec import Shot

INTERJ = ["아이고", "아이구", "그래", "그럼", "응", "어", "아", "야", "글쎄", "에휴", "참", "뭐", "그니까", "그러니까",
          "그래서", "근데", "인제", "이제", "그냥", "막", "좀", "많이"]
HONORIFIC = re.compile(r"(요|습니다|습니까|세요|셔요|십시오)[.?!~]*$")
CARE = re.compile(r"밥\s*(은|잘|챙|먹|꼭|거르)|챙겨\s*먹|끼니|건강\s*(해|하|챙|조심)|몸\s*(조심|챙|상하|잘)|조심\s*해|감기\s*조심")
LOVE = re.compile(r"사랑")
# 주고받기에서 뺄 말: 건강·의료·돈 같은 사실이 프롬프트에 새면 기억 정책(물을 때만)·사인 정책을 우회한다
SENSITIVE = re.compile(r"주사|병원|약(을|은|이|국| 먹)|당뇨|혈당|수술|아파|아프|입원|응급|상속|재산|주소|장례|돌아가")
_kiwi = None


def kiwi():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
    return _kiwi


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.?!~])\s+|\n", text) if len(s.strip()) >= 2]


def ending(sent: str) -> str:
    toks = [t for t in kiwi().tokenize(sent) if not t.tag.startswith("S")]
    efs = [t for t in toks if t.tag == "EF"]
    if efs:
        return efs[-1].form
    return f"({toks[-1].tag})" if toks else ""


def features(utts: list[str]) -> dict:
    sents = [s for u in utts for s in sentences(u)]
    n = max(1, len(sents))
    ends = Counter(ending(s) for s in sents)
    interj: Counter = Counter()
    for s in sents:
        for w in INTERJ:
            if re.search(rf"(^|\s){w}(\s|[,.!?~]|$)", s):
                interj[w] += 1
    return {
        "n_utts": len(utts), "n_sents": len(sents),
        "chars_per_sent_mean": round(sum(map(len, sents)) / n, 2),
        "chars_per_utt_mean": round(sum(map(len, utts)) / max(1, len(utts)), 2),
        "honorific_share": round(sum(1 for s in sents if HONORIFIC.search(s)) / n, 3),
        "question_share": round(sum(1 for s in sents if s.endswith("?")) / n, 3),
        "care_share": round(sum(1 for u in utts if CARE.search(u)) / max(1, len(utts)), 3),
        "love_share": round(sum(1 for u in utts if LOVE.search(u)) / max(1, len(utts)), 3),
        "ending_dist": {k: round(v / n, 4) for k, v in ends.most_common(25)},
        "interj_per_sent": {k: round(v / n, 4) for k, v in interj.most_common(12)},
    }


def js_distance(p: dict, q: dict) -> float:
    keys = set(p) | set(q)
    sp, sq = sum(p.values()) or 1, sum(q.values()) or 1
    P = {k: p.get(k, 0) / sp for k in keys}
    Q = {k: q.get(k, 0) / sq for k in keys}
    M = {k: (P[k] + Q[k]) / 2 for k in keys}
    kl = lambda A: sum(A[k] * math.log2(A[k] / M[k]) for k in keys if A[k] > 0)  # noqa: E731
    return round((kl(P) + kl(Q)) / 2, 4)


def card(f: dict) -> str:
    ed = f["ending_dist"]
    ends = ", ".join(f"-{k}" for k in [k for k in ed if not k.startswith("(")][:8])
    frag_ic, frag_ec = ed.get("(IC)", 0), ed.get("(EC)", 0)
    interj = ", ".join(list(f["interj_per_sent"])[:6])
    care = f["care_share"] * 100
    lines = [
        "# 말투 카드 (실제 통화 전사에서 자동으로 잰 것)",
        f"- 한 문장 평균 {f['chars_per_sent_mean']:.0f}자 안팎으로 짧게 말한다. 한 번 말할 때 {f['chars_per_utt_mean']:.0f}자 정도.",
        f"- 존댓말 문장 비율 {f['honorific_share'] * 100:.0f}%.",
        f"- 자주 쓰는 문장 끝: {ends}",
        f"- 문장을 다 맺지 않는 말이 많다: 감탄사로 끝나는 말 {frag_ic * 100:.0f}%(\"어.\", \"응.\"), "
        f"연결어미로 끊기는 말 {frag_ec * 100:.0f}%(\"…하고.\", \"…해서.\").",
        f"- 자주 쓰는 감탄·이음말: {interj}",
        f"- 묻는 말 비율 {f['question_share'] * 100:.0f}%.",
        f"- 밥·건강 챙기는 말은 실제로 말 {max(1, round(100 / care)) if care else '여러'} 마디에 한 번 꼴이다({care:.0f}%). 매 답마다 챙기지 않는다.",
    ]
    if f["love_share"] < 0.02:
        lines.append("- \"사랑한다\"는 실제 통화에서 거의 하지 않는다(통화를 끝낼 때 정도).")
    return "\n".join(lines)


def card_from_utterances(utts: list[str], min_utts: int = 30) -> str | None:
    """대상자 발화로 카드. 발화가 너무 적거나 형태소 분석기가 없으면 None."""
    utts = [u for u in utts if len(u.strip()) >= 2]
    if len(utts) < min_utts:
        return None
    try:
        return card(features(utts))
    except ImportError:
        return None


def _text(seg) -> str:
    return (getattr(seg, "corrected_text", None) or seg.transcript_text or "").strip()


def subject_utterances(segments_by_recording: dict, labels: dict) -> list[str]:
    out = []
    for rec, segs in segments_by_recording.items():
        lab = labels.get(rec)
        if lab:
            out += [_text(s) for s in segs if s.speaker_label == lab and len(_text(s)) >= 4]
    return out


def shots_from_segments(segments_by_recording: dict, labels: dict, *, max_shots: int = 30, seed: int = 0,
                        user_len=(4, 60), persona_len=(4, 50), exclude=None) -> list[Shot]:
    """가족 말 → 바로 뒤 대상자 답. 대상자 화자 라벨이 없는 통화는 건너뛴다.
    exclude(통화, 세그먼트 번호) -> bool: 평가 문항과 겹치는 구간을 빼는 데 쓴다(답 베끼기 방지)."""
    by_rec: dict[str, list[Shot]] = {}
    for rec, segs in sorted(segments_by_recording.items()):
        lab = labels.get(rec)
        if not lab:
            continue
        segs = sorted(segs, key=lambda s: s.segment_index)
        for prev, cur in zip(segs, segs[1:]):
            if prev.speaker_label == lab or cur.speaker_label != lab:
                continue
            u, p = _text(prev), _text(cur)
            if exclude is not None and (exclude(rec, prev.segment_index) or exclude(rec, cur.segment_index)):
                continue
            if SENSITIVE.search(u) or SENSITIVE.search(p):
                continue
            if user_len[0] <= len(u) <= user_len[1] and persona_len[0] <= len(p) <= persona_len[1]:
                by_rec.setdefault(rec, []).append(Shot(user=u, persona=p, source=f"call:{rec}#{cur.segment_index}"))
    rng = random.Random(seed)
    for v in by_rec.values():
        rng.shuffle(v)
    picked: list[Shot] = []
    seen: set[str] = set()
    while len(picked) < max_shots and any(by_rec.values()):
        for rec in sorted(by_rec):
            if by_rec[rec] and len(picked) < max_shots:
                sh = by_rec[rec].pop()
                if sh.persona not in seen:
                    picked.append(sh)
                    seen.add(sh.persona)
    return picked


def style_from_segments(segments_by_recording: dict, labels: dict, *, max_shots: int = 30,
                        exclude=None) -> tuple[str | None, list[Shot]]:
    return (card_from_utterances(subject_utterances(segments_by_recording, labels)),
            shots_from_segments(segments_by_recording, labels, max_shots=max_shots, exclude=exclude))
