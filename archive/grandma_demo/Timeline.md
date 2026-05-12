# TalkTo Persona AI — 개발 타임라인

TalkTo 사후 Phase(Persona AI)의 AI 파트 개발 타임라인

## 코드 구조 (기능별)

```
PersonaAI/
├── llm/
│   └── core.py                     generate_response() — GPT 호출 + RAG 연결
├── prompt/
│   ├── system_prompt.py            현재 사용 중인 프롬프트 (A를 가리킴)
│   ├── system_prompt_A.py          상세 프롬프트 (기본 GPT용)
│   ├── system_prompt_B.py          최소 프롬프트 (파인튜닝용)
│   ├── system_prompt_C.py          상세 프롬프트 (파인튜닝용, A와 동일)
│   ├── eval_questions.py           평가 질문 20개
│   ├── eval_run.py                 평가 실행
│   ├── eval_all.py                 9가지 조합 평가
│   └── eval_prototype.py           대표 모델 평가
├── memory/
│   ├── embeddings.py               임베딩 생성 + RAG 검색 (top_k=5)
│   └── short_term.py               단기 기억 스키마 + 저장/조회/강도갱신
├── voice/
│   ├── stt.py                      transcribe() — Whisper 음성→텍스트
│   └── tts.py                      synthesize() — ElevenLabs 텍스트→할머니 목소리
├── monitoring/                     (미구현)
├── data/
│   ├── voice_raw/                  할머니 음성 원본 wav 13개 (GitHub 미포함)
│   ├── 전사본.txt                  1~13 순서 정렬
│   ├── 전사본_화자구분.txt          화자 분리 완료
│   ├── 할머니_분석.md              7개 카테고리 분석
│   ├── memories.json               장기 기억 chunk 37개
│   ├── memory_embeddings.json      장기 기억 임베딩 벡터
│   ├── short_term_memories.json    단기 기억 (대화 중 자동 저장)
│   ├── finetune_train.jsonl        파인튜닝 학습 데이터 262건
│   ├── finetune_val.jsonl          파인튜닝 검증 데이터 30건
│   └── eval_comparison.md          9가지 조합 비교 결과
├── tools/
│   ├── build_finetune_dataset.py   전사본 → JSONL 변환
│   ├── run_finetune.py             파인튜닝 실행/상태 확인
│   └── reorder_transcript.py       전사본 순서 정렬
├── Timeline.md
└── README.md
```

---

## Stage 1: 최소 동작 챗봇 ✅

### 1-1. GPT 호출 함수 작성

- `llm/core.py` → `generate_response(message, history)` 함수
- 기본 GPT 모델 (gpt-4.1-mini) 호출
- 입력: 사용자 메시지, 대화 히스토리 → 출력: 할머니 답변 텍스트
- 대화형 테스트 루프 포함 (터미널에서 연속 대화 가능)

### 1-2. 간단한 시스템 프롬프트 작성

- `prompt/system_prompt.py` → 현재 사용 중인 프롬프트 선택 (A를 가리킴)

### 1-3. 백엔드 연동 인터페이스 협의

- 백엔드 담당자에게 함수 구조 공유
- `generate_response(message: str, history: list) -> str`
- history는 백엔드가 관리, AI는 받아서 GPT에 전달

### 이 Stage가 끝나면:

> `generate_response("할머니야, 안녕", history)` 호출하면 할머니 말투로 답한다. 백엔드가 이 함수를 API 엔드포인트에서 호출할 수 있다.

---

## Stage 2: 화자 분리 + 전사본 분석 ✅

### 2-1. 화자 분리

- 음성 원본 파일 수령 완료 (wav 13개, `data/voice_raw/`)
- `tools/reorder_transcript.py`로 전사본 1~13 순서 정렬 → `data/전사본.txt`
- Claude에 넣어서 맥락 기반 화자 분리 → `data/전사본_화자구분.txt`

### 2-2. 전사본 분석

- 화자 분리된 전사본 기반으로 7개 카테고리 분석 → `data/할머니_분석.md`
- 기본정보, 언어적 특징, 성격/심리, 관계, 가치관, 기억/경험, 대화패턴

### 이 Stage가 끝나면:

> 화자가 정확히 분리된 전사본과, 할머니의 말투/성격/기억 분석 문서가 완성된다.

---

## Stage 3: 프롬프팅 강화 ✅

### 3-1. 시스템 프롬프트 A/B/C 작성

- `prompt/system_prompt_A.py` — 상세 프롬프트 (말투+성격+규칙, 기본 GPT용)
- `prompt/system_prompt_B.py` — 최소 프롬프트 (사실 정보+금지 사항만, 파인튜닝용)
- `prompt/system_prompt_C.py` — 상세 프롬프트 (파인튜닝 모델용, A와 동일)

### 3-2. 말투 평가 세트 구축

- `prompt/eval_questions.py` — 평가 질문 20개 (실제 할머니 답변 포함)
- `prompt/eval_run.py` — 평가 자동 실행 스크립트

### 이 Stage가 끝나면:

> 시스템 프롬프트가 정교해져서 할머니 말투/성격이 더 자연스럽다.

---

## Stage 4: 파인튜닝 ✅

### 4-1. 파인튜닝 데이터셋 구축

- `tools/build_finetune_dataset.py` — 전사본 → JSONL 변환 (슬라이딩 윈도우)
- `data/finetune_train.jsonl` — 학습 데이터 262건
- `data/finetune_val.jsonl` — 검증 데이터 30건

### 4-2. 파인튜닝 실행

- `tools/run_finetune.py` — OpenAI Fine-tuning API 실행/상태 확인
- 모델: `ft:gpt-4.1-mini-2025-04-14:talkto::DeJqGFij`

### 4-3. 9가지 조합 비교 테스트

- `prompt/eval_all.py` — 기본/파인튜닝 × 상세/최소 × RAG유무 = 8가지 + 대표 모델
- `prompt/eval_prototype.py` — 대표 파인튜닝 모델 평가
- `data/eval_comparison.md` — 9가지 조합 비교 결과
- 결론: 기본 GPT + 상세 프롬프트 A + RAG (조합 3)이 현재 최선

### 이 Stage가 끝나면:

> 프롬프팅과 파인튜닝 중 더 할머니다운 방식이 결정되고, 챗봇에 적용된다.

---

## Stage 5: 장기 기억 + RAG ✅

### 5-1. 기억 chunk 추출

- `data/memories.json` — 장기 기억 chunk 37개 (가족, 요리, 추억, 가치관 등)
- 할머니_분석.md 기반, 관계 정보 보강 포함

### 5-2. 임베딩 생성

- `data/memory_embeddings.json` — 37개 chunk의 임베딩 벡터
- `python -m memory.embeddings --build`로 생성/재생성

### 5-3. RAG 검색 구현

- `memory/embeddings.py` → `search_memories(query, top_k=5)` 함수
- `llm/core.py`에 RAG 연결 — 검색된 기억을 프롬프트에 주입

### 이 Stage가 끝나면:

> "할머니 불고기 어떻게 만들어?"에 실제 할머니가 알려준 레시피 기반으로 답한다.

---

## Stage 6: 음성 (STT/TTS) ✅

### 6-1. STT 구현

- `voice/stt.py` → `transcribe(audio_path)` 함수
- Whisper API로 음성 → 텍스트 변환
- 에러 처리 포함 (success/error 반환)

### 6-2. TTS 구현

- `voice/tts.py` → `synthesize(text, output_path)` 함수
- ElevenLabs PVC로 텍스트 → 할머니 목소리 변환
- voice_id: `lTmcD2Kp43w1lHsMQmq7` (대표가 클로닝 완료)
- 에러 처리 포함 (success/error 반환)

### 6-3. voice-first 흐름, fallback, 보안

- 프론트/백엔드 영역. AI는 함수만 제공.

### 이 Stage가 끝나면:

> Push-to-talk으로 말하면 할머니 목소리로 답한다. 텍스트 채팅도 여전히 동작한다.

---

## Stage 7: 단기 기억 ✅

### 7-1. 단기 기억 스키마 정의

- `memory/short_term.py` — 스키마, 저장/조회/강도갱신 함수
- `data/short_term_memories.json` — 단기 기억 저장 파일

### 7-2. 중요도 평가

- `memory/importance.py` → `evaluate_importance(user_msg, assistant_msg)` 함수
- GPT 기반 5차원 중요도 평가 (감정, 관계, 개인정보, 기억가치, 연속성)

### 7-3. 기억 추출 + 저장

- `memory/extract.py` → `extract_and_save(user_msg, assistant_msg)` 함수
- importance ≥ 6이면 단기 기억에 자동 저장
- `llm/core.py` 대화 루프에 연결 완료

### 7-4. 단기 기억 검색

- `llm/core.py`에서 장기 top-3 + 단기 최근 2개 합쳐서 프롬프트에 주입
- 회상된 단기 기억은 `on_memory_recalled()`로 strength +1

### 이 Stage가 끝나면:

> "나 오늘 회사에서 힘들었어"를 기억해뒀다가, 다음 대화에서 "저번에 힘들었다고 했는데 좀 나아졌어?" 라고 물어본다.

---

## Stage 8: 프롬프트 조정 ← 다음

> 파일: `prompt/system_prompt_A.py`

### 8-1. eval 결과 기반 문제점 파악

- `python -m prompt.eval_run`으로 20개 질문 답변 확인
- `data/eval_comparison.md` 9가지 조합 비교 참고
- 현재 최선 조합: 기본 GPT (gpt-4.1-mini) + 상세 프롬프트 A + RAG

### 8-2. 말투 자연스러움 개선

- GPT가 만들어내는 잘못된 사투리 금지 목록 확대 (~한디, ~다꼬, ~구먼, ~당께, ~했슈 등)
- 실제 할머니 사투리만 허용 (께름하다, 질력나다, 그러니끼 등)
- `data/할머니_분석.md` 언어적 특징 섹션 참고

### 8-3. 답변 길이/톤 조정

- 안부 대화: 1~2문장 유지
- 요리 설명: 길게 허용하되 할머니 말투로
- 위로/칭찬: 너무 빈약하지 않게 (현재 "어. 왜?" 수준 → 개선)
- "밖에서 사먹는 거 어때?" 같은 가치관 질문에 할머니답게 답하게

### 8-4. 매번 붙는 패턴 방지

- "사랑한다"는 작별 인사 때만
- "건강은 괜찮니?", "날씨는 춥지 않지?" 매번 안 붙이기
- 질문은 한 번에 1개만

### 8-5. 파인튜닝 모델 재학습 검토

- 프롬프트 조정 후 파인튜닝 데이터셋도 업데이트 필요한지 판단
- 파인튜닝 모델이 RAG 기억을 무시하는 문제 해결 방안 검토
- 필요하면 파인튜닝 데이터에 RAG 기억 포함시켜 재학습

### 8-6. eval 재실행 + 비교

- 프롬프트 수정 후 `python -m prompt.eval_run`으로 재평가
- 이전 결과와 비교해서 개선 여부 확인
- 만족스러울 때까지 8-2 ~ 8-6 반복

### 이 Stage가 끝나면:

> 할머니 말투가 자연스럽고, 요리법은 정확하고, 감정 표현도 적절한 수준의 챗봇이 완성된다.
