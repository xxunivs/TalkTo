# TalkTo AI 파이프라인 평가

전사, 화자 식별, 기억 검색, 페르소나 응답 품질을 확인하는 평가 도구와 기존 결과 정리

## 평가 항목

| 단계 | 평가 내용 | 평가 도구 |
|---|---|---|
| s1 전사 | 문자 오류율(CER), 화자 수, 처리 시간 | `transcription/e2e.py`, `transcription/bakeoff/run_bakeoff.py` |
| s1 화자 식별 | 대상자 식별 정확도, 목소리 유사도 | `speaker_id/lab.py` |
| s1 교정 | 고유명사 교정과 원문 보존 | `correction/lab.py` |
| s2 분석 | 인물 귀속, 민감 정보, 기억 근거, 요약과 태그 | `analysis/`, `memory_segments/`, `enrichment/` |
| s3 검색 | 정답 기억 검색률(Recall@k) | `embeddings/lab.py` |
| s4 음성 | 대상자 샘플 수집, 클론 생성, 음성 합성 | 외부 5인 흐름 검증 기록 |
| s5 대화 | 정확성, 말투, 안전, 반복 실행 시 일관성 | `persona/lab.py` |
| 전체 흐름 | 외부 녹음으로 단계 연결과 일반화 확인 | `e2e_external.py` |

## 현재 실행 상태

전사 E2E와 STT 비교 도구는 도움말 확인 가능

실제 평가 실행 시 녹음, 정답, 출력 경로 지정 필요

전사 E2E의 `.env` 참조 위치와 인증 토큰 설정도 확인 필요

분석, 기억, 검색, 화자 식별, 페르소나 평가 도구에는 이전 `app.*` 임포트가 남아 있어 현재 구조에서 실행 불가

공용 로더의 저장소 루트, 자료 경로, 시나리오 모듈 경로도 수정 필요

```bash
# 저장소 루트에서 실행
python -m evaluation.transcription.e2e --help
python -m evaluation.transcription.bakeoff.run_bakeoff --help
```

- 전사 E2E: 실행 중인 AI 서버 필요, 입력은 WAV 파일
- STT 비교: ElevenLabs 또는 RTZR API 키 필요, `--providers`로 비교 대상 선택
- CER 채점: 녹음과 정답 파일의 이름이 일치해야 함
- 실제 평가: 외부 API 호출 비용 발생

## 자료와 결과 위치

| 자료 | 위치 |
|---|---|
| 원본 녹음 | 저장소 루트의 `input/<대상자>/음성/` |
| 전사 정답과 용어집 | `transcription/gold/` |
| 전사와 화자 식별 결과 | `transcription/results/` |
| STT 비교 결과 | `transcription/bakeoff/results/`, `results-kw/`, `results-spk2/` |
| 외부 인물 설정 | `persona/fixtures/` |
| 페르소나 평가 결과 | `persona/results/` |
| 외부 흐름 검증 결과 | `e2e/results/` |

위 경로는 원본 녹음을 제외하고 `evaluation/` 기준

인물 설정에는 실제 외부 발화를 바탕으로 작성한 가상 이름과 관계 정보 포함

## 저장된 평가 결과

아래 수치는 기존 결과 파일 기준이며, 현재 코드로 재측정한 결과는 아님

### 전사와 화자 식별

- [전사 E2E](transcription/results/summary.md): 2026-07-07 실행, CER 채점 11건 평균 0.1780
- 전사 결과의 화자 수: 13건 중 12건은 2명, 1건은 3명
- CER 제외 2건: 정답 전사 후반부 누락으로 비교에서 제외
- [목소리 기반 화자 식별](transcription/results/speaker_id_summary.md): 저장된 13건에서 13/13 식별
- [외부 5인 흐름 검증](e2e/results/gen5_flow_validation.md): 참조 목소리 기반 18/18 매칭, 5인 모두 클론과 TTS 성공

### 페르소나 응답

2026-07-08 저장 결과, 인물별 시나리오 14개를 3회 반복한 42개 샘플, 점수 범위 1~5

| 인물 | 정확성 | 말투 | 안전 | 결과 |
|---|---|---|---|---|
| 최영자 | 3.81 | 3.90 | 5.00 | [상세](persona/results/eval_persona_choiyoungja_gangwon.md) |
| 이순덕 | 3.93 | 3.95 | 5.00 | [상세](persona/results/eval_persona_isunduk_gangwon.md) |
| 정말순 | 4.14 | 4.05 | 5.00 | [상세](persona/results/eval_persona_jeongmalsun_gangwon.md) |
| 김분남 | 3.98 | 3.74 | 5.00 | [상세](persona/results/eval_persona_kimbunnam_gyeongsang.md) |
| 서정숙 | 4.02 | 3.81 | 5.00 | [상세](persona/results/eval_persona_seojeongsuk_gyeongsang.md) |

점수는 LLM 채점 결과이며, 해당 설정과 시나리오에서의 비교 지표

현재 코드의 성능 확인에는 경로 수정 후 재평가 필요
