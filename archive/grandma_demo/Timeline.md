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

## Stage 6: 품질 보정 (보류)

> 폴더: `llm/`, `prompt/`

### 6-1. 프롬프트 기반 품질 가이드라인 강화

- 프롬프트 A에 기본 가이드라인 이미 포함
- 추후 실제 테스트 피드백 반영해서 개선

### 6-2. 코드 기반 보정 (프롬프트로 안 되는 경우)

- 단답 감지 → 재생성 요청
- 반복 감지 (Jaccard ≥ 0.6) → 재생성 요청
- 할루시네이션 감지 → 회피 답변 유도

### 6-3. RAG 캐시

- 같은 질문이 45초 안에 다시 오면 캐시된 검색 결과 사용

---

## Stage 7: 음성 (STT/TTS) ✅

### 7-1. STT 구현

- `voice/stt.py` → `transcribe(audio_path)` 함수
- Whisper API로 음성 → 텍스트 변환
- 에러 처리 포함 (success/error 반환)

### 7-2. TTS 구현

- `voice/tts.py` → `synthesize(text, output_path)` 함수
- ElevenLabs PVC로 텍스트 → 할머니 목소리 변환
- voice_id: `lTmcD2Kp43w1lHsMQmq7` (대표가 클로닝 완료)
- 에러 처리 포함 (success/error 반환)

### 7-3~7-5. voice-first 흐름, fallback, 보안

- 프론트/백엔드 영역. AI는 함수만 제공.

### 이 Stage가 끝나면:

> Push-to-talk으로 말하면 할머니 목소리로 답한다. 텍스트 채팅도 여전히 동작한다.

---

## Stage 8: 단기 기억 ✅

> 폴더: `memory/`

### 8-1. 단기 기억 스키마 정의

- `memory/short_term.py` — 스키마, 저장/조회/강도갱신 함수
- `data/short_term_memories.json` — 단기 기억 저장 파일
- 필드: id, content, category, importance, strength, recall_count, created_at, last_recalled_at, archived

### 8-2. 중요도 평가 (ImportanceEvaluator)

- 매 assistant 답변 직후 5차원 중요도 평가
- 감정 30%, 관계 25%, 개인정보 15%, 기억가치 20%, 연속성 10%
- GPT 보조 모델 + 휴리스틱 하이브리드, 일일 100회 제한

### 8-3. 기억 요약 + 저장 (EnhancedMemorySummarizer)

- importance ≥ 6이면 요약 생성 (제목, 내용, 인사이트, 감정톤)
- 임베딩 생성 후 단기 기억 DB에 추가

### 8-4. 단기 기억 검색 — Generative Agents 적용

- 세 가지 점수 합산: Relevance + Recency + Importance
- 장기 top-3 + 단기 top-2를 합쳐서 프롬프트에 주입

### 8-5. 회상 시 강도 갱신 — MemoryBank 적용

- 단기 기억이 검색 결과에 포함되면 strength +1
- 자주 회상되는 기억은 강해지고, 안 불리는 기억은 약해짐

### 8-6. Reflection

- 코드 작성하되 MVP에서는 비활성화 (REFLECTION_ENABLED=false)
- 단기 기억 50개 이상 누적 시 메타 인사이트 생성 가능

### 이 Stage가 끝나면:

> "나 오늘 회사에서 힘들었어"를 기억해뒀다가, 다음 대화에서 "저번에 힘들었다고 했는데 좀 나아졌어?" 라고 물어본다.

---

## Stage 9: 감정 엔진

> 폴더: `prompt/`

### 9-1. EmotionDetector

- 한국어 키워드 기반 감정 추론
- 9가지 감정: happy, excited, calm, neutral, sad, worried, stressed, angry, affectionate

### 9-2. EmotionTracker

- EMA(지수이동평균)로 감정 안정화
- 히스테리시스 + 디바운스로 감정이 갑자기 왔다갔다 안 하게 처리

### 9-3. ToneMapper

- 감정 → LLM 프롬프트 톤 지시 조절
- 감정 → ElevenLabs TTS 설정 조절 (stability, similarity_boost)
- 슬플 때 → 따뜻한 위로 톤, 기쁠 때 → 밝은 톤

### 이 Stage가 끝나면:

> 슬픈 메시지에는 따뜻한 위로, 기쁜 메시지에는 칭찬. 할머니 톤이 감정에 맞게 변한다.

---

## Stage 10: 운영/모니터링

> 폴더: `monitoring/`

### 10-1. 사용 이벤트 저장

- usage_events: ptt_pressed, voice_play_started, text_revealed 등

### 10-2. 오류 로그 저장

- error_logs: STT/TTS/LLM/RAG 오류를 provider별로 분리 저장

### 10-3. 피드백 분류

- "사실이 틀렸어요", "기억에 없는 말을 지어냈어요" → 기억 보강 큐
- "섬뜩하거나 불편했어요" → 윤리/UX 검토 큐

### 10-4. 단기 기억 망각 배치 — MemoryBank 적용

- 일 1회 cron 실행
- 14일간 한 번도 회상 안 된 기억은 강도 자연 감쇠
- 강도 0.1 미만이면 archived 처리

### 10-5. 기억 보강 사이클

- 부정 피드백 → 대표가 가족과 통화 → 장기 기억 수정 → 재임베딩 → 재테스트

### 10-6. 개발자 통계/피드백 페이지 API

- 통계 카드, 피드백 테이블, 부정 피드백 큐, 오류 로그 조회

### 이 Stage가 끝나면:

> 피드백/오류를 추적하고, 부정 피드백 → 기억 보강 → 재테스트 사이클이 돌아간다.

---

## Stage 11: 테스트 후 조정

> 폴더: 전체

### 11-1. Reflection 활성화 검토

- 단기 기억 50개 이상 누적?
- "맥락 못 짚는다", "같은 말 반복한다" 피드백?
- 둘 다 Yes → REFLECTION_ENABLED=true

### 11-2. 검색 가중치 조정

- relevance, recency, importance 비율 미세 조정
- 가족 만족도 기반

### 11-3. 추출 임계값 조정

- 단기 기억이 너무 적으면 5점으로, 너무 많으면 7점으로

### 11-4. 장기/단기 우선순위 재검토

- 충돌 사례 발생 시 정책 재검토

### 이 Stage가 끝나면:

> 가족 테스트 데이터 기반으로 최적화된 최종 MVP.
