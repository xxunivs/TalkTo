# 음성 클론 아키텍처 (2026-08-01)

목표: 사용자가 녹음에서 고인 목소리 한 곳(5~10초)만 짚으면, 전사 결과로 고인 발화를 자동으로 모아 클론하고, 페르소나가 그 목소리로 대화하게 한다.

## 1. 전체 흐름 (end-to-end)

```
업로드 + 고인 목소리 5~10초 짚기
  → ① 전사        Scribe (STT + 화자분리) → segments(start/end/speaker)
  → ② 대상자 식별  짚은 5~10초 → ECAPA 성문 → 각 통화 화자와 매칭 → subject label
  → ③ 재료 누적    대상자 구간만 품질필터 후 '대상자별 풀'에 누적 (여러 통화)
  → ④ 클론 생성    풀에서 뽑아 IVC(voices/add) → voiceId
  → ⑤ 바인딩       subject → voiceId(+상태), 페르소나 TTS가 사용
  → ⑥ 대화        응답 → TTS(voiceId) → 앱 재생
```

## 2. 입력·UX (고인 목소리 지정)

- **랜덤 통화 X** — 통화마다 고인 등장·음질이 달라, 고인이 또렷하게 들리는 지점을 사용자가 짚게 한다.
- **권장 방식** — Scribe 화자분리 결과로 각 화자의 대표 스니펫을 들려주고 "이 목소리가 고인"을 탭. 스크럽 부담 없이 화자 라벨 확정 + 성문 재료를 동시에 얻는다.
- **5~10초 한 번이면 충분** — 짚는 구간의 역할은 성문(정체 등록)이지 클론 재료가 아니다. 재료는 자동수집이 담당한다.
- **여러 구간 = 선택적 강화** — 참조를 2~3개 주면 잡음 통화에서 매칭 신뢰도가 오른다(ECAPA 임베딩 평균). 필수는 아니며, 기본은 한 번 + "더 추가" 옵션.
- **짚은 통화 vs 다른 통화** — 짚은 통화는 탭 시각의 화자 라벨을 바로 읽어 ECAPA가 필요 없다. 성문 매칭은 사용자가 안 짚은 다른 통화에서 같은 사람을 찾을 때만 쓴다.

## 3. 핵심 설계 결정

1. **STT + 화자분리 = ElevenLabs Scribe**
   4종 비교(Scribe·gpt-transcribe·gpt-4o-diarize·RTZR) 실측 결과 전사 정확도 + 화자분리 조합에서 Scribe가 최고. gpt-4o-diarize는 참조 지목이 되지만 전사 품질 저하가 커서 제외.

2. **대상자 식별 = ECAPA 성문(주) + LLM 의미분석(보조)**
   성문으로 "누가 고인 + 통화 간 연결"을 확정. LLM은 호칭·맥락으로 역할·가족관계를 뽑고, 참조가 없을 때 폴백. 기존의 "발화량 최다 화자 = 대상자" 폴백은 폐기.

3. **클론 = IVC 자동, PVC 수동 슬롯**
   IVC(instant)는 즉시 자동 생성. PVC(professional)는 고인이라 서류(사망확인서·가족동의서) 승인이 필요해 자동화 불가 — 아키텍처엔 자리만 두고, 승인 후 voiceId를 수동으로 채운다.

4. **재료 누적 ↔ 클론 생성 분리**
   대상자별 재료 풀은 하나로 계속 쌓고, 소비만 방법별로 다르게. IVC는 좋은 2~3분을 뽑고, 장래 PVC는 같은 풀에서 30분 이상을 뽑는다.

5. **앵커 5~10초 = 식별용, 클론 재료 아님**
   짚은 구간은 "이 목소리가 고인"이라는 기준 성문일 뿐. 클론 분량은 전사에서 자동 수집한다.

6. **상태 모델**
   `PLACEHOLDER → IVC_READY → (PVC_PENDING → PVC_READY)`
   필드·상태값은 처음부터 전부 넣어, 나중에 PVC를 붙일 때 스키마를 바꾸지 않는다.

## 4. 담당 분리

- **FE**: 녹음 업로드, 고인 목소리 5~10초 짚는 화면, 클론 상태 표시
- **BE**: 보안 저장소, 짚은 구간을 클립으로 잘라 AI로 전달, voiceId 저장(providerAsset), 상태 관리, PVC 수동 승인 반영
- **AI(우리)**: 전사(Scribe), 식별(ECAPA + LLM), 재료 누적, 클론 잡(IVC), 페르소나·TTS

## 5. API 계약 (AI 관점)

- 전사 `POST /transcriptions` — audio_url + reference_voice_sample_url → segments + subject_speaker_label  (있음)
- 클론 `POST /voice/clone` — samples[] 또는 sample_audio_url → voiceId  (있음)
- 클론(전사기반) `POST /voice/clone-from-transcript` — audio_url + transcript_segments + subject_speaker_label → 대상자 구간 자동 수집 → voiceId  (있음, 최소 슬라이스)
- TTS `POST /voice/speech` — voiceId → 음성  (있음)

## 6. 구현 순서

1. ~~최소 슬라이스 — 단일 녹음 자동수집 → IVC → voiceId~~ **완료** (외부 최영자 데이터 E2E로 실제 voiceId 확인)
2. **누적 풀** — 대상자별로 여러 통화에서 누적 (진행)
3. ~~LLM 의미 폴백 + 관계 추출~~ **완료** — 의미 폴백(호칭 방향으로 대상자 판정, 근거 없으면 미확정) 신규 구현, "발화량 최다" 폐기. 관계 추출은 기존 인물분석(persons)에 이미 있어 재사용. 실데이터 검증: 가족 통화는 정확, 호칭 없으면 안 찍음.
4. **상태·PVC 슬롯 + BE 바인딩 연결**

## 7. 미정 (합의 필요)

- 재료 풀 **저장 스키마 세부** — BE 소관 (아래 8-1 기준으로 구체화)
- **품질 필터** — confidence 실측 결과 "방언은 confidence 높고, 낮은 건 이미 800ms 규칙이 거르는 짧은 리액션"이라 별도 필터 불필요로 결론. 실사용 녹음이 많이 시끄러우면 그때 오디오 SNR 분석 검토.
- IVC 재료 **상한 180초** 고정, 선별은 통화 간 라운드로빈 + 긴 구간 우선(구현됨)

---

## 8. BE·FE 전달 스펙 (구현 대상)

AI 파이프라인은 완성돼 있고(전사·식별·수집·클론·TTS), 아래는 **BE·FE가 구현할 부분**이다.

### 8-1. BE 계약

**목소리 상태 모델** — 대상자(persona)마다 상태 1개 저장:
`PLACEHOLDER → IVC_READY → (PVC_PENDING → PVC_READY)`

**호출 흐름**
1. 녹음 업로드 → `POST /transcriptions` (audio_url + reference_voice_sample_url=사용자가 짚은 클립) → 응답 `segments` + `subjectSpeakerLabel` 저장
2. 녹음마다 `POST /voice/collect-samples` (audio_url + transcript_segments + subjectSpeakerLabel) → 반환된 대상자 구간을 **대상자별 재료 풀에 누적 저장**
3. 클론 시점 → `POST /voice/clone` (samples=누적 풀 전체) → `voiceId` → 저장 + 상태 `IVC_READY`
4. 대화 → `persona.voiceId`=저장한 voiceId → `POST /voice/speech`로 합성

**voiceId 바인딩** — voiceId를 `providerAsset.externalAssetId`에 저장하고 대화 시 `persona.voiceId`로 주입. PVC로 교체되면 **포인터만 갱신 + 상태 PVC_READY**.

**재료 풀 저장** — 대상자별로 `{audioUrl(또는 recordingId), startMs, endMs}` 목록 누적. `/voice/collect-samples`가 이 구간들을 반환하니 그대로 쌓으면 된다(상한·선별은 클론 시 AI가 처리).

**PVC 수동 슬롯** — 재료 30분+ & 사망확인서·가족동의서 승인 후, 관리자가 PVC voiceId를 **수동 입력** → 상태 `PVC_READY`, 포인터 교체. 자동화 없음(정책상 고인 PVC는 서류 심사 필요).

**보안 저장소** — reference 클립·재료 오디오는 보안 저장소 경유(별도 배분계획 문서 기준).

### 8-2. FE 스펙

- **고인 목소리 지정 화면** — Scribe 화자 스니펫을 들려주고 "이 목소리가 고인"을 탭(5~10초). 복수 선택은 선택적 강화(성문 보강). 랜덤 통화 강제 금지.
- **클론 상태 표시** — PLACEHOLDER / IVC_READY / PVC_PENDING / PVC_READY.
- **대화 음성 재생** — 페르소나 TTS 재생(기존).

### 8-3. 합의 필요 (BE↔AI)

- 구간 지정 계약: 사용자가 짚은 클립을 **BE가 잘라 presigned URL로** `reference_voice_sample_url`에 전달(기존 계약 그대로, AI 추가 작업 없음).
- 암호화 도입 후 AI가 원본을 읽는 **접근 경로·임시 경로**(별도 배분계획 합의 ② 기준).
