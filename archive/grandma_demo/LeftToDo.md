# Left To Do

## 백엔드 DB 연동 완료 후 정리할 것

### 1. 백엔드 DB 연동 전 로컬 기억 테스트 코드 유지 여부

현재 `/ai/chat`은 백엔드가 `memories`를 보내면 백엔드가 검색한 기억을 사용한다.

다만 백엔드가 아직 `memories`를 보내지 않거나, 백엔드 DB 구축 전 AI 단독 성능을 테스트해야 하는 경우에는 AI 서버 내부의 `data/memories.json` + `data/memory_embeddings.json`으로 직접 장기기억을 검색한다.

현재 `/ai/chat`은 `PERSONA_ENABLE_LOCAL_SHORT_TERM=true`인 환경에서 답변 생성 후 `data/short_term_memories.json`에 단기기억을 자동 저장할 수 있다.

운영 Render 환경에서는 `PERSONA_ENABLE_LOCAL_SHORT_TERM=false`로 두고, 실제 단기기억 저장은 백엔드가 `/ai/memory/extract` 결과를 받아 DB에 저장하는 구조다.

백엔드 DB 구축 전에는 AI 단독으로 장기기억 RAG와 이어지는 대화 성능을 테스트해야 하므로, 내부 RAG fallback과 로컬 단기기억 자동 저장 코드는 당장 제거하지 않는다.

나중에 백엔드 DB 기반 장기기억 검색과 단기기억 저장/검색이 완전히 안정되면 제거를 검토한다.

### 2. `AI_SERVER_TOKEN` 운영 적용

AI 서버는 `AI_SERVER_TOKEN`이 설정되면 `/ai/*` 요청에 `X-AI-Server-Token` 헤더를 요구한다.

백엔드 최신 코드도 `AI_SERVER_TOKEN`을 읽어서 같은 헤더로 보내도록 구현되어 있다.

AI repo 로컬 `.env`에는 공유용 토큰을 넣어둔 상태다.

이제 같은 토큰을 백엔드 환경변수와 Render AI 서버 환경변수에 넣고, 두 서버를 재시작/재배포한 뒤 호출 테스트를 해야 한다.

실제 토큰 값은 GitHub 문서나 코드에 올리지 않는다.
