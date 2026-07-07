"""전사 보정 오프라인 랩 (백로그 3, 명세 ANL-005).

캔어리는 실녹음에서 관찰된 오전사 사례의 고정 스냅샷이다 — 저장된 전사
결과에 앵커하지 않으므로 전사를 재생성해도 깨지지 않는다. 용어는 하드코딩
없이 컨텍스트 픽스처(BE가 보낼 JSON 모양)에서 자동 파생한다.

Usage:
    python -m evaluation.correction_lab [model ...]   # default: .env 설정
"""

import sys

from app.core.config import load_settings
from app.schemas.transcript import TranscriptSegment
from app.services.correction import correct_segments
from app.services.glossary import build_glossary
from evaluation.common import REPO_ROOT, load_context_fixture


def segment(index: int, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        segment_index=index,
        start_ms=index * 1000,
        end_ms=(index + 1) * 1000,
        speaker_label="SPK_0",
        transcript_text=text,
    )


# 실녹음 관찰 사례의 고정 스냅샷: (설명, 원문, 기대 — "교정:단어" 또는 "불변")
CANARIES = [
    ("이름 오전사 (10번 관찰)", "어, 영아.", "교정:향아"),
    ("이름 오전사 — 일반명사와 동형이라 애매 (10번 재생성 관찰)", "어, 상아.", "교정또는리뷰:향아"),
    ("사투리 — 이름으로 바꾸면 안 됨 (7번 관찰)", "구려유. 할머니도 잘 자.", "불변"),
]


def run(model: str | None) -> bool:
    settings = load_settings(REPO_ROOT / ".env")
    if model:
        settings = settings.model_copy(update={"openai_correction_model": model})
    print(f"모델: {settings.openai_correction_model}")

    subject, intake = load_context_fixture()
    glossary = build_glossary(subject, intake)

    segments = [segment(i, text) for i, (_, text, _) in enumerate(CANARIES)]
    correct_segments(segments, glossary=glossary, settings=settings)

    all_ok = True
    for (label, original, expectation), seg in zip(CANARIES, segments):
        if expectation == "불변":
            ok = seg.corrected_text is None
            got = "불변 유지" if ok else f"오교정→{seg.corrected_text}"
        else:
            kind, want = expectation.split(":")
            corrected_ok = seg.corrected_text is not None and want in seg.corrected_text
            # "교정또는리뷰" = 애매한 케이스: 교정 또는 검수 표시 둘 다 안전 (오교정·무반응만 실패)
            review_ok = kind == "교정또는리뷰" and seg.corrected_text is None and seg.needs_review
            ok = corrected_ok or review_ok
            got = seg.corrected_text or ("리뷰 표시(검수행)" if seg.needs_review else "미교정")
        all_ok &= ok
        print(f"  {'✅' if ok else '❌'} [{label}] '{original}' → {got}")
    return all_ok


if __name__ == "__main__":
    models = sys.argv[1:] or [None]
    ok = all(run(model) for model in models)
    raise SystemExit(0 if ok else 1)
