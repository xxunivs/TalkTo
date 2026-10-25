"""가족 표(M2-01): 그분이 알아야 할 가족. 대화에서 '아빠'라고 해도 누구인지 알아듣게 한다.

이름은 모두 지어낸 것이다.
"""

from fastapi.testclient import TestClient
from test_billing import _ready_persona
from test_persona_setup import BASICS, FakeAI, _signup, _wait, fake_ai  # noqa: F401

from app.main import app
from app.services import intake

API = "/api/v1"
ROWS = [
    {
        "name": "홍철수",
        "relation": "큰아들",
        "toMe": "아빠",
        "calledBy": "철수야",
        "status": "alive",
        "note": "막내라 늘 걱정하셨어요",
    },
    {"name": "홍영감", "relation": "남편", "toMe": "할아버지", "status": "deceased"},
]


def test_family_rows_are_cleaned_and_never_raise():
    rows = intake.validate_answers(
        {
            "M2-01": [
                *ROWS,
                {"name": "홍철수", "relation": "같은 이름은 한 번만"},
                {"relation": "이름 없는 줄"},
                "줄이 아닌 값",
                {"name": "가" * 40, "status": "모름", "secret": "모르는 칸"},
            ]
        },
        final=False,
    )["M2-01"]
    assert [r["name"] for r in rows] == ["홍철수", "홍영감", "가" * 15]
    assert rows[0] == ROWS[0]
    assert "status" not in rows[2] and "secret" not in rows[2]
    # 문자열이나 이상한 값이 와도 오류 없이 비운다
    assert "M2-01" not in intake.validate_answers({"M2-01": "홍철수"}, final=False)
    many = [{"name": f"가족{i}"} for i in range(20)]
    assert len(intake.validate_answers({"M2-01": many}, final=False)["M2-01"]) == 10


def test_listener_comes_first_and_family_follows():
    basics = intake.validate_basics(BASICS)
    answers = {
        "M2-01": [
            *ROWS,
            # 나(회원)와 그분 자신은 가족 줄로 넣지 않는다
            {"name": basics["myName"], "relation": "손자"},
            {"name": basics["name"], "relation": "본인"},
            {"name": "나", "relation": "손자"},
        ],
        "M2-01-self": "할머니가",
        "M2-01-level": "반말",
    }
    payload = intake.build_intake(basics, intake.validate_answers(answers, final=False))
    family = payload["intakeContext"]["familyMap"]
    me, son, husband = family
    assert me["name"] == basics["myName"] and me["isListener"] is True
    assert me["relation"] == "손자" and me["selfReference"] == "할머니가"
    assert me["speechLevel"] == "반말"
    assert son == {
        "name": "홍철수",
        "relation": "큰아들",
        "addressTermTo": "철수야",
        "listenerCalls": "아빠",
        "note": "막내라 늘 걱정하셨어요",
    }
    assert husband == {
        "name": "홍영감",
        "relation": "남편",
        "listenerCalls": "할아버지",
        "deceased": True,
    }
    taboo = payload["intakeContext"]["tabooTopics"]
    assert {"topic": "다른 가족의 사생활", "grade": "금지"}.items() <= next(
        t for t in taboo if t["sourceQuestion"] == "M2-01"
    ).items()
    context = payload["subjectContext"]
    assert [m["name"] for m in context["familyMembers"]] == [basics["myName"], "홍철수", "홍영감"]
    assert context["familyMembers"][1]["addressTerms"] == ["철수야"]
    assert "홍철수" in context["glossaryTerms"]


def test_without_family_rows_nothing_changes():
    basics = intake.validate_basics(BASICS)
    payload = intake.build_intake(basics, {})
    assert len(payload["intakeContext"]["familyMap"]) == 1
    assert not any(t["sourceQuestion"] == "M2-01" for t in payload["intakeContext"]["tabooTopics"])


def test_self_persona_rows_are_my_family():
    basics = intake.validate_basics({"relation": intake.SELF_RELATION, "name": "홍길동"})
    payload = intake.build_intake(basics, {"M2-01": [{"name": "홍철수", "relation": "아들"}]})
    me, son = payload["intakeContext"]["familyMap"]
    assert me["relation"] == "본인(이 대화 상대 자신)" and me["isListener"] is True
    assert "listenerCalls" not in son


def test_meta_and_saving_family_rebuilds_the_persona(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        meta = client.get(f"{API}/intake/meta").json()["data"]
        q = {x["id"]: x for x in meta["questions"]}["M2-01"]
        assert q["type"] == "family" and q["required"] is False and q["section"] == "나와 그분"
        assert "어머니" in meta["myRelations"]

        h = _signup(client, "family-rows@example.com")
        pid = _ready_persona(client, h)
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        answers = {**detail["answers"], "M2-01": ROWS}
        saved = client.put(
            f"{API}/personas/{pid}", headers=h, json={"basics": BASICS, "answers": answers}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["updating"] is True
        _wait(client, h, pid, "UPDATE")
        family = fake_ai.calls["/v1/persona/assembly"][-1]["intakeContext"]["familyMap"]
        assert [row["name"] for row in family][:3] == [BASICS["myName"], "홍철수", "홍영감"]
        assert family[1]["listenerCalls"] == "아빠"
        kept = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["answers"]
        assert kept["M2-01"] == ROWS
        # 같은 줄을 다시 저장하면 다시 만들지 않는다
        again = client.put(
            f"{API}/personas/{pid}", headers=h, json={"basics": BASICS, "answers": kept}
        )
        assert again.status_code == 200 and again.json()["data"]["updating"] is False


def test_recording_terms_fill_the_matching_family_row():
    """녹음에서 그분이 '철수야'라고 부른 사람은 가족 표의 홍철수 줄에 채운다(새 줄 없이)."""
    from types import SimpleNamespace

    from app.services import persona_builder

    basics = intake.validate_basics(BASICS)
    answers = {"M2-01": [{"name": "홍철수", "relation": "큰아들", "toMe": "아빠"}]}
    analysis = {
        "speechStyle": {
            "addressTerms": [
                {"person": "철수", "term": "철수야"},
                {"person": "영희", "term": "영희야"},
                {"person": basics["myName"], "term": "강아지"},
            ]
        }
    }
    rec = SimpleNamespace(analysis=analysis, segments=[])
    _, ic, _ = persona_builder.build_prompt_material(basics, answers, [rec], set())
    by_name = {row["name"]: row for row in ic["familyMap"]}
    assert set(by_name) == {basics["myName"], "홍철수", "영희"}
    assert by_name["홍철수"]["addressTermTo"] == "철수야"
    # 나를 부르는 말은 기본 정보에서 적은 대로 둔다
    assert by_name[basics["myName"]].get("addressTermTo") == basics.get("calledMe")


def test_progress_heartbeat_writes_through_its_own_session(fake_ai: FakeAI):  # noqa: F811
    """막대 덤 기록은 작업 세션과 다른 세션으로 적는다(작업이 바꾸던 값이 사라지지 않게)."""
    import asyncio

    from app.db.models import PersonaJob
    from app.db.session import SessionLocal
    from app.services import persona_builder

    with TestClient(app) as client:
        h = _signup(client, "family-heartbeat@example.com")
        pid = _ready_persona(client, h)

    async def go():
        async with SessionLocal() as db:
            job = PersonaJob(persona_id=pid, kind="VOICE", detail="준비하는 중", percent=40)
            db.add(job)
            await db.commit()
            write = persona_builder._job_writer(job.id)
            await write("그분 목소리를 다시 만드는 중", 55.4)
            await write("뒤로 가지 않는다", 30)
            await db.refresh(job)
            return job.detail, job.percent

    detail, percent = asyncio.run(go())
    assert percent == 55 and detail == "뒤로 가지 않는다"
