# AI <-> 백엔드 연동 요청 ver5 — 4단계 reflection + voice STT/TTS

ver4에 없던 신규·이식 항목이다. **§1~2 reflection**: 녹음이 여러 건 쌓이면 개별 기억을 가로질러 대상자의 성향·가치관을 요약하는 통찰을 만들고 페르소나에 반영한다(P2, 누적 필요). **§4 voice**: 옛 MVP의 음성 STT/TTS를 새 서버로 이식한 컷오버(경로만 교체). **ver4와 충돌 없음**: §1은 새 엔드포인트, §2는 기존 `/assembly`에 옵션 필드 추가(안 보내면 종전과 동일), §4는 옛 `/ai/*` 음성 경로를 새 `/v1/voice/*`로 옮기는 것이다.

## 1. [요청] 통찰 도출 — `POST /v1/persona/reflection` (구현 완료)

대상자의 **누적 기억 조각들**을 받아, 여러 기억을 가로지르는 상위 통찰을 만들어 돌려준다(개별 사실이 아니라 반복·수렴하는 성향·가치관). 각 통찰은 근거가 된 기억 id로 역추적된다. **언제 호출하나**: 새로 저장된 기억의 importance 합이 임계값을 넘거나, 수동 재빌드 시. (매 녹음마다 부를 필요 없음.)

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

## 3. 반영 시점 (reflection)

reflection·personaInsights는 **P2**다. ver4의 컷오버(채팅·임베딩·recording)를 먼저 붙이고, 녹음이 실제로 여러 건 쌓여 통찰이 의미 있어지는 시점에 반영하면 된다. 지금 당장 BE가 할 일은 없고, 계약만 확정해둔다.

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
  - 클론 생성: 이미 클론이 있는 대상자는 그 voiceId를 쓰면 되고, 새 대상자용 클론 자동 생성(음성 클로닝, 명세 VPA-006~009)은 별도 P2다.
  - 서버 env: `ELEVENLABS_SPEED`(기본 속도), `ELEVENLABS_MODEL`(기본 `eleven_multilingual_v2`) — 필요 시 Render에 설정. `ELEVENLABS_DEFAULT_VOICE_ID`는 위 이유로 보통 비워둔다.
