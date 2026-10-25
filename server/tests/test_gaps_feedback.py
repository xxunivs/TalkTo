"""'더 알려 주실 기억'(gap questions)과 대화 고치기(feedback) — 2026-10-21 개편 §4·§5.

- 빈틈 질문은 대답 뒤 작업으로만 만든다(대답 응답에 싣지 않는다 — 대화 중에 띄우지
  않는다). 알림은 없다.
- 열린 질문은 대화 상대마다 10개까지. 만든 분의 답은 바로 기억(GAP)이 되고, 가족의
  답·고침은 만든 분이 확인해야 반영된다.
- 말투 고침은 다음 대답부터 styleExamples 로 간다(글 예시로만). 내용 고침은
  기억(FEEDBACK)이 되고 그 대답에 쓰인 기억에는 끌 후보 표시만 한다(지우지 않는다).

모든 자료는 지어낸 시험용이다. AI 서버는 가짜(httpx MockTransport)다.
"""

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_sharing import (
    API,
    OWNER_BASICS,
    _accept,
    _admin,
    _all,
    _count,
    _db,
    _family,
    _get,
    _link,
    _persona,
    _room,
    _say,
    _set,
    _signup,
)

from app.api.v1 import conversations
from app.core.config import get_settings
from app.db.models import (
    ConversationFeedback,
    Feedback,
    GapQuestion,
    Memory,
    Message,
    Persona,
    StyleExample,
)
from app.main import app
from app.services import ai as ai_module
from app.services import corrections, gaps, mailer, notify

QUESTION = "할머니께서 즐겨 부르시던 노래가 있었나요?"
PROFILE_B = {
    "myName": "영희",
    "myRelation": "딸",
    "addressTerm": "엄마",
    "calledMe": "영희야",
    "speechLevel": "반말",
    "selfReference": "엄마가",
    "callsOwner": "지은이",
}


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "payments_open", False)
    monkeypatch.setattr(s, "prelaunch_conversations", None)
    monkeypatch.setattr(s, "payments_test_emails", "")
    monkeypatch.setattr(s, "share_open", True)
    monkeypatch.setattr(s, "share_members_per_persona", 5)
    monkeypatch.setattr(s, "gap_questions_enabled", True)
    monkeypatch.setattr(s, "rate_limit_enabled", True)
    return s


class GapAI:
    """AI 서버 대역. 대답에 memoryGap 을 실을지, 빈틈 질문으로 무엇을 돌려줄지 시험이 정한다."""

    def __init__(self) -> None:
        self.calls: dict[str, list[dict]] = defaultdict(list)
        self.gap: dict | None = None
        self.question: dict = {"action": "new", "question": QUESTION, "topic": "즐겨 부른 노래"}
        self.query_vector = [1.0, 0.0, 0.0]
        self.content = "그래, 왔구나."

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content or b"{}")
        self.calls[path].append(body)
        if path == "/v1/persona/assembly":
            return httpx.Response(200, json={"instructions": "조립본"})
        if path == "/v1/persona/responses":
            return httpx.Response(
                200,
                json={
                    "content": self.content,
                    "retrievedMemoryIds": [m["id"] for m in body["memories"]],
                    **({"memoryGap": self.gap} if self.gap is not None else {}),
                    "provider": "fake",
                    "model": "fake",
                },
            )
        if path == "/v1/persona/gap-question":
            return httpx.Response(200, json={**self.question, "provider": "f", "model": "f"})
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
                            "embedding": [1.0, 0.0, 0.0],
                        }
                        for item in body["items"]
                    ]
                },
            )
        if path == "/v1/embeddings/query":
            return httpx.Response(
                200,
                json={
                    "provider": "f",
                    "model": "f",
                    "dimensions": 3,
                    "embedding": self.query_vector,
                },
            )
        return httpx.Response(404, json={"detail": "없음"})

    def replies(self) -> list[dict]:
        return self.calls["/v1/persona/responses"]

    def asked(self) -> list[dict]:
        return self.calls["/v1/persona/gap-question"]


@pytest.fixture
def ai(monkeypatch) -> GapAI:
    fake = GapAI()
    monkeypatch.setattr(get_settings(), "ai_server_url", "https://ai.example.test")
    transport = httpx.MockTransport(fake)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        ai_module.httpx, "AsyncClient", lambda *a, **kw: real_client(transport=transport, **kw)
    )
    return fake


def _owner_persona(client: TestClient, **kw) -> tuple[dict, str, str]:
    h, owner_id, _ = _signup(client, "김지은")
    return h, owner_id, _persona(owner_id, **kw)


def _add_memory(pid: str, owner_id: str, text: str = "홍길동은 남원에서 태어났다.") -> str:
    async def go(db):
        persona = await db.get(Persona, pid)
        memory = Memory(
            person_id=persona.person_id,
            created_by_user_id=owner_id,
            title="고향",
            body_markdown=text,
            source="INTAKE",
            embedding=[1.0, 0.0, 0.0],
            embedding_status="READY",
        )
        db.add(memory)
        await db.commit()
        return memory.id

    return _db(go)


def _questions(pid: str) -> list[GapQuestion]:
    return _all(select(GapQuestion).where(GapQuestion.persona_id == pid))


def _gap_list(client: TestClient, h: dict, pid: str) -> dict:
    response = client.get(f"{API}/personas/{pid}/gap-questions", headers=h)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _last_reply_id(client: TestClient, h: dict, cid: str) -> str:
    messages = client.get(f"{API}/conversations/{cid}", headers=h).json()["data"]["messages"]
    return [m for m in messages if m["role"] == "assistant"][-1]["id"]


def _seed_question(pid: str, **values) -> str:
    async def go(db):
        row = GapQuestion(
            persona_id=pid,
            topic=values.pop("topic", "고향 친구"),
            question=values.pop("question", "할머니께서 고향 친구 이야기를 하셨나요?"),
            signal=values.pop("signal", "model"),
            **values,
        )
        db.add(row)
        await db.commit()
        return row.id

    return _db(go)


# ────────────────────────── AI 서버 계약 ──────────────────────────


@pytest.mark.asyncio
async def test_reply_sends_flags_only_when_given_and_reads_memory_gap(monkeypatch):
    """flagGaps·styleExamples 는 줄 때만 싣는다(아니면 예전 요청과 같다).
    memoryGap 은 부탁했을 때만 읽는다."""
    bodies: list[dict] = []
    answers = [{"memoryGap": {"topic": "노래"}}, {"memoryGap": {"topic": "노래"}}, {}]

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        extra = answers.pop(0)
        return httpx.Response(
            200,
            json={
                "content": "그래.",
                "retrievedMemoryIds": [],
                "provider": "f",
                "model": "f",
                **extra,
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        ai_module,
        "get_settings",
        lambda: get_settings().model_copy(update={"ai_server_url": "https://ai.example.test"}),
    )
    monkeypatch.setattr(
        ai_module.httpx,
        "AsyncClient",
        lambda *a, **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    persona = {"subject_id": "s", "name": "n", "instructions": "지침"}
    style = [{"original": "그래, 밥은?", "corrected": "밥 먹었냐."}]
    flagged = await ai_module.ai_service.reply(
        message="노래?",
        history=[],
        memories=[],
        persona=persona,
        flag_gaps=True,
        style_examples=style,
    )
    plain = await ai_module.ai_service.reply(
        message="노래?", history=[], memories=[], persona=persona
    )
    no_gap = await ai_module.ai_service.reply(
        message="노래?", history=[], memories=[], persona=persona, flag_gaps=True
    )
    assert bodies[0]["flagGaps"] is True and bodies[0]["styleExamples"] == style
    assert "flagGaps" not in bodies[1] and "styleExamples" not in bodies[1]
    assert (flagged.memory_gap, plain.memory_gap, no_gap.memory_gap) == ("노래", None, None)


# ────────────────────────── 빈틈 신호와 대답 뒤 작업 ──────────────────────────


def test_model_gap_creates_a_question_only_after_the_reply(ai: GapAI):
    with TestClient(app) as client:
        h, _, pid = _owner_persona(client)
        cid = _room(client, h, pid)
        ai.gap = {"topic": "노래"}
        sent = _say(client, h, cid, "할머니 좋아하시던 노래 뭐였어요?")
        assert sent.status_code == 200, sent.text
        # 대답 응답에는 빈틈 질문이 없다(대화 중에 띄우지 않는다)
        assert "gap" not in json.dumps(sent.json(), ensure_ascii=False).lower()
        assert ai.replies()[-1]["flagGaps"] is True
        assert "styleExamples" not in ai.replies()[-1]
        # 대답 뒤 작업이 질문을 만들었다
        assert ai.asked()[-1] == {
            "candidate": {
                "userMessage": "할머니 좋아하시던 노래 뭐였어요?",
                "reply": "그래, 왔구나.",
                "topic": "노래",
                "signal": "model",
            },
            "openQuestions": [],
            "subjectLabel": "할머니",
            "subjectSelf": False,
            "deceased": True,
        }
        rows = _questions(pid)
        assert [(r.question, r.topic, r.signal, r.status) for r in rows] == [
            (QUESTION, "즐겨 부른 노래", "model", "open")
        ]
        assert rows[0].source_message_id == sent.json()["data"]["assistantMessage"]["id"]
        detail = client.get(f"{API}/personas/{pid}", headers=h).json()["data"]
        assert detail["gapCount"] == 1
        listed = _gap_list(client, h, pid)
        assert listed["role"] == "owner" and listed["openCount"] == 1
        assert listed["questions"][0]["question"] == QUESTION
        assert listed["canAskFamily"] is False

        # 같은 주제가 또 나오면 AI 서버를 부르지 않고 합친다
        ai.gap = {"topic": "즐겨 부른 노래"}
        asked = len(ai.asked())
        assert _say(client, h, cid, "그 노래 제목이 뭐였더라?").status_code == 200
        assert len(ai.asked()) == asked
        rows = _questions(pid)
        assert len(rows) == 1 and rows[0].hits == 2
        # AI 가 열린 질문과 같다고 하면 합친다
        ai.gap = {"topic": "가수"}
        ai.question = {"action": "merge", "mergeInto": rows[0].id}
        assert _say(client, h, cid, "할머니가 좋아하던 가수는요?").status_code == 200
        assert ai.asked()[-1]["openQuestions"][0]["id"] == rows[0].id
        rows = _questions(pid)
        assert len(rows) == 1 and rows[0].hits == 3


def test_minor_guard_turn_makes_no_gap_question(ai: GapAI):
    """아이의 대화 상대에서 AI 안전 검사가 막거나 바꾼 턴(대체 대답)은 그 말을 '더 알려 주실 기억'
    질문으로 옮겨 해외 회사로 다시 보내지 않는다(AI pipeline/safety/minor_guard.py FALLBACK)."""
    child = {
        **OWNER_BASICS,
        "name": "김하늘",
        "relation": "아들",
        "addressTerm": "하늘이",
        "status": "alive",
        "myRelation": "어머니",
        "minor": True,
        "birthYm": "2012-01",
        "guardianBasis": "both_agreed",
    }
    with TestClient(app) as client:
        h, owner_id, pid = _owner_persona(client, basics=child)
        _add_memory(pid, owner_id)
        cid = _room(client, h, pid)
        ai.query_vector = [0.0, 1.0, 0.0]  # 기억 검색이 비었다(low_recall 이 될 만한 말)
        for fallback in conversations.MINOR_FALLBACKS:
            ai.content = fallback
            assert _say(client, h, cid, "우리 지난번에 한 얘기 기억나요?").status_code == 200
        assert ai.replies()[-1]["persona"]["minor"] is True
        assert ai.asked() == [] and _questions(pid) == []
        # 보통 대답이면 같은 말로 질문을 만든다(막은 것이 대체 대답 때문인지 확인)
        ai.content = "그래, 왔구나."
        assert _say(client, h, cid, "우리 지난번에 한 얘기 기억나요?").status_code == 200
        assert [r.signal for r in _questions(pid)] == ["low_recall"]


def test_low_recall_counts_only_memory_questions(ai: GapAI):
    with TestClient(app) as client:
        h, owner_id, pid = _owner_persona(client)
        _add_memory(pid, owner_id)
        cid = _room(client, h, pid)
        ai.query_vector = [0.0, 1.0, 0.0]  # 어떤 기억과도 닮지 않았다(유사도 0)
        for small_talk in ("안녕?", "할머니 밥 먹었어요?", "거기는 어때요?", "보고 싶어요"):
            assert _say(client, h, cid, small_talk).status_code == 200
        assert ai.asked() == []
        assert _say(client, h, cid, "할머니 고향 친구 이름이 뭐였어요?").status_code == 200
        assert ai.asked()[-1]["candidate"]["signal"] == "low_recall"
        assert ai.asked()[-1]["candidate"]["topic"] == ""
        assert [r.signal for r in _questions(pid)] == ["low_recall"]
        # 기억이 찾히면(유사도 높음) 신호가 아니다
        ai.query_vector = [1.0, 0.0, 0.0]
        asked = len(ai.asked())
        assert _say(client, h, cid, "할머니 고향이 어디였어요?").status_code == 200
        assert len(ai.asked()) == asked


def test_open_questions_are_capped_at_ten(ai: GapAI):
    with TestClient(app) as client:
        h, _, pid = _owner_persona(client)
        for i in range(gaps.MAX_OPEN):
            _seed_question(pid, topic=f"주제{i}", question=f"질문 {i}?")
        cid = _room(client, h, pid)
        ai.gap = {"topic": "새 주제"}
        assert _say(client, h, cid, "할머니 좋아하던 꽃은 뭐였어요?").status_code == 200
        assert ai.asked() == []
        assert len(_questions(pid)) == gaps.MAX_OPEN


def test_flag_off_sends_nothing_and_makes_no_question(ai: GapAI, settings):
    settings.gap_questions_enabled = False
    with TestClient(app) as client:
        h, _, pid = _owner_persona(client)
        cid = _room(client, h, pid)
        ai.gap = {"topic": "노래"}
        assert _say(client, h, cid, "할머니 좋아하시던 노래 뭐였어요?").status_code == 200
        assert "flagGaps" not in ai.replies()[-1]
        assert ai.asked() == [] and _questions(pid) == []


def test_failed_question_task_does_not_break_the_chat(ai: GapAI):
    with TestClient(app) as client:
        h, _, pid = _owner_persona(client)
        cid = _room(client, h, pid)
        ai.gap = {"topic": "노래"}
        ai.question = {"action": "new", "question": ""}  # 쓸 수 없는 답 → 건너뛴다
        assert _say(client, h, cid, "할머니 좋아하시던 노래 뭐였어요?").status_code == 200
        assert _questions(pid) == []


# ────────────────────────── 만든 분의 답·잘 모르겠어요 ──────────────────────────


def test_owner_answer_becomes_an_embedded_gap_memory(ai: GapAI):
    with TestClient(app) as client:
        h, _, pid = _owner_persona(client)
        qid = _seed_question(pid, topic="즐겨 부른 노래", question=QUESTION)
        other = _seed_question(pid)
        blank = client.post(
            f"{API}/personas/{pid}/gap-questions/{qid}/answer", headers=h, json={"text": "  "}
        )
        assert blank.status_code == 400
        answered = client.post(
            f"{API}/personas/{pid}/gap-questions/{qid}/answer",
            headers=h,
            json={"text": "섬집아기를 자주 부르셨어요."},
        )
        assert answered.status_code == 200, answered.text
        data = answered.json()["data"]
        assert (data["status"], data["answerState"], data["answeredByMe"]) == (
            "answered",
            "applied",
            True,
        )
        row = _get(GapQuestion, qid)
        memory = _get(Memory, row.memory_id)
        assert memory.source == "GAP" and memory.embedding_status == "READY"
        assert memory.embedding == [1.0, 0.0, 0.0]
        assert memory.body_markdown == "즐겨 부른 노래: 섬집아기를 자주 부르셨어요."
        # 누가 알려 줬는지(만든 분 이름) 붙인다: 다른 가족과의 대화에서 그 가족의 일처럼 말하지 않게
        assert memory.title == "지은이 알려 준 이야기 · 즐겨 부른 노래"
        # 다시 만들기 없이 바로 다음 대화에서 찾힌다
        cid = _room(client, h, pid)
        assert _say(client, h, cid, "할머니 노래 불러 주세요").status_code == 200
        assert memory.id in [m["id"] for m in ai.replies()[-1]["memories"]]
        again = client.post(
            f"{API}/personas/{pid}/gap-questions/{qid}/answer", headers=h, json={"text": "또"}
        )
        assert again.status_code == 409
        # 잘 모르겠어요 → 닫힌다
        dismissed = client.post(f"{API}/personas/{pid}/gap-questions/{other}/dismiss", headers=h)
        assert dismissed.json()["data"]["status"] == "dismissed"
        assert client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["gapCount"] == 0
        assert _gap_list(client, h, pid)["questions"] == []


def test_strangers_get_404(ai: GapAI):
    with TestClient(app) as client:
        _, _, pid = _owner_persona(client)
        qid = _seed_question(pid)
        stranger, _, _ = _signup(client, "남남")
        assert (
            client.get(f"{API}/personas/{pid}/gap-questions", headers=stranger).status_code == 404
        )
        for action in ("answer", "dismiss", "ask-family", "approve", "reject"):
            response = client.post(
                f"{API}/personas/{pid}/gap-questions/{qid}/{action}",
                headers=stranger,
                json={"text": "x"},
            )
            assert response.status_code == 404, action


# ────────────────────────── 함께 쓰는 가족 ──────────────────────────


def test_member_sees_only_asked_questions_and_answers_wait_for_owner(ai: GapAI):
    with TestClient(app) as client:
        fam = _family(client)
        qid = _seed_question(fam.pid, topic="즐겨 부른 노래", question=QUESTION)
        hidden = _seed_question(fam.pid)
        url = f"{API}/personas/{fam.pid}/gap-questions"
        assert _gap_list(client, fam.member, fam.pid)["questions"] == []
        assert (
            client.get(f"{API}/personas/{fam.pid}", headers=fam.member).json()["data"]["gapCount"]
            == 0
        )
        # 가족은 묻기·확인을 못 한다(404)
        assert client.post(f"{url}/{qid}/ask-family", headers=fam.member).status_code == 404
        assert (
            client.post(
                f"{url}/{hidden}/answer", headers=fam.member, json={"text": "몰라"}
            ).status_code
            == 404
        )
        owner_view = _gap_list(client, fam.owner, fam.pid)
        assert owner_view["canAskFamily"] is True
        asked = client.post(f"{url}/{qid}/ask-family", headers=fam.owner)
        assert asked.status_code == 200 and asked.json()["data"]["askFamily"] is True
        member_view = _gap_list(client, fam.member, fam.pid)
        assert [q["id"] for q in member_view["questions"]] == [qid]
        assert member_view["role"] == "member" and member_view["pendingCorrections"] == []
        assert (
            client.get(f"{API}/personas/{fam.pid}", headers=fam.member).json()["data"]["gapCount"]
            == 1
        )
        answered = client.post(
            f"{url}/{qid}/answer", headers=fam.member, json={"text": "엄마는 섬집아기를 부르셨어"}
        )
        assert answered.status_code == 200, answered.text
        assert answered.json()["data"]["answerState"] == "pending_owner"
        # 만든 분이 확인하기 전에는 기억이 아니다
        gap_memories = select(func.count(Memory.id)).where(
            Memory.source == "GAP", Memory.person_id == _get(Persona, fam.pid).person_id
        )
        before = _count(gap_memories)
        assert _get(GapQuestion, qid).memory_id is None
        owner_view = _gap_list(client, fam.owner, fam.pid)
        assert [q["id"] for q in owner_view["questions"]] == [hidden]
        pending = owner_view["pendingAnswers"]
        assert [(p["id"], p["answerText"]) for p in pending] == [
            (qid, "엄마는 섬집아기를 부르셨어")
        ]
        assert pending[0]["answeredBy"] == {"name": "철수", "relation": "아들"}
        # 만든 분 상세의 gapWaiting 은 확인을 기다리는 가족 답을 센다(가족 상세에는 없다)
        detail = client.get(f"{API}/personas/{fam.pid}", headers=fam.owner).json()["data"]
        assert (detail["gapCount"], detail["gapWaiting"]) == (1, 1)
        member_detail = client.get(f"{API}/personas/{fam.pid}", headers=fam.member).json()
        assert "gapWaiting" not in member_detail["data"]
        mine = _gap_list(client, fam.member, fam.pid)
        assert mine["questions"] == []
        assert mine["pendingAnswers"][0]["answerText"] == "엄마는 섬집아기를 부르셨어"
        # 거절하면 질문이 다시 열리고 가족의 답은 지운다
        rejected = client.post(f"{url}/{qid}/reject", headers=fam.owner)
        assert rejected.status_code == 200
        row = _get(GapQuestion, qid)
        assert (row.status, row.answer_state, row.answer_text) == ("open", "rejected", None)
        assert _count(gap_memories) == before
        # 다시 답하고 확인하면 기억이 된다
        client.post(f"{url}/{qid}/answer", headers=fam.member, json={"text": "섬집아기"})
        approved = client.post(f"{url}/{qid}/approve", headers=fam.owner)
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["answerState"] == "applied"
        assert _count(gap_memories) == before + 1
        assert client.post(f"{url}/{qid}/approve", headers=fam.owner).status_code == 409
        # 가족의 '잘 모르겠어요'는 그 가족에게만 숨긴다
        client.post(f"{url}/{hidden}/ask-family", headers=fam.owner)
        assert client.post(f"{url}/{hidden}/dismiss", headers=fam.member).status_code == 200
        assert _gap_list(client, fam.member, fam.pid)["questions"] == []
        assert _get(GapQuestion, hidden).status == "open"


def test_ask_family_needs_a_joined_member(ai: GapAI):
    with TestClient(app) as client:
        h, _, pid = _owner_persona(client)
        qid = _seed_question(pid)
        response = client.post(f"{API}/personas/{pid}/gap-questions/{qid}/ask-family", headers=h)
        assert response.status_code == 409


def test_member_leaving_reopens_their_pending_answer(ai: GapAI):
    with TestClient(app) as client:
        fam = _family(client)
        qid = _seed_question(fam.pid, ask_family=True)
        url = f"{API}/personas/{fam.pid}/gap-questions/{qid}"
        client.post(f"{url}/answer", headers=fam.member, json={"text": "아마 그랬을걸"})
        left = client.delete(f"{API}/personas/{fam.pid}/shares/{fam.share_id}", headers=fam.member)
        assert left.status_code in (200, 204), left.text
        row = _get(GapQuestion, qid)
        assert (row.status, row.answer_state, row.answer_text, row.answered_by_user_id) == (
            "open",
            None,
            None,
            None,
        )


# ────────────────────────── 대화 고치기 ──────────────────────────


def test_old_clients_still_rate_replies(ai: GapAI):
    with TestClient(app) as client:
        h, _, pid = _owner_persona(client)
        cid = _room(client, h, pid)
        _say(client, h, cid, "안녕하세요")
        reply = _last_reply_id(client, h, cid)
        for body in ({"rating": "UP", "tags": []}, {"rating": "UP", "tags": ["NATURAL"]}):
            response = client.post(f"{API}/messages/{reply}/feedback", headers=h, json=body)
            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert data["state"] == "none" and data["correction"] is None


def test_owner_style_correction_goes_to_the_next_reply_as_text(ai: GapAI):
    with TestClient(app) as client:
        h, _, pid = _owner_persona(client)
        cid = _room(client, h, pid)
        _say(client, h, cid, "할머니 저 왔어요")
        reply = _last_reply_id(client, h, cid)
        response = client.post(
            f"{API}/messages/{reply}/feedback",
            headers=h,
            json={"rating": "DOWN", "tags": ["STYLE"], "correction": "  왔냐, 우리 강아지. "},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["state"] == "applied"
        examples = _all(select(StyleExample).where(StyleExample.persona_id == pid))
        assert [(e.original, e.corrected) for e in examples] == [
            ("그래, 왔구나.", "왔냐, 우리 강아지.")
        ]
        _say(client, h, cid, "밥 먹었어요")
        assert ai.replies()[-1]["styleExamples"] == [
            {"original": "그래, 왔구나.", "corrected": "왔냐, 우리 강아지."}
        ]
        # 같은 것을 다시 보내도 두 번 넣지 않고, 고침을 빼면 거둔다
        client.post(
            f"{API}/messages/{reply}/feedback",
            headers=h,
            json={"rating": "DOWN", "tags": ["STYLE"], "correction": "왔냐, 우리 강아지."},
        )
        assert len(_all(select(StyleExample).where(StyleExample.persona_id == pid))) == 1
        client.post(
            f"{API}/messages/{reply}/feedback",
            headers=h,
            json={"rating": "DOWN", "tags": ["TOO_LONG"]},
        )
        assert _all(select(StyleExample).where(StyleExample.persona_id == pid)) == []
        _say(client, h, cid, "또 왔어요")
        assert "styleExamples" not in ai.replies()[-1]


def test_style_examples_send_only_the_latest_ten(ai: GapAI):
    with TestClient(app) as client:
        h, owner_id, pid = _owner_persona(client)

        async def seed(db):
            for i in range(12):
                db.add(
                    StyleExample(
                        persona_id=pid,
                        original=f"대답 {i}",
                        corrected=f"고침 {i}",
                        created_at=datetime(2026, 10, 1, 9, i, tzinfo=UTC),
                    )
                )
            await db.commit()

        _db(seed)
        cid = _room(client, h, pid)
        _say(client, h, cid, "안녕하세요")
        sent = ai.replies()[-1]["styleExamples"]
        assert [s["corrected"] for s in sent] == [f"고침 {i}" for i in range(2, 12)]


def test_owner_content_correction_becomes_memory_and_flags_used_ones(ai: GapAI):
    with TestClient(app) as client:
        h, owner_id, pid = _owner_persona(client)
        used = _add_memory(pid, owner_id, "할머니는 수원에서 태어났다.")
        cid = _room(client, h, pid)
        _say(client, h, cid, "할머니 고향이 어디예요?")
        reply = _last_reply_id(client, h, cid)
        assert _get(Message, reply).retrieved_memory_ids == [used]
        response = client.post(
            f"{API}/messages/{reply}/feedback",
            headers=h,
            json={"rating": "DOWN", "tags": ["CONTENT"], "correction": "나는 남원 사람이다."},
        )
        assert response.status_code == 200, response.text
        item = _get(Feedback, response.json()["data"]["id"])
        assert item.state == "applied" and item.persona_id == pid
        memory = _get(Memory, item.memory_id)
        assert (memory.source, memory.body_markdown, memory.embedding_status) == (
            "FEEDBACK",
            "나는 남원 사람이다.",
            "READY",
        )
        flagged = _get(Memory, used)
        # 끌 후보 표시만 한다(지우거나 끄지 않는다)
        assert flagged.flagged_wrong is True and flagged.status == "ACTIVE"
        assert memory.flagged_wrong is None
        # '내용이 틀려요'는 빈틈 후보도 된다(대답 뒤 작업)
        candidate = ai.asked()[-1]["candidate"]
        assert candidate == {
            "userMessage": "할머니 고향이 어디예요?",
            "reply": "그래, 왔구나.",
            "topic": "",
            "signal": "feedback",
            "correction": "나는 남원 사람이다.",
        }
        assert [r.signal for r in _questions(pid)] == ["feedback"]


def test_member_corrections_wait_for_owner_approval(ai: GapAI):
    with TestClient(app) as client:
        fam = _family(client)
        cid = _room(client, fam.member, fam.pid)
        _say(client, fam.member, cid, "엄마 나 왔어")
        reply = _last_reply_id(client, fam.member, cid)
        style = client.post(
            f"{API}/messages/{reply}/feedback",
            headers=fam.member,
            json={"rating": "DOWN", "tags": ["STYLE"], "correction": "왔나, 우리 아들."},
        )
        assert style.status_code == 200 and style.json()["data"]["state"] == "pending_owner"
        assert _all(select(StyleExample).where(StyleExample.persona_id == fam.pid)) == []
        _say(client, fam.member, cid, "밥 먹었어")
        assert "styleExamples" not in ai.replies()[-1]
        pending = _gap_list(client, fam.owner, fam.pid)["pendingCorrections"]
        assert [(p["id"], p["original"], p["correction"], p["kinds"]) for p in pending] == [
            (style.json()["data"]["id"], "그래, 왔구나.", "왔나, 우리 아들.", ["STYLE"])
        ]
        assert pending[0]["answeredBy"] == {"name": "철수", "relation": "아들"}

        def waiting() -> int:
            return client.get(f"{API}/personas/{fam.pid}", headers=fam.owner).json()["data"][
                "gapWaiting"
            ]

        # 확인을 기다리는 가족 고침도 만든 분 배지(gapWaiting)에 든다
        assert waiting() == 1
        url = f"{API}/personas/{fam.pid}/corrections/{pending[0]['id']}"
        assert client.post(f"{url}/approve", headers=fam.member).status_code == 404
        approved = client.post(f"{url}/approve", headers=fam.owner)
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["state"] == "applied"
        assert client.post(f"{url}/approve", headers=fam.owner).status_code == 409
        _say(client, fam.member, cid, "또 왔어")
        assert ai.replies()[-1]["styleExamples"] == [
            {"original": "그래, 왔구나.", "corrected": "왔나, 우리 아들."}
        ]
        # 내용 고침도 확인 전에는 기억·끌 후보 표시·빈틈 질문이 없다. 빼면 그대로 끝난다
        _say(client, fam.member, cid, "엄마 고향 어디야?")
        second = _last_reply_id(client, fam.member, cid)
        content = client.post(
            f"{API}/messages/{second}/feedback",
            headers=fam.member,
            json={"rating": "DOWN", "tags": ["CONTENT"], "correction": "남원이지."},
        )
        assert content.json()["data"]["state"] == "pending_owner"
        assert waiting() == 1
        feedback_memories = select(func.count(Memory.id)).where(
            Memory.source == "FEEDBACK", Memory.person_id == _get(Persona, fam.pid).person_id
        )
        assert _count(feedback_memories) == 0
        assert ai.asked() == []
        rejected = client.post(
            f"{API}/personas/{fam.pid}/corrections/{content.json()['data']['id']}/reject",
            headers=fam.owner,
        )
        assert rejected.json()["data"]["state"] == "rejected"
        assert _count(feedback_memories) == 0
        assert _gap_list(client, fam.owner, fam.pid)["pendingCorrections"] == []
        assert waiting() == 0


def test_conversation_comment_and_admin_lists(ai: GapAI):
    with TestClient(app) as client:
        h, _, pid = _owner_persona(client)
        cid = _room(client, h, pid)
        _say(client, h, cid, "안녕하세요")
        reply = _last_reply_id(client, h, cid)
        client.post(
            f"{API}/messages/{reply}/feedback",
            headers=h,
            json={"rating": "DOWN", "tags": ["VOICE"], "comment": "목소리가 낮아요"},
        )
        _say(client, h, cid, "또 왔어요")
        second = _last_reply_id(client, h, cid)
        client.post(
            f"{API}/messages/{second}/feedback",
            headers=h,
            json={"rating": "DOWN", "tags": ["STYLE"], "correction": "왔냐."},
        )
        comment = client.post(
            f"{API}/conversations/{cid}/feedback", headers=h, json={"comment": " 오늘은 좋았어요 "}
        )
        assert comment.status_code == 201, comment.text
        assert comment.json()["data"]["comment"] == "오늘은 좋았어요"
        stranger, _, _ = _signup(client, "남남")
        assert (
            client.post(
                f"{API}/conversations/{cid}/feedback", headers=stranger, json={"comment": "x"}
            ).status_code
            == 404
        )
        assert client.get(f"{API}/admin/feedback", headers=h).status_code == 403
        admin, _ = _admin(client)
        voice = client.get(f"{API}/admin/feedback?tag=VOICE", headers=admin).json()
        mine = [row for row in voice["data"] if row["personaId"] == pid]
        assert [(r["tags"], r["comment"], r["reply"], r["personaTitle"]) for r in mine] == [
            (["VOICE"], "목소리가 낮아요", "그래, 왔구나.", "할머니")
        ]
        assert voice["meta"]["total"] >= 1
        styled = client.get(f"{API}/admin/feedback?tag=STYLE&corrected=true", headers=admin)
        row = next(r for r in styled.json()["data"] if r["personaId"] == pid)
        assert (row["correction"], row["state"], row["member"]) == ("왔냐.", "applied", False)
        patched = client.patch(
            f"{API}/admin/feedback/{row['id']}", headers=admin, json={"reviewed": True}
        )
        assert patched.status_code == 200 and patched.json()["data"]["reviewed"] is True
        unreviewed = client.get(f"{API}/admin/feedback?reviewed=false&tag=STYLE", headers=admin)
        assert row["id"] not in [r["id"] for r in unreviewed.json()["data"]]
        comments = client.get(f"{API}/admin/conversation-feedback", headers=admin).json()["data"]
        mine = next(c for c in comments if c["conversationId"] == cid)
        assert (mine["comment"], mine["personaTitle"], mine["reviewed"]) == (
            "오늘은 좋았어요",
            "할머니",
            False,
        )
        done = client.patch(
            f"{API}/admin/conversation-feedback/{mine['id']}",
            headers=admin,
            json={"reviewed": True},
        )
        assert done.json()["data"]["reviewed"] is True
        assert _get(ConversationFeedback, mine["id"]).reviewed is True


def test_second_member_cannot_see_first_members_pending_answer(ai: GapAI):
    with TestClient(app) as client:
        fam = _family(client)
        token, _ = _link(client, fam.owner, fam.pid)
        other, _, _ = _signup(client, "이영희")
        assert _accept(client, other, token, PROFILE_B).status_code == 201
        qid = _seed_question(fam.pid, ask_family=True)
        url = f"{API}/personas/{fam.pid}/gap-questions/{qid}"
        client.post(f"{url}/answer", headers=fam.member, json={"text": "철수 답"})
        view = _gap_list(client, other, fam.pid)
        assert view["questions"] == [] and view["pendingAnswers"] == []
        assert "철수 답" not in json.dumps(view, ensure_ascii=False)


def test_member_candidates_keep_member_words_out_of_owner_questions(ai: GapAI):
    """함께 쓰는 가족의 대화로는 질문을 만들지 않는다(빈틈 표시도 부탁하지 않는다 — 대화 내용이
    만든 분이 보는 질문으로 옮겨지지 않게). 가족이 직접 '내용이 틀려요'를 고른 대답과 만든 분이
    확인한 가족의 내용 고침만 후보가 되고, fromMember 로 보내 가족이 한 말을 질문에 옮기지 않게
    한다(약관 family_share)."""
    with TestClient(app) as client:
        fam = _family(client)
        cid = _room(client, fam.member, fam.pid)
        ai.gap = {"topic": "민수 결혼식"}
        ai.question = {"action": "new", "question": QUESTION}  # 질문 모델이 주제를 주지 않았다
        ai.query_vector = [0.0, 1.0, 0.0]  # 기억 검색도 비었다(low_recall 이 될 만한 말)
        assert _say(client, fam.member, cid, "엄마, 민수 결혼식 기억나?").status_code == 200
        assert "flagGaps" not in ai.replies()[-1]
        assert ai.asked() == [] and _questions(fam.pid) == []
        # 가족이 고침 없이 '내용이 틀려요'만 고르면 후보가 된다(가족 것으로)
        reply = _last_reply_id(client, fam.member, cid)
        bare = client.post(
            f"{API}/messages/{reply}/feedback",
            headers=fam.member,
            json={"rating": "DOWN", "tags": ["CONTENT"]},
        )
        assert bare.status_code == 200 and bare.json()["data"]["state"] == "none"
        assert ai.asked()[-1]["fromMember"] is True
        assert ai.asked()[-1]["candidate"]["signal"] == "feedback"
        assert [(r.question, r.topic) for r in _questions(fam.pid)] == [(QUESTION, "")]
        assert "민수" not in json.dumps(_gap_list(client, fam.owner, fam.pid), ensure_ascii=False)
        # 만든 분이 확인한 가족의 내용 고침도 가족 것으로 보낸다
        ai.gap = None
        _say(client, fam.member, cid, "엄마 고향 어디야?")
        reply = _last_reply_id(client, fam.member, cid)
        content = client.post(
            f"{API}/messages/{reply}/feedback",
            headers=fam.member,
            json={"rating": "DOWN", "tags": ["CONTENT"], "correction": "남원이지."},
        )
        assert content.json()["data"]["state"] == "pending_owner"
        approved = client.post(
            f"{API}/personas/{fam.pid}/corrections/{content.json()['data']['id']}/approve",
            headers=fam.owner,
        )
        assert approved.status_code == 200, approved.text
        assert ai.asked()[-1]["candidate"]["signal"] == "feedback"
        assert ai.asked()[-1]["fromMember"] is True


def test_owner_candidates_send_no_member_flag(ai: GapAI):
    with TestClient(app) as client:
        h, _, pid = _owner_persona(client)
        cid = _room(client, h, pid)
        ai.gap = {"topic": "노래"}
        ai.question = {"action": "new", "question": QUESTION}
        assert _say(client, h, cid, "할머니 좋아하시던 노래 뭐였어요?").status_code == 200
        assert "fromMember" not in ai.asked()[-1]
        # 만든 분 자신의 대화면 예전처럼 표시 주제로 채운다
        assert [(r.question, r.topic) for r in _questions(pid)] == [(QUESTION, "노래")]


def test_member_correction_changed_after_owner_looked_needs_a_fresh_look(ai: GapAI):
    """가족은 확인을 기다리는 고침을 고쳐 다시 보낼 수 있다(오타 고치기). 만든 분이 목록에서 본
    것과 지금 것이 다르면 확인·빼기가 409 다 — 만든 분이 보지 않은 글이 모든 대화의 말투 예시로
    들어가지 않게. 목록을 새로 보고 확인하면 된다. 본문을 보내지 않는 예전 화면은 그대로 된다."""
    with TestClient(app) as client:
        fam = _family(client)
        cid = _room(client, fam.member, fam.pid)
        _say(client, fam.member, cid, "엄마 나 왔어")
        reply = _last_reply_id(client, fam.member, cid)

        def send(tags: list[str], correction: str):
            sent = client.post(
                f"{API}/messages/{reply}/feedback",
                headers=fam.member,
                json={"rating": "DOWN", "tags": tags, "correction": correction},
            )
            assert sent.status_code == 200, sent.text
            assert sent.json()["data"]["state"] == "pending_owner"
            return sent.json()["data"]["id"]

        fid = send(["STYLE"], "왔나, 우리 아들.")
        seen = _gap_list(client, fam.owner, fam.pid)["pendingCorrections"][0]
        assert (seen["id"], seen["correction"]) == (fid, "왔나, 우리 아들.")
        # 만든 분이 본 뒤에 가족이 글을 바꿨다(같은 고침 id)
        assert send(["STYLE"], "바꿔 넣은 글") == fid
        url = f"{API}/personas/{fam.pid}/corrections/{fid}"
        looked = {"correction": seen["correction"], "tags": seen["tags"]}
        stale = client.post(f"{url}/approve", headers=fam.owner, json=looked)
        assert stale.status_code == 409, stale.text
        assert stale.json()["detail"] == corrections.CHANGED_SINCE_SEEN
        assert client.post(f"{url}/reject", headers=fam.owner, json=looked).status_code == 409
        assert _all(select(StyleExample).where(StyleExample.persona_id == fam.pid)) == []
        assert _get(Feedback, fid).state == "pending_owner"
        # 고른 것만 바꿔도(말투 → 내용, 기억이 된다) 다시 봐야 한다
        assert send(["CONTENT"], "바꿔 넣은 글") == fid
        fresh = _gap_list(client, fam.owner, fam.pid)["pendingCorrections"][0]
        swapped = {"correction": fresh["correction"], "tags": ["STYLE"]}
        assert client.post(f"{url}/approve", headers=fam.owner, json=swapped).status_code == 409
        # 새로 본 대로 확인하면 반영된다(고른 것의 차례는 상관없다)
        assert send(["STYLE"], "바꿔 넣은 글") == fid
        fresh = _gap_list(client, fam.owner, fam.pid)["pendingCorrections"][0]
        approved = client.post(
            f"{url}/approve",
            headers=fam.owner,
            json={"correction": f"  {fresh['correction']} ", "tags": fresh["tags"]},
        )
        assert approved.status_code == 200, approved.text
        examples = _all(select(StyleExample).where(StyleExample.persona_id == fam.pid))
        assert [e.corrected for e in examples] == ["바꿔 넣은 글"]


def test_member_answer_changed_after_owner_looked_needs_a_fresh_look(ai: GapAI):
    """만든 분이 목록에서 본 가족 답(answerText·answeredAt)과 지금 답이 다르면 확인·빼기가 409 다.
    가족이 나가 질문이 다시 열린 뒤 다른 가족이 답한 것을 옛 화면에서 확인해, 본 적 없는 답이
    기억이 되지 않게. 같은 글이어도 다른 가족의 답이면 막는다. 목록이 준 값 그대로 보내면 되고,
    본문을 보내지 않는 예전 화면도 그대로 된다."""
    with TestClient(app) as client:
        fam = _family(client)
        token, _ = _link(client, fam.owner, fam.pid)
        other, other_id, _ = _signup(client, "이영희")
        assert _accept(client, other, token, PROFILE_B).status_code == 201
        qid = _seed_question(fam.pid, ask_family=True)
        url = f"{API}/personas/{fam.pid}/gap-questions/{qid}"
        text = "엄마는 섬집아기를 부르셨어"
        answered = client.post(f"{url}/answer", headers=fam.member, json={"text": text})
        assert answered.status_code == 200, answered.text
        # 철수의 답은 어제(다른 가족이 같은 글로 답해도 답한 때가 다르다)
        _set(GapQuestion, qid, answered_at=datetime(2026, 10, 20, 9, 0, 0, 123456, tzinfo=UTC))
        [seen] = _gap_list(client, fam.owner, fam.pid)["pendingAnswers"]
        assert (seen["answerText"], seen["answeredBy"]["name"]) == (text, "철수")
        assert seen["answeredAt"]
        # 철수가 나가 질문이 다시 열리고, 영희가 같은 글로 답했다(같은 질문 id)
        left = client.delete(f"{API}/personas/{fam.pid}/shares/{fam.share_id}", headers=fam.member)
        assert left.status_code in (200, 204), left.text
        assert client.post(f"{url}/answer", headers=other, json={"text": text}).status_code == 200
        gap_memories = select(func.count(Memory.id)).where(
            Memory.source == "GAP", Memory.person_id == _get(Persona, fam.pid).person_id
        )
        before = _count(gap_memories)
        looked = {"answerText": seen["answerText"], "answeredAt": seen["answeredAt"]}
        stale = client.post(f"{url}/approve", headers=fam.owner, json=looked)
        assert stale.status_code == 409, stale.text
        assert stale.json()["detail"] == gaps.CHANGED_SINCE_SEEN
        assert client.post(f"{url}/reject", headers=fam.owner, json=looked).status_code == 409
        assert _count(gap_memories) == before
        row = _get(GapQuestion, qid)
        assert (row.answer_state, row.answered_by_user_id) == ("pending_owner", other_id)
        # 글이 다르면(답한 때가 맞아도) 다시 봐야 한다
        [fresh] = _gap_list(client, fam.owner, fam.pid)["pendingAnswers"]
        assert fresh["answeredBy"]["name"] == "영희"
        edited = {"answerText": "다른 글", "answeredAt": fresh["answeredAt"]}
        assert client.post(f"{url}/approve", headers=fam.owner, json=edited).status_code == 409
        # 새로 본 대로 빼면 빠지고(질문이 다시 열린다), 다시 답한 것을 본 대로 확인하면 기억이 된다
        looked = {"answerText": fresh["answerText"], "answeredAt": fresh["answeredAt"]}
        assert client.post(f"{url}/reject", headers=fam.owner, json=looked).status_code == 200
        assert _get(GapQuestion, qid).status == "open"
        client.post(f"{url}/answer", headers=other, json={"text": "섬집아기"})
        [fresh] = _gap_list(client, fam.owner, fam.pid)["pendingAnswers"]
        looked = {"answerText": fresh["answerText"], "answeredAt": fresh["answeredAt"]}
        approved = client.post(f"{url}/approve", headers=fam.owner, json=looked)
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["answerState"] == "applied"
        assert _count(gap_memories) == before + 1
        # 본문을 보내지 않는 예전 화면은 지금 답을 그대로 확인한다
        second = _seed_question(fam.pid, topic="고향", ask_family=True)
        old = f"{API}/personas/{fam.pid}/gap-questions/{second}"
        client.post(f"{old}/answer", headers=other, json={"text": "남원"})
        assert client.post(f"{old}/approve", headers=fam.owner).status_code == 200
        assert _count(gap_memories) == before + 2


def test_seen_answer_time_compares_across_time_zones():
    """답한 때는 UTC 로 맞춰 비교한다: DB 가 시간대 없이 준 값(시험 SQLite)·서울 시각·1/1000초까지인
    웹 화면 값이 같은 때면 같다. 1초라도 다르면 다른 답이다."""
    stored = datetime(2026, 10, 21, 1, 2, 3, 456789)
    row = GapQuestion(answer_text="섬집아기", answered_at=stored)
    seoul = timezone(timedelta(hours=9))
    for seen in (
        stored,
        stored.replace(tzinfo=UTC),
        datetime(2026, 10, 21, 10, 2, 3, 456789, tzinfo=seoul),
        datetime(2026, 10, 21, 1, 2, 3, 456000, tzinfo=UTC),
    ):
        gaps._check_seen(row, (" 섬집아기 ", seen))
    gaps._check_seen(row, None)
    for seen in (
        ("섬집아기", stored + timedelta(seconds=1)),
        ("섬집아기", None),
        ("섬집 아기", stored),
        (None, stored),
    ):
        with pytest.raises(HTTPException) as error:
            gaps._check_seen(row, seen)
        assert error.value.status_code == 409


def test_deceased_persona_gap_flow_sends_no_push(ai: GapAI, monkeypatch):
    """고인 대화 상대에는 푸시 알림을 보내지 않는다(대표 지시 2 — 앱 안의 표시(배지)만). 빈틈
    질문이 생기고 답할 때까지 앱 밖으로 알리는 것(디스코드·운영 메일·그 밖의 요청)이 없다."""
    sent: list[str] = []
    monkeypatch.setattr(notify, "send", lambda text, **kw: sent.append(text))
    monkeypatch.setattr(mailer, "send_ops", lambda subject, text: sent.append(subject) or False)
    with TestClient(app) as client:
        h, _, pid = _owner_persona(client)
        assert _get(Persona, pid).basics["status"] == "deceased"
        cid = _room(client, h, pid)
        sent.clear()  # 가입 알림 같은 운영 알림은 빼고, 빈틈 질문 흐름만 본다
        ai.gap = {"topic": "노래"}
        assert _say(client, h, cid, "할머니 좋아하시던 노래 뭐였어요?").status_code == 200
        [row] = _questions(pid)
        # 앱 안의 배지만
        assert client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["gapCount"] == 1
        answered = client.post(
            f"{API}/personas/{pid}/gap-questions/{row.id}/answer",
            headers=h,
            json={"text": "섬집아기를 자주 부르셨어요."},
        )
        assert answered.status_code == 200, answered.text
        assert client.get(f"{API}/personas/{pid}", headers=h).json()["data"]["gapCount"] == 0
    assert sent == []
    # 앱 밖으로 나간 요청은 AI 서버 요청뿐이다(푸시 서비스·웹후크 없음)
    outbound = list(ai.calls)
    assert outbound and all(p.startswith(("/v1/persona/", "/v1/embeddings")) for p in outbound)


def test_no_push_notification_dependency():
    """푸시 알림 라이브러리를 쓰지 않는다(고인 대화 상대 이름으로 푸시가 갈 길이 없다)."""
    root = Path(__file__).parents[1]
    deps = "\n".join(
        (root / name).read_text(encoding="utf-8").lower()
        for name in ("pyproject.toml", "requirements.lock")
    )
    for push in ("firebase", "pyfcm", "webpush", "apns", "onesignal", "exponent-server-sdk"):
        assert push not in deps, push
