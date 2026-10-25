"""실녹음 전체를 파이프라인에 태워 페르소나를 빌드 — 진짜 자동화 흐름.

각 통화: 전사(캐시)→인물·민감 분석→3-A 기억 추출 → 전 통화 기억 누적. 누적 기억으로
reflection→통찰, + intake로 assembly(통찰·말투 few-shot 포함)→페르소나 프롬프트.
결과(누적 기억 + 프롬프트)를 저장해 chat.py가 그대로 대화에 쓴다.

Usage:
    python -m evaluation.persona.build_from_recordings \
        --audio-dir data/voice_raw --fixture data/fixtures/subject_context_singeumja.json \
        --out-dir data/built_singeumja
"""

import argparse
import hashlib
import json
from pathlib import Path

from app.core.config import load_settings
from app.pipeline.analysis.persons import run_persons_analysis
from app.pipeline.analysis.sensitivity import run_sensitivity_analysis
from app.pipeline.memory_segments.service import extract_memory_segments
from app.pipeline.persona.reflection import run_reflection
from app.pipeline.persona.service import assemble_persona_instructions
from app.providers.stt.elevenlabs_scribe import ElevenLabsScribeProvider
from app.schemas.context import IntakeContext, SubjectContext
from app.schemas.persona import PersonaAssemblyRequest
from app.schemas.reflection import ReflectionMemoryInput, ReflectionRequest
from app.schemas.transcript import TranscriptSegment


def _subject_label(segments: list[TranscriptSegment]) -> str | None:
    counts: dict[str, int] = {}
    for seg in segments:
        counts[seg.speaker_label] = counts.get(seg.speaker_label, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _transcribe_cached(stt, audio: Path, cache_dir: Path) -> list[TranscriptSegment]:
    key = hashlib.sha1(audio.name.encode()).hexdigest()[:16]
    cache = cache_dir / f"{key}.json"
    if cache.exists():
        return [TranscriptSegment(**s) for s in json.loads(cache.read_text(encoding="utf-8"))]
    segs = stt.transcribe(audio, language="ko", speaker_diarization=True).segments
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps([s.model_dump(by_alias=True) for s in segs], ensure_ascii=False),
        encoding="utf-8",
    )
    return segs


def main() -> int:
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, default=None,
                        help="폴더 내 *.wav 전부 사용(신금자)")
    parser.add_argument("--pair", type=str, default=None,
                        help="AI Hub 화자쌍 id — find_clips로 그 인물 클립만 사용(외부)")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    settings = load_settings(".env")
    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    subject_context = SubjectContext(**payload["subjectContext"])
    intake_raw = payload.get("intakeContext")
    intake_context = IntakeContext(**intake_raw) if intake_raw else None

    stt = ElevenLabsScribeProvider(api_key=os.environ["ELEVENLABS_API_KEY"])
    cache_dir = args.out_dir / ".transcription_cache"
    if args.pair:
        from evaluation.e2e_external import find_clips
        clips = sorted(find_clips(args.pair))
    elif args.audio_dir:
        clips = sorted(args.audio_dir.glob("*.wav"))
    else:
        raise SystemExit("--audio-dir 또는 --pair 중 하나가 필요합니다")
    print(f"통화 {len(clips)}건 처리 시작\n")

    all_memories: list[dict] = []
    speech_examples: list[str] = []
    for i, clip in enumerate(clips, 1):
        segs = _transcribe_cached(stt, clip, cache_dir)
        label = _subject_label(segs)
        persons = run_persons_analysis(
            segs, subject_context=subject_context, subject_speaker_label=label,
            settings=settings)
        sens = run_sensitivity_analysis(segs, settings=settings)
        mem = extract_memory_segments(
            segs, subject_context=subject_context, subject_speaker_label=label,
            persons_result=persons, sensitivity_result=sens, settings=settings)
        got = mem["memorySegments"]
        all_memories.extend(got)
        # 말투 few-shot: 대상자 화자의 짧은 발화 몇 개
        for seg in segs:
            text = (seg.corrected_text or seg.transcript_text).strip()
            if seg.speaker_label == label and 6 <= len(text) <= 40:
                speech_examples.append(text)
        print(f"  [{i}/{len(clips)}] {clip.name}: 세그 {len(segs)} → 기억 {len(got)}건 (대상자화자 {label})")

    print(f"\n누적 기억 {len(all_memories)}건 · 말투 예시 {len(speech_examples)}개")

    # reflection: 누적 기억 → 통찰
    refl_inputs = [
        ReflectionMemoryInput(
            id=str(i), memory_text=m["memoryText"], tags=m.get("tags", []),
            importance_score=m.get("importanceScore"))
        for i, m in enumerate(all_memories)
    ]
    insights = []
    if refl_inputs:
        refl = run_reflection(
            ReflectionRequest(subject_context=subject_context, memories=refl_inputs),
            settings=settings)
        insights = [r.insight for r in refl.reflections]
    print(f"통찰 {len(insights)}건 도출")

    # assembly: intake + 통찰 + 말투 few-shot
    assembled = assemble_persona_instructions(
        PersonaAssemblyRequest(
            subject_context=subject_context, intake_context=intake_context,
            speech_examples=speech_examples[:40], persona_insights=insights))

    args.out_dir.mkdir(parents=True, exist_ok=True)
    # chat.py가 쓸 누적 기억(콘텐츠·태그) + 페르소나 프롬프트 저장
    mem_out = [{"title": ",".join(m.get("tags", [])[:2]), "content": m["memoryText"],
                "tags": m.get("tags", [])} for m in all_memories]
    (args.out_dir / "memories.json").write_text(
        json.dumps(mem_out, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out_dir / "persona.txt").write_text(assembled.instructions, encoding="utf-8")
    (args.out_dir / "insights.json").write_text(
        json.dumps(insights, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n저장: {args.out_dir}/  (memories.json {len(mem_out)}건, persona.txt, insights.json)")
    print("대화: python -m evaluation.persona.chat "
          f"--fixture {args.fixture} --persona {args.out_dir}/persona.txt "
          f"--memories {args.out_dir}/memories.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
