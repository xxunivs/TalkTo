# TalkTo_PersonaAI_FE

TalkTo 앱·웹 (Flutter). 녹음이나 영상을 올리고, 그분 목소리를 고르고, 핵심 질문에 답하면 바로 페르소나와 대화한다.
목소리는 녹음이 여러 개여도 같은 사람끼리 묶여 나오고, ▶ 로 들어보고 고른다.

```
로그인 → 페르소나 목록 → 기본 정보 → 녹음 올리기 → 목소리 찾기 → 핵심 질문 → 대화
```

## 실행

API 서버(`talkto_api_server`, :8000)와 AI 서버(`TalkTo_PersonaAI_AI`, :8001)를 먼저 띄운다.
방법은 `talkto_api_server/README.md` 의 "로컬에서 전체 실행" 에 있다.

```bash
flutter pub get
flutter run -d chrome
```

API 주소는 기본이 `http://localhost:8000/api/v1` 이다. 다른 곳을 쓰려면
`flutter run -d chrome --dart-define=API_BASE_URL=https://api.도메인/api/v1`.

## 폴더

```
lib/core/              API 연결(토큰 자동 갱신), 로그인 상태, 테마
lib/features/auth/     로그인·회원가입
lib/features/personas/ 목록, 만들기 단계(녹음·화자·질문·만드는 중)
lib/features/chat/     페르소나와 대화 (글·음성 입력, 목소리 듣기, 👍👎)
lib/features/admin/    관리자 대시보드 (관리자 계정에만 보임)
```

질문 문항은 앱에 박지 않고 서버(`GET /intake/meta`)에서 받아 그린다. 문항을 바꿀 때 앱을 다시 배포하지 않아도 된다.

## 테스트

```bash
flutter test
```
