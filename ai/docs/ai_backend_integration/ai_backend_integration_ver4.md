# AI <-> 백엔드 연동 요청 ver4 — 채팅 기억 추출(memory-candidates)

ver3의 페르소나 서빙에 이어, 채팅 중 새 기억을 뽑는 `memory-candidates` 엔드포인트를 정리한다. BE 기존 memory-extract 연동과 대부분 정렬되고, 응답 형태만 달라진다.

## 1. [요청] 채팅 기억 추출 — `POST /v1/persona/memory-candidates`

채팅 한 턴(사용자·페르소나 주고받음)에서 앞으로 기억할 만한 새 사실을 뽑아 돌려준다. 저장 여부·무엇을 저장할지는 BE가 판단한다(AI는 stateless — 저장하지 않는다).

```json
// 요청
{
  "userMessage": "할머니 나 이번에 취직했어!",
  "assistantMessage": "아이고 잘됐다, 밥 잘 챙겨 먹고 다녀라.",
  "history": [ { "role": "user", "content": "..." } ]
}
// 응답
{
  "candidates": [
    { "summary": "사용자가 취직해 다음 주부터 회사에 다닌다.",
      "category": "직장", "importance": 9, "confidence": 0.99 }
  ],
  "provider": "openai",
  "model": "gpt-5.4-mini"
}
```

- 근황·변화(취직·이사·결혼·건강 등)만 뽑고, 인사·감정·일시적 상태는 제외한다. 없으면 빈 배열.
- `importance`(1~10)·`confidence`(0~1)는 AI가 매기고, **저장 임계·중복 판단은 BE**가 한다.

기존 `/ai/memory/extract` 연동과의 관계:
- **요청은 정렬** — BE의 `{history, user_message, assistant_message}`와 동일(카멜케이스만 맞추면 됨).
- **응답 형태가 다름 (부탁)** — BE 기존 응답은 단건 `{saved, importance, memory_type, category, summary, reason}`인데, 새 AI는 **후보 목록** `{candidates: [...]}`를 준다. 한 턴에서 새 사실이 여럿 나올 수 있고, **"저장할지"는 AI가 아니라 BE가 정하는** 게 stateless 원칙에 맞기 때문이다(그래서 AI는 `saved` 플래그를 주지 않는다).
- BE는 후보 목록을 받아 `importance`/`confidence`로 저장 여부·중복을 판단하면 된다. 기존 단건 처리 코드에 "목록 순회 + 저장 결정"만 더하는 변경이라, ver3의 4·5(채팅 서빙 전환)와 같은 시점에 함께 반영해주면 된다.
