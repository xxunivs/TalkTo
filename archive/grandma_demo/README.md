# TalkTo Persona AI

TalkTo 사후 Phase — 할머니 페르소나 AI 챗봇의 AI 파트

## 환경 세팅

### 1. 레포 클론
```bash
git clone https://github.com/xxunivs/PersonaAI.git
cd PersonaAI
```

### 2. Python 가상환경 생성
```bash
python3.11 -m venv venv
source venv/bin/activate
pip install openai python-dotenv
```

### 3. .env 파일 생성
프로젝트 루트에 `.env` 파일을 만들고 API 키를 넣어주세요.
```
OPENAI_API_KEY=여기에_API_키_입력
```
※ API 키는 팀원에게 개별 전달받으세요. .env 파일은 GitHub에 올라가지 않습니다.

### 4. 테스트 실행
```bash
source venv/bin/activate
python stage1_test_api.py
```
GPT 응답이 출력되면 환경 세팅 완료!

## 파일 구조
```
PersonaAI/
├── .env                           ← API 키 (GitHub 미포함)
├── .gitignore
├── README.md
├── stage1_test_api.py             ← OpenAI API 연결 테스트
├── stage2_전사본분석_임시.md        ← 할머니 말투/성격/기억 분석
├── stage2_전사본_화자표시_임시.md    ← 화자 표시 (음성 화자분리 후 확정 예정)
└── venv/                          ← Python 가상환경 (GitHub 미포함)
```

## 개발 단계

### Stage 1: API 연결 확인 ✅
GPT한테 "안녕" 보내고 응답 오는지 확인.

### Stage 2: 전사본 분석 (임시)
할머니 통화 전사본(1,425줄)에서 말투, 성격, 기억 분석.
음성 화자분리 후 확정 예정.

### Stage 3: 프롬프팅 vs 파인튜닝 비교
방법 A(프롬프트로 말투 지시) vs 방법 B(전사본으로 파인튜닝) 비교.
같은 질문 던져보고 어느 쪽이 더 할머니다운지 판단.

### Stage 4: 페르소나 프로필 작성
할머니 인격 설정 파일(JSON) 작성.
metadata, personality, system_prompt, memories 4개 블록.

### Stage 5: 장기 기억 구축
전사본에서 기억 chunk 추출 → 임베딩 생성 → 지식 그래프(KG) 구축.
HippoRAG(PPR) 적용.

### Stage 6: 단기 기억 인프라
대화 중 새 정보 자동 저장 시스템.
MemoryBank 논문 적용 (strength, recall_count, 망각 곡선).

### Stage 7: RAG 검색 파이프라인
장기 기억(임베딩+KG) + 단기 기억(Generative Agents 통합 스코어링) 검색.
장기 top-3 + 단기 top-2 통합, RAG 캐시(45초 TTL).

### Stage 8: 프롬프트 빌더
시스템 프롬프트 + 장기 기억 + 단기 기억 + 대화 히스토리 6턴 + 답변 지침 자동 조립.

### Stage 9: LLM 응답 생성 + 품질 보정
단답 방지, 반복 방지(Jaccard ≥ 0.6), 할루시네이션 회피.
end-to-end: 질문 → 기억 검색 → 프롬프트 → 할머니다운 답변.

### Stage 10: 음성 처리 (STT/TTS)
STT(Whisper) + TTS(ElevenLabs, 할머니 음성 클로닝 완료).
voice-first 흐름: 음성 먼저 재생 → 텍스트 표시.

### Stage 11: 백그라운드 단기 기억 추출
매 대화 턴마다 ImportanceEvaluator(5차원) → 중요도 ≥ 6이면 요약 → 단기 기억 저장.
Reflection은 MVP에서 비활성화, 조건 충족 시 활성화.

### Stage 12: 감정 엔진
EmotionDetector(한국어 키워드) → EmotionTracker(EMA 안정화) → ToneMapper(LLM 톤 + TTS 설정 조절).

### Stage 13: 운영/모니터링
usage_events, error_logs 저장. 피드백 분류(기억 보강 큐, 윤리/UX 큐).
단기 기억 망각 배치(일 1회). 개발자 통계 페이지 API.

### Stage 14: 테스트 후 평가/조정
Reflection 활성화 검토, 검색 가중치 조정, 추출 임계값 조정.
