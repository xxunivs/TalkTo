# GitHub 배포 버튼 (talkto-deploy)

서버 열쇠가 없는 팀원(예: 이재헌님, GitHub `Loda1002`)도 GitHub 에서 버튼을 눌러 운영에 **코드**를 올릴 수 있게 하는 길이에요.

- 누구의 컴퓨터에도 AWS 열쇠나 `.env` 가 없어요. 버튼을 누르면 GitHub 가 그 작업 동안만(최대 1시간) AWS 임시 권한을 받아요(`talkto-github-deploy` 역할).
- 코드만 올려요. 운영 설정(Parameter Store `/talkto/prod/backend-env`·`ai-env`·`generated`)은 대표님이 마지막에 올린 그대로 둬요.
  - 이 역할로 그 값을 **직접** 읽을 수는 없어요.
  - 하지만 서버에 명령을 보내는 권한(배포가 그렇게 돌아요)이 있어서, 서버를 거치면 서버가 닿는 모든 것에 닿을 수 있어요. 운영 설정, DB·관리자 비밀번호, 가족 녹음까지요. 아래 [꼭 알아 둘 위험](#꼭-알아-둘-위험)을 꼭 읽어 주세요.
- 서버에서 누가 만들기 작업(받아쓰기·목소리 만들기 등)을 하고 있으면 서버를 바꾸지 않고 멈춰요. 그 작업이 끊기지 않게요.
- 디스코드 deploy 채널에 시작·완료·실패가 누른 사람(GitHub 아이디)과 함께 올라가요.

파일:

| 파일 | 하는 일 |
|---|---|
| `deploy/aws/ci/deploy.yml` | talkto-deploy 레포의 `.github/workflows/deploy.yml` 로 복사해 두는 배포 버튼 |
| `deploy/aws/ci.yaml` | AWS 역할(CloudFormation `talkto-ci` 스택). 대표님이 `deploy.py ci` 로 만들어요 |
| `deploy/aws/deploy.py` | 실제 배포. GitHub 에서는 `TALKTO_CI=1` 로 돌아요 |

## 배포하는 법

1. GitHub → **MediLux-TalkTo/talkto-deploy** → **Actions** → 왼쪽 **배포** → 오른쪽 **Run workflow**
2. 세 가지를 골라요.
   - **무엇을**(target)
     - `server`: 백엔드·AI 서버
     - `web`: 웹 화면(app.talkto.co.kr)
     - `landing`: 랜딩페이지(talkto.co.kr)
     - `app`: 셋 다(server → web → landing 순서)
   - **브랜치**(ref): 세 앱 레포(백엔드·AI·FE)에서 올릴 브랜치예요. 기본은 `feat/persona-builder` 예요. 세 레포에 같은 이름의 브랜치가 있어야 해요(FE 는 web·landing·app 일 때만 받아요).
   - **force**: 보통은 꺼 두세요. 켜면 돌고 있는 만들기 작업이 있어도 서버를 바꿔요. 그 작업은 끊겨요. 급한 고침을 꼭 지금 올려야 할 때만 켜요.
3. 초록색 **Run workflow** 를 눌러요.
   - 한 번에 하나만 돌아요. 누가 이미 배포 중이면 끝날 때까지 기다렸다가 이어서 돌아요.
   - 걸리는 시간: server 5~10분(서버를 새로 만든 뒤 처음엔 25분), web 5~10분, landing 1~2분이에요.
4. 결과는 디스코드 deploy 채널과 Actions 화면 맨 위 요약에 나와요.

실패하면 Actions 기록에서 **배포** 단계를 열고 맨 아래 줄을 보세요.

- "지금 서버에서 작업이 돌고 있어요"라고 나오면 몇 분 뒤 다시 누르면 돼요.
- 새 서버 버전이 확인을 통과하지 못하면 이전 버전으로 저절로 되돌려요. 서비스는 그대로 돌아요.

## GitHub 배포로는 못 하는 것

아래는 대표님 컴퓨터에서만 해요(`deploy/aws/README.md`).

- **운영 설정(`.env`) 바꾸기**: 새 API 키, 기능 켜고 끄기(`VOICE_RETRAIN_ENABLED`, `FISH_EMOTION` 등)
  - 대표님 컴퓨터에서 `deploy.py server` 로 올려요.
  - 새 코드가 새 설정을 필요로 하면 대표님이 먼저 그렇게 설정을 올리고, 그다음에 GitHub 배포를 써요.
- **인프라·도메인**: `deploy.py infra`, `deploy.py domain`, `deploy.py`(전체)
- **이 역할 만들기·고치기**: `deploy.py ci`

GitHub 배포에서 이것들을 고르면 이유를 알려 주고 바로 멈춰요.

## 처음 한 번 (대표님)

준비할 것:

- AWS 로그인(`aws configure`)과 GitHub CLI 로그인(`gh auth login`)이 된 대표님 컴퓨터
- PowerShell, 백엔드 폴더(`talkto_api_server`)

아래 `<...>` 는 직접 채워요. 비밀값은 화면에 나오지 않게 파이프(`|`)로만 넘겨요.

순서가 중요해요. 레포를 **먼저** 만들어야 해요. AWS 역할이 그 레포의 번호를 넣어 만들어지거든요.

**1. talkto-deploy 레포를 만들고 버튼 올리기**

```
cd $env:TEMP
gh repo create MediLux-TalkTo/talkto-deploy --private --clone --description "TalkTo 운영 배포 버튼"
cd talkto-deploy
New-Item -ItemType Directory -Force .github\workflows | Out-Null
Copy-Item "<백엔드 폴더>\deploy\aws\ci\deploy.yml" .github\workflows\deploy.yml
Copy-Item "<백엔드 폴더>\deploy\aws\ci\README.md" README.md
git add -A
git commit -m "배포 버튼"
git branch -M main
git push -u origin main
```

`push` 가 `workflow` 권한 때문에 막히면 `gh auth refresh -s workflow` 를 한 번 돌리고 `git push -u origin main` 을 다시 해요.

Actions 기록은 90일 동안 남는 게 기본이에요. 서버 오류 때는 서버 기록 몇 줄이 실려서 7일만 남겨요.

```
gh api -X PUT repos/MediLux-TalkTo/talkto-deploy/actions/permissions/artifact-and-log-retention -F days=7
```

화면에서는 talkto-deploy → Settings → Actions → General → **Artifact and log retention** 이에요.

**2. AWS 역할 만들기** (백엔드 폴더에서)

```
.venv\Scripts\python deploy\aws\deploy.py ci
```

- `gh` 로 talkto-deploy 의 조직 번호·레포 번호를 읽어 역할에 넣어요. 레포가 아직 없거나 공개 레포면 이유를 알려 주고 멈춰요.
- 계정에 GitHub OIDC 공급자가 없으면 그것도 같이 만들어요.
- 끝에 두 줄이 나와요.
  - **받는 작업**: `repo:MediLux-TalkTo@<조직 번호>/talkto-deploy@<레포 번호>:ref:refs/heads/main`. 이 역할을 쓸 수 있는 GitHub 작업의 이름표예요(talkto-deploy 의 main 브랜치에서 돈 작업만).
  - **역할**: `arn:aws:iam::<계정번호>:role/talkto-github-deploy`. 3번에 써요.
- 나중에 `deploy.py infra` 로 서버를 바꾸면 역할도 저절로 고쳐요. 저절로 고치지 못했다고 나오면 `deploy.py ci` 를 다시 돌려요.
- talkto-deploy 레포를 지우고 같은 이름으로 다시 만들면 레포 번호가 바뀌어요. 그때도 `deploy.py ci` 를 다시 돌려요.

**3. 역할 주소**

```
gh variable set AWS_DEPLOY_ROLE_ARN -R MediLux-TalkTo/talkto-deploy --body "<2번 역할 주소>"
```

**4. 세 레포의 읽기 전용 열쇠(deploy key)**

열쇠를 잠깐 만들어 GitHub 에 넣고 바로 지워요. `ssh-keygen` 이 암호를 물으면 아무것도 치지 말고 엔터를 두 번 눌러요.

```
$k = "$env:TEMP\talkto-deploy-keys"
New-Item -ItemType Directory -Force $k | Out-Null
foreach ($pair in @("BE=talkto_api_server", "AI=TalkTo_PersonaAI_AI", "FE=TalkTo_PersonaAI_FE")) {
  $name, $repo = $pair.Split("=")
  ssh-keygen -t ed25519 -q -C "talkto-deploy-$name" -f "$k\$name"
  gh repo deploy-key add "$k\$name.pub" -R "MediLux-TalkTo/$repo" -t "talkto-deploy (read only)"
  Get-Content -Raw "$k\$name" | gh secret set "$($name)_DEPLOY_KEY" -R MediLux-TalkTo/talkto-deploy
}
Remove-Item -Recurse -Force $k
```

`--allow-write` 를 붙이지 않아서 세 열쇠 모두 읽기만 할 수 있어요.

**5. 디스코드 알림 주소** (백엔드 폴더에서)

로컬 `.env` 의 값을 그대로 넘겨요. 화면에는 안 나와요.

```
(Select-String -Path .env -Pattern '^DISCORD_DEPLOY_WEBHOOK_URL=(.+)$').Matches[0].Groups[1].Value.Trim().Trim('"', "'") | gh secret set DISCORD_DEPLOY_WEBHOOK_URL -R MediLux-TalkTo/talkto-deploy
```

**6. 이재헌님 초대(쓰기 권한 = 배포 버튼)**

```
gh api -X PUT repos/MediLux-TalkTo/talkto-deploy/collaborators/Loda1002 -f permission=push
```

이재헌님이 GitHub 알림이나 메일에서 초대를 수락하면 버튼이 보여요.

**7. 시험**

1. 대표님이 먼저 `landing` 으로 한 번 눌러 봐요(1~2분, 가장 안전해요).
2. 그다음 `server` 를 눌러 디스코드에 완료가 오는지 봐요.

**AWS 권한 받기** 단계가 `Not authorized to perform sts:AssumeRoleWithWebIdentity` 로 실패해도 아직 아무것도 바뀌지 않은 상태예요. 바로 아래 **권한 받기 실패 원인 보기** 단계에 `GitHub 가 보낸 sub: ...` 줄이 나와요. 2번에서 `deploy.py ci` 가 찍은 **받는 작업**과 견줘 보세요.

- 2026년 7월 15일 뒤에 만든 레포는 이름 옆에 번호가 붙어요: `repo:MediLux-TalkTo@<조직 번호>/talkto-deploy@<레포 번호>:ref:refs/heads/main`. 번호가 다르면(레포를 지우고 다시 만든 경우 등) `deploy.py ci` 를 다시 돌려요.
- 끝이 `:ref:refs/heads/main` 이 아니면 main 이 아닌 브랜치에서 돌린 거예요. main 에서 다시 돌려요(워크플로는 main 이 아니면 아예 돌지 않아요).
- 번호 없는 옛 모양(`repo:MediLux-TalkTo/talkto-deploy:...`)으로 바꾸면 안 돼요. 이 레포의 작업은 그 모양으로 오지 않아서 똑같이 실패해요.

### 요금제 때문에 알아 둘 점

MediLux-TalkTo 조직은 GitHub Free 예요. Free 의 비공개 레포에서는 아래 기능을 못 써요.

- 환경(environment) 보호 규칙(승인자 등)
- 환경 비밀값
- 브랜치 보호

그래서 열쇠·알림 주소는 레포 비밀값으로 두고, 역할은 talkto-deploy 레포(번호까지)의 **main 브랜치**로만 묶어요. 다른 브랜치에서 워크플로를 고쳐 돌려도 역할을 받지 못해요. 다만 브랜치 보호가 없어서 main 에 바로 올릴 수 있는 사람(쓰기 권한)은 배포할 수 있어요. 결과적으로 **talkto-deploy 에 쓰기 권한이 있는 사람 = 배포할 수 있는 사람**이에요.

GitHub Team(한 사람에 월 $4)으로 올리면 main 브랜치 보호(대표님 승인 뒤에만 워크플로 바꾸기)를 걸 수 있어요.

GitHub Actions 는 비공개 레포에 한 달 2,000분까지 무료예요(배포 한 번에 5~15분).

## 권한 거두기

- **한 사람만 멈추기**: 바로 버튼을 못 눌러요.
  ```
  gh api -X DELETE repos/MediLux-TalkTo/talkto-deploy/collaborators/Loda1002
  ```
  - 세 앱 레포의 읽기 권한은 따로예요. 필요하면 각 레포에서 따로 거둬요.
  - 그 사람이 쓰기 권한이 있던 동안 워크플로를 고쳐 열쇠를 꺼냈을 수도 있어요. 믿음이 깨져서 거두는 거라면 4번을 다시 해서 열쇠를 바꾸고, 옛 열쇠는 지워요. 서버를 거쳐 비밀값을 꺼냈을 수도 있어서, 운영 API 키·관리자 비밀번호도 바꾸는 게 안전해요.
    ```
    gh repo deploy-key list -R MediLux-TalkTo/<레포>
    gh repo deploy-key delete <번호> -R MediLux-TalkTo/<레포>
    ```
- **버튼만 잠시 끄기**:
  ```
  gh workflow disable deploy.yml -R MediLux-TalkTo/talkto-deploy
  ```
  다시 켤 때는 `enable` 이에요.
- **모두 멈추기(급할 때)**: AWS 역할을 지워요. 그 순간부터 GitHub 은 AWS 에 아무것도 못 해요.
  ```
  aws cloudformation delete-stack --stack-name talkto-ci --region ap-northeast-2
  ```
  다시 쓰려면 `deploy.py ci` 를 돌려요.

## 꼭 알아 둘 위험

- **코드를 올릴 수 있다는 건 서버를 통째로 맡기는 것과 같아요.**
  - 올린 코드는 서버 안에서 돌면서 운영 DB·녹음·API 키를 모두 쓸 수 있어요.
  - 이 역할이 비밀값을 직접 읽지 못하게 막아 두었어요. 하지만 서버에 명령을 보내는 권한(배포가 그렇게 돌아요)이 있어요. 그 명령은 서버 안에서 서버의 권한으로 돌아요.
  - 그래서 마음먹으면 서버를 거쳐 아래를 모두 꺼낼 수 있어요.
    - 운영 설정(`/talkto/prod/backend-env`·`ai-env`): API 키 등
    - `/talkto/prod/generated`: DB 비밀번호, 관리자 비밀번호, JWT 비밀값
    - 녹음 버킷의 가족 녹음, 운영 DB 의 사용자 정보
- 그래서 talkto-deploy 쓰기 권한은 **서버를 맡길 만큼 믿는 사람에게만** 주세요.
  - 쓰기 권한이 있으면 워크플로 파일도 고칠 수 있어요(Free 요금제라 막을 수 없어요). 다만 그렇게 해도 배포로 이미 할 수 있는 일보다 더 할 수 있는 건 없어요.
- **GitHub 계정이 털려도 같은 위험이에요.** 쓰기 권한을 준 사람은 2단계 인증을 꼭 켜게 해 주세요. 조직 설정에서 모두에게 2단계 인증을 요구할 수도 있어요: Settings → Authentication security → Require two-factor authentication.
- **AI 열쇠는 레포 전체를 읽을 수 있어요.** AI 레포 기록에는 가족 자료가 있어요. 배포는 서버에 올릴 파일만 받고 가족 자료의 내용은 받지 않아요. 그래도 열쇠 자체는 레포 전체를 읽을 수 있어서, 사람이 바뀌면 열쇠도 바꿔요.
- **Actions 기록은 talkto-deploy 를 볼 수 있는 사람 모두에게 보여요.** 서버 오류 때는 서버 기록 몇 줄이 실려요. 키·비밀번호 이름이 붙은 값은 가리지만, 사용자 아이디 같은 값이 섞일 수 있어요. 그래서 이 레포는 꼭 비공개로 두고, 기록은 7일만 남겨요(1번).
