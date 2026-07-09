# 음성 클론 자동화 착수 시 체크리스트 (BE)

> 지금은 클론이 호출되는 경로가 없어 **당장 조치할 것은 없다.** `cloneVoice` ← `cloneApprovedVoiceSample` ← `reviewVoiceSample`(관리자 승인)이고, 승인 대상 `target_voice_samples` 행은 `POST /voice-persona/applications/{id}/voice-samples`로만 생기는데 앱에 그 화면이 없다. 아래는 **클론을 실제로 켜는 시점**에 함께 반영할 목록이다.

1. [스펙 변경 — 이미 운영 서버 반영됨] `POST /v1/voice/clone`의 `samples[]`는 `startMs`/`endMs`가 **필수**다.
   - ver6 §3의 "둘 다 비우면 그 오디오 전체 사용" 문장은 **폐기**한다. 구간 없는 원소를 허용하면 통화 녹음 전체가 학습 재료가 되고, 통화에는 상대 화자(가족) 목소리가 섞여 있어 **대상자가 아닌 목소리로 클론**된다.
   - 이미 잘라놓은 클립 하나를 쓰려면 종전대로 `sampleAudioUrl`(단일 모드)로 보낸다.
   ```json
   // 다구간: 각 원소에 startMs·endMs 필수
   { "name": "외할머니 신금자", "samples": [
       { "audioUrl": "https://…(녹음1)", "startMs": 5000,  "endMs": 13000 },
       { "audioUrl": "https://…(녹음2)", "startMs": 40000, "endMs": 52000 } ] }
   // 단일 클립(변경 없음)
   { "name": "외할머니 신금자", "sampleAudioUrl": "https://…(클립)" }
   ```
   - `endMs > startMs`. 상한: 이어붙인 총 길이 **≤180초**(`createdAt` 순으로 채우고 중단), 자른 뒤 길이가 **800ms 미만인 구간은 버림**.

2. [에러 코드 신설] `422 INVALID_VOICE_SAMPLE`
   - 구간이 오디오 범위를 벗어났거나, 자른 뒤 유효 구간이 하나도 없거나, 컷/이어붙이기가 실패하면 반환된다.
   - 종전에는 이 경우도 `502 TTS_PROVIDER_ERROR`였다. 502는 재시도해도 같은 결과인 입력 문제를 AI 장애로 오인하게 만들어 분리했다. 이제 502는 프로바이더/서버 문제일 때만 나온다.
   - BE는 422를 **재시도하지 말고** 샘플 구간을 고쳐 재호출해야 한다.

3. [BE 수정] 클론한 목소리가 일반 채팅 TTS에 반영되지 않는다 (치명)
   - `cloneApprovedVoiceSample`(`voice-persona.service.ts:423`)이 `voiceId`를 `voice_provider_assets.externalAssetId`에만 저장한다. 그런데 `conversations.service.ts:162,278`의 TTS는 `persona.voiceId`를 쓰고, 이 값은 `'default-voice'` 리터럴로만 대입된다(`personas.service.ts:25`, `bootstrap.service.ts:78`).
   - 요청: 클론 성공 시 `persona.voiceId`에 반환된 `voiceId`를 저장할 것. 이것이 ver7 임시 폴백의 **해제 조건**이기도 하다.
   - 참고: `persona-runtime` 경로는 `loadVoiceId`가 `runtimeConfig.providerAssetId`로 해결하므로 정상 동작 중 — 두 경로가 갈려 있다.

4. [BE 수정] 구간 없는 샘플이면 통화 전체를 클론 재료로 보낸다 (치명)
   - `targetVoiceSampleStorageKey`(`:488`)는 `sample.storageKey`가 없으면 **원본 통화 전체**(`recording.storageKey`)로 폴백하고, `targetVoiceCloneSample`은 `startMs/endMs`가 null이면 구간 없이 `{audioUrl}`만 보낸다. `CreateTargetVoiceSampleDto`가 `recordingId`만 있어도 유효하므로(`assertVoiceSampleRange`가 둘 다 null 허용) 이 경로가 열려 있다.
   - 요청: `recordingId` 폴백으로 원본 녹음을 쓸 때는 `startMs/endMs`를 **필수**로 할 것(없으면 그 샘플 제외). 1번대로 AI는 구간 없는 원소를 422로 거부한다.
   - 함께: `storageKey`(이미 잘린 클립)와 `startMs/endMs`(원본 기준 오프셋)를 동시에 저장할 수 있어 의미가 모호하다. 이중 컷이 되지 않도록 `storageKey`가 있으면 구간을 무시하거나, `storageKey`를 원본 녹음 키로만 한정할 것.

5. [BE 수정] 샘플을 승인할 때마다 ElevenLabs 음성이 중복 생성된다
   - `reviewVoiceSample`(`:384`)이 `APPROVED`마다 `cloneApprovedVoiceSample`을 호출하고, 그 함수는 그때까지 승인된 **모든** 샘플로 새 클론을 만든다. 샘플 3개를 순차 승인하면 클론 3개 + `voice_provider_assets` 3행이 생긴다. ElevenLabs 음성 슬롯은 구독별 상한이 있어 소진·과금된다.
   - 요청: 이미 `REGISTERED` asset이 있으면 건너뛰거나, 샘플 승인과 클론 실행을 분리해 **대상자당 1회만** 클론할 것.

6. [BE 수정] 클론 실패 시 500 + 상태 불일치
   - `cloneApprovedVoiceSample` 호출부에 try/catch가 없다. AI가 422(구간 무효)나 프리사인 만료로 실패하면 예외가 그대로 올라간다. 그 직전에 `sample.reviewStatus`·`application.voiceSampleStatus`는 이미 저장됐고 감사로그(`auditVoicePersonaAdminAction`)는 아직 남기지 않아, **승인은 반영됐는데 API는 500, 감사로그 누락**이 된다. 재시도하면 5번대로 중복 클론된다.
   - 요청: 클론 실패를 catch해 승인 트랜잭션과 분리하고(빌드 상태를 `failed`류로 표시), 감사로그는 남길 것.

7. [설계 미정] 다구간의 "화자 식별 기반 자동 수집"은 아직 구현되지 않았다.
   - `targetVoiceCloneSamples`는 승인된 `target_voice_samples` 행만 모은다. 사용자가 구간을 하나만 지정하면 `samples` 길이가 1이라 **사실상 단일 클립**이고, 다구간의 품질 이점(샘플 길이·다양성)이 나지 않는다.
   - 전사의 `speakerLabel == subjectSpeakerLabel` 구간을 누가 수집해 행으로 넣을지(앱 화면 vs BE 자동 생성) 결정이 필요하다.

8. [확인됨 — 수정 불필요] `samples[]` camelCase 전송, `sampleAudioUrl` XOR `samples` union 타입 강제, `take: 100`(AI 상한과 일치), `endMs > startMs` 검증, `provider-call-gateway`가 배열·중첩 객체까지 재귀하며 `voice_clone`에서 `audioUrl` 키를 허용하도록 처리한 부분, TTS 호출에 `voiceId` 동봉(ver5 §4), reflection·`personaInsights`·`referenceVoiceSampleUrl`·`subjectSpeakerLabel` 배선.
