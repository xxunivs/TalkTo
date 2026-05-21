# PersonaAI 코드 파일 설명

이 문서는 `PersonaAI` 폴더 안의 파일들이 각각 무슨 역할을 하는지 쉽게 이해하기 위한 정리 문서다.

## 기준

- **운영 핵심**: Render에 올라간 AI 서버가 실제로 사용하는 파일.
- **백엔드 연동**: 백엔드가 AI 서버를 호출할 때 직접 관련되는 파일.
- **평가/실험용**: 프롬프트, RAG, 파인튜닝 모델 품질을 비교하려고 쓰는 파일.
- **학습/데이터 준비용**: 파인튜닝 데이터나 기억 데이터를 만들기 위한 파일.
- **문서/기록용**: 개발 과정, 연동 사항, 구조 설명을 남긴 파일.

---

## 루트 파일

### `server.py`

FastAPI 서버의 시작점이다.

백엔드가 호출하는 API들이 여기 있다.

- `/health`: 서버가 살아 있는지 확인
- `/ai/chat`: 사용자 메시지를 받아 할머니 답변 생성
- `/ai/embed`: 텍스트를 임베딩 벡터로 변환
- `/ai/memory/extract`: 대화에서 저장할 만한 단기기억 후보 판단
- `/ai/stt`: 음성을 텍스트로 변환
- `/ai/tts`: 텍스트를 mp3 음성으로 변환
- `/ai/voice-chat`: 음성 입력부터 음성 답변까지 한 번에 처리하는 프로토타입용 API

현재 백엔드 연동에서 가장 중요한 파일이다.

### `requirements.txt`

이 프로젝트가 실행될 때 필요한 Python 패키지 목록이다.

Render가 배포할 때 이 파일을 보고 FastAPI, OpenAI SDK, numpy 같은 라이브러리를 설치한다.

### `render.yaml`

Render 배포 설정 파일이다.

Render에서 어떤 명령으로 서버를 빌드하고 실행할지, 어떤 환경변수를 받을지 적혀 있다.

예를 들면 `OPENAI_API_KEY`, `OPENAI_CHAT_MODEL`, `AI_SERVER_TOKEN` 같은 값이 여기 정의되어 있다.

### `.env`

로컬에서만 쓰는 비밀 설정 파일이다.

OpenAI API key, ElevenLabs key, AI server token 같은 실제 비밀값이 들어간다.

GitHub에는 올라가면 안 되는 파일이고, `.gitignore`에 포함되어 있다.

### `.gitignore`

GitHub에 올리지 말아야 할 파일을 정하는 파일이다.

예를 들면 `.env`, `venv/`, `__pycache__/`, 평가 결과 파일, mp3 파일 등이 제외되어 있다.

---

## 운영 핵심 코드

### `llm/core.py`

실제로 할머니 답변을 만드는 중심 코드다.

하는 일은 세 가지다.

1. 사용할 모델을 정한다.
2. 시스템 프롬프트와 기억, 이전 대화 기록을 합쳐 최종 프롬프트를 만든다.
3. OpenAI Chat API를 호출해서 답변을 받는다.

백엔드가 `/ai/chat`을 부르면 결국 이 파일의 `generate_response()`가 실행된다.

### `prompt/system_prompt.py`

현재 사용할 시스템 프롬프트 버전을 고르는 파일이다.

지금은 `system_prompt_C.py`를 가져와서 쓰고 있다.

모델의 말투나 답변 규칙을 바꾸고 싶을 때 최종적으로 어떤 프롬프트를 사용할지 여기서 연결된다.

### `prompt/system_prompt_C.py`

현재 운영 기준에 가장 가까운 시스템 프롬프트다.

파인튜닝 모델과 RAG 기억을 같이 쓸 때, 모델이 너무 길게 설명하거나 기억에 없는 말을 지어내지 않도록 규칙을 적어둔 파일이다.

현재 가장 중요한 프롬프트 파일이다.

### `memory/embeddings.py`

장기기억을 검색하기 위한 파일이다.

주요 역할은 두 가지다.

1. 텍스트를 OpenAI 임베딩 벡터로 바꾼다.
2. 사용자 질문과 비슷한 장기기억을 찾아낸다.

백엔드 연동 후에는 백엔드가 기억 검색을 담당하지만, `/ai/embed`에서 쓰는 `get_embedding()`은 여전히 중요하다.

로컬 테스트에서는 `data/memories.json`과 `data/memory_embeddings.json`을 읽어서 직접 RAG 검색도 한다.

### `memory/importance.py`

새 대화가 기억할 만한 내용인지 판단하는 파일이다.

예를 들어 사용자가 “나 다음 달에 이사해”라고 말하면, 이걸 다음에도 기억해야 할 정보인지 GPT에게 평가시킨다.

결과로 중요도, 카테고리, 요약, 판단 이유를 돌려준다.

백엔드의 `/ai/memory/extract` 연동에서 중요하다.

### `memory/extract.py`

`memory/importance.py`의 평가 결과를 보고 실제 단기기억으로 저장할지 결정하는 파일이다.

중요도 6 이상이면 저장 대상으로 본다.

운영 서버에서는 실제 저장은 백엔드 DB가 담당하므로, 이 파일의 로컬 저장 기능은 주로 로컬 테스트와 평가용이다.

### `memory/short_term.py`

로컬 단기기억 파일을 관리하는 코드다.

`data/short_term_memories.json`에 단기기억을 저장하고, 다시 불러오고, 회상 횟수와 강도를 갱신한다.

운영에서는 `PERSONA_ENABLE_LOCAL_SHORT_TERM=false`로 두기 때문에 백엔드 DB가 기억 저장소의 기준이 된다.

### `voice/stt.py`

음성 파일을 텍스트로 바꾸는 코드다.

OpenAI Whisper API를 사용한다.

백엔드가 `/ai/stt`를 호출하면 이 파일의 `transcribe()`가 실행된다.

### `voice/tts.py`

텍스트 답변을 할머니 목소리 mp3로 바꾸는 코드다.

ElevenLabs API를 사용한다.

백엔드가 `/ai/tts`를 호출하면 이 파일의 `synthesize()`가 실행된다.

---

## 프롬프트 파일

### `prompt/system_prompt_A.py`

아주 상세한 프롬프트 버전이다.

할머니 성격, 말투, 가족 정보, 금지사항을 길게 적어둔 버전이다.

기본 GPT 모델을 쓸 때 말투를 강하게 잡기 위한 실험용에 가깝다.

### `prompt/system_prompt_B.py`

최소 프롬프트 버전이다.

파인튜닝 모델이 이미 말투를 학습했다고 보고, 프롬프트에는 최소한의 정보와 금지사항만 둔 버전이다.

### `prompt/system_prompt_C.py`

중간 프롬프트 버전이다.

현재 가장 현실적인 운영 후보로, 파인튜닝 모델의 말투를 살리면서도 RAG 기억을 어떻게 써야 하는지 명확히 알려준다.

### `prompt/system_prompt.py`

현재 실제로 사용할 프롬프트를 선택하는 연결 파일이다.

지금은 C 버전을 사용한다.

---

## 평가 코드

### `prompt/eval_questions.py`

평가 질문 목록이 들어 있는 파일이다.

단발 질문 평가용 질문과 이어지는 대화 평가용 시나리오가 들어 있다.

“할머니 저녁 먹었어?”, “불고기 양념 어떻게 해?” 같은 질문과 실제 답변, 평가 포인트를 저장한다.

### `prompt/eval_all.py`

단발 질문 평가를 실행하는 핵심 스크립트다.

같은 질문에 대해 세 가지를 비교한다.

1. 기본 모델
2. 파인튜닝 모델
3. 초기 프로토타입 모델

프롬프트 A/B/C를 바꿔가며 답변 품질을 비교할 수 있다.

### `prompt/eval_dialogue.py`

이어지는 대화 평가를 실행하는 스크립트다.

한 번짜리 질문이 아니라 여러 턴의 대화를 이어가며 평가한다.

평가용 임시 단기기억 파일을 써서, 실제 운영 단기기억 파일을 더럽히지 않는다.

### `prompt/eval_suite.py`

단발 질문 평가와 이어지는 대화 평가를 한 번에 실행하는 스크립트다.

프롬프트를 바꾼 뒤 전체적으로 비교하고 싶을 때 가장 편하게 쓰는 파일이다.

### `prompt/eval_run.py`

초기 말투 평가용 스크립트다.

현재 평가 흐름은 `eval_all.py`, `eval_dialogue.py`, `eval_suite.py` 쪽이 더 정리되어 있어서, 이 파일은 예전 단순 평가용에 가깝다.

---

## 데이터 준비와 학습 스크립트

### `tools/reorder_transcript.py`

원본 전사본을 파일 번호 순서대로 다시 정렬하는 스크립트다.

초기 데이터 정리에 쓰던 파일이다.

### `tools/build_finetune_dataset.py`

화자 구분된 전사본을 읽어서 파인튜닝용 JSONL 파일을 만드는 스크립트다.

손녀 발화는 user, 할머니 발화는 assistant로 바꾸고, 여러 턴 대화 형태로 학습 데이터를 만든다.

출력 파일은 `data/finetune_train.jsonl`, `data/finetune_val.jsonl`이다.

### `tools/run_finetune.py`

OpenAI 파인튜닝 작업을 시작하거나 상태를 확인하는 스크립트다.

훈련 파일과 검증 파일을 업로드하고, 파인튜닝 job을 만든다.

### `tools/export_backend_memories.py`

AI repo의 기존 장기기억을 백엔드가 import할 수 있는 형태로 바꾸는 스크립트다.

`data/memories.json`을 읽어서 `data/backend_memory_import.json`을 만든다.

백엔드 DB로 기존 44개 장기기억을 옮길 때 중요한 파일이다.
source memory title/content에 명시된 인물만 `relatedPeople`로 보수적으로
추출하고, category와 legacy ID는 tags에 보존한다.

---

## 데이터 파일

### `data/memories.json`

기존 장기기억 44개가 들어 있는 파일이다.

가족, 건강, 요리, 추억, 가치관 같은 기억 조각들이 들어 있다.

로컬 RAG 검색과 백엔드 import의 원본 데이터다.

### `data/memory_embeddings.json`

`data/memories.json`의 각 기억을 임베딩 벡터로 바꾼 결과다.

로컬 RAG 검색에서 사용한다.

백엔드 연동 후에는 백엔드 DB가 새 임베딩을 만들기 때문에, 운영 기준에서는 직접 쓰지 않는다.

### `data/backend_memory_import.json`

백엔드 `Memory` DB에 넣기 좋게 변환된 장기기억 파일이다.

백엔드의 import 스크립트가 이 파일을 읽어서 44개 장기기억을 DB에 넣는다.
현재 Neon DB에는 해당 legacy memory import가 반영되어 있다.

### `data/short_term_memories.json`

로컬 단기기억 저장 파일이다.

운영에서는 백엔드 DB가 단기기억 저장소가 되므로, 이 파일은 로컬 테스트와 평가용에 가깝다.

### `data/finetune_train.jsonl`

파인튜닝 훈련 데이터다.

할머니 실제 통화체를 모델이 배우도록 만든 데이터다.

### `data/finetune_val.jsonl`

파인튜닝 검증 데이터다.

학습 중 모델 성능을 확인하는 데 사용한다.

### `data/전사본.txt`

원본 음성 전사 내용을 정리한 텍스트 파일이다.

### `data/전사본_화자구분.txt`

손녀와 할머니 발화를 구분해둔 전사본이다.

파인튜닝 데이터셋을 만들 때 이 파일을 사용한다.

### `data/할머니_분석.md`

할머니 말투, 성격, 대화 패턴, 기억을 분석해둔 문서다.

프롬프트와 평가 기준을 만들 때 참고한 자료다.

---

## 백엔드 연동 문서

### `docs/ai_backend_integration_ver1.md`

초기 백엔드 연동 방향을 정리한 문서다.

백엔드와 AI 서버가 어떤 역할을 나눌지 처음 논의할 때의 기준이다.

### `docs/ai_backend_integration_ver2.md`

백엔드 개발자 회신 내용을 정리한 문서다.

무엇이 이미 구현됐고, 앞으로 무엇을 정해야 하는지 1차로 정리되어 있다.

### `docs/ai_backend_integration_ver3.md`

Render 배포 직후 토큰, 기억 import, 음성 분리 호출, 단기기억 저장 흐름을
정리한 기록이다.

### `docs/ai_backend_integration_ver4.md`

현재 배포/운영 연동 상태와 남은 운영 항목을 정리한 문서다.

---

## 기타 문서

### `README.md`

프로젝트 실행 방법과 주요 명령어를 설명하는 기본 문서다.

### `CODE_STRUCTURE.md`

폴더 구조와 각 모듈의 관계를 설명하는 문서다.

이 문서보다 구조 중심으로 정리되어 있다.

### `HANDOFF.md`

다른 개발자나 미래의 작업자가 이어받을 수 있도록 현재 상태와 맥락을 정리한 문서다.

### `Timeline.md`

프로젝트를 어떤 단계로 진행했는지 기록한 문서다.

---

## 정리할 때 참고할 구분

### 운영에서 바로 쓰는 핵심

- `server.py`
- `llm/core.py`
- `prompt/system_prompt.py`
- `prompt/system_prompt_C.py`
- `memory/embeddings.py`
- `memory/importance.py`
- `voice/stt.py`
- `voice/tts.py`
- `render.yaml`
- `requirements.txt`

### 백엔드 연동에 필요한 자료

- `data/backend_memory_import.json`
- `tools/export_backend_memories.py`
- `docs/ai_backend_integration_ver3.md`
- `docs/ai_backend_integration_ver4.md`

### 평가/실험용

- `prompt/eval_all.py`
- `prompt/eval_dialogue.py`
- `prompt/eval_suite.py`
- `prompt/eval_questions.py`
- `prompt/system_prompt_A.py`
- `prompt/system_prompt_B.py`
- `prompt/eval_run.py`

### 파인튜닝/데이터 준비용

- `tools/build_finetune_dataset.py`
- `tools/run_finetune.py`
- `tools/reorder_transcript.py`
- `data/finetune_train.jsonl`
- `data/finetune_val.jsonl`
- `data/전사본.txt`
- `data/전사본_화자구분.txt`

### 로컬 테스트 중심

- `data/memories.json`
- `data/memory_embeddings.json`
- `data/short_term_memories.json`
- `memory/short_term.py`

운영에서는 기억 저장과 검색의 기준이 백엔드 DB로 넘어가고 있으므로, 위 로컬 테스트 파일들은 바로 삭제하기보다 평가/디버깅 용도로 남길지 먼저 결정하는 것이 좋다.
