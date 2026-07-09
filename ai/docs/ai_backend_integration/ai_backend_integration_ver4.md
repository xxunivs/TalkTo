# AI <-> 백엔드 연동 요청 ver4 — 새 AI 서버 컷오버 + conversationPartnerName + 채팅 기억 추출

새 AI 서버가 배포됐다. 지금 BE는 전사(`/v1/analysis/transcriptions`)만 새 서버로 옮겼고 채팅·임베딩·기억추출은 옛 `/ai/*`(MVP 서버)를 가리킨다. ver3 §4·5에서 "채팅 이전 시점에 반영"으로 미뤄둔 그 시점이 지금이므로, 나머지를 새 서버로 전환하는 요청이다.

## 1. [요청] 새 AI 서버 연동 정보

BE 배포 환경변수에 설정:

| 변수 | 값 |
|---|---|
| `AI_SERVER_URL` | `https://talkto-app-ai.onrender.com` |
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

## 4. [요청] 녹음 분석(통합) — `POST /v1/analysis/recording` (구현 완료)

녹음 1건의 저장된 전사 세그먼트를 받아 **한 번의 호출로** 3-A 기억 + 요약 + 태그 + 말투(언어스타일) + 안전플래그를 만들어 돌려준다. 인물·민감·3-A 분석은 내부에서 한 번만 돌려 공유하므로(엔드포인트를 쪼개 각각 부르는 것보다 LLM 비용이 낮다), **녹음당 이 1콜**이면 Archive(요약)·Memories(기억·태그)·Voice Persona(말투·금기) 재료가 다 나온다. **구현·배포 완료.**

```json
// 요청
{
  "jobId": "uuid", "recordingId": "uuid",
  "transcriptSegments": [
    { "id": "seg-uuid", "segmentIndex": 0, "startMs": 0, "endMs": 1000,
      "speakerLabel": "SPK_1", "transcriptText": "회 좋아하지." }
  ],
  "subjectContext": { …ver1 계약 2 형태(대상자·가족·용어집)… },
  "subjectSpeakerLabel": "SPK_0",
  "conversationPartnerName": "지영"
}
// 응답
{
  "memorySegments": [
    { "segmentIndex": 0, "sourceTranscriptSegmentIds": ["seg-uuid"],
      "startMs": 0, "endMs": 1000, "speakerLabel": "SPK_1",
      "memoryText": "지영은 회를 좋아한다.", "confidence": "confirmed",
      "importanceScore": 7, "tags": ["음식요리"], "relatedPeople": ["지영"],
      "sensitivityFlags": [] }
  ],
  "summary": "자리 양보와 음식 취향에 대해 이야기를 나눴다.",
  "tags": ["음식요리", "가족안부"],
  "speechStyle": {
    "recurringPhrases": [{ "phrase": "그러니까", "sourceTranscriptSegmentIds": ["seg-uuid"] }],
    "addressTerms": [{ "person": "지영", "term": "지영아", "sourceTranscriptSegmentIds": ["seg-uuid"] }],
    "sentencePatterns": ["이유를 덧붙이는 설명형 화법"],
    "emotionalExpressions": [{ "emotion": "애정", "expression": "밥은 먹었냐", "sourceTranscriptSegmentIds": ["seg-uuid"] }]
  },
  "safetyFlags": [
    { "type": "health", "description": "허리 통증 언급", "sourceTranscriptSegmentIds": ["seg-uuid"] }
  ],
  "provider": "openai", "model": "gpt-5.4-mini"
}
```

요청 필드:
- `subjectContext`: 인물·기억 분석에 대상자 이름·가족 정보가 필요하다. transcription과 같은 형태로 함께 보낸다(없어도 동작하나 지칭 해소·상대 확정 정확도가 떨어짐).
- `subjectSpeakerLabel` (optional): 녹음에서 대상자가 어느 화자인지. **미지정 시 AI가 발화량 최다 화자로 자동 판정**(음성ID 서빙 연결 전까지의 기본).
- `conversationPartnerName` (optional): 대상자와 통화한 상대 이름(업로드 시 사용자 선택). **우리가 계속 요청해온 "통화 상대 입력" 이슈의 해법.** 값이 오면 대상자 아닌 화자를 이 이름으로 확정해 발화를 정확히 귀속한다(없으면 "상대" 처리, 하위호환). — STT만 하는 전사 요청이 아니라 **이 분석 호출에 실어야** 쓰인다.

응답 필드:
- `memorySegments` (3-A → Memories): `sourceTranscriptSegmentIds`는 요청 세그먼트 `id`(UUID)로 매핑(원본 재생·근거). `importanceScore`(1~10)·`tags`(통제 어휘)·`relatedPeople`·`sensitivityFlags`·`confidence`(confirmed|inferred)는 명세 ANL-006·3-B 계약대로.
- `summary` (3-C → Archive 목록/상세), `tags` (녹음 대표 주제 상위 → Memories 필터).
- `speechStyle` (2단계 ④ → Voice Persona 말투): 반복 말버릇·호칭·문장패턴·감정표현. `sentencePatterns`만 근거 없음(관찰 일반화).
- `safetyFlags` (민감플래그 → 금기·가족검수 안전노트): `type`은 health/familyConflict/asset/death/thirdParty.
- **미포함**: `personaMaterials`(reflection)는 녹음 여러 건 누적이 필요한 P2라 이 응답에 없음(추후 별도).

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
