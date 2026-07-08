# TalkTo APP AI 배포 체크리스트

플랫폼 무관 컨테이너(`Dockerfile`) 기준. 호출 흐름은 `App → BE → APP_AI`이고, APP_AI는
stateless(DB·원본저장·인증판단 없음)라 컨테이너 1종 + 환경변수만으로 뜬다.

## 1. 필수 시크릿(환경변수)

| 변수 | 필수 | 비고 |
|---|---|---|
| `AI_SERVER_TOKEN` | **필수** | BE→AI 내부 인증. **미설정 시 인증이 꺼진다(무토큰 전면 허용)** — 반드시 설정 |
| `OPENAI_API_KEY` | **필수** | 분석·페르소나·임베딩 |
| `ELEVENLABS_API_KEY` | 필수(STT/TTS 사용 시) | 전사·음성합성 |
| `APP_ENV` | 권장 | `production` |
| `LOG_LEVEL` | 권장 | `INFO` |

나머지 모델·임계값은 `.env.example` 참고(기본값 있음). 모델은 전부 env로 교체 가능
(`OPENAI_CHAT_MODEL` 등) — 키·모델을 코드 수정 없이 바꾼다.

## 2. BE 연동 — 내부 인증 헤더

BE는 AI 호출 시 헤더 `X-AI-Server-Token: <AI_SERVER_TOKEN>`을 실어야 한다. 토큰 불일치는
401(`invalid_internal_token`).

## 3. 빌드·실행

```bash
docker build -t talkto-app-ai .
docker run --rm -p 8000:8000 --env-file .env talkto-app-ai
# 호스팅이 PORT를 주입하면 그 값 사용(기본 8000). 워커 수는 WEB_CONCURRENCY(기본 2).
```

## 4. 헬스체크

- `GET /health` — liveness(컨테이너 HEALTHCHECK도 이걸 씀)
- `GET /ready` — readiness

## 5. 이미지 특성

- Python 3.11-slim + base 의존성만. **torch/speechbrain 미포함**(화자식별은 현재 서빙
  경로에 미연결이라 제외 — 필요해지면 `.[speaker]`로 별도 이미지 구성).
- 비루트(uid 10001) 실행. stateless라 수평 확장 가능(워커·레플리카 자유).
- 비공개 데이터(`data/`)·평가(`evaluation/`)는 `.dockerignore`로 이미지에서 제외.

## 6. 배포 후 스모크

```bash
curl -s localhost:8000/health
curl -s -X POST localhost:8000/v1/persona/assembly \
  -H "X-AI-Server-Token: $AI_SERVER_TOKEN" -H "Content-Type: application/json" \
  -d '{"subjectContext":{"subject":{"name":"홍길동","addressTerm":"할머니"}},"speechExamples":[]}'
```

## 7. 플랫폼별 글루(선택)

- **Cloud Run / Render**: PORT 자동 주입 → 위 CMD 그대로. 시크릿은 콘솔/시크릿매니저로.
- **Fly.io**: `fly.toml`에 internal_port=8000, 헬스체크 `/health`.
- **VM + compose**: `docker-compose.yml`로 env_file + 포트 매핑.

(구체 플랫폼이 정해지면 해당 설정 파일을 추가한다.)
