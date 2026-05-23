# TalkTo Persona AI

TalkTo 사후 Phase — 할머니 페르소나 AI 챗봇의 AI 파트

## 환경 세팅

### 1. 레포 클론
```bash
git clone https://github.com/MediLux-TalkTo/TalkTo_PersonaAI_AI.git
cd TalkTo_PersonaAI_AI
```

### 2. Python 가상환경 생성
```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. .env 파일 생성
프로젝트 루트에 `.env` 파일을 만들고 API 키를 넣어주세요.
```
OPENAI_API_KEY=여기에_API_키_입력
OPENAI_CHAT_MODEL=gpt-4.1-mini
PERSONA_ENABLE_LOCAL_SHORT_TERM=true
AI_SERVER_TOKEN=로컬에서_필요할_때만_입력
ELEVENLABS_API_KEY=여기에_ELEVENLABS_키_입력
ELEVENLABS_VOICE_ID=여기에_voice_id_입력
```
※ API 키는 팀원에게 개별 전달받으세요.

### 4. 서버 실행
```bash
uvicorn server:app --port 8000
```

API 문서: `http://localhost:8000/docs`

### 5. 평가 실행
```bash
source venv/bin/activate
python -m evaluation.eval_run C
```
`evaluation`은 단발 질문 평가와 이어지는 대화 평가를 순서대로 실행합니다.
`C`는 현재 후보 프롬프트입니다. 기본모델+RAG, 파인튜닝모델+RAG, 초기 프로토타입을 같은 질문에서 비교합니다.
대화형 평가는 실제 단기기억 저장/조회 메커니즘을 사용하되, 평가용 임시 파일을 써서 운영 단기기억 JSON은 바꾸지 않습니다.
기본 출력은 답변 비교만 보여줍니다. RAG/평가포인트/단기기억 판정은 필요할 때 `--show-rag`, `--show-points`, `--show-memory` 옵션으로 확인하세요.
실행 결과는 `evaluation/results/`에 자동 저장됩니다.

## 코드 구조

상세 내용은 [docs/CODE_STRUCTURE.md](docs/CODE_STRUCTURE.md) 참고.

## Render 배포

Render Web Service로 배포합니다.

- Runtime: `Python 3`
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
- Health Check Path: `/health`

환경변수:

```env
OPENAI_API_KEY=...
OPENAI_CHAT_MODEL=ft:gpt-4.1-mini-2025-04-14:talkto::DeJqGFij
PERSONA_ENABLE_LOCAL_SHORT_TERM=false
AI_SERVER_TOKEN=백엔드와_공유할_긴_랜덤_토큰
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...
```

`OPENAI_CHAT_MODEL`만 바꾸면 기본 모델/파인튜닝 모델을 코드 수정 없이 교체할 수 있습니다.
운영 배포에서는 기억 저장소의 기준을 백엔드 DB로 두기 위해 `PERSONA_ENABLE_LOCAL_SHORT_TERM=false`를 사용합니다.
`AI_SERVER_TOKEN`을 설정하면 `/ai/*` 호출에는 `X-AI-Server-Token` 헤더가 필요합니다. `/health`는 토큰 없이 확인할 수 있습니다.

배포 후 나온 URL을 백엔드 환경변수에 넣습니다.

```env
AI_SERVER_URL=https://배포된-render-url
AI_SERVER_TIMEOUT_MS=45000
AI_SERVER_TOKEN=Render와_같은_값
```

## 개발 단계

상세 내용은 [docs/Timeline.md](docs/Timeline.md) 참고.

| Stage | 내용 | 상태 |
|---|---|---|
| 1 | 최소 동작 챗봇 | ✅ |
| 2 | 화자 분리 + 전사본 분석 | ✅ |
| 3 | 프롬프팅 강화 | ✅ |
| 4 | 파인튜닝 | ✅ |
| 5 | 장기기억 + RAG | ✅ |
| 6 | 음성 (STT/TTS) | ✅ |
| 7 | 단기기억 | ✅ |
| 8 | 프롬프트 조정 | |
