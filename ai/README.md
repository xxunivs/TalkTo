# TalkTo_PersonaAI_AI

TalkTo 페르소나를 만들고 대화하게 하는 AI 서버와 파이프라인입니다.

```
프론트(TalkTo_PersonaAI_FE) → talkto_api_server → TalkTo_PersonaAI_AI (여기)
```

BE 가 페르소나 프롬프트·기억·목소리 아이디를 요청에 실어 보내면, 이 서버는 받은 대로 답을 만들어 돌려줍니다. 누구의 페르소나인지는 모릅니다. 인증, DB 저장, 동의 판단은 BE 가 합니다.

## 두 가지로 쓴다

**서버**: BE 가 부르는 API 입니다. 대화 응답, 음성 합성, 녹음 전사·분석, 대상자 화자 추천(`/v1/analysis/subject-speaker`), 녹음 사이 같은 목소리 묶기(`/v1/analysis/voice-groups`, ECAPA), 기억 임베딩을 합니다.

**파이프라인**: 녹음과 설문으로 페르소나를 만드는 단계들입니다. 로컬에서 대상자 폴더 단위로 돌립니다.

```
s0 설문    작성본을 읽어 프롬프트용과 기억용으로 가른다
s1 전사    음성을 글자로, 누가 대상자인지 판별, 고유명사 교정
s2 추출    인물·민감도·말투·기억·요약을 뽑는다
s3 종합    기억을 모으고 통찰을 뽑고 프롬프트를 조립하고 임베딩한다
s4 음성    목소리를 복제한다
s5 대화    로컬에서 페르소나와 대화해본다
```

## 실행

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # 값 채우기
uvicorn main:app --reload --port 8001   # BE 가 8000 을 쓴다
```

API 문서는 `http://localhost:8000/docs` 에 있습니다.

화자 식별(ECAPA)까지 쓰려면 `pip install -e ".[dev,speaker]"` 로 torch 를 같이 깝니다.

## 파이프라인 돌리기

```bash
python -m pipeline.s0_intake.b_split_intake --subject 신금자 --write   # 설문에서 뽑기
python -m pipeline.run_pipeline --subject 신금자                      # s1~s3 전체
python -m pipeline.run_pipeline --subject 신금자 --only s2_e          # 한 단계만
python -m pipeline.run_pipeline --subject 신금자 --from s2 --to s3    # 구간만
python -m pipeline.s5_chat.a_chat --subject 신금자 --tts              # 대화
```

전사는 이미 전사한 녹음을 건너뛰어, 다시 돌려도 유료 API 를 부르지 않습니다.

## 폴더

```
infra/              서버 기반. 설정, 인증, 스키마, BE 가 부르는 라우트, 외부 API 통로
pipeline/           단계별 도구. s0_intake ~ s5_chat
input/<대상자>/      사람이 넣는 자료. 음성, Persona Intake 작성본, 손으로 쓴 자료
output/<대상자>/     단계별 산출물. <대상자>_manual/ 은 손으로 채운 판
docs/               Persona Intake 양식과 문항분류, 인계사항
test/               자동 테스트와 품질 평가
외부데이터/          일반화 검증용 외부 녹음
```

도구 이름과 산출물 이름을 맞춰 두었습니다. `pipeline/s2_extraction/c_extract_linguistic_style.py` 가 `output/<대상자>/s2_extraction/c_recording_linguistic_style.json` 을 냅니다.

## 테스트

```bash
python -m unittest discover -s test -t .
```

`-t .` 을 빼면 임포트가 깨집니다. 바깥 API 는 목킹돼 있어 돈이 들지 않습니다.

`test/evaluation` 은 실제 오디오와 유료 API 로 품질 지표를 내는 곳인데, 예전 경로를 임포트하고 있어 지금은 돌아가지 않습니다.

## 배포

Render 팀 계정의 `TalkTo_PersonaAI_AI` 서비스입니다. 설정은 `render.yaml` 에 있고, main 에 푸시하면 자동으로 올라갑니다.

```bash
docker build -t talkto-personaai-ai .
docker run --rm -p 8000:8000 --env-file .env talkto-personaai-ai
```

- 운영에서는 `AI_SERVER_TOKEN` 을 반드시 설정합니다. 비어 있으면 인증이 꺼집니다
- BE 에 보내는 요청 모양이 바뀌면 이 레포를 BE 보다 먼저 푸시합니다. 이 서버는 모르는 필드를 거부해서, 순서가 뒤집히면 그 기능만 조용히 실패합니다
- 이미지에 torch 가 들어가 첫 빌드가 20분 가까이 걸립니다. 의존성이 안 바뀌면 다음부터는 캐시로 빠릅니다

## 로그 원칙

로그에는 작업 ID, 처리 단계, 상태, 지연 시간 같은 운영 정보만 남깁니다. 사용자 원문, 전사문, 기억 내용, 음성, API 키, 토큰, 서명된 URL 은 남기지 않습니다.
