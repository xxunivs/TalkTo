"""4층 페르소나 프롬프트 — 재료(설문·인터뷰·통화 전사) → 페르소나 명세(spec) → 프롬프트.

    L1 공통 규칙        prompts/rules/ (layered 세트, 페르소나 이름 없음)
    L2 개인 정보·정책   기본 정보·성격·상황별 반응·주제 등급(사망 정책은 L1 모듈 변형으로)
    L3 관계·호칭        가족 표: 부르는 말·자기를 가리키는 말·말 높이·톤·걱정거리 (공개 페르소나면 방문자 안내)
    L4 개인 말투        설문 말투 + 전사 말투 카드 + 실제로 하던 말 + 실제 주고받기
층은 고정이 아니다. prompts/layers.json 이 상황(공개/비공개, 생존/사후, 재료 유무, 주고받기 위치)에 따라
켤 모듈을 정하고, 턴마다 바뀌는 절(지금 상대, 이미 한 말)은 런타임 모듈로 기억 뒤에 붙는다(layers.py).

생성기
    from_intake.spec_from_intake        G1 설문(IntakeContext) → spec            규칙, LLM 없음
    from_intake.merge_recording         전사 재료(d_recording_prompt) 로 빈칸 채우기
    from_transcript.style_from_segments G3 통화 전사 → 말투 카드·실제 주고받기   통계, LLM 없음
    from_interview.extract_candidates   G2 가족 인터뷰 txt → spec 후보          LLM(근거 줄 검증), 사람 확인 뒤 적용
    render.render_layered_prompt        spec → 프롬프트                         템플릿, LLM 없음
    layers.runtime_section              spec + 이번 턴 상황 → 런타임 절           템플릿, LLM 없음
칸이 겹칠 때: 사실·정책은 설문 > 인터뷰 > 전사, 말투 실측(카드·주고받기)은 전사만.
"""
