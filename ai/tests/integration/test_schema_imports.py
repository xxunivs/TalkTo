import unittest


class SchemaImportTest(unittest.TestCase):
    def test_schema_and_provider_interfaces_import_without_calls(self) -> None:
        from app.providers.elevenlabs import ElevenLabsProvider
        from app.providers.openai import OpenAIProvider
        from app.schemas.analysis import EnrichmentRequest, EnrichmentResponse
        from app.schemas.embeddings import EmbeddingRequest, EmbeddingResponse
        from app.schemas.memory import MemorySegmentExtractionRequest
        from app.schemas.persona import PersonaResponseRequest
        from app.schemas.transcript import TranscriptionRequest
        from app.schemas.voice import SpeechSynthesisRequest

        imported = (
            OpenAIProvider,
            ElevenLabsProvider,
            EnrichmentRequest,
            EnrichmentResponse,
            EmbeddingRequest,
            EmbeddingResponse,
            MemorySegmentExtractionRequest,
            PersonaResponseRequest,
            TranscriptionRequest,
            SpeechSynthesisRequest,
        )
        self.assertEqual(len(imported), 10)


if __name__ == "__main__":
    unittest.main()
