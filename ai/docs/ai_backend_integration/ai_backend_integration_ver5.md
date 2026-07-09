# AI <-> 백엔드 연동 요청 ver5 — 4단계 reflection (누적 기억 → 페르소나 프로필)

ver4에 없던 신규 항목이다. 녹음이 여러 건 쌓이면 개별 기억을 가로질러 대상자의 성향·가치관을 요약하는 통찰(reflection)을 만들고, 그걸 페르소나에 반영한다. **ver4와 충돌 없음**: 아래 1은 새 엔드포인트, 2는 기존 `/assembly` 요청에 옵션 필드 하나 추가(안 보내면 종전과 동일)다. 녹음 누적이 있어야 의미 있는 P2라 급하지 않다.

## 1. [요청] 통찰 도출 — `POST /v1/persona/reflection` (구현 완료)

대상자의 **누적 기억 조각들**을 받아, 여러 기억을 가로지르는 상위 통찰을 만들어 돌려준다(개별 사실이 아니라 반복·수렴하는 성향·가치관). 각 통찰은 근거가 된 기억 id로 역추적된다. **언제 호출하나**: 새로 저장된 기억의 importance 합이 임계값을 넘거나, 수동 재빌드 시. (매 녹음마다 부를 필요 없음.)

```json
// 요청
{
  "subjectContext": { …ver1 계약 2 형태(대상자·가족)… },
  "memories": [
    { "id": "mem-uuid", "memoryText": "명절마다 가족이 다 모였다.",
      "tags": ["가족안부"], "importanceScore": 7 }
  ]
}
// 응답
{
  "reflections": [
    { "insight": "가족이 함께 모여 식사하는 시간을 중요하게 여긴다.",
      "category": "가치관", "evidenceMemoryIds": ["mem-uuid-1", "mem-uuid-2"],
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

## 3. 반영 시점 (reflection)

reflection·personaInsights는 **P2**다. ver4의 컷오버(채팅·임베딩·recording)를 먼저 붙이고, 녹음이 실제로 여러 건 쌓여 통찰이 의미 있어지는 시점에 반영하면 된다. 지금 당장 BE가 할 일은 없고, 계약만 확정해둔다.

## 4. [요청] voice STT/TTS 컷오버 — `/ai/stt`·`/ai/tts` → `/v1/voice/*`

옛 MVP AI의 음성 STT/TTS를 새 서버로 이식 완료했다. BE의 `transcribe()`·`synthesizeSpeech()`가 부르던 경로만 바꾸면 된다(요청/응답 형태는 거의 그대로 — 드롭인에 가깝다).

| 기능 | 옛(현재 BE) | 새 경로 | 요청/응답 |
|---|---|---|---|
| 음성 메시지 STT | `/ai/stt` | `POST /v1/voice/transcriptions` | 멀티파트 `audio_file` → `{ text, provider, model }` |
| 페르소나 답변 TTS | `/ai/tts` | `POST /v1/voice/speech` | `{ text, voiceId? }` → **audio/mpeg 바이트** |

- **STT**: BE `transcribe()`가 보내는 멀티파트 필드 `audio_file` 그대로 받는다. 응답 `text`를 읽으면 된다(옛 `stt_text`도 호환되게 BE가 `stt_text ?? text`로 읽고 있음 — `text`로 옴). OpenAI whisper 사용.
- **TTS**: `{ text }`를 보내면 audio/mpeg 바이트를 돌려준다(옛 `/ai/tts`와 동일). **voiceId를 함께 보내면 그 음성으로 합성**하고, 없으면 서버의 `ELEVENLABS_DEFAULT_VOICE_ID`로 폴백한다.
  - ⚠️ **대상자별 클론 음성**: 실제로 고인 목소리로 들려주려면 대상자마다 클론된 `voiceId`가 필요하다. 그 **voiceId를 만드는 음성 클로닝(명세 VPA-006~009)은 아직 별도 P2 작업**이다. TTS 엔드포인트 자체는 voiceId만 받으면 동작하므로, 클로닝이 붙기 전까지는 BE가 기본 voiceId(또는 대상자별로 수동 등록한 voiceId)를 보내면 된다.
  - 서버 env: `ELEVENLABS_DEFAULT_VOICE_ID`(폴백용), `ELEVENLABS_MODEL`(기본 `eleven_multilingual_v2`) — 필요 시 Render에 설정.
