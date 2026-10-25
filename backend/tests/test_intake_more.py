"""다 만든 뒤 '더 알려주기': 선택 질문에 답하면 지침과 기억을 다시 만든다."""

import asyncio
import time

from fastapi.testclient import TestClient
from legal_gates import declare_bereaved
from sqlalchemy import select
from test_billing import _ready_persona
from test_persona_setup import ANSWERS, BASICS, FakeAI, _signup, _wait, fake_ai  # noqa: F401

from app.core.config import get_settings
from app.db.models import Memory, Persona
from app.db.session import SessionLocal
from app.main import app
from app.services import intake

API = "/api/v1"

# 2026-10-17 '질문 더 답하기'에서 만들 때 질문으로 옮긴 문항(id 는 그대로). 2026-10-21 개편에서
# 언제 무렵(P1-05)·고향(M1-02)·하신 일(M1-07)·말끝(M3-07)은 다시 '기억 더 채우기'로 갔다(TO_MORE).
MOVED = {
    "C-06",
    "C-04",
    "M8-11",
    "M8-03",
    "M8-08",
    "M9-03",
    *(f"M9-01-{key}" for key in intake.M9_TOPICS),
}
# 2026-10-21: 만들 때 질문 → 기억 더 채우기('모름'과 함께 묻는 그분의 개인 정보와 말투 세부)
TO_MORE = {"P1-05", "M1-02", "M1-07", "M3-05", "M3-07"}
# 2026-10-21: 기억 더 채우기 → 만들 때 질문(첫마디·마지막 말·챙겨 주시던 장면)
TO_CREATION = {"M3-01", "M3-02", "C-08"}
# 개편 뒤 가족·고인이 꼭 답할 질문(Persona_Intake.md PART 0 순서). 임종 등급은 미리 채운 값을
# 확인만 한다(돌아가신 분만).
V2_REQUIRED = [
    "P1-07A",
    "M2-01-level",
    "M2-01-self",
    "M3-01",
    "M3-02",
    "C-09",
    "C-01",
    "C-02",
    "C-07",
    "C-03",
    "M4-01",
    "M6-08",
    "M7-01",
    "M9-01-death",
]


def _agree(client: TestClient, h: dict, pid: str, kind: str) -> None:
    """질문 더 답하기의 민감 범주 동의."""
    response = client.post(
        f"{API}/personas/{pid}/consents", headers=h, json={"type": kind, "agreed": True}
    )
    assert response.status_code == 200, response.text


def test_more_questions_improve_persona(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        meta = client.get(f"{API}/intake/meta").json()["data"]
        more = {q["id"]: q for q in meta["moreQuestions"]}
        assert {"P1-05", "M1-02", "M5-01", "M7-01-2", "T1-02"} <= set(more)
        assert more["T1-02"]["when"] == "deceased"
        # 첫마디·먼저 묻던 말·조심할 이야기는 만들 때 묻는다. 예전에 '질문 더 답하기'에서 저장한
        # 답은 같은 id 라 아래처럼 그대로 저장·반영된다.
        asked = {q["id"] for q in meta["questions"]}
        assert {"M3-01", "M8-11", "M9-01-health", "M9-03"} <= asked and not asked & set(more)
        modules = {m["id"] for m in meta["moreModules"]}
        assert "인생 이야기" in modules and "조심할 이야기" not in modules

        h = _signup(client, "more-questions@example.com")
        pid = _ready_persona(client, h)
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        answers = {
            **detail["answers"],
            "M3-01": "우리 강아지니?",
            "M3-04": "아이고 / 아유",
            "M5-01": "마당에 감나무가 있던 기와집. 겨울이면 아궁이에 고구마를 구웠다.",
            "M6-01": ["김치찌개 — 일요일마다", "식혜 — 명절마다"],
            "M8-11": ["밥은 먹었냐"],
            "M9-01-health": "물으면만",
            "M9-03": ["큰아버지"],
            "T1-02": "노환으로 편안하게",
        }
        # 건강(T1-02)은 동의한 뒤에만 받는다
        _agree(client, h, pid, "sensitive_health")
        saved = client.put(
            f"{API}/personas/{pid}", headers=h, json={"basics": BASICS, "answers": answers}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["data"]["updating"] is True
        _wait(client, h, pid, "UPDATE")
        # 저장한 답은 그대로 남는다(정보 고치기에서 다시 저장해도 지워지지 않게)
        kept = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["answers"]
        assert kept["M6-01"] == answers["M6-01"] and kept["M9-01-health"] == "물으면만"

        ic = fake_ai.calls["/v1/persona/assembly"][-1]["intakeContext"]
        assert ic["speechStyle"]["callOpen"] == "우리 강아지니?"
        assert ic["speechStyle"]["interjections"] == ["아이고", "아유"]
        assert ic["basicProfile"]["deathContext"] == "노환으로 편안하게"
        assert {"situation": "먼저 말을 걸 때", "response": "밥은 먹었냐"} in ic[
            "situationalReactions"
        ]
        taboo = {(t["topic"], t["grade"]) for t in ic["tabooTopics"]}
        assert ("그분의 건강·병력", "물으면만") in taboo and ("큰아버지", "물으면만") in taboo
        # 인생 이야기·대표 음식은 기억으로(지침·기억 보기는 고객 API 에 없어 DB 에서 읽는다)
        texts = _memory_texts(pid)
        assert any("어린 시절 살던 집" in t and "감나무" in t for t in texts)
        assert sum("대표 음식" in t for t in texts) == 2


def test_moved_questions_are_asked_at_creation_and_optional():
    """2026-10-21 개편의 질문 배치. 같은 id 는 한 목록에만, 개편 뒤 필수는 PART 0 순서."""
    asked = {q["id"]: q for q in intake.QUESTIONS}
    more = {q["id"]: q for q in intake.MORE_QUESTIONS}
    assert MOVED <= set(asked) and not MOVED & set(more)
    assert all(asked[qid]["required"] is False for qid in MOVED - {"M9-01-death"})
    assert TO_MORE <= set(more) and not TO_MORE & set(asked)
    assert TO_CREATION <= set(asked) and not TO_CREATION & set(more)
    # 개편 뒤 꼭 답할 것(가족·고인). 예전 대화 상대·나 자신은 예전 넷이다(LEGACY_REQUIRED)
    assert [q["id"] for q in intake.QUESTIONS if q.get("required")] == V2_REQUIRED
    assert asked["C-05"]["required"] is False
    assert intake.LEGACY_REQUIRED == ("C-01", "C-02", "C-05", "M4-01")
    # basics 없이 부르면 예전 필수로 본다(예전 답도 최종 검사를 지난다)
    assert intake.validate_answers(ANSWERS, final=True)
    ids = [q["id"] for q in [*intake.QUESTIONS, *intake.MORE_QUESTIONS]]
    assert len(ids) == len(set(ids))
    # '기억이 안 나요'·'따로 없었어요'·'모름' 단추
    forgot = {"value": "__unknown__", "label": "기억이 안 나요"}
    assert asked["M3-01"]["skipOption"] == forgot and asked["M3-02"]["skipOption"] == forgot
    assert asked["M2-01-self"]["skipOption"] == {"value": "__none__", "label": "따로 없었어요"}
    for qid in ("P1-05", "M1-02", "M1-07"):
        assert more[qid]["skipOption"] == {"value": "__unknown__", "label": "모름"}
    # 떠오르는 것(M6-08)과 그 까닭(M6-08-why, 선택)은 나란한 두 칸
    order = list(asked)
    assert order.index("M6-08-why") == order.index("M6-08") + 1
    assert asked["M6-08"]["type"] == asked["M6-08-why"]["type"] == "text"
    assert asked["M6-08-why"]["required"] is False
    assert asked["M6-08-why"]["title"] == "왜 떠오르나요? (선택)"
    # 부르던 말(P1-07A)은 기본 정보에 있으면 묻지 않는다
    assert asked["P1-07A"]["skipIfBasics"] == "calledMe" and asked["P1-07A"]["when"] == "notSelf"
    # 동의가 필요한 건강(T1-02)은 기억 더 채우기에 남는다
    assert "T1-02" in more and not any(q.get("sensitive") for q in intake.QUESTIONS)
    # 임종 줄은 돌아가신 분만, M9-01 은 촘촘한 줄, 녹음에서 찾은 말 후보는 만들 때 C-09 만
    assert asked["M9-01-death"]["when"] == "deceased"
    assert all(asked[f"M9-01-{key}"]["compact"] is True for key in intake.M9_TOPICS)
    assert {q["id"] for q in intake.QUESTIONS if q.get("suggest")} == {"C-09"}
    # 살아 계신 분은 현재형, 돌아가신 분은 과거형
    living = intake.validate_basics(BASICS | {"status": "alive", "minor": False})
    assert intake.question_title(asked["C-07"], BASICS) == "그분은 나를 어떻게 챙기셨어요?"
    assert intake.question_title(asked["C-07"], living) == "그분은 나를 어떻게 챙기세요?"


def test_meta_sections_and_modules():
    with TestClient(app) as client:
        meta = client.get(f"{API}/intake/meta").json()["data"]
    # 묶음 안내는 질문이 처음 나오는 순서대로(Persona_Intake.md PART 0). 묶음마다 한 줄 안내가 있다
    order = list(dict.fromkeys(q["section"] for q in meta["questions"]))
    assert [s["id"] for s in meta["sections"]] == order
    assert order == [
        "나와 그분",
        "그분이 하시던 말",
        "그분이 해 주신 것",
        "그분은 어떤 분",
        "조심할 이야기",
    ]
    assert all(s.get("lead") for s in meta["sections"])
    assert meta["legacyRequired"] == ["C-01", "C-02", "C-05", "M4-01"]
    # 기억 더 채우기의 묶음마다 문항이 있다(빈 0 / 0 카드가 남지 않게)
    modules = [m["id"] for m in meta["moreModules"]]
    assert set(modules) == {q["section"] for q in meta["moreQuestions"]}
    assert len(modules) == len(set(modules))


def _as_v1(pid: str, answers: dict | None = None, step: str | None = None) -> None:
    """개편 전에 만든 대화 상대처럼: basics 의 질문 차례(intakeVersion)를 지운다."""

    async def go():
        async with SessionLocal() as db:
            persona = await db.get(Persona, pid)
            persona.basics = {k: v for k, v in persona.basics.items() if k != "intakeVersion"}
            if answers is not None:
                persona.answers = answers
            if step is not None:
                persona.setup_step = step
            await db.commit()

    asyncio.run(go())


# 개편 전 만든 대화 상대가 가진 답(예전 필수 넷 + 몇 개)
V1_ANSWERS = {
    "C-01": "한두 문장으로 짧게",
    "C-02": "말수가 적고, 말보다 행동(끼니·안부 챙김)으로 표현한다",
    "C-05": "가족을 위해 희생하며 사랑으로 살아오신 분",
    "M4-01": ["다정함", "걱정이 많음"],
    "C-09": ["몸조심해라"],
    "M7-01": "수능 날 아침 찹쌀떡을 쪄주셨다",
}


def test_ready_persona_saves_without_new_questions(fake_ai: FakeAI):  # noqa: F811
    """개편 전(v1) 대화 상대는 예전 필수 넷만 받는다. 새 필수 답 없이도 그대로 저장·반영된다.
    옮긴 문항 답은 같은 칸으로 간다."""
    with TestClient(app) as client:
        h = _signup(client, "old-answers@example.com")
        pid = _ready_persona(client, h)
        _as_v1(pid, dict(V1_ANSWERS))
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        assert "intakeVersion" not in detail["basics"]
        assert detail["requiredQuestions"] == ["C-01", "C-02", "C-05", "M4-01"]
        # 예전 대화 상대에는 미리 채우는 답이 없다
        assert detail["answers"] == V1_ANSWERS
        put = lambda answers: client.put(  # noqa: E731
            f"{API}/personas/{pid}", headers=h, json={"basics": BASICS, "answers": answers}
        )
        answers = {**detail["answers"], "M3-01": "우리 강아지니?"}
        saved = put(answers)
        assert saved.status_code == 200, saved.text
        _wait(client, h, pid, "UPDATE")
        # 저장해도 예전 대화 상대로 남는다(질문 차례를 새로 적지 않는다)
        kept = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        assert "intakeVersion" not in kept["basics"] and "M9-01-death" not in kept["answers"]
        # 예전 필수(C-05)는 여전히 꼭 받는다
        missing = put({k: v for k, v in answers.items() if k != "C-05"})
        assert missing.status_code == 400 and "한 문장" in missing.json()["detail"]

        answers |= {
            "M1-02": "전남 순천",
            "C-04": ["무조건 내 편을 들어준다"],
            "M8-08": "글쎄다, 나는 모르겠다",
        }
        saved = put(answers)
        assert saved.status_code == 200, saved.text
        _wait(client, h, pid, "UPDATE")
        ic = fake_ai.calls["/v1/persona/assembly"][-1]["intakeContext"]
        assert ic["personality"]["adviceStyle"] == ["무조건 내 편을 들어준다"]
        # 그분다운 '모르겠다'는 공통 규칙의 고정 예보다 앞서게 상황 이름에 적어 보낸다
        assert {
            "situation": "모르는 것을 물으면 (모른다고 할 때는 늘 이 말투로)",
            "response": "글쎄다, 나는 모르겠다",
        } in ic["situationalReactions"]
        assert any("고향: 전남 순천" in t for t in _memory_texts(pid))


def test_v1_persona_builds_with_old_answers_and_v2_is_enforced(
    fake_ai: FakeAI,  # noqa: F811
    monkeypatch,
):
    """만들기(build)도 대화 상대의 질문 차례로 필수를 본다: 새로 만든 가족·고인은 새 필수가 다
    있어야 하고, 예전 대화 상대는 예전 넷이면 된다."""
    from app.services import persona_builder

    async def no_build(db, job, progress):
        return None

    monkeypatch.setattr(persona_builder, "run_build", no_build)
    with TestClient(app) as client:
        h = _signup(client, "v1-build@example.com")
        created = client.post(f"{API}/personas", headers=h, json=BASICS)
        pid = created.json()["data"]["id"]
        # 돌아가신 분은 만들기 전에 신청 자격과 유족 명단을 적는다(2026-10-22 법률 자문)
        declare_bereaved(None, pid)
        _as_v1(pid, step="QUESTIONS")
        # 개편 전 대화 상대: 예전 넷만 있어도 만든다
        built = client.post(f"{API}/personas/{pid}/build", headers=h, json={"answers": V1_ANSWERS})
        assert built.status_code == 200, built.text

        created = client.post(f"{API}/personas", headers=h, json=BASICS)
        pid = created.json()["data"]["id"]
        declare_bereaved(None, pid)

        async def to_questions():
            async with SessionLocal() as db:
                persona = await db.get(Persona, pid)
                persona.setup_step = "QUESTIONS"
                await db.commit()

        asyncio.run(to_questions())
        # 개편 뒤 대화 상대: 예전 넷만으로는 안 된다(첫 빈 필수 질문을 알려 준다)
        refused = client.post(
            f"{API}/personas/{pid}/build", headers=h, json={"answers": V1_ANSWERS}
        )
        assert refused.status_code == 400
        assert "나에게는 반말이셨어요" in refused.json()["detail"]
        built = client.post(f"{API}/personas/{pid}/build", headers=h, json={"answers": ANSWERS})
        assert built.status_code == 200, built.text


def _memory_texts(pid: str) -> list[str]:
    async def go():
        async with SessionLocal() as db:
            persona = await db.get(Persona, pid)
            rows = await db.scalars(
                select(Memory).where(
                    Memory.person_id == persona.person_id, Memory.status == "ACTIVE"
                )
            )
            return [m.body_markdown for m in rows]

    return asyncio.run(go())


def _memory_titles(pid: str) -> list[str]:
    async def go():
        async with SessionLocal() as db:
            persona = await db.get(Persona, pid)
            rows = await db.scalars(select(Memory).where(Memory.person_id == persona.person_id))
            return [m.title for m in rows]

    return asyncio.run(go())


def test_asked_only_memories_and_last_days(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """'물으면만' 기억은 표시를 붙이고 성격 정리에 넣지 않는다. 돌아가신 때·마지막 무렵은
    기억으로 만들지 않는다(사후 페르소나는 그 이야기를 입에 올리지 않는다).

    성격 정리는 운영(layered)에서 꺼 두지만, 켰을 때 넣는 기억을 본다(PERSONA_REFLECTION)."""
    monkeypatch.setattr(get_settings(), "persona_reflection", True)
    with TestClient(app) as client:
        h = _signup(client, "asked-only@example.com")
        pid = _ready_persona(client, h)
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        answers = {
            **detail["answers"],
            "M1-09": "불교",
            "M5-07A": "사업이 어려웠던 해에 밤마다 일하셨다.",
            "T1-01": "2024년 3월",
            "T1-02": "말하고 싶지 않음",
            "T1-03": "큰딸이 곁에 있었고 막내는 외국에 있었다.",
            "T1-04": "병원에서 마지막으로 밥은 먹었냐고 물으셨다.",
            "M4-04": ["부지런해야 먹고산다"],
        }
        _agree(client, h, pid, "sensitive_religion")
        _agree(client, h, pid, "sensitive_health")
        saved = client.put(
            f"{API}/personas/{pid}", headers=h, json={"basics": BASICS, "answers": answers}
        )
        assert saved.status_code == 200, saved.text
        _wait(client, h, pid, "UPDATE")

        titles = _memory_titles(pid)
        assert "종교 (물으면만)" in titles and "힘들었던 시기 (물으면만)" in titles
        assert "마지막 시기에 곁에 있던 가족 (물으면만)" in titles
        assert not any(t.startswith(("돌아가신 때", "마지막 무렵")) for t in titles)
        # 성격 정리(늘 지침에 들어간다)에는 '물으면만' 기억을 넣지 않는다
        reflected = [
            m["memoryText"] for m in fake_ai.calls["/v1/persona/reflection"][-1]["memories"]
        ]
        assert not any("불교" in t or "사업이 어려웠던" in t or "큰딸" in t for t in reflected)
        assembly = fake_ai.calls["/v1/persona/assembly"][-1]
        # '말하고 싶지 않음'이면 그 이야기는 아예 꺼내지 않는다
        taboo = {(t["topic"], t["grade"]) for t in assembly["intakeContext"]["tabooTopics"]}
        assert ("돌아가신 일과 마지막 무렵", "금지") in taboo
        # 일·성실함에 대해 자주 하시던 말(M4-04)도 말투 예시로 들어간다
        assert "부지런해야 먹고산다" in assembly["speechExamples"]


def test_failed_update_is_retried_on_same_answers(fake_ai: FakeAI, monkeypatch):  # noqa: F811
    """반영(UPDATE)이 실패하면, 같은 답으로 다시 저장해도 다시 반영한다."""
    from app.services import ai as ai_module

    with TestClient(app) as client:
        h = _signup(client, "update-retry@example.com")
        pid = _ready_persona(client, h)
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        answers = {**detail["answers"], "M3-01": "우리 강아지니?"}
        real = ai_module.AIService.assemble

        async def broken(self, **kw):
            raise ai_module.AIServerError(503)

        monkeypatch.setattr(ai_module.AIService, "assemble", broken)
        body = {"basics": BASICS, "answers": answers}
        assert client.put(f"{API}/personas/{pid}", headers=h, json=body).json()["data"]["updating"]
        _wait_end(client, h, pid, "UPDATE")
        jobs = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["jobs"]
        assert jobs["UPDATE"]["status"] == "FAILED"

        monkeypatch.setattr(ai_module.AIService, "assemble", real)
        again = client.put(f"{API}/personas/{pid}", headers=h, json=body)
        assert again.status_code == 200 and again.json()["data"]["updating"] is True
        _wait(client, h, pid, "UPDATE")
        ic = fake_ai.calls["/v1/persona/assembly"][-1]["intakeContext"]
        assert ic["speechStyle"]["callOpen"] == "우리 강아지니?"
        # 반영이 끝난 뒤 같은 답이면 다시 만들지 않는다
        same = client.put(f"{API}/personas/{pid}", headers=h, json=body)
        assert same.json()["data"]["updating"] is False


def _wait_end(client: TestClient, h: dict, pid: str, kind: str, timeout: float = 20) -> None:
    end = time.time() + timeout
    while time.time() < end:
        job = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["jobs"].get(kind)
        if job and job["status"] != "RUNNING":
            return
        time.sleep(0.1)
    raise AssertionError(f"{kind} did not finish")
