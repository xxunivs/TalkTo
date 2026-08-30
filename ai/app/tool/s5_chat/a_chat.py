"""s5 대화 (로컬 테스트기) — 코드 안에서 페르소나와 실제로 대화해보는 harness.

아직 미구현 스텁. 배포 서빙은 BE(persona-runtime)가 담당하고, 여기서는 같은 로직을
로컬 파일 기준으로 복제해 대화 품질을 확인·튜닝할 예정이다.

구현 예정 (BE persona-runtime.service.ts 로직 복제, 파라미터 동일):
- 대화 루프: 사용자 입력 → 기억 검색 → 응답 생성 → 출력, 반복
- 기억 검색(신규): 질문 임베딩 → c_embeddings.json 유사도 top-k
  (top-k=5, 임계값=0.18, 후보 상한=500, 폴백=최근 5개)
- 응답: s3 b_persona.generate_persona_response 재사용
- tts/stt: s4 a_voice.synthesize_speech / transcribe_voice_message 재사용
- 안전어 정규식 게이트 복제
- 산출물: output/<대상자>/s5_chat/chat_YYMMDD_HHMM.json (대화 로그)
"""

# TODO(s5): 로컬 대화 harness 구현. 위 설계대로 BE persona-runtime 로직을 복제한다.
