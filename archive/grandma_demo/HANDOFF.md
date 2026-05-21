# HANDOFF

## 프로젝트 역할

`PersonaAI`는 TalkTo 사후 Phase의 할머니 페르소나 AI 서버다.
FastAPI로 백엔드가 호출할 AI 기능을 제공한다.

현재 제공 API:

- `GET /health`
- `POST /ai/chat`
- `POST /ai/embed`
- `POST /ai/memory/extract`
- `POST /ai/stt`
- `POST /ai/tts`
- `POST /ai/voice-chat`

## 현재 배포 상태

- AI Render URL: `https://talkto-persona-ai.onrender.com`
- BE Render URL: `https://talkto-personaai-be.onrender.com`
- BE health: `https://talkto-personaai-be.onrender.com/api/v1/health`
- BE DB: Neon PostgreSQL
- TTS mp3: private Cloudflare R2 object + signed playback URL
- AI Render 운영 기본값: `PERSONA_ENABLE_LOCAL_SHORT_TERM=false`

## 현재 연동 기준

### 역할 분리

```text
BE: 대화/기억 DB 저장, 기억 검색, 관리자 기능, TTS 파일 저장
AI: LLM 답변, 임베딩, STT, TTS, 단기기억 후보 판단
```

### 텍스트

1. BE가 사용자 메시지와 최근 history를 준비한다.
2. BE가 `/ai/embed`로 query embedding을 만든다.
3. BE가 DB memory를 검색한다.
4. BE가 `/ai/chat`에 `message`, `history`, `memories`를 보낸다.
5. AI는 BE가 보낸 `memories[].id`를 `retrieved_memory_ids`로 돌려준다.

운영 응답 ID 기준은 BE `Memory.id` UUID다. AI legacy ID는 tag로만 보존한다.

### 음성

운영 BE 흐름은 단계 분리 호출이다.

1. `/ai/stt`
2. BE memory search
3. `/ai/chat`
4. `/ai/tts`
5. BE가 mp3를 R2에 저장하고 signed URL을 반환

`/ai/voice-chat`은 AI 단독 프로토타입과 데모용으로 남아 있다.

### 단기기억

AI는 `/ai/memory/extract`로 저장 후보를 판단한다. 운영 저장 기준은 BE DB다.
로컬 `data/short_term_memories.json`은 AI 단독 테스트와 eval 용도다.

## 기억 데이터 상태

- `data/memories.json`: legacy 장기기억 44개 원본
- `data/memory_embeddings.json`: AI 로컬 RAG용 legacy embedding
- `data/backend_memory_import.json`: BE `Memory` import payload 44개
- Neon DB: legacy 장기기억 44개 import 완료

`backend_memory_import.json` 기준:

- `memoryType`: `LONG_TERM`
- `tags`: `category:*`, `legacy_id:*`
- `relatedPeople`: source title/content에 명시된 인물만 보수적으로 추출
- embedding: BE import/create 흐름이 `/ai/embed`로 생성

export 경로:

```bash
python tools/export_backend_memories.py
python3 -m unittest tools/test_export_backend_memories.py
```

## 보안

- `AI_SERVER_TOKEN`이 설정되면 `/ai/*`는 `X-AI-Server-Token`을 요구한다.
- `/health`는 토큰 없이 접근 가능하다.
- 실제 token 값은 코드, GitHub issue, 문서에 쓰지 않는다.

아직 남은 운영 작업은 BE Render와 AI Render에 같은 token을 넣고 token path를
재검증하는 것이다.

## 남은 결정

현재 backlog 기준은 BE GitHub issue #1/#2다.

1. signed URL 만료 뒤 과거 음성 재생을 지원할지
2. memory search를 현재 `float8[]` + TypeScript cosine similarity에서 pgvector로 바꿀지
3. 운영 에러 추적을 `SystemLog`만으로 갈지 외부 도구를 붙일지
4. 인증/초대 정책과 추가 관리자 KPI 범위

## 실행

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --port 8000
```

로컬 API 문서:

```text
http://localhost:8000/docs
```

## 문서 기준

- 현재 배포/연동 상태: `docs/ai_backend_integration_ver4.md`
- 과거 연동 기록: `docs/ai_backend_integration_ver1.md` ~ `ver3.md`
- 구조 설명: `CODE_STRUCTURE.md`
- 파일 역할 설명: `CODE_EXPLANATION.md`
