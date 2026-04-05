# TalkTo

텔레그램 봇을 통해 사용자와 대화하고, cognee 지식그래프로 페르소나를 구축하는 파이프라인.

## Setup

```bash
cp .env.example .env
# .env 파일 열어서 BOT_TOKEN 값 입력
```

## 주요 스크립트

- `bin/sync_to_cognee_univs.py` - 세션 로그 → cognee dataset_univs 자동 저장 (2분 cron)
- `bin/generate_question.py` - 지식그래프 기반 질문 생성 + 텔레그램 전송 (10분 cron)
- `bin/collect_replies.py` - 텔레그램 답변 수집 → cognee 저장 (2분 cron)
- `bin/view_knowledge_graph.py` - 지식그래프 CLI 조회
- `bin/update_knowledge.py` - 지식그래프 수동 업데이트
