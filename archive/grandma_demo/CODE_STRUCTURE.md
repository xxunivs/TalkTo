# 코드 구조 상세 설명

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
│   ├── short_term.py               단기 기억 스키마 + 저장/조회/강도갱신
│   ├── importance.py               중요도 평가 (GPT 기반 5차원)
│   └── extract.py                  중요도 평가 → 단기 기억 자동 저장
├── voice/
│   ├── stt.py                      transcribe() — Whisper 음성→텍스트
│   └── tts.py                      synthesize() — ElevenLabs 텍스트→할머니 목소리
├── data/
│   ├── voice_raw/                  할머니 음성 원본 wav 13개 (GitHub 미포함)
│   ├── memories.json               장기 기억 chunk 37개
│   ├── memory_embeddings.json      장기 기억 임베딩 벡터
│   ├── short_term_memories.json    단기 기억 (대화 중 자동 저장)
│   ├── 전사본.txt                  1~13 순서 정렬
│   ├── 전사본_화자구분.txt          화자 분리 완료
│   ├── 할머니_분석.md              7개 카테고리 분석
│   ├── finetune_train.jsonl        파인튜닝 학습 데이터 262건
│   ├── finetune_val.jsonl          파인튜닝 검증 데이터 30건
│   └── eval_comparison.md          9가지 조합 비교 결과
├── tools/
│   ├── build_finetune_dataset.py   전사본 → JSONL 변환
│   ├── run_finetune.py             파인튜닝 실행/상태 확인
│   └── reorder_transcript.py       전사본 순서 정렬
├── CODE_STRUCTURE.md               이 파일
├── Timeline.md                     개발 타임라인
└── README.md                       환경 세팅 + 개발 단계
```

---

## llm/

### core.py
**역할**: AI 파트의 핵심 함수. 백엔드가 호출하는 진입점.

**주요 함수**:
- `generate_response(message, history) → str`
  - 사용자 메시지와 대화 히스토리를 받아서 할머니 답변을 반환
  - 내부 동작:
    1. 장기 기억 검색 (top-3) — `memory/embeddings.py`의 `search_memories()` 호출
    2. 단기 기억 조회 (최근 2개) — `memory/short_term.py`의 `get_active_memories()` 호출
    3. 시스템 프롬프트 + 기억 + 히스토리 + 사용자 메시지를 조립
    4. GPT API 호출 (현재 gpt-4.1-mini)
    5. 답변 반환
  - 테스트 루프 포함: `python -m llm.core`로 터미널에서 연속 대화 가능
  - 대화 후 `memory/extract.py`의 `extract_and_save()`로 단기 기억 자동 추출

---

## prompt/

### system_prompt.py
**역할**: 현재 사용 중인 프롬프트 선택. A를 가리킴.

### system_prompt_A.py
**역할**: 상세 프롬프트 (기본 GPT용). 할머니_분석.md 기반.
- 가족 정보, 건강, 성격, 말투 규칙, 실제 사투리 목록, 금지 사투리, 말투 예시, 대화 패턴, 금지 사항 포함

### system_prompt_B.py
**역할**: 최소 프롬프트 (파인튜닝 모델용). 말투 지시 없음.
- 기본 정보, 가족 관계, 금지 사항만 포함
- 말투는 파인튜닝이 학습하므로 프롬프트에서 제외

### system_prompt_C.py
**역할**: 상세 프롬프트 (파인튜닝 모델용). A와 동일.
- A를 import해서 사용

### eval_questions.py
**역할**: 말투 평가용 질문 20개. 실제 할머니 답변 포함.
- 안부/일상 6개, 칭찬/격려 2개, 건강 2개, 요리법 4개, 가족 관계 3개, 가치관 2개, 추억 1개

### eval_run.py
**역할**: 현재 설정(프롬프트 + 모델 + RAG)으로 eval 질문 20개 실행.
- `python -m prompt.eval_run`으로 실행
- 실제 답변 vs AI 답변 비교 출력

### eval_all.py
**역할**: 9가지 조합 평가 스크립트.
- `python -m prompt.eval_all [1~9]`로 특정 조합 실행
- 기본/파인튜닝 × 상세/최소 × RAG유무 = 8가지 + 대표 모델

### eval_prototype.py
**역할**: 대표 prototype 파인튜닝 모델 평가.
- 대표 API 키 + 대표 모델 + 대표 프롬프트로 eval 실행

---

## memory/

### embeddings.py
**역할**: 장기 기억 임베딩 생성 + RAG 검색.

**주요 함수**:
- `build_embeddings()` — `data/memories.json`의 37개 chunk를 벡터로 변환, `data/memory_embeddings.json`에 저장
  - 실행: `python -m memory.embeddings --build`
- `search_memories(query, top_k=5) → list` — 사용자 질문과 가장 관련 있는 기억 검색
  - 코사인 유사도 기반
  - 반환: `[{"id", "title", "content", "score"}, ...]`
- `get_embedding(text) → list` — 텍스트를 벡터로 변환 (text-embedding-3-small)

### short_term.py
**역할**: 단기 기억 관리 (저장/조회/강도갱신).

**주요 함수**:
- `add_short_term_memory(content, category, importance) → dict` — 새 단기 기억 추가
- `get_active_memories() → list` — archived되지 않은 활성 기억 반환
- `on_memory_recalled(memory_id)` — 기억이 검색에 포함됐을 때 strength +1, recall_count +1
- `load_short_term_memories() → list` — JSON 파일에서 로드
- `save_short_term_memories(memories)` — JSON 파일에 저장

**스키마**:
```json
{
  "id": "st_001",
  "content": "향이가 회사를 그만뒀다",
  "category": "직장",
  "importance": 7,
  "strength": 1.0,
  "recall_count": 0,
  "created_at": "2026-05-12T15:30:00",
  "last_recalled_at": null,
  "archived": false
}
```

### importance.py
**역할**: 대화 한 턴의 중요도를 GPT로 평가.

**주요 함수**:
- `evaluate_importance(user_message, assistant_message) → dict`
  - GPT한테 5차원 기준으로 1~10점 평가 요청
  - 반환: `{"importance": 7, "category": "직장", "summary": "...", "reason": "..."}`
  - 5차원: 감정(30%), 관계(25%), 개인정보(15%), 기억가치(20%), 연속성(10%)

### extract.py
**역할**: 중요도 평가 → 6점 이상이면 단기 기억에 저장.

**주요 함수**:
- `extract_and_save(user_message, assistant_message) → dict`
  - `evaluate_importance()` 호출 → importance ≥ 6이면 `add_short_term_memory()` 호출
  - 반환: `{"saved": True, "memory": {...}}` 또는 `{"saved": False, ...}`
  - `llm/core.py` 대화 루프에서 매 턴 후 자동 호출됨

---

## voice/

### stt.py
**역할**: 음성 → 텍스트 변환 (Speech-to-Text).

**주요 함수**:
- `transcribe(audio_file_path) → dict`
  - OpenAI Whisper API 사용, 한국어 설정
  - 반환: `{"success": True, "text": "변환된 텍스트"}` 또는 `{"success": False, "error": "..."}`
  - 지원 형식: wav, mp3, m4a 등

### tts.py
**역할**: 텍스트 → 할머니 목소리 변환 (Text-to-Speech).

**주요 함수**:
- `synthesize(text, output_path="output.mp3") → dict`
  - ElevenLabs API 사용, 할머니 클로닝 음성 (voice_id: lTmcD2Kp43w1lHsMQmq7)
  - 반환: `{"success": True, "path": "파일경로"}` 또는 `{"success": False, "error": "..."}`

---

## data/

### memories.json
장기 기억 chunk 37개. 카테고리: 기본정보, 가족, 건강, 요리, 추억, 장소, 가치관, 생활.

### memory_embeddings.json
`memories.json` 37개 chunk의 임베딩 벡터. `python -m memory.embeddings --build`로 생성.

### short_term_memories.json
단기 기억 저장 파일. 대화 중 자동으로 추가됨. 초기에는 빈 배열 `[]`.

### 전사본.txt
할머니 통화 13개 파일을 1~13 순서대로 정렬. 메타데이터 제거.

### 전사본_화자구분.txt
화자 분리 완료. 각 줄에 "할머니:" 또는 "손녀:" 표시.

### 할머니_분석.md
전사본 기반 7개 카테고리 분석: 기본정보, 언어적 특징, 성격/심리, 관계, 가치관, 기억/경험, 대화패턴.

### finetune_train.jsonl / finetune_val.jsonl
파인튜닝 데이터셋. train 262건, val 30건. 슬라이딩 윈도우(6턴, 2턴 이동)로 생성.

### eval_comparison.md
9가지 조합(기본/파인튜닝 × 상세/최소 × RAG유무 + 대표 모델) × 20개 질문 비교 결과.

### voice_raw/ (gitignore)
할머니 음성 원본 wav 13개.

### tts_test.mp3 (gitignore)
TTS 테스트용 출력 파일.

---

## tools/

### build_finetune_dataset.py
`data/전사본_화자구분.txt`에서 파인튜닝 JSONL 데이터셋 생성. 시스템 프롬프트 B 포함.

### run_finetune.py
OpenAI Fine-tuning API로 데이터 업로드 + 학습 시작 + 상태 확인.
- `python tools/run_finetune.py` — 학습 시작
- `python tools/run_finetune.py --check [job_id]` — 상태 확인

### reorder_transcript.py
prototype의 전사본을 파일 1~13 순서로 재정렬. 메타데이터 제거.

---

## 기타

### .env (gitignore)
API 키 저장: OPENAI_API_KEY, HF_TOKEN, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID

### .gitignore
제외 대상: .env, venv/, __pycache__/, .DS_Store, data/voice_raw/, data/tts_test.mp3

### Timeline.md
Stage 1~8 개발 타임라인. 각 Stage별 작업 내용, 파일, 완료 상태.

### README.md
환경 세팅 가이드 + 코드 구조 + 개발 단계 요약.
