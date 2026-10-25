<img src=".github/assets/hero.png" alt="TalkTo">

# TalkTo

통화 녹음과 설문만으로 한 사람의 말투, 기억, 목소리를 그대로 구현하는 페르소나 AI 서비스

**서비스: https://app.talkto.co.kr** (운영 중)

| 기간 | 구분 |
|------|------|
| 2026.03 ~ 진행 중 | 헬스케어 창업동아리 Medilux, TalkTo팀 |

이 레포는 2026년 10월 25일 운영 코드를 포트폴리오용으로 옮겨 정리한 사본입니다. 운영 코드는 팀 조직의 비공개 레포에 있습니다.

<br>

## 🎯 개요

통화 녹음과 설문만으로 **한 사람의 말투, 기억, 목소리를 그대로 구현하는 페르소나 AI 서비스**를 개발했습니다.
기존에는 사람이 직접 수작업으로 페르소나를 만드는 데 몇 주씩 걸렸지만(프롬프트 5,863자, 기억 72개), 이를 녹음 업로드와 간소화된 설문 응답만으로 자동 생성할 수 있도록 파이프라인을 구축했습니다.

#### 인물의 말투와 목소리를 재현한 대화 화면

<img src=".github/assets/screen_chat.png" alt="대화 화면">

#### 녹음 업로드부터 설문 작성까지 step-by-step 페르소나 생성 화면

<img src=".github/assets/screen_steps.png" alt="페르소나 생성 화면">

**맡은 역할 (AI, BE)**: 페르소나 제작 파이프라인 설계, 화자 식별, 기억 추출 및 평가, 4층 구조 프롬프트 적용, 백엔드 연동 API 개발 및 운영 배포

<br>

## 📂 시스템 구조

사용자가 웹에서 녹음과 설문을 올리면 AI 파이프라인이 페르소나를 만들고, 대화할 때는 검색한 기억으로 그분 말투의 답과 목소리를 만듭니다.

<img src=".github/assets/architecture.png" alt="시스템 구조">

<br>

## 🔧 트러블 슈팅

### 1. 통화 녹음 속 대상자 목소리 식별

<table>
<tr>
<td width="44%"><img src=".github/assets/ts1_voice.png" alt="목소리 선택 화면"></td>
<td>
<b>상황</b><br>통화 녹음은 누가 대상자인지 구분되지 않아 대화 속 호칭으로 대상자를 추정했으나, 호칭이 없는 경우가 많아 외부 데이터 18건 중 2건만 식별 가능했습니다.<br><br>
<b>해결</b><br>사용자가 대상자 목소리를 최초 1회 선택하면 ECAPA 성문 임베딩으로 유사도를 비교하도록 개선했습니다. 기준에 미달하는 화자는 비워두어 타인의 발화가 섞이는 오류를 차단했습니다.<br><br>
<b>결과</b><br>외부 데이터 통화에서 대상자 식별 2/18건 → <b>18/18건</b><br><br>
<b>코드</b><br><code>ai/pipeline/s1_transcription/b_speaker_id.py</code><br><code>ai/pipeline/s1_transcription/d_voice_groups.py</code>
</td>
</tr>
</table>

### 2. 녹음에 없는 정보를 채우는 설문 설계

<table>
<tr>
<td width="44%"><img src=".github/assets/ts2_survey.png" alt="설문 화면"></td>
<td>
<b>상황</b><br>일상 통화 녹음은 안부 위주라 인물의 성격이나 깊은 가치관을 파악하기에는 한계가 있었습니다. 그렇다고 99문항에 달하는 초기 설문을 모두 요구하면 사용자의 피로도가 높아져 끝까지 답하기 어려웠습니다.<br><br>
<b>해결</b><br>설문 문항을 목적별로 분류해 필수 13문항으로 축소했습니다. 녹음에서 이미 파악 가능한 정보는 질문에서 제외하고, 녹음 속 실제 발화는 버튼 형태로 제공해 입력 부담을 대폭 줄였습니다.<br><br>
<b>결과</b><br>사용자가 꼭 답해야 하는 문항 99개 → <b>13개</b><br><br>
<b>코드</b><br><code>ai/pipeline/s0_intake/</code><br><code>ai/docs/Persona_Intake.md</code>
</td>
</tr>
</table>

### 3. 프롬프트 모듈화

<table>
<tr>
<td width="44%"><img src=".github/assets/ts3_layers.png" alt="4층 프롬프트 구조"></td>
<td>
<b>상황</b><br>LLM이 프롬프트 전체를 구성하면서 필수 안전 규칙까지 매번 변형되었고, 출처 추적이 불가능해 오답이 발생해도 원인을 찾기 어려웠습니다.<br><br>
<b>해결</b><br>프롬프트를 4개의 층위로 모듈화했습니다. 절대 변하지 않아야 하는 안전 규칙은 고정하고, 개인정보나 말투 같은 가변 요소만 설문과 녹음 데이터에서 동적으로 채워지도록 구조화했습니다.<br><br>
<b>결과</b><br>규칙(안전, 호칭, 모르는 일 등)을 전부 지킨 비율 65.5% → <b>81.6%</b> (탐침 29개, 반복 평균)<br><br>
<b>코드</b><br><code>ai/pipeline/persona_prompt/</code><br><code>ai/prompts/rules/</code>
</td>
</tr>
</table>

<br>

## 📊 결과

실제 통화 13건과 외부 공개 데이터(AI Hub 방언 2인 대화, 5명)로 단계마다 따로 측정했습니다.

<img src=".github/assets/results.png" alt="결과">

<br>

## 🛠 사용 기술

<img src=".github/assets/stack.png" alt="사용 기술">

<br>

## 📁 폴더 구조

```
ai/                  AI 서버 (페르소나 제작 파이프라인, 대화, 평가)
├── app/             서버 설정, API, 스키마, 외부 API 연동
├── pipeline/        s0 설문 ~ s5 대화, persona_prompt (4층 프롬프트)
├── orchestrator/    대화 한 턴의 중간계층
├── prompts/         공통 규칙 모듈, 상황별 층 구성
├── evaluation/      화자 식별, 전사, 페르소나 품질 평가
└── test/            자동 테스트 (API 키 없이 실행)

server/              백엔드 (FastAPI, 팀)
web/                 웹 앱 (Flutter, 팀)
archive/             이전 버전 (2026.04 텔레그램 시제품, 2026.05 할머니 챗봇 1차)
.github/             CI 설정, README 그림
```
