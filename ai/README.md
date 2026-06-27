# TalkTo APP AI

TalkTo 앱에서 사용하는 통합 AI 서비스/worker 저장소입니다.

기본 호출 흐름은 `TalkTo App → TalkTo Backend → TalkTo APP AI`입니다. APP_AI는 BE가 전달한 입력을 처리해 구조화된 결과를 반환하며, 사용자 인증, DB 저장, 결제·동의 판단, 녹음 원본 영구 저장은 담당하지 않습니다.

## 역할

- 녹음 전사 및 분석 결과 생성
- Persona 응답과 memory/RAG 처리 기반 제공
- STT/TTS provider 연동
- BE 저장 구조에 맞는 JSON 반환

## 실행 방법

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

API 문서: `http://localhost:8000/docs`

테스트:

```bash
python -m unittest discover -s tests -v
```

## 환경변수

전체 항목은 `.env.example`을 참고합니다.

- 공통: `APP_ENV`, `LOG_LEVEL`, `AI_SERVER_TOKEN`
- OpenAI: `OPENAI_API_KEY`, Chat/Analysis/STT/Embedding 모델 설정
- Embedding: `OPENAI_EMBEDDING_DIMENSIONS`
- TTS: `TTS_PROVIDER`, ElevenLabs API key/model/voice ID
- 처리 제한: `MAX_AUDIO_BYTES`, `TEMP_DIR`

실제 API key와 token은 저장소에 커밋하지 않습니다.

## 현재 구현 범위

- FastAPI 기본 구조
- 중앙 환경설정
- OpenAI/ElevenLabs provider interface
- 요청/응답 schema
- 내부 token 검증 기반
- `GET /health`, `GET /ready`
- import, config, endpoint 테스트

실제 OpenAI/ElevenLabs 호출과 녹음 분석, Persona, STT/TTS 기능은 아직 구현하지 않았습니다.

## Placeholder endpoint

다음 endpoint는 현재 HTTP 501을 반환합니다.

- `POST /v1/persona/responses`
- `POST /v1/persona/memory-candidates`
- `POST /v1/analysis/transcriptions`
- `POST /v1/analysis/memory-segments`
- `POST /v1/analysis/enrichments`
- `POST /v1/embeddings`
- `POST /v1/voice/transcriptions`
- `POST /v1/voice/speech`

## 보안 및 로깅 원칙

- App은 APP_AI를 직접 호출하지 않고 BE를 통해 호출합니다.
- 기능 endpoint는 `AI_SERVER_TOKEN` 기반 내부 인증을 사용합니다.
- 로그에는 job ID, recording ID, 처리 단계, 상태, 지연시간 같은 운영 메타데이터만 기록합니다.
- 사용자 원문, 전사문, 기억 내용, 음성, 민감정보를 로그에 남기지 않습니다.
- API key, token, 인증 헤더, signed URL, provider 요청·응답 원문을 로그에 남기지 않습니다.
