# HEARTBEAT.md - UnivsFirst_agent

## 사용자 식별 (중요)

- 이 에이전트의 사용자: **김우주** (우주)
- Telegram sender_id: `8786432628`
- Cognee dataset: `telegram-user-8786432628`
- **다른 사용자(윤재원/yoonjw 등)의 dataset을 절대 조회하거나 저장하지 말 것**

## 대화 후 지식 그래프 업데이트

최근 텔레그램 대화에서 우주가 공유한 개인 정보가 있으면 cognee에 저장:

```
cognee_univs_add_and_cognify(
  content="[날짜] 요약 내용",
  dataset_name="telegram-user-8786432628"
)
```

- 저장 대상: 관심사, 취미, 감정, 경험, 의견 등 페르소나 정보
- 저장 제외: 단순 인사, 명령, 확인 응답

## 주기적 체크

- 새로운 대화 내용 중 저장할 만한 게 있으면 cognee 업데이트
- 아무 것도 없으면 HEARTBEAT_OK
