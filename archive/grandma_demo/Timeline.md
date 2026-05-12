# TalkTo Persona AI — 개발 타임라인

TalkTo 사후 Phase(Persona AI)의 AI 파트 개발 타임라인

## 코드 구조 (기능별)

```
PersonaAI/
├── llm/
│   └── core.py              generate_response() — GPT 호출 핵심 함수
├── prompt/
│   └── system_prompt.py     시스템 프롬프트 (Stage 진행하면서 고도화)
├── memory/                  장기/단기 기억, 임베딩, KG, RAG 검색
├── voice/                   STT, TTS
├── monitoring/              사용 이벤트, 오류 로그, 피드백
├── data/
│   ├── voice_raw/           할머니 음성 원본 wav 13개 (GitHub 미포함)
│   ├── 전사본분석_임시.md    할머니 말투/성격/기억 분석 (화자분리 후 확정)
│   └── 전사본_화자표시_임시.md  화자 표시 (화자분리 후 확정)
├── Timeline.md              이 파일
└── README.md
```

---

## Stage 1: 최소 동작 챗봇 ✅

> 폴더: `llm/core.py`, `prompt/system_prompt.py`

### 1-1. GPT 호출 함수 작성 

- `llm/core.py` → `generate_response(message, history)` 함수
- 기본 GPT 모델 (gpt-4o-mini) 호출
- 입력: 사용자 메시지, 대화 히스토리 → 출력: 할머니 답변 텍스트
- 대화형 테스트 루프 포함 (터미널에서 연속 대화 가능)

### 1-2. 간단한 시스템 프롬프트 작성 

- `prompt/system_prompt.py` → SYSTEM_PROMPT 변수
- "너는 83세 할머니 신금자야" 수준의 최소 프롬프트

### 1-3. 백엔드 연동 인터페이스 협의

- 백엔드 담당자에게 함수 구조 공유 완료
- `generate_response(message: str, history: list) -> str`
- history는 백엔드가 관리, AI는 받아서 GPT에 전달

### 이 Stage가 끝나면:

> `generate_response("할머니야, 안녕", history)` 호출하면 할머니 말투로 답한다. 백엔드가 이 함수를 API 엔드포인트에서 호출할 수 있다.

---

## Stage 2: 화자 분리 + 전사본 분석 ✅

> 폴더: `data/`

### 2-1. 화자 분리

- 음성 원본 파일 수령 완료 (wav 13개, `data/voice_raw/`)
- 전사본을 Claude에 넣어서 맥락 기반 화자 분리 (할머니 / 손자 / 손녀 구분)
- 화자 표시된 전사본 확정

### 2-2. 전사본 분석

- 화자 분리된 전사본 기반으로 분석
- 할머니 말투 패턴 정리 (사투리, 자주 쓰는 표현, 문장 끝 처리)
- 성격/감정 패턴 정리 (걱정, 칭찬, 관심 표현 방식)
- 사실 정보 추출 (가족관계, 요리법, 경험, 가치관)

### 이 Stage가 끝나면:

> 화자가 정확히 분리된 전사본과, 할머니의 말투/성격/기억 분석 문서가 완성된다.

---

## Stage 3: 프롬프팅 강화 ✅

> 폴더: `prompt/`, `data/`

### 3-1. 시스템 프롬프트 개선

- Stage 2 분석 결과를 시스템 프롬프트에 반영
- 말투 예시, 행동 가이드라인, 성격 특성, 감정/품질 가이드라인 포함
- Stage 1의 간단한 프롬프트를 교체

### 3-2. 페르소나 프로필 JSON 작성

- metadata: 이름, 나이, 관계
- personality: 핵심 성격 특성
- system_prompt: 기본 프롬프트 + 행동 가이드라인 + 말투 예시
- memories: 빈 배열 (Stage 5에서 채움)

### 이 Stage가 끝나면:

> 시스템 프롬프트가 정교해져서 할머니 말투/성격이 더 자연스럽다. 전라도 사투리, 걱정 표현, 요리 설명 스타일이 반영된다.

---

## Stage 4: 파인튜닝 ✅

> 폴더: `data/`

### 4-1. 파인튜닝 데이터셋 구축

- 화자 분리된 전사본에서 user(손자/손녀) - assistant(할머니) 대화쌍 추출
- OpenAI 파인튜닝 JSONL 형식으로 변환
- 슬라이딩 윈도우로 학습 데이터 증강

### 4-2. 파인튜닝 실행

- OpenAI Fine-tuning API로 모델 학습 (gpt-4o-mini 기반)
- 학습 완료 후 모델 ID 발급

### 4-3. 프롬프팅 vs 파인튜닝 비교 테스트

- 동일한 질문 세트로 두 방식 비교
- 할머니다움, 말투 자연스러움, 사실 정확도 평가
- 결과에 따라 방향 결정 (프롬프팅만 / 파인튜닝 / 둘 다)

### 4-4. 챗봇에 적용

- 결정된 방식으로 기존 챗봇의 모델/프롬프트 교체

### 이 Stage가 끝나면:

> 프롬프팅과 파인튜닝 중 더 할머니다운 방식이 결정되고, 챗봇에 적용된다.

---

## Stage 5: 장기 기억 + RAG ✅

> 폴더: `memory/`, `data/`

### 5-1. 기억 chunk 추출

- 전사본 + 가족 정보에서 기억을 하나씩 정리
- 각 chunk에 id, category, title, content, importance 부여
- `data/memories.json`에 37개 chunk 저장

### 5-2. 임베딩 생성

- 각 기억 chunk를 벡터로 변환 (text-embedding-3-small, 1536차원)
- `data/memory_embeddings.json`에 저장

### 5-3. RAG 검색 구현

- `memory/embeddings.py` → `search_memories(query, top_k=5)` 함수
- 사용자 질문을 임베딩 → 기억 chunk와 코사인 유사도 비교
- 관련 기억 top-5를 프롬프트에 주입
- `llm/core.py`에 RAG 연결 완료

### 5-4. 재임베딩 스크립트

- `python -m memory.embeddings --build`로 전체 재임베딩

### 이 Stage가 끝나면:

> "할머니 불고기 어떻게 만들어?"에 실제 할머니가 알려준 레시피 기반으로 답한다. 기억 없이 지어내던 이전과 달리, 실제 데이터에 근거한 답변을 한다.

---

## Stage 6: 품질 보정 (보류)

> 폴더: `llm/`, `prompt/`

### 6-1. 프롬프트 기반 품질 가이드라인 강화

- "1~2문장으로 답하되 너무 짧게는 하지 마"
- "이전에 한 말을 반복하지 마"
- "기억에 없는 건 지어내지 말고 모른다고 해"
- 프롬프트 수정으로 해결 가능한 부분 먼저 처리

### 6-2. 코드 기반 보정 (프롬프트로 안 되는 경우)

- 단답 감지 → 재생성 요청
- 반복 감지 (Jaccard ≥ 0.6) → 재생성 요청
- 할루시네이션 감지 → 회피 답변 유도

### 6-3. RAG 캐시

- 같은 질문이 45초 안에 다시 오면 캐시된 검색 결과 사용
- 중복 API 호출 방지

### 이 Stage가 끝나면:

> 너무 짧게 답하거나, 같은 말 반복하거나, 모르는 걸 지어내지 않는다.

---

## Stage 7: 음성 (STT/TTS) ✅

> 폴더: `voice/`

### 7-1. STT 구현

- Whisper API로 음성 → 텍스트 변환하는 함수
- 잡음 필터링 처리

### 7-2. TTS 구현

- ElevenLabs PVC로 텍스트 → 할머니 목소리 변환하는 함수
- 이미 클로닝된 voice_id 사용

### 7-3. voice-first 흐름 구현

- Push-to-talk 입력 → STT → 답변 생성 → 할머니 음성 먼저 재생 → 재생 끝나면 텍스트 표시
- 음성 재생 중 Push-to-talk 비활성화

### 7-4. fallback 처리

- TTS 실패 → 텍스트 답변만 표시
- STT 실패 → "다시 말씀해주세요" 안내

### 7-5. 보안

- 생성된 음성 다운로드 금지 (signed URL 짧은 만료)

### 이 Stage가 끝나면:

> Push-to-talk으로 말하면 할머니 목소리로 답한다. 텍스트 채팅도 여전히 동작한다.

---

## Stage 8: 단기 기억 ← 지금 여기

> 폴더: `memory/`

### 8-1. 단기 기억 스키마 정의

- 필드: content, category, importance (1~10), strength (기본 1.0), recall_count, last_recalled_at, archived
- 저장 형태: JSON + 임베딩 파일

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
