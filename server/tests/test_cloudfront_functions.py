"""CloudFront Functions(deploy/aws/stack.yaml)의 주소 정리와 검색 제외 헤더.

stack.yaml 에서 함수 코드를 꺼내 ${SiteDomain}·${LegacyHostRedirect} 를 채우고 node 로 돌린다.
node 가 없으면 함수 시험은 건너뛰고 YAML 이 읽히는지만 본다.
"""

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

yaml = pytest.importorskip("yaml")

STACK = Path(__file__).resolve().parents[1] / "deploy" / "aws" / "stack.yaml"
SITE = "talkto.co.kr"
APP = "app." + SITE
CF = "d111111abcdef8.cloudfront.net"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(
    NODE is None, reason="node 가 PATH 에 없어 함수를 돌려 볼 수 없어요"
)


class _CfnLoader(yaml.SafeLoader):
    """!Sub·!If·!Ref·!GetAtt 같은 CloudFormation 태그를 {'!Sub': 값} 으로 읽는다."""


def _cfn_tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {"!" + suffix: value}


_CfnLoader.add_multi_constructor("!", _cfn_tag)


def _stack() -> dict:
    return yaml.load(STACK.read_text(encoding="utf-8"), Loader=_CfnLoader)


def _code(name: str, legacy: str = "off") -> str:
    code = _stack()["Resources"][name]["Properties"]["FunctionCode"]["!Sub"]
    code = code.replace("${SiteDomain}", SITE).replace("${LegacyHostRedirect}", legacy)
    assert "${" not in code
    return code


# node 안에서 함수를 띄우고 사건(event) 표를 하나씩 넣어 결과를 돌려준다.
# rawQueryString() 은 cloudfront-js-2.0 이 request 에 붙여 주는 도우미라 흉내 낸다.
_HARNESS = r"""
const vm = require('vm');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const handler = vm.runInNewContext(input.code + '\n;handler', {});
const out = input.cases.map(function (c) {
  const headers = c.host === null ? {} : { host: { value: c.host } };
  const request = {
    method: 'GET', uri: c.uri, querystring: {}, headers: headers, cookies: {},
    rawQueryString: function () { return c.qs || ''; }
  };
  const event = {
    version: '1.0', context: { eventType: input.type }, viewer: { ip: '198.51.100.1' },
    request: request
  };
  if (input.type === 'viewer-response') {
    event.response = { statusCode: 200, statusDescription: 'OK', headers: {}, cookies: {} };
  }
  const r = handler(event);
  return { statusCode: r.statusCode || null, uri: r.uri || null, headers: r.headers || {} };
});
process.stdout.write(JSON.stringify(out));
"""


def _run(code: str, kind: str, cases: list[dict], tmp_path: Path) -> list[dict]:
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    payload = json.dumps({"code": code, "type": kind, "cases": cases})
    done = subprocess.run(
        [NODE, str(harness)],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _request(host, uri, qs=""):
    return {"host": host, "uri": uri, "qs": qs}


def test_stack_yaml_parses_with_functions():
    res = _stack()["Resources"]
    for name in ("CanonicalHostRedirect", "OffSiteNoIndex"):
        fn = res[name]
        assert fn["Type"] == "AWS::CloudFront::Function"
        assert fn["Condition"] == "HasDomain"
        assert fn["Properties"]["AutoPublish"] is True
        assert fn["Properties"]["FunctionConfig"]["Runtime"] == "cloudfront-js-2.0"
        # CloudFront Functions 코드는 10KB 까지
        assert len(_code(name).encode("utf-8")) < 10 * 1024
    default = res["Distribution"]["Properties"]["DistributionConfig"]["DefaultCacheBehavior"]
    cond, assoc, _ = default["FunctionAssociations"]["!If"]
    assert cond == "HasDomain"
    assert {a["EventType"]: a["FunctionARN"]["!GetAtt"] for a in assoc} == {
        "viewer-request": "CanonicalHostRedirect.FunctionARN",
        "viewer-response": "OffSiteNoIndex.FunctionARN",
    }


# (host, uri, query) → 그대로 보여 줄 S3 자리(uri) 또는 301 로 넘길 주소
REQUESTS = [
    (SITE, "/", "", ("serve", "/_landing/index.html")),
    (SITE, "/", "utm=a", ("serve", "/_landing/index.html")),
    (SITE, "/index.html", "", ("301", f"https://{SITE}/")),
    (SITE, "/index.html", "x=1", ("301", f"https://{SITE}/?x=1")),
    (SITE, "/terms", "", ("301", f"https://{SITE}/terms.html")),
    (SITE, "/terms/", "", ("301", f"https://{SITE}/terms.html")),
    (SITE, "/refund", "", ("301", f"https://{SITE}/refund.html")),
    (SITE, "/privacy/", "a=b", ("301", f"https://{SITE}/privacy.html?a=b")),
    (SITE, "/terms.html", "", ("serve", "/_landing/terms.html")),
    (SITE, "/privacy.html", "", ("serve", "/_landing/privacy.html")),
    (SITE, "/_landing/index.html", "", ("301", f"https://{SITE}/")),
    (SITE, "/_landing/", "", ("301", f"https://{SITE}/")),
    (SITE, "/_landing", "", ("301", f"https://{SITE}/")),
    (SITE, "/_landing/terms.html", "", ("301", f"https://{SITE}/terms.html")),
    (SITE, "/_landing/terms", "", ("301", f"https://{SITE}/terms.html")),
    (SITE, "/_landing/style.css", "v=2", ("301", f"https://{SITE}/style.css?v=2")),
    (SITE, "/style.css", "", ("serve", "/_landing/style.css")),
    (SITE, "/img/og.png", "", ("serve", "/_landing/img/og.png")),
    (SITE, "/fonts/a.woff", "", ("serve", "/_landing/fonts/a.woff")),
    (SITE, "/robots.txt", "", ("serve", "/_landing/robots.txt")),
    (SITE, "/sitemap.xml", "", ("serve", "/_landing/sitemap.xml")),
    (SITE, "/nope", "x=1", ("301", f"https://{APP}/nope?x=1")),
    (SITE, "/img/", "", ("301", f"https://{APP}/img/")),
    # www 는 도메인 자체의 규칙으로 대표 주소에 한 번에
    ("www." + SITE, "/", "", ("301", f"https://{SITE}/")),
    ("www." + SITE, "/index.html", "", ("301", f"https://{SITE}/")),
    ("www." + SITE, "/terms", "", ("301", f"https://{SITE}/terms.html")),
    ("www." + SITE, "/style.css", "", ("301", f"https://{SITE}/style.css")),
    ("www." + SITE, "/nope", "x=1", ("301", f"https://{APP}/nope?x=1")),
    ("www." + APP, "/", "", ("301", f"https://{APP}/")),
    ("www." + APP, "/login", "next=a", ("301", f"https://{APP}/login?next=a")),
    # 앱은 그대로(경로·쿼리 손대지 않음). 앱 주소로 본 랜딩 사본은 도메인 자체로
    (APP, "/", "", ("serve", "/")),
    (APP, "/main.dart.js", "", ("serve", "/main.dart.js")),
    (APP, "/index.html", "", ("serve", "/index.html")),
    (APP, "/_landing/terms.html", "", ("301", f"https://{SITE}/terms.html")),
    (APP, "/_landing/index.html", "", ("301", f"https://{SITE}/")),
    (APP, "/_landing/nope", "", ("301", f"https://{APP}/nope")),
    # 옛 주소: LegacyHostRedirect=off 면 앱을 그대로
    (CF, "/", "", ("serve", "/")),
    (CF, "/index.html", "", ("serve", "/index.html")),
    # host 가 없거나 모르는 주소면 앱으로
    (None, "/", "", ("301", f"https://{APP}/")),
    ("evil.example", "/x", "", ("301", f"https://{APP}/x")),
]


def _outcome(r: dict):
    if r["statusCode"] is None:
        return ("serve", r["uri"])
    assert r["statusCode"] == 301
    return ("301", r["headers"]["location"]["value"])


@needs_node
def test_canonical_host_redirect_table(tmp_path):
    cases = [_request(h, u, q) for h, u, q, _ in REQUESTS]
    got = [
        _outcome(r) for r in _run(_code("CanonicalHostRedirect"), "viewer-request", cases, tmp_path)
    ]
    for (host, uri, qs, want), have in zip(REQUESTS, got, strict=True):
        assert have == want, f"{host}{uri}?{qs}"


@needs_node
def test_every_redirect_lands_in_one_hop(tmp_path):
    """넘긴 주소를 다시 넣으면 더 넘어가지 않고 바로 보여 준다(고리·두 번 넘김 없음)."""
    code = _code("CanonicalHostRedirect")
    targets = [w[1] for *_, w in REQUESTS if w[0] == "301"]
    cases = []
    for t in targets:
        u = urlsplit(t)
        assert u.scheme == "https"
        cases.append(_request(u.hostname, u.path, u.query))
    for t, r in zip(targets, _run(code, "viewer-request", cases, tmp_path), strict=True):
        assert r["statusCode"] is None, t


@needs_node
def test_legacy_host_redirect_on(tmp_path):
    cases = [_request(CF, "/", ""), _request(CF, "/chat", "a=1"), _request(APP, "/", "")]
    got = [
        _outcome(r)
        for r in _run(_code("CanonicalHostRedirect", "on"), "viewer-request", cases, tmp_path)
    ]
    assert got == [("301", f"https://{APP}/"), ("301", f"https://{APP}/chat?a=1"), ("serve", "/")]


@needs_node
def test_offsite_noindex_header(tmp_path):
    hosts = [SITE, APP, "www." + APP, CF, None]
    cases = [_request(h, "/") for h in hosts]
    got = _run(_code("OffSiteNoIndex"), "viewer-response", cases, tmp_path)
    tags = {
        h: r["headers"].get("x-robots-tag", {}).get("value")
        for h, r in zip(hosts, got, strict=True)
    }
    # 랜딩(도메인 자체)만 검색에 싣고, host 를 모르면 건드리지 않는다
    assert tags == {SITE: None, APP: "noindex", "www." + APP: "noindex", CF: "noindex", None: None}
    assert all(r["statusCode"] == 200 for r in got)
