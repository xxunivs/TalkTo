# TalkTo AWS 배포 (서울 리전)

앱은 `https://app.talkto.co.kr` 한 주소 아래에 웹 화면과 API를 함께 둔다.

```
사용자 ─https─▶ app.talkto.co.kr (Route 53 → CloudFront)
                           ├─ /                → S3 (웹 화면, Flutter)
                           └─ /api/*, /health → (AWS 안쪽 길, VPC 오리진) EC2 한 대
                                                 ├─ 도커: 백엔드 + AI 서버
                                                 ├─ RDS PostgreSQL (대화·회원, 비공개)
                                                 └─ S3 (녹음·목소리, 비공개)
```

## 한 달 비용 (서울, 부가세 별도)

| 만드는 것 | 내용 | 한 달 |
|---|---|---|
| EC2 t4g.medium (ARM, 4GB) | 백엔드 + AI 서버 도커 2개, 스왑 4GB | $30.37 |
| EBS 30GB gp3 | 서버 디스크(암호화) | $2.74 |
| 공인 IPv4 1개 | 서버가 OpenAI·Fish Audio·ElevenLabs 등을 부를 때 쓰는 고정 IP | $3.65 |
| RDS PostgreSQL 16 (db.t4g.micro) + 20GB | 비공개, 암호화, 7일 백업(무료), 지우기 방지 | $20.87 |
| Parameter Store | 운영 설정·비밀값(SecureString, 4KB 이하 무료) | $0 |
| CloudFront | https 주소, 웹 캐시, 보안 헤더(매달 1TB·1천만 요청까지 무료) | $0 |
| S3 3개 + 게이트웨이 엔드포인트 | 웹 / 녹음 / 배포 묶음(30일 뒤 자동 삭제) | 몇백 원 |
| CloudWatch Logs | 서버 기록 14일 보관(매달 5GB 무료) | $0 |
| Route 53 + 인증서 | talkto.co.kr DNS(호스팅 영역 1개), https 인증서는 무료·자동 갱신 | $0.50 |

합계 **약 $58.5/월(부가세 포함 약 $64, 약 8만 9천 원)**. 처음 만든 t3.large(8GB)보다 한 달 약 $46 싸다.
OpenAI·Fish Audio(목소리)·ElevenLabs(받아쓰기, 예전 목소리)·MiniMax(예전 목소리) 요금은 별도다.
예전 목소리를 모두 Fish 로 다시 만들 때까지는 ElevenLabs·MiniMax 계정도 충전해 둔다.

더 줄이려면(서비스가 안정된 뒤): 1년 Compute Savings Plan과 RDS 예약 인스턴스로 한 달 $15쯤 더 줄일 수 있다.
둘 다 약정이라 대표가 결정한다.

## 처음 한 번

1. **AWS 로그인**: 터미널에서 직접 `aws configure` 를 실행해 액세스 키를 넣는다. 지역은 `ap-northeast-2`.
   키는 채팅이나 파일에 붙여 넣지 않는다.
2. **배포** (백엔드 폴더에서):
   ```
   .venv\Scripts\python deploy\aws\deploy.py
   ```
3. **소셜 로그인 주소 등록**(2026-10-14 등록함):
   - 카카오: [앱] → [플랫폼 키] → [REST API 키] → 카카오 로그인 리다이렉트 URI
     `https://app.talkto.co.kr/api/v1/auth/oauth/kakao/callback`
   - 구글: Google 인증 플랫폼 → 클라이언트 → 웹 클라이언트 → 승인된 리디렉션 URI
     `https://app.talkto.co.kr/api/v1/auth/oauth/google/callback`
   - 구글: [대상]의 게시 상태가 '테스트'면 테스트 사용자만 로그인할 수 있다
4. **관리자 로그인**: 이메일은 로컬 `.env` 의 `BOOTSTRAP_ADMIN_EMAIL` 이고, 비밀번호는 새로 만들어진다.
   본인 PowerShell에서만 아래 명령으로 확인한다. 비밀번호만 나온다(다른 비밀값은 화면에 안 나온다).
   ```
   (aws ssm get-parameter --name /talkto/prod/generated --with-decryption --query Parameter.Value --output text --region ap-northeast-2 | ConvertFrom-Json).ADMIN_PASSWORD
   ```

## 고친 뒤 다시 올리기

| 바꾼 것 | 명령 |
|---|---|
| 백엔드·AI 코드, `.env` | `.venv\Scripts\python deploy\aws\deploy.py server` |
| 웹 화면 | `.venv\Scripts\python deploy\aws\deploy.py web` |
| 랜딩페이지 | `.venv\Scripts\python deploy\aws\deploy.py landing` |
| 서버 + 웹 + 랜딩 | `.venv\Scripts\python deploy\aws\deploy.py app` |
| 서버 구성(`stack.yaml`) | `.venv\Scripts\python deploy\aws\deploy.py infra` |
| 상태만 보기 | `.venv\Scripts\python deploy\aws\deploy.py status` |
| GitHub 배포 역할(`ci.yaml`) | `.venv\Scripts\python deploy\aws\deploy.py ci` |

- **GitHub 배포 버튼**: 팀원은 talkto-deploy 레포의 Actions → 배포로 코드만 올린다(server·web·landing·app). 운영 설정은 건드리지 않는다. 만들기·쓰기·거두기는 [`ci/README.md`](ci/README.md).
- **돌고 있는 작업 확인**: `server` 는 컨테이너를 바꾸기 전에 서버에서 돌고 있는 만들기 작업(받아쓰기·목소리 만들기 등)을 센다. 있으면 아무것도 올리지 않고 멈춘다. 꼭 지금 올려야 하면 아래 한 줄로 그 한 번만 force 로 돌린다(그 작업은 끊긴다). 끝의 `Remove-Item` 이 force 를 바로 끄므로, 같은 창에서 다음에 돌리는 배포는 다시 확인한다.
  ```
  $env:TALKTO_FORCE='1'; .venv\Scripts\python deploy\aws\deploy.py server; Remove-Item Env:TALKTO_FORCE
  ```
  도중에 Ctrl+C 로 멈췄으면 `Remove-Item Env:TALKTO_FORCE` 를 따로 한 번 돌린다(아니면 창을 닫는다).

- 운영 설정은 로컬 `.env` 를 바탕으로 한다. 단, 코드가 실제로 읽는 이름만 올라간다. 개인 키나 로컬 전용 값은 운영으로 새지 않는다.
- 스크립트가 덮어쓰는 값은 아래와 같다.
  - 주소, DB, S3
  - 결제 끄기(`PAYMENTS_OPEN=false`)
  - 결제 전 대화 상한: 대화 상대마다 한 달 200번
  - 결제 전 계정마다 대화 상대 3명까지
  - 요청 횟수 제한 켜기
  - 기본 목소리 없음(로컬 기본 목소리는 가족 목소리라서)
- 새 버전이 상태 확인을 통과하지 못하면 이전 버전으로 자동으로 되돌린다.
- 서버를 바꾸는 동안 1~2분은 접속이 끊긴다. 그때 돌던 목소리 만들기 작업도 끊기므로, 사용자가 적은 시간에 올린다.
- AI 서버 `.env` 에 `OPENAI_API_KEY`·`FISH_AUDIO_API_KEY`·`ELEVENLABS_API_KEY`·`MINIMAX_API_KEY` 가 없으면 올리지 않고 멈춘다.
- 받아쓰기 모델: AI 서버 `.env` 에 `ELEVENLABS_STT_MODEL=scribe_v1`(ElevenLabs 가 내린 모델)이 남아 있으면 `scribe_v2` 로 바꿔 올린다. 로컬 `.env` 도 고쳐 둔다.
- **예전 목소리를 Fish 로 다시 만들기**(`VOICE_RETRAIN_ENABLED`)는 기본으로 꺼져 있다. 다시 만들면 예전 목소리를 그 회사에서 지워 되돌릴 수 없다.
  1. `server` 를 올린 뒤 관리자 대화 상대 하나로 목소리 만들기 → 대답 듣기(mp3 가 나오는지) → 다시 만들기 → 지우기를 해 본다.
  2. 다시 만든 목소리를 귀로 들어 본다(수치가 아니라 귀로 정한다).
  3. 괜찮으면 로컬 `.env` 에 `VOICE_RETRAIN_ENABLED=true` 를 넣고 `server` 를 다시 올린다. 그 뒤로 10분마다 하나씩, 동의한 분의 것만 다시 만든다.
- **데이터를 고치는 배포 전 RDS 스냅샷**: 켤 때 고치는 SQL(예: 똑 닮은 목소리 요금제 정리)은 다시 올려도 되돌릴 수 없어, 그 전에 스냅샷을 손으로 찍어 둔다. 손으로 찍은 스냅샷은 7일 자동 백업과 달리 저절로 지워지지 않는다. 배포를 확인한 뒤 **7일 안에 지운다**(처리방침에서 지웠다고 한 정보, 예를 들어 예전 상담 전화번호가 그 안에 남는다). 지울 때까지는 접근을 관리자로만 두고, 지운 날을 적어 둔다.
- 서버 기록은 AWS 콘솔의 CloudWatch → 로그 그룹 `/talkto/backend`, `/talkto/ai` 에서 본다.
- 패키지 버전은 `requirements.lock` 으로 고정한다(백엔드·AI 각각). 올리려면 로컬에서 버전을 바꿔 시험한 뒤 그 파일을 고친다.

### 서버 통째로 바꾸기 (OS 이미지·ARM↔x86·디스크 크기)

CloudFront VPC 오리진은 배포에 붙어 있는 동안 고칠 수 없다. 그래서 `deploy.py infra` 가 이런 변경을 알아채면 순서를 바꿔 처리한다.

1. VPC 오리진을 잠시 떼고 예전 길(공개 주소)로 돌린다.
2. 서버를 바꾼다.
3. VPC 오리진을 다시 붙인다.
4. 서버 입구를 다시 좁힌다.

새 서버에는 컨테이너가 없다. 그래서 `deploy.py` 전체(또는 `infra` 다음 `server`)를 이어서 돌려야 하고, 그동안 API는 멈춘다(15~25분).

## 도메인 (talkto.co.kr)

- **DNS:** 가비아에서 산 도메인의 네임서버를 Route 53 으로 옮겼다. 레코드는 가비아가 아니라 Route 53 에 있다. `deploy/aws/domain.yaml`(us-east-1)이 호스팅 영역과 인증서를 만들고, `stack.yaml` 이 CloudFront 별칭 레코드를 만든다.
- **주소:**

  | 주소 | 하는 일 |
  |---|---|
  | `app.talkto.co.kr` | 앱. 모든 응답에 `x-robots-tag: noindex`(검색에 싣지 않는다) |
  | `www.app.talkto.co.kr` | 앱으로 넘김(301) |
  | `talkto.co.kr` | 랜딩페이지(S3 `_landing/`). `/index.html`→`/`, `/terms`·`/terms/`→`/terms.html` 처럼 같은 페이지의 다른 주소는 한 번에 대표 주소로 넘김(301). 랜딩에 없는 주소는 앱으로(301) |
  | `www.talkto.co.kr` | 같은 규칙으로 `talkto.co.kr` 대표 주소로 넘김(301) |
  | 옛 주소 `xxxx.cloudfront.net` | 지금은 앱을 그대로 보여 준다(검색 제외 표시는 붙는다) |

  새 DNS 가 국내 통신사까지 다 퍼진 뒤(하루 이틀) 옛 주소도 넘기려면 아래를 돌린다.
  ```
  $env:TALKTO_LEGACY_REDIRECT='on'; .venv\Scripts\python deploy\aws\deploy.py infra
  ```
- **웹 빌드:** API 를 상대 경로(`/api/v1`)로 부른다. 어느 주소로 열어도 같은 주소의 API 를 쓴다.
- **소셜 로그인 콜백:** 앱 주소 기준(`PUBLIC_BASE_URL=https://app.talkto.co.kr`)이라 소셜 로그인은 앱 주소에서만 된다. 이메일 로그인은 어느 주소에서나 된다.
- **랜딩페이지를 올릴 때:**
  1. `stack.yaml` 의 CloudFront `Aliases` 와 `SiteRecords` 에서 `talkto.co.kr`·`www` 를 뺀다.
  2. 랜딩페이지 쪽 CloudFront 나 호스팅에 붙인다. 같은 주소를 두 CloudFront 에 동시에 붙일 수 없어서, 빼고 붙이는 순서가 중요하다.
  3. 인증서는 네 주소를 이미 다 덮는다.
- **다른 레코드(메일 등):** 메일(MX)·검증용 TXT 같은 레코드도 Route 53 에 넣는다. 가비아 DNS 에 넣으면 효과가 없다.

## 운영 알림 메일 (SES)

새 기억 인터뷰 신청이 오면 운영 메일함으로 한 통 보낸다(`app/services/mailer.py`, 개인 정보 없이 '1건'만). 보내는 주소는 `noreply@talkto.co.kr`.
`stack.yaml` 이 SES 도메인 확인(DKIM CNAME 셋, Route 53)과 서버 역할의 `ses:SendEmail`(그 도메인, 보내는 주소 `noreply@<도메인>` 하나만 — `ses:FromAddress` 조건)을 만든다. 받는 메일함은 만들지 않는다. 그래서 `MAIL_FROM` 은 꼭 `noreply@<도메인>` 이어야 한다(다른 주소면 SES 가 거절하고 실패로 기록만 남는다).

1. `.venv\Scripts\python deploy\aws\deploy.py infra` — SES 도메인과 DKIM 레코드가 생긴다.
2. AWS 콘솔 → SES(서울 ap-northeast-2) → Identities → `talkto.co.kr` 가 **Verified** 가 될 때까지 기다린다(보통 몇 분, 길면 72시간).
3. 같은 화면의 Account dashboard 에서 **샌드박스 해제(Request production access)** 를 신청한다. 샌드박스에서는 확인한 주소로만 보낼 수 있어 gmail 로 가지 않는다(그동안 보내기는 실패로 기록만 남고 신청은 그대로 된다).
4. 백엔드 로컬 `.env` 에 `OPS_EMAIL_TO=asktalktoteam@gmail.com`, `MAIL_FROM=noreply@talkto.co.kr` 를 넣고 `.venv\Scripts\python deploy\aws\deploy.py server` 로 올린다. 둘 중 하나라도 비면 보내지 않는다.
5. 관리자 계정으로 시험 대화 상대에 기억 인터뷰를 신청해 메일함·디스코드에 '새 기억 인터뷰 신청 1건'이 오는지 본다. 그 신청은 관리자 화면에서 취소한다.

## 지키는 것

- **서버 입구**
  - 서버는 우리 CloudFront의 VPC 오리진에서 오는 요청만 받는다. 보안 그룹이 `CloudFront-VPCOrigins-Service-SG` 만 허용한다.
  - CloudFront → 서버 구간은 인터넷이 아니라 AWS 안쪽 길로 다닌다.
  - SSH는 열지 않고, 관리는 SSM으로 한다.
- **데이터 보관**
  - DB는 인터넷에 열지 않는다. 서버만 접속할 수 있다.
  - 녹음 S3는 공개하지 않는다. 서버가 만든, 시간이 지나면 만료되는 서명 주소로만 열린다. 모든 버킷은 https만 받는다.
- **비밀값**
  - API 키와 비밀번호는 Parameter Store(SecureString)에 둔다.
  - 서버에 받은 설정 파일은 컨테이너를 켠 직후 지운다.
  - DB 비밀번호는 CloudFormation에도 넘기지만, 화면과 기록에는 가려진다.
- **컨테이너**
  - AI 컨테이너와 이미지 빌드는 서버의 IAM 역할(S3·설정 접근 권한)을 읽지 못하게 iptables로 막는다.
  - 두 컨테이너 모두 루트가 아니다. 권한(capability)은 모두 빼고, 권한 상승을 막고, 메모리와 프로세스 수에 상한을 둔다.
- **CloudFront**: 응답에 보안 헤더(HSTS, nosniff, 다른 사이트 안에 넣기 금지, referrer)를 붙인다.
- **배포 묶음**
  - 필요한 코드 폴더만 담는다.
  - `input/`, `output/`, `.oss/`, `voice_lab`, 녹음 파일, `.env` 는 들어가지 않는다.
  - git 이 무시하는 파일(`.gitignore`)도 담지 않는다. 묶음은 작업 폴더에서 만들어서, 커밋하지 못하는 파일이 남아 있어도 실려 가지 않게.
- **결제**: 열지 않는다(`PAYMENTS_OPEN=false`, 토스 키 제외). 변호사 회신과 사업자 정리가 끝난 뒤에 연다.

## 알아 둘 점

- **새 AWS 계정**은 CloudFront를 처음 만들 때 "계정 확인이 필요하다"는 오류가 날 수 있다.
  그때는 AWS Support에 확인을 요청한 뒤 다시 돌린다.
- **처음 만들다 실패하면** 만든 것을 그대로 두고 멈춘다(DB 지우기 방지 때문). 원인을 고치고 같은 명령을 다시 돌리면 이어서 만든다.
- **요금 알림**: AWS 콘솔 → Billing → Budgets 에서 월 예산(예: $70)을 만들고, 80%·100%에서 이메일을 받게 해 두기를 권한다(무료).

## 모두 지우기 (되돌릴 수 없음)

0. GitHub 배포 역할이 있으면 먼저 지운다: `aws cloudformation delete-stack --stack-name talkto-ci --region ap-northeast-2`
1. RDS 콘솔에서 `talkto-db` 의 "삭제 방지"를 끈다.
2. 웹·배포 버킷을 비운다(비어 있지 않으면 스택이 못 지운다). `<계정번호>` 는 12자리 AWS 계정 번호다.
   ```
   aws s3 rm s3://talkto-web-<계정번호> --recursive
   aws s3 rm s3://talkto-deploy-<계정번호> --recursive
   ```
3. `aws cloudformation delete-stack --stack-name talkto-prod --region ap-northeast-2` 를 실행한다.
   - CloudFront가 만든 보안 그룹은 조금 늦게 지워진다. 그래서 처음엔 VPC나 보안 그룹 지우기가 실패할 수 있다.
   - 몇 분 뒤 같은 명령을 다시 돌린다.
4. DB는 마지막 스냅샷을 남기고 지워진다. 녹음 S3(`talkto-media-…`)는 실수로 지워지지 않게 남는다. 따로 비우고 지운다.
5. Parameter Store의 `/talkto/prod/*` 3개를 지운다.
6. 도메인을 그만 쓸 때만 us-east-1 의 `talkto-domain` 스택을 지운다.
   - 호스팅 영역은 실수로 지워지지 않게 남는다. Route 53 에서 따로 지운다.
   - 지우기 전에 가비아 네임서버를 되돌려 놓는다.
