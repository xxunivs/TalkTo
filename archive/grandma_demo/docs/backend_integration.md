# AI ↔ 백엔드 연동 가이드

## 1. AI 서버 개요

AI 서버는 Python FastAPI로 구현되어 있으며, 백엔드(NestJS)가 HTTP로 호출하는 구조.

- 실행: `uvicorn server:app --port 8000`
- API 문서: `http://localhost:8000/docs`
- 소스: `server.py`

---

## 2. 엔드포인트

### POST /ai/chat — 텍스트 대화

요청:
```json
{
  "message": "할머니 안녕",
  "history": [
    {"role": "user", "content": "이전 메시지"},
    {"role": "assistant", "content": "이전 답변"}
  ]
}
```

응답:
```json
{
  "content": "어. 잘 있었어?",
  "retrieved_memory_ids": ["recipe_bulgogi", "family_hyangi"],
  "latency_ms": 2100
}
```

- `history`: 해당 대화의 최근 메시지들. 백엔드 DB에서 꺼내서 보내주면 됨.
- `retrieved_memory_ids`: AI 내부 기억 ID. 참고용으로 저장하면 됨.
- `latency_ms`: AI 서버 내부 처리 시간.

---

### POST /ai/stt — 음성 → 텍스트

요청: `multipart/form-data`
- `audio_file`: 음성 파일 (wav, mp3, m4a)

응답:
```json
{
  "success": true,
  "text": "할머니 안녕",
  "error": ""
}
```

---

### POST /ai/tts — 텍스트 → 할머니 목소리

요청:
```json
{
  "text": "어. 잘 있었어?"
}
```

응답: `audio/mpeg` (mp3 파일 바이트 직접 반환)
- 백엔드가 이 파일을 받아서 S3 등에 저장하고 signed URL 생성

실패 시:
```json
{
  "success": false,
  "error": "ElevenLabs API 오류: 429"
}
```

---

### POST /ai/voice-chat — 음성 대화 한 번에 처리

요청: `multipart/form-data`
- `audio_file`: 음성 파일
- `history`: 이전 대화 JSON 문자열 (예: `"[{\"role\":\"user\",\"content\":\"안녕\"}]"`)

응답:
```json
{
  "success": true,
  "stt_text": "할머니 안녕",
  "content": "어. 잘 있었어?",
  "retrieved_memory_ids": ["recipe_bulgogi"],
  "latency_ms": 6200,
  "tts_success": true,
  "tts_audio_base64": "...(mp3 파일 base64 인코딩)...",
  "tts_error": ""
}
```

- STT → 답변 생성 → TTS를 한 번에 처리
- `tts_audio_base64`: base64 디코딩하면 mp3 파일. 백엔드가 S3에 저장 후 signed URL 생성.
- `tts_success: false`면 텍스트 fallback 처리 (`content`는 정상 반환됨)

---

### GET /health — 헬스 체크

응답:
```json
{"status": "ok"}
```

---

## 3. 백엔드에서 수정이 필요한 부분

### 3-1. chat-runtime.service.ts

현재 `buildAssistantReply()`가 Mock 응답을 반환하고 있음.
→ AI 서버 `/ai/chat`을 HTTP로 호출하도록 변경 필요.

```typescript
// 현재 (Mock)
const assistantReply = this.chatRuntimeService.buildAssistantReply({...});

// 변경 후
const aiResponse = await fetch("http://ai-server:8000/ai/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    message: dto.content,
    history: recentMessages,  // DB에서 최근 메시지 조회해서 전달
  }),
});
const { content, retrieved_memory_ids, latency_ms } = await aiResponse.json();
```

### 3-2. conversations.service.ts — retrieveMemories()

현재 백엔드가 자체적으로 키워드 검색(`ILike`)을 하고 있음.
→ AI 서버가 내부적으로 벡터 검색(RAG)을 하므로, 백엔드에서 별도 기억 검색은 불필요.
→ `/ai/chat` 호출 시 AI 서버가 알아서 기억 검색 + 프롬프트 주입까지 처리함.

### 3-3. conversations.service.ts — sendTextMessage()에 history 전달

현재 `sendTextMessage()`에서 이전 대화를 AI한테 안 보내고 있음.
→ 해당 conversation의 최근 메시지(예: 최근 20턴)를 DB에서 조회해서 `/ai/chat`의 `history`로 전달 필요.

```typescript
// 예시: 최근 20개 메시지 조회
const recentMessages = await this.messagesRepository.find({
  where: { conversationId },
  order: { createdAt: 'ASC' },
  take: 20,
});
const history = recentMessages.map(m => ({
  role: m.senderType === 'USER' ? 'user' : 'assistant',
  content: m.content,
}));
```

### 3-4. conversations.service.ts — sendVoiceMessage()

현재 STT/TTS가 Mock 상태.
→ 두 가지 방법 중 택 1:
- **방법 1**: `/ai/voice-chat` 한 번 호출 (STT+답변+TTS 한 번에)
- **방법 2**: `/ai/stt` → `/ai/chat` → `/ai/tts` 세 번 호출

방법 1 권장. `tts_audio_base64`를 디코딩해서 S3에 저장 후 signed URL 생성.

---

## 4. 기억(Memory) 데이터 관련

### 현재 상태
- 백엔드: Memory 테이블 있음, 비어있음. 청크 분할 + 임베딩 테이블 있으나 embedding은 null.
- AI: `data/memories.json`에 37개 기억, `data/memory_embeddings.json`에 임베딩 벡터. AI 서버 내부에서 벡터 검색.

### 장기 방향
기억 데이터를 백엔드 DB로 통일하고, AI 서버는 임베딩 생성/벡터 검색만 담당.
→ AI 서버에 `/ai/embed`, `/ai/search` 엔드포인트 추가 예정.
→ 백엔드가 기억 생성 시 `/ai/embed` 호출해서 임베딩 벡터를 받아 DB에 저장.
→ 대화 시 `/ai/search` 호출해서 관련 기억 조회.

### 현재 (MVP)
AI 서버가 내부 JSON으로 기억 관리. 백엔드는 `/ai/chat`만 호출하면 기억 검색까지 AI 서버에서 처리.

---

## 5. 단기 기억

AI 서버가 매 대화 후 자동으로 중요도를 평가하고, 중요도 6 이상이면 단기 기억에 저장.
- 저장 위치: `data/short_term_memories.json` (현재), 장기적으로 백엔드 DB로 이전 필요.
- 백엔드에서 별도 처리 불필요. `/ai/chat` 호출하면 내부적으로 단기 기억 추출까지 처리됨.

---

## 6. AI 서버 환경 세팅

### 필요 패키지
```bash
python3.11 -m venv venv
source venv/bin/activate
pip install openai python-dotenv fastapi uvicorn python-multipart requests numpy
```

### .env 파일
```
OPENAI_API_KEY=...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=lTmcD2Kp43w1lHsMQmq7
```

### 서버 실행
```bash
uvicorn server:app --port 8000
```
