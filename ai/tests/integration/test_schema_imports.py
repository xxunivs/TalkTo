import unittest


class SchemaImportTest(unittest.TestCase):
    def test_schema_and_provider_interfaces_import_without_calls(self) -> None:
        from infra.external_api.stt import STTProvider, STTResult
        from infra.schema.subject import IntakeContext, SubjectContext
        from infra.schema.embedding import EmbeddingRequest, EmbeddingResponse
        from infra.schema.memory import MemorySegment
        from infra.schema.persona import PersonaResponseRequest
        from infra.schema.recording_analysis import (
            RecordingAnalysisRequest,
            RecordingAnalysisResponse,
        )
        from infra.schema.transcript import TranscriptionRequest
        from infra.schema.voice import SpeechSynthesisRequest

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
