# AI <-> 백엔드 연동 업데이트 ver4

## 1. 프롬프트 품질 확인용 백엔드 경로 테스트

프롬프트 조정은 우선 AI repo 내부 JSON 기반 eval로 빠르게 진행한다.

다만 내부 JSON RAG와 백엔드 DB RAG는 기억 원천은 같아도 검색 방식과 결과가 달라질 수 있다.

- AI 내부 eval: `data/memories.json` + `data/memory_embeddings.json` + AI 쪽 키워드 boost
- 백엔드 실서비스 경로: 백엔드 DB `memories` / `memory_embeddings` + TypeScript cosine similarity + keyword fallback

최종 답변 품질은 백엔드 API 경로에서도 확인하는 것이 좋다.

이를 위해 AI 쪽에는 백엔드 테스트용 로그인 계정이 필요하다.

해당 계정으로 백엔드 API 경로에서 실제 대화를 생성/전송해보고, 그 결과를 프롬프트 조정에 참고할 수 있다.

---

## 2. 백엔드 memory import 파일 경로 변경

AI 코드 구조 정리 과정에서 백엔드 memory import 관련 파일 경로와 이름이 변경되었다.

운영 DB에 이미 import가 완료된 상태라면 즉시 조치할 내용은 없다. 재import나 새 환경 세팅 시 새 경로를 참고하면 된다.

기존 경로:

```text
data/backend_memory_import.json
tools/export_backend_memories.py
tools/test_export_backend_memories.py
```

변경 경로:

```text
backend_memory/memory_import.json
backend_memory/build_memory_import.py
backend_memory/test_build_memory_import.py
```

새 GitHub Contents API 경로:

```text
https://api.github.com/repos/MediLux-TalkTo/TalkTo_PersonaAI_AI/contents/backend_memory/memory_import.json?ref=main
```

---

## 3. AI 서버 토큰 설정

백엔드와 AI 서버 기본 연동 테스트 전에는 토큰 없이 먼저 `/ai/chat`, `/ai/embed`, `/ai/memory/extract` 흐름을 확인한다.

실연동 검증이 끝난 뒤 백엔드 env와 Render AI 서버 env에 같은 `AI_SERVER_TOKEN`을 적용한다.

- `AI_SERVER_TOKEN`이 설정되면 `/ai/*` 요청은 `X-AI-Server-Token` 헤더가 있어야 한다.
- `GET /health`는 배포 상태 확인용이므로 토큰 없이 접근 가능하다.
- 실제 토큰 값은 GitHub 문서나 코드에 올리지 않고 환경변수로만 공유한다.
