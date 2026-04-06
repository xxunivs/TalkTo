# TalkTo

텔레그램 Bot API 기반 음성 메시지 페르소나 구축 시스템.

매일 밤 10시에 자동으로 대상 사용자에게 텔레그램 음성 메시지를 보내 대화를 나누고, 대화 내용을 지식그래프(Cognee)로 축적해 페르소나를 구축한다.

## 한 줄 요약

> 봇이 음성으로 질문 → 사용자가 답변 → AI가 꼬리질문 → 반복 → 지식그래프에 저장

## 전체 흐름

```
┌──────────────────────────────────────────────────────────────────┐
│ 매일 밤 10시 자동 실행                                                │
│                                                                  │
│  1. personas.yaml에서 랜덤 질문 선택                                │
│  2. Kokoro TTS로 질문을 한국어 음성으로 변환                         │
│  3. 텔레그램 봇이 음성 메시지로 전송                                  │
│  4. 사용자 답변 대기 (음성/텍스트 모두 가능, 2분 타임아웃)              │
│  5. 음성이면 Faster-Whisper로 텍스트 변환                            │
│  6. Cognee 지식그래프에 실시간 저장                                   │
│  7. Qwen LLM이 답변을 보고 꼬리질문 생성                             │
│  8. 2~7 반복 (최대 10턴, 무응답 2회 시 종료)                         │
│  9. 대화 종료 후 전체 transcript를 LLM이 감정분석 + 팩트 분류          │
│ 10. Master(안정 사실) / Overlay(일시 맥락) 레이어로 나눠서 저장        │
└──────────────────────────────────────────────────────────────────┘
```

## 아키텍처

```
[Scheduler] ─매일 22시→ [Bot API] → 음성메시지 전송
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

## 프로젝트 구조

```
TalkTo/
├── userbot/                    # 봇 본체 (텔레그램 음성 메시지 송수신)
│   ├── main.py                 #   엔트리포인트. 봇 시작, 스케줄러, 대화 루프
│   ├── call_controller.py      #   (레거시) P2P 통화 컨트롤러 — 현재 미사용
│   ├── Dockerfile              #   Python 3.11 + ffmpeg
│   └── requirements.txt        #   python-telegram-bot, httpx, pyyaml 등
│
├── services/                   # AI 서비스 (각각 독립 Docker 컨테이너)
│   ├── tts/                    #   음성 합성 (텍스트 → 음성)
│   │   ├── server.py           #     POST /synthesize → WAV 반환
│   │   ├── Dockerfile          #     kokoro-onnx 기반
│   │   └── requirements.txt
│   ├── stt/                    #   음성 인식 (음성 → 텍스트)
│   │   ├── server.py           #     POST /transcribe → {"text": "..."} 반환
│   │   ├── Dockerfile          #     Faster-Whisper small, CPU, int8
│   │   └── requirements.txt
│   └── llm/                    #   LLM (꼬리질문 생성 + 감정분석)
│       ├── server.py           #     POST /tail_question, POST /analyze_call
│       ├── prompts/            #     프롬프트 템플릿
│       │   ├── tail_question.txt       꼬리질문 생성 프롬프트
│       │   └── emotion_analysis.txt    감정분석 + 팩트 분류 프롬프트
│       ├── Dockerfile          #     llama-cpp-python (Qwen GGUF)
│       └── requirements.txt
│
├── orchestrator/               # 파이프라인 조율
│   ├── pipeline.py             #   speak(), transcribe(), next_question() — 서비스 HTTP 호출
│   ├── call_session.py         #   대화 1건의 상태 (transcript, 타임스탬프 등)
│   └── scheduler.py            #   N분마다 대화 트리거 (현재 main.py에서 직접 처리)
│
├── ingest/                     # 대화 후 지식그래프 저장
│   ├── cognee_client.py        #   Cognee API 인증 + 공통 호출 (login → Bearer token)
│   ├── cognee_master.py        #   Master 레이어 저장 (안정 사실: 이름, 직업, 가치관 등)
│   ├── cognee_overlay.py       #   Overlay 레이어 저장 (일시 맥락: 오늘 기분, 최근 이벤트 등)
│   └── post_call.py            #   대화 종료 후 LLM 분석 → Master/Overlay 분기 저장
│
├── config/
│   └── personas.yaml           # 초기 질문 풀 (봇이 대화를 시작할 때 랜덤 선택)
│
├── data/                       # 런타임 데이터 (gitignored)
│   ├── models/                 #   AI 모델 파일
│   │   ├── kokoro/             #     Kokoro ONNX 모델 (338MB)
│   │   ├── whisper/            #     Faster-Whisper small (464MB)
│   │   └── qwen/              #     Qwen 3B Q4_K_M GGUF (2GB)
│   ├── calls/                  #   대화별 transcript.json, analysis.json 저장
│   └── logs/                   #   런타임 로그
│
├── scripts/
│   ├── download_models.sh      # 모델 다운로드 (Whisper, Qwen, Kokoro)
│   ├── init_bot.py             # 봇 토큰 검증 + TARGET_CHAT_ID 확인
│   └── patch_cognee.sh         # cognee 컨테이너 패치 (재시작 시마다 실행 필요)
│
├── tests/
│   ├── test_pipeline.py        # 파이프라인 테스트 스텁
│   └── test_ingest.py          # ingest 테스트 스텁
│
├── docker-compose.yml          # 4개 서비스 정의 (tts, stt, llm, userbot)
├── .env.example                # 환경변수 템플릿
└── .gitignore
```

## 각 서비스 설명

### userbot (봇 본체)
텔레그램 Bot API를 통해 음성 메시지를 보내고 받는 핵심 서비스.
- `python-telegram-bot` 라이브러리 사용
- 매일 밤 10시에 자동으로 대화 시작
- 음성, 영상노트, 텍스트 답장 모두 수신 가능
- 매 턴마다 사용자 답변을 Cognee에 실시간 저장

### services/tts (Kokoro TTS)
텍스트를 한국어 음성(WAV)으로 변환하는 HTTP 서버.
- `POST /synthesize` — 텍스트 → 16-bit PCM WAV
- kokoro-onnx 기반 (CPU 전용, PortAudio 불필요)
- 포트: 8001

### services/stt (Faster-Whisper)
음성을 한국어 텍스트로 변환하는 HTTP 서버.
- `POST /transcribe` — WAV/OGG 업로드 → `{"text": "...", "duration": ...}`
- Whisper small 모델, int8 양자화, CPU 전용
- 포트: 8002

### services/llm (Qwen)
꼬리질문 생성과 대화 분석을 담당하는 HTTP 서버.
- `POST /tail_question` — 직전 답변 → 꼬리질문 1개
- `POST /analyze_call` — 전체 대화 → 감정/토픽/팩트 분류 (JSON)
- Qwen 3B Q4_K_M GGUF, llama-cpp-python, CPU 전용
- 포트: 8003

### ingest (Cognee 지식그래프)
대화 내용을 지식그래프로 저장하는 모듈. 기존 cognee-api (별도 Docker)를 HTTP로 호출.
- **Master 레이어**: 잘 안 변하는 사실 (이름, 직업, 가치관, 장기 목표)
- **Overlay 레이어**: 지금-여기 맥락 (오늘 기분, 최근 이벤트, 일시적 계획)
- Cognee가 Together AI (Qwen 235B)를 사용해 엔티티/관계 자동 추출

## 환경

- Ubuntu 미니 PC, CPU 전용, 16GB RAM
- Docker Compose 기반 (TalkTo 4개 서비스 + Cognee 3개 서비스)
- Python 3.11 (Docker), Python 3.12 (호스트)

## Quickstart

```bash
# 1. 환경변수 설정
cp .env.example .env
# .env에 BOT_TOKEN, TARGET_CHAT_ID 입력

# 2. 모델 다운로드 (~3GB)
./scripts/download_models.sh

# 3. Docker 이미지 빌드
docker compose build

# 4. 봇 토큰 검증 + TARGET_CHAT_ID 확인
export BOT_TOKEN=...
python3 scripts/init_bot.py
# 봇에게 /start 보낸 후 다시 실행하면 chat_id 출력

# 5. 서비스 시작
docker compose up -d

# 6. Cognee 패치 (cognee 컨테이너 재시작 시마다 필요)
./scripts/patch_cognee.sh
```

## 환경변수 (.env)

| 변수 | 설명 | 예시 |
|---|---|---|
| `BOT_TOKEN` | 텔레그램 봇 토큰 (BotFather 발급) | `123456:ABC...` |
| `TARGET_CHAT_ID` | 대화 상대 텔레그램 user ID | `8786432628` |
| `SCHEDULE_HOUR` | 대화 시작 시각 (0~23) | `22` |
| `MAX_TURNS` | 대화당 최대 턴 수 | `10` |
| `REPLY_TIMEOUT_SECONDS` | 답변 대기 시간 (초) | `120` |
| `COGNEE_USER` | Cognee 로그인 이메일 | `user@example.com` |
| `COGNEE_PASS` | Cognee 로그인 비밀번호 | `password` |

## Docker 서비스 구성

| 컨테이너 | 이미지 | 포트 | 역할 |
|---|---|---|---|
| `talkto-tts` | `talkto-tts` | 8001 | Kokoro TTS (한국어 음성 합성) |
| `talkto-stt` | `talkto-stt` | 8002 | Faster-Whisper (한국어 음성 인식) |
| `talkto-llm` | `talkto-llm` | 8003 | Qwen LLM (꼬리질문 + 감정분석) |
| `talkto-userbot` | `talkto-userbot` | — | 봇 본체 (대화 루프 + cognee 저장) |
| `cognee-api` | (기존) | 8000 | Cognee 지식그래프 API |
| `cognee-frontend` | (기존) | 3030 | Cognee 그래프 시각화 |

## 지식그래프 (Cognee)

Cognee가 대화 내용에서 자동으로 추출하는 것들:

- **엔티티**: 사람, 조직, 프로젝트, 감정, 활동, 이벤트, 개념
- **관계**: 엔티티 간 연결 ("합격했다", "느끼고 있다", "소속이다" 등)
- **분류**: Master(안정) vs Overlay(일시) 레이어 분리

예시:
```
[우주] ──합격──→ [AR 헬스케어 연구실]
[우주] ──느끼는──→ [취업에 대한 불안]
[우주] ──참여 중──→ [AR 프로젝트]
```

시각화: `http://<서버IP>:3030` (Cognee 프론트엔드)
