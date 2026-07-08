# TalkTo AI 파이프라인 평가 프레임워크

각 단계의 **지표·합격선·채점기·실측결과**를 한곳에 모은 정본. 외부 케이스 상세는
[EXTERNAL_CASES.md](EXTERNAL_CASES.md).

평가 데이터가 없으면 "동작한다"는 주장이 근거가 없다. 그래서 두 종류로 잰다 —
정답지가 있는 **우리 데이터**(정확성까지)와, 안 본 화자·주제·음질로 일반화를 보는
**외부 데이터**(정답 불필요 축). 외부는 정답지가 없어 정확성(기대답변 대비)은 재지
않고, 전사 CER(AI Hub 골드 있음)·환각 없음·안전·화제 커버리지만 잰다.

## 결과 출처 표기

`파일` = results/에 저장돼 재현·추적 가능. `로그` = 세션 실행에서만 확인, 아직
저장 안 됨(저장 과제 진행 중). 숫자 신뢰도는 `파일` > `로그`.

## 단계별 기준·실측

| 단계 | 지표 | 채점기 | 합격선 | 우리 데이터 | 외부 데이터 | 출처 |
|---|---|---|---|---|---|---|
| 1 전사 | CER | 코드 | 참고치(상한) | **0.178** (11건) | 전화 0.076·방언 0.070·노인 0.035 | 파일(우리)/로그(외부) |
| 1 화자분리 | 화자수 | 코드 | 실제와 일치 | 13/13 | 정확 | 파일 |
| 화자식별 | 성공률 | speaker_id/lab | — | 13/13, 임계 0.5 | — | 파일 |
| 1.5 보정 | 원문 훼손 | correction/lab | 0건 | 캔어리 통과 | 합성용어집 무해 | 로그 |
| 2 인물 | 근거·귀속 게이트 | analysis/lab | 위반 0 | 통과 | 통과 | 로그 |
| 2 민감 | 유형·재현 | analysis/lab | 구조위반시 중단 | 당뇨·수술·병원비 포착 | — | 로그 |
| 3-A R1 | 근거 실존 | 코드 | 100% | 통과 | 통과 | 로그 |
| 3-A R2 | 근거 뒷받침 | memory_segments/faithfulness | 불지지 0 | ~84%* | 방언 3~4/4·노인 우수 | 로그 |
| 3-A 커버리지 | 화제 포착 | memory_segments/coverage | 기준선 | 46→72% | — | 로그 |
| 3-B 검색 | Recall@5 | embeddings/lab | ≥0.8 | **1.00** (@1 0.909) | — | 로그 |
| 3-B 재정렬 | P0 vs P1 | embeddings/lab | 실험 | **기각**(악화) | — | 로그 |
| 3-C 요약 | S1·S3·S4 | enrichment/lab | 지어냄0·2~3문장 | 통과 | S1·S3 통과 | 로그 |
| 3-C 태그 | T1·T3 | 코드 | 어휘내·3~7 | 통과 | 통과 | 로그 |
| 4 페르소나 | 정확·말투·안전 | persona/lab (35) | F 전원 5 | 정확3.2·말투3.9·안전4.8* | 저작인물(최영자) 튜닝 후 F 게이트 ✅ 통과** | 파일 |
| 4 일반화 | 환각·안전 | e2e_external | — | — | 환각없음 3~4/4·안전 통과 | 로그 |

\* 정확성 잔여 갭 = 채점기 엄격(맞는 말 다른 표현) + 정답이 참조하는 정보 미스매치
(페르소나가 안 받은 정보를 올바르게 "모른다"). fact 우겨넣기는 과공유 역효과라 미채택.
현재 코드는 memoryCards 상시주입 제거(런타임 RAG only) 버전 — 이 버전 기준 F 게이트
authoritative 재실행·저장 필요.

\*\* 저작 인물(가상 최영자, 강원 speakergw712 근거)로 일반화 검증. 기준선은 신금자와
거의 같은 수치(과적합 아님), 이후 인물 무관 고정 규칙 4건 수정(모르면모른다·death
누출·자해 채점오염·의료 일관성)으로 **F 안전 게이트 통과**(F×3 전원 5). 튜닝 이력·
정직한 단서(정확성은 judge 모델 의존)는 [EXTERNAL_CASES.md](EXTERNAL_CASES.md).

## Intake 픽스처

- `data/fixtures/subject_context_singeumja.json` — 신금자(우리 데이터). subjectContext
  (가족·용어집) + intakeContext(사망맥락·가족별톤·성격·상황스크립트·금기·memoryCards).
- `evaluation/persona/fixtures/subject_context_choiyoungja_gangwon.json` — **가상 최영자**(강원, 외부
  화자 speakergw712 실발화 근거 + 가족·이름·사망맥락 임의 저작). 정답(기대답변) 있어
  정확성까지 잰다. 우리 가족 비의존 일반화용. → EXTERNAL_CASES.md.
- 저작 파이프라인: 픽스처 → `persona/build.py`로 프롬프트 조립 → `persona/lab.py
  --scenarios <모듈>`로 정답 대비 채점.
- **남은 미비**: 유형이 더 다른 인물(무뚝뚝/수다/생존 페르소나) 2~3종 추가하면 일반화
  표본이 넓어진다.

## 재현

```
python -m evaluation.transcription.e2e                # 1 전사 CER (저장됨)
python -m evaluation.speaker_id.lab                   # 화자식별 (저장됨)
python -m evaluation.correction.lab                   # 1.5 보정 캔어리
python -m evaluation.analysis.lab persons|sensitivity # 2 분석
python -m evaluation.memory_segments.lab              # 3-A 추출
python -m evaluation.memory_segments.faithfulness     # 3-A R2
python -m evaluation.memory_segments.coverage         # 3-A 커버리지
python -m evaluation.embeddings.lab                   # 3-B 검색
python -m evaluation.enrichment.lab                   # 3-C 요약·태그
python -m evaluation.persona.lab                      # 4 페르소나 35
python -m evaluation.e2e_external --pair <화자쌍>      # 외부 전 파이프라인 체인
```

## 남은 평가 과제

1. **결과 저장 확산** — 전사·화자식별·페르소나(lab)·외부 e2e는 파일 저장 됨. 남은
   lab(analysis·memory_segments·enrichment·embeddings)도 results/에 요약 md 남기기.
2. **다양한 인물 Intake** — 저작 인물 5종(강원3·경상2), 전 파이프라인 e2e 통과. 정확성
   레퍼런스는 최영자·신금자만(다른 3종은 e2e 행동/충실도만).
3. **페르소나 개선(인물 무관)** — ✅모르면모른다 강화 ✅death-context 누출 차단
   ✅자해 채점오염 제거 ✅의료 일관성 ✅말투 약축 ✅기억주입 관련성 게이트.
   ✅정확성 authoritative 재측정(주입 ON·repeat3·gpt-5.5): 최영자 3.81·신금자 3.48, 안전 4.8~4.98.
   남음: E2형(주입해도 모델이 구체 사실 안 녹임), 말투 잔여(존댓말·장황).
4. **R2 절대% 신뢰화** — 과분해·귀속(①) 걷어낸 순수 지지율.
