"""만 19세 미만 대화 상대(2026-10-22 법률 자문 · WO 4 · SPEC §6).

기본 정보 검사(validate_basics 표), 확인 문자 공급자(off · log · manual), 기존 대화 상대의 미성년
여부(PUT /minor), 법정대리인 확인과 확인 문자, 가족 공유 범위, 나이가 바뀔 때(minors.sweep — 시계를
넣어 본다). 모든 자료는 지어낸 시험용이다(실제 가족 자료를 쓰지 않는다).
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from legal_gates import pass_subject_consent
from sqlalchemy import select
from test_sharing import (  # noqa: F401 — settings 는 이 파일에도 그대로 걸린다(autouse)
    API,
    OWNER_BASICS,
    PROFILE,
    _accept,
    _admin,
    _all,
    _db,
    _get,
    _link,
    _persona,
    _room,
    _say,
    _set,
    _signup,
    settings,
)

from app.db.models import (
    ConsentRecord,
    OwnerNotice,
    Person,
    Persona,
    PersonaMinorCheck,
    PersonaShare,
)
from app.main import app
from app.services import ages, intake, minors, notify, sms

# 지어낸 아이(민지)와 엄마(지은)
CHILD = {
    "name": "김민지",
    "relation": "딸",
    "addressTerm": "민지",
    "status": "alive",
    "myName": "지은",
    "myRelation": "어머니",
    "calledMe": "엄마",
}
TODAY = date(2026, 10, 23)
TEEN = {"minor": True, "birthYm": "2010-01", "guardianBasis": "both_agreed", "guardianAttest": True}
KID = TEEN | {"birthYm": "2016-05"}


def _ym(day: date, years: int, months_ahead: int = 0) -> str:
    """day 에서 months_ahead 달 뒤가 '그 나이가 되는 달'인 태어난 해와 달."""
    month = day.month - 1 + months_ahead
    return f"{day.year + month // 12 - years:04d}-{month % 12 + 1:02d}"


@pytest.fixture
def provider(settings, monkeypatch):  # noqa: F811
    """확인 문자 공급자를 바꾼다(기본은 off — 대표 답 2026-10-23)."""

    def use(name: str, environment: str = "local") -> None:
        monkeypatch.setattr(settings, "sms_provider", name)
        monkeypatch.setattr(settings, "environment", environment)

    use("off")
    return use


@pytest.fixture
def alerts(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda text, channel="events": sent.append(text))
    return sent


@pytest.fixture
def cleanup():
    """이 시험이 만든 대화 상대를 끝에 지운다(나이 바뀜 sweep 이 다른 시험 줄을 건드리지 않게)."""
    made: list[str] = []
    yield made

    async def go(db):
        for pid in made:
            persona = await db.get(Persona, pid)
            if persona is not None:
                await db.delete(await db.get(Person, persona.person_id))
        await db.commit()

    _db(go)


def _error(fn, *args, **kwargs) -> str:
    with pytest.raises(intake.IntakeError) as error:
        fn(*args, **kwargs)
    return str(error.value)


# ────────────────────────── 1. validate_basics 표(SPEC §6.2) ──────────────────────────


def test_new_living_persona_must_say_whether_minor(provider):
    assert _error(intake.validate_basics, CHILD) == "그분이 만 19세 미만인지 골라 주세요."
    assert _error(intake.validate_basics, CHILD | {"minor": "yes"}) == intake.MINOR_REQUIRED
    adult = intake.validate_basics(
        CHILD | {"minor": False, "birthYm": "2010-01", "guardianBasis": "sole_custody"}
    )
    assert adult["minor"] is False and "birthYm" not in adult and "guardianBasis" not in adult
    # 돌아가신 분·나 자신은 묻지 않는다(보내도 두지 않는다)
    deceased = intake.validate_basics(CHILD | TEEN | {"status": "deceased"})
    assert not set(intake.MINOR_KEYS) & set(deceased)
    me = intake.validate_basics({"name": "한바다", "relation": "나 자신"} | TEEN)
    assert not set(intake.MINOR_KEYS) & set(me)


def test_minor_fields_and_messages(provider):
    teen = intake.validate_basics(CHILD | TEEN, today=TODAY)
    assert {k: teen[k] for k in intake.MINOR_KEYS if k in teen} == {
        "minor": True,
        "birthYm": "2010-01",
        "guardianBasis": "both_agreed",
    }
    assert "guardianAttest" not in teen  # 요청에만 있다
    cases = [
        ({"birthYm": None}, "아이의 태어난 해와 달을 골라 주세요."),
        ({"birthYm": "2010-13"}, intake.MINOR_BIRTH),
        ({"birthYm": "2010/01"}, intake.MINOR_BIRTH),
        ({"birthYm": "2026-11"}, intake.MINOR_BIRTH),  # 앞날
        ({"birthYm": "2007-09"}, "만 19세 미만이 아니면 '아니요'를 골라 주세요."),
        (
            {"relation": "손녀"},
            "아이의 대화 상대는 부모님만 만들 수 있어요. 그분이 나의 아들인지 딸인지 골라 주세요.",
        ),
        (
            {"myRelation": "할머니"},
            "아이의 대화 상대는 부모님만 만들 수 있어요. "
            "'나는 그분의' 칸에서 어머니나 아버지를 골라 주세요.",
        ),
        (
            {"guardianBasis": None},
            "다른 쪽 부모님의 동의를 받았는지, 혼자 친권을 가지고 계신지 골라 주세요.",
        ),
        ({"guardianBasis": "both"}, intake.GUARDIAN_BASIS_REQUIRED),
        ({"guardianAttest": False}, "아이의 법정대리인(부모)인지 확인해 주세요."),
    ]
    for change, message in cases:
        assert _error(intake.validate_basics, CHILD | TEEN | change, today=TODAY) == message, change
    # 만 19세가 되기 전 마지막 달(태어난 다음 달 1일생으로 본다)과 이번 달에 태어난 아기
    assert intake.validate_basics(CHILD | TEEN | {"birthYm": "2007-10"}, today=TODAY)["minor"]
    born = CHILD | KID | {"birthYm": "2026-10"}
    assert _error(intake.validate_basics, born, today=TODAY) == intake.UNDER14_CLOSED


def test_under14_is_blocked_while_sms_is_off(provider):
    message = (
        "만 14세 미만 아이의 대화 상대는 아직 만들 수 없어요"
        "(법정대리인 확인 문자를 준비하고 있어요)."
    )
    assert _error(intake.validate_basics, CHILD | KID, today=TODAY) == message
    # 만 14~18세는 그대로 연다
    assert intake.validate_basics(CHILD | TEEN, today=TODAY)["minor"] is True
    for name in ("manual", "log"):
        provider(name)
        assert intake.validate_basics(CHILD | KID, today=TODAY)["birthYm"] == "2016-05"


def test_create_minor_through_the_api(provider, cleanup):
    """POST /personas: 만 19세 미만인지 꼭 묻고, 만 14세 미만은 확인 문자가 꺼져 있으면 400,
    만 14~18세는 만들어 법정대리인 확인 기록과 상태 줄(나이 바뀜을 지켜보기 시작)을 남긴다."""
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        asked = client.post(f"{API}/personas", headers=h, json=CHILD)
        assert asked.status_code == 400 and asked.json()["detail"] == intake.MINOR_REQUIRED
        kid = client.post(f"{API}/personas", headers=h, json=CHILD | KID)
        assert kid.status_code == 400 and kid.json()["detail"] == intake.UNDER14_CLOSED
        teen = client.post(f"{API}/personas", headers=h, json=CHILD | TEEN)
        assert teen.status_code == 201, teen.text
        pid = teen.json()["data"]["id"]
        cleanup.append(pid)
        basics = _get(Persona, pid).basics
        assert (basics["minor"], basics["birthYm"], basics["guardianBasis"]) == (
            True,
            "2010-01",
            "both_agreed",
        )
        assert "guardianAttest" not in basics
        records = _all(
            select(ConsentRecord).where(ConsentRecord.consent_type == f"guardian_attest:{pid}")
        )
        assert [(r.agreed, r.doc) for r in records] == [(True, "guardian_consent")]
        assert _check(pid) is not None

        # 부모 양쪽 근거는 고칠 수 있고, 고치면 기록이 남는다(같은 값으로 저장하면 남기지 않는다)
        for basis in ("sole_custody", "sole_custody"):
            saved = client.put(
                f"{API}/personas/{pid}",
                headers=h,
                json={"basics": CHILD | TEEN | {"guardianBasis": basis}},
            )
            assert saved.status_code == 200, saved.text
        assert _get(Persona, pid).basics["guardianBasis"] == "sole_custody"
        records = _all(
            select(ConsentRecord).where(ConsentRecord.consent_type == f"guardian_attest:{pid}")
        )
        assert sorted(r.channel for r in records) == ["basis_sole_custody", "web"]


def test_editing_keeps_and_locks_the_minor_fields(provider):
    previous = intake.validate_basics(CHILD | TEEN, today=TODAY)
    # 화면이 칸을 보내지 않아도 그대로 잇는다(아이 안전 규칙·가족 공유 제한이 풀리지 않게)
    kept = intake.validate_basics(CHILD | {"calledMe": "엄마야"}, previous=previous, today=TODAY)
    assert (kept["minor"], kept["birthYm"], kept["guardianBasis"]) == (
        True,
        "2010-01",
        "both_agreed",
    )
    same = intake.validate_basics(CHILD | TEEN, previous=previous, today=TODAY)
    assert same["birthYm"] == "2010-01"
    fixed = (
        "아이의 태어난 해·달과 만 19세 미만 여부는 고칠 수 없어요. "
        "잘못 적었으면 고객센터로 알려 주세요."
    )
    assert _error(intake.validate_basics, CHILD | {"minor": False}, previous=previous) == fixed
    changed = CHILD | {"birthYm": "2011-01"}
    assert _error(intake.validate_basics, changed, previous=previous) == fixed
    # 부모 양쪽 근거는 바꿀 수 있다
    sole = intake.validate_basics(CHILD | {"guardianBasis": "sole_custody"}, previous=previous)
    assert sole["guardianBasis"] == "sole_custody"
    # 관계 잠금은 고칠 때도 같다
    assert _error(intake.validate_basics, CHILD | {"relation": "손녀"}, previous=previous) == (
        intake.MINOR_RELATION
    )
    # '아니요'라고 답한 대화 상대도 고칠 수 없다
    adult = intake.validate_basics(CHILD | {"minor": False})
    assert intake.validate_basics(CHILD, previous=adult)["minor"] is False
    assert _error(intake.validate_basics, CHILD | TEEN, previous=adult) == fixed
    # 예전 대화 상대(미성년 칸 없음)는 묻지 않는다(PUT /minor 로 답한다)
    legacy = intake.validate_basics(CHILD | TEEN, previous=dict(CHILD))
    assert not set(intake.MINOR_KEYS) & set(legacy)


def test_not_guardian_and_deceased_child_keep_their_fields(provider):
    previous = {
        **CHILD,
        "relation": "손녀",
        "myRelation": "할머니",
        "minor": True,
        "birthYm": "2016-05",
        "notGuardian": True,
    }
    kept = intake.validate_basics(previous | {"calledMe": "할미"}, previous=previous)
    assert kept["notGuardian"] is True and kept["birthYm"] == "2016-05"
    assert "guardianBasis" not in kept
    # 떠나신 자녀(§6.9): 상태는 바꿀 수 있고 미성년 표시는 남지만 고인 규칙을 따른다
    teen = intake.validate_basics(CHILD | TEEN, today=TODAY)
    gone = intake.validate_basics(CHILD | {"status": "deceased"}, previous=teen, today=TODAY)
    assert gone["status"] == "deceased" and gone["minor"] is True
    assert ages.consent_mode(gone, TODAY) is None and ages.is_minor_now(gone, TODAY) is False
    built = intake.build_intake(gone, {})["intakeContext"]["basicProfile"]
    assert built["minor"] is False and built["status"] == "사망"


def test_other_persona_cannot_become_myself():
    previous = intake.validate_basics(OWNER_BASICS)
    with pytest.raises(intake.IntakeError) as error:
        intake.validate_basics({"name": "지은", "relation": "나 자신"}, previous=previous)
    assert (
        str(error.value)
        == "다른 분의 대화 상대를 '나 자신'으로 바꿀 수 없어요. 새로 만들어 주세요."
    )
    # 나 자신 → 다른 분은 지금처럼 된다(더 엄격해지는 쪽)
    me = intake.validate_basics({"name": "지은", "relation": "나 자신"})
    assert intake.validate_basics(OWNER_BASICS, previous=me)["status"] == "deceased"


def test_build_intake_sends_only_the_minor_flag(provider):
    teen = intake.validate_basics(CHILD | TEEN)
    profile = intake.build_intake(teen, {})["intakeContext"]["basicProfile"]
    assert profile["minor"] is True
    # 태어난 해와 달은 보내지 않는다
    assert "2010" not in str(intake.build_intake(teen, {}))
    adult = intake.validate_basics(CHILD | {"minor": False})
    assert intake.build_intake(adult, {})["intakeContext"]["basicProfile"]["minor"] is False
    assert intake.build_intake(OWNER_BASICS, {})["intakeContext"]["basicProfile"]["minor"] is False


# ────────────────────────── 2. 확인 문자 공급자 ──────────────────────────


def test_sms_providers(provider, caplog):
    for name in ("off", "", "OFF"):
        provider(name)
        assert sms.get_provider() is None and sms.under14_open() is False
    provider("manual")
    assert sms.get_provider().name == "manual" and sms.under14_open() is True
    provider("log")
    assert sms.get_provider().name == "log"
    # 개발용은 운영에서 쓰지 않는다
    provider("log", "production")
    assert sms.get_provider() is None
    provider("solapi")
    assert sms.get_provider() is None
    text = sms.message("123456", sms.valid_for(False))
    assert "123456" in text and "(10분 안)" in text
    assert sms.valid_for(True) == "24시간"
    for vendor in ("Fish", "ElevenLabs", "OpenAI", "Eleven"):
        assert vendor not in text
    assert sms.phone_digits("010-1234-5678") == "01012345678"
    assert sms.phone_digits("02-123-4567") is None and sms.phone_digits(None) is None
    assert sms.mask_phone("01012345678") == "010-****-5678"


# ────────────────────────── 3. 기존 대화 상대의 미성년 여부(PUT /minor) ──────────────────────────


def test_put_minor_once_with_parent(provider, cleanup):
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid, basics=CHILD)
        cleanup.append(pid)
        # 다른 분은 못 한다(가족·모르는 사람 404)
        other, _, _ = _signup(client)
        assert client.put(f"{API}/personas/{pid}/minor", headers=other, json={}).status_code == 404
        empty = client.put(f"{API}/personas/{pid}/minor", headers=h, json={})
        assert empty.status_code == 400 and empty.json()["detail"] == intake.MINOR_REQUIRED
        missing = client.put(
            f"{API}/personas/{pid}/minor", headers=h, json={"minor": True, "birthYm": "2010-01"}
        )
        assert missing.status_code == 400
        assert missing.json()["detail"] == intake.GUARDIAN_BASIS_REQUIRED
        done = client.put(f"{API}/personas/{pid}/minor", headers=h, json=TEEN)
        assert done.status_code == 200, done.text
        data = done.json()["data"]
        assert data["minor"] is True and data["notGuardian"] is False
        assert data["pausedMembers"] == 0
        assert data["guardian"]["mode"] == "minor_teen" and data["guardian"]["guardianOk"] is True
        persona = _get(Persona, pid)
        assert persona.basics["birthYm"] == "2010-01" and persona.basics["guardianBasis"]
        assert "guardianAttest" not in persona.basics
        records = _all(
            select(ConsentRecord).where(ConsentRecord.consent_type == f"guardian_attest:{pid}")
        )
        assert [(r.agreed, r.doc) for r in records] == [(True, "guardian_consent")]
        # 한 번만
        again = client.put(f"{API}/personas/{pid}/minor", headers=h, json={"minor": False})
        assert again.status_code == 409 and again.json()["code"] == "minor_answered"
        # 돌아가신 분은 묻지 않는다
        gone = _persona(uid)
        cleanup.append(gone)
        refused = client.put(f"{API}/personas/{gone}/minor", headers=h, json={"minor": False})
        assert refused.status_code == 409 and refused.json()["code"] == "not_required"


def test_put_minor_no_and_not_guardian(provider, cleanup):
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        adult = _persona(uid, basics=CHILD | {"relation": "어머니", "myRelation": "딸"})
        grandchild = _persona(uid, basics=CHILD | {"relation": "손녀", "myRelation": "할머니"})
        cleanup.extend([adult, grandchild])
        no = client.put(f"{API}/personas/{adult}/minor", headers=h, json={"minor": False})
        assert no.status_code == 200 and _get(Persona, adult).basics["minor"] is False
        # 부모가 아닌 분이 예전에 만든 아이: 400 을 내지 않고 사실대로 적어 둔다
        bad = client.put(
            f"{API}/personas/{grandchild}/minor", headers=h, json={"minor": True, "birthYm": "x"}
        )
        assert bad.status_code == 400 and bad.json()["detail"] == intake.MINOR_BIRTH
        told = client.put(
            f"{API}/personas/{grandchild}/minor",
            headers=h,
            json={"minor": True, "birthYm": "2016-05"},
        )
        assert told.status_code == 200, told.text
        assert told.json()["data"]["notGuardian"] is True
        basics = _get(Persona, grandchild).basics
        assert (basics["minor"], basics["birthYm"], basics["notGuardian"]) == (
            True,
            "2016-05",
            True,
        )
        guardian = client.get(f"{API}/personas/{grandchild}/guardian", headers=h).json()["data"]
        assert guardian["notGuardian"] is True and guardian["missing"] == ["attest"]
        assert guardian["notGuardianText"] == minors.NOT_GUARDIAN_TEXT
        refused = client.post(
            f"{API}/personas/{grandchild}/guardian/consent",
            headers=h,
            json={"attest": True, "consent": True, "overseas": True},
        )
        assert refused.status_code == 409 and refused.json()["code"] == "not_guardian"
        assert not _all(
            select(ConsentRecord).where(
                ConsentRecord.consent_type == f"guardian_attest:{grandchild}"
            )
        )


def test_put_minor_asks_my_relation_before_marking_not_guardian(provider, cleanup):
    """그분이 아들·딸인데 '나는 그분의'가 비었거나 직접 적은 말이면 부모일 수 있다:
    notGuardian(풀 수 없음)으로 잠그지 않고 먼저 어머니·아버지를 고르게 한다. 고르면 부모로
    저장하고, 그 밖을 고르면 부모가 아니라고 답한 것으로 본다."""
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        empty = _persona(uid, basics=CHILD | {"myRelation": None})
        typed = _persona(uid, basics=CHILD | {"relation": "아들", "myRelation": "엄마"})
        step = _persona(uid, basics=CHILD | {"relation": "아들", "myRelation": "엄마"})
        cleanup.extend([empty, typed, step])
        for pid in (empty, typed):
            asked = client.put(f"{API}/personas/{pid}/minor", headers=h, json=TEEN)
            assert asked.status_code == 400, asked.text
            assert asked.json() == {"detail": intake.MINOR_MY_RELATION, "code": "minor_my_relation"}
            assert "minor" not in _get(Persona, pid).basics
        bad = client.put(
            f"{API}/personas/{empty}/minor",
            headers=h,
            json=TEEN | {"guardianAttest": False, "myRelation": "어머니"},
        )
        assert bad.status_code == 400 and bad.json()["detail"] == intake.GUARDIAN_ATTEST_REQUIRED
        assert _get(Persona, empty).basics["myRelation"] is None
        done = client.put(
            f"{API}/personas/{empty}/minor", headers=h, json=TEEN | {"myRelation": "어머니"}
        )
        assert done.status_code == 200, done.text
        assert done.json()["data"]["notGuardian"] is False
        basics = _get(Persona, empty).basics
        assert basics["myRelation"] == "어머니" and "notGuardian" not in basics
        assert basics["guardianBasis"] == "both_agreed"
        records = _all(
            select(ConsentRecord).where(ConsentRecord.consent_type == f"guardian_attest:{empty}")
        )
        assert [r.doc for r in records] == ["guardian_consent"]
        # 다음 정보 고치기(화면이 저장된 '어머니'를 보낸다)도 부모 검사를 통과한다
        saved = client.put(
            f"{API}/personas/{empty}", headers=h, json={"basics": CHILD | {"myRelation": "어머니"}}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["updating"] is False
        # 부모가 아니라고 답함(새아버지): 사실대로 notGuardian, 적어 둔 '나는 그분의'는 그대로
        told = client.put(
            f"{API}/personas/{step}/minor",
            headers=h,
            json={"minor": True, "birthYm": "2012-03", "myRelation": "새아버지"},
        )
        assert told.status_code == 200, told.text
        assert told.json()["data"]["notGuardian"] is True
        basics = _get(Persona, step).basics
        assert basics["notGuardian"] is True and basics["myRelation"] == "엄마"


def test_minor_scope_pauses_members_outside_the_family(provider, cleanup):
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(uid, basics=CHILD)
        cleanup.append(pid)
        token, _ = _link(client, h, pid)
        member, _, _ = _signup(client, "박삼촌")
        joined = _accept(client, member, token, PROFILE | {"myRelation": "삼촌"})
        assert joined.status_code == 201, joined.text
        share_id = joined.json()["data"]["shareId"]
        room = _room(client, member, pid)
        assert _say(client, member, room).status_code == 200
        done = client.put(f"{API}/personas/{pid}/minor", headers=h, json=TEEN)
        assert done.json()["data"]["pausedMembers"] == 1
        notices = _all(
            select(OwnerNotice).where(OwnerNotice.user_id == uid, OwnerNotice.kind == "minor_scope")
        )
        assert len(notices) == 1 and notices[0].title == "함께 쓰는 가족을 확인해 주세요"
        assert notices[0].body.startswith("'민지' 대화 상대는 아이의 대화 상대라")
        assert notices[0].action == {"label": "가족 공유 보기", "route": f"/p/{pid}/family"}
        paused = _say(client, member, room)
        assert paused.status_code == 403
        assert paused.json() == {
            "detail": "아이의 대화 상대는 법정대리인과 직계가족(부모·조부모)만 함께 쓸 수 있어요. "
            "관계가 맞으면 내 정보에서 관계를 고쳐 주세요.",
            "code": "share_paused",
            "reason": "minor_scope",
        }
        # 자리·나눈 대화는 그대로
        assert _get(PersonaShare, share_id) is not None
        assert client.get(f"{API}/conversations/{room}", headers=member).status_code == 200
        # 가족의 '나에 대한 정보 고치기'도 직계가족 목록만 보인다
        sheet = client.get(f"{API}/personas/{pid}", headers=member).json()["data"]
        assert sheet["minorScope"] is True
        assert sheet["relations"] == list(intake.MINOR_SHARE_RELATIONS)
        # 목록 밖으로 고치면 400, 목록 안으로 고치면 풀린다
        wrong = client.patch(
            f"{API}/personas/{pid}/shares/{share_id}", headers=member, json={"myName": "삼촌"}
        )
        assert wrong.status_code == 400 and wrong.json()["code"] == "share_minor_scope"
        fixed = client.patch(
            f"{API}/personas/{pid}/shares/{share_id}",
            headers=member,
            json={"myRelation": "외할아버지"},
        )
        assert fixed.status_code == 200, fixed.text
        assert _say(client, member, room).status_code == 200


# ────────────────────────── 4. 법정대리인 동의·확인 문자 ──────────────────────────


def _child_persona(client, h, uid, cleanup) -> str:
    """법정대리인 확인(attest)까지 한 만 14세 미만 아이(기존 대화 상대가 PUT /minor 로 답함)."""
    pid = _persona(uid, basics=CHILD)
    cleanup.append(pid)
    done = client.put(f"{API}/personas/{pid}/minor", headers=h, json=KID)
    assert done.status_code == 200, done.text
    return pid


def _check(pid: str) -> PersonaMinorCheck:
    async def go(db):
        return await db.scalar(select(PersonaMinorCheck).where(PersonaMinorCheck.persona_id == pid))

    return _db(go)


def test_guardian_consent_and_log_sms(provider, cleanup, monkeypatch):
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _child_persona(client, h, uid, cleanup)
        view = client.get(f"{API}/personas/{pid}/guardian", headers=h).json()["data"]
        assert view["needed"] is True and view["mode"] == "minor_child"
        assert view["missing"] == ["consent", "overseas", "sms"]
        assert view["under14Open"] is False and set(view["labels"]) == {
            "attest",
            "consent",
            "overseas",
        }
        url = f"{API}/personas/{pid}/guardian"
        early = client.post(f"{url}/sms", headers=h, json={"phone": "01012345678"})
        assert early.status_code == 409 and early.json() == {
            "detail": "먼저 법정대리인 동의에 체크해 주세요.",
            "code": "guardian_consent",
        }
        half = client.post(f"{url}/consent", headers=h, json={"consent": True})
        assert (
            half.status_code == 400
            and half.json()["detail"] == "법정대리인 동의 두 가지에 모두 체크해 주세요."
        )
        both = client.post(f"{url}/consent", headers=h, json={"consent": True, "overseas": True})
        assert both.status_code == 200 and both.json()["data"]["missing"] == ["sms"]
        off = client.post(f"{url}/sms", headers=h, json={"phone": "01012345678"})
        assert off.status_code == 409 and off.json() == {
            "detail": "지금은 확인 문자를 보낼 수 없어요.",
            "code": "sms_unavailable",
        }
        provider("log")
        bad = client.post(f"{url}/sms", headers=h, json={"phone": "02-123-4567"})
        assert bad.status_code == 400 and bad.json()["detail"] == "휴대전화 번호를 확인해 주세요."
        monkeypatch.setattr(minors.secrets, "randbelow", lambda n: 123456)
        sent = client.post(f"{url}/sms", headers=h, json={"phone": "010-1234-5678"})
        assert sent.status_code == 200, sent.text
        assert sent.json()["data"]["queued"] is False
        assert sent.json()["data"]["phoneMasked"] == "010-****-5678"
        check = _check(pid)
        # 전체 번호·확인 번호는 남기지 않는다(가린 번호와 해시만)
        assert check.sms_phone_masked == "010-****-5678" and check.sms_phone_pending is None
        assert check.sms_code_pending is None and check.sms_code_hash != "123456"
        assert "01012345678" not in str({c.name: getattr(check, c.name) for c in check.__table__.c})
        # 60초 안에 다시 받지 못한다
        wait = client.post(f"{url}/sms", headers=h, json={"phone": "01012345678"})
        assert wait.status_code == 409 and wait.json()["code"] == "sms_wait"
        # 틀린 번호 5번이면 새로 받아야 한다
        for n in range(1, 6):
            wrong = client.post(f"{url}/sms/verify", headers=h, json={"code": "000000"})
            expected = "확인 번호가 맞지 않아요." if n < 5 else "확인 번호를 새로 받아 주세요."
            assert wrong.status_code == 400 and wrong.json()["detail"] == expected, n
        right = client.post(f"{url}/sms/verify", headers=h, json={"code": "123456"})
        assert right.json()["detail"] == "확인 번호를 새로 받아 주세요."
        # 하루 5번까지
        _set(
            PersonaMinorCheck,
            check.id,
            sms_requested_at=datetime.now(UTC) - timedelta(minutes=2),
            sms_sent_count=5,
        )
        daily = client.post(f"{url}/sms", headers=h, json={"phone": "01012345678"})
        assert daily.status_code == 409 and daily.json()["code"] == "sms_daily"
        _set(PersonaMinorCheck, check.id, sms_sent_count=1)
        monkeypatch.setattr(minors.secrets, "randbelow", lambda n: 654321)
        assert (
            client.post(f"{url}/sms", headers=h, json={"phone": "01012345678"}).status_code == 200
        )
        ok = client.post(f"{url}/sms/verify", headers=h, json={"code": "654321"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["data"]["guardianOk"] is True and ok.json()["data"]["missing"] == []
        assert _check(pid).sms_verified_at is not None and _check(pid).sms_code_hash is None
        logged = _all(
            select(ConsentRecord).where(ConsentRecord.consent_type == f"guardian_sms:{pid}")
        )
        assert [(r.doc, r.channel) for r in logged] == [("guardian_consent", "sms")]
        # 한 번 쓴 번호는 다시 쓰지 못한다
        reuse = client.post(f"{url}/sms/verify", headers=h, json={"code": "654321"})
        assert reuse.status_code == 409


def test_manual_sms_queue(provider, cleanup, monkeypatch, alerts):
    provider("manual")
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _child_persona(client, h, uid, cleanup)
        url = f"{API}/personas/{pid}/guardian"
        client.post(f"{url}/consent", headers=h, json={"consent": True, "overseas": True})
        monkeypatch.setattr(minors.secrets, "randbelow", lambda n: 246810)
        queued = client.post(f"{url}/sms", headers=h, json={"phone": "010-9876-5432"})
        assert queued.status_code == 200 and queued.json()["data"]["queued"] is True
        assert any("GUARDIAN_SMS_PENDING" in text for text in alerts)
        assert not any("01098765432" in text or "246810" in text for text in alerts)
        view = client.get(url, headers=h).json()["data"]
        assert view["smsQueued"] is True and view["phoneMasked"] == "010-****-5432"
        assert client.get(f"{API}/admin/guardian-sms", headers=h).status_code == 403
        admin, _ = _admin(client)
        rows = client.get(f"{API}/admin/guardian-sms", headers=admin).json()["data"]
        row = next(r for r in rows if r["personaId"] == pid)
        assert row["phone"] == "01098765432" and row["code"] == "246810"
        assert row["title"] == "민지" and row["expired"] is False
        assert "확인 번호 246810를 넣어 주세요(24시간 안)" in row["text"]
        sent = client.post(f"{API}/admin/guardian-sms/{pid}/sent", headers=admin)
        assert sent.status_code == 200
        check = _check(pid)
        assert check.sms_phone_pending is None and check.sms_code_pending is None
        assert check.sms_phone_masked == "010-****-5432" and check.sms_code_hash
        rows = client.get(f"{API}/admin/guardian-sms", headers=admin).json()["data"]
        assert pid not in {r["personaId"] for r in rows}
        assert client.post(f"{API}/admin/guardian-sms/{pid}/sent", headers=admin).status_code == 409
        told = _all(
            select(OwnerNotice).where(
                OwnerNotice.user_id == uid, OwnerNotice.kind == "guardian_sms_sent"
            )
        )
        assert (
            told[0].body
            == "'민지' 대화 상대의 법정대리인 확인 문자를 보냈어요. 받으신 확인 번호를 넣어 주세요."
        )
        assert told[0].action["route"] == f"/p/{pid}/guardian"
        # 대표가 보낸 문자의 번호는 24시간 안에 맞는다
        ok = client.post(f"{url}/sms/verify", headers=h, json={"code": "246810"})
        assert ok.status_code == 200 and ok.json()["data"]["guardianOk"] is True


def test_minor_state(provider, cleanup):
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        teen = _persona(uid, basics=CHILD | {"minor": True, "birthYm": "2010-01"})
        cleanup.append(teen)

        async def state(db, pid, today=None):
            return await minors.state(db, await db.get(Persona, pid), today=today)

        found = _db(lambda db: state(db, teen, TODAY))
        assert (found.age, found.mode, found.guardian_ok, found.missing) == (
            16,
            "minor_teen",
            False,
            ["attest"],
        )
        # 만 19세가 지나면 법정대리인은 더 보지 않는다(본인 동의 녹음을 다시 받는다)
        grown = _db(lambda db: state(db, teen, date(2029, 3, 1)))
        assert grown.mode == "adult" and grown.guardian_ok is True
        adult = _persona(uid, basics=CHILD | {"minor": False})
        cleanup.append(adult)
        assert _db(lambda db: state(db, adult)) is None


# ────────────────────────── 5. 나이가 바뀔 때(minors.sweep) ──────────────────────────


def _at(day: date, hour: int = 9) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=ages.KST).astimezone(UTC)


def _notices(uid: str, kind: str) -> list[OwnerNotice]:
    return _all(
        select(OwnerNotice)
        .where(OwnerNotice.user_id == uid, OwnerNotice.kind == kind)
        .order_by(OwnerNotice.created_at)
    )


def _sweep(now: datetime) -> int:
    return _db(lambda db: minors.sweep(db, now))


def test_sweep_turning_14_reminders_and_hold(provider, cleanup, alerts):
    today = ages.today_kst()
    turn = date(today.year + (today.month + 1) // 12, (today.month + 1) % 12 + 1, 1)
    ym = _ym(today, 14, 2)
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(
            uid, basics=CHILD | {"minor": True, "birthYm": ym, "guardianBasis": "both_agreed"}
        )
        cleanup.append(pid)
        # 지켜보기 시작(지금은 만 13세) — 아직 알릴 것이 없다
        _sweep(datetime.now(UTC))
        assert not _notices(uid, "minor_turns14")
        assert ages.turning_month(ym, 14) == turn
        at = _at(turn + timedelta(days=3))
        assert _sweep(at) >= 1
        check = _check(pid)
        start = ages.reconsent_from(ym, 14)
        assert check.reconsent_kind == "14" and check.turned14_notified_at is not None
        assert minors._aware(check.reconsent_from) == _at(start, 0)
        due = minors._aware(check.reconsent_due_at)
        assert due == _at(minors._add_months(start, 6), 0)
        [notice] = _notices(uid, "minor_turns14")
        last = minors.last_day(due)
        assert notice.title == "아이 본인의 동의 녹음이 필요해요"
        assert notice.body == (
            "아이가 만 14세가 되는 달이에요. '민지' 대화 상대를 계속 쓰려면 아이 본인의 동의 "
            f"녹음이 필요해요. {last.month}월 {last.day}일까지 받지 못하면 대화가 멈추고 지워져요."
        )
        assert notice.action == {"label": "동의 녹음 하기", "route": f"/p/{pid}/consent"}
        # 같은 달에 다시 돌아도 한 번만
        _sweep(at + timedelta(days=1))
        assert len(_notices(uid, "minor_turns14")) == 1
        # 기한 30일 전·7일 전 한 번씩
        _sweep(due - timedelta(days=30))
        [d30] = _notices(uid, "minor_due_d30")
        assert d30.title == "동의 녹음 기한이 30일 남았어요"
        _sweep(due - timedelta(days=29))
        assert len(_notices(uid, "minor_due_d30")) == 1
        _sweep(due - timedelta(days=7))
        [d7] = _notices(uid, "minor_due_d7")
        assert d7.title == "동의 녹음 기한이 7일 남았어요"
        assert d7.body == (
            f"'민지' 대화 상대의 동의 녹음을 {last.month}월 {last.day}일까지 받아 주세요. "
            "받지 못하면 대화가 멈추고 지워져요."
        )
        assert _get(Persona, pid).hold_reason is None
        # 기한이 지나면 멈추기만 한다(지우기는 holds.sweep 이 7일 뒤)
        _sweep(due + timedelta(hours=1))
        persona = _get(Persona, pid)
        assert persona.hold_reason == "minor_consent_missing" and persona.delete_after is not None
        [stopped] = _notices(uid, "stopped")
        assert stopped.body.startswith("아이 본인의 동의를 기한까지 받지 못해 대화를 멈췄어요.")
        assert any("PERSONA_HOLD" in text for text in alerts)


def test_sweep_passed_reconsent_is_done(provider, cleanup):
    today = ages.today_kst()
    turn = date(today.year + (today.month + 1) // 12, (today.month + 1) % 12 + 1, 1)
    ym = _ym(today, 14, 2)
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid = _persona(
            uid, basics=CHILD | {"minor": True, "birthYm": ym, "guardianBasis": "both_agreed"}
        )
        cleanup.append(pid)
        _sweep(datetime.now(UTC))
        _sweep(_at(turn))
        check = _check(pid)
        start = minors._aware(check.reconsent_from)
        # 새 나이(만 14세)의 동의 녹음이 재동의 시작 뒤에 통과했다
        pass_subject_consent(None, pid, "minor_teen", at=start + timedelta(days=3))
        _sweep(start + timedelta(days=4))
        assert _check(pid).reconsent_done_at is not None
        _sweep(minors._aware(check.reconsent_due_at) + timedelta(days=1))
        assert _get(Persona, pid).hold_reason is None


def test_sweep_never_stops_without_reminders_or_in_bulk(provider, cleanup, alerts):
    now = datetime.now(UTC)
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        late = _persona(
            uid,
            basics=CHILD | {"minor": True, "birthYm": "2010-01", "guardianBasis": "sole_custody"},
        )
        cleanup.append(late)
        _sweep(now)
        check = _check(late)
        # 서버가 멈춰 있어 미리 알림 없이 기한이 지났다
        _set(
            PersonaMinorCheck,
            check.id,
            reconsent_kind="19",
            reconsent_from=now - timedelta(days=200),
            reconsent_due_at=now - timedelta(days=1),
            reminders={},
        )
        _sweep(now)
        assert _get(Persona, late).hold_reason is None
        moved = _check(late)
        # 알려 드리지 않은 채 멈추지 않는다: 기한을 7일 뒤로 미루고 7일 전 알림을 남긴다
        assert minors._aware(moved.reconsent_due_at) > now + timedelta(days=6)
        assert set(moved.reminders) == {"d30", "d7"}
        assert len(_notices(uid, "minor_due_d7")) == 1
        _sweep(now + timedelta(days=7, hours=1))
        assert _get(Persona, late).hold_reason == "minor_consent_missing"

        # 한 번에 멈출 대화 상대가 셋을 넘으면 멈추지 않고 운영 알림만
        many = []
        for _ in range(4):
            pid = _persona(
                uid,
                basics=CHILD
                | {"minor": True, "birthYm": "2010-01", "guardianBasis": "sole_custody"},
            )
            cleanup.append(pid)
            many.append(pid)
        _sweep(now)
        for pid in many:
            _set(
                PersonaMinorCheck,
                _check(pid).id,
                reconsent_kind="19",
                reconsent_from=now - timedelta(days=200),
                reconsent_due_at=now - timedelta(hours=1),
                reminders={
                    "d30": (now - timedelta(days=31)).isoformat(),
                    "d7": (now - timedelta(days=8)).isoformat(),
                },
            )
        alerts.clear()
        _sweep(now)
        assert all(_get(Persona, pid).hold_reason is None for pid in many)
        assert any("MINOR_HOLD_GUARD" in text for text in alerts)


def _turning_persona(uid: str, cleanup, **extra) -> tuple[str, str, date]:
    """다음다음 달에 만 14세가 되는 아이(지금은 만 13세). (id, 태어난 해와 달, 14세가 되는
    달 1일)."""
    today = ages.today_kst()
    turn = date(today.year + (today.month + 1) // 12, (today.month + 1) % 12 + 1, 1)
    ym = _ym(today, 14, 2)
    pid = _persona(
        uid,
        basics=CHILD | {"minor": True, "birthYm": ym, "guardianBasis": "both_agreed"} | extra,
    )
    cleanup.append(pid)
    return pid, ym, turn


def test_sweep_leaves_not_guardian_children_to_the_admin(provider, cleanup):
    """부모가 아닌 분이 예전에 만든 아이(notGuardian)는 아이 본인 동의 녹음을 받을 길이 없다: 14세가
    되어도 알리지 않고, 기한이 지난 것처럼 보여도 멈추지(→ 지우지) 않는다. 관리자 목록에 남는다."""
    now = datetime.now(UTC)
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid, _, turn = _turning_persona(
            uid, cleanup, relation="손녀", myRelation="할머니", notGuardian=True
        )
        _sweep(now)
        _sweep(_at(turn + timedelta(days=3)))
        assert not _notices(uid, "minor_turns14")
        assert _check(pid) is None

        # 예전 판의 sweep 이 남긴 재동의 기한이 지났어도 멈추지 않는다
        async def row(db):
            await minors.check_row(db, pid, create=True)
            await db.commit()

        _db(row)
        _set(
            PersonaMinorCheck,
            _check(pid).id,
            reconsent_kind="14",
            reconsent_from=now - timedelta(days=200),
            reconsent_due_at=now - timedelta(hours=1),
            reminders={
                "d30": (now - timedelta(days=31)).isoformat(),
                "d7": (now - timedelta(days=8)).isoformat(),
            },
        )
        _sweep(now)
        assert _get(Persona, pid).hold_reason is None
        assert not _notices(uid, "minor_due_d7") and not _notices(uid, "stopped")
        admin, _ = _admin(client)
        rows = client.get(f"{API}/admin/personas/unable", headers=admin).json()["data"]
        assert next(r for r in rows if r["personaId"] == pid)["kind"] == "minor_not_guardian"


def test_sweep_one_failure_does_not_lose_the_others(provider, cleanup, monkeypatch):
    """한 대화 상대에서 오류가 나도 그날 다른 대화 상대의 알림은 남는다(대화 상대마다 commit)."""
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        broken, _, turn = _turning_persona(uid, cleanup)
        fine, _, _ = _turning_persona(uid, cleanup, name="김민서", addressTerm="민서")
        _sweep(datetime.now(UTC))
        real = minors._turning

        async def turning(db, persona, *args):
            if persona.id == broken:
                raise RuntimeError("시험용 실패")
            return await real(db, persona, *args)

        monkeypatch.setattr(minors, "_turning", turning)
        assert _sweep(_at(turn + timedelta(days=1))) >= 1
        [notice] = _notices(uid, "minor_turns14")
        assert notice.persona_id == fine
        assert _check(broken).turned14_notified_at is None
        # 다음 날 다시 한다
        monkeypatch.setattr(minors, "_turning", real)
        _sweep(_at(turn + timedelta(days=2)))
        assert {n.persona_id for n in _notices(uid, "minor_turns14")} == {broken, fine}


def test_sweep_trusts_only_a_counted_pass(provider, cleanup):
    """14세가 되는 달(재동의 시작 전)에 옛 나이(minor_child)로 받은 녹음이 통과해 재동의 표시가
    찍혀도, 시작 뒤 지금 나이(minor_teen)의 통과가 없으면 표시를 지우고 다시 알린다(게이트·카드는
    여전히 막는데 알림만 멈추지 않게 — passed 만 믿는다)."""
    with TestClient(app) as client:
        h, uid, _ = _signup(client, "김지은")
        pid, _, turn = _turning_persona(uid, cleanup)
        _sweep(datetime.now(UTC))
        _sweep(_at(turn))
        check = _check(pid)
        start = minors._aware(check.reconsent_from)
        due = minors._aware(check.reconsent_due_at)
        early = _at(turn + timedelta(days=3))
        pass_subject_consent(None, pid, "minor_child", at=early)
        _set(PersonaMinorCheck, check.id, reconsent_done_at=early)
        # 재동의 시작 전에는 그대로 둔다
        _sweep(early + timedelta(days=1))
        assert _check(pid).reconsent_done_at is not None
        # 시작 뒤 지금 나이의 통과가 없으니 표시를 지우고 기한 알림을 이어 간다
        _sweep(start + timedelta(days=1))
        assert _check(pid).reconsent_done_at is None
        _sweep(due - timedelta(days=30))
        assert len(_notices(uid, "minor_due_d30")) == 1
        # 지금 나이로 통과하면 끝난다
        pass_subject_consent(None, pid, "minor_teen", at=due - timedelta(days=20))
        _sweep(due - timedelta(days=19))
        assert _check(pid).reconsent_done_at is not None
        _sweep(due - timedelta(days=18))
        assert _check(pid).reconsent_done_at is not None
        _sweep(due + timedelta(days=8))
        assert _get(Persona, pid).hold_reason is None


def test_member_assembly_keeps_the_minor_flag(monkeypatch):
    """함께 쓰는 가족의 조립본에도 아이 안전 규칙이 들어간다(basicProfile.minor). 태어난 해와 달은
    보내지 않는다."""
    import asyncio

    from app.services import sharing

    sent: list[dict] = []

    async def assemble(self, **body):
        sent.append(body)
        return "조립본"

    async def recordings(db, persona_id):
        return []

    monkeypatch.setattr(type(sharing.ai_service), "assemble", assemble)
    monkeypatch.setattr(sharing.persona_builder, "load_recordings", recordings)
    teen = intake.validate_basics(CHILD | TEEN, today=TODAY)
    persona = Persona(id="p-minor", basics=teen, answers={}, subject_voices=[])
    asyncio.run(sharing.assemble_for_member(None, persona, PROFILE | {"myRelation": "할머니"}))
    profile = sent[-1]["intakeContext"]["basicProfile"]
    assert profile["minor"] is True and "2010" not in str(sent[-1])
    persona.basics = intake.validate_basics(CHILD | {"minor": False})
    asyncio.run(sharing.assemble_for_member(None, persona, PROFILE | {"myRelation": "할머니"}))
    assert sent[-1]["intakeContext"]["basicProfile"]["minor"] is False
