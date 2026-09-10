# 로컬 실행 (input/<대상자> 를 읽어 output/<대상자>/sN 에 쓴다). 전체 또는 단계만 골라 돌린다.
# 같은 단계 순서가 infra/be_ai_api/recording_analysis.py 에도 있다. 순서를 바꾸면 둘 다 확인할 것.
#
#     python -m pipeline.run_pipeline --subject 신금자                # 전체
#     python -m pipeline.run_pipeline --subject 신금자 --only s2      # s2 전체
#     python -m pipeline.run_pipeline --subject 신금자 --only s2_e    # 기억 추출만
#     python -m pipeline.run_pipeline --subject 신금자 --from s2 --to s3
#
# 각 단계는 앞 단계 결과를 output/<subject>/ 에서 읽는다. 전사는 a_transcript.json 에
# 이미 있는 녹음을 건너뛰어, 다시 돌려도 유료 API 를 부르지 않는다.
#
# 사람이 넣는 자료는 input/<대상자>/ 에 둔다. 파이프라인이 자동으로 읽는 것은
# persona_intake.json 과 음성/ 뿐이고, 나머지 자료(인터뷰 전사, 기억DB 등)는 사람이
# 도구를 골라 넣는다. 매뉴얼 페르소나는 같은 입력을 쓰고 산출물만
# output/<대상자>_manual/ 로 갈린다.

import argparse
import json
import logging
import sys
from pathlib import Path

from infra.setting import Settings, load_settings
from infra.schema.subject import (
    FamilyNote,
    IntakeContext,
    SpeechStyle,
    SubjectContext,
    TabooTopic,
)
from infra.schema.persona import PersonaAssemblyRequest
from infra.schema.insight import ReflectionMemoryInput, ReflectionRequest
from infra.schema.transcript import TranscriptSegment
from infra.external_api.stt import create_stt_provider
from pipeline.s1_transcription.b_speaker_id import identify_subject_by_content
from pipeline.s1_transcription.c_correction import build_glossary, correct_segments
from pipeline.s2_extraction.a_extract_persons import run_persons_analysis
from pipeline.s2_extraction.b_extract_sensitivity import run_sensitivity_analysis
from pipeline.s2_extraction.c_extract_linguistic_style import run_linguistic_style_analysis
from pipeline.s2_extraction.d_merge_recording_prompt import merge_recording_prompt
from pipeline.s2_extraction.e_extract_memory import extract_memory_segments
from pipeline.s2_extraction.f_extract_summary import aggregate_tags, summarize_recording
from pipeline.s3_synthesis.a_merge_memory import merge_memory
from pipeline.s3_synthesis.b_extract_insight import run_reflection
from pipeline.s3_synthesis.c_build_system_prompt import assemble_persona_instructions
from pipeline.s3_synthesis.d_build_memory_embedding import embed_texts

logger = logging.getLogger(__name__)

AUDIO_EXTS = ("*.wav", "*.m4a", "*.mp3", "*.webm", "*.ogg", "*.flac")

STEP_ORDER = [
    "s1_a", "s1_b", "s1_c",
    "s2_a", "s2_b", "s2_c", "s2_d", "s2_e", "s2_f",
    "s3_a", "s3_b", "s3_c", "s3_d",
]
STEP_LABEL = {
    "s1_a": "전사", "s1_b": "화자 판별", "s1_c": "교정",
    "s2_a": "인물", "s2_b": "민감도", "s2_c": "말투",
    "s2_d": "프롬프트 재료 합치기", "s2_e": "기억", "s2_f": "요약과 태그",
    "s3_a": "기억 모으기", "s3_b": "통찰", "s3_c": "프롬프트 조립", "s3_d": "임베딩",
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
    """s2 -> s2_a..s2_f, s2_d -> [s2_d]. 모르는 값이면 빈 목록."""
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

    def __init__(
        self, subject_dir: Path, input_dir: Path, settings: Settings, limit: int | None
    ) -> None:
        self.dir = subject_dir
        self.input_dir = input_dir
        self.settings = settings
        self.limit = limit
        self._segments: dict[str, list[TranscriptSegment]] | None = None
        self._labels: dict[str, str | None] | None = None
        self._persons: dict[str, dict] | None = None
        self._sensitivity: dict[str, dict] | None = None
        self._memories: list[dict] | None = None
        self._insights: list[str] | None = None

        # 작성본에서 변환한 것(s2_extraction)을 먼저 보고, 없으면 앱이 넣어준 입력을 본다
        made = subject_dir / "s2_extraction" / "b_intake_prompt.json"
        source = made if made.exists() else input_dir / "persona_intake.json"
        payload = _read_json(source, "s0_intake")
        self.subject_context = SubjectContext(**payload["subjectContext"])
        raw = payload.get("intakeContext")
        self.intake_context = IntakeContext(**raw) if raw else None
        self.glossary = build_glossary(self.subject_context, self.intake_context)

    def clips(self) -> list[Path]:
        audio_dir = self.input_dir / "음성"
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
            self._persons = _read_json(self.dir / "s2_extraction" / "a_recording_persons.json", needed_by)
        return self._persons

    def sensitivity(self, needed_by: str) -> dict[str, dict]:
        if self._sensitivity is None:
            self._sensitivity = _read_json(self.dir / "s2_extraction" / "b_recording_sensitivity.json", needed_by)
        return self._sensitivity

    def memories(self, needed_by: str) -> list[dict]:
        """s3_a 가 모아 둔 한 장을 본다. 출처별 파일을 직접 세지 않는다."""
        if self._memories is None:
            self._memories = _read_json(
                self.dir / "s3_synthesis" / "a_memory.json", needed_by)
        return self._memories

    def prompt_material(self) -> tuple[IntakeContext, dict]:
        """설문과 전사, 두 장을 합쳐 프롬프트 재료 하나로 만든다.

        같은 칸에 둘 다 값이 있으면 설문이 이긴다. 사용자가 직접 쓴 것이라서다.
        전사는 설문이 비워둔 칸만 채운다.
        """
        intake = self.intake_context or IntakeContext()
        path = self.dir / "s2_extraction" / "d_recording_prompt.json"
        if not path.exists():
            return intake, {}
        recording = json.loads(path.read_text(encoding="utf-8"))

        known = {f.name for f in intake.family_map}
        for row in recording.get("familyMap", []):
            if row.get("name") and row["name"] not in known:
                intake.family_map.append(FamilyNote(**row))

        style = recording.get("speechStyle") or {}
        if isinstance(intake.speech_style, SpeechStyle):
            if not intake.speech_style.interjections:
                intake.speech_style.interjections = style.get("interjections", [])
            if not intake.speech_style.summary:
                intake.speech_style.summary = style.get("summary")
        elif intake.speech_style is None and style:
            intake.speech_style = SpeechStyle(**style)

        if all(isinstance(t, TabooTopic) for t in intake.taboo_topics):
            topics = {t.topic for t in intake.taboo_topics}
            for row in recording.get("tabooTopics", []):
                if row.get("topic") and row["topic"] not in topics:
                    intake.taboo_topics.append(TabooTopic(**row))

        return intake, recording

    def insights(self, needed_by: str) -> list[str]:
        if self._insights is None:
            self._insights = _read_json(self.dir / "s3_synthesis" / "b_insight.json", needed_by)
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
        raise NeedPriorStep(ctx.input_dir / "음성", "s1_a")

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
    _write_json(ctx.dir / "s2_extraction" / "a_recording_persons.json", out)
    return f"{len(out)}건 분석"


def step_s2_b(ctx: Ctx) -> str:
    segments = ctx.segments("s2_b")
    out = {name: run_sensitivity_analysis(segs, settings=ctx.settings) for name, segs in segments.items()}
    ctx._sensitivity = out
    _write_json(ctx.dir / "s2_extraction" / "b_recording_sensitivity.json", out)
    return f"{len(out)}건 분석"


def step_s2_c(ctx: Ctx) -> str:
    segments, labels = ctx.segments("s2_c"), ctx.labels("s2_c")
    out = {
        name: run_linguistic_style_analysis(
            segs, subject_context=ctx.subject_context,
            subject_speaker_label=labels.get(name), settings=ctx.settings)["linguisticStyle"]
        for name, segs in segments.items()
    }
    _write_json(ctx.dir / "s2_extraction" / "c_recording_linguistic_style.json", out)
    return f"{len(out)}건 분석"


def step_s2_d(ctx: Ctx) -> str:
    """a·b·c 를 설문과 같은 칸 이름으로 옮겨 담는다. s3 는 이 파일 하나만 본다.

    a·b·c 는 각각 돈이 드는 단계라, 아직 안 돌린 것이 있을 수 있다. 없는 것은
    빈 값으로 두고 있는 것만 담는다. 전사만 있어도 말투 예시는 나온다.
    """
    def maybe(filename: str) -> dict:
        path = ctx.dir / "s2_extraction" / filename
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    merged = merge_recording_prompt(
        persons=maybe("a_recording_persons.json"),
        sensitivity=maybe("b_recording_sensitivity.json"),
        linguistic_style=maybe("c_recording_linguistic_style.json"),
        speech_examples=ctx.speech_examples("s2_d"),
    )
    _write_json(ctx.dir / "s2_extraction" / "d_recording_prompt.json", merged)
    return (f"가족 {len(merged['familyMap'])}명 / 금기 {len(merged['tabooTopics'])}건 / "
            f"말투 예시 {len(merged['speechExamples'])}건")


def step_s2_e(ctx: Ctx) -> str:
    segments = ctx.segments("s2_e")
    labels = ctx.labels("s2_e")
    persons = ctx.persons("s2_e")
    sensitivity = ctx.sensitivity("s2_e")

    collected: list[dict] = []
    for name, segs in segments.items():
        result = extract_memory_segments(
            segs, subject_context=ctx.subject_context, subject_speaker_label=labels.get(name),
            persons_result=persons.get(name, {}), sensitivity_result=sensitivity.get(name, {}),
            settings=ctx.settings)
        for memory in result["memorySegments"]:
            memory["recording"] = name  # 어느 통화에서 나왔는지 (s2_f 가 녹음별 태그를 낼 때 쓴다)
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
    _write_json(ctx.dir / "s2_extraction" / "e_recording_memory.json", memory_out)
    return f"기억 {len(memory_out)}건"


def step_s2_f(ctx: Ctx) -> str:
    segments = ctx.segments("s2_f")
    memories = ctx.memories("s2_f")
    # 기억이 어느 통화에서 나왔는지는 s2_e 가 recording 필드로 남긴다. 그 필드가 없는
    # 옛 결과라면 녹음별로 나눌 수 없어 전체 기억으로 태그를 낸다(근사).
    tagged_by_recording = any(m.get("recording") for m in memories)
    note = "" if tagged_by_recording else " (기억에 통화 구분이 없어 전체 기준으로 태그. s2_e 를 다시 돌리면 통화별로 나뉜다)"

    out: dict[str, dict] = {}
    for name, segs in segments.items():
        mine = [m for m in memories if m.get("recording") == name] if tagged_by_recording else memories
        out[name] = {
            "summary": summarize_recording(segs, settings=ctx.settings),
            "tags": aggregate_tags({"memorySegments": mine}),
        }
    _write_json(ctx.dir / "s2_extraction" / "f_recording_summary.json", out)
    return f"{len(out)}건 요약{note}"


# ────────────────────────── s3 종합 ──────────────────────────

def step_s3_a(ctx: Ctx) -> str:
    """설문·전사·따로 넣은 기억을 한 장으로. 뒤 단계는 이 파일만 본다."""
    merged = merge_memory(ctx.dir / "s2_extraction")
    if not merged:
        raise NeedPriorStep(ctx.dir / "s2_extraction" / "*_memory.json", "s3_a")
    ctx._memories = merged
    _write_json(ctx.dir / "s3_synthesis" / "a_memory.json", merged)
    dupes = sum(1 for m in merged if m.get("duplicateOf"))
    by_source: dict[str, int] = {}
    for m in merged:
        by_source[m["source"]] = by_source.get(m["source"], 0) + 1
    detail = ", ".join(f"{k} {v}건" for k, v in by_source.items())
    return f"기억 {len(merged)}건 ({detail})" + (f", 겹치는 것 {dupes}건" if dupes else "")


def step_s3_b(ctx: Ctx) -> str:
    memories = ctx.memories("s3_b")
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
    _write_json(ctx.dir / "s3_synthesis" / "b_insight.json", insights)
    return f"통찰 {len(insights)}건"


def step_s3_c(ctx: Ctx) -> str:
    intake, recording = ctx.prompt_material()
    examples = [row["text"] for row in (recording.get("speechExamples") or []) if row.get("text")]
    insights = ctx.insights("s3_c")
    assembled = assemble_persona_instructions(
        PersonaAssemblyRequest(
            subject_context=ctx.subject_context, intake_context=intake,
            speech_examples=examples[:40], persona_insights=insights))
    path = ctx.dir / "s3_synthesis" / "c_system_prompt.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(assembled.instructions, encoding="utf-8")
    return f"{len(assembled.instructions)}자 (말투 예시 {len(examples)}개, 통찰 {len(insights)}건)"


def step_s3_d(ctx: Ctx) -> str:
    memories = ctx.memories("s3_d")
    rows = []
    if memories:
        vectors = embed_texts([m["memoryText"] for m in memories], settings=ctx.settings)
        rows = [{"id": m["id"], "embedding": vec} for m, vec in zip(memories, vectors)]
    _write_json(ctx.dir / "s3_synthesis" / "d_memory_embedding.json", {"embeddings": rows})
    return f"임베딩 {len(rows)}건"


STEP_FUNC = {
    "s1_a": step_s1_a, "s1_b": step_s1_b, "s1_c": step_s1_c,
    "s2_a": step_s2_a, "s2_b": step_s2_b, "s2_c": step_s2_c,
    "s2_d": step_s2_d, "s2_e": step_s2_e, "s2_f": step_s2_f,
    "s3_a": step_s3_a, "s3_b": step_s3_b, "s3_c": step_s3_c, "s3_d": step_s3_d,
}


def run(
    subject_dir: Path, input_dir: Path, *, settings: Settings, limit: int | None, steps: list[str]
) -> int:
    try:
        ctx = Ctx(subject_dir, input_dir, settings, limit)
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
    parser = argparse.ArgumentParser(description="파이프라인 로컬 실행 (input/<대상자> -> output/<대상자>/sN)")
    parser.add_argument("--subject", required=True, help="대상자 폴더명 (예: 신금자)")
    parser.add_argument("--root", default="output", help="산출물 루트 (기본 output)")
    parser.add_argument("--input-root", default="input", help="입력 루트 (기본 input)")
    parser.add_argument("--limit", type=int, default=None, help="음성 파일 최대 개수(빠른 테스트용)")
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
    # 매뉴얼 산출물(<이름>_manual)도 입력은 같은 사람 것을 쓴다
    input_dir = Path(args.input_root) / args.subject.removesuffix("_manual")
    if not input_dir.exists():
        print(f"입력 폴더가 없다: {input_dir}", file=sys.stderr)
        sys.exit(1)
    subject_dir.mkdir(parents=True, exist_ok=True)
    sys.exit(run(subject_dir, input_dir, settings=settings, limit=args.limit, steps=steps))


if __name__ == "__main__":
    main()
