# orchestrator — 모듈 계약

BE 와 LLM 사이의 중간계층. `/v1/persona/responses` 의 요청·응답 모양은 그대로 두고, 안에서 한 턴을 단계로 나눠 처리한다. 모든 단계는 환경 변수로 켜며, 기본 구성(legacy 조립 · 판정 없음 · 가드 없음)은 기존 `generate_persona_response` 와 같은 메시지·같은 응답을 낸다(`test/orchestrator` 동일성 테스트).

## 한 턴의 흐름

```
PersonaResponseRequest ─turn_from_request→ TurnInput
  ① SessionStore.load        → SessionState
  ② MemorySelector.select    → MemorySelection   (MemoryStore 에서 읽음)
  ③ Precheck.check           → PrecheckResult
  ④ Assembler.assemble       → PromptBundle(sections)
  ⑤ LLM.generate             → Generation
     VoiceMarks.split        → VoiceRun   (FISH_EMOTION 이 켜진 Fish 목소리일 때만, 가드 앞에서 음성 표시 떼기)
  ⑥ Guard.apply              → GuardResult
  ⑦ PostTurn.after           → SessionState(다음) → SessionStore.save
     VoiceMarks.finish       → (emotion, speech_text)
→ TurnResult → PersonaResponseResult
```

순서는 `pipeline.PersonaOrchestrator` 만 안다. 단계는 서로를 부르지 않고, `contracts.py` 의 계약 모델만 주고받는다. 모든 구현은 `impl` 이름을 가지며 추적 로그(`ORCHESTRATOR_TRACE_DIR`)와 실험 조건 이름으로 쓴다. 계약 필드를 바꾸면 이 문서와 `test/orchestrator` 를 같이 고친다.

## 단계와 구현

| 단계 | 입력 → 출력 | 구현(플래그) |
|---|---|---|
| ① 세션 | TurnInput → SessionState(turn_index, speaker, recent_turns, counters, delta_memories) | `InMemorySessionStore` — 프로세스 메모리. 워커가 여럿이면 세션이 끊긴다 |
| 기억 원본 | TurnInput → list[MemoryItem] | `RequestMemoryStore`(BE 가 보낸 기억) / `ExportFileMemoryStore`(DB export, 오프라인 재현용) |
| ② 선택 | → MemorySelection(memories, delta, strategy) | `PassThroughSelector`(기본) / `TieredSelector`(`ORCHESTRATOR_SELECTOR=tiered`) / 제한 기억 빼기 `RestrictedSelector`(structured 일 때) |
| ③ 사전 판정 | → PrecheckResult(known, coverage, topic_terms, directive) | `NoopPrecheck`(기본) / `CoveragePrecheck`(`coverage`) / `CoveragePrecheckV11`(`coverage_v11`) |
| ④ 조립 | → PromptBundle(sections: name·role·content) | `LegacyAssembler`(기본) / `StructuredAssembler`(`ORCHESTRATOR_ASSEMBLER=structured`) |
| ⑤ 생성 | messages → Generation(text, model, usage) | `OpenAIChatLLM`(비스트리밍) / `FakeLLM`(테스트) |
| 음성 표시 | Generation.text → VoiceRun(guard_text, 감정·연기 표시) | `VoiceMarks`(`FISH_EMOTION` = off · cue · inline, 기본 off 면 아무것도 안 함). 아래 "음성 감정" |
| ⑥ 가드 | → GuardResult(text, action, reasons, cited_memory_ids, counter_delta) | `PassGuard`(기본) / `GuardV1`(`v1`) / `GuardV11`(`v11`), 그 뒤에 `LanguageGuard`(`ORCHESTRATOR_LANG_GUARD=flag|fix`) |
| ⑦ 턴 뒤 | → SessionState | `RecordTurn`(최근 8턴, 카운터 반영) |

`retrieved_memory_ids` 는 가드가 근거 헤더로 인용 기억을 확인하면 그것만, 아니면 넣은 기억 전부다.

## 페르소나 등록부 (`ORCHESTRATOR_PERSONA_DIR`)

`<dir>/<이름>/` 폴더 하나가 페르소나 하나다. 개인 정보라 git 밖(`personas_local/`)에 둔다.

| 파일 | 내용 |
|---|---|
| `profile.json` | `{"subject_ids": [...], "match_name": "...", "public": false, "deceased": true, "has_family": true, "self_label": "할머니", "max_shots": 30, "death_talk": null}` |
| `spec.json` | 4층 생성기의 페르소나 명세(`python -m pipeline.persona_prompt build` 의 persona_spec.json). 있으면 런타임 절을 가족 표로 만들고, 말투 카드·주고받기 파일이 없을 때 명세의 것을 쓴다 |
| `style_card.md`, `shots.jsonl` | 말투 카드, 실제 주고받기(`{"user", "persona"}` 한 줄씩) |
| `intake.json` | s0 설문 산출물(b_intake_prompt.json). 사망·임종 정책과 발화 분해 사전을 여기서 정한다 |
| `restricted.json` | 사인·임종 경위 같은 제한 정보(형식은 `restricted.py` 머리말) |

등록이 없으면 지시문 문장으로 공개·고인·가족 여부를 추정하고, 그것도 안 되면 비공개·생존·가족 있음으로 본다. 찾는 순서: `subject_id` → 지시문 첫 문장의 이름이 `match_name` 과 같은 것.

## 조립 (StructuredAssembler)

```
[system] 공통 규칙 + 페르소나 지시문(공통 규칙과 같은 줄·빈 제목 제거) + 말투 카드 + 실제 주고받기(system 모드, 지시문에 이미 있으면 생략)
[system] 음성 지침(FISH_EMOTION=cue·inline 이고 Fish 목소리일 때만)
[system] 참고 기억 → [system] 이번 통화에서 새로 들은 것(델타)
[user/assistant …] 실제 주고받기(ORCHESTRATOR_SHOTS=messages)
[system] 사전 판정 지시 → [system] 지금 상황(런타임 절)
[history …] → [system] 말투 덧붙임(STYLE_LATE_NOTE, 범위 안일 때만) → [user] 발화
```

정적인 것을 앞에 둬 프롬프트 캐시를 살린다.

- `ORCHESTRATOR_RULES=legacy`: 공통 규칙은 legacy 세트, 사인 정책은 별도 절(`restricted.brief_rule`), 지금 상황은 "지금 말하는 사람: X" 한 줄.
- `ORCHESTRATOR_RULES=layered`: 공통 규칙은 `prompts/rules` layered 세트이고, 사인 정책은 사망 모듈 변형 하나로 들어간다(별도 절 없음). 등록부에 `spec.json` 이 있으면 지금 상황은 `prompts/layers.json` 의 런타임 모듈로 만든다.
  - 화자를 알면: 가족 표의 그 사람 줄(부르는 말·자기를 가리키는 말·말 높이)을 다시 짚는다. 화자 힌트가 관계말("손녀")이면 그 관계인 사람이 한 명일 때만 그 줄을 쓰고, 여럿이면 확인하라고 한다. 표에 없는 사람이면 아는 척하지 말라고 한다.
  - 화자를 모르면(비공개·가족 있음): 호칭을 단정하지 말고 확인하라고 한다.
  - 대화가 길어지면(앞 대화 6개 이상): 이미 한 챙김·'사랑해'만 짚어 반복하지 않게 한다.
  - 등록부 없이(추정 프로필) 4층 지시문(L1 표지 `# 말하는 형식`·`# 기억을 쓰는 법`)이 오면 공통 규칙을 앞에 다시 붙이지 않고 지시문을 그대로 첫 system 으로 쓴다. 만들 때 정한 사망 정책이 한 벌만 남고, 추정한 가족 유무로 다른 줄이 어긋나지 않는다(운영: 등록부 없음).
- 말투 덧붙임(`STYLE_LATE_NOTE=on`, 기본): 첫 system 에 `# 말투 카드` 와 짧은 말투 줄(`- 기본은 1~2문장으로 짧게.`)이 함께 있으면 마지막 발화 바로 앞에 말투 덧붙임 system 을 넣는다. legacy 조립기와 기존 경로(`generate_persona_response`)도 같은 자리에 넣고, 가져온 지침(`fixedPrompt`)에는 넣지 않는다. 덧붙임 예시가 반말이라 지금 상대에게 존댓말을 쓰는 지시문(가족 표 듣는 사람 줄이 존댓말, 공개 본인 페르소나의 `# 방문자`)에도 넣지 않는다.
- 참고 기억 블록(`build_memory_block`: 기존 경로·legacy 조립기·header 없는 structured)은 기억 id 순으로 고정해 넣는다(BE 가 보낸 순서와 무관 — 같은 기억이면 같은 글이라 프롬프트 캐시가 유지된다). guard v1/v11 의 (m번호) 블록(`header_block`)은 번호가 `parse_header` 와 맞아야 해서 선택 순서 그대로다. 기억이 `MEMORY_FULL_MAX`(400)보다 많으면 경고 로그와 trace `flags.memory_over_full_max` 만 남기고 자르지 않는다.

## 사망·임종 정책

- 설문 M9-01 "그분의 사망·장례·임종" 등급: 금지→deny / 짧게 넘김→brief / 물으면만→ask_only / 자유→open. T1-02 "말하고 싶지 않음"이면 등급과 무관하게 deny. 정책은 `intake_policy.py` 가 한 곳에서 정한다.
- 등록부에서는 profile.json `death_talk` 가 설문보다 우선한다(false → deny, true → deny 였으면 ask_only). 설문(intake.json)이 없고 spec.json 이 있으면 명세의 정책을 쓴다.
- 지시문을 만드는 `/v1/persona/assembly` 에서는 요청 `deathTalk` > `PERSONA_DEATH_TALK` > 설문 순이다.
- 답 문장: restricted.json `brief_answer` 우선, 없으면 T1-02 분류별 기본 문장. 사실(병명·날짜 등)은 프롬프트로 보내지 않는다.
- `RestrictedFacts.policy_source` 에 어디서 정해졌는지 남긴다.

## 제한 정보 (restricted.json)

structured 경로에서만 적용한다.
- `memory_ids`·`leak_terms`: 해당 기억을 프롬프트·사전 판정에서 뺀다(`RestrictedSelector`). 가드는 `leak_terms` 가 든 문장을 출력에서 지운다.
- `replace`: 지시문의 문장을 바꾸거나 지운다. 못 찾은 문장이 있으면 손으로 쓴 지시문이 바뀐 신호라 경고 로그를 남긴다. 4층 생성기가 만든 지시문에는 그 문장이 애초에 없으므로 조용히 넘어간다.

## 기억 선택 (TieredSelector)

장기 기억은 받은 순서 그대로 전부(프롬프트 앞쪽에 고정돼 캐시된다), 단기 기억은 발화 분해 단서의 임베딩으로 top-k(`ORCHESTRATOR_SELECTOR_ST_TOPK`, 코사인 임계 `ORCHESTRATOR_SELECTOR_THRESHOLD`)를 고른다. BE 가 기억마다 `memoryType`(`LONG_TERM` | `SHORT_TERM`)을 보내야 효과가 있다. 안 보내면 전부 장기로 보고 받은 그대로 넘긴다. 단서가 비면(인사·호칭·군말뿐) 단기 검색을 건너뛰고, 임베딩이 실패하면 받은 순서 앞에서 k 개로 대신한다.

## 사전 판정

질문일 때만 판정한다. 화제어(형태소) 가 문서(선택된 기억 + 델타 + 지시문의 '기본 정보' 절) 하나에 얼마나 모였는지(최대 커버율)를 보고 임계(기본 0.6) 미만이면 "모른다·되묻기" 지시를 system 절로 넣는다. 동의어는 `prompts/topic_synonyms.json`. v1.1 은 이름 조각 합치기, 부분 문자열 일치, 가벼운 말·부르는 말 빼기, 발화 분해 단서를 더한다. `pip install '.[orchestrator]'`(kiwipiepy)가 필요하다.

한계: 낱말 겹침으로 보므로 구어체 질문("몇 숟갈 넣어?")과 기억 문장의 표현이 다르면 근거가 있어도 막는다. 막는 비율은 기억 검색·표현 정규화와 같이 줄여야 한다.

## 사후 가드

`ORCHESTRATOR_GUARD=v1|v11`. structured 조립과 같이 켜면 기억 블록이 `(m번호)` id 블록으로 바뀌고 첫 줄 근거 헤더(`[근거: m3,m10]`)를 요구한다. legacy 조립이면 헤더 없이 넣은 기억 전부를 근거로 본다.

순서: 헤더 떼기 → 누출(leak_terms 문장 삭제, 다 지워지면 brief_answer) → 지어내기(질문일 때만: 근거 없음·없는 id / 인용 기억이 질문 화제어를 못 덮음 / 근거에 없는 숫자·고유명사) → 애정(가족이 먼저 사랑·보고 싶다를 말했거나 마무리 인사일 때만, 세션당 1번) → 챙김(세션당 2번). 연속 되묻기는 짧은 '모름' 문구로 바꾼다. action: pass / trim / ask_back / redact.

언어 누출 가드(`ORCHESTRATOR_LANG_GUARD`)는 그 뒤에 붙는다. 한글이 아닌 글자를 일괄로 막지 않고, 이번 턴 맥락(가족 말·지시문·넣은 기억·앞 대화)에 없던 문자 체계, 맥락에 없던 영어 낱말, "Wait"·"The user" 같은 추론 문장만 본다. 가족이 영어로 말하는 중이면 영어 답은 통과한다. `flag` 는 이유만 기록, `fix` 는 걸린 문장을 지우고 다 지워지면 되묻는다.

## 음성 감정 (FISH_EMOTION, `orchestrator/voice.py`)

Fish 목소리 대답에만 쓴다. 대답 LLM 이 같은 호출에서 정한다(LLM 을 더 부르지 않는다). 기본 off 면 메시지·응답이 예전과 같다.

- `cue`: 대답 맨 앞 `[감정:이름]` 하나(neutral, calm, warm, happy, sad, surprised, worried) → `emotion`. 고인(모르면 고인)은 sad → calm. BE 가 읽기 요청에 감정을 실으면 Fish 가 한국어 연기 표시(`fish.FISH_CUES`)를 맨 앞에 붙인다. 글에 섞여 나온 다른 `[ ]` 표시(`[울면서]` 등)는 inline 과 같이 지운다.
- `inline`: 대답 안 정해진 연기 표시(`fish.FISH_INLINE_CUES`)를 많아야 2개, 꾸미는 말 바로 앞에. `content` 는 표시 없는 글, `speechText` 는 같은 글에 표시만 남긴 것(목록 밖 표시·맨 끝 표시는 버린다).
- 조립: 두 조립기 모두 첫 system 바로 뒤에 음성 지침 system 하나(지시문 글은 그대로라 말투 덧붙임·존댓말 판정이 바뀌지 않는다). legacy 조립은 기존 경로와 같은 자리다.
- 떼기: 생성 바로 뒤, 가드 앞. 가드·언어 가드·턴 뒤 처리(세션 최근 턴)는 표시 없는 글만 본다. 근거 헤더(`[근거: …]`)는 표시가 앞에 와도 맨 앞에 두어 가드가 읽는다. `TurnRun.generation` 에는 모델 글이 그대로 남고, 추적에는 `voice` 단계(방식·감정·표시 이름)가 붙는다.
- 가드 뒤: 연기 표시는 가드가 남긴 글의 같은 자리에 다시 꽂는다(지운 문장의 표시는 빠진다). 가드가 답을 갈아 끼웠으면(`ask_back`·`redact`) 감정·표시를 버린다.
- MiniMax 감정(`VOICE_EMOTION`)은 기존 경로에만 있다. 중간계층에서는 지금처럼 없다.

## BE 요청에서 받을 수 있는 필드(선택)

`sessionId`, `speakerHint` — 없으면 None. BE 가 보내기 시작하면 세션 상태가 이어진다. `memories[].memoryType` — 없으면 장기로 본다. `persona.deceased` — `TurnInput.deceased` 로 옮겨 음성 감정 안전선에만 쓴다.

응답의 `emotion`·`speechText` 는 기존 경로와 같은 뜻이다. `speechText` 는 있을 때만 칸이 생긴다.

## 모듈 하나만 돌려 보기

```
python -m orchestrator <fixture.jsonl> --until assemble            # LLM 없이 조립까지
python -m orchestrator <fixture.jsonl> --mock                      # 끝까지, 고정 답
python -m orchestrator <fixture.jsonl> --memories <memories.json>  # BE 기억 대신 export 전부
```

fixture 한 줄 = `{"id", "turn": TurnInput, "state": SessionState(선택)}`. `ORCHESTRATOR_TRACE_DIR` 로 쌓인 턴 로그의 `turn`·`state` 를 그대로 fixture 로 쓸 수 있다(대화 원문이 들어 있으니 git 밖에 둔다).
