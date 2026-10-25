"""TalkTo 를 AWS(서울)에 배포한다. 이 컴퓨터의 AWS 로그인(aws configure)을 쓴다.

백엔드 가상환경의 파이썬으로 돌린다(boto3 가 들어 있다):

    .venv\\Scripts\\python deploy\\aws\\deploy.py            # 전부(인프라 → 서버 → 웹)
    .venv\\Scripts\\python deploy\\aws\\deploy.py infra      # CloudFormation 만
    .venv\\Scripts\\python deploy\\aws\\deploy.py server     # 백엔드·AI 서버만 새로 빌드·교체
    .venv\\Scripts\\python deploy\\aws\\deploy.py web        # 웹 화면만 다시 올리기
    .venv\\Scripts\\python deploy\\aws\\deploy.py landing    # 랜딩페이지(talkto.co.kr)만
    .venv\\Scripts\\python deploy\\aws\\deploy.py app        # server + web + landing
    .venv\\Scripts\\python deploy\\aws\\deploy.py status     # 주소·상태만 보기
    .venv\\Scripts\\python deploy\\aws\\deploy.py ci         # GitHub 배포 역할(talkto-ci 스택)

비밀값(API 키·DB 비밀번호 등)은 화면·파일에 남기지 않고 Parameter Store(SecureString)로만 보낸다.
로컬 .env(백엔드·AI) 값을 바탕으로 운영 값(주소·DB·S3)을 덮어써서 올린다.
서버를 바꾸기 전에 돌고 있는 만들기 작업(PersonaJob RUNNING)이 있으면 멈춘다
(TALKTO_FORCE=1 이면 그대로 바꾼다. 그 작업은 끊긴다).

GitHub 배포(TALKTO_CI=1, talkto-deploy 레포의 '배포' 버튼, deploy/aws/ci/README.md):
    server·web·landing·app 만 돈다. 코드만 올리고 운영 설정(Parameter Store)은 대표님이 마지막에
    올린 그대로 둔다. 로컬 .env 를 읽지 않고, 비밀값을 만들거나 올리지 않는다(역할에 그 권한도
    없다). infra·domain·all·ci 와 설정 바꾸기는 대표님 컴퓨터에서만 돌린다.

환경 변수(선택):
    TALKTO_INSTANCE_TYPE   서버 크기(기본 t4g.medium)
    TALKTO_API_ORIGIN      vpc(기본: CloudFront → 서버를 AWS 안쪽 길로) 또는 public(예전 방식)
    TALKTO_DOMAIN          사이트 도메인(기본 talkto.co.kr, 앱은 app.<도메인>)
    TALKTO_LEGACY_REDIRECT on 이면 옛 주소(xxxx.cloudfront.net)도 앱 주소로 넘긴다(기본 off).
    TALKTO_FORCE           1 이면 돌고 있는 만들기 작업이 있어도 서버를 바꾼다(그 작업은 끊긴다).
    TALKTO_CI              1 이면 GitHub 배포(위). GitHub Actions 만 켠다.
    DISCORD_DEPLOY_WEBHOOK_URL  (CI 만) 디스코드 deploy 채널. 로컬은 백엔드 .env 에서 읽는다.
    GITHUB_TRIGGERING_ACTOR (CI 만) 누른 사람(다시 돌리기 포함, 없으면 GITHUB_ACTOR).
                           디스코드 알림에 붙인다.
    TALKTO_GITHUB_ORG_ID·TALKTO_GITHUB_REPO_ID  ('ci' 만) talkto-deploy 의 조직·레포 번호.
                           보통은 비워 둔다(gh 로 읽는다). gh 를 못 쓸 때만 준다.
"""

from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import re
import secrets
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError, WaiterError

PROJECT = "talkto"
STACK = f"{PROJECT}-prod"
# 도메인 DNS·인증서 스택. CloudFront 인증서는 us-east-1 에만 둘 수 있다.
DOMAIN_STACK = f"{PROJECT}-domain"
DOMAIN_REGION = "us-east-1"
SITE_DOMAIN = os.environ.get("TALKTO_DOMAIN", "talkto.co.kr").strip().lower().rstrip(".")
HERE = Path(__file__).resolve().parent
BE = HERE.parents[1]  # talkto_api_server
ROOT = BE.parent  # TalkTo
AI = ROOT / "TalkTo_PersonaAI_AI"
FE = ROOT / "TalkTo_PersonaAI_FE"
WEB_BUILD = FE / "build" / "web-prod"  # 운영 웹 빌드(로컬 build/web 과 섞이지 않게)
AMI_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-{arch}"
VPC_ORIGIN_SG = "CloudFront-VPCOrigins-Service-SG*"
DEFAULT_INSTANCE = "t4g.medium"
# 관리자 비밀번호만 꺼내 보는 PowerShell 명령(다른 비밀값은 화면에 안 나온다)
ADMIN_PASSWORD_COMMAND = (
    f"(aws ssm get-parameter --name /{PROJECT}/prod/generated --with-decryption "
    "--query Parameter.Value --output text --region {region} | ConvertFrom-Json).ADMIN_PASSWORD"
)
# 예전(Secrets Manager) 이름. Parameter Store 로 옮긴 뒤 7일 유예를 두고 지운다.
LEGACY_SECRETS = ("generated", "backend-env", "ai-env")
# 서버 묶음·이미지 이름(TAG) 시각. 로컬·GitHub(UTC) 어디서 돌려도 한국 시각으로 둔다
# (서버 정리가 이름 순서로 최근 것을 고른다).
KST = timezone(timedelta(hours=9))

STEPS = ("all", "infra", "server", "web", "landing", "app", "status", "domain", "ci")
# GitHub 배포(TALKTO_CI=1)가 돌릴 수 있는 것. 코드만 올린다.
CI_STEPS = ("server", "web", "landing", "app")
# GitHub 배포 역할(deploy/aws/ci.yaml). 대표님이 'ci' 로 만든다.
CI_STACK = f"{PROJECT}-ci"
CI_GITHUB = {
    "GitHubOrg": "MediLux-TalkTo",
    "GitHubRepo": "talkto-deploy",
    "GitHubBranch": "main",
}
GITHUB_OIDC_HOST = "token.actions.githubusercontent.com"
# 2026-07-15 뒤에 만든 레포는 OIDC sub 에 조직·레포 번호가 붙는다(바꿀 수 없다):
#   repo:MediLux-TalkTo@<조직 번호>/talkto-deploy@<레포 번호>:ref:refs/heads/main
# 레포를 지우고 같은 이름으로 다시 만들면 번호가 바뀌어 'ci' 를 다시 돌려야 한다.
CI_GITHUB_IDS = {"GitHubOrgId": "TALKTO_GITHUB_ORG_ID", "GitHubRepoId": "TALKTO_GITHUB_REPO_ID"}
# 이 출력이 바뀌면(서버 교체 등) 역할도 고친다
CI_OUTPUTS = ("ArtifactBucket", "WebBucket", "DistributionId", "InstanceId")


def in_ci() -> bool:
    """GitHub Actions 배포(talkto-deploy)인지. 코드만 올리고 운영 설정·비밀값은 건드리지 않는다."""
    return os.environ.get("TALKTO_CI") == "1"


def forced() -> bool:
    return os.environ.get("TALKTO_FORCE") == "1"


def check_step(step: str) -> None:
    if step not in STEPS:
        raise SystemExit(__doc__)
    if in_ci() and step not in CI_STEPS:
        raise SystemExit(
            f"'{step}' 는 GitHub 배포에서 돌릴 수 없어요(server·web·landing·app 만). "
            "인프라·도메인·운영 설정(.env) 바꾸기는 대표님 컴퓨터에서만 돌려요."
        )


# 소스 묶음에 넣을 것만(허용 목록). 가족 녹음(input/·output/·.oss/)·.env·테스트는 애초에 안 담는다.
BE_INCLUDE = [
    "Dockerfile",
    ".dockerignore",
    "pyproject.toml",
    "README.md",
    "requirements.lock",
    "app",
]
AI_INCLUDE = [
    "Dockerfile",
    ".dockerignore",
    "pyproject.toml",
    "README.md",
    "requirements.lock",
    "main.py",
    "app",
    "pipeline",
    "orchestrator",
    "prompts",
]
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", "voice_lab", "input", "output", ".oss"}
SKIP_SUFFIX = {".pyc", ".pyo", ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".db", ".sqlite"}

# 웹 파일 형식. 윈도우는 레지스트리 때문에 .js 를 text/plain 으로 올리기도 해서 직접 정한다.
WEB_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".json": "application/json",
    ".wasm": "application/wasm",
    ".css": "text/css",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".webp": "image/webp",
    ".xml": "application/xml",
    ".txt": "text/plain; charset=utf-8",
}

# 고칠 수 없는 상태 → 사람이 손봐야 하는 것
STUCK = {
    "ROLLBACK_FAILED": "되돌리기가 실패했어요(대개 DB 지우기 방지 때문). "
    "RDS 콘솔에서 talkto-db 지우기 방지를 끄고 "
    "CloudFormation 콘솔에서 스택을 지운 뒤 다시 돌려 주세요.",
    "UPDATE_ROLLBACK_FAILED": "변경 되돌리기가 실패했어요. "
    "CloudFormation 콘솔에서 '업데이트 롤백 계속'을 누른 뒤 다시 돌려 주세요.",
    "DELETE_FAILED": "스택 지우기가 실패했어요. "
    "README 의 '모두 지우기'를 따라 버킷을 비우고 다시 지워 주세요.",
}


def log(msg: str) -> None:
    print(msg, flush=True)


class Aws:
    def __init__(self) -> None:
        self.session = boto3.session.Session()
        self.region = self.session.region_name or "ap-northeast-2"

    def client(self, name: str):
        return self.session.client(name, region_name=self.region)


# ── 설정 파일 ─────────────────────────────────────────────
def read_env(path: Path) -> dict[str, str]:
    """.env 를 읽는다(따옴표·줄 끝 주석 처리). 값은 어디에도 찍지 않는다."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if value[:1] in {'"', "'"}:
            q = value[0]
            end = value.find(q, 1)
            value = value[1:end] if end > 0 else value[1:]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        out[key] = value
    return out


def render_env(values: dict[str, str | None]) -> str:
    """도커 --env-file 형식(따옴표 없이 KEY=VALUE). None 은 뺀다."""
    lines = []
    for k in sorted(values):
        v = values[k]
        if v is None:
            continue
        if "\n" in v or "\r" in v:
            raise SystemExit(f"{k} 값에 줄바꿈이 있어 올릴 수 없어요.")
        lines.append(f"{k}={v}")
    return "\n".join(lines) + "\n"


def param_name(name: str) -> str:
    return f"/{PROJECT}/prod/{name}"


def put_param(aws: Aws, name: str, value: str) -> None:
    # 4KB 이하는 무료(Standard). 넘으면 자동으로 Advanced(월 $0.05)로 올라간다.
    aws.client("ssm").put_parameter(
        Name=param_name(name),
        Value=value,
        Type="SecureString",
        Overwrite=True,
        Tier="Intelligent-Tiering",
    )


def get_param(aws: Aws, name: str) -> str | None:
    ssm = aws.client("ssm")
    try:
        return ssm.get_parameter(Name=param_name(name), WithDecryption=True)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return None


def legacy_secret(aws: Aws, name: str) -> str | None:
    sm = aws.client("secretsmanager")
    try:
        return sm.get_secret_value(SecretId=f"{PROJECT}/prod/{name}")["SecretString"]
    except sm.exceptions.ResourceNotFoundException:
        return None
    except ClientError as e:
        # 지우기 예약된 비밀값(InvalidRequestException)은 없는 것으로 본다
        if e.response.get("Error", {}).get("Code") == "InvalidRequestException":
            return None
        raise


def generated_secrets(aws: Aws) -> dict[str, str]:
    """처음 한 번 만들어 두고 계속 쓴다(JWT·AI 토큰·DB·관리자 비밀번호).

    '아직 없음'일 때만 새로 만든다. 그 밖의 오류에 새로 만들면 DB 비밀번호가 바뀌어
    서버가 DB 에 못 붙는다. 예전 Secrets Manager 값이 있으면 그대로 옮긴다.
    """
    raw = get_param(aws, "generated")
    migrated = False
    if raw is None:
        raw = legacy_secret(aws, "generated")
        migrated = raw is not None
    data = json.loads(raw) if raw else {}
    missing = [
        k
        for k in ("JWT_SECRET", "AI_SERVER_TOKEN", "DB_PASSWORD", "ADMIN_PASSWORD")
        if not data.get(k)
    ]
    for key in missing:
        data[key] = secrets.token_urlsafe(32)
    if missing or migrated:
        put_param(aws, "generated", json.dumps(data))
        if migrated:
            log("  비밀값을 Secrets Manager 에서 Parameter Store 로 그대로 옮겼어요.")
        if missing:
            log("  비밀값을 새로 만들어 Parameter Store 에 두었어요(화면에는 안 찍어요).")
    return data


def retire_legacy_secrets(aws: Aws) -> None:
    """Parameter Store 로 옮긴 뒤 예전 비밀값(월 $0.40씩)을 7일 유예로 지운다.

    그 전엔 되살릴 수 있다.
    """
    sm = aws.client("secretsmanager")
    retired = []
    for name in LEGACY_SECRETS:
        sid = f"{PROJECT}/prod/{name}"
        try:
            info = sm.describe_secret(SecretId=sid)
        except sm.exceptions.ResourceNotFoundException:
            continue
        if info.get("DeletedDate"):
            continue
        sm.delete_secret(SecretId=sid, RecoveryWindowInDays=7)
        retired.append(sid)
    if retired:
        log(
            f"  예전 Secrets Manager 비밀값 {len(retired)}개를 7일 뒤 지워지게 했어요"
            "(그 전엔 되살릴 수 있어요)."
        )


# ── 스택 ─────────────────────────────────────────────────
def stack(aws: Aws) -> dict | None:
    try:
        return aws.client("cloudformation").describe_stacks(StackName=STACK)["Stacks"][0]
    except ClientError as e:
        if "does not exist" in str(e):
            return None
        raise


def outputs(aws: Aws) -> dict[str, str]:
    st = stack(aws)
    return {o["OutputKey"]: o["OutputValue"] for o in (st or {}).get("Outputs", [])}


def failure_reasons(aws: Aws, name: str = STACK) -> list[str]:
    try:
        events = aws.client("cloudformation").describe_stack_events(StackName=name)["StackEvents"]
    except ClientError:
        return []
    return [
        f"{e['LogicalResourceId']}: {e.get('ResourceStatusReason', '')}"
        for e in events[:40]
        if "FAILED" in e["ResourceStatus"]
    ][:8]


def arch_of(aws: Aws, instance_type: str) -> str:
    info = aws.client("ec2").describe_instance_types(InstanceTypes=[instance_type])[
        "InstanceTypes"
    ][0]
    return "arm64" if "arm64" in info["ProcessorInfo"]["SupportedArchitectures"] else "x86_64"


def server_ami(aws: Aws, out: dict[str, str], instance_type: str) -> str:
    """지금 서버의 이미지를 그대로 쓴다.

    서버가 없거나 아키텍처가 바뀌면 그 아키텍처의 최신 Amazon Linux 2023.
    """
    arch = arch_of(aws, instance_type)
    if out.get("InstanceId"):
        res = aws.client("ec2").describe_instances(InstanceIds=[out["InstanceId"]])["Reservations"]
        if res and res[0]["Instances"] and res[0]["Instances"][0]["Architecture"] == arch:
            return res[0]["Instances"][0]["ImageId"]
    return aws.client("ssm").get_parameter(Name=AMI_PARAM.format(arch=arch))["Parameter"]["Value"]


def vpc_origin_sg(aws: Aws, vpc_id: str | None) -> str:
    """CloudFront 가 VPC 오리진을 만들며 이 VPC 에 둔 관리형 보안 그룹. 아직 없으면 'none'."""
    if not vpc_id:
        return "none"
    for _ in range(10):
        groups = aws.client("ec2").describe_security_groups(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "group-name", "Values": [VPC_ORIGIN_SG]},
            ]
        )["SecurityGroups"]
        if len(groups) <= 1:
            return groups[0]["GroupId"] if groups else "none"
        time.sleep(30)  # 옛 VPC 오리진의 보안 그룹은 CloudFront 가 조금 뒤에 지운다
    # 여기서 멈추면 새 서버에 컨테이너가 안 올라가 서비스가 멈춘 채로 남는다.
    # 입구 좁히기만 다음으로 미룬다.
    log("  경고: CloudFront VPC 오리진 보안 그룹이 여러 개라 입구 좁히기를 다음 infra 로 미뤄요.")
    return "none"


def domain_cf(aws: Aws):
    return aws.session.client("cloudformation", region_name=DOMAIN_REGION)


def domain_outputs(aws: Aws) -> dict[str, str]:
    """도메인 스택 출력(HostedZoneId·NameServers·CertificateArn). 없으면 빈 dict."""
    if not SITE_DOMAIN:
        return {}
    try:
        st = domain_cf(aws).describe_stacks(StackName=DOMAIN_STACK)["Stacks"][0]
    except ClientError as e:
        if "does not exist" in str(e):
            return {}
        raise
    status = st["StackStatus"]
    if status.endswith("_IN_PROGRESS") or status.endswith("_FAILED"):
        raise SystemExit(f"도메인 스택이 {status} 상태예요. 끝난 뒤 다시 돌려 주세요.")
    if status == "ROLLBACK_COMPLETE":
        return {}  # 처음 만들다 실패해 빈 스택(다시 만든다)
    # CREATE_COMPLETE·UPDATE_COMPLETE·UPDATE_ROLLBACK_COMPLETE 는 모두 쓸 수 있는 상태다
    return {o["OutputKey"]: o["OutputValue"] for o in st.get("Outputs", [])}


def public_ns(domain: str) -> set[str]:
    """인터넷에서 보이는 이 도메인의 네임서버(구글·클라우드플레어 DNS 로 확인)."""
    found: set[str] = set()
    for url, headers in (
        (f"https://dns.google/resolve?name={domain}&type=NS", {}),
        (
            f"https://cloudflare-dns.com/dns-query?name={domain}&type=NS",
            {"accept": "application/dns-json"},
        ),
    ):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                answers = json.loads(resp.read()).get("Answer", [])
            found |= {a["data"].rstrip(".").lower() for a in answers if a.get("type") == 2}
        except Exception:  # noqa: BLE001
            continue
    return found


def apply_domain_stack(aws: Aws, want_certificate: bool) -> dict[str, str]:
    cf = domain_cf(aws)
    params = [
        {"ParameterKey": "DomainName", "ParameterValue": SITE_DOMAIN},
        {
            "ParameterKey": "CreateCertificate",
            "ParameterValue": "true" if want_certificate else "false",
        },
    ]
    body = (HERE / "domain.yaml").read_text(encoding="utf-8")
    try:
        cf.describe_stacks(StackName=DOMAIN_STACK)
        exists = True
    except ClientError:
        exists = False
    try:
        if exists:
            cf.update_stack(StackName=DOMAIN_STACK, TemplateBody=body, Parameters=params)
            waiter = "stack_update_complete"
        else:
            cf.create_stack(StackName=DOMAIN_STACK, TemplateBody=body, Parameters=params)
            waiter = "stack_create_complete"
    except ClientError as e:
        if "No updates are to be performed" in str(e):
            return domain_outputs(aws)
        raise
    try:
        # 인증서 확인(DNS)은 보통 5~30분 걸린다
        cf.get_waiter(waiter).wait(
            StackName=DOMAIN_STACK, WaiterConfig={"Delay": 20, "MaxAttempts": 180}
        )
    except WaiterError:
        events = cf.describe_stack_events(StackName=DOMAIN_STACK)["StackEvents"][:20]
        reasons = [
            f"{e['LogicalResourceId']}: {e.get('ResourceStatusReason', '')}"
            for e in events
            if "FAILED" in e["ResourceStatus"]
        ]
        raise SystemExit(
            "도메인 스택 실패:\n  " + "\n  ".join(reasons[:6] or ["(콘솔에서 확인)"])
        ) from None
    return domain_outputs(aws)


def step_domain(aws: Aws) -> bool:
    """도메인 DNS(Route 53)·인증서를 준비한다. 다 됐으면 True.

    네임서버를 아직 안 바꿨으면 바꿀 곳을 알려 주고 False(사이트는 지금 주소로 계속 돈다).
    """
    if not SITE_DOMAIN:
        return True
    log(f"[도메인] {SITE_DOMAIN}")
    out = domain_outputs(aws)
    if out.get("CertificateArn"):
        log("  도메인 준비됨(DNS·인증서).")
        return True
    if not out:
        if domain_stack_status(aws) == "ROLLBACK_COMPLETE":
            domain_cf(aws).delete_stack(StackName=DOMAIN_STACK)
            domain_cf(aws).get_waiter("stack_delete_complete").wait(StackName=DOMAIN_STACK)
        log("  Route 53 호스팅 영역을 만들어요(한 달 $0.50).")
        out = apply_domain_stack(aws, want_certificate=False)
    expected = {n.rstrip(".").lower() for n in out["NameServers"].split(",")}
    if public_ns(SITE_DOMAIN) != expected:
        log("  아직 네임서버가 바뀌지 않았어요.")
        log("  가비아 → 도메인 관리 → 네임서버 설정에서 아래 4개로 바꿔 주세요:")
        for i, ns in enumerate(sorted(expected), 1):
            log(f"    {i}차: {ns}")
        log("  바꾼 뒤(보통 몇 분~1시간) 다시 돌리면 인증서·주소 연결을 이어서 해요.")
        log("  그때까진 지금 주소로 계속 돌아요.")
        return False
    log("  네임서버가 바뀐 걸 확인했어요. https 인증서를 받아요(5~30분).")
    apply_domain_stack(aws, want_certificate=True)
    log("  도메인 준비 완료(DNS·인증서).")
    return True


def domain_stack_status(aws: Aws) -> str | None:
    try:
        return domain_cf(aws).describe_stacks(StackName=DOMAIN_STACK)["Stacks"][0]["StackStatus"]
    except ClientError as e:
        if "does not exist" in str(e):
            return None
        raise


def site_domain_params(aws: Aws) -> dict[str, str]:
    """인증서까지 준비됐으면 CloudFront 에 도메인을 붙인다. 아니면 비워서 기본 주소만 쓴다."""
    out = domain_outputs(aws)
    if not out.get("CertificateArn"):
        st = stack(aws)
        live = {
            p["ParameterKey"]: p.get("ParameterValue", "") for p in (st or {}).get("Parameters", [])
        }
        if live.get("SiteDomain") and os.environ.get("TALKTO_DETACH_DOMAIN") != "1":
            raise SystemExit(
                f"지금 {live['SiteDomain']} 를 쓰는 중인데 인증서 정보를 못 읽었어요. "
                "도메인 스택을 확인해 주세요(정말 떼려면 TALKTO_DETACH_DOMAIN=1)."
            )
        return {
            "SiteDomain": "",
            "SiteHostedZoneId": "",
            "SiteCertificateArn": "",
            "LegacyHostRedirect": "off",
        }
    return {
        "SiteDomain": SITE_DOMAIN,
        "SiteHostedZoneId": out["HostedZoneId"],
        "SiteCertificateArn": out["CertificateArn"],
        # 옛 주소(cloudfront.net)도 앱 주소로 넘길지. 새 DNS 가 다 퍼진 뒤 on 으로 켠다.
        "LegacyHostRedirect": os.environ.get("TALKTO_LEGACY_REDIRECT", "off"),
    }


class Infra:
    """CloudFormation 변경 계획(change set)을 만들어 보고 적용한다."""

    def __init__(
        self, aws: Aws, gen: dict[str, str], instance_type: str, ami: str, prefix: str
    ) -> None:
        self.aws = aws
        self.cf = aws.client("cloudformation")
        self.base = {
            "Project": PROJECT,
            "InstanceType": instance_type,
            "DBPassword": gen["DB_PASSWORD"],
            "CloudFrontPrefixListId": prefix,
            "ServerAmiId": ami,
            **site_domain_params(aws),
        }

    def plan(self, mode: str, sg: str) -> dict | None:
        st = stack(self.aws)
        kind = "CREATE" if st is None or st["StackStatus"] == "REVIEW_IN_PROGRESS" else "UPDATE"
        never_completed = st is None or st["StackStatus"] in ("REVIEW_IN_PROGRESS", "CREATE_FAILED")
        params = {**self.base, "ApiOriginMode": mode, "VpcOriginSecurityGroupId": sg}
        # 처음 만들다 실패해도 되돌리지 않는다(DB 지우기 방지 때문에 되돌리기가 실패한다).
        # 고쳐서 다시 돌린다.
        extra = {"OnStackFailure": "DO_NOTHING"} if kind == "CREATE" else {}
        cs = self.cf.create_change_set(
            StackName=STACK,
            ChangeSetName=f"{PROJECT}-{datetime.now():%Y%m%d%H%M%S}",
            ChangeSetType=kind,
            TemplateBody=(HERE / "stack.yaml").read_text(encoding="utf-8"),
            Parameters=[{"ParameterKey": k, "ParameterValue": v} for k, v in params.items()],
            Capabilities=["CAPABILITY_IAM"],
            **extra,
        )
        try:
            self.cf.get_waiter("change_set_create_complete").wait(
                ChangeSetName=cs["Id"], WaiterConfig={"Delay": 5, "MaxAttempts": 120}
            )
        except WaiterError:
            info = self.cf.describe_change_set(ChangeSetName=cs["Id"])
            reason = info.get("StatusReason", "")
            self.cf.delete_change_set(ChangeSetName=cs["Id"])
            if "didn't contain changes" in reason or "No updates" in reason:
                return None
            raise SystemExit(f"CloudFormation 변경 계획 실패: {reason}") from None
        changes, token = [], None
        while True:
            page = self.cf.describe_change_set(
                ChangeSetName=cs["Id"], **({"NextToken": token} if token else {})
            )
            changes += [c["ResourceChange"] for c in page.get("Changes", [])]
            token = page.get("NextToken")
            if not token:
                break
        return {
            "id": cs["Id"],
            "kind": kind,
            "changes": changes,
            "never_completed": never_completed,
        }

    @staticmethod
    def replaces_server(plan: dict) -> bool:
        return any(
            c["LogicalResourceId"] == "Server" and c.get("Replacement") in ("True", "Conditional")
            for c in plan["changes"]
        )

    def discard(self, plan: dict) -> None:
        self.cf.delete_change_set(ChangeSetName=plan["id"])

    def apply(self, plan: dict | None) -> None:
        if plan is None:
            log("  바뀐 것이 없어요.")
            return
        for c in plan["changes"]:
            rep = " (통째로 교체)" if c.get("Replacement") == "True" else ""
            log(f"  - {c['Action']} {c['LogicalResourceId']}{rep}")
        extra = (
            {"DisableRollback": True}
            if plan["kind"] == "UPDATE" and plan["never_completed"]
            else {}
        )
        self.cf.execute_change_set(ChangeSetName=plan["id"], **extra)
        waiter = "stack_create_complete" if plan["kind"] == "CREATE" else "stack_update_complete"
        try:
            self.cf.get_waiter(waiter).wait(
                StackName=STACK, WaiterConfig={"Delay": 20, "MaxAttempts": 180}
            )
        except WaiterError:
            reasons = failure_reasons(self.aws) or [
                "(CloudFormation 콘솔에서 이벤트를 확인해 주세요)"
            ]
            raise SystemExit(
                "CloudFormation 실패:\n  "
                + "\n  ".join(reasons)
                + "\n원인을 고치고 같은 명령을 다시 돌려 주세요."
            ) from None


def step_infra(aws: Aws, gen: dict[str, str], instance_type: str) -> dict[str, str]:
    mode = os.environ.get("TALKTO_API_ORIGIN", "vpc")
    if mode not in ("vpc", "public"):
        raise SystemExit("TALKTO_API_ORIGIN 은 vpc 또는 public 이어야 해요.")
    st = stack(aws)
    status = st["StackStatus"] if st else None
    if status in STUCK:
        raise SystemExit(f"스택 상태 {status}: {STUCK[status]}")
    if status and status.endswith("_IN_PROGRESS") and status != "REVIEW_IN_PROGRESS":
        raise SystemExit(f"스택이 다른 작업 중이에요({status}). 끝난 뒤 다시 돌려 주세요.")
    if status == "ROLLBACK_COMPLETE":
        # 처음 만들다 실패해 아무것도 안 남은 상태는 고칠 수 없어 지우고 다시 만든다
        log("  지난번 실패한 빈 스택을 지우고 다시 만들어요.")
        cf = aws.client("cloudformation")
        cf.delete_stack(StackName=STACK)
        cf.get_waiter("stack_delete_complete").wait(StackName=STACK)
        st = None
    out = outputs(aws) if st else {}
    log(
        "[인프라] CloudFormation 배포"
        + ("" if st else " (처음이라 15~25분: DB·CloudFront 가 오래 걸려요)")
    )
    prefix = aws.client("ec2").describe_managed_prefix_lists(
        Filters=[
            {
                "Name": "prefix-list-name",
                "Values": ["com.amazonaws.global.cloudfront.origin-facing"],
            }
        ]
    )["PrefixLists"][0]["PrefixListId"]
    infra = Infra(aws, gen, instance_type, server_ami(aws, out, instance_type), prefix)
    current_mode = out.get("ApiOriginMode", "public")
    sg = vpc_origin_sg(aws, out.get("VpcId")) if mode == "vpc" and current_mode == "vpc" else "none"

    plan = infra.plan(mode, sg)
    if plan and current_mode == "vpc" and infra.replaces_server(plan):
        # 배포에 붙은 VPC 오리진은 고칠 수 없어(서버가 바뀌면 실패) 잠시 예전 길로 돌린 뒤 바꾼다
        infra.discard(plan)
        log("  서버를 통째로 바꾸는 변경이라, VPC 오리진을 잠시 떼고 바꾼 뒤 다시 붙여요.")
        infra.apply(infra.plan("public", "none"))
        plan = infra.plan(mode, "none") if mode == "vpc" else None
    infra.apply(plan)

    out = outputs(aws)
    if mode == "vpc":
        # 2단계: 서버 입구를 우리 CloudFront(VPC 오리진) 전용으로 좁힌다
        sg2 = vpc_origin_sg(aws, out.get("VpcId"))
        if sg2 != "none" and sg2 != sg:
            log("  서버 입구를 우리 CloudFront 전용으로 좁혀요.")
            infra.apply(infra.plan("vpc", sg2))
            out = outputs(aws)
        elif sg2 == "none":
            log("  경고: VPC 오리진 보안 그룹을 아직 못 찾았어요. 다음 infra 때 다시 좁혀요.")
    log(f"  주소: {out['SiteUrl']}")
    return out


# ── 서버 ─────────────────────────────────────────────────
def git_env() -> dict[str, str]:
    """git 이 빠진 파일을 원격에서 몰래 받아 오지 않게(부분 체크아웃)."""
    return {**os.environ, "GIT_NO_LAZY_FETCH": "1"}


def git_ignored(src: Path, include: list[str]) -> set[str]:
    """담을 폴더(include) 안에서 git 이 무시하는(.gitignore) 파일. 커밋할 수 없는 파일은 묶음에도
    담지 않는다.

    묶음은 작업 폴더에서 만들어서, 무시된 채 남은 파일(예전 서류 PDF 등)이 그대로 실려 갈 수 있다.
    git 을 못 부르면 빈 집합(나머지 규칙 SKIP_PARTS·SKIP_SUFFIX·.env 는 그대로 걸러진다).

    GitHub 배포의 AI 레포는 include 만 받은 부분 체크아웃(sparse, blob:none)이다. 그 밖의 파일
    (가족 녹음 등)은 내용을 받지 않았고, git 이 그걸 읽으려고 GitHub 에서 받아 오지 않게
    GIT_NO_LAZY_FETCH 를 켠다(받은 코드엔 커밋된 파일만 있어 무시된 파일도 없다).
    """
    args = ["ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", *include]
    try:
        r = subprocess.run(
            ["git", "-C", str(src), *args], capture_output=True, check=True, env=git_env()
        )
    except (OSError, subprocess.CalledProcessError):
        log(f"  (git 으로 무시된 파일을 확인하지 못했어요: {src.name})")
        return set()
    return {name for name in r.stdout.decode("utf-8", "replace").split("\0") if name}


def pack(src: Path, include: list[str]) -> bytes:
    ignored = git_ignored(src, include)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in include:
            base = src / name
            if not base.exists():
                raise SystemExit(f"{base} 가 없어요.")
            paths = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
            for p in paths:
                rel = p.relative_to(src)
                if SKIP_PARTS & set(rel.parts) or p.suffix.lower() in SKIP_SUFFIX:
                    continue
                if rel.name.startswith(".env") and rel.name != ".env.example":
                    continue
                if rel.as_posix() in ignored:
                    continue
                info = tar.gettarinfo(str(p), arcname=rel.as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o644
                with p.open("rb") as fh:
                    tar.addfile(info, fh)
    return buf.getvalue()


def backend_keys() -> set[str]:
    """백엔드가 실제로 읽는 설정 이름(Settings 필드). 이것 말고는 운영에 올리지 않는다."""
    sys.path.insert(0, str(BE))
    from app.core.config import Settings

    return {name.upper() for name in Settings.model_fields}


def ai_keys() -> set[str]:
    """AI 서버가 실제로 읽는 설정 이름(app/setting.py 의 os.getenv). 이것 말고는 올리지 않는다."""
    text = (AI / "app" / "setting.py").read_text(encoding="utf-8")
    return set(re.findall(r'os\.(?:getenv|environ\.get)\(\s*"([A-Z0-9_]+)"', text))


def allowed_only(local: dict[str, str], overrides: dict[str, str | None], keys: set[str], who: str):
    """로컬 .env 에서 코드가 읽는 값만 고르고 운영 값을 덮어쓴다.

    개인 키·로컬 전용 값이 운영으로 새지 않게 한다.
    """
    unknown = [k for k in overrides if k not in keys]
    if unknown:
        raise SystemExit(f"{who} 설정에 없는 이름이에요: {', '.join(unknown)}")
    env: dict[str, str | None] = {k: v for k, v in local.items() if k in keys}
    env.update(overrides)
    return env


def toss_test_keys(local: dict[str, str]) -> dict[str, str | None]:
    """결제 시험 계정(PAYMENTS_TEST_EMAILS)을 정해 두었고 토스 키가 둘 다 테스트 키(test_)일 때만
    운영에 올린다. 실제 키(live_)는 결제를 열 때 따로 정한다(billing.payments_open)."""
    client = (local.get("TOSS_CLIENT_KEY") or "").strip()
    secret = (local.get("TOSS_SECRET_KEY") or "").strip()
    testers = (local.get("PAYMENTS_TEST_EMAILS") or "").strip()
    if testers and client.startswith("test_") and secret.startswith("test_"):
        return {"TOSS_CLIENT_KEY": client, "TOSS_SECRET_KEY": secret}
    if (client or secret) and testers:
        log("  토스 키가 둘 다 테스트 키(test_)가 아니라 운영에 올리지 않아요(결제는 닫혀 있어요)")
    return {"TOSS_CLIENT_KEY": None, "TOSS_SECRET_KEY": None}


def stale_plan_keys(local: dict[str, str]) -> list[str]:
    """로컬 .env 의 요금 설정(PLAN_*) 가운데 코드 기본값(app/core/config.py)과 다른 이름.

    운영은 요금을 따로 정하지 않고 코드 기본값을 쓴다. 그런데 로컬 .env 에 예전 .env.example 의
    PLAN_* 줄이 남아 있으면 allowed_only 가 그 값을 운영으로 올려 예전 요금이 남는다. 이름만
    돌려준다(값은 찍지 않는다)."""
    sys.path.insert(0, str(BE))
    from app.core.config import Settings

    fields = Settings.model_fields
    stale = []
    for key, value in local.items():
        field = fields.get(key.lower())
        if not key.startswith("PLAN_") or field is None:
            continue
        default = "" if field.default is None else str(field.default)
        if str(value).strip() != default:
            stale.append(key)
    return sorted(stale)


def kakao_channel(local: dict[str, str]) -> str | None:
    """운영에 올릴 고객센터 카카오톡 채널 주소(KAKAO_CHANNEL_CHAT_URL).

    로컬 .env 값을 support.channel_url 로 거른다(http 는 https 로). 비었거나 카카오톡 채널 주소가
    아니면(다른 곳·오타) 코드 기본값(config.py — 대표님이 주신 TalkTo 채널)을 올린다. 값을 그대로
    올리면 운영에서 단추가 소리 없이 사라지기 때문이다. 값은 찍지 않는다."""
    sys.path.insert(0, str(BE))
    from app.core.config import Settings
    from app.services.support import channel_url

    raw = (local.get("KAKAO_CHANNEL_CHAT_URL") or "").strip()
    url = channel_url(raw)
    if raw and url is None:
        log(
            "  로컬 백엔드 .env 의 KAKAO_CHANNEL_CHAT_URL 이 카카오톡 채널 주소"
            "(https://pf.kakao.com/_채널/chat)가 아니라 기본 TalkTo 채널을 올려요."
        )
    return url or channel_url(Settings.model_fields["kakao_channel_chat_url"].default)


def backend_env(out: dict[str, str], gen: dict[str, str], region: str) -> dict[str, str | None]:
    local = read_env(BE / ".env")
    stale = stale_plan_keys(local)
    if stale:
        log(
            "  로컬 백엔드 .env 의 요금 설정이 코드 기본값과 달라 그 값이 운영에 올라가요: "
            f"{', '.join(stale)}. 새 요금(config.py)을 쓰려면 그 줄을 지워 주세요."
        )
    site = out["SiteUrl"].rstrip("/")
    overrides: dict[str, str | None] = {
        "ENVIRONMENT": "production",
        "DATABASE_URL": (
            f"postgresql+asyncpg://talkto:{gen['DB_PASSWORD']}@{out['DatabaseEndpoint']}"
            ":5432/talkto?sslmode=require"
        ),
        "JWT_SECRET": gen["JWT_SECRET"],
        "AI_SERVER_URL": f"http://{PROJECT}-ai:8001",
        "AI_SERVER_TOKEN": gen["AI_SERVER_TOKEN"],
        "STORAGE_BACKEND": "s3",
        "S3_BUCKET": out["MediaBucket"],
        "S3_REGION": region,
        "PUBLIC_BASE_URL": site,
        "FRONTEND_URL": site,
        "CORS_ORIGINS": json.dumps(
            list(dict.fromkeys([site, out.get("CloudFrontUrl", site).rstrip("/")]))
        ),
        "CORS_ALLOW_LOCALHOST": "false",
        "BOOTSTRAP_ADMIN_EMAIL": local.get("BOOTSTRAP_ADMIN_EMAIL") or "admin@talkto.dev",
        "BOOTSTRAP_ADMIN_PASSWORD": gen["ADMIN_PASSWORD"],
        # 결제는 아직 열지 않는다(마스터 리포트: 변호사 회신·사업자 정리 전 실결제 금지)
        "PAYMENTS_OPEN": "false",
        # 결제 전엔 대화 상대마다 30일마다 200번(동의문·랜딩에 이 값이 보인다).
        # 모르는 사람이 비용을 쓰지 못하게.
        "PRELAUNCH_CONVERSATIONS": local.get("PRELAUNCH_CONVERSATIONS") or "200",
        # 결제가 닫혀 있는 동안 토스 키는 테스트 키(test_)만 올린다. 결제 시험 계정
        # (PAYMENTS_TEST_EMAILS)이 돈이 나가지 않는 시험 결제창을 확인하는 데만 쓰인다.
        **toss_test_keys(local),
        "LEGAL_DATA_REGION": "AWS 서울 리전(대한민국)",
        "LOCAL_MEDIA_DIR": None,
        "LOCAL_MEDIA_PUBLIC_PATH": None,
        # 운영에선 늘 켠다(로컬에서 꺼 둔 값이 올라가지 않게)
        "RATE_LIMIT_ENABLED": "true",
        # 이메일 가입 때 메일을 받을 수 있는 주소인지 본다(오타·버리는 메일·없는 도메인)
        "EMAIL_DOMAIN_CHECK": "true",
        "PRELAUNCH_PERSONAS_PER_ACCOUNT": local.get("PRELAUNCH_PERSONAS_PER_ACCOUNT") or "3",
        # 고객센터 '카카오톡 채널로 문의하기' 단추(늘 채널 주소로 — kakao_channel)
        "KAKAO_CHANNEL_CHAT_URL": kakao_channel(local),
    }
    return allowed_only(local, overrides, backend_keys(), "백엔드")


def ai_env(gen: dict[str, str]) -> dict[str, str | None]:
    overrides: dict[str, str | None] = {
        "APP_ENV": "production",
        "AI_SERVER_TOKEN": gen["AI_SERVER_TOKEN"],
        # 로컬이 DEBUG 여도 운영은 INFO(DEBUG 는 openai 가 대화 본문을 찍는다)
        "LOG_LEVEL": "INFO",
        "TEMP_DIR": None,  # 컨테이너 기본 /tmp 를 쓴다(로컬 경로를 가져가지 않게)
        "ORCHESTRATOR_TRACE_DIR": None,  # 대화 원문이 남는 기록은 운영에서 끈다
        "ORCHESTRATOR_PERSONA_DIR": None,
        "SPEAKER_MODEL_DIR": None,  # 이미지 안의 /opt/ecapa 를 쓴다
        # 녹음 한 개 2GB(128kbps 로 약 37시간)까지. 디스크가 차지 않게 막는 상한이다.
        "MAX_AUDIO_BYTES": str(2 * 1024**3),
        # 로컬 기본 목소리는 가족 한 분의 복제 목소리다. 운영에서는 목소리 없이 부르면 오류가 낫다.
        "ELEVENLABS_DEFAULT_VOICE_ID": None,
    }
    env = allowed_only(read_env(AI / ".env"), overrides, ai_keys(), "AI 서버")
    # 받아쓰기 scribe_v1 은 ElevenLabs 가 내린 모델이다. 로컬 .env 에 남아 있어도
    # scribe_v2 로 올린다.
    if (env.get("ELEVENLABS_STT_MODEL") or "").strip() == "scribe_v1":
        log(
            "  ELEVENLABS_STT_MODEL=scribe_v1 은 내린 모델이라 scribe_v2 로 올려요"
            "(로컬 .env 도 고쳐 주세요)."
        )
        env["ELEVENLABS_STT_MODEL"] = "scribe_v2"
    # Fish = 새 목소리 만들기·대답 읽기. ElevenLabs = 녹음 받아쓰기 + 다시 만들기 전 예전 목소리,
    # MiniMax = 다시 만들기 전 예전 목소리(모두 Fish 로 옮기기 전까지는 있어야 한다)
    for key in ("OPENAI_API_KEY", "FISH_AUDIO_API_KEY", "ELEVENLABS_API_KEY", "MINIMAX_API_KEY"):
        if not env.get(key):
            raise SystemExit(f"AI 서버 .env 에 {key} 가 없어요.")
    # 가족 공유: 함께 쓰는 가족의 지침은 4층 조립기(layered) 기준으로 만든다(기억 통찰 없이 조립).
    # 막지는 않고 알리기만 한다(값은 찍지 않는다).
    if (env.get("PERSONA_PROMPT_BUILDER") or "").strip() != "layered":
        log("  PERSONA_PROMPT_BUILDER 가 layered 가 아니에요. 멤버 지침은 layered 기준이에요.")
        # 백엔드는 layered 기준으로 성격 정리(reflection)를 부르지 않는다(PERSONA_REFLECTION=false).
        log("  legacy 로 돌리면 백엔드 .env 에 PERSONA_REFLECTION=true 도 넣어 주세요.")
    # 대답 목소리 감정(FISH_EMOTION)은 대표님 청취 판정 뒤에만 운영에서 켠다.
    # 로컬에서 들어 보려고 켠 값이 그대로 올라가지 않게 알린다(막지는 않는다).
    fish_emotion = (env.get("FISH_EMOTION") or "off").strip().lower() or "off"
    if fish_emotion != "off":
        log(
            f"  FISH_EMOTION={fish_emotion} 로 올라가요(대답 목소리에 감정 표시). "
            "청취 판정 전이면 AI 서버 .env 에서 off 로 두세요."
        )
    return env


def wait_ssm(aws: Aws, instance_id: str) -> None:
    ssm = aws.client("ssm")
    for _ in range(60):
        rows = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        )["InstanceInformationList"]
        if rows and rows[0].get("PingStatus") == "Online":
            return
        time.sleep(10)
    raise SystemExit("서버가 SSM 에 연결되지 않아요(10분). 서버가 켜져 있는지 확인해 주세요.")


def run_on_server(aws: Aws, instance_id: str, commands: list[str], timeout: int = 3600) -> str:
    ssm = aws.client("ssm")
    cid = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Comment="talkto deploy",
        TimeoutSeconds=600,
        Parameters={"commands": commands, "executionTimeout": [str(timeout)]},
    )["Command"]["CommandId"]
    deadline = time.time() + timeout + 300
    while time.time() < deadline:
        time.sleep(15)
        try:
            inv = ssm.get_command_invocation(CommandId=cid, InstanceId=instance_id)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if inv["Status"] in ("Pending", "InProgress", "Delayed"):
            continue
        stdout = inv.get("StandardOutputContent") or ""
        stderr = inv.get("StandardErrorContent") or ""
        if inv["Status"] != "Success":
            raise SystemExit(
                f"서버 작업 실패({inv['Status']}):\n{stdout[-2500:]}\n{stderr[-1000:]}"
            )
        return stdout
    raise SystemExit(f"서버 작업이 {timeout // 60}분 넘게 끝나지 않아요(명령 id {cid}).")


# 서버에서 돌고 있는 만들기 작업(PersonaJob RUNNING)을 종류별로 센다. 백엔드 컨테이너 안에서
# 운영 DB 로 돈다. 출력은 'TALKTO_JOBS {...}' 한 줄(ASCII 만: SSM 출력이 깨지지 않게).
JOBS_CHECK = """\
import asyncio, json
from sqlalchemy import func, select
from app.db.models import JobStatus, PersonaJob
from app.db.session import SessionLocal, engine

async def main():
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(PersonaJob.kind, func.count())
            .where(PersonaJob.status == JobStatus.RUNNING)
            .group_by(PersonaJob.kind)
        )).all()
    await engine.dispose()
    print("TALKTO_JOBS " + json.dumps({str(k): int(n) for k, n in rows}))

asyncio.run(main())
"""
JOB_NAMES = {
    "TRANSCRIBE": "받아쓰기",
    "ANALYZE": "목소리 나누기",
    "BUILD": "만들기",
    "VOICE": "목소리 만들기",
    "UPDATE": "다시 정리",
    "EXTEND": "녹음 더하기",
}


def running_jobs(aws: Aws, instance_id: str) -> dict[str, int] | None:
    """돌고 있는 만들기 작업 수(종류별). 백엔드가 꺼져 있으면 {}(끊길 작업이 없다). 못 세면 None."""
    script = base64.b64encode(JOBS_CHECK.encode()).decode()
    backend = f"{PROJECT}-backend"
    running = f"docker inspect -f '{{{{.State.Running}}}}' {backend} 2>/dev/null"
    result = run_on_server(
        aws,
        instance_id,
        [
            f"if [ \"$({running})\" != true ]; then echo 'TALKTO_JOBS none'; exit 0; fi",
            f"echo {script} | base64 -d | docker exec -i {backend} python - 2>&1 | tail -n 20",
        ],
        timeout=300,
    )
    for line in reversed(result.splitlines()):
        line = line.strip()
        if not line.startswith("TALKTO_JOBS "):
            continue
        value = line.removeprefix("TALKTO_JOBS ").strip()
        if value == "none":
            return {}
        try:
            return {str(k): int(n) for k, n in json.loads(value).items()}
        except (ValueError, TypeError, AttributeError):
            return None
    return None


def guard_running_jobs(aws: Aws, instance_id: str) -> None:
    """컨테이너를 바꾸면 돌던 작업(받아쓰기·목소리 만들기 등)이 끊긴다. 있으면 멈춘다.

    TALKTO_FORCE=1(GitHub 배포는 'force')이면 알리고 그대로 간다.
    """
    retry = (
        "GitHub 배포의 force 를 켜고 다시 돌려 주세요"
        if in_ci()
        else "TALKTO_FORCE=1 로 다시 돌려 주세요"
    )
    jobs = running_jobs(aws, instance_id)
    if jobs is None:
        if forced():
            log("  돌고 있는 작업을 세지 못했지만 force 라 그대로 바꿔요.")
            return
        raise SystemExit(
            f"서버에서 돌고 있는 작업을 확인하지 못했어요. 그래도 지금 바꿔야 하면 {retry}."
        )
    busy = {k: n for k, n in sorted(jobs.items()) if n > 0}
    if not busy:
        log("  돌고 있는 만들기 작업 없음")
        return
    listing = ", ".join(f"{JOB_NAMES.get(k, k)}({k}) {n}개" for k, n in busy.items())
    if forced():
        log(f"  돌고 있는 작업이 있지만({listing}) force 라 그대로 바꿔요(그 작업은 끊겨요).")
        return
    raise SystemExit(
        f"지금 서버에서 작업이 돌고 있어요: {listing}. 서버를 바꾸면 그 작업이 끊겨요. "
        f"끝난 뒤(보통 몇 분) 다시 돌려 주세요. 꼭 지금 올려야 하면 {retry}."
    )


def step_server(aws: Aws, out: dict[str, str], gen: dict[str, str] | None = None) -> None:
    """백엔드·AI 를 새로 빌드해 바꾼다.

    gen(비밀값)이 있으면 로컬 .env 로 운영 설정도 올린다. 없으면(GitHub 배포) 운영 설정은
    대표님이 마지막에 올린 그대로 두고 코드만 바꾼다.
    """
    tag = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    configs: dict[str, str] = {}
    if gen is not None:
        # 먼저 만들어 본다(로컬 .env 에 빠진 키가 있으면 서버를 건드리기 전에 멈춘다)
        configs = {
            "backend-env": render_env(backend_env(out, gen, aws.region)),
            "ai-env": render_env(ai_env(gen)),
        }
    log("[서버] 서버 연결 기다리기(SSM)")
    wait_ssm(aws, out["InstanceId"])
    log("[서버] 돌고 있는 만들기 작업 확인")
    guard_running_jobs(aws, out["InstanceId"])
    log(f"[서버] 소스 묶어 올리기 ({tag})")
    s3 = aws.client("s3")
    for name, src, inc in (("be", BE, BE_INCLUDE), ("ai", AI, AI_INCLUDE)):
        data = pack(src, inc)
        s3.put_object(Bucket=out["ArtifactBucket"], Key=f"{tag}/{name}.tar.gz", Body=data)
        log(f"  {name}: {len(data) / 1e6:.1f}MB")
    if configs:
        log("[서버] 운영 설정 올리기(Parameter Store)")
        for name, value in configs.items():
            put_param(aws, name, value)
    else:
        log("[서버] 운영 설정은 그대로 둬요(대표님이 마지막에 올린 값)")
    log("[서버] 빌드·교체(처음엔 15~25분: AI 이미지에 torch 가 들어가요)")
    script = base64.b64encode(
        (HERE / "server" / "run.sh").read_bytes().replace(b"\r\n", b"\n")
    ).decode()
    result = run_on_server(
        aws,
        out["InstanceId"],
        [
            "set -euo pipefail",
            "for i in $(seq 1 120); do [ -f /opt/talkto/.bootstrapped ] && break; sleep 5; done",
            "[ -f /opt/talkto/.bootstrapped ] || { echo '서버 준비가 덜 됐어요'; exit 1; }",
            f"echo {script} | base64 -d > /opt/talkto/run.sh",
            "chmod 700 /opt/talkto/run.sh",
            f"PROJECT={PROJECT} REGION={aws.region} TAG={tag} "
            f"ARTIFACT_BUCKET={out['ArtifactBucket']} /opt/talkto/run.sh",
        ],
    )
    log("  " + "\n  ".join(result.strip().splitlines()[-8:]))
    if gen is not None:
        # 예전 비밀값 정리는 대표님 배포에서만(GitHub 역할엔 Secrets Manager 권한이 없다)
        retire_legacy_secrets(aws)


# ── 웹 ───────────────────────────────────────────────────
def upload_web(aws: Aws, build: Path, bucket: str) -> int:
    """웹 빌드를 S3 에 올린다. 파일 형식·캐시를 직접 정하고, 빌드에 없는 옛 파일은 지운다."""
    s3 = aws.client("s3")
    keys = set()
    for p in sorted(build.rglob("*")):
        if not p.is_file():
            continue
        key = p.relative_to(build).as_posix()
        keys.add(key)
        ctype = (
            WEB_TYPES.get(p.suffix.lower())
            or mimetypes.guess_type(p.name)[0]
            or "application/octet-stream"
        )
        # 파일 이름에 버전이 없어서 모두 매번 확인(no-cache, 바뀌지 않았으면 304 로 짧게 끝난다).
        # 글꼴 중 아이콘 글꼴은 빌드마다 쓰는 아이콘만 남겨 새로 만들어진다. canvaskit/ 도 같다:
        # 웹이 화면 엔진을 gstatic 대신 여기서 읽는다(web/flutter_bootstrap.js canvasKitBaseUrl).
        # 하루 캐시로 두면 Flutter 를 올린 뒤 하루 동안 예전 canvaskit 과 새 main.dart.js 가 섞여
        # 빈 화면이 될 수 있다.
        cache = "no-cache"
        s3.upload_file(str(p), bucket, key, ExtraArgs={"ContentType": ctype, "CacheControl": cache})
    stale = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        stale += [
            {"Key": o["Key"]}
            for o in page.get("Contents", [])
            # 랜딩페이지(_landing/)는 따로 올린다(웹 빌드에 없어도 지우지 않는다)
            if o["Key"] not in keys and not o["Key"].startswith(LANDING_PREFIX)
        ]
    for i in range(0, len(stale), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": stale[i : i + 1000]})
    return len(keys)


LANDING_PREFIX = "_landing/"
LANDING = FE / "landing"


def step_landing(aws: Aws, out: dict[str, str]) -> str:
    """랜딩페이지(FE 레포 landing/)를 S3 _landing/ 에 올린다. talkto.co.kr 이 보여 준다."""
    if not (LANDING / "index.html").is_file():
        raise SystemExit(f"{LANDING} 에 index.html 이 없어요.")
    log("[랜딩] S3 에 올리기")
    s3 = aws.client("s3")
    bucket = out["WebBucket"]
    keys = set()
    for p in sorted(LANDING.rglob("*")):
        if not p.is_file():
            continue
        key = LANDING_PREFIX + p.relative_to(LANDING).as_posix()
        keys.add(key)
        ctype = (
            WEB_TYPES.get(p.suffix.lower())
            or mimetypes.guess_type(p.name)[0]
            or "application/octet-stream"
        )
        # 글·화면·글꼴은 매번 확인(글꼴은 랜딩 글에 맞춰 다시 만들어 이름이 같아도 글자가 달라진다),
        # 그림만 하루 캐시
        long_cache = p.suffix.lower() in (".webp", ".png", ".svg")
        cache = "public, max-age=86400" if long_cache else "no-cache"
        s3.upload_file(str(p), bucket, key, ExtraArgs={"ContentType": ctype, "CacheControl": cache})
    stale = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=LANDING_PREFIX):
        stale += [{"Key": o["Key"]} for o in page.get("Contents", []) if o["Key"] not in keys]
    for i in range(0, len(stale), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": stale[i : i + 1000]})
    aws.client("cloudfront").create_invalidation(
        DistributionId=out["DistributionId"],
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": ["/*"]},
            "CallerReference": f"landing-{time.time()}",
        },
    )
    log(f"  {len(keys)}개 파일 올림: https://{SITE_DOMAIN}/")
    return f"랜딩 {len(keys)}개 파일"


# 예전에 등록된 Flutter 오프라인 캐시 워커가 있으면 스스로 지운다(옛 화면이 남지 않게).
# 늘 이 이름으로 올려 둔다: 파일이 없어지면(404) 브라우저가 옛 워커를 그대로 둔다.
SW_CLEANUP = """'use strict';
// TalkTo: 예전 Flutter 오프라인 캐시 워커를 지운다. 이 파일은 계속 올려 둔다.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.filter((k) => k.startsWith('flutter-')).map((k) => caches.delete(k)));
    } catch (e) {}
    try { await self.registration.unregister(); } catch (e) {}
  })());
});
"""


# 빌드(flutter·pub 패키지 코드)에는 넘기지 않는 값: AWS 임시 열쇠·디스코드 주소·
# GitHub OIDC 토큰을 새로 받는 값(그걸로 배포 역할을 다시 받을 수 있다)
BUILD_ENV_DROP = ("AWS_", "DISCORD_", "ACTIONS_ID_TOKEN_REQUEST_")


def build_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.upper().startswith(BUILD_ENV_DROP)}


def step_web(aws: Aws, out: dict[str, str]) -> str:
    site = out["SiteUrl"].rstrip("/")
    # 웹 빌드 번호(version.json·화면). 열어 둔 탭이 새 버전을 알아채는 데 쓴다(숫자 14자리)
    build_id = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    log(f"[웹] Flutter 빌드 ({build_id})")
    flutter = "flutter.bat" if os.name == "nt" else "flutter"
    r = subprocess.run(
        # 운영 빌드는 따로 둔다(build/web 은 로컬 확인용 서버가 쓰는 로컬 주소 빌드다)
        [
            flutter,
            "build",
            "web",
            "--release",
            # 오프라인 캐시 워커를 만들지 않는다(배포 뒤 옛 화면이 남는 원인). 대신 SW_CLEANUP
            "--pwa-strategy=none",
            f"--build-number={build_id}",
            f"--dart-define=APP_BUILD={build_id}",
            # 웹과 API 가 같은 주소라 상대 경로로 빌드한다(앱 주소·옛 주소 어디서 열어도 된다)
            "--dart-define=API_BASE_URL=/api/v1",
            # 상대 경로로 준다: 절대 경로에 한글이 있으면 셰이더 컴파일러(impellerc)가 죽는다
            f"--output={WEB_BUILD.relative_to(FE).as_posix()}",
        ],
        cwd=FE,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=build_env(),
    )
    if r.returncode != 0:
        raise SystemExit("Flutter 빌드 실패:\n" + r.stdout[-2000:] + r.stderr[-2000:])
    (WEB_BUILD / "flutter_service_worker.js").write_text(SW_CLEANUP, encoding="utf-8")
    log("[웹] S3 에 올리기")
    count = upload_web(aws, WEB_BUILD, out["WebBucket"])
    aws.client("cloudfront").create_invalidation(
        DistributionId=out["DistributionId"],
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": ["/*"]},
            "CallerReference": str(time.time()),
        },
    )
    log(f"  {count}개 파일 올림: {site} (웹 빌드 {build_id})")
    return build_id


def smoke(out: dict[str, str]) -> bool:
    site = out["SiteUrl"].rstrip("/")
    ok = True
    for path in ("/health", "/api/v1/legal", "/"):
        try:
            with urllib.request.urlopen(site + path, timeout=30) as resp:
                log(f"  {path} → {resp.status}")
        except Exception as e:  # noqa: BLE001
            ok = False
            log(f"  {path} → 실패: {e}")
    return ok


# ── 디스코드 deploy 채널 ─────────────────────────────────
DISCORD_HOOKS = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")


def tell_discord(text: str) -> None:
    """배포 소식을 디스코드 deploy 채널로(로컬 .env 의 DISCORD_DEPLOY_WEBHOOK_URL).

    서버에는 올리지 않는 값이다(백엔드 설정이 아니다). 실패해도 배포는 그대로 간다.
    GitHub 배포는 로컬 .env 가 없어 환경 변수(GitHub 비밀값)에서 읽는다. 주소는 찍지 않는다.
    """
    if in_ci():
        url = os.environ.get("DISCORD_DEPLOY_WEBHOOK_URL", "")
    else:
        url = read_env(BE / ".env").get("DISCORD_DEPLOY_WEBHOOK_URL") or ""
    url = url.strip()
    if not url.startswith(DISCORD_HOOKS):
        return
    body = json.dumps({"content": text[:1900], "allowed_mentions": {"parse": []}}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://app.talkto.co.kr, 1.0)",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception as error:  # noqa: BLE001
        log(f"  (디스코드 알림을 보내지 못했어요: {type(error).__name__})")


def commits() -> str:
    """세 레포의 지금 커밋(커밋하지 않은 수정이 있으면 '+수정').

    GitHub 배포는 얕은(depth 1)·부분 체크아웃이고 server 만 올릴 땐 FE 를 받지 않는다(빼고 적는다).
    """
    parts = []
    for name, repo in (("BE", BE), ("FE", FE), ("AI", AI)):
        if not (repo / ".git").exists():
            continue

        def git(*args: str, repo: Path = repo) -> str:
            r = subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=git_env(),
            )
            return r.stdout.strip()

        dirty = git("status", "--porcelain", "--untracked-files=no")
        head = git("rev-parse", "--short", "HEAD") or "?"
        parts.append(f"{name} {head}{'+수정' if dirty else ''}")
    return " · ".join(parts)


def deployer() -> str:
    """디스코드 알림에 붙일 '누가'. GitHub 배포만 누른 사람(GitHub 아이디)을 붙인다.

    '다시 돌리기'는 GITHUB_ACTOR 가 처음 누른 사람으로 남아서 GITHUB_TRIGGERING_ACTOR 를 먼저 본다.
    """
    if not in_ci():
        return ""
    actor = ""
    for name in ("GITHUB_TRIGGERING_ACTOR", "GITHUB_ACTOR"):
        actor = re.sub(r"[^A-Za-z0-9-]", "", os.environ.get(name, ""))[:39]
        if actor:
            break
    return f" · GitHub {actor or '?'}"


# ── GitHub 배포 역할(talkto-ci 스택) ─────────────────────
def ci_stack(aws: Aws) -> dict | None:
    try:
        return aws.client("cloudformation").describe_stacks(StackName=CI_STACK)["Stacks"][0]
    except ClientError as e:
        if "does not exist" in str(e):
            return None
        raise


def github_oidc_exists(aws: Aws) -> bool:
    """이 계정에 GitHub Actions OIDC 공급자가 이미 있는지(계정에 하나만 둘 수 있다)."""
    rows = aws.client("iam").list_open_id_connect_providers()["OpenIDConnectProviderList"]
    return any(r["Arn"].endswith(f":oidc-provider/{GITHUB_OIDC_HOST}") for r in rows)


def valid_github_ids(ids: dict[str, str] | None) -> bool:
    return bool(ids) and all(
        re.fullmatch(r"[0-9]{1,20}", str(ids.get(k) or "")) for k in CI_GITHUB_IDS
    )


def github_ids() -> dict[str, str]:
    """talkto-deploy 의 조직·레포 번호(역할의 sub 에 넣는다). gh 로 읽는다(값을 바꾸지 않는다).

    gh 를 못 쓰면 TALKTO_GITHUB_ORG_ID·TALKTO_GITHUB_REPO_ID 로 준다. 레포가 아직 없거나
    이름이 다르거나(옮김·이름 바꿈) 공개 레포면 멈춘다.
    """
    org, repo = CI_GITHUB["GitHubOrg"], CI_GITHUB["GitHubRepo"]
    given = {k: os.environ.get(env, "").strip() for k, env in CI_GITHUB_IDS.items()}
    if any(given.values()):
        if not valid_github_ids(given):
            raise SystemExit(
                f"{' · '.join(CI_GITHUB_IDS.values())} 는 둘 다 숫자로 줘야 해요"
                f"(gh api repos/{org}/{repo} 의 owner.id·id)."
            )
        return given
    try:
        r = subprocess.run(
            ["gh", "api", f"repos/{org}/{repo}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        raise SystemExit(
            "GitHub CLI(gh)가 없어요. 설치하고 gh auth login 한 뒤 다시 돌려 주세요."
        ) from None
    if r.returncode != 0:
        if "HTTP 404" in r.stderr or "Not Found" in r.stdout + r.stderr:
            raise SystemExit(
                f"{org}/{repo} 레포가 아직 없어요(또는 이 gh 로그인으로 볼 수 없어요). "
                "deploy/aws/ci/README.md 순서대로 레포를 먼저 만들고 다시 돌려 주세요."
            )
        raise SystemExit(
            f"GitHub 에서 {org}/{repo} 를 읽지 못했어요. gh auth login 을 확인해 주세요.\n"
            + r.stderr.strip()[-300:]
        )
    try:
        info = json.loads(r.stdout)
        ids = {"GitHubOrgId": str(info["owner"]["id"]), "GitHubRepoId": str(info["id"])}
    except (ValueError, KeyError, TypeError):
        raise SystemExit(f"gh 가 돌려준 {org}/{repo} 정보를 읽지 못했어요.") from None
    # 옮기거나 이름을 바꾼 레포는 GitHub 가 새 이름으로 넘겨준다. sub 는 실제 이름을 쓴다.
    if info.get("full_name") != f"{org}/{repo}":
        raise SystemExit(
            f"{org}/{repo} 를 찾았더니 {info.get('full_name')} 예요. 레포 이름을 정확히 "
            f"{org}/{repo} 로 맞춰 주세요."
        )
    if info.get("private") is not True:
        raise SystemExit(
            f"{org}/{repo} 는 비공개여야 해요(Actions 기록에 서버 기록이 실려요). "
            "Settings → General → Danger Zone → Change visibility 로 비공개로 바꿔 주세요."
        )
    if not valid_github_ids(ids):
        raise SystemExit(f"{org}/{repo} 의 번호가 이상해요: {ids}")
    return ids


def ci_subject(ids: dict[str, str]) -> str:
    """역할이 받는 GitHub 작업(OIDC sub). ci.yaml 의 조건과 같은 모양."""
    g = CI_GITHUB
    return (
        f"repo:{g['GitHubOrg']}@{ids['GitHubOrgId']}/{g['GitHubRepo']}@{ids['GitHubRepoId']}"
        f":ref:refs/heads/{g['GitHubBranch']}"
    )


def ci_parameters(
    aws: Aws, out: dict[str, str], st: dict | None, ids: dict[str, str]
) -> dict[str, str]:
    live = {
        p["ParameterKey"]: p.get("ParameterValue", "") for p in (st or {}).get("Parameters", [])
    }
    # 이 스택이 OIDC 공급자를 만들었으면 계속 둔다(false 로 바꾸면 CloudFormation 이 지운다)
    make_oidc = live.get("CreateOidcProvider") == "true" or not github_oidc_exists(aws)
    return {
        **{k: out[k] for k in CI_OUTPUTS},
        **CI_GITHUB,
        **{k: ids[k] for k in CI_GITHUB_IDS},
        "MainStackName": STACK,
        "CreateOidcProvider": "true" if make_oidc else "false",
    }


def step_ci(aws: Aws, out: dict[str, str], ids: dict[str, str] | None = None) -> str:
    """GitHub(talkto-deploy) 가 코드만 올릴 수 있는 역할을 만들거나 고친다(대표님 컴퓨터에서만).

    역할은 talkto-deploy 레포(조직·레포 번호까지)의 production 환경에서 돈 작업만 받을 수 있고,
    운영 설정·비밀값을 직접 읽지 못한다(deploy/aws/ci.yaml). ids 가 없으면 gh 로 읽는다.
    """
    missing = [k for k in CI_OUTPUTS if not out.get(k)]
    if missing:
        raise SystemExit(
            f"먼저 인프라를 만들어 주세요({STACK} 출력에 {', '.join(missing)} 가 없어요)."
        )
    if not valid_github_ids(ids):
        ids = github_ids()
    cf = aws.client("cloudformation")
    st = ci_stack(aws)
    status = st["StackStatus"] if st else None
    if status and status.endswith("_IN_PROGRESS"):
        raise SystemExit(
            f"{CI_STACK} 스택이 다른 작업 중이에요({status}). 끝난 뒤 다시 돌려 주세요."
        )
    if status == "ROLLBACK_COMPLETE":
        log(f"  지난번 실패한 빈 {CI_STACK} 스택을 지우고 다시 만들어요.")
        cf.delete_stack(StackName=CI_STACK)
        cf.get_waiter("stack_delete_complete").wait(StackName=CI_STACK)
        st = None
    params = ci_parameters(aws, out, st, ids)
    log(f"[CI] GitHub 배포 역할 ({CI_STACK} 스택)")
    log(f"  받는 작업: {ci_subject(ids)}")
    if params["CreateOidcProvider"] == "true":
        log("  GitHub OIDC 공급자도 이 스택에 둬요(계정에 아직 없어서).")
    kw = {
        "StackName": CI_STACK,
        "TemplateBody": (HERE / "ci.yaml").read_text(encoding="utf-8"),
        "Parameters": [{"ParameterKey": k, "ParameterValue": v} for k, v in params.items()],
        "Capabilities": ["CAPABILITY_NAMED_IAM"],
    }
    waiter = None
    try:
        if st:
            cf.update_stack(**kw)
            waiter = "stack_update_complete"
        else:
            cf.create_stack(**kw)
            waiter = "stack_create_complete"
    except ClientError as e:
        if "No updates are to be performed" not in str(e):
            raise
        log("  바뀐 것이 없어요.")
    if waiter:
        try:
            cf.get_waiter(waiter).wait(
                StackName=CI_STACK, WaiterConfig={"Delay": 10, "MaxAttempts": 90}
            )
        except WaiterError:
            reasons = failure_reasons(aws, CI_STACK) or ["(CloudFormation 콘솔에서 확인)"]
            raise SystemExit(f"{CI_STACK} 스택 실패:\n  " + "\n  ".join(reasons)) from None
    st = ci_stack(aws) or {}
    role = {o["OutputKey"]: o["OutputValue"] for o in st.get("Outputs", [])}.get("RoleArn", "")
    repo = f"{CI_GITHUB['GitHubOrg']}/{CI_GITHUB['GitHubRepo']}"
    log(f"  역할: {role}")
    log("다음(처음 한 번): deploy/aws/ci/README.md 의 '처음 한 번' 3번부터 이어서 해 주세요.")
    log(f"  gh variable set AWS_DEPLOY_ROLE_ARN -R {repo} --body {role}")
    return f"GitHub 배포 역할 {role}"


def refresh_ci(aws: Aws, out: dict[str, str]) -> None:
    """인프라를 바꾼 뒤 GitHub 배포 역할이 옛 서버·버킷을 가리키면 고친다(역할이 있을 때만).

    인프라는 이미 바뀐 뒤라 무엇이 잘못돼도 알리기만 하고 서버·웹 배포를 이어 간다.
    """
    warn = "  경고: GitHub 배포 역할을 {}({}). 'deploy.py ci' 를 다시 돌려 주세요."
    try:
        st = ci_stack(aws)
    except (ClientError, BotoCoreError) as error:
        log(warn.format("확인하지 못했어요", error))
        return
    if not st:
        return
    live = {p["ParameterKey"]: p.get("ParameterValue", "") for p in st.get("Parameters", [])}
    if all(live.get(k) == out.get(k) for k in CI_OUTPUTS):
        return
    log("  서버·버킷이 바뀌어 GitHub 배포 역할도 고쳐요.")
    # 레포 번호는 지금 역할에 든 값을 그대로 쓴다(gh 없이도 고칠 수 있게)
    ids = {k: live.get(k, "") for k in CI_GITHUB_IDS}
    try:
        step_ci(aws, out, ids if valid_github_ids(ids) else None)
    except (SystemExit, ClientError, BotoCoreError) as error:
        log(warn.format("못 고쳤어요", error))


def main() -> None:
    step = sys.argv[1] if len(sys.argv) > 1 else "all"
    check_step(step)
    if step in ("status", "ci"):
        run(step)
        return
    started = time.time()
    who = deployer()
    tell_discord(f"🚀 배포 시작 · {step} · {commits()}{who}")
    try:
        summary = run(step)
    except BaseException as error:
        reason = (str(error).strip().splitlines() or [type(error).__name__])[-1][:300]
        tell_discord(f"❌ 배포 실패 · {step} · {reason}{who}")
        raise
    minutes = max(1, round((time.time() - started) / 60))
    tell_discord(f"✅ 배포 완료 · {step} · {minutes}분 · {summary}{who}")


def run(step: str) -> str:
    """배포 한 단계. 끝나면 디스코드에 보낼 한 줄 요약을 돌려준다."""
    check_step(step)
    ci = in_ci()
    instance_type = os.environ.get("TALKTO_INSTANCE_TYPE", DEFAULT_INSTANCE)
    aws = Aws()
    try:
        account = aws.client("sts").get_caller_identity()["Account"]
    except (ClientError, BotoCoreError):
        if ci:
            raise SystemExit(
                "AWS 역할을 받지 못했어요. talkto-deploy 의 AWS_DEPLOY_ROLE_ARN 과 "
                f"{CI_STACK} 스택(deploy.py ci)을 확인해 주세요."
            ) from None
        raise SystemExit(
            "AWS 로그인이 안 돼요. 터미널에서 aws configure 로 새 액세스 키를 넣어 주세요."
        ) from None
    log(f"AWS 계정 …{account[-4:]} · 리전 {aws.region}")
    if step == "status":
        st = stack(aws)
        log(f"  스택: {st['StackStatus'] if st else '없음'}")
        out = outputs(aws)
        for k, v in out.items():
            if k != "DatabaseEndpoint":
                log(f"  {k}: {v}")
        if out:
            smoke(out)
        return ""
    if step == "ci":
        return step_ci(aws, outputs(aws))
    if step in ("all", "domain"):
        ready = step_domain(aws)
        if step == "domain":
            return "도메인 확인"
        if not ready:
            log("도메인이 준비되면 다시 돌려 주세요(지금 사이트는 그대로 돌아요).")
            return "도메인이 아직 준비되지 않아 멈춤"
    # 비밀값(DB 비밀번호 등)은 인프라·운영 설정을 올릴 때만 꺼낸다. GitHub 배포는 꺼내지 않는다
    # (역할에 Parameter Store 읽기 권한도 없다). 운영 설정은 대표님이 마지막에 올린 그대로 둔다.
    configures = step in ("all", "infra", "server", "app")
    gen = generated_secrets(aws) if configures and not ci else None
    if step in ("all", "infra"):
        out = step_infra(aws, gen, instance_type)
        refresh_ci(aws, out)
    else:
        out = outputs(aws)
    if not out:
        raise SystemExit("아직 인프라가 없어요. 먼저 'infra' 나 전체를 돌려 주세요.")
    done = []
    if step in ("all", "server", "app"):
        step_server(aws, out, gen)
        done.append("서버")
    if step in ("all", "web", "app"):
        done.append(f"웹 {step_web(aws, out)}")
    if step in ("all", "landing", "app"):
        done.append(step_landing(aws, out))
    log("[확인] (CloudFront 가 처음 퍼지는 동안 몇 분은 실패할 수 있어요)")
    ok = smoke(out)
    log(f"\n완료. 주소: {out['SiteUrl']}")
    if not ci:
        log("관리자 비밀번호 보기(본인 PowerShell 에서, 비밀번호만 나와요):")
        log(f"  {ADMIN_PASSWORD_COMMAND.format(region=aws.region)}")
    check = "확인 통과" if ok else "확인 일부 실패(몇 분 뒤 다시 확인)"
    return " · ".join([*done, check, out["SiteUrl"]])


if __name__ == "__main__":
    main()
