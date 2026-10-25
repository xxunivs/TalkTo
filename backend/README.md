# TalkTo API Server

FastAPI backend for TalkTo. The initial API surface follows the supplied user API CSV and keeps
the domain centered on `people` so Voice Thread records, memories, personas, and conversations can
be connected to the same person.

## 로컬에서 전체 실행 (AI 서버 · API · 웹)

녹음 올리기 → 화자 고르기 → 핵심 질문 → 페르소나와 대화까지 로컬에서 끝까지 돌린다.
터미널 셋을 열어 순서대로 띄운다.

| 순서 | 폴더 | 명령 | 주소 |
|---|---|---|---|
| 1 | `TalkTo_PersonaAI_AI` | `.venv\Scripts\python -m uvicorn main:app --port 8001` | http://localhost:8001 |
| 2 | `talkto_api_server` | `.venv\Scripts\python -m uvicorn app.main:app --port 8000 --reload` | http://localhost:8000/docs |
| 3 | `TalkTo_PersonaAI_FE` | `flutter run -d chrome` | 자동으로 열린다 |

- AI 서버의 `.env` 에 `OPENAI_API_KEY`(분석·대화), `ELEVENLABS_API_KEY`(전사, 예전 목소리),
  `FISH_AUDIO_API_KEY`(목소리 만들기·대답 읽기)가 있어야 한다. 예전 MiniMax 목소리를 읽으려면 `MINIMAX_API_KEY` 도.
- 녹음 여러 개에서 같은 목소리를 묶으려면 AI 서버에 화자 식별 패키지가 있어야 한다 (`pip install -e ".[speaker]"`). 없으면 녹음별로만 나눈다.
- 이 서버는 ffmpeg 가 있어야 한다(영상에서 소리 뽑기, 미리듣기 클립). Docker 이미지에는 들어 있다.
- 이 서버의 `.env` 는 `.env.example` 을 복사해 쓴다. `AI_SERVER_URL=http://localhost:8001` 이 기본이다.
- 모델을 바꾼 뒤에는 `talkto.db` 를 지운다(아직 마이그레이션 도구가 없어 칸이 새로 생기지 않는다).
- **Postgres(Neon·compose)도 같다.** 서버가 켜질 때 빈 표만 만들고 있는 표는 고치지 않는다(빈칸 허용 칸을 붙이는 임시 방편은 로컬 SQLite 에서만 돈다). 이 브랜치를 배포하기 전에 예전 스키마로 만든 Postgres DB 는 지우고 다시 만든다. 실제 사용자 데이터가 생기기 전에 Alembic 마이그레이션을 들인다.

### 로그인 (`app/api/v1/auth.py`)

이메일·비밀번호(`POST /auth/signup`, `/auth/login`) 말고 카카오·구글로도 들어온다.

| 단계 | API | 하는 일 |
|---|---|---|
| 버튼 켜기 | `GET /auth/providers` | `{kakao, google, signup}` — 키를 넣은 로그인만 `true` |
| 시작 | `GET /auth/oauth/{kakao\|google}/start?return_to=<웹 주소>` | 창 전체를 옮긴다. state(서명)·쿠키를 만들고 그 회사 로그인으로 보낸다 |
| 돌아옴 | `GET /auth/oauth/{provider}/callback` | 사용자를 찾거나 만들고 `{return_to}#/auth?ticket=…` 로 보낸다. 실패하면 `#/login?error=…` |
| 토큰 받기 | `POST /auth/oauth/exchange` `{"ticket"}` | 한 번짜리 표(2분)를 `/auth/login` 과 같은 토큰으로 바꾼다 |

- 사용자 찾기: 이은 소셜 계정 → 그 회사가 **인증한** 이메일과 같은 기존 사용자에 잇기 → 새로 가입
  (`ALLOW_SIGNUP=false` 면 새 가입만 막는다). 이메일이 없으면 `…@social.talkto.invalid` 를 넣고
  `/auth/me` 에는 `email: null`, `provider: "kakao"` 처럼 낸다.
- `return_to` 는 `CORS_ORIGINS`, `FRONTEND_URL`, (로컬이면) `http://localhost:포트` 만 받는다.
- 설정: `KAKAO_CLIENT_ID`(REST API 키)·`KAKAO_CLIENT_SECRET`, `GOOGLE_CLIENT_ID`·`GOOGLE_CLIENT_SECRET`,
  `FRONTEND_URL`. 개발자 콘솔에 Redirect URI `{PUBLIC_BASE_URL}/api/v1/auth/oauth/{provider}/callback`
  을 등록한다(로컬: `http://localhost:8000/api/v1/auth/oauth/kakao/callback`, `…/google/callback`).
  어디서 값을 받는지는 `.env.example` 에 적어 두었다.

### 페르소나 만들기 API (`app/api/v1/persona_setup.py`)

| 단계 | API | 뒤에서 하는 일 (AI 서버) |
|---|---|---|
| 기본 정보 | `POST /personas` | — |
| 녹음·영상 올리기 | `POST /personas/{id}/recordings`, `POST /personas/{id}/transcribe` | (이 서버) 영상에서 소리 뽑기 → 전사·화자 나누기, 화자 추천, 녹음 사이 목소리 묶기 → (이 서버) 목소리별 미리듣기 |
| 목소리 고르기 | `POST /personas/{id}/speakers` (`voiceKeys`) | 녹음 분석(기억·말투·민감) |
| 핵심 질문 | `PUT /personas/{id}/answers`, `POST /personas/{id}/build` | 통찰, 지침 조립, 임베딩, (목소리 복제) |
| 대화 | `POST /personas/{id}/conversation`, `/conversations/{id}/messages/*`, `POST /messages/{id}/speech` | 기억 검색 + 응답(고친 말투 `styleExamples`, 빈틈 표시 `flagGaps`), 음성 |
| 더 알려 주실 기억 | `GET /personas/{id}/gap-questions`, `POST …/{qid}/answer·dismiss·ask-family·approve·reject` | 대답 뒤 작업이 빈틈 질문 만들기(`/v1/persona/gap-question`), 답 → 기억(GAP) 임베딩 |
| 대화 고치기 | `POST /messages/{id}/feedback`(`tags`·`correction`), `POST /conversations/{id}/feedback`, `POST /personas/{id}/corrections/{fid}/approve·reject` | 말투 고침 → StyleExample, 내용 고침 → 기억(FEEDBACK) 임베딩 |

'더 알려 주실 기억'(`app/services/gaps.py`)과 대화 고치기(`app/services/corrections.py`): 빈틈 질문은
대답을 돌려준 뒤에만 만든다(대화 중에 띄우지 않고 알림도 없다). 열린 질문은 대화 상대마다 10개까지다.
함께 쓰는 가족의 답·고침은 만든 분이 확인해야 반영된다. `GAP_QUESTIONS_ENABLED=false` 면 빈틈 표시를
부탁하지 않고 새 질문도 만들지 않는다(AI 서버를 먼저 올린 뒤 켠다 — 예전 AI 서버는 `flagGaps` 를 모른다).

오래 걸리는 일은 `persona_jobs` 표에 상태를 남기고 화면이 몇 초마다 읽는다. 녹음은 서명된 주소로만
내려받을 수 있다(로컬 디스크 / 운영 S3, `STORAGE_BACKEND`).

### 목소리 (Fish Audio drama-3, `app/services/persona_builder.py`, `app/services/voice_retrain.py`)

- 새 목소리는 모두 Fish drama-3 로 만든다(`voice_provider = "fish"`, `voice_id = "fish_…"`). AI 서버가
  목소리 id 로 회사를 고른다(`fish_` → Fish, `TalkTo` → MiniMax, 나머지 ElevenLabs).
- 그분 녹음을 Fish 로 보내기 전에 주인의 `overseas_fish` 동의를 본다(관리자는 빼고). 동의 게이트를 지나지
  않는 뒤 작업(배포 전에 건 작업·다시 시도·다시 만들기)도 막는다.
- 예전 목소리(`elevenlabs`·`minimax`·`elevenlabs_pvc`)는 `voice_retrain.sweep` 이 하나씩 Fish 로 다시 만든다
  (VOICE 작업 `reason: fish`, 서버 전체에서 한 번에 하나, 녹음 탓으로 3번 실패하면 멈춤 — 목소리 회사
  문제·끊김은 세지 않는다). 필수 동의를 다 마친 분의 것만 건다. 관리자가 손으로 넣은 목소리(회사 없음)는
  다시 만들지 않는다. `POST /consents/accept` 와 10분마다 도는 `main.periodic` 이 부른다.
  **기본은 꺼져 있다**(`VOICE_RETRAIN_ENABLED=false`): 배포 뒤 몇 개 먼저 들어 보고 켠다.
- 다 만들면 목소리 id 와 회사를 한 번에 바꾸고 예전 대답 음성을 비운 뒤, 예전 대답 음성 파일과 예전 목소리를
  그 회사에서 지운다. 대답 음성 파일은 목소리마다 키가 달라(`reply_audio_key`) 새 목소리 것은 남는다.
- 새 목소리를 받은 뒤 바꾸지 못하면 새 목소리를 지운다. 서버가 꺼져 끊긴 만들기는 작업에 남긴 표시
  (`options.clone`)를 보고 켤 때 정리한다(`clean_interrupted_clones`, 회사가 답하기 전에 끊겼으면 관리자 알림).
  목소리 회사 문제(`VOICE_PROVIDER_*`)는 관리자 알림으로 따로 보낸다(`notify.voice_provider`). Fish 에 간
  호출이 성공해야 풀린 것으로 본다(예전 목소리 읽기는 세지 않는다).

### 페르소나 가져오기 (관리자, `app/services/persona_import.py`, `app/api/v1/admin_import.py`)

손으로 만든 페르소나(AI 레포 `output/<subject>/` 의 옛 프롬프트·장기기억)를 고른 계정에 바로 대화할 수 있는
대화 상대로 넣는다. 묶음은 AI 레포 `tools/export_manual_persona.py` 가 만든다. **가족 자료라 git 레포
밖(기본 `TalkTo\페르소나 가져오기\`)에 두고 커밋하지 않는다. OneDrive 로 동기화되므로 가져온 뒤 지운다.**
관리자 웹 `/admin/import` 가 아래 API 를 부른다.

| API | 하는 일 |
|---|---|
| `GET /admin/users?q=` | 이메일·이름·소셜 계정 이메일·id 로 찾는다(카카오가 이메일을 안 주면 users.email 이 자리표시라 소셜 이메일로 찾는다). 줄마다 `providers`(이은 소셜 전부)·`personaCount`·`emailMasked`(`a***@gmail.com`)·`importedPersonas`(`[{id, name, createdAt}]`, 되돌리기 id). **전체 이메일은 싣지 않는다.** q 가 없으면 예전 목록 |
| `POST /admin/personas/import` | 묶음 + 대상 + 지침 방식 → `dryRun: true`(기본)면 쓰지 않고 미리보기, false 면 가져오기 |
| `DELETE /admin/personas/{id}/import` | 가져온 대화 상대(`origin=import`)만 지운다(사람·기억·대화·작업까지, `persona_delete` 와 같은 길). 만들기 흐름 대화 상대는 409 |

**묶음(version 1)** — 정본은 `app/schemas/persona_import.py`(pydantic, 모르는 칸 거부. `source` 만 모르는 칸을 버린다):

```json
{
  "format": "talkto-persona-bundle", "version": 1,
  "source": {"subject": "…", "promptSha256": "<sha256(prompt.legacy, UTF-8, LF)>", "builtAt": "…",
             "rulesSha256": "<선택: sha256(prompt.rules), 있으면 맞는지 본다>",
             "promptRev": "disk", "promptBlob": "<git hash-object>", "toolCommit": "<AI HEAD>"},
  "persona": {"name": "…", "relation": "할머니", "addressTerm": "할머니", "status": "deceased"},
  "prompt": {"legacy": "<옛 프롬프트 원문>", "rules": "<L1 layered, 사망 규칙 자리 {{death_rule}}>",
             "deathRules": {"deny": "…", "brief": "…{{death_answer}}…", "ask_only": "…", "open": "…"},
             "bridge": "<연결 문단, '- {{listener_line}}' 한 줄>"},
  "memories": [{"legacyId": "…", "category": "…", "title": "…", "content": "…", "sensitive": false}],
  "probeEmbeddings": [{"legacyId": "…", "embedding": [0.01]}],
  "voice": {"voiceId": "fish_…"}
}
```

**요청**: `{targetUserId, requireProvider: "kakao"(기본)|"google"|null, dryRun, bundle,
prompt: {mode: "guarded"(기본)|"verbatim", deathPolicy: "ask_only"(기본)|"deny"|"brief"|"open", deathAnswer},
overrides: {name, relation, addressTerm, myName, myRelation, calledMe}, voice: {attach: "after_owner_pledge"(기본)|"none",
probe}, checkQuery}`. 지침 방식·사망 정책·내 이름은 묶음을 다시 만들지 않고 화면에서 바꾼다.

- **대상**: id 로만 고른다. ACTIVE 이고 `requireProvider` 로 이은 계정이어야 한다(같은 이메일의 관리자 계정을
  잘못 고르지 않게). **계정 필수 동의(0.5 국외 이전 포함)를 마치지 않은 계정은 가져오기 400**(미리보기는 경고만,
  관리자 계정이어도 같다) — 주인이 먼저 한 번 로그인해 동의를 마친다. 같은 주인에게 같은 원본(`promptSha256`)을
  또 넣으면 409(먼저 되돌린다). 결제 전 대화 상대 상한은 경고만 한다.
- **기본 정보**: 관계·내 관계는 `intake.RELATIONS`/`MY_RELATIONS` 에서(`나 자신` 은 안 됨, `GET /intake/meta`),
  상태는 `deceased` 만. 저장하는 길이(이름·관계·내 이름·내 관계 20자, 호칭·나를 부르던 말 15자)를 넘으면
  자르지 않고 400 이다 — 잘린 내 이름은 옛 가족 목록에 없는 이름이 된다. 주인의 '정보 고치기'도 같은 규칙이다.
- **지침**: `guarded` = `rules`(`{{death_rule}}` ← 고른 정책 줄, `{{death_answer}}` ← 한마디. 비우면 지어내지 않고
  `"…" 정도로(만)` 구절을 뺀다. 한마디 자리는 `brief`·`ask_only` 에만 있다 — `deny`·`open` 은 한마디를 쓰지 않고
  `deathAnswerSet=false`) + 빈 줄 + `bridge`(`{{listener_line}}` ← myName 이 있으면 '지금 대화하는 사람' 한 줄,
  없으면 그 줄째 뺀다) + 빈 줄 + `legacy`. `verbatim` = `legacy`. 옛 본문은 끝 글자까지 그대로다(줄바꿈만 LF).
  `{{` 가 남거나 50,000자를 넘으면 400. guarded 는 `rules` 에 지금 L1 의 안전 규칙(`persona_import.GUARDRAILS`:
  안전 가드레일 제목·109·대화 설정 비공개·의료/법률/금전 조언 금지·마크다운·`'(물으면만)'` 규칙)이 다 있어야 하고,
  하나라도 없으면 400(오래됐거나 손으로 줄인 묶음). L1 문장을 바꾸면 `GUARDRAILS` 도 맞춘다. 미리보기 `prompt` 에
  `rulesSha256`·`rulesChars`·`guardrails`(항목별 참거짓, verbatim 은 모두 false)를 싣는다.
- **기억**: source `IMPORT`, `EPISODE`, ACTIVE, 제목은 옛 제목(sensitive 면 ` (물으면만)`), tags
  `category:<분류>`·`legacy_id:<옛 id>`(+`sensitive`). 임베딩은 **본문만** 지금 AI 서버로 다시 만든다(만들기 흐름과
  같은 입력). 질문 벡터와 차원이 다르거나 한 건이라도 빠지면 아무것도 쓰지 않고 502 — 차원이 다르면 오류 없이
  기억이 하나도 안 찾힌다. `probeEmbeddings` 는 `"title: content"` 를 다시 임베딩해 옛 벡터와 견준 최소 코사인
  (`mvpModelMatch`)만 보인다(참고, 멈추지 않음). 시험 질문(`checkQuery`)으로 찾히는 기억 수도 보인다(대화와 같은
  고르기 `memory_search.best`. 가져온 뒤에는 DB 에서 다시 읽은 벡터로 보고, 대화용 '최근 기억' 대신하기는 쓰지 않는다).
- **대화 상대**: `READY`, `origin="import"`, `answers={}`, `ready_at=지금`, `is_active=false`, 고인. 체험·구독은
  건드리지 않는다. `persona_jobs` 에 `IMPORT`(DONE) 한 줄로 기록을 남긴다(관리자 id·때·해시·건수·방식·임베딩
  모델/차원·목소리 상태 — 내용 없음). **이 줄은 대화 상대를 지우면(되돌리기 포함) 함께 지워진다.** 대화 상대보다
  오래 남는 관리자 작업 기록은 통계 이벤트다: `persona_imported`(관리자 id·해시 셋·건수·방식·사망 정책·임베딩
  모델/차원·목소리)·`persona_import_undone`(관리자 id·해시 셋). `analytics_events` 는 대화 상대에 외래 키가 없어
  지워도 남고 1년 보관한다. 대상 계정은 `user_id` 칸에만 둔다(탈퇴하면 `forget_user` 가 끊는다).
- **목소리**: `fish_…` 는 대기로 두고(`IMPORT` 기록의 `voiceId`), 주인이 앱에서 '목소리 붙이기'로
  `pledge_deceased` 를 직접 확인하면(`POST /personas/{id}/voice`, channel web) 그때 붙인다. 새로 만들지 않고 VOICE
  작업 DONE 한 줄만 남긴다. `voice_provider` 는 비워 둔다: 대화 상대를 지우거나 목소리 동의를 거둬도 회사의 목소리를
  지우지 않는다(대답 읽기는 AI 서버가 id 앞글자 `fish_` 로 회사를 고르고, `voice_retrain` 은 provider 없는 목소리를
  건너뛴다). 주인이 동의를 거둬 목소리가 떨어지면 다시 대기다. **관리자가 목소리만 떼면**(`PATCH
  /admin/personas/{id}` `{voiceId: null}`) 기다리던 목소리도 거둔다(`IMPORT` 기록 `voice="removed"`, 주인이 다시
  확인해도 붙지 않는다. 회사 쪽 목소리는 지우지 않는다). `voice.probe` 면 미리보기에서 짧은 한 문장을 읽어 본다
  (`alert=False` — 실패해도 목소리 회사 경보를 남기지 않고, 붙이기를 `none` 으로 내린다).
- **보호 장치**(`origin=import`): 녹음 올리기·지우기·반영, 전사·화자·질문 답·만들기·되돌아가기는 409.
  `PUT /personas/{id}` 는 기본 정보만 바꾸고 다시 만들지 않는다(`updating=false`, 답을 바꾸면 409).
  `run_update`·`run_extend`·`_assemble` 은 아무것도 바꾸지 않는다(이중 안전). 종교·건강 동의를 거두면 그 분류
  (`신앙`·`종교`/`건강`)의 가져온 기억을 끄고(INACTIVE), 다시 동의하면 그때 끈 것만 켠다. 화면에는 `origin`,
  상세에 `voicePending` 과 `sensitiveMemories`(`{religion|health: "on"|"off"|"none"}` — 동의 전에도 켜진 채라
  `consents` 만으로는 '아직 고르지 않음'과 '거둬서 꺼 둠'을 가를 수 없다)를 싣는다.
- `personas.origin` 칸은 서버가 켤 때 운영 PostgreSQL 에도 붙는다(`app/db/session.py` `ADDED`, 빈칸 허용).

### 동의 (`app/services/legal.py`, `app/api/v1/legal.py`, `app/api/v1/consents.py`)

- 동의문 원문은 `app/static/legal/*.md` 하나뿐이다. `GET /legal`·`GET /legal/{key}` 가 자리표시
  (`{{상호}}` 등, `BUSINESS_*`·`LEGAL_*` 설정)를 채워 보낸다. `{{호칭}}` 은 화면이 바꾼다.
- 계정 필수 동의 9가지가 다 있어야 API 를 쓴다(`CurrentUser`, 관리자 제외). 빠지면 403
  `consent_required`. `/auth/me`·`/auth/logout`·`/consents/accept`·`/consents/me`·`DELETE /auth/me`
  는 동의 전에도 열린다(`AuthedUser`). 문서 version 을 올리면 다시 동의를 받는다.
- 지금 상태는 `consents`, 이력은 `consent_records`(덮어쓰지 않음, 탈퇴·지우기 뒤에도 남김).
- 대화 상대별 확인: 목소리 만들기(그분 상태별 pledge 문서), 녹음·남긴 글 올리기(`pledge`),
  종교·건강 질문(`POST /personas/{id}/consents`), 결제(`agreements`).
- 회원 탈퇴(`DELETE /auth/me {confirm:"탈퇴"}`)는 대화 상대를 모두 지우고(목소리를 만든 회사에서도)
  계정은 알아볼 수 없게 바꾼다. 결제 기록·동의 이력은 남긴다.

### 결제 전 모드 · 구독 신청 (`PAYMENTS_OPEN`, `app/services/billing.py`)

- `PAYMENTS_OPEN=false`(기본)면 결제 전 모드다. 다 만든 대화 상대는 `status: PRELAUNCH` 로 무료로,
  횟수 제한 없이 쓴다(무료 체험 자리 `trials_used` 를 쓰지 않는다). `PRELAUNCH_CONVERSATIONS` 를
  넣으면 대화 상대마다 최근 30일 대화 상한이 생긴다.
- 요금제는 추억(`memories`)·가족(`family`) 둘이다. 똑 닮은 목소리(`premium`) 요금제는 없앴다
  (2026-10-17). 남은 구독·신청·결제 창은 서버가 켤 때 고친다(`app/db/session.py` `_retire_premium`).
- 결제 전에는 계정 하나가 가진 대화 상대를 `PRELAUNCH_PERSONAS_PER_ACCOUNT`(기본 3, 비우면 제한 없음)
  명까지 만든다. 넘으면 `POST /personas` 가 409(관리자 제외, 지우면 다시 만들 수 있다).
- `POST /billing/requests {personaId, plan}` 구독 신청(대화 상대마다 하나, 다시 보내면 요금제를 바꾼다),
  `DELETE /billing/requests/{personaId}` 취소. `POST /billing/checkout` 은 결제 전에는 409(관리자도).
- `GET /billing/me` 에 `paymentsOpen`·`prelaunchConversations`, 대화 상대 보기마다 `request`
  (신청 중인 것만). 결제를 열면(`PAYMENTS_OPEN=true` + 다시 켜기) 원래 규칙으로 체험이 시작되고,
  신청한 대화 상대가 결제에 성공하면 신청이 `CONVERTED` 가 된다.

### 사용 통계 (`app/services/analytics.py`, `app/api/v1/stats.py`)

- 서버가 정해진 이벤트(가입·로그인·대화 상대 만들기·메시지·구독 신청·결제 …)를 `analytics_events` 에
  남긴다. 대화 내용·녹음·질문 답·이름·이메일은 넣지 않는다(props 는 짧은 글자·숫자·참거짓만).
  화면은 `POST /analytics/events` 로 허용한 이름(screen_view 등)만 보낸다. 나머지는 무시(204).
- 1년 지난 이벤트는 `main.periodic` 이 하루 한 번 지운다. 회원 탈퇴하면 그 사람 이벤트의 user_id 를 비운다.
- `GET /admin/stats?days=30`(관리자) — 숫자·퍼널·일별·분포·표. 관리자 계정의 사용은 세지 않는다.
  `GET /admin/stats/export?kind=requests|events&days=30` — CSV(UTF-8 BOM).

### 운영 보안 (`ENVIRONMENT=production`)

- 켜질 때 `JWT_SECRET`(32자 이상)·`BOOTSTRAP_ADMIN_PASSWORD`(기본값 금지)·`CORS_ALLOW_LOCALHOST=false`·
  `AI_SERVER_TOKEN`·`STORAGE_BACKEND=s3` 를 보고, 하나라도 안전하지 않으면 켜지지 않는다.
  API 문서(`/docs`·`/redoc`·`/openapi.json`)는 닫는다.
- 요청 횟수 제한(`app/core/ratelimit.py`, 서버 메모리): 로그인·가입·토큰 갱신은 IP(CloudFront 가 붙인
  주소)마다, 메시지·목소리 듣기·올리기·사용 통계는 사용자마다. 넘으면 429. `RATE_LIMIT_ENABLED=false` 로 끈다.
- 본문 크기 상한(`app/core/body_limit.py`): 녹음 `max(2GiB, 20 × MAX_UPLOAD_MB)`, 남긴 글 110MiB,
  목소리 메시지 26MiB, 그 밖 2MiB. 넘으면 413.

## Run with Docker Compose

Docker Compose runs the API and PostgreSQL in the same topology intended for a Linux server.

```bash
cp .env.docker.example .env
# Change POSTGRES_PASSWORD, JWT_SECRET, and BOOTSTRAP_ADMIN_PASSWORD in .env.
docker compose up -d --build
docker compose ps
```

- API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

PostgreSQL data is stored in the `postgres_data` named volume. Uploaded media is stored in the
separate `media_data` named volume mounted at `/data/media`. Rebuilding or replacing containers
does not remove either dataset. `docker compose down` preserves them; only
`docker compose down --volumes` removes them.

On a Linux server, install Docker Engine with the Compose plugin, clone the repository, create the
`.env` file, and run the same `docker compose up -d --build` command. The Compose configuration
does not depend on a specific cloud provider.

Useful commands:

```bash
docker compose logs -f api
docker compose restart api
docker compose pull
docker compose up -d --build
```

## Run directly with Python

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
uvicorn app.main:app --reload
```

- API docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`
- Default local admin: `admin@talkto.dev` / `Admin1234!`

SQLite remains the non-Docker local default. Set `DATABASE_URL` to a PostgreSQL async URL such as
`postgresql+asyncpg://user:password@localhost:5432/talkto` for shared environments.

## Structure

```text
app/
  api/v1/       HTTP routers
  core/         configuration, authentication, exceptions
  db/           SQLAlchemy session and models
  schemas/      API request/response contracts
  services/     AI and media integrations
tests/          API tests
```

The AI adapter calls `POST {AI_SERVER_URL}/v1/persona/responses` with conversation history
(the room's last 12 messages), retrieved memories, the persona's total active memory count
(`memoryTotal`, lets the AI spot low-memory personas), and persona metadata. When the AI server is not configured, development uses a
clearly marked deterministic fallback response.
