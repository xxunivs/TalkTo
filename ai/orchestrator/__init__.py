"""persona orchestrator — BE 와 LLM 사이 중간계층.

한 턴 = 세션 상태 → 기억 선택 → 사전 판정 → 프롬프트 조립 → 생성 → 사후 가드 → 턴 뒤 처리.
단계마다 입출력 계약(`contracts.py`)을 고정하고, 구현은 갈아 끼운다.
legacy 조립·판정 끔·가드 끔 구성은 기존 `/v1/persona/responses` 동작을 그대로 재현한다(PERSONA_ORCHESTRATOR=1 로 켬).
계약 설명: orchestrator/CONTRACT.md
"""
