"""단계별 산출물 점검 — 외부 실오디오 한 케이스의 전 단계 출력을 통째로 덤프.

e2e_external은 요약치만 찍지만, 이 도구는 각 단계(전사·인물·민감·기억·요약)의
전체 출력을 보여줘 단계별 품질을 육안 점검하게 한다. 전사는 e2e 캐시를 공유해
재전사를 피한다.

Usage:
    python -m evaluation.inspect_stages --pair speakergs4913_speakergs4914 \
        --fixture evaluation/persona/fixtures/subject_context_seojeongsuk_gyeongsang.json
"""

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from app.core.config import load_settings
from app.pipeline.analysis.persons import run_persons_analysis
from app.pipeline.analysis.sensitivity import run_sensitivity_analysis
from app.pipeline.enrichment.service import (
    aggregate_tags,
    sentence_count,
    summarize_recording,
)
from app.pipeline.memory_segments.service import extract_memory_segments
from app.providers.stt.elevenlabs_scribe import ElevenLabsScribeProvider
from app.schemas.context import SubjectContext
from app.schemas.transcript import TranscriptSegment
from evaluation.e2e_external import CACHE_DIR, REPO_ROOT, find_clips


def load_or_transcribe(pair: str, clips: list[Path], no_cache: bool) -> list[TranscriptSegment]:
    key = hashlib.sha1("|".join(sorted(c.name for c in clips)).encode()).hexdigest()[:16]
    cache = CACHE_DIR / f"{pair}_{key}.json"
    if cache.exists() and not no_cache:
        print(f"[전사] 캐시 사용 {cache.name}")
        return [TranscriptSegment(**s) for s in json.loads(cache.read_text(encoding="utf-8"))]
    stt = ElevenLabsScribeProvider(api_key=os.environ["ELEVENLABS_API_KEY"])
    segs, offset = [], 0
    for clip in clips:
        for seg in stt.transcribe(clip, language="ko", speaker_diarization=True).segments:
            seg.segment_index += offset
            segs.append(seg)
        offset = len(segs)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps([s.model_dump(by_alias=True) for s in segs], ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[전사] 신규 전사·캐시 저장 {cache.name}")
    return segs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    settings = load_settings(REPO_ROOT / ".env")
    fx = json.loads(args.fixture.read_text(encoding="utf-8"))
    subject = SubjectContext(**fx["subjectContext"])
    name = subject.subject.name if subject.subject else "대상자"

    segs = load_or_transcribe(args.pair, find_clips(args.pair), args.no_cache)
    by_spk: dict[str, list] = {}
    for s in segs:
        by_spk.setdefault(s.speaker_label, []).append(s)
    subj_label = max(by_spk, key=lambda k: len(by_spk[k]))
    print(f"\n########## {name} ({args.pair}) ##########")
    print(f"화자 {list(by_spk)} · 대상자={subj_label}({len(by_spk[subj_label])}발화) · 총 {len(segs)}세그")

    print("\n===== 1) 전사 (대상자 발화 8개) =====")
    for s in by_spk[subj_label][:8]:
        print(f"  #{s.segment_index}: {s.transcript_text}")

    persons = run_persons_analysis(
        segs, subject_context=subject, subject_speaker_label=subj_label, settings=settings)
    sens = run_sensitivity_analysis(segs, settings=settings)
    print(f"\n===== 2) 인물 {len(persons['persons'])} · 민감 {len(sens['sensitivityFlags'])} =====")
    for p in persons["persons"]:
        print(f"  · {p['name']}({p.get('relationToSubject')}) 근거 {p.get('sourceSegmentIds')}")
    for f in sens["sensitivityFlags"]:
        print(f"  ! [{f['type']}] {f.get('description', '')[:70]}")

    mem = extract_memory_segments(
        segs, subject_context=subject, subject_speaker_label=subj_label,
        persons_result=persons, sensitivity_result=sens, settings=settings)
    print(f"\n===== 3-A) 기억 {len(mem['memorySegments'])}건 =====")
    for m in mem["memorySegments"]:
        print(f"  · [{','.join(m.get('tags', []))}|중{m.get('importanceScore')}] {m['memoryText']}")

    summary = summarize_recording(segs, settings=settings)
    print(f"\n===== 3-C) 요약({sentence_count(summary)}문장) =====\n  {summary}")
    print(f"  태그: {aggregate_tags(mem)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
