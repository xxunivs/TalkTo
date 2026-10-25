"""그분 동의 녹음(app/services/subject_consent.py · /api/v1/…/subject-consent · SPEC §5).

- 녹음을 시작하면 서버가 확인 단어 두 개를 발급한다(60분). 앞선 시도와 같은 쌍은 다시 뽑는다.
- 올린 녹음은 변환하지 않고 서울 저장소(personas/{pid}/subject_consent/)에만 둔다. 다시 녹음해도
  앞의 것을 지우지 않는다. 중간에 멈춘 것은 interrupted, '원하지 않으세요'는 우선 검수.
- 관리자가 10항목으로 검수한다. 통과는 녹음할 때 보인 대본의 판·해시로 동의 기록을 남기고 그분
  녹음 처리를 연다. 원하지 않음은 대화 상대를 멈추고 7일 뒤 지운다. 결과는 만든 분께 알린다.
"""

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from legal_gates import CHECKLIST_ALL, guardian_ok
from sqlalchemy import func, select
from test_persona_setup import _wav
from test_sharing import (  # noqa: F401
    OWNER_BASICS,
    _admin,
    _db,
    _persona,
    _set,
    _signup,
    settings,
)

from app.db.models import (
    ConsentRecord,
    OwnerNotice,
    Persona,
    PersonaMinorCheck,
    PersonaObjection,
    Recording,
    SetupStep,
    SubjectConsentRecording,
)
from app.main import app
from app.services import gate, legal, notify, subject_consent
from app.services.storage import get_storage

API = "/api/v1"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")

LIVING = {
    "name": "박영자",
    "relation": "어머니",
    "addressTerm": "엄마",
    "status": "alive",
    "myName": "김민수",
    "myRelation": "아들",
    "calledMe": "민수야",
    "minor": False,
}


@pytest.fixture(autouse=True)
def _quiet_ops(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """운영 알림(Discord)은 보내지 않고 글만 모은다(개인정보가 없는지 본다)."""
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda text, **_: sent.append(text))
    return sent


def _living(client: TestClient, *, ready: bool = False, recordings: bool = True):
    """살아 계신 분의 대화 상대(만든 분 머리글, id, 만든 분 id). 이야기 녹음 줄을 하나 넣어 둔다
    (검수 1번 '본인 목소리인가'를 비교할 녹음)."""
    h, owner_id, _ = _signup(client, "민수")
    created = client.post(f"{API}/personas", headers=h, json=LIVING)
    assert created.status_code == 201, created.text
    pid = created.json()["data"]["id"]
    if ready:
        _set(Persona, pid, setup_step=SetupStep.READY, status="READY", instructions="(시험)")
    if recordings:

        async def add(db):
            db.add(
                Recording(
                    persona_id=pid,
                    original_name="통화01.wav",
                    storage_key=f"personas/{pid}/recordings/r1.m4a",
                    content_type="audio/mp4",
                    size_bytes=10,
                )
            )
            await db.commit()

        _db(add)
    return h, pid, owner_id


def _start(client: TestClient, h: dict, pid: str):
    return client.post(f"{API}/personas/{pid}/subject-consent/start", headers=h)


def _upload(
    client: TestClient,
    h: dict,
    pid: str,
    rid: str,
    content: bytes | None = None,
    *,
    name: str = "consent.wav",
    **fields,
):
    data = {"steps": json.dumps([{"step": 1, "atMs": 0}, {"step": 2, "atMs": 4000}])}
    data |= {k: str(v).lower() if isinstance(v, bool) else v for k, v in fields.items()}
    return client.post(
        f"{API}/personas/{pid}/subject-consent/{rid}/audio",
        headers=h,
        data=data,
        files=[("audio", (name, content if content is not None else _wav(3), "audio/wav"))],
    )


def _submit(client: TestClient, h: dict, pid: str, **fields) -> str:
    started = _start(client, h, pid)
    assert started.status_code == 201, started.text
    rid = started.json()["data"]["id"]
    sent = _upload(client, h, pid, rid, **fields)
    assert sent.status_code == 200, sent.text
    return rid


def _state(client: TestClient, h: dict, pid: str) -> dict:
    response = client.get(f"{API}/personas/{pid}/subject-consent", headers=h)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _review(client: TestClient, admin: dict, rid: str, verdict: str, **body):
    payload = {"checklist": body.pop("checklist", CHECKLIST_ALL), "verdict": verdict, **body}
    return client.post(f"{API}/admin/subject-consents/{rid}/review", headers=admin, json=payload)


def _row(rid: str) -> SubjectConsentRecording:
    async def go(db):
        return await db.get(SubjectConsentRecording, rid)

    return _db(go)


def _notices(owner_id: str) -> list[OwnerNotice]:
    async def go(db):
        return list(
            (
                await db.scalars(
                    select(OwnerNotice)
                    .where(OwnerNotice.user_id == owner_id)
                    .order_by(OwnerNotice.created_at)
                )
            ).all()
        )

    return _db(go)


# ────────────────────────── 확인 단어 ──────────────────────────


def test_start_issues_two_easy_words_for_an_hour():
    with TestClient(app) as client:
        h, pid, _ = _living(client)
        before = datetime.now(UTC)
        started = _start(client, h, pid)
        assert started.status_code == 201, started.text
        data = started.json()["data"]
        assert len(data["words"]) == 2 and len(set(data["words"])) == 2
        assert all(word in subject_consent.WORDS for word in data["words"])
        assert data["attempt"] == 1 and data["mode"] == "adult"
        assert data["scriptDoc"] == "subject_consent_script"
        assert data["scriptVersion"] == legal.version_of("subject_consent_script")
        assert data["maxSeconds"] == 900 and data["maxMb"] == 32
        expires = datetime.fromisoformat(data["expiresAt"].replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        assert timedelta(minutes=59) < expires - before <= timedelta(minutes=61)
        assert _state(client, h, pid)["status"] == "issued"

        # 다시 시작하면 앞의 발급은 expired, 단어 쌍은 겹치지 않는다
        again = _start(client, h, pid).json()["data"]
        assert again["attempt"] == 2
        assert set(again["words"]) != set(data["words"])
        assert _row(data["id"]).status == "expired"
        assert _row(data["id"]).words == " ".join(data["words"])  # 앞선 줄도 지우지 않는다


def test_words_never_repeat_a_pair():
    rows = [
        SubjectConsentRecording(words=" ".join(pair))
        for pair in (subject_consent.WORDS[i : i + 2] for i in range(0, 78, 2))
    ]
    used = {frozenset(row.words.split()) for row in rows}
    for _ in range(50):
        assert frozenset(subject_consent._new_words(rows)) not in used
    assert len(subject_consent.WORDS) >= 80 and len(set(subject_consent.WORDS)) == 80


def test_who_needs_a_consent_recording():
    """돌아가신 분은 필요 없고, 미성년 여부를 답하지 않은 예전 살아 계신 분은 먼저 답한다."""
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "지은")
        deceased = _persona(owner_id)
        state = _state(client, h, deceased)
        assert state["required"] is False and state["blockCode"] == "not_required"
        refused = _start(client, h, deceased)
        assert refused.status_code == 409 and refused.json()["code"] == "not_required"

        old = _persona(owner_id, basics={k: v for k, v in LIVING.items() if k != "minor"})
        state = _state(client, h, old)
        assert state["required"] is True and state["mode"] is None
        assert state["blockCode"] == "minor_unknown"
        assert _start(client, h, old).json()["code"] == "minor_unknown"

        # 가족·남은 볼 수 없다
        other, _, _ = _signup(client, "남")
        assert client.get(f"{API}/personas/{old}/subject-consent", headers=other).status_code == 404


def test_teen_needs_the_guardian_check_first():
    """만 14~18세 아이: 법정대리인 확인(attest)이 없으면 녹음을 시작하지 못한다."""
    teen = LIVING | {
        "relation": "아들",
        "myRelation": "어머니",
        "minor": True,
        "birthYm": "2010-01",
        "guardianBasis": "both_agreed",
    }
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "지수")
        pid = _persona(owner_id, basics=teen)
        state = _state(client, h, pid)
        assert state["mode"] == "minor_teen" and state["scriptDoc"] == "subject_consent_child"
        assert state["guardian"]["needed"] is True and "attest" in state["guardian"]["missing"]
        blocked = _start(client, h, pid)
        assert blocked.status_code == 409 and blocked.json()["code"] == "guardian"
        guardian_ok(client, pid)
        started = _start(client, h, pid)
        assert started.status_code == 201, started.text
        assert started.json()["data"]["mode"] == "minor_teen"


# ────────────────────────── 올리기 ──────────────────────────


@needs_ffmpeg
def test_upload_keeps_the_file_as_is_in_seoul(_quiet_ops):
    with TestClient(app) as client:
        h, pid, _ = _living(client)
        started = _start(client, h, pid).json()["data"]
        content = _wav(3)
        sent = _upload(client, h, pid, started["id"], content)
        assert sent.status_code == 200, sent.text
        assert sent.json()["data"] == {"id": started["id"], "status": "submitted"}
        row = _row(started["id"])
        assert row.storage_key == f"personas/{pid}/subject_consent/01-{row.id}.wav"
        assert get_storage().get(row.storage_key) == content  # 바꾸지 않고 그대로
        assert row.sha256 == hashlib.sha256(content).hexdigest()
        assert row.size_bytes == len(content) and 2900 <= row.duration_ms <= 3100
        assert row.client_meta["steps"] == [{"step": 1, "atMs": 0}, {"step": 2, "atMs": 4000}]
        assert "ua" not in row.client_meta
        # 이야기 녹음(받아쓰기 대상) 표에는 들어가지 않는다
        count = _db(
            lambda db: db.scalar(
                select(func.count(Recording.id)).where(Recording.persona_id == pid)
            )
        )
        assert count == 1
        # 운영 알림에는 개인정보 없이 건수만
        waiting = [text for text in _quiet_ops if "동의 녹음" in text]
        assert waiting == [
            "🎙️ 동의 녹음 검수 대기 1건 — 관리자 화면 '동의 녹음 검수'에서 확인해 주세요"
        ]
        state = _state(client, h, pid)
        assert state["status"] == "submitted" and state["submittedCount"] == 1
        # 응답을 받지 못해 같은 파일을 다시 보내면 같은 줄
        again = _upload(client, h, pid, started["id"], content)
        assert again.status_code == 200 and again.json()["data"]["status"] == "submitted"
        # 다른 파일은 처음부터(확인 단어가 바뀌었어요)
        other = _upload(client, h, pid, started["id"], _wav(4))
        assert other.status_code == 409 and other.json()["code"] == "expired"


@needs_ffmpeg
def test_upload_checks(monkeypatch: pytest.MonkeyPatch):
    with TestClient(app) as client:
        h, pid, _ = _living(client)
        rid = _start(client, h, pid).json()["data"]["id"]
        bad = _upload(client, h, pid, rid, b"not audio", name="consent.txt")
        assert bad.status_code == 400 and bad.json()["code"] == "bad_format"
        short = _upload(client, h, pid, rid, _wav(0.4))
        assert short.status_code == 400 and short.json()["detail"] == subject_consent.NO_LENGTH
        settings = subject_consent.get_settings()
        monkeypatch.setattr(
            subject_consent,
            "get_settings",
            lambda: settings.model_copy(update={"subject_consent_max_seconds": 1}),
        )
        long = _upload(client, h, pid, rid, _wav(8))
        assert long.status_code == 400 and long.json()["detail"] == subject_consent.TOO_LONG
        monkeypatch.setattr(
            subject_consent,
            "get_settings",
            lambda: settings.model_copy(update={"subject_consent_max_mb": 0}),
        )
        big = _upload(client, h, pid, rid, _wav(2))
        assert big.status_code == 413
        monkeypatch.undo()
        # 60분이 지나면 받지 않는다
        _set(SubjectConsentRecording, rid, expires_at=datetime.now(UTC) - timedelta(minutes=1))
        late = _upload(client, h, pid, rid)
        assert late.status_code == 409 and late.json()["code"] == "expired"
        assert _row(rid).status == "expired"


@needs_ffmpeg
def test_interrupted_refused_and_superseded():
    with TestClient(app) as client:
        h, pid, _ = _living(client)
        # 중간에 멈춤: 기록으로 남고 검수 대상이 아니다
        cut = _submit(client, h, pid, interrupted=True, reason="hidden")
        assert _row(cut).status == "interrupted"
        assert _row(cut).client_meta["reason"] == "hidden"
        # 올리지 못하고 끊김(abandon)
        rid = _start(client, h, pid).json()["data"]["id"]
        gone = client.post(
            f"{API}/personas/{pid}/subject-consent/{rid}/abandon",
            headers=h,
            json={"reason": "mic"},
        )
        assert gone.status_code == 200 and gone.json()["data"]["status"] == "interrupted"
        # 검수 전에 새로 올리면 앞의 것은 superseded(지우지 않는다)
        first = _submit(client, h, pid)
        second = _submit(client, h, pid)
        assert _row(first).status == "superseded" and _row(second).status == "submitted"
        assert get_storage().exists(_row(first).storage_key)
        # 그분이 원하지 않으세요: 끊겼어도 검수로 보낸다(우선)
        no = _submit(client, h, pid, interrupted=True, refused=True)
        assert _row(no).status == "submitted" and _row(no).refused_flag is True
        assert _row(second).status == "superseded"


@needs_ffmpeg
def test_too_many_attempts_until_admin_allows_one_more():
    with TestClient(app) as client:
        h, pid, _ = _living(client)
        admin, _ = _admin(client)
        rids = [_submit(client, h, pid) for _ in range(5)]
        assert _state(client, h, pid)["maxAttempts"] == 5
        blocked = _start(client, h, pid)
        assert blocked.status_code == 409 and blocked.json()["code"] == "too_many"
        assert _state(client, h, pid)["canStart"] is False
        partial = dict(CHECKLIST_ALL, **{"8": False})
        retry = _review(client, admin, rids[-1], "rerequest", checklist=partial, allowMore=True)
        assert retry.status_code == 200, retry.text
        assert _state(client, h, pid)["maxAttempts"] == 6
        assert _start(client, h, pid).status_code == 201


# ────────────────────────── 검수 ──────────────────────────


@needs_ffmpeg
def test_admin_queue_and_detail():
    with TestClient(app) as client:
        h, pid, _ = _living(client)
        admin, _ = _admin(client)
        older = _submit(client, h, pid)
        h2, pid2, _ = _living(client)
        refused = _submit(client, h2, pid2, refused=True, interrupted=True)
        queue = client.get(f"{API}/admin/subject-consents", headers=admin)
        assert queue.status_code == 200, queue.text
        ids = [item["id"] for item in queue.json()["data"]]
        assert ids.index(refused) < ids.index(older)  # 원하지 않음 먼저
        item = next(i for i in queue.json()["data"] if i["id"] == older)
        assert item["ageBand"] == "만 19세 이상" and item["submittedCount"] == 1
        assert item["title"] == "'엄마' 대화 상대" and "@" in item["ownerMasked"]
        assert queue.json()["meta"]["total"] >= 2
        assert client.get(f"{API}/admin/subject-consents", headers=h).status_code == 403

        detail = client.get(f"{API}/admin/subject-consents/{older}", headers=admin)
        assert detail.status_code == 200, detail.text
        data = detail.json()["data"]
        assert len(data["words"]) == 2 and data["hasStoryRecordings"] is True
        assert data["audioUrl"] and "subject_consent" in data["audioUrl"]
        assert [c["key"] for c in data["checklist"]] == [str(n) for n in range(1, 11)]
        assert data["checklist"][3]["title"] == '"괜찮아요?"에 본인이 답했는가'
        assert data["persona"]["status"] == "alive" and data["guardian"] is None
        assert data["recordings"][0]["name"] == "통화01.wav"
        assert data["gapSeconds"] is not None and data["review"] is None
        assert data["rerequestHead"] == subject_consent.REREQUEST_HEAD


@needs_ffmpeg
def test_pass_opens_processing_and_records_the_recorded_script(monkeypatch):
    with TestClient(app) as client:
        h, pid, owner_id = _living(client)
        admin, _ = _admin(client)
        rid = _submit(client, h, pid)
        recorded = _row(rid)
        assert client.post(f"{API}/personas/{pid}/transcribe", headers=h).status_code == 409
        # 녹음한 뒤 대본이 바뀌어도 기록은 녹음할 때 보인 대본의 판·해시
        monkeypatch.setattr(legal, "text_sha256", lambda key: "0" * 64)
        not_all = _review(client, admin, rid, "pass", checklist=dict(CHECKLIST_ALL, **{"6": False}))
        assert not_all.status_code == 400 and not_all.json()["code"] == "checklist"
        done = _review(client, admin, rid, "pass")
        assert done.status_code == 200, done.text
        assert _row(rid).status == "passed" and _row(rid).reviewed_at is not None

        async def records(db):
            return list(
                (
                    await db.scalars(
                        select(ConsentRecord).where(
                            ConsentRecord.consent_type == f"subject_consent:{pid}"
                        )
                    )
                ).all()
            )

        (record,) = _db(records)
        assert record.user_id == owner_id and record.channel == "admin_review"
        assert record.doc == "subject_consent_script"
        assert record.version == recorded.script_version
        assert record.text_sha256 == recorded.script_sha256 != "0" * 64
        notice = _notices(owner_id)[-1]
        assert (
            notice.kind == "subject_consent_passed"
            and notice.title == "그분 동의 녹음이 확인됐어요"
        )
        assert notice.body.startswith("'엄마' 대화 상대의 그분 동의 녹음이 확인됐어요.")
        assert notice.action == {"label": "이어서 만들기", "route": f"/p/{pid}"}
        state = _state(client, h, pid)
        assert state["status"] == "passed" and state["canStart"] is False
        assert state["blockCode"] == "already_passed"
        assert _start(client, h, pid).json()["code"] == "already_passed"
        # 한 번 정한 녹음은 다시 판정하지 않는다
        assert _review(client, admin, rid, "hold").status_code == 409


@needs_ffmpeg
def test_pass_needs_a_story_recording_to_compare():
    with TestClient(app) as client:
        h, pid, _ = _living(client, recordings=False)
        admin, _ = _admin(client)
        rid = _submit(client, h, pid)
        response = _review(client, admin, rid, "pass")
        assert response.status_code == 409 and response.json()["code"] == "no_recordings"
        assert _row(rid).status == "submitted"  # 대기열에 그대로


@needs_ffmpeg
def test_rerequest_and_hold_tell_the_owner():
    with TestClient(app) as client:
        h, pid, owner_id = _living(client, ready=True)
        admin, _ = _admin(client)
        rid = _submit(client, h, pid)
        note = "낭독은 그분이 직접 하셔야 해요."
        partial = dict(CHECKLIST_ALL, **{"5": False})
        assert (
            _review(
                client, admin, rid, "rerequest", checklist=partial, noteToOwner=note
            ).status_code
            == 200
        )
        notice = _notices(owner_id)[-1]
        assert notice.kind == "subject_consent_rerequest" and notice.body == note
        assert notice.action["route"] == f"/p/{pid}/consent"
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        assert detail["actions"][0]["kind"] == "subject_consent"
        assert detail["actions"][0]["body"] == note

        held = _submit(client, h, pid)
        card = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["actions"][0]
        assert card["kind"] == "subject_consent_review"
        assert (
            _review(
                client, admin, held, "hold", checklist=dict(CHECKLIST_ALL, **{"6": False})
            ).status_code
            == 200
        )
        notice = _notices(owner_id)[-1]
        assert notice.kind == "subject_consent_hold" and notice.action is None
        assert "'엄마' 대화 상대의" in notice.body
        assert _start(client, h, pid).json()["code"] == "in_review_hold"
        card = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["actions"][0]
        assert card["kind"] == "subject_consent_hold"
        # 보류는 다시 판정할 수 있다
        assert _review(client, admin, held, "pass").status_code == 200


@needs_ffmpeg
def test_refused_stops_the_persona():
    with TestClient(app) as client:
        h, pid, owner_id = _living(client)
        admin, _ = _admin(client)
        rid = _submit(client, h, pid)
        # 원하지 않음은 녹음 중 '원하지 않으세요'를 눌렀거나 내부 메모가 있어야 한다
        bare = _review(client, admin, rid, "refused", checklist={})
        assert bare.status_code == 400 and bare.json()["code"] == "refused_reason"
        done = _review(client, admin, rid, "refused", checklist={}, adminNote="싫다고 하심")
        assert done.status_code == 200, done.text
        persona = _db(lambda db: db.get(Persona, pid))
        assert persona.hold_reason == "subject_refused" and persona.delete_after is not None
        assert _notices(owner_id)[-1].kind == "stopped"
        blocked = _start(client, h, pid)
        assert blocked.status_code == 409 and blocked.json()["code"] == "stopped"


@needs_ffmpeg
def test_minor_reconsent_hold_can_still_record_and_is_cleared_by_a_pass():
    """아이 본인 동의 기한을 넘겨 멈춘 대화 상대도 동의 녹음은 받는다. 통과하면 되살린다."""
    with TestClient(app) as client:
        h, pid, owner_id = _living(client)
        admin, _ = _admin(client)
        now = datetime.now(UTC)
        _set(
            Persona,
            pid,
            hold_reason="minor_consent_missing",
            hold_at=now,
            delete_after=now + timedelta(days=7),
        )

        async def reconsent(db):
            db.add(
                PersonaMinorCheck(
                    persona_id=pid,
                    reconsent_kind="19",
                    reconsent_from=now - timedelta(days=200),
                    reconsent_due_at=now - timedelta(days=10),
                )
            )
            await db.commit()

        _db(reconsent)
        rid = _submit(client, h, pid)
        assert _review(client, admin, rid, "pass").status_code == 200
        persona = _db(lambda db: db.get(Persona, pid))
        assert persona.hold_reason is None and persona.delete_after is None
        check = _db(
            lambda db: db.scalar(
                select(PersonaMinorCheck).where(PersonaMinorCheck.persona_id == pid)
            )
        )
        assert check.reconsent_done_at is not None
        assert "resumed" in [n.kind for n in _notices(owner_id)]


def test_reconsent_records_from_the_next_month_only():
    """14·19세 재동의는 그 나이가 된 다음 달 1일(reconsent_from)부터 새 나이로 녹음한다. 그 전에
    옛 나이로 녹음하면 시작하는 날 세지 않게 되므로 날짜를 알리고 막는다."""
    with TestClient(app) as client:
        h, pid, _ = _living(client)
        start = datetime.now(UTC) + timedelta(days=5)

        async def reconsent(db):
            db.add(
                PersonaMinorCheck(
                    persona_id=pid,
                    reconsent_kind="19",
                    reconsent_from=start,
                    reconsent_due_at=start + timedelta(days=180),
                )
            )
            await db.commit()

        _db(reconsent)
        day = gate.month_day(start)
        blocked = _start(client, h, pid)
        assert blocked.status_code == 409 and blocked.json()["code"] == "reconsent_not_yet"
        assert blocked.json()["detail"] == f"{day}부터 녹음할 수 있어요."
        state = _state(client, h, pid)
        assert state["canStart"] is False and state["blockCode"] == "reconsent_not_yet"
        # 시작한 뒤에는 녹음할 수 있다
        _set(
            PersonaMinorCheck, _check_id(pid), reconsent_from=datetime.now(UTC) - timedelta(days=1)
        )
        assert _start(client, h, pid).status_code == 201


def _check_id(pid: str) -> str:
    return _db(
        lambda db: db.scalar(
            select(PersonaMinorCheck.id).where(PersonaMinorCheck.persona_id == pid)
        )
    )


@needs_ffmpeg
def test_consent_pass_does_not_lift_a_hold_with_an_open_objection(_quiet_ops: list[str]):
    """기한 넘김으로 멈춘 대화 상대에 이의가 들어와 있으면 동의 녹음이 통과해도 저절로 풀지 않는다
    (이의는 관리자가 직접 푼다 — holds.clear_for_consent). 운영 알림만 남긴다."""
    with TestClient(app) as client:
        h, pid, _ = _living(client)
        admin, _ = _admin(client)
        now = datetime.now(UTC)
        _set(
            Persona,
            pid,
            hold_reason="minor_consent_missing",
            hold_at=now,
            delete_after=now + timedelta(days=7),
        )

        async def objection(db):
            db.add(
                PersonaObjection(
                    persona_id=pid, reason="other_parent", from_relation="아버지", received_at=now
                )
            )
            await db.commit()

        _db(objection)
        rid = _submit(client, h, pid)
        assert _review(client, admin, rid, "pass").status_code == 200
        persona = _db(lambda db: db.get(Persona, pid))
        assert persona.hold_reason == "minor_consent_missing"
        assert any("열린 이의" in text for text in _quiet_ops)


@needs_ffmpeg
def test_deleting_the_persona_removes_consent_recordings():
    with TestClient(app) as client:
        h, pid, _ = _living(client)
        rid = _submit(client, h, pid)
        key = _row(rid).storage_key
        assert get_storage().exists(key)
        assert client.delete(f"{API}/personas/{pid}", headers=h).status_code == 200
        assert _row(rid) is None
        assert not get_storage().exists(key)


def test_rate_limits_are_registered():
    from app.core import ratelimit

    assert ratelimit.SUBJECT_CONSENT_START in ratelimit.ALL
    assert ratelimit.SUBJECT_CONSENT_UPLOAD in ratelimit.ALL


def test_checklist_and_texts_are_complete():
    assert [item["key"] for item in subject_consent.CHECKLIST] == [str(n) for n in range(1, 11)]
    assert all(item["title"] and item["failExample"] for item in subject_consent.CHECKLIST)
    assert subject_consent.CHECKLIST[5]["rerequest"] is None  # 6번(유도·압박)은 보류
    # 고객에게 보이는 글에 '상담'·회사 이름을 넣지 않는다
    texts = [
        subject_consent.PASSED_BODY,
        subject_consent.HOLD_BODY,
        subject_consent.REREQUEST_HEAD,
        *[item["rerequest"] for item in subject_consent.CHECKLIST if item["rerequest"]],
        subject_consent.TOO_MANY,
        subject_consent.EXPIRED,
    ]
    for text in texts:
        assert "상담" not in text
        assert not any(name in text for name in ("Fish", "ElevenLabs", "OpenAI"))
