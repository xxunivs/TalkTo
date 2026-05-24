# AI <-> 백엔드 연동 업데이트 ver4

## 1. 백엔드 memory import 파일 경로 및 payload 업데이트

AI 코드 구조 정리 과정에서 백엔드 memory import 관련 파일 경로와 이름이 변경되었다.

추가 인터뷰와 사후/추모 응답 원칙을 반영해 `data/memories.json`을 72개 기억으로 업데이트했고, 백엔드 import용 `backend_memory/memory_import.json`도 72개 기준으로 재생성했다.

기존 44개 기억을 기준으로 일부 내용을 최신화하고, 생애사·가족관계·천주교 신앙·사망/임종·할아버지 사후 근황·임종을 지키지 못한 가족의 죄책감 보듬기 등 신규 기억을 추가했다.

기억 태그는 `sensitive` 하나로 단순화했다.

`sensitive` 태그가 붙은 기억은 사용자가 해당 인물, 사건, 장소, 사망, 질병, 가족 사정을 직접 물었을 때만 AI가 답변에 조심스럽게 사용한다.

백엔드는 태그를 검색 정책으로 해석하지 않고 저장/전달만 해도 된다. 태그 사용 판단은 AI 프롬프트에서 처리한다.

백엔드가 `/ai/chat`에 검색된 기억을 넘길 때 `tags` 필드를 함께 보내주면 AI가 해당 태그를 프롬프트에 포함해 사용 여부를 판단할 수 있다. 현재 AI 서버의 `/ai/chat` 요청 모델은 `memories[].tags`를 받을 수 있도록 수정되어 있다.

예시:

```json
{
  "id": "memorial_guilt_comfort",
  "title": "임종을 지키지 못한 죄책감 보듬기",
  "content": "임종 자리에 함께 있지 못한 가족은 죄책감과 후회를 느낄 수 있다...",
  "tags": ["sensitive"]
}
```

`memorial_guilt_comfort` 기억은 사용자가 “마지막을 못 봐서 미안해”, “그때 갔어야 했는데”처럼 말할 때 사망 경위보다 죄책감 보듬기 응답이 먼저 나오도록 추가한 기억이다.

`confidenceScore`는 import payload에서 제외했다. 현재 장기기억은 수동 정리된 기준 데이터라 별도 신뢰도 점수를 사용하지 않는다.

기존 운영 DB에 이전 기억이 이미 import되어 있다면, 재import 시 중복 처리 또는 기존 기억 갱신 기준을 확인해야 한다.

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

## 2. AI 서버 토큰 설정

백엔드와 AI 서버 기본 연동 테스트 전에는 토큰 없이 먼저 `/ai/chat`, `/ai/embed`, `/ai/memory/extract` 흐름을 확인한다.

실연동 검증이 끝난 뒤 백엔드 env와 Render AI 서버 env에 같은 `AI_SERVER_TOKEN`을 적용한다.

- `AI_SERVER_TOKEN`이 설정되면 `/ai/*` 요청은 `X-AI-Server-Token` 헤더가 있어야 한다.
- `GET /health`는 배포 상태 확인용이므로 토큰 없이 접근 가능하다.
- 실제 토큰 값은 GitHub 문서나 코드에 올리지 않고 환경변수로만 공유한다.

---

## 3. 백엔드 연동 후 AI 수정 요청

연동 중 AI 서버 응답 형식, 엔드포인트, 프롬프트, RAG 입력 방식 등 AI 쪽 수정이 필요한 부분이 있으면 전달해주세요.
