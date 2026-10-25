"""기억 인터뷰 신청: 주인 신청·취소 → 알림(개인 정보 없이) → 관리자 목록·상태·메모 → 들은 답 넣기
(answers 에 합침)·기억 카드(INTERVIEW, 바로 임베딩) → 다시 만들기(UPDATE) → 30일 뒤 연락처 지우기.

모든 자료는 지어낸 시험용이다(실제 가족 자료를 쓰지 않는다). AI 서버는 가짜(httpx MockTransport)다.
"""

import asyncio
import json
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import main as main_module
from app.core.config import get_settings
from app.db.models import (
    AnalyticsEvent,
    ConsentRecord,
    InterviewEntry,
    InterviewRequest,
    Memory,
    Person,
    Persona,
    PersonaShare,
    SetupStep,
)
from app.db.session import SessionLocal
from app.main import app
from app.services import ai as ai_module
from app.services import intake, interview, legal, mailer, notify

API = "/api/v1"
AGREEMENTS = [
    "age14",
    "service",
    "terms",
    "privacy",
    "voice_data",
    "voice_synthesis",
    "overseas",
    "overseas_elevenlabs",
    "overseas_fish",
]
# 지어낸 가족: 그분(나말순, 할머니) · 만든 사람(가은, 손녀)
BASICS = {
    "name": "나말순",
    "relation": "할머니",
    "addressTerm": "할머니",
    "status": "deceased",
    "myName": "가은",
    "myRelation": "손녀",
    "calledMe": "우리 강아지",
}
ANSWERS = {
    "C-01": "한두 문장으로 짧게",
    "C-02": "말수가 적고, 말보다 행동(끼니·안부 챙김)으로 표현한다",
    "C-05": "시장에서 오래 장사하신 부지런한 분",
    "M4-01": ["부지런함"],
    "C-09": ["밥은 먹었냐"],
}
APPLY = {
    "name": "가은",
    "relation": "손녀",
    "phone": "010 1234 5678",
    "days": ["토", "월", "아무날"],
    "timeText": "오후 2시~5시",
    "method": "phone",
    "agreeUse": True,
    "agreeRecording": False,
}


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "ai_server_url", "")
    monkeypatch.setattr(s, "rate_limit_enabled", True)
    return s


class FakeAI:
    def __init__(self) -> None:
        self.calls: dict[str, list[dict]] = defaultdict(list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}")
        self.calls[path].append(body)
        if path == "/v1/embeddings":
            return httpx.Response(
                200,
                json={
                    "embeddings": [
                        {
                            "memorySegmentId": item["memorySegmentId"],
                            "embeddingIndex": item["embeddingIndex"],
                            "provider": "fake",
                            "model": "fake",
                            "dimensions": 3,
                            "embedding": [0.2, 0.4, 0.1],
                        }
                        for item in body["items"]
                    ]
                },
            )
        if path == "/v1/persona/assembly":
            return httpx.Response(
                200, json={"instructions": json.dumps(body, ensure_ascii=False)[:2000]}
            )
        return httpx.Response(404, json={"detail": "없음"})


@pytest.fixture
def fake_ai(monkeypatch):
    fake = FakeAI()
    monkeypatch.setattr(get_settings(), "ai_server_url", "https://ai.example.test")
    transport = httpx.MockTransport(fake)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        ai_module.httpx, "AsyncClient", lambda *a, **kw: real_client(transport=transport, **kw)
    )
    return fake


# ── 도우미 ──


def _db(fn):
    async def go():
        async with SessionLocal() as db:
            return await fn(db)

    return asyncio.run(go())


def _signup(client: TestClient, name: str = "가은") -> tuple[dict, str]:
    email = f"interview-{uuid4().hex[:10]}@example.com"
    response = client.post(
        f"{API}/auth/signup",
        json={"email": email, "password": "password123", "name": name, "agreements": AGREEMENTS},
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['data']['bearerToken']}"}
    return headers, client.get(f"{API}/auth/me", headers=headers).json()["data"]["id"]


def _admin(client: TestClient) -> dict:
    s = get_settings()
    response = client.post(
        f"{API}/auth/login",
        json={"email": s.bootstrap_admin_email, "password": s.bootstrap_admin_password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['data']['bearerToken']}"}


def _persona(owner_id: str, basics: dict = BASICS, origin: str | None = None) -> str:
    """다 만든 대화 상대(녹음 없이 DB 에 바로)."""

    async def go(db):
        person = Person(
            owner_user_id=owner_id,
            name=basics["name"],
            relationship_label=basics["relation"],
            address_term=basics.get("addressTerm"),
            life_status=intake.LIFE_STATUS[basics["status"]],
        )
        db.add(person)
        await db.flush()
        persona = Persona(
            person_id=person.id,
            name=basics["name"],
            status="READY",
            setup_step=SetupStep.READY,
            basics=dict(basics),
            answers=json.loads(json.dumps(ANSWERS)),
            instructions="(시험용 지침)",
            origin=origin,
            subject_voices=[],
            ready_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        db.add(persona)
        await db.commit()
        return persona.id

    return _db(go)


def _get(model, row_id: str):
    return _db(lambda db: db.get(model, row_id))


def _all(query):
    async def go(db):
        return list((await db.scalars(query)).all())

    return _db(go)


def _apply(client, h, pid, **changes):
    return client.post(f"{API}/personas/{pid}/interview", headers=h, json={**APPLY, **changes})


def _question(kind: str, *, other: bool | None = None, sensitive: bool = False) -> dict:
    """그 종류의 질문 하나(질문 목록이 바뀌어도 시험이 따라가게 서버 정의에서 고른다)."""
    for q in interview.questions().values():
        if q["type"] != kind or bool(q.get("sensitive")) != sensitive:
            continue
        if other is not None and bool(q.get("other")) != other:
            continue
        if kind == "single" and not q.get("options"):
            continue
        return q
    raise AssertionError(f"no {kind} question")


def _wait_rebuild(client, admin, pid, timeout: float = 20) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        state = client.get(f"{API}/admin/personas/{pid}/interview", headers=admin).json()["data"]
        job = state["rebuild"]
        if job and job["status"] != "RUNNING":
            return job
        time.sleep(0.1)
    raise AssertionError("rebuild did not finish")


# ── 주인: 신청 ──


def test_owner_applies_once_with_consents_and_can_cancel():
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        state = client.get(f"{API}/personas/{pid}/interview", headers=h).json()["data"]
        # 인터뷰 받는 분은 만든 분: 만들 때 적은 내 이름·관계로 미리 채운다
        assert state["available"] is True and state["request"] is None
        assert state["prefill"] == {"name": "가은", "relation": "손녀"}
        assert state["days"] == list(interview.DAYS)

        made = _apply(client, h, pid)
        assert made.status_code == 201, made.text
        request = made.json()["data"]["request"]
        assert request["status"] == "requested"
        assert request["phone"] == "010-1234-5678"
        assert request["days"] == ["월", "토"]  # 모르는 값은 버리고 요일 순서로
        assert request["recordingOk"] is False
        assert made.json()["data"]["available"] is False

        # 대화 상대별 동의 둘(문서 interview). 녹음은 동의하지 않은 기록으로 남는다
        records = _all(
            select(ConsentRecord).where(
                ConsentRecord.user_id == uid, ConsentRecord.persona_id == pid
            )
        )
        kinds = {r.consent_type: r for r in records}
        use = kinds[f"interview_use:{pid}"]
        assert use.agreed is True and use.doc == "interview"
        assert use.version == legal.version_of("interview") == "0.9"
        assert kinds[f"interview_recording:{pid}"].agreed is False
        mine = client.get(f"{API}/consents/me", headers=h).json()["data"]
        titles = {row["type"]: row["title"] for row in mine}
        assert titles[f"interview_use:{pid}"] == legal.PERSONA_TYPES["interview_use"][1]

        # 끝나지 않은 신청이 있으면 또 받지 않는다
        again = _apply(client, h, pid)
        assert again.status_code == 409
        assert again.json()["detail"] == interview.ALREADY

        cancelled = client.post(f"{API}/personas/{pid}/interview/cancel", headers=h)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["data"]["request"]["status"] == "cancelled"
        assert _get(InterviewRequest, request["id"]).closed_at is not None
        assert client.post(f"{API}/personas/{pid}/interview/cancel", headers=h).status_code == 409

        # 취소한 뒤에는 다시 신청할 수 있다(녹음 동의도 이번엔 함)
        second = _apply(client, h, pid, method="video", agreeRecording=True)
        assert second.status_code == 201, second.text
        assert second.json()["data"]["request"]["recordingOk"] is True
        assert legal.title_of(f"interview_recording:{pid}") == "기억 인터뷰 — 통화 녹음 동의"


def test_withdrawing_recording_consent_shows_on_the_admin_page():
    """통화 녹음 동의를 거두면(POST /consents) 관리자 화면의 신청에도 바로 '동의 안 함'으로
    보인다(녹음하지 않게). 신청 자체는 그대로다. 끝난 신청이면 마지막 신청에 적는다."""
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        admin = _admin(client)
        rid = _apply(client, h, pid, agreeRecording=True).json()["data"]["request"]["id"]

        def recording_ok(request_id: str) -> bool:
            row = client.get(f"{API}/admin/interviews/{request_id}", headers=admin)
            assert row.status_code == 200, row.text
            return row.json()["data"]["recordingOk"]

        assert recording_ok(rid) is True
        body = {"consentType": f"interview_recording:{pid}", "agreed": False}
        withdrawn = client.post(f"{API}/consents", headers=h, json=body)
        assert withdrawn.status_code == 200, withdrawn.text
        assert recording_ok(rid) is False
        assert _get(InterviewRequest, rid).status == "requested"
        state = client.get(f"{API}/personas/{pid}/interview", headers=h).json()["data"]
        assert state["request"]["recordingOk"] is False

        # 취소한 신청(마지막 신청)도
        assert client.post(f"{API}/personas/{pid}/interview/cancel", headers=h).status_code == 200
        again = _apply(client, h, pid, agreeRecording=True).json()["data"]["request"]["id"]
        assert client.post(f"{API}/personas/{pid}/interview/cancel", headers=h).status_code == 200
        assert recording_ok(again) is True
        assert client.post(f"{API}/consents", headers=h, json=body).status_code == 200
        assert recording_ok(again) is False


def test_apply_checks_the_form():
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        no_use = _apply(client, h, pid, agreeUse=False)
        assert no_use.status_code == 400
        assert no_use.json()["detail"] == interview.USE_REQUIRED
        assert _apply(client, h, pid, phone="12345").status_code == 400
        assert _apply(client, h, pid, days=[]).status_code == 400
        assert _apply(client, h, pid, name="  ").status_code == 400
        assert _apply(client, h, pid, timeText="").status_code == 400
        assert _apply(client, h, pid, method="visit").status_code == 422
        # 막힌 신청은 아무것도 남기지 않는다(동의 기록도)
        assert _all(select(InterviewRequest).where(InterviewRequest.persona_id == pid)) == []
        assert _all(select(ConsentRecord).where(ConsentRecord.persona_id == pid)) == []


def test_only_the_owner_of_a_non_self_persona_can_apply():
    with TestClient(app) as client:
        h, uid = _signup(client)
        # 나 자신 대화 상대: 만든 분이 곧 그분이라 인터뷰하지 않는다
        self_pid = _persona(
            uid, basics=intake.validate_basics({"name": "가은", "relation": "나 자신"})
        )
        state = client.get(f"{API}/personas/{self_pid}/interview", headers=h).json()["data"]
        assert state["available"] is False and state["reason"] == interview.SELF_BLOCKED
        blocked = _apply(client, h, self_pid)
        assert blocked.status_code == 409

        # 함께 쓰는 가족·다른 사람은 주인 경로를 볼 수 없다(404)
        pid = _persona(uid)
        member, member_id = _signup(client, "다른가족")

        async def join(db):
            db.add(
                PersonaShare(
                    persona_id=pid,
                    token_hash=uuid4().hex + uuid4().hex,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                    user_id=member_id,
                    joined_at=datetime.now(UTC),
                    profile={"myName": "다른가족"},
                )
            )
            await db.commit()

        _db(join)
        assert client.get(f"{API}/personas/{pid}/interview", headers=member).status_code == 404
        assert _apply(client, member, pid).status_code == 404
        stranger, _ = _signup(client, "남")
        assert _apply(client, stranger, pid).status_code == 404
        assert _all(select(InterviewRequest).where(InterviewRequest.persona_id == pid)) == []


def test_new_request_is_announced_without_personal_details(monkeypatch):
    sent: list[dict] = []

    class FakeSES:
        def send_email(self, **kwargs):
            sent.append(kwargs)

    s = get_settings()
    monkeypatch.setattr(s, "ops_email_to", "ops@example.com")
    monkeypatch.setattr(s, "mail_from", "noreply@example.com")
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "_client", lambda: FakeSES())
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        assert _apply(client, h, pid).status_code == 201
        end = time.time() + 5
        while not sent and time.time() < end:
            time.sleep(0.05)
        assert len(sent) == 1
        mail = sent[0]
        assert mail["FromEmailAddress"] == "noreply@example.com"
        assert mail["Destination"] == {"ToAddresses": ["ops@example.com"]}
        subject = mail["Content"]["Simple"]["Subject"]["Data"]
        body = mail["Content"]["Simple"]["Body"]["Text"]["Data"]
        assert subject == "새 기억 인터뷰 신청 1건"
        assert "관리자 화면에서 확인" in body and "/#/admin/interviews" in body
        # 이름·전화번호·관계·그분은 싣지 않는다
        for secret in ("가은", "손녀", "1234", "5678", "나말순", "할머니", pid):
            assert secret not in subject + body
    # 디스코드 알림 글도 개인 정보 없이
    events = _all(select(AnalyticsEvent).where(AnalyticsEvent.persona_id == pid))
    requested = [e for e in events if e.name == "interview_requested"]
    assert len(requested) == 1 and requested[0].props == {"method": "phone"}
    text = notify._event_text("interview_requested", requested[0].props)
    assert text and "기억 인터뷰 신청 1건" in text and "가은" not in text
    assert "상담" not in text + subject + body


def test_mail_failure_or_missing_settings_never_fail_the_request(monkeypatch):
    class BrokenSES:
        def send_email(self, **kwargs):
            raise RuntimeError("SES 거절")

    with TestClient(app) as client:
        h, uid = _signup(client)
        # 설정이 없으면(시험 기본) 보내지 않는다
        assert mailer.configured() is False
        assert mailer.send_ops("x", "y") is False
        assert _apply(client, h, _persona(uid)).status_code == 201
        # 보내다 실패해도 신청은 그대로 된다
        monkeypatch.setattr(mailer, "configured", lambda: True)
        monkeypatch.setattr(mailer, "_client", lambda: BrokenSES())
        assert _apply(client, h, _persona(uid)).status_code == 201


# ── 관리자: 목록·상태·메모 ──


def test_admin_lists_requests_and_sets_status_and_note():
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        rid = _apply(client, h, pid).json()["data"]["request"]["id"]
        # 관리자만
        assert client.get(f"{API}/admin/interviews", headers=h).status_code == 403
        admin = _admin(client)
        listed = client.get(f"{API}/admin/interviews?status=requested", headers=admin)
        assert listed.status_code == 200, listed.text
        row = next(r for r in listed.json()["data"] if r["id"] == rid)
        assert "phone" not in row  # 목록에는 전화번호를 싣지 않는다
        # 성함 대신 호칭으로
        assert row["persona"]["title"] == "할머니" and "나말순" not in json.dumps(row)
        assert client.get(f"{API}/admin/interviews?status=nope", headers=admin).status_code == 400
        detail = client.get(f"{API}/admin/interviews/{rid}", headers=admin).json()["data"]
        assert detail["phone"] == "010-1234-5678" and detail["adminNote"] is None

        changed = client.patch(
            f"{API}/admin/interviews/{rid}",
            headers=admin,
            json={"status": "scheduled", "adminNote": "  토요일 오후로 잡음 "},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["data"]["status"] == "scheduled"
        assert changed.json()["data"]["adminNote"] == "토요일 오후로 잡음"
        assert changed.json()["data"]["closedAt"] is None
        done = client.patch(f"{API}/admin/interviews/{rid}", headers=admin, json={"status": "done"})
        assert done.json()["data"]["closedAt"] is not None
        # 메모는 보내지 않으면 그대로, 빈 글이면 지운다
        assert done.json()["data"]["adminNote"] == "토요일 오후로 잡음"
        cleared = client.patch(
            f"{API}/admin/interviews/{rid}", headers=admin, json={"adminNote": ""}
        )
        assert cleared.json()["data"]["adminNote"] is None
        # 끝난 뒤에는 주인이 새로 신청할 수 있고, 그때 예전 신청을 다시 열면 막는다
        assert _apply(client, h, pid).status_code == 201
        reopen = client.patch(
            f"{API}/admin/interviews/{rid}", headers=admin, json={"status": "requested"}
        )
        assert reopen.status_code == 409
        assert client.get(f"{API}/admin/interviews/{uuid4()}", headers=admin).status_code == 404


def test_contact_details_are_wiped_30_days_after_closing(monkeypatch):
    with TestClient(app) as client:
        h, uid = _signup(client)
        old_done = _apply(client, h, _persona(uid)).json()["data"]["request"]["id"]
        recent = _apply(client, h, _persona(uid)).json()["data"]["request"]["id"]
        still_open = _apply(client, h, _persona(uid)).json()["data"]["request"]["id"]
        now = datetime.now(UTC)

        async def close(db):
            for rid, days in ((old_done, 31), (recent, 10)):
                row = await db.get(InterviewRequest, rid)
                row.status = "done"
                row.closed_at = now - timedelta(days=days)
            await db.commit()

        _db(close)
        # 하루 정리(main.cleanup_daily)가 지운다
        monkeypatch.setattr(main_module, "_last_cleanup", None)
        assert asyncio.run(main_module.cleanup_daily(now)) is True
        wiped = _get(InterviewRequest, old_done)
        assert (wiped.name, wiped.phone, wiped.days, wiped.time_text) == (None, None, None, None)
        assert wiped.wiped_at is not None and wiped.method == "phone"
        for rid in (recent, still_open):
            kept = _get(InterviewRequest, rid)
            assert kept.phone == "010-1234-5678" and kept.wiped_at is None
        # 한 번 지운 것은 다시 세지 않는다
        assert _db(lambda db: interview.wipe_closed(db, now)) == 0
        admin = _admin(client)
        row = client.get(f"{API}/admin/interviews/{old_done}", headers=admin).json()["data"]
        assert row["wiped"] is True and row["phone"] is None and row["days"] == []
        # 연락처를 지운 신청은 다시 열 수 없다(연락할 수 없고, 만든 분이 새로 신청하지 못하게 된다)
        for status in ("requested", "scheduled"):
            reopen = client.patch(
                f"{API}/admin/interviews/{old_done}", headers=admin, json={"status": status}
            )
            assert reopen.status_code == 409
        assert _get(InterviewRequest, old_done).status == "done"
        # 닫힌 상태끼리 바꾸는 것은 된다(지운 뒤에도)
        moved = client.patch(
            f"{API}/admin/interviews/{old_done}", headers=admin, json={"status": "cancelled"}
        )
        assert moved.status_code == 200


def test_deleting_the_persona_removes_its_requests():
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        rid = _apply(client, h, pid).json()["data"]["request"]["id"]
        assert client.delete(f"{API}/personas/{pid}", headers=h).status_code == 200
        assert _get(InterviewRequest, rid) is None


# ── 관리자: 들은 내용 넣기 ──


def test_admin_enters_answers_by_question_id_and_they_merge_into_answers():
    long_q = _question("textarea")
    phrases_q = _question("phrases")
    single_q = _question("single", other=False)
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        rid = _apply(client, h, pid).json()["data"]["request"]["id"]
        admin = _admin(client)
        url = f"{API}/admin/personas/{pid}/interview/answers"
        # 관리자만
        assert (
            client.post(url, headers=h, json={"questionId": long_q["id"], "text": "x"}).status_code
            == 403
        )

        first = client.post(
            url,
            headers=admin,
            json={"questionId": long_q["id"], "text": "첫 이야기", "requestId": rid},
        )
        assert first.status_code == 201, first.text
        second = client.post(
            url, headers=admin, json={"questionId": long_q["id"], "text": "둘째 이야기"}
        )
        assert second.json()["data"]["answer"] == "첫 이야기\n둘째 이야기"  # 긴 글은 덧붙인다
        # 이미 들어 있는 글(한 낱말이어도)을 덧붙이면 지금 답을 그대로 둔다
        # (짧은 글로 덮어쓰지 않는다)
        for again in ("둘째", "첫 이야기"):
            kept = client.post(url, headers=admin, json={"questionId": long_q["id"], "text": again})
            assert kept.status_code == 201, kept.text
            assert kept.json()["data"]["answer"] == "첫 이야기\n둘째 이야기"
        replaced = client.post(
            url,
            headers=admin,
            json={"questionId": long_q["id"], "text": "바꾼 이야기", "replace": True},
        )
        assert replaced.json()["data"]["answer"] == "바꾼 이야기"

        # 목록 질문은 줄마다 하나, 지금 답에 더한다(같은 말은 한 번)
        added = client.post(
            url,
            headers=admin,
            json={"questionId": phrases_q["id"], "text": "밥은 먹었냐\n차 조심해라"},
        )
        assert added.status_code == 201, added.text
        persona = _get(Persona, pid)
        expected = ["차 조심해라"]
        if phrases_q["id"] == "C-09":
            expected = ["밥은 먹었냐", "차 조심해라"]
        assert persona.answers[phrases_q["id"]][-len(expected) :] == expected
        assert persona.answers[long_q["id"]] == "바꾼 이야기"
        assert persona.answers["C-01"] == ANSWERS["C-01"]  # 다른 답은 그대로

        # 고르는 질문은 고를 수 있는 답만
        bad = client.post(
            url, headers=admin, json={"questionId": single_q["id"], "text": "아무 말"}
        )
        assert bad.status_code == 400
        ok = client.post(
            url, headers=admin, json={"questionId": single_q["id"], "text": single_q["options"][0]}
        )
        assert ok.status_code == 201, ok.text
        assert (
            client.post(url, headers=admin, json={"questionId": "Z-99", "text": "x"}).status_code
            == 400
        )
        assert (
            client.post(
                url, headers=admin, json={"questionId": "M2-01", "text": "철수"}
            ).status_code
            == 400
        )
        too_long = "가" * (int(long_q.get("maxLength") or 300) + 1)
        assert (
            client.post(
                url,
                headers=admin,
                json={"questionId": long_q["id"], "text": too_long, "replace": True},
            ).status_code
            == 400
        )

        # 넣은 답마다 기록이 남는다(source=interview)
        rows = _all(select(InterviewEntry).where(InterviewEntry.persona_id == pid))
        assert len(rows) == 7
        assert {r.source for r in rows} == {"interview"}
        assert rows[0].request_id == rid and rows[1].request_id is None
        view = client.get(f"{API}/admin/personas/{pid}/interview", headers=admin).json()["data"]
        assert [e["questionId"] for e in view["entries"]][:2] == [long_q["id"], long_q["id"]]
        assert view["entries"][0]["questionTitle"] == long_q["title"]
        assert view["answers"][long_q["id"]] == "바꾼 이야기"
        assert view["requests"][0]["id"] == rid and "phone" not in view["requests"][0]


def test_sensitive_and_imported_personas_are_guarded():
    sensitive = (
        _question("textarea", sensitive=True)
        if any(
            q.get("sensitive") and q["type"] == "textarea" for q in interview.questions().values()
        )
        else next(q for q in interview.questions().values() if q.get("sensitive"))
    )
    long_q = _question("textarea")
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        assert _apply(client, h, pid).status_code == 201  # 인터뷰 내용을 넣는 데 동의함
        admin = _admin(client)
        url = f"{API}/admin/personas/{pid}/interview/answers"
        text = sensitive["options"][0] if sensitive.get("options") else "시험용 이야기"
        blocked = client.post(
            url, headers=admin, json={"questionId": sensitive["id"], "text": text}
        )
        assert blocked.status_code == 400 and "동의" in blocked.json()["detail"]
        # 주인이 그 범주에 동의하면 넣을 수 있다
        agreed = client.post(
            f"{API}/personas/{pid}/consents",
            headers=h,
            json={"type": f"sensitive_{sensitive['sensitive']}", "agreed": True},
        )
        assert agreed.status_code == 200, agreed.text
        assert (
            client.post(
                url, headers=admin, json={"questionId": sensitive["id"], "text": text}
            ).status_code
            == 201
        )

        imported = _persona(uid, origin="import")
        resp = client.post(
            f"{API}/admin/personas/{imported}/interview/answers",
            headers=admin,
            json={"questionId": long_q["id"], "text": "x"},
        )
        assert resp.status_code == 400
        assert (
            client.post(f"{API}/admin/personas/{imported}/rebuild", headers=admin).status_code
            == 409
        )


def test_memory_cards_are_embedded_and_survive_rebuild(fake_ai):
    long_q = _question("textarea")
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        assert _apply(client, h, pid).status_code == 201  # 인터뷰 내용을 넣는 데 동의함
        admin = _admin(client)
        url = f"{API}/admin/personas/{pid}/interview/memories"
        assert client.post(url, headers=h, json={"text": "x"}).status_code == 403
        made = client.post(
            url,
            headers=admin,
            json={"title": "즐겨 부르시던 노래", "text": "설거지하며 '섬집 아기'를 흥얼거리셨다."},
        )
        assert made.status_code == 201, made.text
        asked = client.post(
            url, headers=admin, json={"text": "힘들던 시절 이야기", "askedOnly": True}
        ).json()["data"]
        assert asked["title"] == "기억 인터뷰 (물으면만)"
        cards = _all(
            select(Memory).where(
                Memory.source == "INTERVIEW", Memory.person_id == _get(Persona, pid).person_id
            )
        )
        assert len(cards) == 2
        assert all(m.embedding == [0.2, 0.4, 0.1] and m.embedding_status == "READY" for m in cards)
        assert fake_ai.calls["/v1/embeddings"][0]["items"][0]["text"].startswith("설거지하며")

        # 들은 답을 넣고 다시 만들기 → UPDATE 작업. 인터뷰 기억은 다시 만들어도 남는다
        client.post(
            f"{API}/admin/personas/{pid}/interview/answers",
            headers=admin,
            json={"questionId": long_q["id"], "text": "인터뷰로 들은 이야기"},
        )
        started = client.post(f"{API}/admin/personas/{pid}/rebuild", headers=admin)
        assert started.status_code == 200, started.text
        job = _wait_rebuild(client, admin, pid)
        assert job["status"] == "DONE", job
        # 들은 답이 지침 재료나 질문 답 기억(임베딩)으로 들어갔다
        rebuilt = [fake_ai.calls["/v1/persona/assembly"][-1], *fake_ai.calls["/v1/embeddings"][2:]]
        assert "인터뷰로 들은 이야기" in json.dumps(rebuilt, ensure_ascii=False)
        after = _all(
            select(Memory).where(
                Memory.source == "INTERVIEW", Memory.person_id == _get(Persona, pid).person_id
            )
        )
        assert {m.id for m in after} == {m.id for m in cards}

        # 잘못 넣은 카드는 지운다(인터뷰 기억만)
        gone = client.delete(
            f"{API}/admin/personas/{pid}/interview/memories/{asked['id']}", headers=admin
        )
        assert gone.status_code == 200
        intake_memory = _all(
            select(Memory).where(
                Memory.source == "INTAKE", Memory.person_id == _get(Persona, pid).person_id
            )
        )[0]
        assert (
            client.delete(
                f"{API}/admin/personas/{pid}/interview/memories/{intake_memory.id}", headers=admin
            ).status_code
            == 404
        )
        view = client.get(f"{API}/admin/personas/{pid}/interview", headers=admin).json()["data"]
        assert [m["title"] for m in view["memories"]] == ["즐겨 부르시던 노래"]


def test_memory_card_needs_the_ai_server():
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        assert _apply(client, h, pid).status_code == 201
        admin = _admin(client)
        resp = client.post(
            f"{API}/admin/personas/{pid}/interview/memories",
            headers=admin,
            json={"text": "기억 하나"},
        )
        assert resp.status_code == 400
        assert (
            _all(
                select(Memory).where(
                    Memory.source == "INTERVIEW", Memory.person_id == _get(Persona, pid).person_id
                )
            )
            == []
        )


def test_interview_consent_document():
    item = legal.doc("interview")
    assert item is not None and item.version == "0.9" and item.kind == "consent"
    body = legal.body("interview")
    assert "만든 분" in body and "인터뷰하지 않아요" in body
    assert "상담" not in body
    for prefix in ("interview_use", "interview_recording"):
        assert legal.default_doc(f"{prefix}:abc") == "interview"


# ── 검토에서 고친 것 ──


def test_append_keeps_the_answer_when_the_text_is_already_in_it():
    """덧붙이기(기본)에 이미 들어 있는 글(일부·같은 글)을 넣어도 긴 답을 짧은 글로
    덮어쓰지 않는다."""
    q = interview.questions()["M7-01"]
    old = "할머니는 매주 장날에 시장에 가셨어요.\n김장 때는 온 가족을 부르셨어요."
    for again in ("시장", old, "김장 때는 온 가족을 부르셨어요."):
        assert interview.merge_answer({"M7-01": old}, q, again) == {"M7-01": old}
    assert interview.merge_answer({"M7-01": old}, q, "새 이야기")["M7-01"] == f"{old}\n새 이야기"
    assert interview.merge_answer({"M7-01": old}, q, "시장", replace=True) == {"M7-01": "시장"}


def test_admin_entry_needs_the_owners_interview_consent(fake_ai):
    """신청(인터뷰 내용을 넣는 데 동의)이 없거나 동의를 거둔 대화 상대, '나 자신' 대화 상대에는
    관리자가 들은 답·기억 카드를 넣지 못한다(409). 가져온 대화 상대의 기억 카드는 따로 보지
    않는다."""
    long_q = _question("textarea")
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        admin = _admin(client)
        answers = f"{API}/admin/personas/{pid}/interview/answers"
        cards = f"{API}/admin/personas/{pid}/interview/memories"
        answer = {"questionId": long_q["id"], "text": "들은 이야기"}
        # 신청한 적이 없다
        for url, body in ((answers, answer), (cards, {"text": "기억 하나"})):
            refused = client.post(url, headers=admin, json=body)
            assert refused.status_code == 409, url
            assert refused.json()["detail"] == interview.NO_USE_CONSENT
        view = client.get(f"{API}/admin/personas/{pid}/interview", headers=admin).json()["data"]
        assert view["consents"] == {
            "interviewUse": False,
            "sensitiveReligion": False,
            "sensitiveHealth": False,
        }
        # 신청하면(동의) 넣을 수 있다
        rid = _apply(client, h, pid).json()["data"]["request"]["id"]
        assert client.post(answers, headers=admin, json=answer).status_code == 201
        assert client.post(cards, headers=admin, json={"text": "기억 하나"}).status_code == 201
        view = client.get(f"{API}/admin/personas/{pid}/interview", headers=admin).json()["data"]
        assert view["consents"]["interviewUse"] is True
        # 만든 분이 동의를 거두면 아직 끝나지 않은 신청도 취소되고 더는 넣지 못한다
        withdrawn = client.post(
            f"{API}/consents",
            headers=h,
            json={"consentType": f"interview_use:{pid}", "agreed": False},
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert _get(InterviewRequest, rid).status == "cancelled"
        assert client.post(answers, headers=admin, json=answer).status_code == 409
        assert client.post(cards, headers=admin, json={"text": "또 하나"}).status_code == 409

        # 나 자신 대화 상대(그분 = 만든 분)는 인터뷰하지 않는다
        self_pid = _persona(
            uid, basics=intake.validate_basics({"name": "가은", "relation": "나 자신"})
        )
        self_answer = client.post(
            f"{API}/admin/personas/{self_pid}/interview/answers", headers=admin, json=answer
        )
        assert self_answer.status_code == 409
        assert self_answer.json()["detail"] == interview.SELF_BLOCKED
        self_card = client.post(
            f"{API}/admin/personas/{self_pid}/interview/memories",
            headers=admin,
            json={"text": "기억 하나"},
        )
        assert self_card.status_code == 409

        # 관리자가 가져온 대화 상대의 기억 카드는 그대로 넣는다(기억을 모두 관리자가 넣는 길)
        imported = _persona(uid, origin="import")
        card = client.post(
            f"{API}/admin/personas/{imported}/interview/memories",
            headers=admin,
            json={"text": "가져온 대화 상대 기억"},
        )
        assert card.status_code == 201, card.text


def test_answers_go_only_into_a_finished_persona():
    """만드는 중인 대화 상대에는 들은 답을 넣지 않는다(만든 분의 답 저장이 덮어써 사라진다)."""
    long_q = _question("textarea")
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        assert _apply(client, h, pid).status_code == 201

        async def draft(db):
            persona = await db.get(Persona, pid)
            persona.setup_step = SetupStep.QUESTIONS
            await db.commit()

        _db(draft)
        admin = _admin(client)
        refused = client.post(
            f"{API}/admin/personas/{pid}/interview/answers",
            headers=admin,
            json={"questionId": long_q["id"], "text": "들은 이야기"},
        )
        assert refused.status_code == 400
        assert refused.json()["detail"] == interview.NOT_READY
        assert _all(select(InterviewEntry).where(InterviewEntry.persona_id == pid)) == []


def test_withdrawing_sensitive_consent_removes_interview_entries():
    """종교·건강 동의를 거두면 그 범주로 넣은 인터뷰 답 기록(InterviewEntry)도 같이 지운다."""
    category, ids = next(iter(intake.SENSITIVE.items()))
    sensitive = interview.questions()[ids[0]]
    long_q = _question("textarea")
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        assert _apply(client, h, pid).status_code == 201
        consent = f"{API}/personas/{pid}/consents"
        kind = f"sensitive_{category}"
        agree = client.post(consent, headers=h, json={"type": kind, "agreed": True})
        assert agree.status_code == 200
        admin = _admin(client)
        url = f"{API}/admin/personas/{pid}/interview/answers"
        text = sensitive["options"][0] if sensitive.get("options") else "시험용 이야기"
        added = client.post(url, headers=admin, json={"questionId": sensitive["id"], "text": text})
        assert added.status_code == 201, added.text
        other = client.post(
            url, headers=admin, json={"questionId": long_q["id"], "text": "다른 것"}
        )
        assert other.status_code == 201
        withdraw = client.post(consent, headers=h, json={"type": kind, "agreed": False})
        assert withdraw.status_code == 200
        left = _all(select(InterviewEntry).where(InterviewEntry.persona_id == pid))
        assert [e.question_id for e in left] == [long_q["id"]]
        assert sensitive["id"] not in (_get(Persona, pid).answers or {})


def test_moving_between_closed_statuses_keeps_the_closing_time():
    """끝남 → 취소처럼 닫힌 상태끼리 바꿔도 30일 정리의 기준 시각은 처음 닫은 때 그대로다."""
    first = datetime(2026, 10, 1, tzinfo=UTC)
    request = InterviewRequest(status="scheduled")
    interview.set_status(request, "done", first)
    assert request.closed_at == first
    interview.set_status(request, "cancelled", first + timedelta(days=20))
    assert (request.status, request.closed_at) == ("cancelled", first)
    interview.set_status(request, "done", first + timedelta(days=25))
    assert request.closed_at == first
    # 다시 열면 비우고, 다시 닫으면 그때부터 센다
    interview.set_status(request, "scheduled")
    assert request.closed_at is None
    interview.set_status(request, "done", first + timedelta(days=40))
    assert request.closed_at == first + timedelta(days=40)


def test_apply_cancel_loop_notifies_once_a_day_and_is_capped(monkeypatch):
    """신청·취소를 되풀이해도 같은 대화 상대의 운영 메일·디스코드 알림은 하루 한 번이고, 신청은
    하루 3번까지다(받지 않은 신청은 세지 않는다)."""
    sent: list[dict] = []
    pinged: list[str] = []

    class FakeSES:
        def send_email(self, **kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "_client", lambda: FakeSES())
    monkeypatch.setattr(notify, "send", lambda text: pinged.append(text))
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        # 잘못 적은 신청은 세지 않는다
        assert _apply(client, h, pid, phone="12345").status_code == 400
        for _ in range(3):
            assert _apply(client, h, pid).status_code == 201
            cancel = client.post(f"{API}/personas/{pid}/interview/cancel", headers=h)
            assert cancel.status_code == 200
        assert _apply(client, h, pid).status_code == 429
        end = time.time() + 5
        while not sent and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.2)
        assert len(sent) == 1
        assert len([t for t in pinged if "기억 인터뷰 신청" in t]) == 1
    events = _all(select(AnalyticsEvent).where(AnalyticsEvent.persona_id == pid))
    requested = [e.props for e in events if e.name == "interview_requested"]
    assert sorted(requested, key=len) == [
        {"method": "phone"},
        {"method": "phone", "repeat": True},
        {"method": "phone", "repeat": True},
    ]


# ── 넣은 답 빼기(interview.md: 알려 주시면 넣은 질문 답과 기억을 빼고 다시 만들어요) ──


def test_cut_takes_out_one_appended_block():
    assert interview._cut("가\n나\n다", "나") == "가\n다"
    assert interview._cut("가\n나", "나") == "가"
    assert interview._cut("나\n가", "나") == "가"
    assert interview._cut("나", "나") == ""
    assert interview._cut("가\n첫 줄\n둘째 줄\n다", "첫 줄\n둘째 줄") == "가\n다"
    # 줄 단위로 그대로 없으면(만든 분이 고쳐 썼으면) 손대지 않는다
    assert interview._cut("가나다", "나") is None
    assert interview._cut("가\n나다", "나") is None


def test_admin_removes_one_entry_and_only_what_it_added(fake_ai):
    """만든 분이 빼 달라고 하면 넣은 답 기록 하나를 지우고 그 답이 더한 부분만 뺀다. 만든 분이
    직접 적은 답, 같은 것을 말한 다른 인터뷰 답, 그 뒤 만든 분이 고친 답은 그대로 둔다."""
    with TestClient(app) as client:
        h, uid = _signup(client)
        pid = _persona(uid)
        assert _apply(client, h, pid).status_code == 201

        async def own(db):
            # 만든 분이 직접 적은 함께한 장면
            persona = await db.get(Persona, pid)
            persona.answers = {**persona.answers, "M7-01": "주인이 쓴 장면"}
            await db.commit()

        _db(own)
        admin = _admin(client)
        url = f"{API}/admin/personas/{pid}/interview/answers"

        def put(qid: str, text: str) -> str:
            made = client.post(url, headers=admin, json={"questionId": qid, "text": text})
            assert made.status_code == 201, made.text
            return made.json()["data"]["entryId"]

        def remove(eid: str, who: dict | None = None):
            return client.delete(f"{url}/{eid}", headers=who or admin)

        # 긴 글: 덧붙인 덩어리만 뺀다
        first = put("M7-01", "첫 장면")
        second = put("M7-01", "둘째 장면")
        again = put("M7-01", "주인이 쓴 장면")  # 이미 들어 있는 글이라 더한 게 없다
        assert _get(Persona, pid).answers["M7-01"] == "주인이 쓴 장면\n첫 장면\n둘째 장면"
        # 관리자만, 그 대화 상대의 기록만
        assert remove(first, h).status_code == 403
        other = _persona(uid)
        wrong = client.delete(
            f"{API}/admin/personas/{other}/interview/answers/{first}", headers=admin
        )
        assert wrong.status_code == 404
        assert remove("no-such-entry").status_code == 404
        gone = remove(first)
        assert gone.status_code == 200, gone.text
        assert gone.json()["data"] == {
            "deleted": True,
            "questionId": "M7-01",
            "changed": True,
            "answer": "주인이 쓴 장면\n둘째 장면",
            "requiredMissing": False,
        }
        # 더한 게 없던 기록을 지워도 만든 분의 글은 그대로다
        kept = remove(again).json()["data"]
        assert kept["changed"] is False and kept["answer"] == "주인이 쓴 장면\n둘째 장면"
        assert remove(second).json()["data"]["answer"] == "주인이 쓴 장면"
        assert remove(second).status_code == 404  # 이미 지웠다

        # 목록: 새로 생긴 줄만 뺀다(만든 분이 적은 줄은 남는다). 같은 줄을 말한 다른 인터뷰 답이
        # 남아 있으면 그 줄도 남고, 그 답을 지울 때 빠진다
        lines = put("C-09", "밥은 먹었냐\n차 조심해라")
        overlap = put("C-09", "차 조심해라\n일찍 자라")
        full = ["밥은 먹었냐", "차 조심해라", "일찍 자라"]
        assert _get(Persona, pid).answers["C-09"] == full
        held = remove(lines).json()["data"]
        assert held["changed"] is False and held["answer"] == full
        assert remove(overlap).json()["data"]["answer"] == ["밥은 먹었냐"]

        # 고르는 질문: 지금 답이 넣은 답과 같을 때만 비운다(그 뒤 고친 답은 그대로)
        options = interview.questions()["C-01"]["options"]
        edited = put("C-01", options[1])

        async def owner_edit(db):
            persona = await db.get(Persona, pid)
            persona.answers = {**persona.answers, "C-01": options[2]}
            await db.commit()

        _db(owner_edit)
        assert remove(edited).json()["data"]["changed"] is False
        assert _get(Persona, pid).answers["C-01"] == options[2]
        cleared = remove(put("C-01", options[1])).json()["data"]
        # 예전 대화 상대는 C-01 을 꼭 받는다: 비면 알려 준다(만든 분이 정보 고치기에서 채운다)
        assert cleared["changed"] is True and cleared["answer"] is None
        assert cleared["requiredMissing"] is True
        assert "C-01" not in _get(Persona, pid).answers

        # 만들거나 다시 만드는 중에는 빼지 않는다
        late = put("M7-01", "나중 장면")

        def set_step(value: SetupStep) -> None:
            async def go(db):
                persona = await db.get(Persona, pid)
                persona.setup_step = value
                await db.commit()

            _db(go)

        set_step(SetupStep.BUILDING)
        assert remove(late).status_code == 409
        set_step(SetupStep.READY)
        assert remove(late).status_code == 200
        assert _all(select(InterviewEntry).where(InterviewEntry.persona_id == pid)) == []

        # 다시 만들면 뺀 이야기는 지침에도 질문 답 기억에도 가지 않는다
        embedded = len(fake_ai.calls["/v1/embeddings"])
        assert client.post(f"{API}/admin/personas/{pid}/rebuild", headers=admin).status_code == 200
        assert _wait_rebuild(client, admin, pid)["status"] == "DONE"
        sent = json.dumps(
            [
                fake_ai.calls["/v1/persona/assembly"][-1],
                *fake_ai.calls["/v1/embeddings"][embedded:],
            ],
            ensure_ascii=False,
        )
        assert "주인이 쓴 장면" in sent and "밥은 먹었냐" in sent
        for removed in ("첫 장면", "둘째 장면", "나중 장면", "차 조심해라", "일찍 자라"):
            assert removed not in sent, removed
