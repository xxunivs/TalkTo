# TalkTo

텔레그램 Userbot 기반 음성 전화 페르소나 구축 시스템.

10분마다 자동으로 사용자에게 텔레그램 음성 전화를 걸어 대화를 나누고, 대화 내용을 지식그래프(Cognee)로 축적해 페르소나를 구축한다.

## 아키텍처

```
[Scheduler] ─10m→ [Userbot (TDLib+tgvoip)] → 통화 연결
                            │
                            ▼
  ┌─────────────────────────────────────────────────┐
  │ In-Call Loop (Orchestrator)                     │
  │   질문 → TTS(Kokoro) → 재생 → 상대답변          │
  │   → STT(Whisper) → LLM(Qwen) 꼬리질문 → 다음    │
  └─────────────────────────────────────────────────┘
                            │ 통화 종료
                            ▼
  [Post-call Ingest] LLM 감정분석 + 분류 → Cognee
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
       Master Layer                Overlay Layer
       (안정 페르소나)              (맥락/일시 상태)
```

## 환경

- Ubuntu 미니 PC, CPU 전용, 16GB RAM
- Docker Compose 기반 (모든 서비스 컨테이너화)

## 구성 요소

| 컴포넌트 | 역할 | 모델/스택 |
|---|---|---|
| `userbot/` | 텔레그램 전화 발신/수신 | TDLib + tgvoip |
| `services/tts/` | 한국어 음성 합성 | Kokoro TTS |
| `services/stt/` | 한국어 음성 인식 | Faster-Whisper (small, int8, CPU) |
| `services/llm/` | 꼬리질문 + 감정분석 | Qwen |
| `orchestrator/` | 통화 중 루프 조율 | Python |
| `ingest/` | 통화 후 KG 저장 | Cognee |

## Quickstart

```bash
cp .env.example .env
# .env에 TELEGRAM_API_ID, API_HASH, PHONE_NUMBER 등 입력
scripts/download_models.sh
docker-compose up -d
python scripts/init_tdlib.py    # 최초 1회 텔레그램 로그인
```
