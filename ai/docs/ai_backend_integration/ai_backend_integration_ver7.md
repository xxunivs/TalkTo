# AI <-> 백엔드 연동 공유 ver7 — 고인 목소리 TTS 임시 조치 (수정 요청 아님)

1. 현재 채팅에서 고인 목소리가 나오는 것은 **AI 서버의 임시 폴백** 때문이다. BE 코드는 고칠 것이 없고, 이 문서는 그 상태를 공유하기 위한 것이다.

2. 무엇을 했나
   - BE는 TTS 요청에 `voiceId: persona.voiceId`를 항상 싣는데, 그 값이 자리표시자 `'default-voice'`다(`personas.service.ts`·`bootstrap.service.ts`의 기본값). 이는 ElevenLabs에 존재하지 않는 id라, 종전에는 합성이 실패하고 텍스트로 폴백돼 **음성이 나오지 않았다**.
   - AI 서버가 **`'default-voice'`를 "voiceId 미지정"으로 간주**하고, 서버 전역 기본 음성(`ELEVENLABS_DEFAULT_VOICE_ID` = 신금자 클론 `lTmcD2Kp43w1lHsMQmq7`)으로 폴백하도록 했다.
   - 운영 서버 반영 완료. `POST /v1/voice/speech`에 `{"text":"…","voiceId":"default-voice"}`를 보내면 200 `audio/mpeg`로 고인 목소리가 나온다(검증 완료).

3. 이건 정상 동작이 아니다 — 한계
   - **서버 전역 폴백**이라, 지금은 **어떤 대상자로 대화해도 같은(신금자) 목소리**가 나온다. 대상자별 목소리가 아니다.
   - 따라서 대상자가 둘 이상 되기 전에 해제해야 한다.

4. 해제 조건 (BE가 할 일 — 클론 자동화 시점에)
   - `persona.voiceId`에 **실제 클론 `voiceId`를 저장**하면 된다. 실제 `voiceId`를 보내면 **그 값이 항상 우선**하므로, 저장하는 순간 이 폴백은 자동으로 비활성화된다. **AI 쪽 추가 작업은 없다.**
   - 그 뒤 AI는 `ELEVENLABS_DEFAULT_VOICE_ID`를 비워 fail-safe(voiceId 누락 시 400 `MISSING_VOICE_ID`)로 되돌린다.

5. 주의 — 이 폴백을 모른 채 방치하면
   - "목소리가 잘 나온다"고 판단해 `persona.voiceId` 저장을 넘어가기 쉽다. 그러면 대상자가 늘어나는 순간 **전원이 같은 목소리**로 말한다.
   - AI가 임의로 `ELEVENLABS_DEFAULT_VOICE_ID`를 비우면 **음성이 조용히 사라진다**(400 → BE catch → 텍스트 폴백). 해제는 4번 반영을 확인한 뒤에만 한다.

6. 데모 환경 점검 요청 (음성이 안 나올 때 원인 3가지 — 모두 조용히 텍스트로 폴백된다)
   - `AI_SERVER_URL`이 새 AI 서버를 가리키는가 (미설정 시 `isConfigured()`가 false → 합성 호출 자체가 생략된다).
   - `AI_SERVER_TOKEN`이 AI 서버 값과 일치하는가 (불일치 시 401).
   - 데모 계정에 **VOICE_PERSONA 필수 동의**가 granted인가 (미동의 시 `assertProviderConsents`에서 예외).

7. 음성 클론 자동화(`POST /v1/voice/clone`)는 아직 호출되는 경로가 없어 이 문서 범위 밖이다. 착수 시점에 `clone_automation_checklist.md`를 참고하면 된다(스펙 변경 1건 + BE 수정 4건 정리해 둠).
