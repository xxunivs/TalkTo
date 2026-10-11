# TalkTo_PersonaAI_AI

TalkTo 페르소나 제작과 대화를 위한 AI 서버

녹음 분석, 프롬프트 생성, 기억 임베딩, 음성 합성 처리

대상자 화자 추천(`/v1/analysis/subject-speaker`), 녹음 사이 같은 목소리 묶기(`/v1/analysis/voice-groups`, ECAPA)

```text
프론트(TalkTo_PersonaAI_FE) → talkto_api_server → TalkTo_PersonaAI_AI
```

BE가 전달한 프롬프트, 검색한 기억, voice ID 사용

사용자 인증, 동의 관리, 기억 검색과 운영 DB 저장은 BE 담당

현재 텍스트 응답은 전체 생성 후 한 번에 반환하는 비스트리밍 방식

## 제작 파이프라인

단계별 도구 구성 완료

자료 검토, 수동 수정, 운영 DB 반영은 수작업 필요

| 단계 | 내용 |
|---|---|
| s0 설문 | 설문을 프롬프트 재료와 기억으로 변환 |
| s1 전사 | 녹음 전사, 화자 분리와 대상자 식별, 고유명사 교정 |
| s2 추출 | 인물, 민감도, 말투, 기억, 요약 분석 |
| s3 종합 | 기억 병합, 통찰 추출, 프롬프트 조립, 임베딩 생성 |
| s4 음성 | 대상자 음성 샘플 수집과 목소리 복제 |
| s5 대화 | 로컬 대화 테스트 |

통합 실행기 `pipeline.run_pipeline`의 실행 범위는 s1~s3

s0과 s5는 별도 명령, s4는 API 또는 단계별 함수로 처리

화자 라벨은 녹음별로 부여되므로 각 녹음에서 대상자 식별 필요

## 폴더

```text
infra/                     서버 설정, API, 스키마, 외부 API 연동
pipeline/                  s0~s5 단계별 도구
input/<대상자>/             설문, 녹음, 인터뷰 등 원본 자료
output/<대상자>/            파이프라인 산출물
output/<대상자>_manual/     수동으로 제작하거나 검토, 수정한 산출물
docs/                      설문 양식과 인계 문서
test/                      자동 테스트와 품질 평가
```

`manual`도 같은 대상자의 입력 자료와 단계별 도구 사용

기존 운영 DB에는 manual 제작 결과 반영

로컬 파일 수정 시 DB 자동 갱신 없음

## 실행

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # API 키 등 입력
uvicorn main:app --reload --port 8001  # BE가 8000 사용
```

API 문서: `http://localhost:8001/docs`

목소리 기반 화자 식별에는 `pip install -e ".[dev,speaker]"`와 `ffmpeg` 필요

음성 샘플 수집에는 `ffprobe`도 필요

### 파이프라인 실행

- 설문 위치: `input/<대상자>/Persona_Intake_*.md`
- 녹음 위치: `input/<대상자>/음성/`

```bash
# 설문 변환 후 s1~s3 실행
python -m pipeline.s0_intake.b_split_intake --subject 신금자 --subject-out 신금자 --write
python -m pipeline.run_pipeline --subject 신금자

# 앞 단계 결과가 준비된 상태에서 필요한 단계만 실행
python -m pipeline.run_pipeline --subject 신금자 --only s2_e

# 로컬 대화 테스트
python -m pipeline.s5_chat.a_chat --subject 신금자 --tts
```

설문 변환에서 `--subject-out` 생략 시 `<대상자>_manual`에 저장

`run_pipeline --subject 신금자_manual` 지정 시 아래 경로 사용

- 입력: `input/신금자/`
- 출력: `output/신금자_manual/`

전사는 기존 파일명을 기준으로 캐시 사용

다른 단계는 재실행 시 산출물을 덮어쓸 수 있으므로 수동 수정한 결과 확인 후 실행

## 수동 제작

1. 설문, 녹음, 인터뷰 자료 정리 후 필요한 단계별 도구 실행
2. 대상자 화자, 사실, 말투, 기억 검토 후 manual 폴더의 결과 수정
3. 최종 프롬프트, 기억, 임베딩, voice ID 준비 후 로컬 대화 검증
4. 확정한 결과를 별도 업로드 작업으로 운영 DB에 반영

## 사용 모델

| 기능 | 모델 |
|---|---|
| 페르소나 응답 | 배포 설정 `gpt-5.4-mini` |
| 녹음 분석과 기억 추출 | `gpt-5.4-mini` |
| 전사문 교정 | `gpt-4.1-mini` |
| 녹음 전사와 화자 분리 | ElevenLabs `scribe_v1` |
| 대화 음성 전사 | OpenAI `whisper-1` |
| 목소리 기반 화자 식별 | SpeechBrain ECAPA-TDNN |
| 기억과 질문 임베딩 | `text-embedding-3-small`, 1536차원 |
| 음성 합성 | ElevenLabs `eleven_multilingual_v2` |
| 품질 평가용 채점 | `gpt-5.5` |

모델은 환경변수로 변경 가능

- 기본 설정: `infra/setting.py`
- 응답 모델의 코드 기본값: `gpt-4.1-mini`

페르소나는 파인튜닝 대신 프롬프트와 검색한 기억으로 제어

## 테스트

```bash
python -m unittest discover -s test -t .
```

자동 테스트는 외부 API 모킹

실제 API를 사용하는 품질 평가는 [평가 README](test/evaluation/README.md) 참고

일부 평가 스크립트는 이전 경로가 남아 있어 수정 필요
