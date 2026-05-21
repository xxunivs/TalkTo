# AI <-> 백엔드 연동 업데이트 ver4

## AI 서버 토큰 설정

백엔드 최신 코드에서 `AI_SERVER_TOKEN`을 읽어 AI 서버 호출 시 `X-AI-Server-Token` 헤더로 보내는 것을 확인했다.

AI 서버도 이미 같은 토큰 검증 로직을 가지고 있다.

- `AI_SERVER_TOKEN`이 설정되지 않으면 기존처럼 토큰 없이 동작한다.
- `AI_SERVER_TOKEN`이 설정되면 `/ai/*` 요청은 `X-AI-Server-Token` 헤더가 있어야 한다.
- `GET /health`는 배포 상태 확인용이므로 토큰 없이 접근 가능하다.

백엔드와 AI 서버에는 같은 값을 넣어야 한다.

```env
AI_SERVER_TOKEN=백엔드와_AI서버가_공유할_긴_랜덤_토큰
```

백엔드 호출 헤더:

```http
X-AI-Server-Token: 백엔드와_AI서버가_공유할_긴_랜덤_토큰
```

적용 순서:

1. 백엔드 환경변수에 `AI_SERVER_TOKEN`을 넣는다.
2. Render AI 서버 환경변수에도 같은 `AI_SERVER_TOKEN`을 넣는다.
3. 백엔드와 Render AI 서버를 재시작/재배포한다.
4. 토큰 포함 상태로 `/ai/embed`, `/ai/chat`, `/ai/memory/extract` 호출을 확인한다.

실제 토큰 값은 GitHub 문서나 코드에 올리지 않고, 환경변수로만 공유한다.

