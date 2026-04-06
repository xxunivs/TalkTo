# TalkTo

텔레그램 Bot API 기반 음성 메시지 페르소나 구축 시스템.

10분마다 자동으로 대상 사용자에게 텔레그램 음성 메시지를 보내 대화를 나누고, 대화 내용을 지식그래프(Cognee)로 축적해 페르소나를 구축한다.

## 아키텍처

```
[Scheduler] ─10m→ [Bot API] → 음성메시지 전송
                        │
                        ▼
  ┌─────────────────────────────────────────────────┐
  │ Conversation Loop (main.py)                     │
  │   질문 → TTS(Kokoro) → 음성메시지 전송           │
  │   → 답변 대기 → STT(Whisper) → LLM(Qwen) 꼬리질문│
  └─────────────────────────────────────────────────┘
                        │ 대화 종료
                        ▼
  [Post-call Ingest] LLM 감정분석 + 분류 → Cognee
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
       Master Layer          Overlay Layer
       (안정 페르소나)        (맥락/일시 상태)
```

## 환경

- Ubuntu 미니 PC, CPU 전용, 16GB RAM
- Docker Compose 기반 (모든 서비스 컨테이너화)

## 구성 요소

| 컴포넌트 | 역할 | 모델/스택 |
|---|---|---|
| `userbot/` | 텔레그램 음성 메시지 송수신 | python-telegram-bot (Bot API) |
| `services/tts/` | 한국어 음성 합성 | Kokoro TTS |
| `services/stt/` | 한국어 음성 인식 | Faster-Whisper (small, int8, CPU) |
| `services/llm/` | 꼬리질문 + 감정분석 | Qwen |
| `orchestrator/` | TTS/STT/LLM 파이프라인 | Python |
| `ingest/` | 대화 후 KG 저장 | Cognee |

## Quickstart

```bash
cp .env.example .env
# .env에 BOT_TOKEN, TARGET_CHAT_ID 입력
scripts/download_models.sh
docker-compose up -d
python scripts/init_bot.py    # 봇 토큰 검증 + chat_id 확인
```
