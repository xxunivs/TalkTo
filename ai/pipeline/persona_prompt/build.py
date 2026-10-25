"""재료 -> 명세 -> 프롬프트 진입점 (for_assembly: BE 요청만, for_local: 로컬 산출물)"""

from __future__ import annotations

import json
from pathlib import Path

from app.schema.persona import PersonaAssemblyRequest
from app.schema.subject import IntakeContext, SubjectContext
from app.schema.transcript import TranscriptSegment
from pipeline.persona_prompt.from_intake import (
    apply_death_toggle,
    merge_recording,
    spec_from_intake,
)
from pipeline.persona_prompt.from_interview import apply_candidates
from pipeline.persona_prompt.from_transcript import card_from_utterances, style_from_segments
from pipeline.persona_prompt.layers import ShotsMode
from pipeline.persona_prompt.render import render_layered_prompt
from pipeline.persona_prompt.spec import PersonaSpec

TOGGLE = {"intake": None, "off": False, "on": True}


def for_assembly(
    request: PersonaAssemblyRequest,
    default_death_talk: str = "intake",
    style_card_min_utts: int = 30,
) -> PersonaSpec:
    spec = spec_from_intake(request.subject_context, request.intake_context)
    if request.death_talk is not None:
        apply_death_toggle(spec, request.death_talk, "request")
    else:
        apply_death_toggle(spec, TOGGLE[default_death_talk], "setting")
    spec.style.card = card_from_utterances(request.speech_examples, min_utts=style_card_min_utts)
    if spec.style.card:
        spec.add_material("transcript")
    return spec


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_segments(subject_dir: Path) -> dict[str, list[TranscriptSegment]]:
    raw = _read(subject_dir / "s1_transcription" / "a_transcript.json") or {}
    return {name: [TranscriptSegment(**s) for s in segs] for name, segs in raw.items()}


def for_local(
    subject_dir: Path,
    input_dir: Path,
    *,
    intake_path: Path | None = None,
    labels: dict[str, str | None] | None = None,
    interview: dict | None = None,
    max_shots: int = 30,
    shot_exclude=None,
    death_talk: bool | None = None,
    subject_name: str | None = None,
) -> tuple[PersonaSpec, list[str]]:
    """명세와 사람 확인용 판단, 출처 기록"""
    made = subject_dir / "s2_extraction" / "b_intake_prompt.json"
    src = intake_path or (made if made.exists() else input_dir / "persona_intake.json")
    payload = _read(src)
    if payload is None:
        if intake_path is not None:
            raise FileNotFoundError(src)
        # 설문 없이 녹음만 있는 경우: 이름으로 시작, 녹음 재료로 보충
        spec = spec_from_intake(
            SubjectContext(subject={"name": subject_name or subject_dir.name}), None
        )
        log = ["설문: 없음(녹음 재료만)"]
    else:
        spec = spec_from_intake(
            SubjectContext(**payload["subjectContext"]),
            IntakeContext(**payload["intakeContext"]) if payload.get("intakeContext") else None,
        )
        log = [f"설문: {src}"]
    apply_death_toggle(spec, death_talk, "cli")
    merge_recording(spec, _read(subject_dir / "s2_extraction" / "d_recording_prompt.json"))
    segs = load_segments(subject_dir)
    labels = (
        labels
        if labels is not None
        else (_read(subject_dir / "s1_transcription" / "b_speaker_id.json") or {})
    )
    if segs:
        spec.style.card, spec.style.shots = style_from_segments(
            segs, labels, max_shots=max_shots, exclude=shot_exclude
        )
        if spec.style.card or spec.style.shots:
            spec.add_material("transcript")
        known = sum(1 for k in segs if labels.get(k))
        log.append(
            f"전사: 통화 {len(segs)}개 중 대상자 라벨 있는 것 {known}개 → 카드 {'있음' if spec.style.card else '없음'}, "
            f"주고받기 {len(spec.style.shots)}개"
        )
    if interview:
        applied = apply_candidates(spec, interview)
        log.append(f"인터뷰 후보 적용 {len(applied)}건")
        log += ["  " + x for x in applied]
    return spec, log


def prompt(
    spec: PersonaSpec, *, include_common: bool = True, shots_mode: ShotsMode = "system"
) -> str:
    return render_layered_prompt(spec, include_common=include_common, shots_mode=shots_mode)
