# 로컬 실행 (입력 -> output/sN). 전체 또는 단계만 골라 돌린다.
# 같은 단계 순서가 infra/be_ai_api/recording_analysis.py 에도 있다. 순서를 바꾸면 둘 다 확인할 것.
#
#     python -m pipeline.run_pipeline --subject 신금자                # 전체
#     python -m pipeline.run_pipeline --subject 신금자 --only s2      # s2 전체
#     python -m pipeline.run_pipeline --subject 신금자 --only s2_d    # 기억 추출만
#     python -m pipeline.run_pipeline --subject 신금자 --from s2 --to s3
#
# 각 단계는 앞 단계 결과를 output/<subject>/ 에서 읽는다. 전사는 a_transcript.json 에
# 이미 있는 녹음을 건너뛰어, 다시 돌려도 유료 API 를 부르지 않는다.

import argparse
import json
import logging
import sys
from pathlib import Path

from infra.setting import Settings, load_settings
from infra.schema.subject import IntakeContext, SubjectContext
from infra.schema.persona import PersonaAssemblyRequest
from infra.schema.insight import ReflectionMemoryInput, ReflectionRequest
from infra.schema.transcript import TranscriptSegment
from infra.external_api.stt import create_stt_provider
from pipeline.s1_transcription.b_speaker_id import identify_subject_by_content
from pipeline.s1_transcription.c_correction import build_glossary, correct_segments
from pipeline.s2_extraction.a_persons import run_persons_analysis
from pipeline.s2_extraction.b_sensitivity import run_sensitivity_analysis
from pipeline.s2_extraction.c_linguistic_style import run_linguistic_style_analysis
from pipeline.s2_extraction.d_memory_segments import extract_memory_segments
from pipeline.s2_extraction.e_summary import aggregate_tags, summarize_recording
from pipeline.s3_synthesis.a_reflection import run_reflection
from pipeline.s3_synthesis.b_persona import assemble_persona_instructions
from pipeline.s3_synthesis.c_embeddings import embed_texts

logger = logging.getLogger(__name__)

AUDIO_EXTS = ("*.wav", "*.m4a", "*.mp3", "*.webm", "*.ogg", "*.flac")

STEP_ORDER = [
    "s1_a", "s1_b", "s1_c",
    "s2_a", "s2_b", "s2_c", "s2_d", "s2_e",
    "s3_a", "s3_b", "s3_c",
]
STEP_LABEL = {
    "s1_a": "전사", "s1_b": "화자 판별", "s1_c": "교정",
    "s2_a": "인물", "s2_b": "민감도", "s2_c": "말투", "s2_d": "기억", "s2_e": "요약과 태그",
    "s3_a": "통찰", "s3_b": "프롬프트 조립", "s3_c": "임베딩",
}


class NeedPriorStep(Exception):
    """앞 단계 결과 파일이 없어 진행할 수 없을 때."""

    def __init__(self, missing_file: Path, needed_by: str) -> None:
        super().__init__(str(missing_file))
        self.missing_file = missing_file
        self.needed_by = needed_by


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path, needed_by: str):
    if not path.exists():
        raise NeedPriorStep(path, needed_by)
    return json.loads(path.read_text(encoding="utf-8"))


def _majority_label(segments: list[TranscriptSegment]) -> str | None:
    counts: dict[str, int] = {}
    for seg in segments:
        counts[seg.speaker_label] = counts.get(seg.speaker_label, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _mem_field(memory: dict, *keys, default=None):
    for key in keys:
        if key in memory and memory[key] not in (None, ""):
            return memory[key]
    return default


def expand_steps(token: str) -> list[str]:
    """s2 -> s2_a..s2_e, s2_d -> [s2_d]. 모르는 값이면 빈 목록."""
    if token in STEP_ORDER:
        return [token]
    return [s for s in STEP_ORDER if s.startswith(token + "_")]


def select_steps(only: str | None, start: str | None, end: str | None) -> list[str]:
    known = "s1, s2, s3, " + ", ".join(STEP_ORDER)
    if only:
        chosen = expand_steps(only)
        if not chosen:
            raise SystemExit(f"모르는 단계: {only}  (가능: {known})")
        return chosen
    steps = list(STEP_ORDER)
    if start:
        first = expand_steps(start)
        if not first:
            raise SystemExit(f"모르는 단계: {start}  (가능: {known})")
        steps = [s for s in steps if STEP_ORDER.index(s) >= STEP_ORDER.index(first[0])]
    if end:
        last = expand_steps(end)
        if not last:
            raise SystemExit(f"모르는 단계: {end}  (가능: {known})")
        steps = [s for s in steps if STEP_ORDER.index(s) <= STEP_ORDER.index(last[-1])]
    return steps


class Ctx:
    """단계들이 함께 쓰는 상태. 메모리에 없으면 output 파일에서 읽어온다."""

    def __init__(self, subject_dir: Path, settings: Settings, limit: int | None) -> None:
        self.dir = subject_dir
        self.settings = settings
        self.limit = limit
        self._segments: dict[str, list[TranscriptSegment]] | None = None
        self._labels: dict[str, str | None] | None = None
        self._persons: dict[str, dict] | None = None
        self._sensitivity: dict[str, dict] | None = None
        self._memories: list[dict] | None = None
        self._insights: list[str] | None = None

        payload = _read_json(subject_dir / "s0_input" / "persona_intake.json", "s0_input")
        self.subject_context = SubjectContext(**payload["subjectContext"])
        raw = payload.get("intakeContext")
        self.intake_context = IntakeContext(**raw) if raw else None
        self.glossary = build_glossary(self.subject_context, self.intake_context)

    def clips(self) -> list[Path]:
        audio_dir = self.dir / "s0_input" / "통화녹음"
        found: list[Path] = []
        for pattern in AUDIO_EXTS:
            found.extend(audio_dir.glob(pattern))
        found = sorted(found)
        return found[: self.limit] if self.limit else found

    def segments(self, needed_by: str) -> dict[str, list[TranscriptSegment]]:
        if self._segments is None:
            raw = _read_json(self.dir / "s1_transcription" / "a_transcript.json", needed_by)
            self._segments = {
                name: [TranscriptSegment(**s) for s in rows] for name, rows in raw.items()
            }
        return self._segments

    def labels(self, needed_by: str) -> dict[str, str | None]:
        if self._labels is None:
            self._labels = _read_json(self.dir / "s1_transcription" / "b_speaker_id.json", needed_by)
        return self._labels

    def persons(self, needed_by: str) -> dict[str, dict]:
        if self._persons is None:
            self._persons = _read_json(self.dir / "s2_extraction" / "a_persons.json", needed_by)
        return self._persons

    def sensitivity(self, needed_by: str) -> dict[str, dict]:
        if self._sensitivity is None:
            self._sensitivity = _read_json(self.dir / "s2_extraction" / "b_sensitivity.json", needed_by)
        return self._sensitivity

    def memories(self, needed_by: str) -> list[dict]:
        if self._memories is None:
            self._memories = _read_json(self.dir / "s2_extraction" / "d_memory_segments.json", needed_by)
        return self._memories

    def insights(self, needed_by: str) -> list[str]:
        if self._insights is None:
            self._insights = _read_json(self.dir / "s3_synthesis" / "a_reflection.json", needed_by)
        return self._insights

    def speech_examples(self, needed_by: str) -> list[str]:
        """대상자가 말한 짧고 담백한 발화 (말투 few-shot)."""
        segments = self.segments(needed_by)
        labels = self.labels(needed_by)
        out: list[str] = []
        for name, segs in segments.items():
            label = labels.get(name)
            for seg in segs:
                text = (seg.corrected_text or seg.transcript_text).strip()
                if seg.speaker_label == label and 6 <= len(text) <= 40:
                    out.append(text)
        return out

    def save_segments(self) -> None:
        _write_json(
            self.dir / "s1_transcription" / "a_transcript.json",
            {
                name: [s.model_dump(by_alias=True) for s in segs]
                for name, segs in (self._segments or {}).items()
            },
        )


# ────────────────────────── s1 전사 ──────────────────────────

def step_s1_a(ctx: Ctx) -> str:
    """전사는 유료라, a_transcript.json 에 이미 있는 녹음은 다시 전사하지 않는다.

    다시 전사하려면 그 녹음 항목을 a_transcript.json 에서 지우면 된다.
    이미 있는 항목은 그대로 두므로 s1_c 가 채운 교정문도 보존된다.
    """
    clips = ctx.clips()
    if not clips:
        raise NeedPriorStep(ctx.dir / "s0_input" / "통화녹음", "s1_a")

    done: dict[str, list[TranscriptSegment]] = {}
    path = ctx.dir / "s1_transcription" / "a_transcript.json"
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        done = {name: [TranscriptSegment(**s) for s in rows] for name, rows in raw.items()}

    todo = [c for c in clips if c.name not in done]
    if todo:
        provider = create_stt_provider(ctx.settings)
        for clip in todo:
            done[clip.name] = provider.transcribe(
                clip, language="ko", speaker_diarization=True).segments

    ctx._segments = done
    ctx.save_segments()
    total = sum(len(v) for v in done.values())
    skipped = len(clips) - len(todo)
    detail = f"통화 {len(done)}건, 세그먼트 {total}개"
    return detail + (f" (이미 있던 {skipped}건은 건너뜀)" if skipped else "")


def step_s1_b(ctx: Ctx) -> str:
    segments = ctx.segments("s1_b")
    labels: dict[str, str | None] = {}
    for name, segs in segments.items():
        labels[name] = (
            identify_subject_by_content(segs, ctx.subject_context, settings=ctx.settings)
            or _majority_label(segs)
        )
    ctx._labels = labels
    _write_json(ctx.dir / "s1_transcription" / "b_speaker_id.json", labels)
    found = sum(1 for v in labels.values() if v)
    return f"{found}/{len(labels)}건 대상자 화자 확정"


def step_s1_c(ctx: Ctx) -> str:
    if not ctx.glossary:
        return "용어집이 비어 건너뜀"
    segments = ctx.segments("s1_c")
    fixed = 0
    for segs in segments.values():
        correct_segments(segs, glossary=ctx.glossary, settings=ctx.settings)
        fixed += sum(1 for s in segs if s.corrected_text)
    ctx.save_segments()
    return f"용어집 {len(ctx.glossary)}개 기준, 교정 {fixed}건"


# ────────────────────────── s2 추출 ──────────────────────────

def step_s2_a(ctx: Ctx) -> str:
    segments, labels = ctx.segments("s2_a"), ctx.labels("s2_a")
    out = {
        name: run_persons_analysis(
            segs, subject_context=ctx.subject_context,
            subject_speaker_label=labels.get(name), settings=ctx.settings)
        for name, segs in segments.items()
    }
    ctx._persons = out
    _write_json(ctx.dir / "s2_extraction" / "a_persons.json", out)
    return f"{len(out)}건 분석"


def step_s2_b(ctx: Ctx) -> str:
    segments = ctx.segments("s2_b")
    out = {name: run_sensitivity_analysis(segs, settings=ctx.settings) for name, segs in segments.items()}
    ctx._sensitivity = out
    _write_json(ctx.dir / "s2_extraction" / "b_sensitivity.json", out)
    return f"{len(out)}건 분석"


def step_s2_c(ctx: Ctx) -> str:
    segments, labels = ctx.segments("s2_c"), ctx.labels("s2_c")
    out = {
        name: run_linguistic_style_analysis(
            segs, subject_context=ctx.subject_context,
            subject_speaker_label=labels.get(name), settings=ctx.settings)["linguisticStyle"]
        for name, segs in segments.items()
    }
    _write_json(ctx.dir / "s2_extraction" / "c_linguistic_style.json", out)
    return f"{len(out)}건 분석"


def step_s2_d(ctx: Ctx) -> str:
    segments = ctx.segments("s2_d")
    labels = ctx.labels("s2_d")
    persons = ctx.persons("s2_d")
    sensitivity = ctx.sensitivity("s2_d")

    collected: list[dict] = []
    for name, segs in segments.items():
        result = extract_memory_segments(
            segs, subject_context=ctx.subject_context, subject_speaker_label=labels.get(name),
            persons_result=persons.get(name, {}), sensitivity_result=sensitivity.get(name, {}),
            settings=ctx.settings)
        for memory in result["memorySegments"]:
            memory["recording"] = name  # 어느 통화에서 나왔는지 (s2_e 가 녹음별 태그를 낼 때 쓴다)
            collected.append(memory)

    memory_out = [
        {
            "id": str(idx),
            "recording": m.get("recording"),
            "memoryText": _mem_field(m, "memoryText", "memory_text"),
            "tags": m.get("tags", []),
            "importanceScore": _mem_field(m, "importanceScore", "importance"),
            "relatedPeople": m.get("relatedPeople", []),
            "confidence": m.get("confidence"),
        }
        for idx, m in enumerate(collected)
    ]
    ctx._memories = memory_out
    _write_json(ctx.dir / "s2_extraction" / "d_memory_segments.json", memory_out)
    return f"기억 {len(memory_out)}건"


def step_s2_e(ctx: Ctx) -> str:
    segments = ctx.segments("s2_e")
    memories = ctx.memories("s2_e")
    # 기억이 어느 통화에서 나왔는지는 s2_d 가 recording 필드로 남긴다. 그 필드가 없는
    # 옛 결과라면 녹음별로 나눌 수 없어 전체 기억으로 태그를 낸다(근사).
    tagged_by_recording = any(m.get("recording") for m in memories)
    note = "" if tagged_by_recording else " (기억에 통화 구분이 없어 전체 기준으로 태그. s2_d 를 다시 돌리면 통화별로 나뉜다)"

    out: dict[str, dict] = {}
    for name, segs in segments.items():
        mine = [m for m in memories if m.get("recording") == name] if tagged_by_recording else memories
        out[name] = {
            "summary": summarize_recording(segs, settings=ctx.settings),
            "tags": aggregate_tags({"memorySegments": mine}),
        }
    _write_json(ctx.dir / "s2_extraction" / "e_summary.json", out)
    return f"{len(out)}건 요약{note}"


# ────────────────────────── s3 종합 ──────────────────────────

def step_s3_a(ctx: Ctx) -> str:
    memories = ctx.memories("s3_a")
    insights: list[str] = []
    if memories:
        result = run_reflection(
            ReflectionRequest(
                subject_context=ctx.subject_context,
                memories=[
                    ReflectionMemoryInput(
                        id=m["id"], memory_text=m["memoryText"], tags=m["tags"],
                        importance_score=m["importanceScore"])
                    for m in memories
                ],
            ),
            settings=ctx.settings,
        )
        insights = [r.insight for r in result.reflections]
    ctx._insights = insights
    _write_json(ctx.dir / "s3_synthesis" / "a_reflection.json", insights)
    return f"통찰 {len(insights)}건"


def step_s3_b(ctx: Ctx) -> str:
    examples = ctx.speech_examples("s3_b")
    insights = ctx.insights("s3_b")
    assembled = assemble_persona_instructions(
        PersonaAssemblyRequest(
            subject_context=ctx.subject_context, intake_context=ctx.intake_context,
            speech_examples=examples[:40], persona_insights=insights))
    path = ctx.dir / "s3_synthesis" / "b_persona.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(assembled.instructions, encoding="utf-8")
    return f"{len(assembled.instructions)}자 (말투 예시 {len(examples)}개, 통찰 {len(insights)}건)"


def step_s3_c(ctx: Ctx) -> str:
    memories = ctx.memories("s3_c")
    rows = []
    if memories:
        vectors = embed_texts([m["memoryText"] for m in memories], settings=ctx.settings)
        rows = [{"id": m["id"], "embedding": vec} for m, vec in zip(memories, vectors)]
    _write_json(ctx.dir / "s3_synthesis" / "c_embeddings.json", {"embeddings": rows})
    return f"임베딩 {len(rows)}건"


STEP_FUNC = {
    "s1_a": step_s1_a, "s1_b": step_s1_b, "s1_c": step_s1_c,
    "s2_a": step_s2_a, "s2_b": step_s2_b, "s2_c": step_s2_c,
    "s2_d": step_s2_d, "s2_e": step_s2_e,
    "s3_a": step_s3_a, "s3_b": step_s3_b, "s3_c": step_s3_c,
}


def run(subject_dir: Path, *, settings: Settings, limit: int | None, steps: list[str]) -> int:
    try:
        ctx = Ctx(subject_dir, settings, limit)
    except NeedPriorStep as missing:
        print(f"입력이 없다: {missing.missing_file}", file=sys.stderr)
        return 1

    print(f"[{subject_dir.name}] {len(steps)}개 단계 실행: {', '.join(steps)}\n")
    for step in steps:
        try:
            detail = STEP_FUNC[step](ctx)
        except NeedPriorStep as missing:
            print(
                f"\n{step}({STEP_LABEL[step]})를 돌리려면 앞 단계 결과가 필요하다.\n"
                f"  없는 파일: {missing.missing_file}\n"
                f"  먼저 돌릴 것: python -m pipeline.run_pipeline --subject {subject_dir.name} --to {step}",
                file=sys.stderr,
            )
            return 1
        print(f"  {step} {STEP_LABEL[step]}: {detail}")

    print(f"\n저장 완료: {subject_dir}/")
    print(f"대화: python -m pipeline.s5_chat.a_chat --subject {subject_dir.name}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="파이프라인 로컬 실행 (입력 -> output/sN)")
    parser.add_argument("--subject", required=True, help="대상자 폴더명 (예: 신금자)")
    parser.add_argument("--root", default="output", help="output 루트 (기본 output)")
    parser.add_argument("--limit", type=int, default=None, help="통화녹음 최대 개수(빠른 테스트용)")
    parser.add_argument("--only", help="이 단계만 (s2 또는 s2_d)")
    parser.add_argument("--from", dest="start", help="이 단계부터")
    parser.add_argument("--to", dest="end", help="이 단계까지")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    steps = select_steps(args.only, args.start, args.end)

    settings = load_settings()
    if settings.openai_api_key is None or settings.elevenlabs_api_key is None:
        print("OPENAI_API_KEY / ELEVENLABS_API_KEY가 필요하다 (.env 확인).", file=sys.stderr)
        sys.exit(1)
    subject_dir = Path(args.root) / args.subject
    if not subject_dir.exists():
        print(f"대상자 폴더가 없다: {subject_dir}", file=sys.stderr)
        sys.exit(1)
    sys.exit(run(subject_dir, settings=settings, limit=args.limit, steps=steps))


if __name__ == "__main__":
    main()
