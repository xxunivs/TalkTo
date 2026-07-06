# AI <-> 백엔드 연동 요청 ver2

## 1. [요청] 용어집 전달에 pronunciationHint 포함

`buildGlossaryTerms`가 `FamilyGlossaryTerm.term`만 추출하고 **`pronunciationHint`를 버리고 있다**. 발음 힌트("찬민"의 "찬미니")는 STT가 잘못 받아쓴 이름을 교정하는 핵심 재료라, 있으면 함께 보내주면 된다 — 별도 구조 없이 glossary 배열에 term과 나란히 넣어주면 AI가 그대로 쓴다.

```ts
// 예: term + pronunciationHint를 평평하게
["찬민", "찬미니", "정읍", ...]
```

## 2. [요청] 전사 저장에 correctedText / needsReview 추가

AI 전사 응답의 각 세그먼트에는 보정 결과가 이미 포함돼 있다 (기능명세 ANL-005 — 원문과 분리 저장, 의미 변경 금지):

- `correctedText`: string | null — glossary 기반 이름·지명 교정본. 교정된 세그먼트에만 값이 있음
- `needsReview`: boolean — 확신 없는 교정 후보 표시

현재 `markStt`가 이 두 필드를 버리고 있다. confidence처럼 **`TranscriptSegmentInputDto` 필드 + 컬럼 추가** 부탁. Archive 화면에는 correctedText가 있으면 그걸, 없으면 원문을 보여주면 된다.

## 3. [요청] 목소리 샘플 구간 선택 저장 (기능명세 VPA-005)

명세 VPA-005는 "녹음 구간 재생, **시작/끝 선택**, 라벨링 → enrollment 저장"인데, 현재 `CreateTargetVoiceSampleDto`는 `recordingId`/`storageKey`만 받아서 **구간 정보가 저장되지 않는다**. 요청 2건:

1. 샘플 제출에 `startMs`/`endMs` 필드 + 엔티티 컬럼 추가
2. 분석 요청의 `intakeContext.sttHints.voiceSampleRef`에 현재 `documentId`만 담기는데, `startMs`/`endMs`도 포함 (ver1 계약 2의 형태 그대로)

이 구간이 화자 식별(누가 대상자 목소리인지)의 reference 등록에 쓰인다. 구간 없이 파일 전체가 오면 다른 화자 음성이 섞여 reference 품질이 떨어진다. 선택 화면(FE)은 별개 트랙이고, BE는 받을 자리만 먼저 만들어주면 된다.
