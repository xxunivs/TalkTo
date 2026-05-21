# AI 서버 연동 BE 개발 업데이트

## 추가 개발한 것

1. AI 서버 HTTP client 추가
   - `/ai/chat` 호출 계약 반영
   - `/ai/embed` 호출 계약 반영
   - `AI_SERVER_URL` 미설정 시 기존 local fallback 유지

2. 텍스트 대화 AI 연동 준비
   - conversation history를 AI 서버 `history` 형식으로 변환
   - BE에서 검색한 memories를 `/ai/chat`에 전달
   - AI 응답 `content`, `retrieved_memory_ids`, `latency_ms` 저장
   - `retrieved_memory_ids`는 BE `Memory` UUID 기준으로 필터링

3. 임베딩/메모리 검색 1차 구현
   - memory 생성/수정/reembed 시 `/ai/embed` 호출
   - `MemoryEmbedding.embedding`에 vector 저장
   - query embedding + cosine similarity 기반 검색 추가
   - 실패 시 기존 키워드 검색 fallback

4. 실패 로그 보강
   - AI chat 실패는 `LLM` 로그
   - embedding/query embedding 실패는 `MEMORY` 로그

## 앞으로 정해야 하는 것

1. AI 서버를 어디에 올릴지, BE에서 접근 가능한 `AI_SERVER_URL` 확정 필요
2. 로컬/스테이징/운영 환경별 `AI_SERVER_URL`, timeout 값 필요
3. 서버 간 인증이 필요한지 결정 필요
4. 기존 44개 장기기억을 BE DB로 import하는 방식 결정 필요
5. AI legacy ID와 BE `Memory` UUID 매핑 방식 결정 필요
6. 음성은 `/ai/voice-chat` 단일 호출로 갈지, STT -> chat -> TTS 분리로 갈지 결정 필요
7. TTS base64를 mp3 파일로 저장할 스토리지/S3 및 signed URL 정책 필요
8. MVP에서는 `float8[]` + TypeScript cosine similarity로 구현했는데, pgvector로 갈지 여부 결정 필요
9. 단기기억을 BE `Memory`로 저장할지, 저장한다면 `memoryType` 표준값 결정 필요

## BE 브랜치

- `feat/ai-server-contracts`

## GitHub issue

- [#1 백엔드 MVP 결정 필요 사항](https://github.com/MediLux-TalkTo/TalkTo_PersonaAI_BE/issues/1)
- [#2 MVP 미구현 항목](https://github.com/MediLux-TalkTo/TalkTo_PersonaAI_BE/issues/2)
