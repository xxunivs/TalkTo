import unittest


class SchemaImportTest(unittest.TestCase):
    def test_schema_and_provider_interfaces_import_without_calls(self) -> None:
        from app.providers.stt import STTProvider, STTResult
        from app.schemas.context import IntakeContext, SubjectContext
        from app.schemas.embeddings import EmbeddingRequest, EmbeddingResponse
        from app.schemas.memory import MemorySegment
        from app.schemas.persona import PersonaResponseRequest
        from app.schemas.recording import (
            RecordingAnalysisRequest,
            RecordingAnalysisResponse,
        )
        from app.schemas.transcript import TranscriptionRequest
        from app.schemas.voice import SpeechSynthesisRequest

        imported = (
            STTProvider,
            STTResult,
            SubjectContext,
            IntakeContext,
            EmbeddingRequest,
            EmbeddingResponse,
            MemorySegment,
            RecordingAnalysisRequest,
            RecordingAnalysisResponse,
            PersonaResponseRequest,
            TranscriptionRequest,
            SpeechSynthesisRequest,
        )
        self.assertEqual(len(imported), 12)


if __name__ == "__main__":
    unittest.main()
