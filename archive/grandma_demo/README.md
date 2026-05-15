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
python -m prompt.eval_all C
```
`C`는 현재 후보 프롬프트입니다. 기본모델+RAG, 파인튜닝모델+RAG, 초기 프로토타입을 같은 질문에서 비교합니다.

## 코드 구조

상세 내용은 [CODE_STRUCTURE.md](CODE_STRUCTURE.md) 참고.

## 개발 단계

상세 내용은 [Timeline.md](Timeline.md) 참고.

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
