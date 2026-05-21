# AI <-> 백엔드 연동 업데이트 ver4

이 문서가 현재 배포/운영 연동 상태의 기준이다.

## 현재 반영 상태

- AI Render URL: `https://talkto-persona-ai.onrender.com`
- BE Render URL: `https://talkto-personaai-be.onrender.com`
- BE DB: Neon PostgreSQL
- TTS mp3: private Cloudflare R2 object + signed playback URL
- 기존 장기기억 44개: AI repo `data/backend_memory_import.json` 기준으로 BE DB import 완료
- import payload: `LONG_TERM`, `category:*`, `legacy_id:*`, 명시 인물 `relatedPeople` 기준
- BE deployed smoke: health/auth/persona/imported memories 자동 검증 가능

## 현재 호출 경계

- 텍스트: BE query embedding -> BE memory search -> `/ai/chat`
- 음성 운영 흐름: `/ai/stt` -> BE memory search -> `/ai/chat` -> `/ai/tts`
- 단기기억 후보: `/ai/memory/extract`
- `/ai/voice-chat`은 AI 단독 프로토타입/데모용으로 남긴다.
- `/ai/chat`의 `retrieved_memory_ids`는 BE가 보낸 memory UUID 기준이다.

## 남은 운영 항목

1. `AI_SERVER_TOKEN`을 BE Render와 AI Render에 같은 값으로 넣고 token path를 재검증한다.
2. signed URL 만료 뒤 과거 음성 재생을 지원할지 결정한다.
3. BE memory search를 현재 `float8[]` + TypeScript cosine similarity에서 pgvector 검색으로 바꿀지 결정한다.
4. 운영 에러 추적, 인증/초대 정책, 추가 관리자 KPI 범위를 확정한다.

## AI 서버 토큰 설정

백엔드 최신 코드에서 `AI_SERVER_TOKEN`을 읽어 AI 서버 호출 시 `X-AI-Server-Token` 헤더로 보내는 것을 확인했다.

AI 서버도 이미 같은 토큰 검증 로직을 가지고 있다.

- `AI_SERVER_TOKEN`이 설정되지 않으면 기존처럼 토큰 없이 동작한다.
- `AI_SERVER_TOKEN`이 설정되면 `/ai/*` 요청은 `X-AI-Server-Token` 헤더가 있어야 한다.
- `GET /health`는 배포 상태 확인용이므로 토큰 없이 접근 가능하다.

백엔드와 AI 서버에는 같은 값을 넣어야 한다.

```env
AI_SERVER_TOKEN=백엔드와_AI서버가_공유할_긴_랜덤_토큰
```

백엔드 호출 헤더:

```http
X-AI-Server-Token: 백엔드와_AI서버가_공유할_긴_랜덤_토큰
```

적용 순서:

1. 백엔드 환경변수에 `AI_SERVER_TOKEN`을 넣는다.
2. Render AI 서버 환경변수에도 같은 `AI_SERVER_TOKEN`을 넣는다.
3. 백엔드와 Render AI 서버를 재시작/재배포한다.
4. 토큰 포함 상태로 `/ai/embed`, `/ai/chat`, `/ai/memory/extract` 호출을 확인한다.

실제 토큰 값은 GitHub 문서나 코드에 올리지 않고, 환경변수로만 공유한다.
