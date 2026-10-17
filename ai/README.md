# TalkTo_PersonaAI_AI

TalkTo 페르소나 제작과 대화를 위한 AI 서버. 녹음 분석, 페르소나 프롬프트 생성, 기억 임베딩, 음성 합성, 그리고 대화 한 턴을 처리하는 중간계층을 담는다.

**4층 페르소나 프롬프트 생성기**와 **중간계층(orchestrator)** 도 이 레포에 있다. 새 기능은 전부 환경 변수로 켜며, 기본값에서는 기존 동작과 같다.

```text
프론트 → BE(talkto_api_server) → 이 서버(/v1/...) → OpenAI · Fish Audio · ElevenLabs
```

BE 가 사용자 인증·동의·운영 DB 저장과 기억 검색을 맡고, 이 서버는 BE 가 보낸 프롬프트·기억·voice ID 로 답을 만든다.

텍스트 응답은 다 만든 뒤 한 번에 돌려준다(스트리밍 아님).

## 폴더

```text
infra/                      서버 설정, API 라우트, 스키마, 외부 API 연동
pipeline/                   제작 파이프라인 s0~s5
  persona_prompt/           4층 페르소나 프롬프트 생성기(설문·통화 전사·가족 인터뷰 → 명세 → 프롬프트)
  s3_synthesis/common_rules.py  공통 규칙 렌더러
orchestrator/               대화 한 턴의 중간계층(기억 선택 → 사전 판정 → 조립 → 생성 → 가드). 계약: orchestrator/CONTRACT.md
prompts/
  rules/                    공통 규칙 모듈(L1)과 순서(manifest.json)
  layers.json               상황별 층 구성(정적 L1~L4 + 런타임 절)
  topic_synonyms.json       사전 판정용 동의어
experiments/prompt_compare/ 프롬프트 비교 실험 도구(코드만, 데이터는 로컬)
tools/                      로컬 도구. export_manual_persona.py = 수동 제작 페르소나 → 관리자 가져오기 묶음
docs/                       Persona Intake 설문 양식과 문항 분류
test/                       자동 테스트(외부 API 호출 없음)
```

개인 데이터(녹음, 설문 작성본, 인터뷰, 파이프라인 산출물, 페르소나 등록부, 운영 DB export, 실험 결과)는 git 에 넣지 않는다. 로컬에서 아래 폴더에 두며 `.gitignore` 가 막는다.

```text
input/<대상자>/             설문 작성본, 녹음(음성/), 가족 인터뷰
output/<대상자>/            파이프라인 산출물
output/<대상자>_manual/     사람이 검토·수정한 산출물
personas_local/             중간계층 페르소나 등록부(ORCHESTRATOR_PERSONA_DIR)
experiments/prompt_compare/{data,variants,results}/
```

## 제작 파이프라인

| 단계 | 내용 |
|---|---|
| s0 설문 | 설문 작성본을 프롬프트 재료와 기억으로 변환 |
| s1 전사 | 녹음 전사, 화자 분리와 대상자 식별, 고유명사 교정 |
| s2 추출 | 인물, 민감도, 말투, 기억, 요약 분석 |
| s3 종합 | 기억 병합, 통찰 추출, 프롬프트 조립, 임베딩 생성 |
| s4 음성 | 대상자 음성 샘플 수집과 목소리 복제 |
| s5 대화 | 로컬 대화 테스트 |

통합 실행기 `pipeline.run_pipeline` 은 s1~s3 를 돈다. s0 과 s5 는 별도 명령, s4 는 API 또는 단계별 함수로 처리한다. 화자 라벨은 녹음마다 따로 붙으므로 녹음마다 대상자를 식별한다.

자료 검토, 수동 수정, 운영 DB 반영은 사람이 한다(아래 '수동 제작').

## 4층 페르소나 프롬프트

재료별 생성기가 **페르소나 명세(spec)** 하나를 채우고, 렌더러가 명세를 프롬프트로 편다. 칸마다 출처(설문 문항·인터뷰 줄·통화 세그먼트)와 생성기의 판단 기록이 남는다.

```text
설문 작성본 ──G1 규칙────────────────────┐
가족 인터뷰 ──G2 LLM + 근거 줄 검증───────┼─▶ spec.json ──템플릿──▶ L1 공통 규칙 / L2 개인 정보·정책 / L3 관계·호칭 / L4 말투
통화 전사 ────G3 통계(말투 카드·주고받기)─┘
```

| 층 | 내용 |
|---|---|
| L1 공통 규칙 | 대화 흐름, 사용자 식별, 사후 대화, 기억 쓰는 법, 말하는 형식, 안전. 사망 정책은 deny/brief/ask_only/open 변형 하나만 들어간다 |
| L2 개인 정보·정책 | 정체, 기본 정보, 성격·가치관, 상황별 반응, 주제 등급 |
| L3 관계·호칭 | 비공개: 가족 표(부르는 말·자기를 가리키는 말·말 높이·톤·걱정거리) / 공개: 방문자 안내 |
| L4 개인 말투 | 말투 서술, 말투 카드(전사 통계), 실제로 하던 말, 실제 주고받기 |

층 구성은 고정이 아니다. `prompts/layers.json` 이 페르소나 종류(공개/비공개, 생존/사후), 재료 유무, 주고받기 위치에 따라 켤 모듈을 정한다. 턴마다 바뀌는 것(지금 말하는 사람의 가족 표 줄, 이미 챙긴 말)은 런타임 절로 기억 뒤에 붙는다. 정적인 앞부분이 그대로라 프롬프트 캐시가 유지된다.

칸이 겹치면 사실·정책은 설문 > 인터뷰 > 전사, 말투 실측은 전사만 쓴다. 인터뷰 후보는 설문이 비운 칸만 채우고, 같은 주제·같은 내용·서술형 호칭은 거른다.

```bash
python -m pipeline.persona_prompt build --subject <대상자> [--shots-mode messages] [--death-talk intake|off|on]
python -m pipeline.persona_prompt extract-interview --subject <대상자>   # LLM 1회, 후보 파일은 사람이 확인
```

## 환경 변수(기능 플래그)

기본값은 모두 기존 동작이다.

| 변수 | 기본 | 뜻 |
|---|---|---|
| `PERSONA_PROMPT_BUILDER` | `legacy` | `/v1/persona/assembly`·로컬 s3_c 조립기. `layered` 면 4층 생성기 |
| `PERSONA_DEATH_TALK` | `intake` | 사인·임종 질문: 설문 그대로 / `off` 말하지 않음 / `on` 물으면 짧게. 요청의 `deathTalk` 가 우선 |
| `PERSONA_ORCHESTRATOR` | `false` | `/v1/persona/responses` 를 중간계층으로 처리 |
| `ORCHESTRATOR_ASSEMBLER` | `legacy` | `structured` 면 공통 규칙 + 페르소나 + 말투 재료 + 기억 순으로 조립 |
| `ORCHESTRATOR_RULES` | `legacy` | `layered` 면 L1 모듈 사용. 등록부에 spec.json 이 있으면 런타임 절(지금 상대·이미 한 말)도 붙인다 |
| `ORCHESTRATOR_SHOTS` | `system` | 실제 주고받기를 지시문 안 글로 / `messages` 면 기억 뒤 대화 메시지로 |
| `ORCHESTRATOR_PERSONA_DIR` | 없음 | 페르소나 등록부(profile.json, spec.json, style_card.md, shots.jsonl, restricted.json, intake.json) |
| `ORCHESTRATOR_SELECTOR` | `passthrough` | `tiered` 면 장기 기억 전부 + 단기 기억은 발화 분해 단서로 검색 |
| `ORCHESTRATOR_PRECHECK` | `noop` | `coverage`/`coverage_v11` 이면 기억이 질문 화제를 못 덮을 때 "모른다·되묻기" 지시 |
| `ORCHESTRATOR_GUARD` | `pass` | `v1`/`v11` 이면 근거 헤더·지어내기·애정·챙김 사후 검사 |
| `ORCHESTRATOR_LANG_GUARD` | `off` | `flag` 기록만 / `fix` 이번 턴 맥락에 없던 문자 체계·영어 낱말·추론 문장이 든 문장을 지움 |
| `ORCHESTRATOR_TRACE_DIR` | 없음 | 턴마다 추적 로그(jsonl, 대화 원문 포함 — git 밖 경로에) |
| `ORCHESTRATOR_PRECHECK_THRESHOLD` | `0.6` | 사전 판정 커버율 임계 |
| `ORCHESTRATOR_SELECTOR_ST_TOPK` / `_THRESHOLD` | `5` / `0.18` | tiered 선택의 단기 기억 개수·코사인 임계 |

자세한 계약과 단계별 구현은 [orchestrator/CONTRACT.md](orchestrator/CONTRACT.md).

## 실행

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,orchestrator]"
cp .env.example .env  # API 키 입력
uvicorn main:app --reload --port 8001  # BE 가 8000 사용
```

API 문서는 `http://localhost:8001/docs`. 목소리 기반 화자 식별에는 `pip install -e ".[dev,speaker]"` 와 `ffmpeg`, 음성 샘플 수집에는 `ffprobe` 가 필요하다.

### 파이프라인

```bash
# 설문 변환 후 s1~s3 실행 (설문: input/<대상자>/Persona_Intake_*.md, 녹음: input/<대상자>/음성/)
python -m pipeline.s0_intake.b_split_intake --subject <대상자> --subject-out <대상자> --write
python -m pipeline.run_pipeline --subject <대상자>

# 필요한 단계만
python -m pipeline.run_pipeline --subject <대상자> --only s2_e

# 로컬 대화 테스트
python -m pipeline.s5_chat.a_chat --subject <대상자> --tts
```

설문 변환에서 `--subject-out` 을 생략하면 `<대상자>_manual` 에 저장한다. `run_pipeline --subject <대상자>_manual` 은 입력을 `input/<대상자>/` 에서 읽고 출력을 `output/<대상자>_manual/` 에 쓴다. 전사는 파일명 기준으로 캐시하고, 다른 단계는 다시 돌리면 산출물을 덮어쓰므로 손으로 고친 결과는 먼저 확인한다.

## 수동 제작

1. 설문, 녹음, 인터뷰 자료를 정리한 뒤 필요한 단계별 도구를 돌린다.
2. 대상자 화자, 사실, 말투, 기억을 검토하고 manual 폴더의 결과를 고친다.
3. 최종 프롬프트, 기억, 임베딩, voice ID 를 준비한 뒤 로컬 대화로 확인한다.
4. 확정한 결과를 별도 업로드 작업으로 운영 DB 에 반영한다.

돌아가신 분의 수동 제작 페르소나(옛 프롬프트 + 장기기억)는 묶음 JSON 으로 만들어 BE 관리자 화면 '페르소나 가져오기'(`/admin/import`)에 올린다. 묶음 모양의 정본은 BE `app/schemas/persona_import.py` 다.

```bash
python tools/export_manual_persona.py --src output/<대상자>_manual --voice-id-file <Fish 모델 id 파일> --out <git 레포 밖>/<대상자>_bundle.json
```

- 읽는 것: `s3_synthesis/c_system_prompt.txt`(줄바꿈만 LF), `s2_extraction/e_recording_memory.json`(장기기억 전부), `s3_synthesis/d_memory_embedding.json`(확인용 옛 벡터 3건 — 입력이 "제목: 본문" 인 옛 목록 모양일 때만. 지금 파이프라인의 `{"embeddings": …}` 는 읽되 싣지 않는다). 단기기억·설문 기억은 넣지 않는다.
- 지금 L1 공통 규칙(layered·비공개·고인·가족 있음)과 사망 정책별 규칙 줄, 짧은 연결 문단을 함께 싣는다. 지침은 BE 가 가져올 때 짠다(guarded·verbatim, 사망 정책·한마디, 지금 대화하는 사람). 옛 글에 사인을 묻는 질문의 답·예시가 있어도 연결 문단이 위 사망 규칙을 따르라고 짚는다(옛 글은 고치지 않는다).
- 중간계층 structured 조립기는 등록부에 사망 정책이 없으면 지시문이 이미 고른 사망 규칙 줄을 쓴다(`orchestrator/assembler.py` `adopt_death_rule`) — 서로 다른 사망 규칙 두 줄이 한 메시지에 남지 않게.
- 파이프라인을 부르지 않고 원본을 고치지 않는다. 묶음은 가족 자료라 git 레포 안에는 쓰지 않는다(도구가 막는다). 화면에는 건수·글자 수·sha·경로만 찍는다.

다른 단계는 다시 돌리면 산출물을 덮어쓸 수 있으니, 손으로 고친 결과를 확인한 뒤 돌린다.

## 사용 모델

| 기능 | 기본 모델 |
|---|---|
| 페르소나 응답·녹음 분석·기억 추출·전사 교정 | `gpt-6-luna` |
| 품질 평가 채점 | `gpt-5.5` |
| 녹음 전사와 화자 분리 | ElevenLabs Scribe `scribe_v2` |
| 대화 음성 전사 | OpenAI `whisper-1` |
| 목소리 기반 화자 식별 | SpeechBrain ECAPA-TDNN |
| 기억과 질문 임베딩 | `text-embedding-3-small`, 1536차원 |
| 목소리 만들기·음성 합성 | Fish Audio `drama-3` (예전 목소리: ElevenLabs `eleven_v4_turbo`·MiniMax) |

모델은 환경 변수로 바꾼다(기본값: `infra/setting.py`). 페르소나는 파인튜닝 대신 프롬프트와 검색한 기억으로 제어한다. Fish 합성 모델(`drama-3`)만은 코드에 고정한다(아래 '목소리').

## 목소리

새 목소리는 모두 Fish Audio 즉시 복제로 만들고 `drama-3` 로 합성한다. ElevenLabs·MiniMax 는 예전에 만든 목소리를 다시 만들기 전까지 합성하고, 다시 만든 뒤 지우는 데만 쓴다.

| 일 | 경로 | 하는 일 |
|---|---|---|
| 만들기 | `POST /v1/voice/clone` | BE 가 보낸 `provider` 와 무관하게 늘 Fish(`POST /model`, `train_mode=fast`, `visibility=private`). 그분 구간 600초 후보에서 깨끗한 60초를 골라 44.1kHz mono 16비트 wav 하나로 올린다. 제목에 짧은 꼬리를 붙여 다시 만들 때 겹치지 않게 한다. 응답 `state` 가 `trained` 가 아니면 30초쯤 다시 묻고, 그래도 아니면 지우고 실패로 답한다. 다시 보내지 않는다(목소리가 둘 생긴다) |
| 합성 | `POST /v1/voice/speech` | `voiceId` 앞글자로 회사를 고른다: `fish_…` → Fish, `TalkTo…` → MiniMax, 그 밖 → ElevenLabs(예전 IVC·PVC). Fish 에는 앞글자를 떼고 보낸다. 헤더 `model: drama-3`(빠지거나 틀리면 Fish 가 말없이 `s2.1-pro` 로 합성한다), mp3 44.1kHz 128kbps. 감정 표시와 `[ ]`·`< >`·`<\|…\|>` 지시 표시를 지우고, ElevenLabs 속도·결 값은 보내지 않는다. 40초 안에 끝내고(BE 는 45초), 429·503 이면 한 번만 다시 한다 |
| 지우기 | `POST /v1/voice/delete` | 앞글자가 먼저고, 앞글자로 모르면 `provider`, 그것도 없으면 ElevenLabs. Fish 는 `DELETE /model/{id}`(이미 없으면 넘어간다) |

Fish 가 막히면 503 과 코드로 답한다. 키 `VOICE_PROVIDER_KEY`(401·403), 잔액 `VOICE_PROVIDER_CREDITS`(402), 동시 요청 한도 `VOICE_PROVIDER_RATE_LIMIT`(429), 장애·시간 초과 `VOICE_PROVIDER_UNAVAILABLE`. OpenAI 의 `AI_PROVIDER_*` 와 따로 두어 BE 가 목소리용 관리자 알림을 띄운다. 회사 원문은 로그에만 남긴다.

환경 변수: `FISH_AUDIO_API_KEY`(필수), `ELEVENLABS_API_KEY`(녹음 전사와 예전 목소리), `MINIMAX_API_KEY`(예전 목소리). BE 의 `deploy.py` 는 `infra/setting.py` 에 `os.getenv` 로 글자 그대로 적힌 이름만 운영 비밀값으로 옮기므로, 키 이름을 변수로 만들지 않는다. `TTS_PROVIDER` 는 `fish` 가 기본이고 새 목소리 만들기에는 쓰이지 않는다.

## 테스트

```bash
python -m pytest test
```

외부 API 는 모두 모킹하며 `.env` 없이 돈다. 4층 프롬프트의 기본 구성 출력은 `test/pipeline/fixtures/layered_golden/` 골든으로, 공통 규칙 legacy 출력은 `test/pipeline/fixtures/common_rules_golden/` 으로 고정한다.

## 배포

운영은 AWS 서울이다. BE 레포(`talkto_api_server`)의 `deploy/aws/deploy.py server` 가 이 레포 소스를 묶어 서버에서 이미지를 만든다(`deploy/aws/README.md`).

```bash
docker build -t talkto-personaai-ai .
docker run --rm -p 8001:8001 -e PORT=8001 --env-file .env talkto-personaai-ai
```

- 운영(staging·production)에서는 `AI_SERVER_TOKEN` 이 없으면 서버가 뜨지 않는다. BE 와 같은 값을 넣는다.
- BE 에 보내는 요청 모양이 바뀌면 이 서버를 BE 보다 먼저 올린다. 이 서버는 모르는 필드를 거부해서, 순서가 뒤집히면 그 기능만 조용히 실패한다.
- 패키지 버전은 `requirements.lock` 으로 고정한다. 이미지를 만들 때 목소리 묶기(ECAPA) 모델을 실제로 한 번 돌려 보고, 깨졌으면 빌드가 실패한다.
- 이미지에 torch 가 들어가 첫 빌드는 오래 걸린다. 의존성이 그대로면 다음부터는 캐시로 빠르다.

## 로그 원칙

로그에는 작업 ID, 처리 단계, 상태, 지연 시간 같은 운영 정보만 남긴다. 사용자 원문, 전사문, 기억 내용, 음성, API 키, 토큰, 서명된 URL, 대상자 이름은 남기지 않는다. httpx·openai 라이브러리 로그는 경고부터만 남긴다.
