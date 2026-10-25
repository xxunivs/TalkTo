"""'대화가 어려우신 분'(unable) 제외(2026-10-22 법률 자문 · WO 2 · SPEC §4).

새로 고를 수 없고, 예전에 고른 대화 상대는 지우지 않고 그 상태 그대로 고칠 수 있으며(상태는 바꾸지
못함), 관리자 목록으로 보고한다. 모든 자료는 지어낸 시험용이다.
"""

import pytest
from fastapi.testclient import TestClient
from legal_gates import declare_bereaved
from test_sharing import (  # noqa: F401 — settings 는 이 파일에도 그대로 걸린다(autouse)
    API,
    OWNER_BASICS,
    _admin,
    _db,
    _persona,
    _signup,
    settings,
)

from app.db.models import LifeStatus, Person
from app.main import app
from app.services import gate, intake

UNABLE = OWNER_BASICS | {"status": "unable"}


def test_statuses_drop_unable_but_keep_the_legacy_mapping():
    assert intake.STATUSES == {"alive": "네, 살아 계세요", "deceased": "아니요, 돌아가셨어요"}
    assert intake.LEGACY_STATUSES == {"unable": "대화가 어려우신 분(예전에 고른 상태)"}
    # 예전 줄의 목소리 확인·생존 표시가 그대로 맞게 남긴다
    assert intake.LIFE_STATUS["unable"] == LifeStatus.LIVING
    with TestClient(app) as client:
        meta = client.get(f"{API}/intake/meta").json()["data"]
    assert "unable" not in meta["statuses"]


def test_new_unable_is_refused():
    for previous in (None, OWNER_BASICS, OWNER_BASICS | {"status": "alive", "minor": False}):
        with pytest.raises(intake.IntakeError) as error:
            intake.validate_basics(UNABLE, previous=previous)
        assert str(error.value) == "지금 그분이 살아 계신지 골라 주세요."
    with pytest.raises(intake.IntakeError) as error:
        intake.validate_basics(OWNER_BASICS | {"status": "모름"})
    assert str(error.value) == intake.STATUS_REQUIRED


def test_existing_unable_keeps_its_status_and_cannot_change_it():
    previous = intake.validate_basics(OWNER_BASICS) | {"status": "unable"}
    kept = intake.validate_basics(UNABLE | {"calledMe": "아가"}, previous=previous)
    assert kept["status"] == "unable" and kept["calledMe"] == "아가"
    assert not any(key in kept for key in intake.MINOR_KEYS)
    for status in ("alive", "deceased", None):
        with pytest.raises(intake.IntakeError) as error:
            intake.validate_basics(OWNER_BASICS | {"status": status}, previous=previous)
        assert (
            str(error.value) == "이 대화 상대의 상태는 지금 바꿀 수 없어요. 고객센터로 알려 주세요."
        )
    # '나 자신'으로 바꿔 피하지도 못한다
    with pytest.raises(intake.IntakeError) as error:
        intake.validate_basics({"name": "홍길동", "relation": "나 자신"}, previous=previous)
    assert (
        str(error.value)
        == "다른 분의 대화 상대를 '나 자신'으로 바꿀 수 없어요. 새로 만들어 주세요."
    )


def test_question_titles_stay_present_tense_for_unable():
    question = next(q for q in intake.QUESTIONS if q.get("titleLiving"))
    assert intake.question_title(question, UNABLE) == question["titleLiving"]


def _delete_people(*persona_ids: str) -> None:
    async def go(db):
        from app.db.models import Persona

        for pid in persona_ids:
            persona = await db.get(Persona, pid)
            if persona is not None:
                await db.delete(await db.get(Person, persona.person_id))
        await db.commit()

    _db(go)


def test_existing_unable_is_saved_through_the_api():
    """PUT /personas/{id} 가 원래 basics 를 previous 로 넘겨 예전 unable 대화 상대도 저장되고,
    상태 바꾸기만 막힌다."""
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid, basics=UNABLE)
        try:
            same = client.put(f"{API}/personas/{pid}", headers=h, json={"basics": UNABLE})
            assert same.status_code == 200, same.text
            assert same.json()["data"]["updating"] is False
            moved = client.put(
                f"{API}/personas/{pid}", headers=h, json={"basics": UNABLE | {"status": "alive"}}
            )
            assert moved.status_code == 400 and moved.json()["detail"] == intake.STATUS_LOCKED
        finally:
            _delete_people(pid)


def test_admin_lists_unable_and_not_guardian_children_only():
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        unable = _persona(uid, basics=UNABLE)
        child = _persona(
            uid,
            basics=OWNER_BASICS
            | {
                "status": "alive",
                "relation": "손녀",
                "myRelation": "할머니",
                "minor": True,
                "birthYm": "2016-05",
                "notGuardian": True,
            },
        )
        others = [
            _persona(uid),
            _persona(uid, basics=OWNER_BASICS | {"status": "alive", "minor": False}),
        ]
        try:
            assert client.get(f"{API}/admin/personas/unable", headers=h).status_code == 403
            admin, _ = _admin(client)
            listed = client.get(f"{API}/admin/personas/unable", headers=admin)
            assert listed.status_code == 200, listed.text
            rows = {row["personaId"]: row for row in listed.json()["data"]}
            assert rows[unable]["kind"] == "unable" and rows[child]["kind"] == "minor_not_guardian"
            assert not set(others) & set(rows)
            row = rows[unable]
            assert set(row) == {
                "personaId",
                "kind",
                "title",
                "ownerMasked",
                "step",
                "hasVoice",
                "voiceProvider",
                "createdAt",
                "readyAt",
                "lastChatAt",
                "members",
            }
            assert row["title"] == "할머니" and row["step"] == "READY" and row["members"] == 0
            # 전체 이메일은 싣지 않는다
            assert "@" in row["ownerMasked"] and "***" in row["ownerMasked"]
            # 버튼 없음: 지우지 않는다
            assert client.get(f"{API}/personas/{unable}", headers=h).status_code == 200
        finally:
            _delete_people(unable, child, *others)


def test_admin_lists_status_changed_until_declared():
    """살아 계신 분을 돌아가신 분으로 바꿨는데(gate.BEREAVED_REQUIRED) 유족 선언이 없으면
    status_changed 로 보이고, 선언을 적으면 빠진다. 처음부터 돌아가신 분은 보이지 않는다.
    떠나신 부모 아닌 분의 아이는 minor_not_guardian 이 아니라 status_changed 로 본다."""
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        changed = _persona(
            uid, basics=OWNER_BASICS | {"minor": False, gate.BEREAVED_REQUIRED: True}
        )
        child = _persona(
            uid,
            basics=OWNER_BASICS
            | {
                "relation": "손녀",
                "myRelation": "할머니",
                "minor": True,
                "birthYm": "2016-05",
                "notGuardian": True,
                gate.BEREAVED_REQUIRED: True,
            },
        )
        plain = _persona(uid)
        try:
            admin, _ = _admin(client)

            def listed() -> dict:
                rows = client.get(f"{API}/admin/personas/unable", headers=admin).json()["data"]
                return {row["personaId"]: row["kind"] for row in rows}

            kinds = listed()
            assert kinds[changed] == "status_changed" and kinds[child] == "status_changed"
            assert plain not in kinds
            declare_bereaved(None, changed)
            kinds = listed()
            assert changed not in kinds and kinds[child] == "status_changed"
        finally:
            _delete_people(changed, child, plain)
