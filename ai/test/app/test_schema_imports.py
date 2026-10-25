import unittest


class SchemaImportTest(unittest.TestCase):
    def test_schema_and_provider_interfaces_import_without_calls(self) -> None:
        from app.external_api.stt import STTProvider, STTResult
        from app.schema.embedding import EmbeddingRequest, EmbeddingResponse
        from app.schema.memory import MemorySegment
        from app.schema.persona import PersonaResponseRequest
        from app.schema.recording_analysis import (
            RecordingAnalysisRequest,
            RecordingAnalysisResponse,
        )
        from app.schema.subject import IntakeContext, SubjectContext
        from app.schema.transcript import TranscriptionRequest
        from app.schema.voice import SpeechSynthesisRequest

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
