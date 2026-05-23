# AI ↔ 백엔드 연동 체크리스트

이 문서는 `PersonaAI` AI 서버와 `TalkTo_PersonaAI_BE` 백엔드의 현재 연동 상태를 기준으로, **이미 해결된 것**과 **백엔드 담당자와 다음으로 맞출 것**만 정리한다.

기준 우선순위:

1. `PersonaAI_Spec`
2. 현재 `TalkTo_PersonaAI_BE` `main` 코드
3. 현재 `PersonaAI` 코드

---

## 현재 확정된 것

### 1. AI 서버 배포 위치

- Render URL: `https://talkto-persona-ai.onrender.com`
- 헬스체크: `GET /health`
- 확인 결과:
  - `/health` 정상
  - `/ai/embed` 정상
  - `/ai/chat` 정상

백엔드 환경변수:

```env
AI_SERVER_URL=https://talkto-persona-ai.onrender.com
AI_SERVER_TIMEOUT_MS=45000
```

`AI_SERVER_TIMEOUT_MS`는 첫 `/ai/chat` 호출이 약 37초 걸린 것을 기준으로 45초로 잡는다. Render free 인스턴스는 cold start가 있을 수 있다.

---

### 2. 텍스트 채팅 + RAG 기본 계약

백엔드 최신 `main`에는 다음 흐름이 들어와 있다.

1. 사용자 메시지 저장
2. 최근 대화 history 조회
3. `/ai/embed`로 query embedding 생성
4. 백엔드 DB의 `MemoryEmbedding.embedding`과 cosine similarity 계산
5. 관련 `Memory`를 `/ai/chat`의 `memories`로 전달
6. AI 응답 `content`, `retrieved_memory_ids`, `latency_ms` 저장
7. `MessageMemoryRef` 저장

AI 서버 `/ai/chat` 요청:

```json
{
  "message": "할머니 안녕",
  "history": [
    {"role": "user", "content": "이전 메시지"},
    {"role": "assistant", "content": "이전 답변"}
  ],
  "memories": [
    {
      "id": "백엔드 Memory UUID",
      "title": "불고기",
      "content": "불고기는 간장과 마늘을 넣고..."
    }
  ]
}
```

AI 서버 `/ai/chat` 응답:

```json
{
  "content": "어. 잘 있었어?",
  "retrieved_memory_ids": ["백엔드 Memory UUID"],
  "latency_ms": 2100
}
```

현재 AI 서버는 백엔드가 `memories: []`를 보낸 경우 내부 `data/memories.json` fallback을 하지 않는다. 빈 배열은 "검색된 기억 없음"으로 처리한다.

---

### 3. 기억 ID 기준

현재 연동 기준은 **백엔드 `Memory.id` UUID**이다.

- 백엔드가 `/ai/chat`에 `memories[].id`로 UUID를 전달한다.
- AI 서버는 받은 ID를 그대로 `retrieved_memory_ids`로 반환한다.
- 백엔드는 응답 ID를 요청에 넣었던 memory UUID 목록으로 필터링한다.
- `Message.retrievedMemoryIds`, `MessageMemoryRef.memoryId`는 백엔드 UUID 기준으로 저장된다.

기존 AI legacy ID(`recipe_bulgogi` 등)는 운영 응답 ID 기준으로 쓰지 않는다.

---

### 4. 임베딩 검색 방식

백엔드는 MVP 기준으로 다음 방식을 이미 구현했다.

- `MemoryEmbedding.embedding`: `float8[]`
- 검색 방식: TypeScript cosine similarity
- query embedding 실패 시 keyword fallback
- memory 생성/수정/reembed 시 `/ai/embed` 호출

따라서 pgvector 도입은 현재 MVP 필수 논의에서 제외한다. 성능 문제가 확인되면 이후 변경한다.

---

## 다음으로 맞출 것

### 1. 기존 44개 장기기억을 백엔드 DB로 import

현재 AI 쪽 원본:

- `data/memories.json`
- `data/memory_embeddings.json`

백엔드 기준 저장 위치:

- `memories`
- `memory_embeddings`

맞출 내용:

- `memoryType`: 기존 `category`를 그대로 쓸지, `LONG_TERM`으로 통일하고 category는 tag로 둘지
- legacy ID 보존 위치: `tags`에 `legacy_id:recipe_bulgogi` 형태로 둘지
- 임베딩은 기존 `memory_embeddings.json`을 넣을지, 백엔드가 `/ai/embed`로 재생성할지

현재 백엔드 구현상 가장 단순한 방식은 `Memory`를 생성하고 백엔드의 기존 `rebuildEmbeddings()`가 `/ai/embed`로 새 임베딩을 만들게 하는 것이다.

---

### 2. 단기기억 저장 흐름

Render 운영 AI 서버는 로컬 `data/short_term_memories.json` 저장을 끈 상태다.

```env
PERSONA_ENABLE_LOCAL_SHORT_TERM=false
```

따라서 단기기억도 백엔드 DB에 저장되어야 한다. 현재 백엔드에는 자동 단기기억 추출/저장 흐름이 아직 없다.

맞출 내용:

- 단기기억도 `Memory` row로 저장할지
- `memoryType` 표준값을 `SHORT_TERM`으로 둘지
- 어느 시점에 저장할지: assistant 답변 생성 직후
- 누가 중요도 판단을 할지: AI 서버가 후보를 반환하고 백엔드가 저장할지
- 사용자/대화별 단기기억 범위를 어떻게 분리할지

AI 서버 쪽에서 추가로 제공할 수 있는 계약:

```http
POST /ai/memory/extract
```

요청:

```json
{
  "user_message": "나 다음 달에 이사해",
  "assistant_message": "어디로 가는데?"
}
```

응답:

```json
{
  "saved": true,
  "importance": 8,
  "category": "일상",
  "summary": "향이가 다음 달에 이사한다고 말했다.",
  "reason": "다음 대화에서 이어 물어볼 수 있는 개인 일정이다."
}
```

이 엔드포인트는 후보 판단만 하고, 실제 저장은 백엔드 `Memory` DB가 담당하는 구조가 맞다.

---

### 3. 음성 처리

명세 기준:

- Push-to-talk 입력
- STT 결과를 사용자 메시지로 표시
- AI 답변 생성
- ElevenLabs TTS 생성
- 음성 먼저 자동 재생
- 음성 재생 후 assistant 텍스트 reveal

현재 백엔드:

- `sendVoiceMessage()` 구조와 `VoiceArtifact`는 있다.
- 하지만 실제 `/ai/stt`, `/ai/tts`, `/ai/voice-chat` 호출은 아직 붙어 있지 않다.
- 현재 `ttsAudioUrl`은 placeholder 경로다.

맞출 내용:

- 내부 흐름을 `/ai/voice-chat` 단일 호출로 할지
- `/ai/stt` → `/ai/chat` → `/ai/tts` 분리 호출로 할지
- TTS mp3 저장 위치: S3 또는 다른 스토리지
- signed URL 정책
- STT/TTS 실패 시 저장할 `VoiceArtifact` 상태

---

### 4. 서버 간 인증

현재 Render AI 서버 URL은 public URL이다. 지금은 백엔드가 호출할 수 있도록 별도 인증 없이 열려 있다.

맞출 내용:

- MVP 테스트 동안 인증 없이 갈지
- `X-AI-Server-Token` 같은 shared secret header를 둘지
- Render와 백엔드 양쪽 환경변수에 같은 토큰을 넣을지

공개 URL이므로 장기 운영 전에는 shared secret 방식이라도 넣는 편이 안전하다.

---

## 백엔드에 전달할 현재 값

```env
AI_SERVER_URL=https://talkto-persona-ai.onrender.com
AI_SERVER_TIMEOUT_MS=45000
```

텍스트 채팅과 임베딩은 위 값으로 바로 연동 테스트 가능하다.
