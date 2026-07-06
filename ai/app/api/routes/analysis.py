from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import SettingsDependency, require_internal_request
from app.core.errors import FeatureNotImplementedError
from app.providers.stt import create_stt_provider
from app.schemas.analysis import EnrichmentRequest, EnrichmentResponse
from app.schemas.memory import (
    MemorySegmentExtractionRequest,
    MemorySegmentExtractionResponse,
)
from app.schemas.transcript import TranscriptionRequest, TranscriptionResponse
from app.services.transcription import transcribe_recording


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


@router.post("/memory-segments", response_model=MemorySegmentExtractionResponse)
def create_memory_segments(
    _request: MemorySegmentExtractionRequest,
    _authorized: InternalRequest,
) -> MemorySegmentExtractionResponse:
    raise FeatureNotImplementedError("Recording memory segment extraction")


@router.post("/enrichments", response_model=EnrichmentResponse)
def create_enrichments(
    _request: EnrichmentRequest,
    _authorized: InternalRequest,
) -> EnrichmentResponse:
    raise FeatureNotImplementedError("Recording enrichment analysis")
