# AI <-> 백엔드 연동 업데이트 ver3

> 상태: Render 배포 직후 연동 기록. 현재 배포/남은 운영 항목은 `ai_backend_integration_ver4.md`를 기준으로 본다.

## 1. AI 서버를 어디에 올릴지, BE에서 접근 가능한 `AI_SERVER_URL` 확정 필요

### AI에서 해결한 것

- AI 서버를 Render에 배포했다.
- 배포 URL: `https://talkto-persona-ai.onrender.com`
- 배포 서버에서 아래 엔드포인트 동작을 확인했다.
  - `GET /health`
  - `POST /ai/embed`
  - `POST /ai/chat`
  - `POST /ai/memory/extract`

### 백엔드가 참조해야 할 것

```env
AI_SERVER_URL=https://talkto-persona-ai.onrender.com
```

백엔드는 위 URL을 기준으로 AI 서버를 호출하면 된다.

---

## 2. 로컬/스테이징/운영 환경별 `AI_SERVER_URL`, timeout 값 필요

### AI에서 해결한 것

- Render free 인스턴스 첫 `/ai/chat` 호출이 약 37초 걸린 것을 확인했다.
- `/health`, `/ai/embed`, `/ai/chat`, `/ai/memory/extract`는 Render 배포 서버에서 정상 응답을 확인했다.

### 백엔드가 참조해야 할 것

```env
AI_SERVER_URL=https://talkto-persona-ai.onrender.com
AI_SERVER_TIMEOUT_MS=45000
```

기존 10초 timeout은 Render free 인스턴스 cold start 상황에서 부족할 수 있다.

---

## 3. 서버 간 인증이 필요한지 결정 필요

### AI에서 해결한 것

- AI 서버에 shared secret 기반 인증을 추가했다.
- `AI_SERVER_TOKEN` 환경변수가 설정된 경우 `/ai/*` 요청에 `X-AI-Server-Token` 헤더를 요구한다.
- `GET /health`는 배포 상태 확인을 위해 토큰 없이 접근 가능하다.
- `AI_SERVER_TOKEN`이 설정되지 않은 환경에서는 기존처럼 토큰 없이 동작한다.

### 백엔드가 참조해야 할 것

현재 Render AI 서버에는 아직 `AI_SERVER_TOKEN`을 설정하지 않은 상태로 두는 것이 좋다.
백엔드가 먼저 토큰 없이 서버 호출 테스트를 끝내고, 이후 AI 서버 호출 시 공통 헤더를 붙이도록 수정한 뒤 토큰을 확정해서 양쪽 환경변수에 같은 값을 넣으면 된다.

적용 순서:

1. 백엔드가 현재 `AI_SERVER_URL`로 `/ai/embed`, `/ai/chat`, `/ai/memory/extract` 호출 테스트
2. 백엔드가 `/ai/*` 호출 시 `X-AI-Server-Token` 헤더를 붙이도록 수정
3. AI와 백엔드가 공유할 긴 랜덤 토큰 값 확정
4. 백엔드 환경변수와 Render AI 서버 환경변수에 같은 토큰 등록
5. 토큰 포함 호출 테스트

```env
AI_SERVER_TOKEN=백엔드와_AI서버가_공유할_긴_랜덤_토큰
```

백엔드는 `/ai/*` 호출 시 아래 헤더를 추가하면 된다.

```http
X-AI-Server-Token: 백엔드와_AI서버가_공유할_긴_랜덤_토큰
```

예시:

```ts
headers: {
  'content-type': 'application/json',
  'x-ai-server-token': process.env.AI_SERVER_TOKEN,
}
```

주의: Render에 `AI_SERVER_TOKEN`을 먼저 설정하면, 백엔드가 헤더를 보내기 전까지 `/ai/*` 요청은 `401`로 막힌다.

---

## 4. 기존 44개 장기기억을 BE DB로 import하는 방식 결정 필요

### AI에서 해결한 것

- 기존 `data/memories.json` 44개 기억을 백엔드 `CreateMemoryDto` 형태로 변환했다.
- 생성 파일: `data/backend_memory_import.json`
- 변환 스크립트: `tools/export_backend_memories.py`
- 각 기억은 아래 기준으로 변환했다.
  - `memoryType`: `LONG_TERM`
  - `bodyMarkdown`: 기존 `content`
  - `tags`: 기존 category와 legacy ID 보존
  - `confidenceScore`: `1.0`

### 백엔드가 참조해야 할 것

백엔드는 `data/backend_memory_import.json`을 읽어서 각 item을 `POST /api/v1/memories` 생성 payload로 사용할 수 있다.

예시:

```json
{
  "title": "불고기 만드는 법",
  "memoryType": "LONG_TERM",
  "relatedPeople": [],
  "relatedPeriod": null,
  "bodyMarkdown": "불고기 만들 때 소고기 앞다리살을 쓰고...",
  "tags": [
    "category:요리",
    "legacy_id:recipe_bulgogi"
  ],
  "confidenceScore": 1.0
}
```

백엔드의 현재 `create()` 흐름은 memory 생성 후 `rebuildEmbeddings()`를 호출하므로, import 시 백엔드가 `/ai/embed`를 통해 새 임베딩을 생성하면 된다.

기존 `data/memory_embeddings.json`은 이번 import 기준에서는 직접 사용하지 않는다.

---

## 5. AI legacy ID와 BE `Memory` UUID 매핑 방식 결정 필요

### AI에서 해결한 것

- `/ai/chat`은 백엔드가 전달한 `memories[].id`를 그대로 `retrieved_memory_ids`로 반환한다.
- 백엔드가 `memories: []`를 보내면 AI 서버가 내부 `data/memories.json`으로 fallback 검색하지 않도록 수정했다.
- 백엔드가 `memories` 필드를 보내는 경우, AI 서버는 백엔드가 넘긴 기억만 사용한다.

### 백엔드가 참조해야 할 것

백엔드 연동에서는 `/ai/chat` 요청에 항상 `memories` 필드를 보내면 된다.

```json
{
  "message": "할머니 안녕",
  "history": [],
  "memories": []
}
```

백엔드가 기억을 검색한 경우에는 `memories[].id`에 BE `Memory` UUID를 넣으면 된다.

```json
{
  "message": "할머니 불고기 어떻게 해?",
  "history": [],
  "memories": [
    {
      "id": "백엔드 Memory UUID",
      "title": "불고기 만드는 법",
      "content": "불고기 만들 때 소고기 앞다리살을 쓰고..."
    }
  ]
}
```

AI 응답의 `retrieved_memory_ids`도 같은 UUID 기준으로 반환된다.

---

## 6. 음성은 `/ai/voice-chat` 단일 호출로 갈지, STT -> chat -> TTS 분리로 갈지 결정 필요

### AI에서 해결한 것

- AI 서버는 단계별 음성 처리 엔드포인트를 제공한다.
  - `POST /ai/stt`: 음성 파일을 텍스트로 변환
  - `POST /ai/chat`: 텍스트와 백엔드가 검색한 기억을 기반으로 답변 생성
  - `POST /ai/tts`: 답변 텍스트를 mp3 음성으로 변환
- `/ai/voice-chat` 단일 호출 엔드포인트도 존재하지만, 현재 백엔드 DB/RAG 연동 구조에서는 분리 호출을 기준으로 쓰는 것이 적합하다.

### 백엔드가 참조해야 할 것

음성 대화도 텍스트 대화와 같은 저장/검색 흐름을 쓰려면 `STT -> chat -> TTS` 분리 호출을 권장한다.

권장 흐름:

1. 백엔드가 사용자 음성 파일을 받는다.
2. 백엔드가 `/ai/stt`를 호출해서 음성을 텍스트로 변환한다.
3. 백엔드가 STT 결과 텍스트를 user message로 저장한다.
4. 백엔드가 STT 결과 텍스트로 기억을 검색한다.
5. 백엔드가 `/ai/chat`을 호출한다.
   - `message`: STT 결과 텍스트
   - `history`: 최근 대화
   - `memories`: 백엔드가 검색한 기억
6. 백엔드가 assistant 답변과 `retrieved_memory_ids`를 저장한다.
7. 백엔드가 `/ai/tts`를 호출해서 assistant 답변을 mp3로 변환한다.
8. 백엔드가 mp3 파일을 저장하고 프론트에서 재생할 URL을 만든다.
9. 백엔드가 `/ai/memory/extract`를 호출해서 새 단기기억 후보를 판단한다.

`/ai/voice-chat` 단일 호출은 로컬 프로토타입이나 간단한 데모에는 쓸 수 있지만, 백엔드가 기억 검색, 메시지 저장, `MessageMemoryRef`, 음성 파일 저장을 관리해야 하는 운영 연동에서는 분리 호출이 더 맞다.

---

## 7. TTS mp3 파일을 저장할 스토리지/S3 및 signed URL 정책 필요

### AI에서 해결한 것

- AI 서버는 `POST /ai/tts` 엔드포인트를 제공한다.
- `/ai/tts`는 답변 텍스트를 받아 `audio/mpeg` mp3 bytes를 반환한다.
- AI 서버가 담당하는 범위는 mp3 생성까지이다.

요청:

```json
{
  "text": "어. 그래 잘 자라. 사랑한다."
}
```

응답:

```http
Content-Type: audio/mpeg
Content-Disposition: attachment; filename=tts_output.mp3
```

### 백엔드가 참조해야 할 것

백엔드는 `/ai/tts` 응답으로 받은 mp3 bytes를 저장하고, 프론트가 재생할 수 있는 URL을 만들어야 한다.
mp3 저장소, URL 발급 방식, 보관 기간, DB 저장 필드는 백엔드/프론트/인프라 쪽 협의 사안이다.

AI 쪽 추가 작업은 현재 없다.
단, 백엔드가 base64 응답 등 다른 TTS 응답 형식을 원하면 AI 서버 응답 형식만 조정하면 된다.

---

## 8. MVP에서는 `float8[]` + TypeScript cosine similarity로 구현했는데, pgvector로 갈지 여부 결정 필요

### AI에서 해결한 것

- `/ai/embed`가 Render 배포 서버에서 정상 동작하는 것을 확인했다.
- 현재 임베딩 모델은 AI 서버 코드 기준 `text-embedding-3-small`이다.
- 실제 응답 벡터 길이는 1536이다.
- AI가 해결한 범위는 임베딩 생성 API 제공까지이며, 백엔드의 벡터 저장/검색 방식까지 결정한 것은 아니다.

### 백엔드가 참조해야 할 것

현재 백엔드 구현은 `float8[]` 저장 후 TypeScript cosine similarity로 검색하는 방식이다.
이 방식으로 계속 갈지, DB 레벨 검색을 위해 pgvector로 전환할지는 별도 결정이 필요하다.

두 방식 중 어느 쪽을 선택하더라도, memory 생성/수정/reembed 및 query embedding 시 임베딩 생성은 `/ai/embed`를 호출하면 된다.

요청:

```json
{
  "text": "임베딩할 텍스트"
}
```

응답:

```json
{
  "embedding": [0.01, -0.02]
}
```

---

## 9. 단기기억을 BE `Memory`로 저장할지, 저장한다면 `memoryType` 표준값 결정 필요

### AI에서 해결한 것

- Render 운영 환경에서 AI 서버 로컬 `data/short_term_memories.json` 저장을 비활성화했다.
- 단기기억 후보 판단용 API를 추가했다.
- 단기기억 후보 판단 시 현재 턴뿐 아니라 백엔드가 전달한 최근 대화 `history`도 함께 참고하도록 수정했다.
- 자동 추출된 대화 기반 기억은 응답에서 `memory_type: "SHORT_TERM"`으로 내려준다.

```http
POST /ai/memory/extract
```

이 API는 로컬 파일에 저장하지 않고, 백엔드 DB에 저장할 후보만 반환한다.

### 백엔드가 참조해야 할 것

요청:

```json
{
  "history": [
    {
      "role": "user",
      "content": "요즘 집 알아보고 있어"
    },
    {
      "role": "assistant",
      "content": "어디 쪽으로 알아보는데?"
    }
  ],
  "user_message": "나 다음 달에 이사해",
  "assistant_message": "어디로 가는데?"
}
```

응답:

```json
{
  "saved": true,
  "importance": 7,
  "memory_type": "SHORT_TERM",
  "category": "가족",
  "summary": "손녀가 다음 달에 이사 간다는 사실",
  "reason": "가족 간의 중요한 생활 변화 정보로 다음 대화에서 언급될 가능성이 높음"
}
```

`history`는 선택값이지만, 맥락 의존 문장까지 제대로 판단하려면 백엔드가 `/ai/chat`에 넘기는 최근 대화 히스토리와 같은 형식으로 전달하는 것이 좋다.

`memoryType` 표준값은 아래 기준으로 맞춘다.

- 기존 44개 고정 기억, 관리자/대표가 직접 작성하거나 검수한 기억: `LONG_TERM`
- 대화 중 `/ai/memory/extract`가 자동 추출한 새 기억 후보: `SHORT_TERM`

백엔드가 `saved: true` 응답을 받아 `Memory`를 생성할 때는 아래처럼 매핑하면 된다.

```json
{
  "title": "손녀가 다음 달에 이사 간다는 사실",
  "memoryType": "SHORT_TERM",
  "relatedPeople": [],
  "relatedPeriod": null,
  "bodyMarkdown": "손녀가 다음 달에 이사 간다는 사실",
  "tags": [
    "category:가족",
    "source:ai_memory_extract"
  ],
  "confidenceScore": 0.7
}
```

백엔드에서 사용할 수 있는 흐름:

1. `/ai/chat`으로 assistant 답변 생성
2. 최근 대화 `history`, 사용자 메시지, assistant 답변을 `/ai/memory/extract`에 전달
3. `saved: true`이면 백엔드가 `Memory` row로 저장
4. 저장 후 기존 `rebuildEmbeddings()` 흐름으로 `/ai/embed` 호출 및 embedding 저장

AI 서버 운영 환경변수:

```env
PERSONA_ENABLE_LOCAL_SHORT_TERM=false
```
