# 프롬프트 비교 실험

같은 질문·같은 기억으로 페르소나 지시문만 바꿔 답을 만들고, 사실 정답·지어냄·말투·규칙 준수를 비교한다. OpenAI 호출은 `compare.py`·`batch.py` 의 실행·판정 단계와 가족 인터뷰 후보 추출(`batch.py all` 이 후보 파일이 없을 때 한 번)뿐이고 나머지는 로컬에서 돈다.

## 데이터 (git 제외, `data/`)

대상자 이름·가족·통화 내용이 들어가는 개인 데이터라 레포에 넣지 않는다.

| 파일 | 내용 |
|---|---|
| `subject.json` | 대상자 설정(형식은 `subject.py` 머리말) |
| `memories_<name>_<tag>.json`, `personas_<tag>.json`, `memory_embeddings_<tag>_p*.json` | 운영 DB export(기억·손으로 쓴 프롬프트·기억 벡터) |
| `items.jsonl` (`subject.json` 의 `items_file`) | 실제 통화에서 뽑은 원본 문항: fact(기억에 답 있음)·absent(없음)·loo(근거 기억을 뺀 것) |
| `probes.json` | 규칙·관계 탐침 문항(형식은 `probes.py` 머리말) |
| `utterances.csv`, `cue_emb.json` | 통화 전사 발화(말투 기준선·화자 라벨), 질의 임베딩 캐시 |
| `interview_candidates*.json` | 가족 인터뷰 후보(사람 확인본 / 자동) |

## 순서

```powershell
.venv\Scripts\python.exe evaluation\prompt_compare\prepare.py        # 변형·문항·기억 선택 만들기(OpenAI 없음)
.venv\Scripts\python.exe evaluation\prompt_compare\batch.py cost     # 비용 추정
.venv\Scripts\python.exe evaluation\prompt_compare\batch.py all --batch <id>   # 생성 → 판정 → 요약
```

`compare.py` 는 변형 몇 개를 한 번씩 돌리는 단순 비교(`all --limit 5` 로 시험), `batch.py` 는 조건×반복 묶음과 부트스트랩 차이·판정기 일치도까지 낸다. `--mock` 이면 OpenAI 없이 전 단계 경로만 확인한다.

## 변형과 조건

`prepare.py` 가 `variants/` 에 지시문을 만든다: 운영 수제(ops), 기존 자동 조립(auto_now), 4층(auto_layered)과 그 절제 변형(주고받기 0·60개, 말투 카드 없음, L1 없음, 인터뷰 후보 포함), 운영 수제 + 4층 L1(ops_L1). 4층의 실제 주고받기는 평가 문항 구간(같은 통화 세그먼트 ±8)을 빼고 고른다 — 녹음 속 실제 답을 프롬프트로 베끼지 않게. `batch.py` 의 조건 표는 그 파일 머리말에 있다(주고받기를 메시지로 넣는 조건, 중간계층 경로 포함).

## 지표

- 원본 세트(실제 통화 질문): fact 정답·지어냄·놓침(LLM 판정), absent 모름 처리, 말투 거리(종결어미 분포 Jensen-Shannon, 실제 대상자 발화 기준), 실제 답 대비 길이 배율, 존댓말·사랑·챙김 비율.
- 확장 세트(만든 문항): loo 모름 처리, 탐침 통과율(LLM 기준표 + 코드 검사), 여러 턴 대화의 챙김 턴 수·'사랑' 횟수.
- 원본 세트와 확장 세트는 따로 보고한다.

## 보조 도구 (OpenAI 없음)

- `lang_scan.py` — 이미 만든 답에 언어 누출 가드(`orchestrator/lang_guard.py`)를 돌려 조건별로 걸린 비율과 걸린 답 전부를 남긴다.
- `precheck_replay.py --batch <id>` — 중간계층 조건의 '놓침'이 사전 판정 지시 때문인지, 가드 되묻기 때문인지, 모델 스스로인지 가른다.
- `style.py` — 말투 특징·거리 계산.
