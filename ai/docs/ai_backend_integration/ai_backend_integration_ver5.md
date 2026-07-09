# AI <-> 백엔드 연동 요청 ver5 — reflection · voice(STT/TTS·클론·속도) · 화자식별 · 컷오버 주의

ver4에 없던 신규·이식 항목이다.
- **§1~3 reflection**: 누적 기억에서 성향·가치관 통찰을 만들어(§1) 페르소나 성격·가치관에 반영(§2), BE 페르소나 빌드 잡에 2콜로 연결(§3).
- **§4 voice STT/TTS**: 옛 MVP 음성 경로를 새 서버로 이식한 컷오버(경로만 교체). 말하기 속도 조절 포함.
- **§4-1 음성 클론**: 고인 목소리 샘플 → 클론 `voiceId` 발급(대상자별 목소리 자동화).
- **§5 컷오버 공통 주의**: 모든 새 `/v1/*` 엔드포인트의 camelCase·엄격검증(ver4 포함).
- **§6 화자 자동 식별**: 참조 목소리 샘플로 녹음 속 대상자 화자를 성문 매칭 확정.

**ver4와 충돌 없음**: 새 엔드포인트(§1·§4-1·§6 필드추가)이거나 기존 요청에 옵션 필드 추가(§2·§4)라, 안 보내면 종전과 동일하다. §4-1 클론과 §6 화자식별은 **같은 고인 목소리 샘플 하나**(FE 구간 선택 결과)를 공유한다.

## 1. [요청] 통찰 도출 — `POST /v1/persona/reflection` (구현 완료)

대상자의 **누적 기억 조각들**을 받아, 여러 기억을 가로지르는 상위 통찰을 만들어 돌려준다(개별 사실이 아니라 반복·수렴하는 성향·가치관). 각 통찰은 근거가 된 기억 id로 역추적된다. **언제 호출하나**: 페르소나를 (재)빌드할 때 — 구체 흐름·트리거는 §3.

```json
// 요청
{
  "subjectContext": { …ver1 계약 2 형태(대상자·가족)… },
  "memories": [
    { "id": "mem-1", "memoryText": "명절마다 가족이 다 모였다.",
      "tags": ["가족안부"], "importanceScore": 7 },
    { "id": "mem-2", "memoryText": "가족과 함께 밥 먹는 걸 좋아했다.",
      "tags": ["가족안부"], "importanceScore": 6 }
  ]
}
// 응답
{
  "reflections": [
    { "insight": "가족이 함께 모여 식사하는 시간을 중요하게 여긴다.",
      "category": "가치관", "evidenceMemoryIds": ["mem-1", "mem-2"],
      "importance": 8 }
  ],
  "provider": "openai", "model": "gpt-5.4-mini"
}
```

- `memories[].id`: BE의 기억 식별자. 통찰의 `evidenceMemoryIds`로 그대로 돌려주므로 근거 역추적이 된다.
- `insight`: 한 문장 3인칭 서술. **두 개 이상의 기억이 뒷받침하는 것만** 통찰로 나온다(단일 사실은 제외 — 그건 이미 memorySegment).
- `category`: 가치관 / 성향 / 반복주제 / 관계 / 생애서사 중 하나.
- `evidenceMemoryIds`: 그 통찰의 근거 기억 id(요청에 있던 것). 항상 2개 이상.
- **BE 저장**: 이 통찰들을 대상자 단위로 저장(personaMaterials). 재실행 시 갱신. 통찰 자체도 중요도(`importance`)를 가지므로 Memories/프로필 노출에 쓸 수 있다.

## 2. [요청] 통찰을 페르소나에 반영 — `/assembly`에 `personaInsights` 추가

위에서 저장한 통찰을 페르소나 조립 시 넘기면, **성향·가치관** 슬롯에 합쳐진다(Intake personality + 통찰). ver3 §5의 `/v1/persona/assembly` 요청에 **옵션 필드 하나**만 더한다.

```json
// POST /v1/persona/assembly 요청 (ver3 §5 + personaInsights)
{
  "subjectContext": { … },
  "intakeContext": { … },
  "speechExamples": ["…"],
  "personaInsights": [
    "가족이 함께 모여 식사하는 시간을 중요하게 여긴다.",
    "손이 덜 가는 음식을 선호하는 실용적인 식사 습관이 있다."
  ]
}
```

- `personaInsights`: reflection이 만든 `insight` 문자열 목록(≤50). BE가 저장해둔 통찰을 실어 보낸다.
- **하위호환**: 안 보내면(빈 배열) 종전 assembly와 동일하다 — Intake만으로 조립. 통찰이 쌓이면 프로필이 파이프라인 산출로 풍부해진다.
- 응답은 ver3 §5 그대로(`instructions`, `subjectName`).

## 3. [요청] 페르소나 (재)빌드 흐름 — reflection·assembly 호출(트리거 포함)

**트리거**: BE는 **녹음 분석(`/v1/analysis/recording`)이 끝나 기억을 저장한 직후, 그 대상자의 페르소나를 재빌드한다**(기존 `persona-build-job`에서). 새 녹음이 분석될 때마다 아래를 실행:

1. 그 대상자의 **저장된 기억 전체**를 모아 `POST /v1/persona/reflection` 호출 → 통찰 목록(`reflections[].insight`).
2. `POST /v1/persona/assembly`에 context와 그 통찰을 **`personaInsights`(insight 문자열 목록)**로 실어 호출 → 페르소나 프롬프트(`instructions`).
3. `instructions`를 `PersonaBible`에 저장하고, 채팅(`/responses`)은 이 저장본을 사용.

이러면 **새 녹음이 들어올 때마다 최신 기억·통찰이 반영된 페르소나로 갱신**된다. 기억이 1~2건이라 통찰이 빈 배열이어도 2번은 `personaInsights=[]`로 Intake 기반 조립이 정상 동작하므로, 빌드 흐름에 위 3단계를 그대로 넣으면 된다.

## 4. [요청] voice STT/TTS 컷오버 — `/ai/stt`·`/ai/tts` → `/v1/voice/*`

옛 MVP AI의 음성 STT/TTS를 새 서버로 이식 완료했다. BE의 `transcribe()`·`synthesizeSpeech()`가 부르던 경로만 바꾸면 된다(요청/응답 형태는 거의 그대로 — 드롭인에 가깝다).

| 기능 | 옛(현재 BE) | 새 경로 | 요청/응답 |
|---|---|---|---|
| 음성 메시지 STT | `/ai/stt` | `POST /v1/voice/transcriptions` | 멀티파트 `audio_file` → `{ text, provider, model }` |
| 페르소나 답변 TTS | `/ai/tts` | `POST /v1/voice/speech` | `{ text, voiceId? }` → **audio/mpeg 바이트** |

- **STT**: BE `transcribe()`가 보내는 멀티파트 필드 `audio_file` 그대로 받는다. 응답 `text`를 읽으면 된다(옛 `stt_text`도 호환되게 BE가 `stt_text ?? text`로 읽고 있음 — `text`로 옴). OpenAI whisper 사용.
- **TTS**: `{ text, voiceId?, speed? }`를 보내면 audio/mpeg 바이트를 돌려준다(옛 `/ai/tts`와 호환). **voiceId를 함께 보내면 그 음성으로 합성**한다. voiceId가 없으면 서버 기본값이 설정돼 있을 때만 그걸로 폴백하고, 기본값도 없으면 400(`MISSING_VOICE_ID`)이다(대상자별 운용 권장 — 아래 참고).
  - **말하기 속도**: `speed`(0.7~1.2, 낮을수록 느림)를 요청에 넣거나 서버 env `ELEVENLABS_SPEED`로 전역 설정한다(요청값 우선, 둘 다 없으면 파라미터 중립값 1.0). **현재 서버 기본은 0.9**(대표 피드백 "조금 빠름" 반영). 실측(같은 문장, 한 실행): 1.2→약 4.0초, 1.0→약 5.4초, 0.8→약 6.1초로 단조롭게 느려짐(생성마다 길이 변동은 있음). 속도는 대상자와 무관한 전역 취향이라 BE가 매번 보낼 필요 없다.
  - **대상자별 목소리(중요)**: 목소리는 대상자마다 다르다. BE엔 **이미 `persona.voiceId` 필드가 있으므로**(update DTO로 설정 가능, 현재 기본값 `'default-voice'` 플레이스홀더) 저장소를 새로 만들 필요는 없다. 필요한 건 두 가지다: **① `persona.voiceId`에 실제 클론 id를 채우고**(신금자 = `lTmcD2Kp43w1lHsMQmq7`), **② TTS 호출 시 그 값을 요청에 싣기** — 현재 `synthesizeSpeech`가 `{ text }`만 보내므로(ai-client.service.ts) `{ text, voiceId: persona.voiceId }`로 추가하면 된다.
  - 서버 전역 기본 voiceId(`ELEVENLABS_DEFAULT_VOICE_ID`)는 **설정하지 않는 것을 권장**한다: 설정해두면 voiceId 없는 요청이 조용히 그 목소리로 나가버린다. 비워두면 voiceId 누락 시 400(`MISSING_VOICE_ID`)으로 안전하게 실패한다(단일 대상자 데모에서만 편의로 채움).
  - 서버 env: `ELEVENLABS_SPEED`(기본 속도), `ELEVENLABS_MODEL`(기본 `eleven_multilingual_v2`) — 필요 시 Render에 설정. `ELEVENLABS_DEFAULT_VOICE_ID`는 위 이유로 보통 비워둔다.

### 4-1. [요청] 음성 클론 생성 — `POST /v1/voice/clone` (구현 완료)

대상자별 `voiceId`를 만드는 자동화. 고인 목소리가 잘 들리는 샘플 클립을 받아 클론 음성을 등록하고 `voiceId`를 돌려준다. BE는 이 값을 `persona.voiceId`에 저장했다가 이후 TTS 요청에 실어 보낸다.

```json
// 요청
{ "name": "외할머니 신금자", "sampleAudioUrl": "https://... (presigned GET, 샘플 클립)" }
// 응답
{ "voiceId": "xxxxxxxxxxxxxxxxxxxx", "provider": "elevenlabs" }
```

- `sampleAudioUrl`: BE가 `TargetVoiceSample`(recordingId + storageKey + startMs/endMs — FE의 "고인 목소리 구간 선택" 결과)에서 **그 구간을 잘라낸 오디오 클립**의 presigned URL. BE는 이미 녹음+구간으로 클립을 뽑는 로직이 있다(memory-segments 재생용). AI 쪽 구간 슬라이싱은 불필요 — 잘린 클립만 주면 된다.
- `name`: 클론 음성 라벨(대상자 식별용).
- 호출 시점: VP 신청의 voice sample 승인(`reviewStatus`) 후 1회. 결과 `voiceId`를 저장하면 그 대상자 TTS가 그 목소리로 나온다.

## 5. 컷오버 공통 주의 (ver4 포함, 모든 새 `/v1/*` 엔드포인트)

옛 `/ai/*`와 새 `/v1/*`는 계약 스타일이 두 가지 다르다 — BE가 경로만 바꾸면 되는 대부분과 달리 이 두 가지는 코드 수정이 필요하다.

1. **필드는 camelCase**: 새 엔드포인트는 응답을 camelCase로 낸다(요청은 camel/snake 둘 다 받음). 옛 응답의 snake_case를 그대로 읽으면 안 된다 — 특히 **채팅 응답 `retrieved_memory_ids` → `retrievedMemoryIds`**. 임베딩·기억추출 응답은 형태 자체가 바뀌므로(ver4 §3·§5) 이미 새로 읽어야 한다.
2. **정의되지 않은 필드 거부(엄격 검증)**: 스키마에 없는 필드를 요청에 넣으면 422다. 필요한 필드만 보낸다. 특히 **`/v1/persona/responses`는 옛 채팅과 달리 `persona`(instructions 포함)가 필수** — 현재 BE `AiChatRequest`엔 없으니 추가해야 한다(ver3 §4). redaction으로 값만 마스킹하는 건 무방(필드 구조는 그대로면 됨).

## 6. [요청] 대상자 화자 자동 식별 — 전사에 `referenceVoiceSampleUrl` 추가 (구현 완료)

녹음에서 "어느 화자가 고인인지"를 성문 매칭(ECAPA)으로 확정한다. §4-1과 **같은 고인 목소리 샘플**을 전사 요청에 함께 주면, 응답에 `subjectSpeakerLabel`이 담긴다. BE는 그걸 `/v1/analysis/recording`의 `subjectSpeakerLabel`로 넘긴다 — "발화량 최다" 휴리스틱을 대체하는 정확한 근거다.

- `POST /v1/analysis/transcriptions` 요청에 `referenceVoiceSampleUrl`(샘플 클립 presigned URL, §4-1과 동일 소스) 추가.
- 응답에 `subjectSpeakerLabel`(확정된 대상자 화자 라벨). 참조 미제공·임계값 미달이면 `null`.
- BE 흐름: 전사 응답의 `subjectSpeakerLabel`을 저장했다가 `/recording` 요청의 `subjectSpeakerLabel`로 전달 → 인물·기억 분석이 정확한 대상자 기준으로 동작. `null`이면 `/recording`이 발화량 최다로 폴백.
- **하나의 목소리 샘플, 두 용도**: FE의 "고인 목소리 구간 선택" 1회 결과가 **§4-1 클론(voiceId 발급)**과 **§6 화자 식별(성문 매칭)** 양쪽에 쓰인다. BE는 그 샘플 클립 URL을 두 호출에 실어 보내면 된다.
- 서버: 첫 호출 때 ECAPA 모델 1회 다운로드로 지연이 있을 수 있음(이후 캐시). torch 미설치·매칭 실패 시 `subjectSpeakerLabel=null`로 graceful(전사는 정상).
