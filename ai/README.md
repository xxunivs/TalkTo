# TalkTo APP AI

`TalkTo_APP_AI` is the integrated AI service and worker foundation for the TalkTo app.

## System boundary

The default request flow is:

```text
TalkTo App -> TalkTo Backend -> TalkTo APP AI
                              -> Backend persistence
                              -> Archive / Memories / Voice Persona
```

This service processes inputs supplied by the backend and returns validated AI result JSON. It is intended to support recording analysis, Persona responses, memory/RAG context handling, STT, TTS, and Voice Persona workflows.

This service does **not** own:

- end-user authentication or authorization;
- database reads or writes;
- consent, payment, or entitlement decisions;
- permanent storage of original recordings;
- direct calls from the TalkTo App.

The backend remains responsible for access control, storage, job orchestration, retrieval, and persistence. Temporary files created during processing must be removed after each request.

## Current scope

This initial repository contains only:

- FastAPI application wiring;
- central environment configuration;
- OpenAI and ElevenLabs provider interfaces;
- request and response schema foundations;
- `/health` and `/ready` endpoints;
- placeholder feature endpoints that return HTTP 501;
- import, configuration, health, readiness, and schema tests.

No OpenAI or ElevenLabs call is implemented. No recording analysis, Persona response, STT, TTS, embedding, or memory extraction is performed yet.

## Local setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

API documentation is available at `http://localhost:8000/docs` while the service is running.

## Endpoints

Implemented:

- `GET /health`
- `GET /ready`

Placeholders returning HTTP 501:

- `POST /v1/persona/responses`
- `POST /v1/persona/memory-candidates`
- `POST /v1/analysis/transcriptions`
- `POST /v1/analysis/memory-segments`
- `POST /v1/analysis/enrichments`
- `POST /v1/embeddings`
- `POST /v1/voice/transcriptions`
- `POST /v1/voice/speech`

When `AI_SERVER_TOKEN` is configured, feature endpoints require the same value in the `X-AI-Server-Token` header. Health and readiness endpoints remain available without that header.

## Logging and sensitive data

Logs must contain operational metadata only, such as request ID, job ID, recording ID, stage, status, latency, provider, and model name.

Never log:

- raw user messages or Persona responses;
- transcripts or memory text;
- uploaded audio or derived voice data;
- health, family, financial, memorial, or other sensitive content;
- signed URLs, authorization headers, API keys, or internal tokens;
- raw provider request or response bodies.

Errors exposed to callers must use stable codes and safe messages. Provider errors must be sanitized before logging or returning them.

## Configuration

Configuration is loaded centrally from environment variables and an optional local `.env` file. Secrets are represented with Pydantic `SecretStr` values so accidental object rendering does not expose them.

The initial model settings are separated by responsibility:

- `OPENAI_CHAT_MODEL`: Persona response generation
- `OPENAI_ANALYSIS_MODEL`: extraction, classification, summarization, and structured analysis
- `OPENAI_STT_MODEL`: speech transcription
- `OPENAI_EMBEDDING_MODEL`: search embeddings

`OPENAI_EMBEDDING_DIMENSIONS` defaults to `1536` to match the current backend pgvector contract. Changing it requires backend schema and re-indexing coordination.

## Tests

Standard-library test command, requiring no provider credentials:

```bash
python -m unittest discover -s tests -v
```

After installing development dependencies, pytest can also run the suite:

```bash
pytest
```
