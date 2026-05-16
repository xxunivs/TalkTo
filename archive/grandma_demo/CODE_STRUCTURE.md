# 코드 구조 상세 설명

```
 PersonaAI/
│
├── data/                             데이터 파일
│   ├── voice_raw/                    할머니 음성 원본 wav 13개 (GitHub 미포함)
│   ├── eval_results/                 평가 실행 결과 자동 저장 폴더 (GitHub 미포함)
│   ├── finetune_train.jsonl          파인튜닝 학습 데이터 262건
│   ├── finetune_val.jsonl            파인튜닝 검증 데이터 30건
│   ├── memories.json                 장기기억 chunk 44개
│   ├── memory_embeddings.json        장기기억 임베딩 벡터
│   ├── short_term_memories.json      로컬 단기기억 (운영 기본값은 비활성)
│   ├── 전사본.txt                      1~13 순서 정렬
│   ├── 전사본_화자구분.txt               화자 분리 완료
│   └── 할머니_분석.md                   7개 카테고리 분석
│
├── docs/                             문서
│   └── backend_integration.md        AI ↔ 백엔드 연동 가이드
│
├── llm/                              LLM 호출
│   └── core.py                       generate_response() — GPT 호출 + RAG 연결
│
├── memory/                           기억 시스템
│   ├── embeddings.py                 임베딩 생성 + RAG 검색 (top_k=5)
│   ├── extract.py                    중요도 평가 → 단기기억 자동 저장
│   ├── importance.py                 중요도 평가 (GPT 기반 5차원)
│   └── short_term.py                 단기기억 스키마 + 저장/조회/강도갱신
│
├── prompt/                           프롬프트 + 평가
│   ├── eval_all.py                   프롬프트별 3-way 평가 + RAG 디버그 출력
│   ├── eval_dialogue.py              이어지는 대화형 3-way 평가
│   ├── eval_questions.py             단발 평가 질문 + 대화 평가 시나리오
│   ├── eval_run.py                   평가 실행
│   ├── eval_suite.py                 단발 + 대화형 평가 통합 실행
│   ├── system_prompt.py              현재 사용 중인 프롬프트 (C를 가리킴)
│   ├── system_prompt_A.py            상세 프롬프트 (기본 GPT용)
│   ├── system_prompt_B.py            최소 프롬프트 (파인튜닝용)
│   └── system_prompt_C.py            RAG 운영용 중간 프롬프트
│
├── tools/                            유틸리티 스크립트
│   ├── build_finetune_dataset.py     전사본 → JSONL 변환
│   ├── reorder_transcript.py         전사본 순서 정렬
│   └── run_finetune.py               파인튜닝 실행/상태 확인
│
├── voice/                            음성 처리
│   ├── stt.py                        transcribe() — Whisper 음성→텍스트
│   └── tts.py                        synthesize() — ElevenLabs 텍스트→할머니 목소리
│
├── .env                              API 키 (GitHub 미포함)
├── .gitignore
├── .python-version                   Render/Python 런타임 버전 고정
├── CODE_STRUCTURE.md                 이 파일
├── README.md                         환경 세팅 + 개발 단계
├── render.yaml                       Render Web Service 배포 설정
├── server.py                         FastAPI AI 서버 (백엔드 연동 진입점)
├── Timeline.md                       개발 타임라인
└── requirements.txt                  Python 의존성
```

---

## server.py

**역할**: FastAPI AI 서버. 백엔드(NestJS)가 HTTP로 호출하는 진입점.

**실행**: `uvicorn server:app --port 8000`
**API 문서**: `http://localhost:8000/docs`

**엔드포인트**:

- `POST /ai/chat` — 텍스트 메시지 → 할머니 답변 (RAG + 단기기억 추출 포함)
- `POST /ai/embed` — 텍스트 → 임베딩 벡터
- `POST /ai/stt` — 음성 파일 → 텍스트 (Whisper)
- `POST /ai/tts` — 텍스트 → 할머니 목소리 mp3 반환 (ElevenLabs)
- `POST /ai/voice-chat` — 음성 → STT + 답변 + TTS 한 번에 처리
- `GET /health` — 헬스 체크

---

## llm/

### core.py

**역할**: AI 파트의 핵심 함수.

**주요 함수**:

- `generate_response(message, history) → str`
  - 사용자 메시지와 대화 히스토리를 받아서 할머니 답변을 반환
  - 내부 동작:
    1. 장기기억 검색 (top-5) — `memory/embeddings.py`의 `search_memories()` 호출
    2. 로컬 단기기억이 켜져 있으면 `memory/short_term.py`의 `get_active_memories()` 호출
    3. 시스템 프롬프트 + 기억 + 히스토리 + 사용자 메시지를 조립
    4. GPT API 호출 (`OPENAI_CHAT_MODEL`, 기본값 gpt-4.1-mini)
    5. 답변 반환
  - 테스트 루프 포함: `python -m llm.core`로 터미널에서 연속 대화 가능
  - 로컬 테스트 루프에서는 `memory/extract.py`의 `extract_and_save()`로 단기기억 자동 추출
  - 운영 배포에서는 `PERSONA_ENABLE_LOCAL_SHORT_TERM=false`를 사용하고, 기억 저장 기준은 백엔드 DB로 둠

---

## memory/

### embeddings.py

**역할**: 장기기억 임베딩 생성 + RAG 검색.

**주요 함수**:

- `build_embeddings()` — `data/memories.json`의 44개 chunk를 벡터로 변환, `data/memory_embeddings.json`에 저장
  - 실행: `python -m memory.embeddings --build`
- `search_memories(query, top_k=5) → list` — 사용자 질문과 가장 관련 있는 장기기억 검색
  - 코사인 유사도 기반
  - 반환: `[{"id", "title", "content", "score"}, ...]`
- `get_embedding(text) → list` — 텍스트를 벡터로 변환 (text-embedding-3-small)

### extract.py

**역할**: 중요도 평가 → 6점 이상이면 단기기억에 저장.

**주요 함수**:

- `extract_and_save(user_message, assistant_message) → dict`
  - `evaluate_importance()` 호출 → importance ≥ 6이면 `add_short_term_memory()` 호출
  - 반환: `{"saved": True, "memory": {...}}` 또는 `{"saved": False, ...}`
  - `llm/core.py` 대화 루프에서 매 턴 후 자동 호출됨

### importance.py

**역할**: 대화 한 턴의 중요도를 GPT로 평가.

**주요 함수**:

- `evaluate_importance(user_message, assistant_message) → dict`
  - GPT한테 5차원 기준으로 1~10점 평가 요청
  - 반환: `{"importance": 7, "category": "직장", "summary": "...", "reason": "..."}`
  - 5차원: 감정(30%), 관계(25%), 개인정보(15%), 기억가치(20%), 연속성(10%)

### short_term.py

**역할**: 단기기억 관리 (저장/조회/강도갱신).

**주요 함수**:

- `add_short_term_memory(content, category, importance) → dict` — 새 단기기억 추가
- `get_active_memories() → list` — archived되지 않은 활성 단기기억 반환
- `on_memory_recalled(memory_id)` — 단기기억이 프롬프트에 포함됐을 때 strength +1, recall_count +1
- `PERSONA_SHORT_TERM_PATH` — 평가/테스트 시 단기기억 저장 파일을 임시 경로로 바꾸는 환경변수

**스키마**:

```json
{
  "id": "ST-0001",
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

---

## prompt/

### system_prompt.py

**역할**: 현재 사용 중인 프롬프트 선택. C를 가리킴.

### system_prompt_A.py

**역할**: 상세 프롬프트 (기본 GPT용). 할머니_분석.md 기반.

- 가족 정보, 건강, 성격, 말투 규칙, 실제 사투리 목록, 금지 사투리, 말투 예시, 대화 패턴, 금지 사항 포함

### system_prompt_B.py

**역할**: 최소 프롬프트 (파인튜닝 모델용). 말투 지시 없음.

- 기본 정보, 가족 관계, 금지 사항만 포함

### system_prompt_C.py

**역할**: 기본 GPT + RAG 운영용 중간 프롬프트.

- RAG 기억을 우선 사용
- 기억에 없는 구체 사실/레시피/가족관계 환각 금지
- 영어 출력 금지
- 짧은 통화체 유지

### eval_questions.py

**역할**: 평가용 단발 질문과 대화형 시나리오.

- `EVAL_SET`: 단발 질문 평가. 실제 답변/기대 답변과 평가포인트 포함
- `DIALOGUE_EVAL_SET`: 3~4턴짜리 이어지는 대화 시나리오

### eval_run.py

**역할**: 현재 설정(프롬프트 + 모델 + RAG)으로 단발 eval 질문 실행.

- `python -m prompt.eval_run`으로 실행

### eval_all.py

**역할**: 프롬프트 버전별 3-way 평가 스크립트.

- `python -m prompt.eval_all [A|B|C]`로 프롬프트 버전 선택
- 같은 질문에 대해 기본모델+RAG, 파인튜닝모델+RAG, 초기 프로토타입을 나란히 출력
- 기본 출력에서는 RAG 상세와 평가포인트를 숨김
- `--show-rag`, `--show-points` 옵션으로 RAG 검색 결과와 평가포인트 출력 가능
- `PROTO_OPENAI_API_KEY`가 없으면 sibling 폴더 `TalkTo_prototype/.env`의 `OPENAI_API_KEY`와 `OPENAI_MODEL`을 사용
- 초기 프로토타입 평가는 `PROTO_OPENAI_API_KEY` 또는 `TalkTo_prototype/.env`가 있을 때 실행 가능
- 결과는 `data/eval_results/eval_[A|B|C]_YYYYMMDD_HHMMSS.md`로 자동 저장

### eval_dialogue.py

**역할**: 이어지는 대화형 3-way 평가 스크립트.

- `python -m prompt.eval_dialogue [A|B|C]`로 프롬프트 버전 선택
- 시나리오별로 각 모델의 이전 대화 history를 유지하며 다음 턴을 평가
- 실제 `extract_and_save()`와 `get_active_memories()`를 사용하되, 모델별 임시 단기기억 파일을 써서 운영 JSON을 오염시키지 않음
- 기본 출력에서는 RAG 상세, 평가포인트, 단기기억 저장판정을 숨김
- `--show-rag`, `--show-points`, `--show-memory` 옵션으로 디버그 정보 출력 가능
- 맥락 유지, 반복 질문, 잘못된 전제 수정, 후속 질문 처리 능력을 확인
- 결과는 `data/eval_results/dialogue_[A|B|C]_YYYYMMDD_HHMMSS.md`로 자동 저장

### eval_suite.py

**역할**: 단발 평가와 대화형 평가를 한 번에 실행.

- `python -m prompt.eval_suite [A|B|C]`로 프롬프트 버전 선택
- 내부에서 `eval_all` 실행 후 `eval_dialogue` 실행
- `--show-rag`, `--show-points`, `--show-memory` 옵션을 하위 평가에 전달
- 결과 파일은 각각 `data/eval_results/`에 저장

---

## voice/

### stt.py

**역할**: 음성 → 텍스트 변환 (Speech-to-Text).

**주요 함수**:

- `transcribe(audio_file_path) → dict`
  - OpenAI Whisper API 사용, 한국어 설정
  - 반환: `{"success": True, "text": "변환된 텍스트"}` 또는 `{"success": False, "error": "..."}`

### tts.py

**역할**: 텍스트 → 할머니 목소리 변환 (Text-to-Speech).

**주요 함수**:

- `synthesize(text, output_path="output.mp3") → dict`
  - ElevenLabs API 사용, 할머니 클로닝 음성 (voice_id: lTmcD2Kp43w1lHsMQmq7)
  - 반환: `{"success": True, "path": "파일경로"}` 또는 `{"success": False, "error": "..."}`

---

## data/


| 파일                       | 설명                                                                   |
| ---------------------------- | ------------------------------------------------------------------------ |
| `memories.json`            | 장기기억 chunk 44개 (가족, 요리, 추억, 가치관 등)                      |
| `memory_embeddings.json`   | 44개 chunk의 임베딩 벡터.`python -m memory.embeddings --build`로 생성  |
| `short_term_memories.json` | 로컬 단기기억. 운영 배포에서는 기본 비활성, 로컬 테스트/eval에서 사용 |
| `전사본.txt`               | 할머니 통화 13개 파일 1~13 순서 정렬                                   |
| `전사본_화자구분.txt`      | 화자 분리 완료. "할머니:" / "손녀:" 표시                               |
| `할머니_분석.md`           | 7개 카테고리 분석 (기본정보, 언어, 성격, 관계, 가치관, 기억, 대화패턴) |
| `finetune_train.jsonl`     | 파인튜닝 학습 데이터 262건                                             |
| `finetune_val.jsonl`       | 파인튜닝 검증 데이터 30건                                              |
| `eval_results/`            | `eval_all`, `eval_dialogue` 실행 결과 자동 저장 폴더                  |
| `voice_raw/`               | 할머니 음성 원본 wav 13개 (GitHub 미포함)                              |

---

## docs/

### backend_integration.md

AI ↔ 백엔드 연동 가이드. 엔드포인트 형식, 백엔드 수정 필요 사항, 기억 데이터 방향.

---

## tools/


| 파일                        | 설명                          | 실행                                                |
| ----------------------------- | ------------------------------- | ----------------------------------------------------- |
| `build_finetune_dataset.py` | 전사본 → 파인튜닝 JSONL 변환 | `python tools/build_finetune_dataset.py`            |
| `reorder_transcript.py`     | 전사본 1~13 순서 정렬         | `python tools/reorder_transcript.py`                |
| `run_finetune.py`           | 파인튜닝 실행/상태 확인       | `python tools/run_finetune.py` / `--check [job_id]` |

---

## 기타


| 파일                    | 설명                                                                                      |
| ------------------------- | ------------------------------------------------------------------------------------------- |
| `.env`                  | API 키/모델/운영 옵션: OPENAI_API_KEY, OPENAI_CHAT_MODEL, PERSONA_ENABLE_LOCAL_SHORT_TERM, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID (GitHub 미포함) |
| `requirements.txt`      | Python 의존성 목록                                                                        |
| `.python-version`       | Render에서 사용할 Python 버전                                                            |
| `render.yaml`           | Render Web Service 배포 설정                                                             |
| `.gitignore`            | 제외: .env, venv/,__pycache__/, .DS_Store, data/voice_raw/, data/*.mp3                    |
| `.vscode/settings.json` | VS Code 터미널 설정                                                                       |
