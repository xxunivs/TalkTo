# TalkTo Persona AI

TalkTo 사후 Phase — 할머니 페르소나 AI 챗봇의 AI 파트

## 환경 세팅

### 1. 레포 클론
```bash
git clone https://github.com/MediLux-TalkTo/TalkTo_AI.git
cd TalkTo_AI
```

### 2. Python 가상환경 생성
```bash
python3.11 -m venv venv
source venv/bin/activate
pip install openai python-dotenv
```

### 3. .env 파일 생성
프로젝트 루트에 `.env` 파일을 만들고 API 키를 넣어주세요.
```
OPENAI_API_KEY=여기에_API_키_입력
```
※ API 키는 팀원에게 개별 전달받으세요.

### 4. 테스트 실행
```bash
source venv/bin/activate
python llm/test_api.py
```
GPT 응답이 출력되면 환경 세팅 완료!

## 코드 구조
```
PersonaAI/
├── llm/          GPT 호출 (history 받아서 답변 생성)
├── prompt/       시스템 프롬프트 조립, 감정/품질 가이드라인
├── memory/       장기/단기 기억, 임베딩, KG, RAG 검색
├── voice/        STT, TTS
├── monitoring/   사용 이벤트, 오류 로그, 피드백
├── data/         전사본, 파인튜닝 데이터, 페르소나 JSON
├── Timeline.md   전체 개발 타임라인
└── README.md
```

## 개발 단계

상세 내용은 [Timeline.md](Timeline.md) 참고.

| Stage | 내용 | 상태 |
|---|---|---|
| 1 | 최소 동작 챗봇 | |
| 2 | 프롬프팅 강화 | |
| 3 | 파인튜닝 | |
| 4 | 장기 기억 + RAG | |
| 5 | KG + 하이브리드 검색 | |
| 6 | 품질 보정 | |
| 7 | 음성 (STT/TTS) | |
| 8 | 단기 기억 | |
| 9 | 감정 엔진 | |
| 10 | 운영/모니터링 | |
| 11 | 테스트 후 조정 | |
