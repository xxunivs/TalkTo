# PersonaAI 코드 구조

이 문서는 PersonaAI 레포에서 각 폴더와 주요 파일이 어떤 역할을 하는지 쉽게 보기 위한 정리다.

## 전체 구조

```text
PersonaAI/
  server.py
  render.yaml
  requirements.txt
  README.md

  core/
    response/
    memory/
    voice/

  prompts/
  evaluation/
    results/

  finetune/
  backend_memory/
  tests/
  data/
  docs/
```

루트에는 서버 실행과 배포에 바로 필요한 파일만 남겼다. 실제 PersonaAI의 본체 로직은 `core/`에 있고, 프롬프트 실험은 `prompts/`, 평가 실행은 `evaluation/`, 파인튜닝 재현 자료는 `finetune/`, 백엔드 memory import 자료는 `backend_memory/`에 둔다.

## 루트 파일

### `server.py`

백엔드가 호출하는 FastAPI 서버의 입구다.

현재 제공하는 엔드포인트는 다음과 같다.

- `GET /health`: Render와 백엔드가 서버 상태를 확인할 때 사용한다.
- `POST /ai/chat`: 사용자 메시지와 기억을 받아 할머니 답변을 만든다.
- `POST /ai/embed`: 백엔드가 기억 검색용 임베딩을 만들 때 사용한다.
- `POST /ai/memory/extract`: 현재 대화에서 새 단기기억으로 저장할 만한 내용이 있는지 판단한다.
- `POST /ai/stt`: 음성 파일을 텍스트로 바꾼다.
- `POST /ai/tts`: 텍스트를 할머니 목소리 mp3로 바꾼다.

`AI_SERVER_TOKEN`이 설정되어 있으면 `/ai/*` 요청에는 `X-AI-Server-Token` 헤더가 필요하다. `/health`는 배포 상태 확인용이라 토큰 없이 열어둔다.

### `render.yaml`

Render 배포 설정 파일이다.

Render는 이 파일을 보고 Python 버전, build command, start command, 환경변수 목록을 잡는다. 그래서 `requirements.txt`와 함께 유지해야 한다.

### `requirements.txt`

로컬과 Render에서 같은 Python 패키지를 설치하기 위한 목록이다.

로컬에서는 `pip install -r requirements.txt`, Render에서는 `render.yaml`의 build command가 같은 파일을 사용한다.

### `README.md`

새로 레포를 받은 사람이 서버 실행, 평가 실행, Render 배포 흐름을 빠르게 따라갈 수 있도록 적어둔 시작 문서다.

## `core/`

PersonaAI의 실제 실행 로직이 들어있는 본체 폴더다. API 입구인 `server.py`는 루트에 남겨두고, 그 안에서 호출되는 핵심 기능을 `core/` 아래에 묶었다.

### `core/response/generator.py`

할머니 답변을 생성하는 중심 파일이다.

사용자 메시지, 이전 대화 `history`, 장기기억/단기기억 텍스트를 합쳐 OpenAI Chat 모델을 호출한다. 운영에서는 백엔드가 검색한 기억을 받아 쓰고, 로컬 테스트에서는 내부 JSON 기억에서 직접 검색할 수 있다.

`OPENAI_CHAT_MODEL` 환경변수를 바꾸면 기본 모델과 파인튜닝 모델을 코드 수정 없이 교체할 수 있다.

### `core/memory/embeddings.py`

로컬 JSON 기반 RAG 검색을 담당한다.

`data/memories.json`의 장기기억과 `data/memory_embeddings.json`의 임베딩을 읽고, 사용자 질문과 가까운 기억을 찾는다. 현재는 임베딩 유사도에 키워드 boost를 더해서 가족, 건강, 요리처럼 명확한 질문에서 관련 기억이 더 잘 올라오게 한다.

운영에서는 백엔드 DB 검색이 기준이고, 이 파일은 AI 단독 테스트와 fallback 용도다.

### `core/memory/importance.py`

대화 한 턴이 기억할 만큼 중요한지 판단한다.

현재 사용자 메시지와 할머니 답변뿐 아니라 최근 대화 `history`도 함께 보고, 중요도, 카테고리, 요약, 판단 이유를 JSON으로 받는다.

### `core/memory/extract.py`

`importance.py`의 판단 결과를 받아 실제 단기기억 후보를 만든다.

로컬 테스트에서는 중요도 6 이상이면 `short_term.py`를 통해 JSON 파일에 저장한다. 운영에서는 `/ai/memory/extract`가 후보만 반환하고, 실제 DB 저장은 백엔드가 맡는다.

### `core/memory/short_term.py`

로컬 단기기억 JSON 파일을 읽고 쓰는 파일이다.

`PERSONA_ENABLE_LOCAL_SHORT_TERM=true`일 때만 대화 생성 과정에서 로컬 단기기억을 자동 저장/조회한다. Render 운영 환경에서는 백엔드 DB를 기준으로 삼기 위해 꺼둔다.

### `core/voice/stt.py`

OpenAI Whisper API로 음성 파일을 텍스트로 변환한다.

백엔드는 음성 파일을 `/ai/stt`로 보내고, AI 서버는 변환된 텍스트를 반환한다.

### `core/voice/tts.py`

ElevenLabs API로 텍스트를 할머니 목소리 mp3로 변환한다.

백엔드는 `/ai/tts` 응답으로 받은 mp3 파일을 저장하고, 프론트가 접근할 수 있는 URL을 만든다.

## `prompts/`

할머니 페르소나 프롬프트를 관리하는 폴더다.

### `prompts/system_prompt.py`

현재 서버가 실제로 쓰는 프롬프트 선택 파일이다.

지금은 `system_prompt_C.py`를 바라보게 되어 있다. 서버 응답 말투를 바꾸고 싶으면 보통 `system_prompt_C.py`를 고치거나, 이 파일이 다른 버전을 바라보게 바꾸면 된다.

### `prompts/system_prompt_A.py`

프롬프트 후보 A다.

### `prompts/system_prompt_B.py`

프롬프트 후보 B다.

### `prompts/system_prompt_C.py`

프롬프트 후보 C다.

## `evaluation/`

프롬프트와 모델 답변 품질을 비교하는 평가 코드다.

### `evaluation/eval_questions.py`

평가 질문과 기대 답변 포인트가 들어 있다.

단발 질문 평가와 이어지는 대화 평가 시나리오를 모두 이 파일에서 관리한다.

### `evaluation/eval_run.py`

프롬프트 평가의 메인 실행 파일이다.

단발 질문 평가와 이어지는 대화 평가를 한 파일에서 처리한다.

같은 질문 또는 같은 대화 흐름에 대해 기본 모델, 파인튜닝 모델, 초기 프로토타입 모델 답변을 나란히 비교한다. 필요하면 `--show-rag`, `--show-points`, `--show-memory` 옵션으로 검색된 기억, 평가 포인트, 단기기억 판정도 같이 볼 수 있다.

### `evaluation/results/`

평가 실행 결과가 자동 저장되는 폴더다.

결과 파일을 남겨두면 프롬프트나 모델을 바꿨을 때 이전 결과와 비교하기 쉽다.

## `finetune/`

파인튜닝 재현에 필요한 스크립트와 데이터가 들어 있다.

현재 운영 답변은 `OPENAI_CHAT_MODEL` 환경변수에 설정된 모델을 사용한다. 파인튜닝을 다시 실험하거나 재현해야 할 때만 이 폴더를 보면 된다.

### `finetune/build_dataset.py`

화자 구분된 전사본에서 파인튜닝용 JSONL 파일을 만든다.

입력은 `data/transcript.txt`, 출력은 `finetune/train.jsonl`과 `finetune/val.jsonl`이다.

### `finetune/run_finetune.py`

OpenAI 파인튜닝 작업을 실행하는 스크립트다.

학습 데이터 업로드, validation 데이터 업로드, fine-tuning job 생성까지 담당한다.

### `finetune/train.jsonl`, `finetune/val.jsonl`

파인튜닝 학습과 검증에 사용한 JSONL 파일이다.

## `backend_memory/`

백엔드 DB에 기존 장기기억을 넣기 위한 import 파일과 생성 스크립트가 들어 있다.

서비스 런타임에서 계속 읽는 폴더는 아니고, 백엔드 DB를 새로 만들거나 기존 장기기억을 다시 import해야 할 때 사용한다.

### `backend_memory/build_memory_import.py`

AI 레포의 장기기억 JSON을 백엔드 import 형식으로 변환한다.

입력은 `data/memories.json`, 출력은 `backend_memory/memory_import.json`이다.

### `backend_memory/memory_import.json`

백엔드가 기존 장기기억을 DB로 가져갈 때 쓰는 import 데이터다.

이미 운영 DB에 import가 끝난 경우 매 요청마다 쓰이는 파일은 아니지만, 새 DB 세팅이나 재import가 필요할 때 기준 파일로 사용한다.

## `tests/`

테스트 코드가 들어 있다.

### `backend_memory/test_build_memory_import.py`

`backend_memory/build_memory_import.py`가 백엔드 import 형식에 맞게 변환하는지 확인한다.

특히 가족 이름, 별칭, `relatedPeople` 추출이 의도대로 되는지 검사한다.

## `data/`

모델이 참조하거나 도구가 생성하는 데이터 파일이다.

### `data/memories.json`

AI 단독 테스트용 장기기억 원본이다.

운영에서는 백엔드 DB가 기준이지만, 백엔드 DB가 완전히 붙기 전에는 이 파일로 RAG 품질을 테스트한다.

### `data/memory_embeddings.json`

`memories.json`의 각 기억에 대한 임베딩 캐시다.

로컬 RAG 검색에서 매번 임베딩을 새로 만들지 않기 위해 저장해둔다.

### `data/short_term_memories.json`

로컬 테스트용 단기기억 저장 파일이다.

운영 Render에서는 `PERSONA_ENABLE_LOCAL_SHORT_TERM=false`라서 이 파일을 기준으로 삼지 않는다.

### `data/transcript.txt`, `data/persona_analysis.md`

`data/transcript.txt`는 화자 구분이 끝난 최종 전사본이다. 파인튜닝 데이터 생성은 이 파일을 기준으로 한다.

`data/persona_analysis.md`는 전사본을 바탕으로 말투, 성격, 관계 정보를 정리한 분석 자료다.

### `data/voice_raw/`

할머니 목소리 원본 파일이 들어 있다.

STT 테스트나 음성 관련 실험에서 참조한다.

## `docs/`

프로젝트 설명과 백엔드 협의 문서를 모아둔 폴더다.

### `docs/CODE_STRUCTURE.md`

현재 보고 있는 코드 구조 설명 문서다.

### `docs/Timeline.md`

프로젝트가 어떤 순서로 발전했는지 기록한 개발 타임라인이다.

### `docs/LeftToDo.md`

아직 나중에 정리해야 할 임시 운영 사항을 적어둔 파일이다.

### `docs/ai_backend_integration/ai_backend_integration_ver*.md`

백엔드와 AI 서버 연동을 논의하면서 남긴 버전별 문서다.

`ver1`, `ver2`, `ver3`는 논의 과정 기록이라 현재 코드와 일부 표현이 다를 수 있다. 최신 운영 기준은 `ver4`와 현재 코드 구조를 함께 보면 된다.

## 자주 보는 실행 명령

서버 실행:

```bash
uvicorn server:app --port 8000
```

평가 전체 실행:

```bash
python -m evaluation.eval_run C
```

단발 질문만 평가:

```bash
python -m evaluation.eval_run C --single
```

이어지는 대화만 평가:

```bash
python -m evaluation.eval_run C --dialogue
```

백엔드 import용 장기기억 생성:

```bash
python backend_memory/build_memory_import.py
```
