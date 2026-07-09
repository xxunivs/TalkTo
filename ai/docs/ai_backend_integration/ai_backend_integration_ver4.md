# AI <-> 백엔드 연동 요청 ver4 — 새 AI 서버 컷오버 + conversationPartnerName + 채팅 기억 추출

새 AI 서버가 배포됐다. 지금 BE는 전사(`/v1/analysis/transcriptions`)만 새 서버로 옮겼고 채팅·임베딩·기억추출은 옛 `/ai/*`(MVP 서버)를 가리킨다. ver3 §4·5에서 "채팅 이전 시점에 반영"으로 미뤄둔 그 시점이 지금이므로, 나머지를 새 서버로 전환하는 요청이다.

## 1. [요청] 새 AI 서버 연동 정보

BE 배포 환경변수에 설정:

| 변수 | 값 |
|---|---|
| `AI_SERVER_URL` | `https://talkto-app-ai.onrender.com` (Render 대시보드의 실제 서비스 URL로 확정 — 이 값은 예상 기본형) |
| `AI_SERVER_TOKEN` | `78287caad778adcdb4656dd2546a76f6b3ad00f94564ab8bb35e3b76cab55b43` |
| `AI_SERVER_TIMEOUT_MS` | `120000` (ver3 §6. 코드 기본값이 45000이라 env로 상향 확인 필요) |

- 인증: BE는 AI 호출 시 헤더 `x-ai-server-token: <AI_SERVER_TOKEN>`. 위 토큰은 AI 서버(Render)에 설정된 값과 동일해야 통과한다(불일치 시 401).

## 2. [요청] 엔드포인트 컷오버 — 옛 `/ai/*` → 새 `/v1/*`

`ai-client.service.ts`의 호출 경로를 아래로 전환. 요청/응답 계약은 각 항 참조.

| 기능 | 현재(옛) | 새 경로 | 계약 |
|---|---|---|---|
| 전사 | `/v1/analysis/transcriptions` | (동일, 완료) | ver1 |
| 채팅 응답 | `/ai/chat` | `POST /v1/persona/responses` | ver3 §4 |
| 페르소나 조립 | (없음) | `POST /v1/persona/assembly` | ver3 §5 |
| 임베딩 | `/ai/embed` | `POST /v1/embeddings` | 아래 3 |
| 기억추출 | `/ai/memory/extract` | `POST /v1/persona/memory-candidates` | 아래 4 |

- 채팅·조립 계약(요청/응답 JSON)은 ver3 §4·§5 그대로다. 바뀐 건 호출 경로와 `persona.instructions` 추가뿐이다.
- 전환 순서 권장: `/assembly`(1회 조립·저장) → `/embeddings`(기억 벡터화·저장) → `/responses`(채팅) → `/memory-candidates`(채팅 후 기억 후보).

## 3. [요청] 임베딩 — `POST /v1/embeddings`

3-A 기억(memorySegment)을 벡터화해 BE가 저장(pgvector 등)한다. 항목별로 인덱스를 되돌려주므로 매핑만 하면 된다.

```json
// 요청
{
  "jobId": "uuid",
  "items": [
    { "memorySegmentId": "uuid", "embeddingIndex": 0, "text": "평생 강원 시골 단독주택에 살았다." }
  ]
}
// 응답
{ "embeddings": [ { "memorySegmentId": "uuid", "embedding": [0.01, -0.02, ...] } ] }
```

- 모델 `text-embedding-3-small`(1536차원, 기존과 동일). 채팅 시 사용자 메시지도 같은 방식으로 임베딩해 top-k(5~8) 검색 → `/responses`의 `memories`에 싣는다(ver3 §4).

## 4. [요청] conversationPartnerName — 전사 요청에 통화 상대 이름 추가

우리가 계속 요청해온 "통화 상대 입력"(상대 화자 귀속 미해결 이슈)의 해법. 전사 요청에 필드 하나 추가한다.

- `POST /v1/analysis/transcriptions` 요청 본문에 `conversationPartnerName` (string, optional) 추가.
- 값: 그 녹음에서 대상자와 통화한 상대의 이름/호칭(업로드 시 사용자가 선택). 미선택이면 생략(또는 null).
- AI 용도: 이 값이 오면 **대상자가 아닌 화자 = 그 상대로 확정**해 기억·인물 분석에서 발화를 정확히 귀속한다(지금은 이름 근거가 없으면 "상대"로만 남아 R2 지지율이 깎였다). 없으면 종전대로 "상대" 처리라 하위호환.

```json
{
  "jobId": "uuid", "recordingId": "uuid",
  "audioUrl": "https://... (presigned GET, ≥30분)",
  "audioMimeType": "audio/m4a", "language": "ko", "speakerDiarization": true,
  "glossary": ["정읍", "매실청"],
  "conversationPartnerName": "도윤"
}
```

## 5. [요청] 채팅 기억 추출 — `POST /v1/persona/memory-candidates`

채팅 한 턴(사용자·페르소나 주고받음)에서 앞으로 기억할 만한 새 사실을 뽑아 돌려준다. 저장 여부·무엇을 저장할지는 BE가 판단한다(AI는 stateless — 저장하지 않는다).

```json
// 요청
{
  "userMessage": "할머니 나 이번에 취직했어!",
  "assistantMessage": "아이고 잘됐다, 밥 잘 챙겨 먹고 다녀라.",
  "history": [ { "role": "user", "content": "..." } ]
}
// 응답
{
  "candidates": [
    { "summary": "사용자가 취직해 다음 주부터 회사에 다닌다.",
      "category": "직장", "importance": 9, "confidence": 0.99 }
  ],
  "provider": "openai",
  "model": "gpt-5.4-mini"
}
```

- 근황·변화(취직·이사·결혼·건강 등)만 뽑고, 인사·감정·일시적 상태는 제외한다. 없으면 빈 배열.
- `importance`(1~10)·`confidence`(0~1)는 AI가 매기고, **저장 임계·중복 판단은 BE**가 한다.
- 기존 `/ai/memory/extract`와의 차이: 요청은 정렬(카멜케이스만 맞춤), 응답이 단건 `{saved, ...}`이 아니라 **후보 목록** `{candidates: [...]}`이다. 한 턴에서 새 사실이 여럿 나올 수 있고 "저장할지"는 BE가 정하는 게 stateless 원칙에 맞아 AI는 `saved` 플래그를 주지 않는다. BE는 목록을 순회하며 저장 결정만 더하면 된다.
