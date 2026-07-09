# AI <-> 백엔드 연동 요청 ver6 — 음성 클론 다구간화 (ver5 §4-1 대체)

1. **ver5의 §4-1(음성 클론 — 단일 클립)은 이 문서로 대체한다.** ver5의 나머지(§1 reflection, §2 personaInsights, §3 재빌드 트리거, §4 voice STT/TTS·속도, §5 컷오버 주의, §6 화자 자동 식별)는 **그대로 유효**하다. 엔드포인트 경로·응답 형식은 §4-1과 동일(`POST /v1/voice/clone` → `{ voiceId, provider }`), 입력만 다구간으로 확장됐다.

2. 왜 바꾸나 (근거)
   - ElevenLabs IVC(instant voice cloning) 품질은 샘플의 **길이·다양성**에 민감하다. 소스가 전화 통화(8kHz·잡음·상대방 겹침)라, 짧은 단일 구간 하나로는 유사도 한계가 크다.
   - 대상자 화자 구간은 **이미 파이프라인이 안다**: `/analysis/recording`이 `subjectSpeakerLabel`을 확정하고, 전사 세그먼트마다 `speakerLabel`·`startMs`·`endMs`가 있다. 그 구간들을 **여러 통화에서 모아** 이어붙이면 샘플이 길고 다양해져 클론 품질이 올라간다. (검증: 통화 1건 안에서 3구간을 이어붙여 클론→TTS 정상.)

3. [요청] 음성 클론 생성 — `POST /v1/voice/clone` (구현 완료, 입력 확장)

   입력은 **두 방식 중 정확히 하나**다.

   ```json
   // 방식 A — 다구간(권장). 대상자 화자 구간들을 AI가 잘라 이어붙여 한 목소리로 학습.
   {
     "name": "외할머니 신금자",
     "samples": [
       { "audioUrl": "https://... (녹음1 presigned GET)", "startMs": 5000,  "endMs": 13000 },
       { "audioUrl": "https://... (녹음1 presigned GET)", "startMs": 20000, "endMs": 31000 },
       { "audioUrl": "https://... (녹음2 presigned GET)", "startMs": 40000, "endMs": 52000 }
     ]
   }

   // 방식 B — 단일 클립(ver5 §4-1 호환, 그대로 동작). 이미 잘린 클립 1개.
   { "name": "외할머니 신금자", "sampleAudioUrl": "https://... (presigned GET, 샘플 클립)" }

   // 응답(두 방식 공통)
   { "voiceId": "xxxxxxxxxxxxxxxxxxxx", "provider": "elevenlabs" }
   ```

   - `samples[]`: 대상자 화자 구간 목록(최대 100개). 각 원소 `{ audioUrl, startMs?, endMs? }`.
     - `audioUrl`: 그 구간이 들어있는 **녹음 오디오**의 presigned URL(전사에 쓴 그 파일). 구간별로 다른 녹음이어도 된다.
     - `startMs`/`endMs`: 그 오디오 기준 구간. **둘 다 주거나 둘 다 비운다**(비우면 그 오디오 전체 사용). `endMs > startMs`.
   - `name`: 클론 음성 라벨(대상자 식별용).
   - AI 처리: 각 구간을 잘라 mono 22050 wav로 정규화 → 이어붙임 → 클론 → `voiceId`. **AI가 슬라이싱·이어붙이기·길이 제한을 다 처리**한다(BE가 미리 자를 필요 없음). 상한: 이어붙인 총 길이 **≤180초**(초과분은 앞에서부터 채우고 중단), **800ms 미만 구간은 버림**.

4. BE가 `samples[]`를 채우는 법 (화자 식별 결과 재사용)
   - 그 대상자의 **승인된 통화들**에서, 전사 세그먼트 중 `speakerLabel == subjectSpeakerLabel`(= §6 화자 식별로 확정된 대상자 화자)인 것들을 골라 `{ audioUrl(그 녹음), startMs, endMs }`로 매핑한다. 여러 통화에 걸쳐 모으는 게 목적이다.
   - `target_voice_samples` 테이블은 이미 `(recordingId, startMs, endMs)`를 **application당 여러 행** 저장하는 구조라, 그 행들을 그대로 `samples[]`로 보내면 된다. 사용자가 구간을 하나만 지정했으면 `samples` 1개로 보내도 되고(그럼 방식 B와 사실상 동일), 화자 식별로 자동 수집하면 여러 개가 된다.

5. BE 현행 코드와의 갭 (요청 근거)
   - 현재 BE는 `/voice/clone`을 **호출하는 경로가 없다**(`ai-client.service.ts`에 clone 메서드 부재, `src`·`docs` 전체에 `clone`/`voices/add` 문자열 없음). 그리고 `persona.voiceId`가 `'default-voice'`로 **하드코딩**돼 있어(`bootstrap.service.ts`·`personas.service.ts`), 지금 TTS는 대상자 목소리가 아니라 기본 음성으로 나간다.
   - 필요한 연결 두 가지: **① voice sample 승인(`reviewStatus`) 후 build job에서 `POST /v1/voice/clone` 호출**(위 `samples[]`로) → **② 받은 `voiceId`를 `persona.voiceId`에 저장**. 이후 TTS는 ver5 §4대로 `{ text, voiceId: persona.voiceId }`로 실어 보내면 그 목소리로 나온다.

6. 주의
   - 구간 좌표(`startMs`/`endMs`)는 그 `audioUrl` 오디오 기준(전사에 쓴 원본과 동일 파일이어야 정렬이 맞다).
   - `audioUrl`은 presigned라 만료에 유의(AI가 각 URL을 개별 다운로드한다). 만료 시 그 구간만 실패가 아니라 요청 전체가 실패하니, 호출 직전에 발급할 것.
   - 방식 B(`sampleAudioUrl`)는 유지되므로, 당장 다구간을 못 채우면 단일 클립으로 먼저 붙이고 나중에 `samples[]`로 넓혀도 된다.
