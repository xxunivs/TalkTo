"""대화 상대 멈춤과 자동 삭제(2026-10-22 법률 자문 · WO 5-4 · SPEC §7.4).

이의 접수 → 바로 멈춤(가족도) · 만든 분 알림 → 풀기·미루기 → 7일 뒤 기존 삭제 경로로 자동 삭제와
그 안전장치(다시 읽기·통과 확인·한 번에 셋까지·비상 스위치·작업 중이면 다음에·하루 전 알림).
모든 자료는 지어낸 시험용이다(실제 가족 자료를 쓰지 않는다).
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from legal_gates import pass_subject_consent
from sqlalchemy import select
from test_sharing import (  # noqa: F401 — settings 는 이 파일에도 그대로 걸린다(autouse)
    API,
    OWNER_BASICS,
    _admin,
    _all,
    _db,
    _family,
    _get,
    _link,
    _persona,
    _preview,
    _room,
    _say,
    _set,
    _signup,
    settings,
)

from app.db.models import (
    DeletionRecord,
    JobStatus,
    OwnerNotice,
    Persona,
    PersonaJob,
    PersonaObjection,
    SetupStep,
    SubjectConsentRecording,
)
from app.main import app
from app.services import holds, notify


@pytest.fixture
def alerts(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda text, channel="events": sent.append(text))
    monkeypatch.setattr(holds, "_alerted", {})
    monkeypatch.setattr(holds, "_warned", {})
    return sent


@pytest.fixture
def roomy(settings, monkeypatch):  # noqa: F811
    """다른 시험이 남긴 지울 때가 된 줄이 상한을 먹지 않게 넉넉히 둔다(상한 시험은 따로)."""
    monkeypatch.setattr(settings, "holds_max_deletes_per_sweep", 50)
    monkeypatch.setattr(settings, "persona_auto_delete", True)
    return settings


def _notices(uid: str, kind: str) -> list[OwnerNotice]:
    return _all(
        select(OwnerNotice)
        .where(OwnerNotice.user_id == uid, OwnerNotice.kind == kind)
        .order_by(OwnerNotice.created_at)
    )


def _objections(pid: str) -> list[PersonaObjection]:
    return _all(
        select(PersonaObjection)
        .where(PersonaObjection.persona_id == pid)
        .order_by(PersonaObjection.received_at)
    )


def _sweep(now: datetime | None = None) -> int:
    return _db(lambda db: holds.sweep(db, now or datetime.now(UTC)))


def _due(pid: str, reason: str = "objection", *, at: datetime | None = None) -> None:
    """지울 때가 된 멈춘 대화 상대로(직접)."""
    when = at or datetime.now(UTC) - timedelta(minutes=1)
    _set(Persona, pid, hold_reason=reason, hold_at=when - timedelta(days=7), delete_after=when)


def _gone(pid: str) -> bool:
    return _get(Persona, pid) is None


# ────────────────────────── 1. 이의 접수·풀기·미루기 ──────────────────────────


def test_objection_stops_everyone_at_once(alerts):
    with TestClient(app) as client:
        fam = _family(client)
        room = _room(client, fam.member, fam.pid)
        assert _say(client, fam.member, room).status_code == 200
        token, _ = _link(client, fam.owner, fam.pid, agreements=["family_share"])
        admin, admin_id = _admin(client)
        url = f"{API}/admin/personas/{fam.pid}/objection"
        assert client.post(url, headers=fam.owner, json={"reason": "objection"}).status_code == 403
        bad = client.post(url, headers=admin, json={"reason": "other"})
        assert bad.status_code == 422
        before = datetime.now(UTC)
        received = client.post(
            url,
            headers=admin,
            json={
                "reason": "objection",
                "fromName": "오영희",
                "fromRelation": "딸",
                "note": "전화",
            },
        )
        assert received.status_code == 200, received.text
        data = received.json()["data"]
        assert data["already"] is False and data["refund"] is None
        assert data["replaced"] is False and data["reason"] == "objection"
        persona = _get(Persona, fam.pid)
        assert persona.hold_reason == "objection"
        delete_after = holds._aware(persona.delete_after)
        assert before + timedelta(days=7) <= delete_after <= datetime.now(UTC) + timedelta(days=7)
        [row] = _objections(fam.pid)
        assert (row.reason, row.from_name, row.admin_user_id, row.outcome) == (
            "objection",
            "오영희",
            admin_id,
            None,
        )
        assert row.owner_user_id == fam.owner_id
        # 만든 분 알림(TalkTo 가 말하는 글 — 대화 상대가 보낸 것처럼 쓰지 않는다)
        [notice] = _notices(fam.owner_id, "stopped")
        day = holds.month_day(persona.delete_after)
        assert notice.title == "'할머니' 대화 상대를 멈췄어요"
        assert notice.body == (
            f"고인의 가족께서 이의를 보내오셔서 대화를 멈췄어요. {day}에 지워요. 결제한 기간이 "
            "남아 있으면 남은 날만큼 돌려드려요. 궁금한 점은 고객센터로 알려 주세요."
        )
        assert notice.action == {"label": "자세히 보기", "route": f"/p/{fam.pid}"}
        assert any("PERSONA_HOLD" in text for text in alerts)
        assert not any("오영희" in text or fam.pid in text for text in alerts)
        # 가족은 바로 멈춘다(자리·나눈 대화는 그대로)
        paused = _say(client, fam.member, room)
        assert paused.status_code == 403 and paused.json() == {
            "detail": "지금은 이 대화 상대와 이야기할 수 없어요.",
            "code": "share_paused",
            "reason": "stopped",
        }
        assert client.get(f"{API}/conversations/{room}", headers=fam.member).status_code == 200
        assert _preview(client, token)["state"] == "unavailable"
        # 이미 멈춘 대화 상대에 또 오면 기록만 더한다(지우는 날은 그대로)
        again = client.post(url, headers=admin, json={"reason": "prior_wish"})
        assert again.json()["data"]["already"] is True
        assert again.json()["data"]["replaced"] is False
        assert _get(Persona, fam.pid).hold_reason == "objection"
        assert holds._aware(_get(Persona, fam.pid).delete_after) == delete_after
        assert len(_objections(fam.pid)) == 2 and len(_notices(fam.owner_id, "stopped")) == 1
        # 관리자 목록
        rows = client.get(f"{API}/admin/holds", headers=admin).json()["data"]
        row = next(r for r in rows if r["personaId"] == fam.pid)
        assert row["reason"] == "objection" and row["title"] == "할머니"
        assert "***" in row["ownerMasked"]

        # 미루기(1~30일)
        extend = f"{API}/admin/personas/{fam.pid}/hold/extend"
        assert client.post(extend, headers=admin, json={"days": 31}).status_code == 422
        moved = client.post(extend, headers=admin, json={"days": 3, "note": "가족 통화 뒤"})
        assert moved.status_code == 200
        assert holds._aware(_get(Persona, fam.pid).delete_after) == delete_after + timedelta(days=3)
        assert "가족 통화 뒤" in _objections(fam.pid)[0].note

        # 풀기(이의 철회): 세 칸을 비우고 열린 이의는 cancelled, 만든 분 알림
        cleared = client.post(
            f"{API}/admin/personas/{fam.pid}/hold/clear", headers=admin, json={"note": "철회"}
        )
        assert cleared.status_code == 200
        persona = _get(Persona, fam.pid)
        assert (persona.hold_reason, persona.hold_at, persona.delete_after) == (None, None, None)
        assert {r.outcome for r in _objections(fam.pid)} == {"cancelled"}
        [resumed] = _notices(fam.owner_id, "resumed")
        assert resumed.title == "'할머니' 대화 상대를 다시 쓸 수 있어요"
        assert resumed.body == "확인을 마쳐 '할머니' 대화 상대를 다시 쓸 수 있어요."
        assert _say(client, fam.member, room).status_code == 200
        # 멈추지 않은 것은 풀거나 미루지 못한다
        assert client.post(extend, headers=admin, json={"days": 1}).status_code == 409
        not_held = client.post(f"{API}/admin/personas/{fam.pid}/hold/clear", headers=admin, json={})
        assert not_held.status_code == 409
        assert (
            client.post(
                f"{API}/admin/personas/nope/objection", headers=admin, json={"reason": "objection"}
            ).status_code
            == 404
        )


CHILD = OWNER_BASICS | {
    "status": "alive",
    "relation": "딸",
    "myRelation": "어머니",
    "minor": True,
    "birthYm": "2010-01",
    "guardianBasis": "both_agreed",
}


def test_objection_takes_over_a_system_hold(roomy, alerts):
    """아이 본인 동의 기한 넘김으로 멈춘 중에 다른 쪽 부모의 이의가 오면 까닭을 이의로 바꾸고 지우는
    날은 더 이른 쪽으로 — 그 뒤 동의 녹음이 통과해도 이의가 풀리지 않고 지운다(WO 4)."""
    now = datetime.now(UTC)
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        late = _persona(uid, basics=CHILD)
        soon = _persona(uid, basics=CHILD)
        # 관리자가 미뤄 둔 것(10일 뒤)과 곧 지울 것(2일 뒤)
        for pid, days in ((late, 10), (soon, 2)):
            _set(
                Persona,
                pid,
                hold_reason="minor_consent_missing",
                hold_at=now - timedelta(days=1),
                delete_after=now + timedelta(days=days),
            )
        admin, _ = _admin(client)
        url = f"{API}/admin/personas/{late}/objection"
        taken = client.post(url, headers=admin, json={"reason": "other_parent", "fromName": "김철"})
        assert taken.status_code == 200, taken.text
        data = taken.json()["data"]
        assert (data["already"], data["replaced"], data["reason"]) == (True, True, "other_parent")
        persona = _get(Persona, late)
        assert persona.hold_reason == "other_parent"
        assert holds._aware(persona.delete_after) <= datetime.now(UTC) + timedelta(days=7)
        assert holds._aware(persona.hold_at) < now  # 멈춘 때는 그대로
        [notice] = _notices(uid, "stopped")
        assert notice.body.startswith("아이의 다른 쪽 부모님께서 반대 의견을 보내오셔서")
        kept = client.post(
            f"{API}/admin/personas/{soon}/objection", headers=admin, json={"reason": "other_parent"}
        )
        assert kept.json()["data"]["replaced"] is True
        assert holds._aware(_get(Persona, soon).delete_after) <= now + timedelta(days=2)

        # 동의 녹음이 통과해도 열린 이의가 있으면 저절로 풀지 않는다(관리자가 직접 푼다)
        pass_subject_consent(None, late, "minor_teen")
        _set(Persona, late, hold_reason="minor_consent_missing")  # 예전 줄(까닭을 안 바꾼 것)

        async def release(db):
            return await holds.clear_for_consent(db, await db.get(Persona, late))

        assert _db(release) is False
        assert _get(Persona, late).hold_reason == "minor_consent_missing"
        assert any("열린 이의가 있어" in text for text in alerts)
        assert [r.outcome for r in _objections(late)] == [None]
        # 지울 때가 되면 이의대로 지운다(통과로 풀지 않는다)
        _due(late, "minor_consent_missing")
        _sweep()
        assert _gone(late) and not _notices(uid, "resumed")
        assert [r.outcome for r in _objections(late)] == ["deleted"]
        _set(Persona, soon, hold_reason=None, hold_at=None, delete_after=None)


def test_stopped_bodies_by_reason():
    persona = Persona(
        name="x",
        basics={"addressTerm": "엄마"},
        hold_reason="subject_refused",
        delete_after=datetime(2026, 10, 30, 3, tzinfo=UTC),
        setup_step=SetupStep.QUESTIONS,
    )
    assert holds.stopped_body(persona) == (
        "그분이 동의 녹음에서 원하지 않으셔서 이 대화 상대는 만들 수 없어요. 올리신 녹음과 자료는 "
        "10월 30일에 지워요. 지금 바로 지우실 수도 있어요."
    )
    persona.setup_step = SetupStep.READY
    assert holds.stopped_body(persona).endswith(
        "지금 바로 지우실 수도 있어요. 결제한 기간이 남아 있으면 남은 날만큼 돌려드려요."
    )
    persona.hold_reason = "minor_consent_missing"
    assert holds.stopped_body(persona) == (
        "아이 본인의 동의를 기한까지 받지 못해 대화를 멈췄어요. 10월 30일에 지워요. 그 전에 동의 "
        "녹음이 확인되면 다시 쓸 수 있어요."
    )
    persona.hold_reason = "other_parent"
    assert holds.stopped_body(persona).startswith("아이의 다른 쪽 부모님께서 반대 의견을")
    persona.hold_reason = "subject_withdrew"
    assert holds.stopped_body(persona).startswith("그분께서 그만두길 원하셔서 대화를 멈췄어요.")
    assert holds.blocked_message(persona) == (
        "이 대화 상대는 지금 멈췄어요. 10월 30일에 지워요. 자세한 내용은 알림을 확인해 주세요."
    )
    assert holds.view(persona)["reason"] == "subject_withdrew"


def test_notice_templates_speak_as_talkto():
    """이 레인의 알림 글(멈춤·풀림·지움·나이 바뀜·기한·가족 범위·확인 문자, SPEC §10.1): 대화 상대는
    늘 notices.persona_label('…' 대화 상대)로 가리키고 목적격 '를'·소유격 '의'만 쓴다. 대화 상대가
    주어가 되거나 보낸 것처럼 쓰지 않는다."""
    import inspect
    import re

    from app.services import minors, sharing

    for module in (holds, minors, sharing):
        source = inspect.getsource(module)
        assert "notices.persona_label(" in source, module.__name__
        assert not re.search(r"\{label\}(이|가|을|께서)", source), module.__name__
        assert not re.search(r"기다려요|보고 싶|말씀하셨어요", source), module.__name__


# ────────────────────────── 2. 자동 삭제 ──────────────────────────


def test_sweep_deletes_through_the_existing_path(roomy, alerts):
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid)
        admin, _ = _admin(client)
        client.post(
            f"{API}/admin/personas/{pid}/objection", headers=admin, json={"reason": "prior_wish"}
        )
        _due(pid, "prior_wish")
        assert _sweep() >= 1
        assert _gone(pid)
        [record] = _all(select(DeletionRecord).where(DeletionRecord.persona_id == pid))
        assert (record.kind, record.requested_by) == ("prior_wish", "system")
        assert [r.outcome for r in _objections(pid)] == ["deleted"]
        [notice] = _notices(uid, "deleted")
        assert notice.title == "'할머니' 대화 상대를 지웠어요"
        assert notice.body.startswith(
            "알려 드린 대로 '할머니' 대화 상대를 지웠어요. TalkTo 서버에서"
        )
        assert "서버 백업에는" in notice.body and notice.action is None
        assert any("PERSONA_AUTO_DELETED" in text for text in alerts)


def test_sweep_safeguards(roomy, alerts):
    now = datetime.now(UTC)
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        # 아직 때가 아님 · 그사이 풀림
        later = _persona(uid)
        _due(later, at=now + timedelta(days=2))
        cleared = _persona(uid)
        _due(cleared)
        _set(Persona, cleared, hold_reason=None, hold_at=None, delete_after=None)
        # 아이 본인 동의 기한 넘김인데 그사이 동의 녹음이 통과함 → 지우지 않고 풀어 준다
        child = _persona(
            uid,
            basics=OWNER_BASICS
            | {
                "status": "alive",
                "relation": "딸",
                "myRelation": "어머니",
                "minor": True,
                "birthYm": "2010-01",
            },
        )
        _due(child, "minor_consent_missing")
        pass_subject_consent(None, child, "minor_teen")
        # 그분이 원하지 않음인데 그 판정이 바뀜(원하지 않음 줄이 없음) → 지우지 않고 알림
        refused = _persona(uid, basics=OWNER_BASICS | {"status": "alive", "minor": False})
        _due(refused, "subject_refused")
        # 작업이 도는 중 → 다음에 다시
        busy = _persona(uid)
        _due(busy)

        async def running(db):
            db.add(PersonaJob(persona_id=busy, kind="UPDATE", status=JobStatus.RUNNING))
            await db.commit()

        _db(running)
        _sweep()
        for pid in (later, cleared, child, refused, busy):
            assert not _gone(pid), pid
        assert _get(Persona, child).hold_reason is None
        assert _notices(uid, "resumed")
        assert _get(Persona, refused).hold_reason == "subject_refused"
        assert any("판정이 바뀌어" in text for text in alerts)

        # 원하지 않음 줄이 있으면 지운다
        async def refusal(db):
            db.add(
                SubjectConsentRecording(
                    persona_id=refused,
                    attempt=1,
                    mode="adult",
                    script_doc="subject_consent_script",
                    script_version="0.1",
                    words="사과 바다",
                    words_issued_at=now - timedelta(days=8),
                    expires_at=now - timedelta(days=8),
                    status="refused",
                    refused_flag=True,
                )
            )
            await db.commit()

        _db(refusal)

        async def finish(db):
            job = await db.scalar(select(PersonaJob).where(PersonaJob.persona_id == busy))
            job.status = JobStatus.DONE
            await db.commit()

        _db(finish)
        _sweep()
        assert _gone(refused) and _gone(busy) and not _gone(later)
        _set(Persona, later, hold_reason=None, hold_at=None, delete_after=None)


def test_sweep_limit_switch_and_tomorrow_warning(settings, alerts, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "holds_max_deletes_per_sweep", 3)
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        old = datetime(2000, 1, 1, tzinfo=UTC)
        pids = [_persona(uid) for _ in range(5)]
        for n, pid in enumerate(pids):
            _due(pid, at=old + timedelta(minutes=n))
        # 비상 스위치: 지우지 않고 알림만
        monkeypatch.setattr(settings, "persona_auto_delete", False)
        assert _sweep() == 0 and not any(_gone(pid) for pid in pids)
        assert any("자동 삭제가 꺼져 있어요" in text for text in alerts)
        monkeypatch.setattr(settings, "persona_auto_delete", True)
        # 한 번에 셋까지만, 남으면 알림
        assert _sweep() == 3
        assert [_gone(pid) for pid in pids] == [True, True, True, False, False]
        assert any("더 있어요" in text for text in alerts)
        monkeypatch.setattr(settings, "holds_max_deletes_per_sweep", 50)
        _sweep()
        assert all(_gone(pid) for pid in pids)
        # 지우기 하루 전 알림(건수만, 한 번)
        tomorrow = _persona(uid)
        _due(tomorrow, at=datetime.now(UTC) + timedelta(hours=20))
        alerts.clear()
        _sweep()
        _sweep()
        warned = [text for text in alerts if "PERSONA_DELETE_TOMORROW" in text]
        assert len(warned) == 1 and tomorrow not in warned[0]
        _set(Persona, tomorrow, hold_reason=None, hold_at=None, delete_after=None)
