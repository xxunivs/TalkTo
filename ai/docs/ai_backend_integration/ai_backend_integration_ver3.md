# AI <-> 백엔드 연동 요청 ver3 — intakeContext 부록 + 페르소나 서빙 계약

두 묶음이다. 1~3은 ver1 계약 2에서 미뤄둔 intakeContext 문항 키 확정, 4~5는 페르소나 채팅(응답 생성) 서빙 계약이다. 아래 키·필드는 AI 파이프라인이 실제로 소비하는 것 = 정본이며, BE는 저장 구조를 바꿀 필요 없이 호출 시 이 모양으로 변환·전달하면 된다.

## 1. [확정] intakeContext 섹션별 키·타입·용도

모든 키는 camelCase. 값은 사람이 읽는 서술 문자열이 기본(AI가 프롬프트 슬롯에 그대로 녹임) — 별도 코드화·enum 불필요.

AI는 아래 필드를 **있으면 모두 쓴다**(별도 등급 없음). 백엔드는 Intake에서 **있는 값만** 보내면 되고, 없는 필드는 AI가 건너뛴다(에러 없음 — 그 값이 하던 역할만 빠진다). **이 표는 현재 AI가 소비하는 키 기준이며, 이후 필드가 추가될 수 있다(미정의 키는 계속 무시되므로 추가는 하위호환). 키 rename 같은 깨지는 변경만 별도 통지한다.**

| 섹션 키 | 타입 | AI 용도 |
|---|---|---|
| `basicProfile` | object | 아래 1-1 |
| `speechStyle` | string | 말투 슬롯(예: "짧고 담담한 단문, 요리·추억은 길게") |
| `personality` | string | 성격·가치관 슬롯 |
| `familyMap` | object[] | 가족별 응답 톤(아래 1-2). 페르소나 톤 차별화의 1차 소스 |
| `situationalReactions` | object[] | 상황별 응답 지침(아래 1-3) |
| `tabooTopics` | string[] | 먼저 꺼내면 안 되는 화제(예: "상속·재산", "정치") |
| `memoryCards` | object[] | 아래 1-4. 채팅 런타임에 RAG로 주입(조립 시 미주입) |
| `sttHints` | object | 아래 1-5 |

### 1-1. basicProfile

| 키 | 타입 | 용도 |
|---|---|---|
| `status` | string | "사망"이면 사후 페르소나 프레이밍 자동 적용 |
| `oneLine` | string | 한 줄 소개(기본 정보 슬롯) |
| `deathContext` | string \| null | 사망 배경. **AI는 이 원문을 페르소나 프롬프트에 넣지 않는다**(사인 누출 방지). status 판단·안전 처리용으로만 보관 |
| `familyStatusNow` | string \| null | 현재 미소비(과공유 방지로 프롬프트 제외). 보내도 무시됨 |

### 1-2. familyMap[]

| 키 | 타입 | 용도 |
|---|---|---|
| `name` | string | 가족 이름(subjectContext.familyMembers와 동일 표기) |
| `relation` | string | 관계(예: "큰딸", "손자") |
| `tone` | string | 이 사람에게 하는 말투·챙김(예: "담배 걱정하는 톤"). 빈 문자열 허용 |

### 1-3. situationalReactions[]

| 키 | 타입 | 용도 |
|---|---|---|
| `situation` | string | 트리거 상황(예: "보고 싶어요") |
| `response` | string | 대상자가 할 법한 답(스크립트) |
| `avoid` | string \| null | 피해야 할 답 |

### 1-4. memoryCards[]

| 키 | 타입 | 용도 |
|---|---|---|
| `title` | string | 카드 제목 |
| `content` | string | 기억 내용. 채팅 시 질의 관련된 것만 RAG로 주입 |

### 1-5. sttHints

| 키 | 타입 | 용도 |
|---|---|---|
| `names` | string[] | STT 인식 힌트용 이름 |
| `voiceSampleRef` | object \| null | 화자식별 reference 등록. `documentId`·`startMs`·`endMs`(ver2 요청 2건과 동일 형태) |

## 2. [정리] ver1 예시에서 빠지는 키

ver1 계약 2의 intakeContext 예시에 있던 `timeline`, `sensoryMemories`는 현재 파이프라인이 소비하지 않는다. **변환 대상에서 제외**하면 된다(보내도 무시). 새로 채워야 하는 건 위 1의 키뿐이다.

## 3. [확인] 표기 일치

`familyMap[].name`은 subjectContext.familyMembers[].name과 같은 표기여야 톤 매칭이 된다(서로 다른 표기면 그 가족 톤이 적용되지 않음). 두 소스가 같은 이름 필드를 공유하도록 변환해주면 된다.

## 4. [요청] 페르소나 응답 서빙 — `POST /v1/persona/responses`

채팅 시점에 AI가 페르소나 응답을 생성한다. **기억 벡터 검색은 BE가 하고, AI는 넘겨받은 기억을 프롬프트에 주입해 응답**한다(AI는 stateless — DB 미접근). 요청/응답 형태:

```json
// 요청
{
  "message": "할머니 어디서 사셨어?",
  "history": [
    { "role": "user", "content": "할머니, 나 도윤이야." },
    { "role": "assistant", "content": "어이구 우리 도윤이." }
  ],
  "memories": [
    { "id": "mem-uuid", "title": "한골로 이사한 시골살이",
      "content": "평생 강원 시골 단독주택…", "tags": ["거주"] }
  ],
  "persona": { "subjectId": "uuid", "instructions": "<조립된 페르소나 프롬프트>", "voiceId": null }
}
// 응답
{ "content": "강원 시골에서 평생 살았다, 한골로 이사 와서 십오 년쯤…",
  "retrievedMemoryIds": ["mem-uuid"], "provider": "openai", "model": "gpt-4.1-mini" }
```

BE 담당 2가지:
1. **기억 임베딩 저장 + 채팅 시 top-k 검색.** 3-A로 추출한 memorySegment들을 `POST /v1/embeddings`로 벡터화해 저장(pgvector 등)하고, 채팅마다 사용자 메시지를 임베딩해 관련 상위 k개를 뽑아 `memories`에 담는다. **k는 5~8 권장**(내부 평가 Recall@5 100%). 관련 없으면 빈 배열 — AI가 "그건 잘 모르겠다"로 방어한다.
2. **조립본 전달.** 저장해 둔 페르소나 조립 프롬프트를 `persona.instructions`에 싣는다(아래 5).

- `memories[].id`로 넘긴 것 중 응답에 쓰인 것을 `retrievedMemoryIds`로 돌려준다(로깅·근거표시용).
- 검증됨: 같은 질문에 기억을 주면 정확히 회상, 안 주면 지어내지 않고 "모르겠다"로 받는다(과공유 없음).

## 5. [요청] 페르소나 조립본 산출·저장 — `POST /v1/persona/assembly`

위 `persona.instructions`는 파이프라인 산출물 + intakeContext로 AI가 조립한 system 프롬프트 문자열이다. AI가 조립해서 돌려주는 엔드포인트를 제공한다(**LLM 미사용 · 무비용 · 즉시**). BE는 Voice Persona 신청 완료 시 1회 호출해 결과를 저장했다가, 채팅 때 `/responses`의 `persona.instructions`로 넘기면 된다.

```json
// 요청
{
  "subjectContext": { …ver1 계약 2 형태… },
  "intakeContext": { …위 1의 확정 키… },
  "speechExamples": ["뭐든지 적당히 하는 게 제일 힘든데.", "김치는 집에서 직접 담가야지."]
}
// 응답
{ "instructions": "<조립된 페르소나 system 프롬프트>", "subjectName": "최영자" }
```

- `speechExamples`: 대상자 **본인의 짧고 담백한 실제 발화**(말투 few-shot). BE가 전사에서 6~40자짜리로 몇 개(≤50) 추려 보낸다. 없으면 빈 배열(말투 예시만 비고 나머지는 조립됨).
- **BE 저장**: 응답 `instructions`를 대상자 단위로 저장할 text 컬럼(대상자당 1건, 재신청·재분석 시 갱신).
- 사후 페르소나(basicProfile.status="사망")면 사망 사인·경위는 조립본에 넣지 않는다(안전). status 플래그만으로 처리된다.

## 6. [요청] 전사 호출 타임아웃 상향 (AI_SERVER_TIMEOUT_MS)

현재 BE는 AI 호출에 45초(`AI_SERVER_TIMEOUT_MS=45000`)를 준다. 전사 처리 시간은 오디오 길이에 비례하고, 실측 최댓값이 **30분 녹음 ≈ 66초**라 긴 녹음이 45초를 넘겨 타임아웃될 수 있다.

- 요청: **전사 엔드포인트(`POST /v1/analysis/transcriptions`) 호출 타임아웃을 최소 90초, 권장 120초**로. 오디오 다운로드·재시도 여유 포함. persona·embeddings 등 다른 엔드포인트는 <10초라 45초 유지해도 무방(전역 상향도 무해 — 빠른 호출은 일찍 반환).
- 실측 근거(골드 전사 실행): 30.0분 66초 / 16.1분 44초 / 대부분 수십 초.
- 대안(선택): 30분보다 긴 녹음까지 늘어나면 동기 호출 대신 비동기(잡 제출 → 완료 콜백/폴링)로 전환. 현재 길이(≤30분)에선 타임아웃 상향으로 충분하다.
