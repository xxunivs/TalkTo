# AI <-> 백엔드 연동 업데이트 ver3

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

백엔드와 Render AI 서버에 같은 토큰 값을 설정해야 한다.

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

### 백엔드가 참조해야 할 것

---

## 7. TTS base64를 mp3 파일로 저장할 스토리지/S3 및 signed URL 정책 필요

### AI에서 해결한 것

### 백엔드가 참조해야 할 것

---

## 8. MVP에서는 `float8[]` + TypeScript cosine similarity로 구현했는데, pgvector로 갈지 여부 결정 필요

### AI에서 해결한 것

- `/ai/embed`가 Render 배포 서버에서 정상 동작하는 것을 확인했다.
- 현재 임베딩 모델은 AI 서버 코드 기준 `text-embedding-3-small`이다.

### 백엔드가 참조해야 할 것

백엔드는 기존 구현대로 memory 생성/수정/reembed 및 query embedding 시 `/ai/embed`를 호출하면 된다.

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

실제 응답 벡터 길이는 1536이다.

---

## 9. 단기기억을 BE `Memory`로 저장할지, 저장한다면 `memoryType` 표준값 결정 필요

### AI에서 해결한 것

- Render 운영 환경에서 AI 서버 로컬 `data/short_term_memories.json` 저장을 비활성화했다.
- 단기기억 후보 판단용 API를 추가했다.

```http
POST /ai/memory/extract
```

이 API는 로컬 파일에 저장하지 않고, 백엔드 DB에 저장할 후보만 반환한다.

### 백엔드가 참조해야 할 것

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
  "importance": 7,
  "category": "가족",
  "summary": "손녀가 다음 달에 이사 간다는 사실",
  "reason": "가족 간의 중요한 생활 변화 정보로 다음 대화에서 언급될 가능성이 높음"
}
```

백엔드에서 사용할 수 있는 흐름:

1. `/ai/chat`으로 assistant 답변 생성
2. 사용자 메시지와 assistant 답변을 `/ai/memory/extract`에 전달
3. `saved: true`이면 백엔드가 `Memory` row로 저장
4. 저장 후 기존 `rebuildEmbeddings()` 흐름으로 `/ai/embed` 호출 및 embedding 저장

AI 서버 운영 환경변수:

```env
PERSONA_ENABLE_LOCAL_SHORT_TERM=false
```
