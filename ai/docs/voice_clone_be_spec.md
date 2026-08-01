# 음성 클론 엔드포인트와 구현 스펙 (BE/FE)

## 0. 지금 상태 (IVC / PVC)

1. 지금 클론은 IVC(즉시 생성)로 자동 완결된다. 재료 1~3분이면 되고 파이프라인이 여기까지 스스로 만든다. 아래 엔드포인트가 주는 `voiceId`는 IVC 클론이다.
2. PVC(고품질)는 자동화 못 한다. ElevenLabs 정책상 대상자 목소리 PVC는 사망확인서와 가족동의서를 제출해 수동 승인받아야 하고, 인물마다 심사가 붙어 API로 자동 생성이 안 된다.
3. 그래서 PVC는 자리만 만들어 둔 상태다. 재료 풀과 상태 모델, 바인딩은 IVC/PVC 공용이라, 승인 후 나온 PVC voiceId를 사람이 한 번 입력하면 그 목소리로 승격된다(4, 5항).
4. BE와 FE는 지금 IVC 기준으로 붙이면 되고, PVC는 상태값(`PVC_PENDING`, `PVC_READY`)만 미리 둬서 나중에 수동으로 채운다.

## 1. 인증

모든 호출에 헤더 `X-AI-Server-Token`. 값은 AI 서버 환경변수 `AI_SERVER_TOKEN`과 같은 토큰이다. 실제 토큰 값은 이후 구현 시 공유한다.

## 2. 호출 흐름

시각 단위 ms, 필드는 camelCase. 상세 요청과 응답 스키마는 배포 서버 `/docs`(Swagger) 참고.

| 순서 | 엔드포인트 | 용도 | 핵심 필드 |
|------|-----------|------|----------|
| 1 | `POST /v1/analysis/transcriptions` | 전사, 화자분리, 대상자 식별 | 입력 `referenceVoiceSampleUrl`(짚은 클립), 응답 `subjectSpeakerLabel`, `segments[]` |
| 2 | `POST /v1/voice/collect-samples` | 통화 하나에서 대상자 구간 반환 | 입력 `audioUrl`, `subjectSpeakerLabel`, `transcriptSegments`, 응답 `samples[]` |
| 3 | `POST /v1/voice/clone` | 누적 풀로 클론 | 입력 `samples[]`(풀 전체), 응답 `voiceId` |
| 4 | `POST /v1/voice/speech` | 답변 합성 | 입력 `text`, `voiceId`, 응답 음성(audio) |

2번 결과 `samples[]`(`{audioUrl, startMs, endMs}`)를 대상자별 풀에 누적한다(BE). 상한(180초)과 선별은 3번에서 AI가 처리한다. 단일 통화만으로 바로 클론하려면 `POST /v1/voice/clone-from-transcript`로 수집과 클론을 한 번에 처리한다.

## 3. 구간 지정 계약

1. 앱이 짚은 `(recordingId, startMs, endMs)`를 BE가 원본에서 5~10초 클립으로 잘라 presigned URL을 만들고, 그 URL을 1번의 `referenceVoiceSampleUrl`로 전달한다.
2. AI는 시각을 직접 받지 않는다. 잘라낸 클립 URL만 받는다(스키마 변경이나 AI 추가작업 없음).
3. 여러 구간을 허용하면 참조 성문이 강화된다(선택).

## 4. BE 구현 항목

1. 구간 클립 생성: 3항대로 짚은 구간을 잘라 `referenceVoiceSampleUrl`로 전달.
2. 재료 풀 저장: 통화마다 2번 결과를 대상자별로 누적.
3. voiceId 저장과 바인딩: 3번 `voiceId`를 `providerAsset.externalAssetId`에 저장하고 대화 시 `persona.voiceId`로 주입.
4. 상태 모델: 대상자별 `PLACEHOLDER → IVC_READY → PVC_PENDING → PVC_READY`.
5. PVC 수동 슬롯: 서류 승인 후 PVC voiceId를 수동 입력하면 상태 승격과 포인터 교체(자동화 없음).

## 5. FE 구현 항목

1. 목소리 지정 화면: 화자별 스니펫을 들려주고 대상자 목소리를 짚기(5~10초). 랜덤 통화 강제 금지, 복수 선택은 선택.
2. 클론 상태 표시: 위 상태 모델 기준.
3. 대화 음성 재생: 페르소나 TTS 재생(기존).
