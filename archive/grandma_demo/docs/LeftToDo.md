# Left To Do

## 백엔드 DB 연동 완료 후 정리할 것

### 1. 백엔드 실연동 검증 전 로컬 기억 테스트 코드 유지 여부

현재 `/ai/chat`은 백엔드가 `memories`를 보내면 백엔드가 검색한 기억을 사용한다.

다만 백엔드가 아직 `memories`를 보내지 않거나, 백엔드 실연동 검증 전 AI 단독 성능을 테스트해야 하는 경우에는 AI 서버 내부의 `data/memories.json` + `data/memory_embeddings.json`으로 직접 장기기억을 검색한다.

현재 `/ai/chat`은 `PERSONA_ENABLE_LOCAL_SHORT_TERM=true`인 환경에서 답변 생성 후 `data/short_term_memories.json`에 단기기억을 자동 저장할 수 있다.

운영 Render 환경에서는 `PERSONA_ENABLE_LOCAL_SHORT_TERM=false`로 두고, 실제 단기기억 저장은 백엔드가 `/ai/memory/extract` 결과를 받아 DB에 저장하는 구조다.

백엔드 실연동 검증이 끝나기 전에는 AI 단독으로 장기기억 RAG와 이어지는 대화 성능을 테스트해야 하므로, 내부 RAG fallback과 로컬 단기기억 자동 저장 코드는 당장 제거하지 않는다.

나중에 백엔드 DB 기반 장기기억 검색과 단기기억 저장/검색이 완전히 안정되면 제거를 검토한다.

### 2. 백엔드 배포 서버와 실제 연동 테스트

프롬프트와 답변 품질 개선은 당분간 AI repo의 JSON 기반 eval로 계속 진행한다.

현재 eval은 `data/memories.json`과 `data/memory_embeddings.json`을 사용하므로, 백엔드 DB에 import된 기존 장기기억과 같은 원천 데이터 기준으로 답변 품질을 비교할 수 있다.

다만 실제 서비스 흐름은 백엔드 DB 검색, conversation history, assistant 메시지 저장, `/ai/memory/extract` 이후 단기기억 DB 저장까지 포함하므로 별도 연동 테스트가 필요하다.

백엔드 연동 테스트를 하려면 테스트용 로그인 계정/비밀번호를 받아서 다음 흐름을 확인한다.

1. `/api/v1/auth/login`으로 access token 발급
2. conversation 생성
3. 텍스트 메시지 전송
4. 백엔드가 DB에서 검색한 `memories`를 `/ai/chat`에 넘기는지 확인
5. assistant 답변이 DB에 저장되는지 확인
6. `/ai/memory/extract` 결과가 `SHORT_TERM` memory로 DB에 저장되는지 확인

### 3. 프롬프트 엔지니어링

답변 품질 개선을 위해 `prompts/system_prompt.py`를 중심으로 프롬프트를 계속 조정한다.

우선 백엔드 실연동과 별개로 AI repo의 JSON 기반 eval을 사용해서 단발 질문과 이어지는 대화 결과를 비교한다.

```bash
python -m evaluation.eval_run
```

확인할 내용은 다음과 같다.

1. 너무 짧게 끊기는 답변이 줄어드는지
2. 실제 장기기억을 더 자연스럽게 반영하는지
3. 할머니 말투가 과하게 설명적이거나 일반 챗봇처럼 변하지 않는지
4. 이어지는 대화에서 최근 맥락과 단기기억 후보가 자연스럽게 반영되는지
5. 파인튜닝 모델을 계속 쓸지, 기본 모델 + 프롬프트/RAG 중심으로 갈지 비교할 수 있는지

프롬프트를 수정하거나 `OPENAI_CHAT_MODEL`을 변경하면 Render AI 서버 재배포가 필요하다.

같은 Render 서비스 안에서 재배포하는 경우 AI 서버 URL은 유지되므로, 백엔드가 추가로 수정할 내용은 없다.

### 4. `AI_SERVER_TOKEN` 운영 적용

AI 서버는 `AI_SERVER_TOKEN`이 설정되면 `/ai/*` 요청에 `X-AI-Server-Token` 헤더를 요구한다.

백엔드 최신 코드도 `AI_SERVER_TOKEN`을 읽어서 같은 헤더로 보내도록 구현되어 있다.

AI repo 로컬 `.env`에는 공유용 토큰을 넣어둔 상태다.

이제 같은 토큰을 백엔드 환경변수와 Render AI 서버 환경변수에 넣고, 두 서버를 재시작/재배포한 뒤 호출 테스트를 해야 한다.

실제 토큰 값은 GitHub 문서나 코드에 올리지 않는다.
