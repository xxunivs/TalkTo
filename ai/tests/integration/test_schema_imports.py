import unittest


class SchemaImportTest(unittest.TestCase):
    def test_schema_and_provider_interfaces_import_without_calls(self) -> None:
        from app.external_service.stt import STTProvider, STTResult
        from app.data_schema.context import IntakeContext, SubjectContext
        from app.data_schema.embeddings import EmbeddingRequest, EmbeddingResponse
        from app.data_schema.memory import MemorySegment
        from app.data_schema.persona import PersonaResponseRequest
        from app.data_schema.recording import (
            RecordingAnalysisRequest,
            RecordingAnalysisResponse,
        )
        from app.data_schema.transcript import TranscriptionRequest
        from app.data_schema.voice import SpeechSynthesisRequest

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
