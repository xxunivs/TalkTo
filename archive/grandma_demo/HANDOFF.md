# HANDOFF

## 프로젝트 / 작업 목표

`PersonaAI`는 TalkTo 사후 Phase의 할머니 페르소나 AI 서버다. FastAPI 서버가 백엔드(NestJS)에서 호출할 AI 기능을 제공한다.

현재 목표는 백엔드와 AI 서버의 연동 방식을 정리하고, 기억 시스템을 실제 서비스 구조에 맞게 연결하는 것이다.

주요 기능 상태:

- 텍스트 대화: `POST /ai/chat`
- 음성 대화: `POST /ai/voice-chat` (STT -> 답변 생성 -> TTS)
- STT 단독: `POST /ai/stt`
- TTS 단독: `POST /ai/tts`
- 임베딩 생성: `POST /ai/embed`
- 장기기억 RAG: `data/memories.json` + `data/memory_embeddings.json`
- 단기기억 자동 저장: `data/short_term_memories.json`

현재 데이터 기준:

- `data/memories.json`: 44개 장기기억 chunk
- `data/memory_embeddings.json`: 44개 임베딩
- `data/short_term_memories.json`: 7개 단기기억

## 최근 수정 / 생성 파일

최근 작업에서 의미 있게 수정된 파일:

- `/Users/univ/Desktop/TalkTo/PersonaAI/server.py`
  - `/ai/chat`에 `memories` 파라미터 추가.
  - 백엔드가 장기기억을 보내면 그 값을 사용하고, 없으면 AI 서버 내부 JSON 검색을 사용.
  - `/ai/embed` 추가. 백엔드가 새 장기기억/수정된 장기기억의 임베딩을 만들 때 호출 가능.

- `/Users/univ/Desktop/TalkTo/PersonaAI/llm/core.py`
  - `generate_response()`가 외부에서 주입된 `memory_text`를 받을 수 있도록 조정됨.
  - 백엔드 검색 장기기억과 AI 내부 검색 장기기억을 모두 프롬프트에 넣을 수 있게 됨.

- `/Users/univ/Desktop/TalkTo/PersonaAI/data/memories.json`
  - 기존 장기기억 일부 보강.
  - 새 장기기억 chunk 추가.
  - 37개에서 44개로 증가.

- `/Users/univ/Desktop/TalkTo/PersonaAI/data/memory_embeddings.json`
  - 44개 장기기억 기준으로 임베딩 재생성.

- `/Users/univ/Desktop/TalkTo/PersonaAI/data/할머니_분석.md`
  - 전사본 대비 빠진 내용 7개 보강.

- `/Users/univ/Desktop/TalkTo/PersonaAI/docs/backend_integration.md`
  - 백엔드 연동 엔드포인트 정리.
  - `/ai/chat`, `/ai/voice-chat` 중심으로 사용하도록 안내.
  - `/ai/embed`, `memories` 파라미터, 기존 44개 장기기억 이관 순서 추가.
  - 연동 순서를 `기존 44개 DB 이관 -> 벡터 검색 연결 -> 이후 새 장기기억 추가`로 수정.

- `/Users/univ/Desktop/TalkTo/PersonaAI/HANDOFF.md`
  - 이 파일. 다음 작업 인계를 위해 생성.

## 주요 결정과 이유

### 1. 백엔드는 데이터 저장/검색, AI 서버는 AI 처리 담당

장기적으로 권장하는 구조:

```text
백엔드: 대화/장기기억 DB 저장, pgvector 검색, 관리자 기능
AI 서버: LLM 답변 생성, 임베딩 생성, STT, TTS
```

이유:

- 장기기억 데이터는 백엔드 DB에서 관리하는 편이 서비스 운영, 관리자 수정, 사용자별 분리, 배포 안정성에 유리하다.
- AI 서버가 모든 장기기억을 JSON으로 들고 있으면 서버 여러 대 운영, 재배포, 데이터 백업/복구에서 불리하다.
- 다만 현재 규모에서는 AI 서버 내부 JSON 검색도 바로 동작하므로, DB 연동 전 임시 운영 방식으로 유지 가능하다.

### 2. 기존 44개 장기기억은 이미 임베딩이 있으므로 먼저 DB에 넣는 흐름이 우선

이전 논의에서 `/ai/embed`는 "새 장기기억 추가 시"만 강조되어 있었는데, 현재 당장 필요한 것은 기존 44개 장기기억을 백엔드 DB로 이관하는 방법이다.

정리된 순서:

1. `data/memories.json`과 `data/memory_embeddings.json`의 기존 44개를 백엔드 DB에 넣는다.
2. 백엔드 `retrieveMemories()`를 기존 키워드 검색에서 pgvector 검색으로 바꾼다.
3. 검색된 장기기억을 `/ai/chat`의 `memories` 파라미터로 전달한다.
4. 이후 관리자가 새 장기기억을 추가/수정할 때 `/ai/embed`를 호출해서 새 임베딩을 저장한다.

### 3. `/ai/chat`은 두 방식 모두 지원

`/ai/chat`은 다음 두 가지 방식으로 동작한다.

- `memories`가 있으면: 백엔드가 검색해서 보내준 장기기억 사용
- `memories`가 없으면: AI 서버 내부의 `memory_embeddings.json`에서 검색

이유:

- 백엔드 DB 연동이 아직 끝나지 않아도 AI 서버를 바로 테스트할 수 있다.
- 나중에 백엔드 pgvector 검색으로 전환해도 같은 endpoint를 계속 쓸 수 있다.

### 4. 단기기억은 일단 AI 서버 내부 JSON 유지

단기기억은 대화 중 자동으로 생기는 참고 기록이다. 현재는 AI 서버가 매 대화 후 중요도를 평가하고, 중요도 6 이상이면 `data/short_term_memories.json`에 저장한다.

논의한 선택지:

- 방법 A: AI 서버가 단기기억을 계속 자체 관리
- 방법 B: AI 서버가 새 단기기억 후보를 응답에 포함하고, 백엔드가 DB에 저장

현재 판단:

- 지금은 방법 A가 단순하고 빠르다.
- 단기기억은 관리자 수정 대상이 아니고, 현재 규모에서는 JSON 관리로 충분하다.
- 서비스가 커져서 서버 여러 대, 사용자별 단기기억, 재배포 안정성이 중요해지면 방법 B로 전환한다.

### 5. 용어 기준

명세서의 공식 용어인 "장기기억 / 단기기억"을 기준으로 맞췄다. 코드와 문서도 기존 파일명/API 필드를 유지한다.

- `memories.json`
- `short_term_memories.json`
- API 필드 `memories`

의미:

- 장기기억: 할머니 AI가 처음부터 알고 있어야 하는 배경 지식.
- 단기기억: 대화 중 새로 알게 된 최근 사실이나 참고 기록.

## 아직 안 끝난 일 / 다음 단계

1. 백엔드 기존 44개 장기기억 이관
   - `data/memories.json` + `data/memory_embeddings.json`을 백엔드 DB에 넣는 스크립트 또는 수동 절차 필요.
   - 백엔드 `Memory`, `MemoryEmbedding` 스키마와 AI 데이터 구조 매핑 확인 필요.
   - 이미 만들어진 임베딩을 import할지, `/ai/embed`로 다시 생성할지 결정.

2. 백엔드 검색 연결
   - `retrieveMemories()` 또는 기존 `retrieveMemories()`를 pgvector 검색으로 변경.
   - 검색 결과를 `/ai/chat`의 `memories` 파라미터로 전달.
   - `retrieved_memory_ids`를 메시지/로그에 저장할지 결정.

3. 대화 히스토리 전달
   - 백엔드가 최근 메시지 예: 최근 20턴을 `/ai/chat`의 `history`로 전달해야 함.
   - 현재 문서에 예시는 있음. 실제 백엔드 구현 확인 필요.

4. 음성 대화 연결
   - 백엔드는 `POST /ai/voice-chat`을 호출하는 방식이 권장됨.
   - `tts_audio_base64`를 mp3로 디코딩해서 S3 등에 저장하고 signed URL을 만들어야 함.
   - TTS 실패 시 텍스트 fallback 처리 필요.

5. 문서 정합성 수정
   - `README.md` 패키지 설치 명령은 현재 서버 기능 기준으로 부족할 수 있음. `fastapi`, `uvicorn`, `python-multipart`, `requests`, `numpy`, `elevenlabs` 관련 의존성 확인 필요.

## 알려진 주의점 / 버그 가능성

- `README.md`의 설치 명령은 최소 GPT 테스트 기준이고, FastAPI 서버/STT/TTS/RAG 전체 실행 기준으로는 부족할 수 있다.
- `/ai/tts`는 실패 시 FastAPI response model 없이 dict를 반환하고, 성공 시 `audio/mpeg` 바이트를 반환한다. 백엔드에서 성공/실패 응답 타입 차이를 처리해야 한다.
- `/ai/voice-chat`은 현재 AI 서버 내부 JSON 검색만 사용한다. 백엔드가 검색한 `memories`를 음성 대화에 주입하는 구조는 아직 없다.
- 단기기억이 JSON 파일에 저장되므로 서버 여러 대 운영, 컨테이너 재배포, 사용자별 분리에는 취약하다.
- 기존 대화에서 Claude Code 호출이 컨텍스트 초과로 `Prompt is too long`을 반복했다. 같은 방식으로 전체 대화 컨텍스트를 다시 Claude에 던지면 실패할 수 있다.

## 시도했다가 안 된 접근 / 반복 금지

- 이전 대화에서 컨텍스트가 너무 커진 상태로 Claude Code에 계속 요청했고, `Prompt is too long`이 반복됨.
- `/compact`를 여러 번 시도했지만 같은 요청이 계속 실패함.
- 해결 방식은 전체 대화 컨텍스트를 다시 모델에 밀어 넣는 것이 아니라, repo 파일과 세션 로그에서 필요한 정보만 읽고 작은 단위로 정리하는 것이다.

## 실행 / 테스트 명령어

AI 서버 환경 세팅:

```bash
cd /Users/univ/Desktop/TalkTo/PersonaAI
python3.11 -m venv venv
source venv/bin/activate
pip install openai python-dotenv fastapi uvicorn python-multipart requests numpy
```

환경 변수:

```text
OPENAI_API_KEY=...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=lTmcD2Kp43w1lHsMQmq7
```

서버 실행:

```bash
cd /Users/univ/Desktop/TalkTo/PersonaAI
source venv/bin/activate
uvicorn server:app --port 8000
```

API 문서:

```text
http://localhost:8000/docs
```

LLM 코어 터미널 테스트:

```bash
cd /Users/univ/Desktop/TalkTo/PersonaAI
source venv/bin/activate
python -m llm.core
```

임베딩 재생성:

```bash
cd /Users/univ/Desktop/TalkTo/PersonaAI
source venv/bin/activate
python -m memory.embeddings --build
```

현재 프롬프트 평가:

```bash
cd /Users/univ/Desktop/TalkTo/PersonaAI
source venv/bin/activate
python -m prompt.eval_run
```

헬스 체크:

```bash
curl http://localhost:8000/health
```

텍스트 대화 예시:

```bash
curl -X POST http://localhost:8000/ai/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"할머니 안녕","history":[]}'
```

임베딩 생성 예시:

```bash
curl -X POST http://localhost:8000/ai/embed \
  -H "Content-Type: application/json" \
  -d '{"text":"불고기 만들 때 간장 한 숟가락을 넣는다."}'
```

## 다음 작업 추천 순서

1. 백엔드 repo에서 `Memory`, `MemoryEmbedding`, `retrieveMemories()`, `sendTextMessage()`, `sendVoiceMessage()` 현재 구현을 확인한다.
2. 기존 44개 장기기억 import 방식과 pgvector 검색 구현을 진행한다.
3. 백엔드에서 `/ai/chat` 호출 시 `memories`와 `history`를 전달하도록 연결한다.
4. 음성 대화는 `/ai/voice-chat` 기준으로 연결하고, TTS 결과 저장/fallback 처리를 구현한다.
5. `README.md`의 의존성 설치 명령을 실제 서버 실행 기준으로 보강한다.
