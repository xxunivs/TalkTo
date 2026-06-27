from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import require_internal_request
from app.core.errors import FeatureNotImplementedError
from app.schemas.analysis import EnrichmentRequest, EnrichmentResponse
from app.schemas.memory import (
    MemorySegmentExtractionRequest,
    MemorySegmentExtractionResponse,
)
from app.schemas.transcript import TranscriptionRequest, TranscriptionResponse


router = APIRouter(tags=["analysis"])
InternalRequest = Annotated[None, Depends(require_internal_request)]


@router.post("/transcriptions", response_model=TranscriptionResponse)
def create_analysis_transcription(
    _request: TranscriptionRequest,
    _authorized: InternalRequest,
) -> TranscriptionResponse:
    raise FeatureNotImplementedError("Recording transcription analysis")


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
