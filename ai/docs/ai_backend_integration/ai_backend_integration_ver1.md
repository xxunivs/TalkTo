# AI <-> 백엔드 연동 요청 ver1

새 AI 서비스 레포 `TalkTo_APP_AI` 기준. 기존 원칙(staged 연동, camelCase 계약, AI_SERVER_TOKEN, 민감 데이터 로그 금지)은 그대로 유지한다.

자동화 파이프라인의 전사 단계 구현이 완료되어(실제 통화 녹음 13건으로 검증) 백엔드에 아래 3건을 요청한다. 항목별로 회신해주면 된다.

---

## 1. [요청] 전사 요청에 audioUrl 추가

현재 `POST /v1/analysis/transcriptions` 요청 모델에는 jobId, recordingId 등 식별자만 있고 오디오가 들어올 자리가 없다. AI 서버는 stateless이고 백엔드 스토리지에 직접 접근하지 않는 원칙이므로, 백엔드가 스토리지의 presigned GET URL을 요청에 담아 전달해주면 된다 (storage의 getSignedUrl 재사용이면 될 것 같음).

```json
{
  "jobId": "8f3c...-uuid",
  "recordingId": "b21a...-uuid",
  "audioUrl": "https://storage.../recordings/xxx.m4a?X-Amz-...",
  "audioMimeType": "audio/m4a",
  "mode": "full",
  "language": "ko",
  "speakerDiarization": true,
  "glossary": ["향", "규하", "정읍", "매실청"]
}
```

- `audioUrl`: presigned GET URL. **유효기간 30분 이상**으로 발급해주면 된다. 실측 기준 30분 녹음(272 세그먼트) 처리에 57초라 여유가 크지만, 재시도·큐 대기까지 감안한 값이다.
- `audioMimeType`: 선택. 없으면 URL 확장자로 판별한다.
- `mode`: `"full"`(기본) 또는 `"preview"`. 무료 샘플 분석(기능명세 PRV-001·002)용 — preview면 앞부분 일부 전사만 반환한다. preview 산출물의 분리 저장(preview_only)과 무료 한도·본분석 게이트 체크는 백엔드 담당.
- `glossary`: family glossary 용어들. 전사 후 이름·지명 보정에 사용한다. glossary 미입력 사용자는 빈 배열로 보내면 된다(보정 스킵하고 진행).
- 응답은 기존 TranscriptionResponse(segments/provider/model) 유지, 세그먼트별 `confidence`가 채워져서 온다. 상세 호출 스펙(주소·헤더·응답 예시)은 서버 배포 시 함께 전달한다.

실패 처리 — AI가 422와 아래 사유 코드를 반환한다:

| 코드 | 의미 | 백엔드 처리 |
|---|---|---|
| `AUDIO_URL_EXPIRED` | presigned URL 만료·거부 | URL 재발급 후 재시도 |
| `AUDIO_DOWNLOAD_FAILED` | 다운로드 실패(404, 네트워크 등) | 재시도, 반복 시 실패 처리 |
| `EMPTY_TRANSCRIPT` | 음성 인식 결과 없음(무음 등) | PRV-003 "음질 부족" 안내 매핑 |
| `AUDIO_TOO_SHORT` | 인식된 발화가 너무 짧음 | PRV-003 "길이 부족" 안내 매핑 |

---

## 2. [요청] 컨텍스트 입력 — 시점별 2소스

분석 파이프라인은 설문/프로필 정보를 힌트로 사용한다. 재료가 두 시점에 나뉘어 존재하므로(온보딩: subject profile + family glossary / Voice Persona 신청: intake 제출), AI 요청에도 두 오브젝트로 나눠 전달해주면 된다. **백엔드 저장 구조는 바꿀 필요 없고, 호출 시 저장된 값을 아래 형태로 변환해 요청에 포함해주기만 하면 된다.**

```json
{
  "subjectContext": {
    "subject": { "addressTerm": "외할머니", "name": "신금자" },
    "familyMembers": [
      { "name": "종서", "relationToSubject": "막내아들", "addressTerms": ["종서야"] },
      { "name": "향", "relationToSubject": "외손녀", "addressTerms": ["향아"] }
    ],
    "glossaryTerms": ["정읍", "매실청", "해욱"]
  },
  "intakeContext": {
    "basicProfile": {},
    "familyMap": {},
    "timeline": {},
    "speechStyle": {},
    "personality": {},
    "sensoryMemories": {},
    "memoryCards": [],
    "situationalReactions": {},
    "tabooTopics": [],
    "sttHints": {
      "names": ["신금자", "이재산", "정읍"],
      "voiceSampleRef": { "documentId": "uuid", "startMs": 12000, "endMs": 25000 }
    }
  }
}
```

- `subjectContext`: 온보딩 직후부터 존재하는 정보. **분석 요청에 항상 포함**해주면 된다(내용이 비어 있어도 됨). 이름·지명 보정과 내용 기반 화자 식별, 지칭 해소("걔" → 누구) 힌트로 쓴다.
- `intakeContext`: Voice Persona intake 제출 전에는 **null 허용**. null이면 AI가 자동으로 fallback 경로(내용 기반 화자 식별)로 동작하고, 제출 후 요청부터 포함해주면 음성 임베딩 매칭·페르소나 조립에 사용한다.
- `intakeContext` 각 섹션의 내부 필드는 설문지 v1.0 문항 키 기준으로 별도 부록에서 확정한다. 설문의 출처·확실성 태그는 변환 시 유지해주면 된다.
- `sttHints.voiceSampleRef`: 대상자 목소리가 잘 들리는 구간(Intake 11-2). 화자 식별의 reference 임베딩 등록에 1회 사용한다. target-voice-sample 플로우와 같은 소스면 documentId만 맞춰주면 된다.

---

## 3. [요청] transcript 저장에 confidence 추가

AI가 전사 세그먼트별 confidence(STT 단어 신뢰도 평균, float 0~1)를 반환하는데(기능명세 ANL-003), 현재 `TranscriptSegmentInputDto`와 transcript_segments 엔티티에 받는 자리가 없어 값이 버려진다. **컬럼·DTO 필드 추가 부탁 (float, nullable).**
