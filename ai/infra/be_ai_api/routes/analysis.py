from typing import Annotated

from fastapi import APIRouter, Depends

from infra.be_ai_api.dependencies import SettingsDependency, require_internal_request
from infra.be_ai_api.recording_analysis import run_recording_analysis
from pipeline.s1_transcription.a_transcript import transcribe_recording
from infra.schema.recording_analysis import RecordingAnalysisRequest, RecordingAnalysisResponse
from infra.schema.transcript import TranscriptionRequest, TranscriptionResponse
from infra.external_api.stt import create_stt_provider


router = APIRouter(tags=["analysis"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/transcriptions", response_model=TranscriptionResponse)
def create_analysis_transcription(
    request: TranscriptionRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> TranscriptionResponse:
    provider = create_stt_provider(settings)
    return transcribe_recording(request, settings=settings, provider=provider)


@router.post("/recording", response_model=RecordingAnalysisResponse)
def create_recording_analysis(
    request: RecordingAnalysisRequest,
    _authorized: InternalRequest,
    settings: SettingsDependency,
) -> RecordingAnalysisResponse:
    """녹음 1건 통합 분석 — 3-A 기억 + 요약 + 태그 + 말투(④) + 안전플래그를 한 번에."""
    return run_recording_analysis(request, settings=settings)
