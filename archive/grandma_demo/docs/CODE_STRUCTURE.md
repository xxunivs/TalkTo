# PersonaAI 코드 구조

이 문서는 PersonaAI 레포의 폴더 구조와 각 파일의 역할을 설명한다.
실제 코드와 데이터가 모여 있는 하위 구조만 정리한다.

## 전체 구조

```text
PersonaAI/
  core/
    response/
      generator.py
    memory/
      embeddings.py
      importance.py
      extract.py
      short_term.py
    voice/
      stt.py
      tts.py

  prompts/
    system_prompt.py

  evaluation/
    eval_questions.py
    eval_run.py
    results/

  data/
    memories.json
    memory_embeddings.json
    short_term_memories.json
    transcript.txt
    interview.txt
    persona_analysis.md
    voice_raw/

  backend_memory/
    build_memory_import.py
    memory_import.json
    test_build_memory_import.py

  finetune/
    build_dataset.py
    run_finetune.py
    train.jsonl
    val.jsonl

  docs/
    CODE_STRUCTURE.md
```

## `core/`

서비스 런타임에서 실제로 호출되는 본체 로직이다.
답변 생성, 기억 검색·추출, 음성 변환처럼 API 요청을 처리할 때 필요한 기능이 이 폴더 아래에 모여 있다.

### `core/response/`

할머니 답변을 생성하는 응답 레이어다.

#### `core/response/generator.py`

사용자 메시지, 대화 히스토리, 장기기억, 단기기억을 합쳐 OpenAI Chat 모델을 호출한다.

주요 역할:

- `prompts/system_prompt.py`의 단일 시스템 프롬프트를 불러온다.
- `OPENAI_CHAT_MODEL` 환경변수로 사용할 Chat 모델을 결정한다.
- 백엔드가 넘겨준 memory text가 있으면 그 기억을 우선 사용한다.
- memory text가 없으면 로컬 JSON 기반 RAG를 fallback으로 사용한다.
- `PERSONA_ENABLE_LOCAL_SHORT_TERM=true`일 때만 로컬 단기기억을 조회한다.

운영 Render 환경에서는 단기기억의 기준이 백엔드 DB이므로 `PERSONA_ENABLE_LOCAL_SHORT_TERM=false`로 둔다.

### `core/memory/`

장기기억 검색, 단기기억 후보 판단, 로컬 단기기억 저장을 담당한다.

#### `core/memory/embeddings.py`

로컬 JSON 기반 장기기억 RAG 검색기다.

입력 데이터:

- `data/memories.json`
- `data/memory_embeddings.json`

검색 방식:

- 사용자 질문을 `text-embedding-3-small`로 임베딩한다.
- 저장된 기억 임베딩과 cosine similarity를 계산한다.
- 가족 이름, 요리, 추모, 건강, 성당 등 명확한 키워드는 boost rule로 보정한다.
- 상위 기억을 `id`, `title`, `content`, `tags`, `score` 형태로 반환한다.

운영에서는 백엔드 DB 검색이 기준이고, 이 파일은 AI 단독 테스트와 fallback 용도다.

#### `core/memory/importance.py`

현재 대화 턴이 나중에 기억할 만한 정보인지 판단한다.

입력:

- 최근 대화 히스토리
- 현재 사용자 메시지
- 현재 assistant 답변

출력:

- `importance`: 1~10 중요도
- `category`: 가족, 건강, 직장, 일상, 감정, 기타
- `summary`: 저장할 한 줄 요약
- `reason`: 판단 이유

중요도 판단은 `gpt-4.1-mini`를 사용하고, 응답은 JSON으로 파싱한다.

#### `core/memory/extract.py`

`importance.py`의 판단 결과를 받아 단기기억 저장 여부를 결정한다.

로컬 테스트에서는 중요도 6 이상이면 `short_term.py`를 통해 JSON 파일에 저장한다.
운영에서는 이 로직이 `/ai/memory/extract` 응답의 기준이 되고, 실제 DB 저장은 백엔드가 담당한다.

#### `core/memory/short_term.py`

로컬 단기기억 JSON 파일을 읽고 쓰는 유틸리티다.

주요 역할:

- 단기기억 추가
- 활성 단기기억 조회
- 기억 recall count와 strength 갱신
- `PERSONA_SHORT_TERM_PATH` 환경변수를 통한 평가용 임시 파일 분리

기본 저장 위치는 `data/short_term_memories.json`이다.

### `core/voice/`

음성 입출력 기능을 담당한다.

#### `core/voice/stt.py`

음성 파일을 텍스트로 변환한다.
OpenAI 음성 인식 API를 사용하며, 백엔드의 음성 입력 흐름에서 호출된다.

#### `core/voice/tts.py`

텍스트 답변을 할머니 목소리 mp3로 변환한다.
ElevenLabs API를 사용하며, 백엔드는 반환된 음성 파일을 저장해 프론트에서 재생할 수 있게 한다.

## `prompts/`

운영 프롬프트를 관리하는 폴더다.

### `prompts/system_prompt.py`

현재 서버와 평가가 공통으로 사용하는 단일 시스템 프롬프트다.

포함 내용:

- 신금자 할머니 기본 정보
- 사용자 식별 정책
- 가족 8명별 호칭과 관계 톤
- 말투와 대화 흐름 원칙
- 추모·사후 응답 기준
- 장기기억·단기기억 활용 원칙
- 금지 표현과 안전 가드레일

프롬프트 품질을 바꾸려면 이 파일을 수정하고, `evaluation/`의 평가를 돌려 변경 전후를 비교한다.

## `evaluation/`

프롬프트와 모델 답변 품질을 비교 평가하는 코드다.
운영 서버를 직접 띄우지 않고도 같은 프롬프트, 같은 로컬 RAG 기준으로 답변을 확인할 수 있다.

### `evaluation/eval_questions.py`

평가 질문과 기대답변을 관리한다.
모든 평가 항목은 user/assistant가 교대로 들어 있는 dialogue 배열이다.

현재 평가 영역:

- `reproduction`: A. 전사본 재현
- `persona`: B. 페르소나 일관성
- `user_tone`: C. 사용자별 톤
- `memorial`: D. 추모/사후
- `deep`: E. 깊어진 대화
- `edge`: F. 엣지/안전

총 35개 시나리오이며, 각 시나리오는 3~4턴 대화형 구조로 구성되어 있다.

### `evaluation/eval_run.py`

평가 실행기다.

주요 역할:

- 선택한 평가 세트 또는 전체 세트를 순서대로 실행한다.
- MVP 모델과 초기 프로토타입 모델 답변을 나란히 출력한다.
- 현재 운영 프롬프트와 로컬 RAG 검색 결과를 사용한다.
- 평가 중 단기기억은 운영 파일이 아니라 임시 JSON 파일에 저장한다.
- 결과를 Markdown 파일로 `evaluation/results/`에 저장한다.

자주 쓰는 실행 예:

```bash
python -m evaluation.eval_run
python -m evaluation.eval_run --set user_tone
python -m evaluation.eval_run --show-rag --show-memory
```

### `evaluation/results/`

평가 결과 Markdown 파일이 저장되는 위치다.
프롬프트 변경 전후 답변을 비교하거나, 특정 세트에서 회귀가 생겼는지 확인할 때 사용한다.

## `data/`

페르소나 원천 자료와 로컬 테스트용 기억 데이터가 들어 있다.
운영 DB의 기준 데이터와 AI 단독 테스트 기준 데이터를 맞추기 위해 유지한다.

### `data/memories.json`

로컬 RAG용 장기기억 원본이다.
가족 정보, 생애 정보, 요리법, 추억, 말투, 추모 정책에 필요한 기억들이 구조화되어 있다.

운영에서는 백엔드 DB가 장기기억의 기준이지만, 평가와 AI 단독 테스트에서는 이 파일을 사용한다.

### `data/memory_embeddings.json`

`memories.json` 각 항목의 임베딩 캐시다.
로컬 RAG 검색에서 매번 모든 장기기억을 다시 임베딩하지 않기 위해 사용한다.

`data/memories.json`을 크게 바꾸면 임베딩도 다시 생성해야 한다.

### `data/short_term_memories.json`

로컬 단기기억 테스트용 JSON 파일이다.
운영 Render에서는 사용하지 않는 것이 원칙이다.

### `data/transcript.txt`

화자 구분이 끝난 전사본이다.
파인튜닝 데이터 생성과 말투 분석의 원천 자료로 사용한다.

### `data/interview.txt`

가족 인터뷰 정리 자료다.
전사본만으로 부족한 가족 관계, 생애 정보, 사후 정보 보강에 사용한다.

### `data/persona_analysis.md`

전사본, 인터뷰, 가족 확인 정보를 합쳐 정리한 페르소나 분석 문서다.

주요 내용:

- 서비스 정체성과 페르소나 메타 원칙
- 신금자 할머니 기본 정보
- 가족 관계와 사용자별 호칭
- 말투, 언어 습관, 감정 표현
- 음식과 요리 기억
- 추모·사후 응답 기준
- 민감정보 처리 기준

프롬프트와 평가 질문을 고칠 때 가장 중요한 참고 문서다.

### `data/voice_raw/`

할머니 목소리 원본 파일이 들어 있는 폴더다.
STT 테스트, TTS 목소리 기준 확인, 음성 관련 실험에서 참조한다.

## `backend_memory/`

AI 레포의 장기기억을 백엔드 DB import 형식으로 변환하기 위한 폴더다.
서비스 런타임에서 매 요청마다 읽는 폴더는 아니고, DB 초기 세팅이나 재import가 필요할 때 사용한다.

### `backend_memory/build_memory_import.py`

`data/memories.json`을 백엔드 import 형식으로 변환한다.

변환 내용:

- `title`
- `memoryType`
- `relatedPeople`
- `relatedPeriod`
- `bodyMarkdown`
- `tags`

`relatedPeople`은 기억의 제목과 본문에 등장하는 가족 이름·별칭을 기준으로 추출한다.

### `backend_memory/memory_import.json`

백엔드가 장기기억을 DB로 가져갈 때 사용하는 import 결과물이다.
새 DB를 세팅하거나 장기기억을 다시 넣어야 할 때 기준 파일로 사용한다.

### `backend_memory/test_build_memory_import.py`

memory import 변환 결과가 기대 형식에 맞는지 확인하는 테스트다.
특히 가족 이름과 별칭이 `relatedPeople`에 제대로 들어가는지 검증한다.

## `finetune/`

파인튜닝 재현 자료가 들어 있는 폴더다.
현재 운영 답변은 기본 모델과 시스템 프롬프트, RAG를 중심으로 사용하지만, 과거 파인튜닝 실험을 재현할 수 있도록 자료를 남겨 둔다.

### `finetune/build_dataset.py`

`data/transcript.txt`를 기반으로 파인튜닝용 JSONL 데이터를 만든다.

출력:

- `finetune/train.jsonl`
- `finetune/val.jsonl`

### `finetune/run_finetune.py`

OpenAI 파인튜닝 작업을 실행한다.

주요 단계:

- 학습 파일 업로드
- 검증 파일 업로드
- fine-tuning job 생성

### `finetune/train.jsonl`, `finetune/val.jsonl`

파인튜닝 학습과 검증에 사용한 JSONL 데이터다.
모델 실험을 다시 해야 할 때 재현 기준으로 사용한다.

## `docs/`

레포 구조 설명 문서만 남긴다.

### `docs/CODE_STRUCTURE.md`

현재 보고 있는 문서다.
코드와 데이터의 역할을 폴더 중심으로 설명한다.

## 주요 흐름

### 답변 생성 흐름

```text
사용자 메시지
  -> 백엔드가 검색한 memories 또는 로컬 RAG fallback
  -> prompts/system_prompt.py
  -> core/response/generator.py
  -> OpenAI Chat 모델
  -> 할머니 답변
```

운영에서는 백엔드가 장기기억과 단기기억을 검색해 AI 서버에 넘긴다.
백엔드가 memories를 넘기지 않는 로컬 테스트 상황에서는 `core/memory/embeddings.py`가 `data/memories.json` 기반으로 검색한다.

### 단기기억 후보 추출 흐름

```text
사용자 메시지 + assistant 답변 + 최근 history
  -> core/memory/importance.py
  -> core/memory/extract.py
  -> 저장 여부, 중요도, 카테고리, 요약 반환
```

운영에서는 백엔드가 이 결과를 받아 DB에 저장한다.
로컬 테스트에서만 `data/short_term_memories.json`에 직접 저장할 수 있다.

### 평가 흐름

```text
evaluation/eval_questions.py
  -> evaluation/eval_run.py
  -> prompts/system_prompt.py
  -> data/memories.json + data/memory_embeddings.json
  -> evaluation/results/*.md
```

평가는 실제 운영 프롬프트를 기준으로 진행한다.
프롬프트를 바꾼 뒤에는 전체 평가 또는 관련 세트를 실행해서 답변 품질과 가드레일을 확인한다.

### 백엔드 장기기억 import 흐름

```text
data/memories.json
  -> backend_memory/build_memory_import.py
  -> backend_memory/memory_import.json
  -> 백엔드 DB import
```

AI 레포의 로컬 RAG 원본과 백엔드 DB의 장기기억 원천을 맞추기 위한 흐름이다.
