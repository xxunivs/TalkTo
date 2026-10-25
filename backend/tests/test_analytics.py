"""사용 통계: 서버 이벤트·화면 이벤트(허용 이름만)·개발자 화면(/admin/stats)·CSV·1년 보관."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_billing import API, _admin, _bare_ready
from test_persona_setup import BASICS, FakeAI, _signup, fake_ai  # noqa: F401

from app import main as main_module
from app.core.config import get_settings
from app.db.models import AnalyticsEvent, ErrorLog, User
from app.db.session import SessionLocal
from app.main import app
from app.services import analytics, billing


@pytest.fixture(autouse=True)
def prelaunch(monkeypatch):
    monkeypatch.setattr(get_settings(), "payments_open", False)
    monkeypatch.setattr(get_settings(), "prelaunch_conversations", None)


def _run(fn):
    async def go():
        async with SessionLocal() as db:
            return await fn(db)

    return asyncio.run(go())


def _user_id(email: str) -> str:
    return _run(lambda db: db.scalar(select(User.id).where(User.email == email)))


def _events(user_id: str | None = None, name: str | None = None) -> list[AnalyticsEvent]:
    async def go(db):
        query = select(AnalyticsEvent).order_by(AnalyticsEvent.created_at)
        if user_id is not None:
            query = query.where(AnalyticsEvent.user_id == user_id)
        if name is not None:
            query = query.where(AnalyticsEvent.name == name)
        return (await db.scalars(query)).all()

    return _run(go)


def _chat(client: TestClient, h: dict, pid: str, n: int) -> None:
    cid = client.post(f"{API}/personas/{pid}/conversation", headers=h).json()["data"][
        "conversationId"
    ]
    for i in range(n):
        sent = client.post(
            f"{API}/conversations/{cid}/messages/text", headers=h, json={"content": f"안녕 {i}"}
        )
        assert sent.status_code == 200, sent.text


def test_typical_flow_records_events_without_content(fake_ai: FakeAI):  # noqa: F811
    email = "events-flow@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        uid = _user_id(email)
        login = client.post(
            f"{API}/auth/login", json={"email": email, "password": "password123"}
        )
        assert login.status_code == 200
        pid = _bare_ready(client, h)
        _chat(client, h, pid, 1)
        client.post(f"{API}/billing/requests", headers=h, json={"personaId": pid, "plan": "family"})
        client.delete(f"{API}/billing/requests/{pid}", headers=h)

        events = _events(uid)
        names = [e.name for e in events]
        for expected in (
            "signup",
            "consent_accepted",
            "login",
            "persona_created",
            "message_sent",
            "subscription_requested",
            "subscription_request_canceled",
        ):
            assert expected in names, names
        by_name = {e.name: e for e in events}
        assert by_name["signup"].props == {"method": "email"}
        assert by_name["login"].props == {"method": "email"}
        assert by_name["persona_created"].props == {"status": "deceased"}
        assert by_name["persona_created"].persona_id == pid
        assert by_name["message_sent"].props == {"kind": "text", "textOnly": False}
        assert by_name["subscription_requested"].props == {"plan": "family"}
        assert by_name["subscription_request_canceled"].props == {"plan": "family"}
        # 대화 내용·이름·이메일은 남기지 않는다
        dumped = json.dumps([e.props for e in events], ensure_ascii=False)
        for secret in ("안녕", BASICS["name"], BASICS["myName"], email, "홍길동"):
            assert secret not in dumped


def test_client_events_whitelist_and_sanitized_props():
    email = "events-client@example.com"
    with TestClient(app) as client:
        h = _signup(client, email)
        uid = _user_id(email)
        sent = client.post(
            f"{API}/analytics/events",
            headers=h,
            json={
                "name": "screen_view",
                "props": {"screen": "x" * 200, "email": email, "nested": {"a": 1}},
            },
        )
        assert sent.status_code == 204
        viewed = {"name": "plans_viewed", "props": {"withPersona": True}}
        assert client.post(f"{API}/analytics/events", headers=h, json=viewed).status_code == 204
        assert client.post(
            f"{API}/analytics/events", headers=h, json={"name": "more_tile_clicked"}
        ).status_code == 204
        # 예전 화면이 보내던 이름(똑 닮은 목소리 타일)은 이제 남기지 않는다
        assert client.post(
            f"{API}/analytics/events", headers=h, json={"name": "pvc_tile_clicked"}
        ).status_code == 204
        # 모르는 이름은 조용히 무시한다
        ignored = client.post(
            f"{API}/analytics/events",
            headers=h,
            json={"name": "message_sent", "props": {"kind": "text"}},
        )
        assert ignored.status_code == 204
        assert client.post(
            f"{API}/analytics/events", headers=h, json={"name": "hack", "props": {}}
        ).status_code == 204
        # 로그인해야 보낸다
        assert client.post(
            f"{API}/analytics/events", json={"name": "screen_view"}
        ).status_code == 401

        events = {e.name: e for e in _events(uid)}
        assert events["screen_view"].props == {"screen": "x" * 80}
        assert events["plans_viewed"].props == {"withPersona": True}
        assert events["more_tile_clicked"].props == {}
        assert "pvc_tile_clicked" not in events
        assert "message_sent" not in events and "hack" not in events


def test_sanitize_and_track_never_break(monkeypatch):
    clean = analytics.sanitize(
        {
            "ok": "a" * 100,
            "n": 3,
            "f": 1.23456,
            "b": True,
            "none": None,
            "list": [1, 2],
            "dict": {"a": 1},
            "bad key": 1,
            "nan": float("nan"),
        }
    )
    assert clean == {"ok": "a" * 80, "n": 3, "f": 1.235, "b": True, "none": None}

    def broken():
        raise RuntimeError("db down")

    monkeypatch.setattr(analytics, "SessionLocal", broken)
    with TestClient(app) as client:
        # 통계를 남기지 못해도 가입은 된다
        h = _signup(client, "events-broken@example.com")
        assert client.get(f"{API}/auth/me", headers=h).status_code == 200
    asyncio.run(analytics.track(None, "signup", method="email"))  # 예외를 내지 않는다


def _stats(client: TestClient, h: dict, days: int = 7) -> dict:
    response = client.get(f"{API}/admin/stats", headers=h, params={"days": days})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _funnel(stats: dict) -> dict[str, int]:
    return {step["key"]: step["users"] for step in stats["funnel"]}


def test_admin_stats_shape_numbers_and_admin_only(fake_ai: FakeAI):  # noqa: F811
    a_email, b_email = "stats-a@example.com", "stats-b@example.com"
    with TestClient(app) as client:
        admin = _admin(client)
        before = _stats(client, admin)

        # A: 가입 → 다 만든 대화 상대 → 대화 10번 → 가족 요금제 신청, 화면 몇 개
        a = _signup(client, a_email)
        pid = _bare_ready(client, a)
        _chat(client, a, pid, 10)
        client.post(f"{API}/billing/requests", headers=a, json={"personaId": pid, "plan": "family"})
        for screen in ("plans", "plans", "stats_home_probe"):
            client.post(
                f"{API}/analytics/events",
                headers=a,
                json={"name": "screen_view", "props": {"screen": screen}},
            )
        # B: 가입하고 대화 상대를 만들다 탈퇴
        b = _signup(client, b_email)
        b_id = _user_id(b_email)
        b_pid = client.post(f"{API}/personas", headers=b, json=BASICS).json()["data"]["id"]
        assert any(e.persona_id == b_pid for e in _events(b_id))
        gone = client.request("DELETE", f"{API}/auth/me", headers=b, json={"confirm": "탈퇴"})
        assert gone.status_code == 200
        # 만들기 이벤트(뒤에서 도는 일이 남기는 것)
        asyncio.run(analytics.track(None, "build_started", user_id=_user_id(a_email)))
        asyncio.run(
            analytics.track(None, "build_done", user_id=_user_id(a_email), seconds=120)
        )

        after = _stats(client, admin)
        assert set(after) >= {
            "generatedAt", "paymentsOpen", "days", "totals", "active", "funnel",
            "personasByStep", "lifeStatus", "voicesByProvider", "requestsByPlan", "builds",
            "daily", "topScreens", "recentRequests", "recentSignups", "aiProviderTrouble",
            "voiceProviderTrouble",
        }  # fmt: skip
        assert after["paymentsOpen"] is False and after["days"] == 7
        assert set(after["totals"]) == {
            "users", "deletedUsers", "personas", "personasReady", "conversations", "messages",
            "voiceMessages", "replyPlays", "subscriptionRequests", "feedbackUp", "feedbackDown",
            "errors24h",
        }  # fmt: skip

        def delta(key: str) -> int:
            return after["totals"][key] - before["totals"][key]

        assert delta("users") == 1 and delta("deletedUsers") == 1
        assert delta("personas") == 1 and delta("personasReady") == 1
        assert delta("messages") == 10 and delta("voiceMessages") == 0
        assert delta("subscriptionRequests") == 1 and delta("conversations") == 1

        f0, f1 = _funnel(before), _funnel(after)
        assert [s["key"] for s in after["funnel"]] == [
            "signed_up", "consented", "persona_created", "recordings_uploaded", "build_done",
            "first_message", "engaged", "subscription_requested",
        ]  # fmt: skip
        assert after["funnel"][0]["label"] == "가입"
        gained = {k: f1[k] - f0[k] for k in f1}
        assert gained == {
            "signed_up": 1,
            "consented": 1,
            "persona_created": 1,
            "recordings_uploaded": 1,  # 다 만든 사람은 올린 것으로 센다(깔때기는 늘 줄어든다)
            "build_done": 1,
            "first_message": 1,
            "engaged": 1,
            "subscription_requested": 1,
        }
        assert after["requestsByPlan"]["family"] - before["requestsByPlan"]["family"] == 1
        assert set(after["requestsByPlan"]) == {"memories", "family"}
        assert after["lifeStatus"]["deceased"] - before["lifeStatus"]["deceased"] == 1
        assert set(after["lifeStatus"]) == {"deceased", "alive", "unable"}
        assert after["voicesByProvider"]["none"] - before["voicesByProvider"]["none"] == 1
        # fish(지금 목소리)와 다시 만들기 전의 예전 목소리를 따로 센다
        assert {"fish", "minimax", "elevenlabs", "elevenlabs_pvc"} <= set(after["voicesByProvider"])
        assert after["personasByStep"]["READY"] - before["personasByStep"]["READY"] == 1
        assert after["builds"]["done"] - before["builds"]["done"] == 1
        assert after["builds"]["started"] - before["builds"]["started"] == 1
        assert after["builds"]["avgSeconds"] is not None
        assert after["active"]["d1"] >= 1 and after["active"]["d30"] >= after["active"]["d1"]

        daily = after["daily"]
        assert len(daily) == 7
        today = datetime.now(billing.KST).date().isoformat()
        assert daily[-1]["date"] == today
        keys = {"date", "signups", "activeUsers", "messages", "builds", "requests"}
        assert set(daily[-1]) == keys
        before_today = next(d for d in before["daily"] if d["date"] == today)
        assert daily[-1]["messages"] - before_today["messages"] == 10
        assert daily[-1]["signups"] - before_today["signups"] == 2  # 탈퇴한 사람도 가입은 했다
        assert daily[-1]["requests"] - before_today["requests"] == 1

        screens = {s["screen"]: s for s in after["topScreens"]}
        assert screens["stats_home_probe"] == {"screen": "stats_home_probe", "views": 1, "users": 1}
        assert screens["plans"]["views"] >= 2

        newest = after["recentRequests"][0]
        assert newest["email"] == a_email and newest["planName"] == "가족"
        assert newest["status"] == "REQUESTED" and newest["personaTitle"] == "할머니"
        assert set(newest) == {
            "createdAt", "email", "name", "personaTitle", "relation", "plan", "planName", "status",
        }  # fmt: skip
        signups = [s["email"] for s in after["recentSignups"]]
        assert a_email in signups and b_email not in signups
        assert not any((e or "").startswith("deleted-") for e in signups)
        assert next(s for s in after["recentSignups"] if s["email"] == a_email)["method"] == "email"
        # 이메일을 안 준 소셜 가입자도 이름으로 알아본다(관리자 표의 빈칸 대신)
        assert all("name" in s for s in after["recentSignups"])

        # 탈퇴한 사람의 이벤트는 남지만 누구의 것인지는 지운다
        assert _events(b_id) == []
        assert _events(name="account_deleted")
        # 대화 상대 번호로도 다시 이어지지 않는다(대화 횟수·결제 기록에는 그 번호가 남는다)
        assert not any(e.persona_id == b_pid for e in _events())

        # 관리자만 본다
        assert client.get(f"{API}/admin/stats", headers=a).status_code == 403
        assert client.get(f"{API}/admin/stats/export", headers=a).status_code == 403


def test_csv_export(fake_ai: FakeAI):  # noqa: F811
    email = "stats-csv@example.com"
    with TestClient(app) as client:
        admin = _admin(client)
        h = _signup(client, email)
        pid = _bare_ready(client, h)
        body = {"personaId": pid, "plan": "memories"}
        assert client.post(f"{API}/billing/requests", headers=h, json=body).status_code == 200

        requests = client.get(
            f"{API}/admin/stats/export", headers=admin, params={"kind": "requests", "days": 30}
        )
        assert requests.status_code == 200
        assert requests.headers["content-type"].startswith("text/csv")
        assert requests.headers["content-disposition"].startswith("attachment;")
        assert requests.content.startswith(b"\xef\xbb\xbf")
        text = requests.content.decode("utf-8-sig")
        lines = text.strip().splitlines()
        header = "created_at,updated_at,email,persona_title,relation,plan,plan_name,status"
        assert lines[0] == header
        assert any(email in line and "추억" in line and "REQUESTED" in line for line in lines)

        events = client.get(
            f"{API}/admin/stats/export", headers=admin, params={"kind": "events", "days": 30}
        )
        assert events.status_code == 200 and events.content.startswith(b"\xef\xbb\xbf")
        rows = events.content.decode("utf-8-sig").strip().splitlines()
        assert rows[0] == "id,created_at,user_id,persona_id,name,props"
        assert any("subscription_requested" in r and '""plan"": ""memories""' in r for r in rows)
        # 가입자는 기간 없이 처음부터 전부
        signups = client.get(f"{API}/admin/stats/export", headers=admin, params={"kind": "signups"})
        assert signups.status_code == 200 and signups.content.startswith(b"\xef\xbb\xbf")
        rows = signups.content.decode("utf-8-sig").strip().splitlines()
        assert rows[0] == "created_at,email,name,method,status,personas"
        assert any(email in r and ",email,ACTIVE,1" in r for r in rows)
        bad = client.get(f"{API}/admin/stats/export", headers=admin, params={"kind": "users"})
        assert bad.status_code == 422


def test_events_older_than_a_year_are_removed_once_a_day(monkeypatch):
    now = datetime.now(UTC)

    async def seed(db):
        for screen, age in (("old", 400), ("recent", 300)):
            db.add(
                AnalyticsEvent(
                    name="screen_view",
                    props={"screen": screen},
                    created_at=now - timedelta(days=age),
                )
            )
            # 오류 기록도 1년이 지나면 지운다(개인정보 수집·이용 안내)
            db.add(ErrorLog(message=f"cleanup-{screen}", created_at=now - timedelta(days=age)))
        await db.commit()

    async def error_messages(db):
        rows = await db.scalars(select(ErrorLog.message).where(ErrorLog.message.like("cleanup-%")))
        return set(rows.all())

    _run(seed)
    monkeypatch.setattr(main_module, "_last_cleanup", None)
    assert asyncio.run(main_module.cleanup_daily(now)) is True
    screens = [e.props.get("screen") for e in _events(name="screen_view")]
    assert "old" not in screens and "recent" in screens
    assert _run(error_messages) == {"cleanup-recent"}
    # 하루에 한 번만 돈다
    assert asyncio.run(main_module.cleanup_daily(now + timedelta(hours=1))) is False
    assert asyncio.run(main_module.cleanup_daily(now + timedelta(days=1, minutes=1))) is True
