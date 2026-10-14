"""말투 특징 추출·비교 모듈 (LLM 없음). 입출력 계약:
  features(utterances: list[str]) -> dict   어미 분포·반말 비율·문장 길이·감탄사·자주 쓰는 말
  distance(f_real, f_gen) -> dict           JS 거리(어미 분포), 길이 비, 반말 비율 차, 감탄사 겹침
  card(f) -> str                            프롬프트에 넣을 '말투 카드'(결정적 생성, 재실행해도 같은 결과)
"""
import math, re
from collections import Counter

_kiwi = None
def kiwi():
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
    return _kiwi

INTERJ = ["아이고", "아이구", "그래", "그럼", "응", "어", "아", "야", "글쎄", "에휴", "참", "뭐", "그니까", "그러니까", "그래서", "근데", "인제", "이제", "그냥", "막", "좀", "많이"]
HONORIFIC = re.compile(r"(요|습니다|습니까|세요|셔요|십시오)[.?!~]*$")


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.?!~])\s+|\n", text) if len(s.strip()) >= 2]


def ending(sent):
    """문장 끝 어미(EF) 형태. 없으면 마지막 형태소."""
    toks = [t for t in kiwi().tokenize(sent) if not t.tag.startswith("S")]  # 문장부호·기호 제외
    efs = [t for t in toks if t.tag == "EF"]
    if efs:
        return efs[-1].form
    return f"({toks[-1].tag})" if toks else ""  # 어미 없이 끝난 조각 말은 품사로 묶는다


def features(utts):
    sents = [s for u in utts for s in sentences(u)]
    ends = Counter(ending(s) for s in sents)
    words = Counter()
    for s in sents:
        for t in kiwi().tokenize(s):
            if t.tag in ("NNG", "VV", "VA", "MAG", "IC") and len(t.form) >= 1:
                words[f"{t.form}/{t.tag}"] += 1
    interj = Counter()
    for s in sents:
        for w in INTERJ:
            if re.search(rf"(^|\s){w}(\s|[,.!?~]|$)", s):
                interj[w] += 1
    n = max(1, len(sents))
    return {
        "n_utts": len(utts), "n_sents": len(sents),
        "chars_per_sent_mean": round(sum(map(len, sents)) / n, 2),
        "chars_per_utt_mean": round(sum(map(len, utts)) / max(1, len(utts)), 2),
        "honorific_share": round(sum(1 for s in sents if HONORIFIC.search(s)) / n, 3),
        "question_share": round(sum(1 for s in sents if s.endswith("?")) / n, 3),
        "ending_dist": {k: round(v / n, 4) for k, v in ends.most_common(25)},
        "interj_per_sent": {k: round(v / n, 4) for k, v in interj.most_common(12)},
        "top_words": [w for w, _ in words.most_common(30)],
    }


def _js(p, q):
    keys = set(p) | set(q)
    P = {k: p.get(k, 0) for k in keys}; Q = {k: q.get(k, 0) for k in keys}
    sp, sq = sum(P.values()) or 1, sum(Q.values()) or 1
    P = {k: v / sp for k, v in P.items()}; Q = {k: v / sq for k, v in Q.items()}
    M = {k: (P[k] + Q[k]) / 2 for k in keys}
    kl = lambda A: sum(A[k] * math.log2(A[k] / M[k]) for k in keys if A[k] > 0)
    return round((kl(P) + kl(Q)) / 2, 4)


def distance(real, gen):
    ri = set(real["interj_per_sent"]); gi = set(gen["interj_per_sent"])
    return {
        "ending_js": _js(real["ending_dist"], gen["ending_dist"]),
        "len_ratio(gen/real, 문장)": round(gen["chars_per_sent_mean"] / max(1e-9, real["chars_per_sent_mean"]), 3),
        "honorific_gap": round(gen["honorific_share"] - real["honorific_share"], 3),
        "question_gap": round(gen["question_share"] - real["question_share"], 3),
        "interj_jaccard": round(len(ri & gi) / max(1, len(ri | gi)), 3),
    }


def card(f, examples=None):
    ed = f["ending_dist"]
    ends = ", ".join(f"-{k}" for k in [k for k in ed if not k.startswith("(")][:8])
    frag_ic, frag_ec = ed.get("(IC)", 0), ed.get("(EC)", 0)
    interj = ", ".join(list(f["interj_per_sent"])[:6])
    lines = [
        "# 말투 카드 (실제 통화 전사에서 자동 추출)",
        f"- 한 문장 평균 {f['chars_per_sent_mean']:.0f}자 안팎으로 짧게 말한다. 한 번에 {f['chars_per_utt_mean']:.0f}자 정도.",
        f"- 존댓말 비율 {f['honorific_share']*100:.0f}%: 손주·자녀에게는 반말이 기본이다.",
        f"- 자주 쓰는 문장 끝: {ends}",
        f"- 문장을 다 맺지 않는 말이 많다: 감탄사로 끝나는 말 {frag_ic*100:.0f}%(\"어.\", \"응.\"), 연결어미로 끊기는 말 {frag_ec*100:.0f}%(\"…하고.\", \"…해서.\").",
        f"- 자주 쓰는 감탄·이음말: {interj}",
        f"- 묻는 말 비율 {f['question_share']*100:.0f}%: 상대에게 자주 되묻는다.",
    ]
    if examples:
        lines.append("- 실제 말 예시:")
        lines += [f"  - \"{e}\"" for e in examples]
    return "\n".join(lines)
