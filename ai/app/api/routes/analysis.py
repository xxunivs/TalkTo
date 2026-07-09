from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import SettingsDependency, require_internal_request
from app.pipeline.analysis.recording import run_recording_analysis
from app.pipeline.transcription.service import transcribe_recording
from app.schemas.recording import RecordingAnalysisRequest, RecordingAnalysisResponse
from app.schemas.transcript import TranscriptionRequest, TranscriptionResponse
from app.providers.stt import create_stt_provider


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
