# AI ↔ 백엔드 연동 논의 체크리스트

이 문서는 `PersonaAI` AI 서버와 `TalkTo_PersonaAI_BE` 백엔드를 연결할 때, **백엔드 담당자와 같이 결정해야 하는 항목만** 정리한다.

사용자가 혼자 정할 수 있는 UX 문구, 제품 우선순위, 답변 톤 문제는 제외했다.

기준 우선순위:

1. `PersonaAI_Spec`
2. 현재 `TalkTo_PersonaAI_BE` 코드
3. 현재 `PersonaAI` 코드

---

## 현재 상태 요약

AI 서버는 이미 다음 API를 제공한다.

- `POST /ai/chat`
- `POST /ai/embed`
- `POST /ai/voice-chat`

백엔드는 다음 구조를 이미 가지고 있다.

- `Memory`
- `MemoryEmbedding`
- `Message.retrievedMemoryIds`
- `MessageMemoryRef`
- `VoiceArtifact`

하지만 현재 백엔드는 아직 AI 서버를 호출하지 않는다. 답변은 `ChatRuntimeService.buildAssistantReply()` mock으로 만들고, 기억 검색은 `ILike` 키워드 검색이다. `MemoryEmbedding.embedding`도 현재는 `null`로 저장된다.

---

## 같이 결정해야 할 것

### 1. 기억 검색과 임베딩 역할을 어떻게 연결할 것인가?

현재 상황:

- AI 서버는 `/ai/embed`로 텍스트 임베딩을 생성할 수 있다.
- AI 서버의 `/ai/chat`은 백엔드가 넘긴 `memories`를 받아 답변을 생성할 수 있다.
- 백엔드에는 `Memory`, `MemoryEmbedding`, `Message.retrievedMemoryIds`, `MessageMemoryRef` 구조가 있다.
- 하지만 현재 백엔드는 AI 서버를 호출하지 않고, `MemoryEmbedding.embedding`도 `null`이며, 기억 검색은 `ILike` 키워드 검색이다.

어떻게 붙일지:

1. 기억 생성/수정 시 백엔드가 `/ai/embed`를 호출해 `MemoryEmbedding.embedding`을 저장한다.
2. 대화 시 백엔드가 사용자 메시지를 `/ai/embed`로 임베딩한다.
3. 백엔드가 DB에 저장된 기억 임베딩과 비교해 관련 기억을 검색한다.
4. 백엔드가 검색한 기억을 `/ai/chat`의 `memories`로 전달한다.
5. AI 서버는 전달받은 기억과 대화 히스토리를 참고해 답변을 생성한다.
6. 백엔드는 AI 응답의 `content`, `retrieved_memory_ids`, `latency_ms`를 저장한다.

백엔드 담당자와 확인할 것:

- 현재 `float8[]` 컬럼으로 유사도 계산을 할지, pgvector를 도입할지
- AI 서버 내부 `data/memories.json` 검색은 로컬 테스트 또는 임시 fallback으로만 둘지

---

### 2. `/ai/chat` 요청/응답 계약을 백엔드 DTO와 어떻게 맞출 것인가?

AI 서버 요청:

```json
{
  "message": "할머니 안녕",
  "history": [
    {"role": "user", "content": "이전 메시지"},
    {"role": "assistant", "content": "이전 답변"}
  ],
  "memories": [
    {
      "id": "memory-id",
      "title": "불고기",
      "content": "불고기는 간장과 마늘을 넣고..."
    }
  ]
}
```

AI 서버 응답:

```json
{
  "content": "어. 잘 있었어?",
  "retrieved_memory_ids": ["memory-id"],
  "latency_ms": 2100
}
```

왜 같이 정해야 하나:

백엔드는 현재 camelCase 필드(`retrievedMemoryIds`)를 쓰고, AI 서버는 snake_case(`retrieved_memory_ids`)를 쓴다. history 변환, `memories` 전달 여부, 응답 매핑을 백엔드 코드에서 명확히 처리해야 한다.

---

### 3. `retrieved_memory_ids`와 `MessageMemoryRef`를 백엔드 UUID 기준으로 맞추기

상황:

- AI 기존 기억 ID: `recipe_bulgogi` 같은 문자열
- 백엔드 `Memory.id`: UUID
- `MessageMemoryRef.memoryId`: 백엔드 `Memory.id`를 참조하는 FK

어떻게 붙일지:

실제 연동에서는 `retrieved_memory_ids`를 백엔드 `Memory.id` 기준으로 맞추는 게 맞다. 백엔드가 `/ai/chat`의 `memories`에 UUID를 넣어 보내고, AI 서버는 그 ID를 그대로 `retrieved_memory_ids`로 반환한다. 그래야 `Message.retrievedMemoryIds`와 `MessageMemoryRef.memoryId`가 같은 기준을 쓴다.

백엔드 담당자와 확인할 것:

- 기존 AI legacy ID를 import 과정에서 어떻게 보존할지
- legacy ID를 `tags`에 둘지, 별도 매핑 컬럼/테이블을 둘지
- `/ai/chat`에 넘기는 `memories[].id`는 백엔드 UUID로 고정할지

---

### 4. 기존 44개 장기기억을 백엔드 DB에 어떻게 import할 것인가?

AI 파일:

- `data/memories.json`
- `data/memory_embeddings.json`

백엔드 테이블:

- `memories`
- `memory_embeddings`

왜 같이 정해야 하나:

기존 기억은 이미 AI 쪽에서 임베딩까지 만들어져 있다. 그대로 import할지, 백엔드에서 `/ai/embed`로 다시 만들지 정해야 한다. 또한 AI legacy ID와 백엔드 UUID 매핑 방식이 필요하다.

논의할 세부 항목:

- import script를 누가/어디에 만들지
- 기존 임베딩을 그대로 넣을지
- `/ai/embed`로 재생성할지
- legacy ID를 `tags`에 둘지, 별도 컬럼을 둘지

---

### 5. 백엔드 벡터 검색을 어떤 방식으로 구현할 것인가?

현재 백엔드:

- `MemoryEmbedding.embedding`: `float8[] | null`
- pgvector 컬럼 아님
- `retrieveMemories()`는 `ILike` 검색

왜 같이 정해야 하나:

Spec은 Embedding RAG를 요구하지만 pgvector를 강제하지는 않는다. 현재 DB 구조를 유지할지, pgvector로 migration할지에 따라 백엔드 구현 방식이 달라진다.

선택지:

- `float8[]` 유지 + TypeScript에서 cosine similarity 계산
- pgvector 도입 + DB 쿼리로 유사도 검색

---

### 6. `rebuildEmbeddings()`에 `/ai/embed` 호출을 어떻게 붙일 것인가?

현재 백엔드:

- `create()`, `update()`, `requestReembed()`에서 `rebuildEmbeddings()`를 호출한다.
- `rebuildEmbeddings()`는 chunk row를 생성한다.
- 하지만 `embedding` 값은 현재 `null`로 저장된다.

어떻게 붙일지:

현재 `rebuildEmbeddings()` 위치에 AI 서버 `/ai/embed` 호출을 추가하면 된다. 각 chunk text를 `/ai/embed`에 보내고, 반환된 벡터를 `MemoryEmbedding.embedding`에 저장한다.

백엔드 담당자와 확인할 것:

- create/update 응답 안에서 동기 처리할지, reembed 요청에서만 비동기로 처리할지
- 임베딩 실패 시 memory 저장은 성공시키고 embedding만 재시도 대상으로 둘지
- 실패 로그와 재시도 방식

---

### 7. 단기기억을 백엔드 `Memory` 구조에 어떻게 저장할 것인가?

현재 AI 서버:

- 대화 후 중요도 평가
- 중요도 6 이상이면 `data/short_term_memories.json`에 저장

Spec:

- 장기기억/단기기억 Markdown 기반 RAG
- 단일 기억 DB 기반 기억 저장

어떻게 붙일지:

Spec 기준으로는 단기기억도 백엔드의 단일 기억 DB에 들어가는 방향이 맞다. 백엔드 `Memory.memoryType`이 string이므로 `SHORT_TERM` 같은 값을 넣어 구분할 수 있다. AI 서버의 로컬 JSON 단기기억 저장은 백엔드 연동 후에는 fallback 또는 로컬 테스트용으로 보는 게 맞다.

백엔드 담당자와 확인할 것:

- `Memory.memoryType` 값으로 `SHORT_TERM` / `LONG_TERM`을 어떻게 표준화할지
- AI가 추출한 단기기억 후보를 백엔드에 저장하는 API가 필요한지
- 사용자/세션별 단기기억 분리가 필요한지
- 단기기억도 `retrieved_memory_ids`와 `MessageMemoryRef`에 포함할지

---

### 8. 음성 처리 흐름을 Spec 응답 구조와 어떻게 맞출 것인가?

AI `/ai/voice-chat` 응답:

```json
{
  "success": true,
  "stt_text": "할머니 안녕",
  "content": "어. 잘 있었어?",
  "retrieved_memory_ids": ["memory-id"],
  "tts_success": true,
  "tts_audio_base64": "..."
}
```

현재 백엔드:

- `sendVoiceMessage()`는 user message와 assistant message를 모두 저장한다.
- `VoiceArtifact.messageId`는 현재 user message에 연결된다.
- assistant 응답에는 `ttsAudioUrl`을 붙여 반환한다.

Spec:

- Push-to-talk는 STT 결과를 user message로 표시한다.
- assistant 응답은 `audio_url`을 먼저 자동 재생하고, 재생 후 텍스트를 표시한다.
- 내부 API는 텍스트/음성 처리 단계를 분리할 수 있다.

어떻게 붙일지:

백엔드 공개 API는 현재 `sendVoiceMessage()` 흐름을 유지하더라도, 내부에서는 AI 서버의 STT, chat, TTS 결과를 조합해 Spec 응답 구조(`stt_text`, assistant `content`, `audio_url`)에 맞춰 저장해야 한다. AI 서버가 반환하는 `tts_audio_base64`는 백엔드가 mp3로 저장하고 URL로 바꿔 내려줘야 한다.

백엔드 담당자와 확인할 것:

- 내부에서 `/ai/voice-chat` 한 번으로 갈지, `/ai/stt` → `/ai/chat` → `/ai/tts`로 나눌지
- signed URL을 어떻게 만들지
- 현재 `VoiceArtifact.messageId = user message` 구조로 충분한지, assistant TTS 파일 참조를 더 명확히 할지
- STT/TTS 실패 시 어떤 DB row를 저장할지

---

### 9. AI 서버 호출 실패를 백엔드에서 어떻게 기록할 것인가?

왜 같이 정해야 하나:

백엔드는 이미 `SystemLog`와 `SystemLogCategory.LLM/STT/TTS/MEMORY/SYSTEM` 구조를 가지고 있다. AI 서버 호출 실패, timeout, STT/TTS 실패를 어떤 category/detail로 남길지 정해야 운영 디버깅이 가능하다.

논의할 세부 항목:

- `/ai/chat` 실패 시 assistant message를 `FAILED`로 저장할지
- `/ai/voice-chat` 실패 시 user message를 저장할지
- RAG/임베딩 실패를 `MEMORY`로 남길지, `RAG` category를 추가할지
- timeout 값을 어디서 관리할지
- `SystemLog.detail`에 어떤 정보를 남길지

---

## 1차 MVP에서 최소로 붙이는 경우

Spec 기준 구조를 크게 바꾸지 않고 붙이는 최소 흐름은 아래처럼 잡을 수 있다.

1. `ChatRuntimeService`를 AI HTTP client로 바꾼다.
2. 기존 44개 장기기억을 백엔드 DB에 import한다.
3. `rebuildEmbeddings()`에서 `/ai/embed`를 호출해 `MemoryEmbedding.embedding`을 채운다.
4. `retrieveMemories()`를 `ILike` 검색에서 벡터 유사도 검색으로 바꾼다.
5. `sendTextMessage()`에서 검색된 기억을 `/ai/chat`의 `memories`로 전달한다.
6. AI 응답의 `content`, `retrieved_memory_ids`, `latency_ms`를 assistant message에 저장한다.
7. `sendVoiceMessage()`도 같은 RAG/chat 흐름을 사용한다.
8. 음성 응답은 AI TTS 결과를 mp3로 저장하고 `audio_url`로 반환한다.

---

## 나중에 Spec 기준으로 정리할 일

1. AI legacy ID와 백엔드 UUID 매핑을 정리한다.
2. 단기기억 후보를 백엔드 `Memory`로 저장하는 흐름을 만든다.
3. 음성 사용량, 재생 완료, 텍스트 reveal 이벤트를 저장한다.
4. RAG/임베딩 실패 로그 category를 확정한다.
