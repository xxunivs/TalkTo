"""2026-10-21 만들 때 질문 개편: 필수 질문(개편 전·후·나 자신)과 '기억이 안 나요' 표시 값."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_billing import _ready_persona
from test_persona_setup import ANSWERS, BASICS, FakeAI, _signup, _wait, fake_ai  # noqa: F401

from app.db.models import Memory, Persona
from app.db.session import SessionLocal
from app.main import app
from app.services import intake, persona_builder, sharing

API = "/api/v1"

V2 = intake.with_version(intake.validate_basics(BASICS))
SELF = intake.validate_basics({"name": "한바다", "relation": "나 자신"})
LEGACY = {
    "C-01": "한두 문장으로 짧게",
    "C-02": "결론이나 답을 바로 말한다",
    "C-05": "웃음이 많은 분",
    "M4-01": ["다정함"],
}
# AI·프롬프트·기억으로 가면 안 되는 말(표시 값과 그 단추 글)
LEAKS = ("__unknown__", "__none__", "기억이 안 나요", "따로 없었어요")
# '기억이 안 나요'·'따로 없었어요'·'모름'을 고른 답
SKIPPED = {
    "M2-01-self": intake.NONE,
    "M3-01": intake.UNKNOWN,
    "M3-02": intake.UNKNOWN,
    "P1-05": intake.UNKNOWN,
    "M1-01": intake.UNKNOWN,
    "M1-02": intake.UNKNOWN,
    "M1-07": intake.UNKNOWN,
}


def _no_leak(payload) -> None:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    for word in LEAKS:
        assert word not in text, word


# ── 필수 질문 ──


def test_new_family_persona_gets_v2_and_self_does_not():
    assert V2["intakeVersion"] == intake.INTAKE_VERSION and intake.is_v2(V2)
    assert "intakeVersion" not in intake.with_version(SELF)
    # 나 자신은 질문 차례가 적혀 있어도 늘 예전 필수
    assert not intake.is_v2({**SELF, "intakeVersion": 2})


def test_v2_required_answers_are_enforced():
    full = intake.validate_answers(ANSWERS, final=True, basics=V2)
    # 임종 등급은 서버가 미리 채운다(확인만)
    assert full["M9-01-death"] == "물으면만"
    for qid in ("M2-01-level", "M3-01", "C-09", "C-07", "C-03", "M6-08", "M7-01"):
        with pytest.raises(intake.IntakeError):
            intake.validate_answers(
                {k: v for k, v in ANSWERS.items() if k != qid}, final=True, basics=V2
            )
    # 예전 필수 넷만으로는 안 된다. 빠진 첫 질문(PART 0 순서)을 그분 상태 문구로 알린다
    with pytest.raises(intake.IntakeError, match="나에게는 반말이셨어요"):
        intake.validate_answers(LEGACY, final=True, basics=V2)
    living = {**V2, "status": "alive"}
    with pytest.raises(intake.IntakeError, match="나에게는 반말이세요"):
        intake.validate_answers(LEGACY, final=True, basics=living)
    # C-05 는 개편 뒤 선택
    without = {k: v for k, v in ANSWERS.items() if k != "C-05"}
    assert "C-05" not in intake.validate_answers(without, final=True, basics=V2)
    # 저장(final=False)은 빠진 답이 있어도 된다
    assert intake.validate_answers({}, final=False, basics=V2) == {"M9-01-death": "물으면만"}


def test_called_me_question_is_satisfied_by_basics():
    no_name = {**V2, "calledMe": None}
    assert "P1-07A" in intake.required_ids(no_name)
    assert "P1-07A" not in intake.required_ids(V2)
    with pytest.raises(intake.IntakeError, match="나를 뭐라고 부르셨어요"):
        intake.validate_answers(ANSWERS, final=True, basics=no_name)
    answers = intake.validate_answers(ANSWERS | {"P1-07A": "길동아"}, final=True, basics=no_name)
    # 기본 정보에 없으면 그 답이 '나를 부르는 말'이 된다
    payload = intake.build_intake(no_name, answers)
    me = payload["intakeContext"]["familyMap"][0]
    assert me["addressTermTo"] == "길동아"
    assert payload["subjectContext"]["familyMembers"][0]["addressTerms"] == ["길동아"]
    # 기본 정보가 있으면 그것이 먼저다
    assert intake.called_me(V2, {"P1-07A": "다른 말"}) == "길동아"


def test_self_and_v1_keep_the_old_required_set():
    for basics in (SELF, {**SELF, "intakeVersion": 2}, intake.validate_basics(BASICS), None):
        assert intake.required_ids(basics) == ["C-01", "C-02", "C-05", "M4-01"]
        assert intake.validate_answers(LEGACY, final=True, basics=basics)
        with pytest.raises(intake.IntakeError):
            intake.validate_answers(
                {k: v for k, v in LEGACY.items() if k != "C-05"}, final=True, basics=basics
            )
        # 미리 채우는 답도 없다(예전과 같게)
        assert intake.answer_defaults(basics) == {}


def test_m9_defaults_count_as_answered_and_choice_wins():
    assert intake.answer_defaults(V2) == {"M9-01-death": "물으면만"}
    # 살아 계신 분은 임종 줄을 묻지 않는다
    assert intake.answer_defaults({**V2, "status": "alive"}) == {}
    chosen = intake.validate_answers(ANSWERS | {"M9-01-death": "금지"}, final=True, basics=V2)
    assert chosen["M9-01-death"] == "금지"

    # 등급은 AI 로 간다. 다만 살아 계신 분으로 바꾼 뒤 남은 임종 줄은 보내지 않는다
    def death(basics):
        taboo = intake.build_intake(basics, chosen)["intakeContext"]["tabooTopics"]
        return [t["grade"] for t in taboo if t["topic"] == "그분의 사망·장례·임종"]

    assert death(V2) == ["금지"]
    assert death({**V2, "status": "alive"}) == []


# ── '기억이 안 나요'·'따로 없었어요'·'모름' ──


def test_skip_values_count_as_answered_only_where_offered():
    answers = intake.validate_answers(ANSWERS | SKIPPED, final=True, basics=V2)
    for qid, value in SKIPPED.items():
        assert answers[qid] == value
    # 단추가 없는 문항에 온 표시 값은 빈 답이다(필수면 빠진 것으로 본다)
    with pytest.raises(intake.IntakeError):
        intake.validate_answers(ANSWERS | {"M6-08": intake.UNKNOWN}, final=True, basics=V2)
    dropped = intake.validate_answers(
        {"C-09": [intake.UNKNOWN, "밥은 먹었냐"], "M2-01-self": intake.UNKNOWN}, final=False
    )
    assert dropped["C-09"] == ["밥은 먹었냐"] and "M2-01-self" not in dropped


def test_skip_values_never_reach_the_ai():
    answers = intake.validate_answers(
        ANSWERS
        | SKIPPED
        | {"M2-01": [{"name": "홍철수", "relation": "아들", "calledBy": intake.UNKNOWN}]},
        final=True,
        basics=V2,
    )
    payload = intake.build_intake(V2, answers)
    _no_leak(payload)
    me = payload["intakeContext"]["familyMap"][0]
    assert "selfReference" not in me
    style = payload["intakeContext"]["speechStyle"]
    assert style["callOpen"] is None and style["callClose"] is None
    assert payload["intakeContext"]["basicProfile"]["period"] is None
    _no_leak(intake.build_intake_memories(V2, answers))
    _no_leak(intake.subject_context(V2, answers))
    _no_leak(intake.family_rows(V2, answers))
    _no_leak(persona_builder.build_prompt_material(V2, answers, [], set()))
    # 고향·하신 일 '모름'은 기억이 되지 않는다
    tags = {row["tags"][0] for row in intake.build_intake_memories(V2, answers)}
    assert "고향" not in tags and "어떤 일을 하며 살아왔나" not in tags


def test_skip_values_never_reach_the_member_prompt(monkeypatch):
    """함께 쓰는 가족의 조립본(주인 줄 포함)에도 표시 값이 가지 않는다."""
    sent: dict = {}

    async def capture(self, **body):
        sent.update(body)
        return "지침"

    monkeypatch.setattr(sharing.ai_service.__class__, "assemble", capture)
    answers = intake.validate_answers(ANSWERS | SKIPPED, final=True, basics=V2)
    persona = Persona(id="p-skip", basics={**V2, "calledMe": None}, answers=answers)
    persona.subject_voices = []
    profile = {"myName": "철수", "myRelation": "아들", "addressTerm": "엄마", "calledMe": "철수야"}

    async def go():
        async with SessionLocal() as db:
            return await sharing.assemble_for_member(db, persona, profile)

    with TestClient(app):  # 앱이 뜰 때 표를 만든다(이 시험만 따로 돌려도 되게)
        asyncio.run(go())
    _no_leak(sent)
    owner = sent["intakeContext"]["familyMap"][1]
    assert owner.get("isOwner") and "selfReference" not in owner


def test_m6_08_becomes_one_memory():
    rows = intake.build_intake_memories(
        V2, {"M6-08": "된장찌개", "M6-08-why": "갈 때마다 끓여 주셨어요"}
    )
    assert [r["memoryText"] for r in rows] == [
        "가족이 나를 떠올리면 생각나는 것: 된장찌개 — 갈 때마다 끓여 주셨어요"
    ]
    assert rows[0]["sourceQuestion"] == "M6-08" and rows[0]["usageScope"] == "자유"
    # 까닭은 선택. 까닭만 있으면 기억이 아니다
    assert intake.build_intake_memories(V2, {"M6-08": "라디오"})[0]["memoryText"].endswith(
        ": 라디오"
    )
    assert intake.build_intake_memories(V2, {"M6-08-why": "아침마다"}) == []
    # 함께한 장면의 둘째부터(기억 더 채우기 M7-01-2)도 기억 카드다
    cards = intake.build_intake_memories(V2, {"M7-01": "장면 하나", "M7-01-2": "장면 둘"})
    assert [r["tags"] for r in cards] == [["기억 카드"], ["기억 카드"]]


# ── API ──


def _memory_texts(pid: str) -> list[str]:
    async def go():
        async with SessionLocal() as db:
            persona = await db.get(Persona, pid)
            rows = await db.scalars(select(Memory).where(Memory.person_id == persona.person_id))
            return [m.body_markdown for m in rows]

    return asyncio.run(go())


def test_skip_values_are_saved_but_never_sent(fake_ai: FakeAI):  # noqa: F811
    """'기억이 안 나요'를 고른 답은 저장·필수로 세지만, 다시 만들 때 AI 로 가는 어떤 요청에도,
    기억에도 들어가지 않는다."""
    with TestClient(app) as client:
        h = _signup(client, "skip-values@example.com")
        pid = _ready_persona(client, h)
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        assert detail["basics"]["intakeVersion"] == 2
        assert "M9-01-death" in detail["answers"]  # 미리 채운 등급이 저장돼 있다
        saved = client.put(
            f"{API}/personas/{pid}",
            headers=h,
            json={"basics": BASICS, "answers": {**detail["answers"], **SKIPPED}},
        )
        assert saved.status_code == 200, saved.text
        _wait(client, h, pid, "UPDATE")
        kept = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["answers"]
        assert kept["M3-01"] == intake.UNKNOWN and kept["M2-01-self"] == intake.NONE
        _no_leak(fake_ai.calls)
        _no_leak(_memory_texts(pid))


def test_ready_v2_persona_needs_called_me_somewhere(fake_ai: FakeAI):  # noqa: F811
    """다 만든 뒤 기본 정보만 고쳐도 개편 뒤 필수(부르던 말)는 비울 수 없다."""
    with TestClient(app) as client:
        h = _signup(client, "called-me@example.com")
        pid = _ready_persona(client, h)
        cleared = client.put(
            f"{API}/personas/{pid}", headers=h, json={"basics": BASICS | {"calledMe": ""}}
        )
        assert cleared.status_code == 400
        assert "나를 뭐라고 부르셨어요" in cleared.json()["detail"]
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        answered = client.put(
            f"{API}/personas/{pid}",
            headers=h,
            json={
                "basics": BASICS | {"calledMe": ""},
                "answers": {**detail["answers"], "P1-07A": "길동아"},
            },
        )
        assert answered.status_code == 200, answered.text
        _wait(client, h, pid, "UPDATE")
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        assert detail["basics"]["intakeVersion"] == 2 and not detail["basics"]["calledMe"]
        assert "P1-07A" in detail["requiredQuestions"]
        me = fake_ai.calls["/v1/persona/assembly"][-1]["intakeContext"]["familyMap"][0]
        assert me["addressTermTo"] == "길동아"


def test_self_persona_detail_lists_the_old_required_set():
    with TestClient(app) as client:
        h = _signup(client, "self-required@example.com")
        me = {"name": "한바다", "relation": "나 자신", "status": "alive", "myName": "한바다"}
        created = client.post(f"{API}/personas", headers=h, json=me)
        assert created.status_code == 201, created.text
        detail = client.get(f"{API}/personas/{created.json()['data']['id']}", headers=h).json()
        assert "intakeVersion" not in detail["data"]["basics"]
        assert detail["data"]["requiredQuestions"] == ["C-01", "C-02", "C-05", "M4-01"]
        assert detail["data"]["answers"] == {}
        # 상세의 questions: 나 자신은 예전 필수 넷만, 미리 채울 답 없음
        data = detail["data"]
        questions = data["questions"]
        assert {q["id"] for q in questions if q["required"]} == {"C-01", "C-02", "C-05", "M4-01"}
        assert not any("default" in q for q in questions)
        # 묶음·기억 더 채우기도 개편 전 그대로(나 자신은 이번 개편 대상이 아니다)
        assert questions == intake.questions_for(data["basics"])
        assert data["sections"] == intake.SELF_QUESTION_SECTIONS
        assert data["moreQuestions"] == intake.more_questions_for(data["basics"])
        assert data["moreModules"] == intake.SELF_MORE_MODULES
        assert not {q["id"] for q in questions} & {q["id"] for q in data["moreQuestions"]}


def test_questions_for_fits_required_title_and_defaults():
    """상세에 싣는 질문(intake.questions_for): 화면은 이것으로 필수·문구·미리 채울 답을 그린다."""
    v2 = {q["id"]: q for q in intake.questions_for(V2)}
    # 개편 뒤 가족·고인: QUESTIONS 의 필수. 기본 정보가 대신한 P1-07A 도 필수로 둔다
    # (정보 고치기에서 부르던 말을 비우면 다시 묻는다)
    assert [i for i, q in v2.items() if q["required"]] == [
        q["id"] for q in intake.QUESTIONS if q.get("required")
    ]
    assert v2["P1-07A"]["required"] and not v2["C-05"]["required"]
    assert v2["M9-01-death"]["default"] == "물으면만"
    assert [i for i, q in v2.items() if "default" in q] == ["M9-01-death"]
    # 살아 계신 분은 현재형 문구, 미리 채울 등급은 그대로 싣는다(상태를 바꾸면 화면이 채운다)
    alive = {q["id"]: q for q in intake.questions_for({**V2, "status": "alive"})}
    for qid, q in alive.items():
        assert q["title"] == intake.question_title(intake._BY_ID[qid], {**V2, "status": "alive"})
    assert any(q["title"] != v2[qid]["title"] for qid, q in alive.items())
    # 예전 대화 상대: 예전 필수 넷, 미리 채울 답 없음
    v1 = intake.questions_for(intake.validate_basics(BASICS))
    assert {q["id"] for q in v1 if q["required"]} == set(intake.LEGACY_REQUIRED)
    assert not any("default" in q for q in v1)
    # QUESTIONS 자체는 바뀌지 않는다
    assert "default" not in intake._BY_ID["M9-01-death"]


def test_v2_detail_carries_questions_for_that_persona(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "detail-questions@example.com")
        pid = _ready_persona(client, h)
        data = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        assert data["questions"] == intake.questions_for(data["basics"])
        required = [q["id"] for q in data["questions"] if q["required"]]
        # 기본 정보가 대신한 P1-07A 만 requiredQuestions 에서 빠진다
        assert [i for i in required if i != "P1-07A"] == [
            i for i in data["requiredQuestions"] if i != "P1-07A"
        ]
        assert data["gapWaiting"] == 0
        # 기억 더 채우기도 이 대화 상대 것으로(만들 때 질문과 겹치지 않는다)
        assert data["moreQuestions"] == intake.more_questions_for(data["basics"])
        assert data["moreModules"] == intake.MORE_MODULES
        assert not {q["id"] for q in data["questions"]} & {q["id"] for q in data["moreQuestions"]}


def test_m6_08_is_optional_for_v1_and_not_asked_of_self():
    """그분 하면 떠오르는 것(M6-08)은 개편 뒤 가족·고인만 꼭 받는다. 예전 대화 상대는 선택이고,
    나 자신에게는 묻지 않는다(개편 때 새로 생긴 문항 — 나 자신은 개편 대상이 아니다)."""
    v1 = intake.validate_basics(BASICS)
    for basics in (SELF, v1):
        assert "M6-08" not in intake.required_ids(basics)
        assert "M6-08" not in intake.validate_answers(LEGACY, final=True, basics=basics)
    fitted = {q["id"]: q for q in intake.questions_for(v1)}
    assert fitted["M6-08"]["required"] is False and fitted["M6-08-why"]["required"] is False
    assert intake.asks(fitted["M6-08"], v1)  # 예전 대화 상대에게는 묻기는 한다
    shown = [q["id"] for q in intake.questions_for(SELF) + intake.more_questions_for(SELF)]
    assert not {"M6-08", "M6-08-why", "M7-01-2"} & set(shown)
    family = {q["id"]: q for q in intake.questions_for(V2)}
    assert family["M6-08"]["required"] is True
    assert family["M6-08"]["title"] == "그분 하면 떠오르는 것 하나를 알려 주세요."
    assert family["M6-08-why"]["placeholder"] == intake._BY_ID["M6-08-why"]["placeholder"]
    # 개편 직후 나 자신에 저장된 답이 있어도 그대로 기억 하나가 된다(저장한 답은 버리지 않는다)
    rows = intake.build_intake_memories(SELF, {"M6-08": "라디오", "M6-08-why": "늘 켜 두었어요"})
    assert [r["memoryText"] for r in rows] == [
        "가족이 나를 떠올리면 생각나는 것: 라디오 — 늘 켜 두었어요"
    ]


def test_more_questions_for_fits_wording_and_never_overlaps():
    """상세에 싣는 기억 더 채우기 질문: 모두 선택, 만들 때 질문과 id 가 겹치지 않고(개편 전·후 모두
    같은 답이 두 화면에 나오지 않는다), 문구는 그분 상태에 맞춘다."""
    created = {q["id"] for q in intake.QUESTIONS}
    for basics in (V2, intake.validate_basics(BASICS), {**V2, "status": "alive"}):
        more = intake.more_questions_for(basics)
        assert [q["id"] for q in more] == [q["id"] for q in intake.MORE_QUESTIONS]
        assert not created & {q["id"] for q in more}
        assert not any(q.get("required") for q in more)
    # 나 자신은 개편 전 목록(만들 때 질문의 나머지 — test_self_path_is_unchanged)
    me = intake.more_questions_for(SELF)
    assert not {q["id"] for q in intake.questions_for(SELF)} & {q["id"] for q in me}
    assert not any(q.get("required") for q in me)
    # 만들 때에서 옮긴 질문(P1-05·M1-02·M1-07·M3-05·M3-07)은 기억 더 채우기에만 있다
    moved = {"P1-05", "M1-02", "M1-07", "M3-05", "M3-07"}
    assert moved <= {q["id"] for q in intake.MORE_QUESTIONS} and not moved & created
    home = {q["id"]: q for q in intake.more_questions_for({**V2, "status": "alive"})}["M1-02"]
    assert home["title"] == "고향은 어디세요?"
    gone = {q["id"]: q for q in intake.more_questions_for(V2)}["M1-02"]
    assert gone["title"] == "고향은 어디셨어요?"


def test_self_keeps_pre_overhaul_wording():
    """나 자신은 이번 개편 대상이 아니다: 살아 계셔도 현재형(titleLiving)으로 바꾸지 않고 개편 전
    문구(titleSelf, 없으면 title) 그대로 묻는다. 만들 때 질문·기억 더 채우기 모두."""
    me = {**SELF, "status": "alive"}
    for q in intake.questions_for(me) + intake.more_questions_for(me):
        source = intake._BY_ID[q["id"]]
        assert q["title"] == (source.get("titleSelf") or source["title"]), q["id"]
    fitted = {q["id"]: q for q in intake.questions_for(me)}
    assert fitted["C-01"]["title"] == "그분은 한 번 말씀하실 때 보통 얼마나 길게 하셨어요?"
    assert fitted["C-03"]["title"] == "상대가 힘들어하면 그분은 어떻게 하셨어요?"


# 개편 전(2026-10-17) 나 자신이 보던 질문 차례: (묶음, 문항). 묻지 않는 문항(when)은 뺐다.
SELF_CREATE_BEFORE = [
    ("가족", ["M2-01", "M2-01-tone"]),
    ("그분에 대해", ["P1-05", "M1-02", "M1-07"]),
    ("말투", ["C-01", "C-02", "C-09", "M3-07", "M8-11", "M3-05"]),
    ("성격", ["C-05", "M4-01", "C-03", "C-04", "C-07", "C-06"]),
    ("이럴 땐", ["M8-01", "M8-02", "M8-03", "M8-08"]),
    (
        "조심할 이야기",
        [
            "C-10",
            "M9-01-health",
            "M9-01-inherit",
            "M9-01-conflict",
            "M9-01-politics",
            "M9-01-religion",
            "M9-01-alcohol",
            "M9-01-regret",
            "M9-03",
        ],
    ),
    ("함께한 기억", ["M7-01"]),
]
SELF_MORE_BEFORE = [
    ("기본 정보", ["M1-01", "M1-03", "M1-05", "M1-06", "M1-08", "M1-09", "M1-10"]),
    ("말투·표현", ["M3-01", "M3-02", "M3-03", "M3-04", "M3-06"]),
    ("성격·가치관", ["C-08", "M4-02", "M4-03", "M4-04", "M4-05", "M4-06", "M4-07"]),
    (
        "인생 이야기",
        ["M5-01", "M5-02", "M5-03", "M5-04", "M5-05", "M5-06", "M5-07A", "M5-08", "M5-09"],
    ),
    ("일상 속 기억", ["M6-01", "M6-02", "M6-03", "M6-04", "M6-05", "M6-07", "M2-02", "M2-03"]),
    ("이럴 땐", ["M8-04", "M8-05", "M8-06", "M8-07", "M8-09", "M8-10"]),
]


def _shown(questions: list[dict], basics: dict) -> list[tuple[str, list[str]]]:
    """화면처럼 묶음마다(처음 나온 차례) 묻는 문항만."""
    groups: dict[str, list[str]] = {}
    for q in questions:
        if intake.asks(q, basics):
            groups.setdefault(q["section"], []).append(q["id"])
    return list(groups.items())


def test_self_path_is_unchanged():
    """나 자신은 이번 개편 대상이 아니다(대표 지시 1): 만들 때 질문·기억 더 채우기의 문항·묶음·
    차례·묶음 안내가 개편 전과 같다. 첫마디·마지막 말·마음을 표현했던 장면은 기억 더 채우기,
    언제 무렵·고향·하신 일·사투리·말끝은 만들 때 묻고, 새 문항(M6-08·M7-01-2)은 없다."""
    me = {**SELF, "status": "alive"}
    assert _shown(intake.questions_for(me), me) == SELF_CREATE_BEFORE
    assert _shown(intake.more_questions_for(me), me) == SELF_MORE_BEFORE
    # 돌아가신 분에게만 묻는 마지막 시기(T1)는 목록에 있지만 나 자신(살아 계심)에게는 묻지 않는다
    last = [q for q in intake.more_questions_for(me) if q["section"] == "마지막 시기"]
    assert last and all(q.get("when") == "deceased" for q in last)
    assert [s["id"] for s in intake.sections_for(me)] == [
        "가족",
        "그분에 대해",
        "말투",
        "성격",
        "이럴 땐",
        "조심할 이야기",
        "함께한 기억",
    ]
    assert intake.sections_for(V2) == intake.QUESTION_SECTIONS
    leads = {m["id"]: m["lead"] for m in intake.more_modules_for(me)}
    assert leads["말투·표현"] == "전화 받을 때 첫마디, 끊을 때 마지막 말, 화나셨을 때 말투"
    assert intake.more_modules_for(V2) == intake.MORE_MODULES
    # 개편 때 생긴 '기억이 안 나요'·'모름' 단추가 없고, 보기 글·안내도 개편 전 것
    every = intake.questions_for(me) + intake.more_questions_for(me)
    assert not any("skipOption" in q for q in every)
    fitted = {q["id"]: q for q in every}
    assert fitted["M3-01"]["placeholder"] == "예: 우리 강아지니?"
    assert fitted["M3-01"]["title"] == "전화를 받으면 첫마디를 뭐라고 하셨나요?"
    assert fitted["C-08"]["title"] == "사랑을 표현하셨던 장면 하나를 들려주세요."
    assert fitted["C-09"]["hint"] == "하나씩 적어 주세요."
    assert fitted["M7-01"]["hint"].startswith("안 적어도 괜찮아요.")
    assert not fitted["M3-01"]["required"] and not fitted["P1-05"]["required"]
    # 가족·고인 쪽 정의는 그대로다(나 자신 모양은 상세에 싣는 사본에만)
    assert intake._BY_ID["M3-01"]["section"] == "그분이 하시던 말"
    assert intake._BY_ID["P1-05"]["skipOption"] == intake.SKIP_DONT_KNOW
    # 저장(나 자신 필수 넷)과 예전에 저장한 답은 그대로 받는다
    kept = intake.validate_answers(
        LEGACY | {"M3-01": "왔니", "P1-05": "칠순 무렵"}, final=True, basics=me
    )
    assert kept["M3-01"] == "왔니" and kept["P1-05"] == "칠순 무렵"


# ── 선택지·글로 고른 '모름' ──


def test_dont_know_choices_never_reach_the_ai():
    """기억 더 채우기의 '모름' 선택지(학교·종교·돈)와 글로 적은 '모름'은 저장은 그대로 하지만
    기억·프롬프트·AI 로는 빈 답이다('다닌 학교: 모름' 같은 기억이 되지 않게)."""
    dont_know = {
        "M1-06": "모름",
        "M1-09": "모름",
        "M4-03": "모름",
        "M1-01": " 모름 ",
        "M1-05": ["모름", "순천 · 1942~1965"],
        "M3-05": "약간(고향 억양)",
        "M3-05:detail": "모름",
        "M2-01": [{"name": "홍철수", "relation": "아들", "calledBy": "모름"}],
    }
    answers = intake.validate_answers(ANSWERS | dont_know, final=True, basics=V2)
    assert answers["M1-06"] == "모름" and answers["M4-03"] == "모름"  # 화면은 고른 것을 보인다
    rows = intake.build_intake_memories(V2, answers)
    assert not any("모름" in row["memoryText"] for row in rows)
    assert "살아온 곳: 순천 · 1942~1965" in [row["memoryText"] for row in rows]
    payload = intake.build_intake(V2, answers)
    assert "모름" not in json.dumps(payload, ensure_ascii=False)
    assert payload["intakeContext"]["speechStyle"]["dialect"] == "약간(고향 억양)"
    son = payload["intakeContext"]["familyMap"][1]
    assert son["name"] == "홍철수" and "addressTermTo" not in son
    assert "모름" not in json.dumps(intake.subject_context(V2, answers), ensure_ascii=False)
    material = persona_builder.build_prompt_material(V2, answers, [], set())
    assert "모름" not in json.dumps(material, ensure_ascii=False, default=str)
