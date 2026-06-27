# TalkTo APP AI

TalkTo 앱에서 사용하는 통합 AI 서비스/worker 저장소입니다.

기본 호출 흐름은 다음과 같습니다.

```text
TalkTo App → TalkTo Backend → TalkTo APP AI
                           → Backend DB 저장
                           → Archive / Memories / Voice Persona
```

## 역할

- BE가 전달한 입력을 AI로 처리
- 녹음 분석, Persona 응답, STT/TTS 기능 제공
- BE 저장 구조에 맞는 JSON 반환
- OpenAI, ElevenLabs 등 provider 연동

담당하지 않는 범위:

- 사용자 인증과 권한 확인
- DB 읽기/쓰기
- 결제, 동의, entitlement 판단
- 녹음 원본 영구 저장
- App의 직접 호출

## 현재 구현 상태

- FastAPI 기본 구조
- 중앙 환경변수 설정
- OpenAI/ElevenLabs provider interface
- 요청/응답 schema
- 내부 `AI_SERVER_TOKEN` 검증 기반
- `/health`, `/ready`
- 최소 import 및 endpoint 테스트

실제 OpenAI/ElevenLabs 호출과 녹음 분석, Persona, STT/TTS 기능은 아직 구현하지 않았습니다. 기능 endpoint는 HTTP 501을 반환합니다.

## 실행

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

API 문서: `http://localhost:8000/docs`

## Endpoint

구현됨:

- `GET /health`
- `GET /ready`

Placeholder:

- `POST /v1/persona/responses`
- `POST /v1/persona/memory-candidates`
- `POST /v1/analysis/transcriptions`
- `POST /v1/analysis/memory-segments`
- `POST /v1/analysis/enrichments`
- `POST /v1/embeddings`
- `POST /v1/voice/transcriptions`
- `POST /v1/voice/speech`

## 환경변수

필요한 값은 `.env.example`을 참고합니다. Chat, Analysis, STT, Embedding 모델은 용도별 환경변수로 분리합니다.

현재 BE pgvector 계약에 맞춰 `OPENAI_EMBEDDING_DIMENSIONS=1536`을 기본값으로 사용합니다.

## 로깅 원칙

로그에는 job ID, recording ID, 처리 단계, 상태, 지연시간 같은 운영 메타데이터만 기록합니다.

다음 데이터는 로그에 남기지 않습니다.

- 사용자 원문과 Persona 응답
- 전사문과 기억 내용
- 음성 데이터
- 민감정보
- signed URL, 인증 헤더, API key, 내부 token
- provider 요청/응답 원문

## 테스트

실제 provider 호출 없이 실행됩니다.

```bash
python -m unittest discover -s tests -v
```
