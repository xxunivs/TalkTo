"""파이프라인 러너 — 한 대상자의 입력을 s1~s3까지 통째로 돌려 output/sN에 저장.

실서비스에선 BE가 단계별로 우리 API를 호출하지만, 로컬 테스트/평가(그리고 s5 대화
harness)는 결과 파일이 있어야 한다. 이 스크립트가 그 역할 — BE가 하는 단계 호출을
로컬에서 파일로 한 번에 수행한다.

흐름 (통화녹음마다): 전사(캐시) → 대상자 화자 판정 → 교정 → 인물·민감·말투·기억·요약.
전 통화 기억을 누적 → reflection(통찰) → 페르소나 조립(b_persona.txt) → 임베딩.

실행:
    python -m app.tool.run_pipeline --subject 신금자
읽기:  output/<subject>/s0_input/{persona_intake.json, 통화녹음/*.wav}
쓰기:  output/<subject>/{s1_transcription, s2_extraction, s3_synthesis}/…
"""

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

from app.core.config import Settings, load_settings
from app.data_schema.context import IntakeContext, SubjectContext
from app.data_schema.persona import PersonaAssemblyRequest
from app.data_schema.reflection import ReflectionMemoryInput, ReflectionRequest
from app.data_schema.transcript import TranscriptSegment
from app.external_service.stt import create_stt_provider
from app.tool.s1_transcription.b_speaker_id import identify_subject_by_content
from app.tool.s1_transcription.c_correction import build_glossary, correct_segments
from app.tool.s2_extraction.a_persons import run_persons_analysis
from app.tool.s2_extraction.b_sensitivity import run_sensitivity_analysis
from app.tool.s2_extraction.c_linguistic_style import run_linguistic_style_analysis
from app.tool.s2_extraction.d_memory_segments import extract_memory_segments
from app.tool.s2_extraction.e_summary import aggregate_tags, summarize_recording
from app.tool.s3_synthesis.a_reflection import run_reflection
from app.tool.s3_synthesis.b_persona import assemble_persona_instructions
from app.tool.s3_synthesis.c_embeddings import embed_texts

logger = logging.getLogger(__name__)

AUDIO_EXTS = ("*.wav", "*.m4a", "*.mp3", "*.webm", "*.ogg", "*.flac")


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _majority_label(segments: list[TranscriptSegment]) -> str | None:
    counts: dict[str, int] = {}
    for seg in segments:
        counts[seg.speaker_label] = counts.get(seg.speaker_label, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _transcribe_cached(provider, audio: Path, cache_dir: Path) -> list[TranscriptSegment]:
    """전사는 비싸니 파일명 기준 캐시. 재실행 때 재전사 안 함."""
    key = hashlib.sha1(audio.name.encode()).hexdigest()[:16]
    cache = cache_dir / f"{key}.json"
    if cache.exists():
        return [TranscriptSegment(**s) for s in json.loads(cache.read_text(encoding="utf-8"))]
    segs = provider.transcribe(audio, language="ko", speaker_diarization=True).segments
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps([s.model_dump(by_alias=True) for s in segs], ensure_ascii=False),
        encoding="utf-8",
    )
    return segs


def _mem_field(memory: dict, *keys, default=None):
    for key in keys:
        if key in memory and memory[key] not in (None, ""):
            return memory[key]
    return default


def run(subject_dir: Path, *, settings: Settings, limit: int | None) -> int:
    s0 = subject_dir / "s0_input"
    intake_path = s0 / "persona_intake.json"
    if not intake_path.exists():
        print(f"입력이 없다: {intake_path}", file=sys.stderr)
        return 1
    payload = json.loads(intake_path.read_text(encoding="utf-8"))
    subject_context = SubjectContext(**payload["subjectContext"])
    intake_raw = payload.get("intakeContext")
    intake_context = IntakeContext(**intake_raw) if intake_raw else None

    audio_dir = s0 / "통화녹음"
    clips: list[Path] = []
    for pattern in AUDIO_EXTS:
        clips.extend(audio_dir.glob(pattern))
    clips = sorted(clips)
    if limit:
        clips = clips[:limit]
    if not clips:
        print(f"통화녹음이 없다: {audio_dir}", file=sys.stderr)
        return 1

    provider = create_stt_provider(settings)
    glossary = build_glossary(subject_context, intake_context)
    cache_dir = subject_dir / "s1_transcription" / ".transcription_cache"

    print(f"[{subject_dir.name}] 통화 {len(clips)}건 처리 시작 (용어집 {len(glossary)}개)\n")

    transcripts: dict[str, list[dict]] = {}
    labels: dict[str, str | None] = {}
    persons_by_rec: dict[str, dict] = {}
    sensitivity_by_rec: dict[str, dict] = {}
    style_by_rec: dict[str, dict] = {}
    summary_by_rec: dict[str, dict] = {}
    all_memories: list[dict] = []
    speech_examples: list[str] = []

    for i, clip in enumerate(clips, 1):
        segs = _transcribe_cached(provider, clip, cache_dir)
        # 대상자 화자: 내용 기반 판정(호칭 방향) → 실패 시 발화량 최다 폴백
        label = identify_subject_by_content(segs, subject_context, settings=settings) \
            or _majority_label(segs)
        # 교정 (용어집 기준, 세그먼트에 corrected_text 채움)
        if glossary:
            segs = correct_segments(segs, glossary=glossary, settings=settings)

        persons = run_persons_analysis(
            segs, subject_context=subject_context, subject_speaker_label=label, settings=settings)
        sens = run_sensitivity_analysis(segs, settings=settings)
        style = run_linguistic_style_analysis(
            segs, subject_context=subject_context, subject_speaker_label=label, settings=settings)
        summary = summarize_recording(segs, settings=settings)
        mem = extract_memory_segments(
            segs, subject_context=subject_context, subject_speaker_label=label,
            persons_result=persons, sensitivity_result=sens, settings=settings)

        got = mem["memorySegments"]
        all_memories.extend(got)
        for seg in segs:
            text = (seg.corrected_text or seg.transcript_text).strip()
            if seg.speaker_label == label and 6 <= len(text) <= 40:
                speech_examples.append(text)

        transcripts[clip.name] = [s.model_dump(by_alias=True) for s in segs]
        labels[clip.name] = label
        persons_by_rec[clip.name] = persons
        sensitivity_by_rec[clip.name] = sens
        style_by_rec[clip.name] = style["linguisticStyle"]
        summary_by_rec[clip.name] = {"summary": summary, "tags": aggregate_tags(mem)}
        print(f"  [{i}/{len(clips)}] {clip.name}: 세그 {len(segs)} → 기억 {len(got)}건 (대상자화자 {label})")

    # 기억에 안정적 id 부여 (임베딩·reflection·s5 검색이 이걸로 연결)
    for idx, memory in enumerate(all_memories):
        memory["id"] = str(idx)
    print(f"\n누적 기억 {len(all_memories)}건 · 말투 예시 {len(speech_examples)}개")

    # ── s1 저장 ──
    _write_json(subject_dir / "s1_transcription" / "a_transcript.json", transcripts)
    _write_json(subject_dir / "s1_transcription" / "b_speaker_id.json", labels)
    # ── s2 저장 ──
    _write_json(subject_dir / "s2_extraction" / "a_persons.json", persons_by_rec)
    _write_json(subject_dir / "s2_extraction" / "b_sensitivity.json", sensitivity_by_rec)
    _write_json(subject_dir / "s2_extraction" / "c_linguistic_style.json", style_by_rec)
    _write_json(subject_dir / "s2_extraction" / "e_summary.json", summary_by_rec)
    memory_out = [
        {
            "id": m["id"],
            "memoryText": _mem_field(m, "memoryText", "memory_text"),
            "tags": m.get("tags", []),
            "importanceScore": _mem_field(m, "importanceScore", "importance"),
            "relatedPeople": m.get("relatedPeople", []),
            "confidence": m.get("confidence"),
        }
        for m in all_memories
    ]
    _write_json(subject_dir / "s2_extraction" / "d_memory_segments.json", memory_out)

    # ── s3: reflection → 통찰 ──
    insights: list[str] = []
    if memory_out:
        refl = run_reflection(
            ReflectionRequest(
                subject_context=subject_context,
                memories=[
                    ReflectionMemoryInput(
                        id=m["id"], memory_text=m["memoryText"], tags=m["tags"],
                        importance_score=m["importanceScore"])
                    for m in memory_out
                ],
            ),
            settings=settings,
        )
        insights = [r.insight for r in refl.reflections]
    _write_json(subject_dir / "s3_synthesis" / "a_reflection.json", insights)
    print(f"통찰 {len(insights)}건")

    # ── s3: 페르소나 조립 ──
    assembled = assemble_persona_instructions(
        PersonaAssemblyRequest(
            subject_context=subject_context, intake_context=intake_context,
            speech_examples=speech_examples[:40], persona_insights=insights))
    (subject_dir / "s3_synthesis").mkdir(parents=True, exist_ok=True)
    (subject_dir / "s3_synthesis" / "b_persona.txt").write_text(
        assembled.instructions, encoding="utf-8")

    # ── s3: 임베딩 (기억 텍스트 → 벡터, id 매칭) ──
    embeddings = []
    if memory_out:
        vectors = embed_texts([m["memoryText"] for m in memory_out], settings=settings)
        embeddings = [
            {"id": m["id"], "embedding": vec} for m, vec in zip(memory_out, vectors)
        ]
    _write_json(subject_dir / "s3_synthesis" / "c_embeddings.json", {"embeddings": embeddings})

    print(f"\n저장 완료: {subject_dir}/  (b_persona.txt, 기억 {len(memory_out)}건, 임베딩 {len(embeddings)}건)")
    print(f"대화: python -m app.tool.s5_chat.a_chat --subject {subject_dir.name}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="파이프라인 러너 (입력 → output/sN)")
    parser.add_argument("--subject", required=True, help="대상자 폴더명 (예: 신금자)")
    parser.add_argument("--root", default="output", help="output 루트 (기본 output)")
    parser.add_argument("--limit", type=int, default=None, help="통화녹음 최대 개수(빠른 테스트용)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    settings = load_settings()
    if settings.openai_api_key is None or settings.elevenlabs_api_key is None:
        print("OPENAI_API_KEY / ELEVENLABS_API_KEY가 필요하다 (.env 확인).", file=sys.stderr)
        sys.exit(1)
    subject_dir = Path(args.root) / args.subject
    if not subject_dir.exists():
        print(f"대상자 폴더가 없다: {subject_dir}", file=sys.stderr)
        sys.exit(1)
    sys.exit(run(subject_dir, settings=settings, limit=args.limit))


if __name__ == "__main__":
    main()
