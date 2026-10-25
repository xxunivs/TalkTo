"""'더 알려 주실 기억'·대화 고치기 — 2026-10-21 검토에서 고친 것.

- 기억 제목에 누가 알려 줬는지(만든 분 이름 / 가족) 붙인다(GF-2).
- 답했거나 '잘 모르겠어요'로 닫은 주제는 다시 묻지 않는다(GF-3).
- 죽음·장례·위기 말이 나온 대화는 후보가 아니다(GF-5).
- 함께 쓰는 가족과의 대화로는 질문을 만들지 않는다(SEC-1·LEGAL-1). 가족의 '내용이 틀려요'만은
  후보가 된다(GF-6).
- 고인 대화 상대에는 '기억이 안 난다'가 든 고친 말투를 보내지 않는다(GF-8).
- 질문 만들기(LLM)를 기다리는 동안 DB 트랜잭션을 열어 두지 않는다(GF-11).
- 가족 답이 확인을 기다리는 질문도 열린 질문 상한에 센다(GF-12).
- 만든 분이 넣은 가족의 고침은 그 가족이 거두지 못한다(GF-13·SEC-5).
- 가족에게는 질문 글의 그분 호칭을 그 가족이 부르는 말로 보인다(GF-14).

모든 자료는 지어낸 시험용이다. AI 서버는 가짜(httpx MockTransport)다.
"""

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_gaps_feedback import QUESTION, GapAI, _last_reply_id, _questions, _seed_question
from test_sharing import (
    API,
    OWNER_BASICS,
    _all,
    _db,
    _family,
    _get,
    _persona,
    _room,
    _say,
    _signup,
)

from app.core.config import get_settings
from app.db.models import Feedback, GapQuestion, Memory, Person, Persona, StyleExample
from app.main import app
from app.services import ai as ai_module
from app.services import gaps


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


def _add_memory(pid: str, text: str = "할머니는 남원에서 태어났다.") -> str:
    async def go(db):
        persona = await db.get(Persona, pid)
        person = await db.get(Person, persona.person_id)
        memory = Memory(
            person_id=persona.person_id,
            created_by_user_id=person.owner_user_id,
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


def _memories(pid: str, source: str) -> list[Memory]:
    person_id = _get(Persona, pid).person_id
    return _all(select(Memory).where(Memory.person_id == person_id, Memory.source == source))


def _feedback(client, h, message_id: str, **body):
    return client.post(
        f"{API}/messages/{message_id}/feedback", headers=h, json={"rating": "DOWN", **body}
    )


# ────────────────────────── GF-2 누가 알려 줬는지 ──────────────────────────


def test_memory_titles_say_who_told_them(ai: GapAI):
    with TestClient(app) as client:
        fam = _family(client)
        url = f"{API}/personas/{fam.pid}/gap-questions"
        mine = _seed_question(
            fam.pid, topic="자장가", question="할머니께서 불러 주신 자장가가 있었나요?"
        )
        theirs = _seed_question(fam.pid, topic="즐겨 부른 노래", question=QUESTION, ask_family=True)
        # 만든 분의 답: 만든 분 이름을 붙인다(다른 가족과의 대화에서 그 가족의 일처럼 말하지 않게)
        client.post(f"{url}/{mine}/answer", headers=fam.owner, json={"text": "나 재울 때 섬집아기"})
        owner_memory = _get(Memory, _get(GapQuestion, mine).memory_id)
        assert owner_memory.title == "지은이 알려 준 이야기 · 자장가"
        assert owner_memory.created_by_user_id == fam.owner_id
        # 가족의 답은 만든 분이 넣어도 '가족'(다른 가족에게 누가 함께 쓰는지 드러내지 않게)
        client.post(f"{url}/{theirs}/answer", headers=fam.member, json={"text": "제가 어릴 때요"})
        assert client.post(f"{url}/{theirs}/approve", headers=fam.owner).status_code == 200
        member_memory = _get(Memory, _get(GapQuestion, theirs).memory_id)
        assert member_memory.title == "가족이 알려 준 이야기 · 즐겨 부른 노래"
        assert "철수" not in member_memory.title
        # 만든 분이 그 기억을 가진 채로 쓰는 사람만 바뀐다(권한은 그대로)
        assert member_memory.created_by_user_id == fam.owner_id

        # 바로잡은 말도 같다
        owner_room = _room(client, fam.owner, fam.pid)
        _say(client, fam.owner, owner_room, "할머니 고향 어디예요?")
        fixed = _feedback(
            client,
            fam.owner,
            _last_reply_id(client, fam.owner, owner_room),
            tags=["CONTENT"],
            correction="나 어릴 때 남원 살았지.",
        )
        assert fixed.json()["data"]["state"] == "applied"
        item = _get(Feedback, fixed.json()["data"]["id"])
        assert _get(Memory, item.memory_id).title == "지은이 바로잡은 이야기"
        member_room = _room(client, fam.member, fam.pid)
        _say(client, fam.member, member_room, "엄마 고향 어디야?")
        sent = _feedback(
            client,
            fam.member,
            _last_reply_id(client, fam.member, member_room),
            tags=["CONTENT"],
            correction="남원이지.",
        )
        approve = f"{API}/personas/{fam.pid}/corrections/{sent.json()['data']['id']}/approve"
        assert client.post(approve, headers=fam.owner).status_code == 200
        item = _get(Feedback, sent.json()["data"]["id"])
        assert _get(Memory, item.memory_id).title == "가족이 바로잡은 이야기"


# ────────────────────────── GF-3 닫은 주제 ──────────────────────────


def test_closed_topics_are_not_asked_again(ai: GapAI):
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "김지은")
        pid = _persona(owner_id)
        dismissed = _seed_question(
            pid, topic="즐겨 부른 노래", question=QUESTION, status="dismissed"
        )
        answered = _seed_question(pid, topic="고향", status="answered", answer_state="applied")
        cid = _room(client, h, pid)
        # 닫은 주제와 같은 표시면 AI 서버를 부르지 않는다
        ai.gap = {"topic": "즐겨 부른 노래"}
        assert _say(client, h, cid, "할머니 좋아하시던 노래 뭐였어요?").status_code == 200
        assert ai.asked() == []
        # 다른 주제면 묻되, 닫은 질문도 '이미 물은 것'으로 함께 보낸다
        ai.gap = {"topic": "가수"}
        ai.question = {"action": "new", "question": "다른 질문이에요?", "topic": "즐겨 부른 노래"}
        assert _say(client, h, cid, "할머니가 좋아하던 가수는요?").status_code == 200
        sent = ai.asked()[-1]["openQuestions"]
        assert {q["id"] for q in sent} == {dismissed, answered}
        # AI 가 고른 주제가 닫은 것과 같으면 새로 만들지 않는다
        assert len(_questions(pid)) == 2
        # 닫은 질문에 합치라고 해도 아무것도 하지 않는다(다시 열지 않는다)
        ai.gap = {"topic": "가수 이름"}
        ai.question = {"action": "merge", "mergeInto": dismissed}
        assert _say(client, h, cid, "그 가수 이름이 뭐였어요?").status_code == 200
        row = _get(GapQuestion, dismissed)
        assert row.status == "dismissed" and (row.hits or 1) == 1
        assert len(_questions(pid)) == 2


# ────────────────────────── GF-5 안전 말 ──────────────────────────


def test_safety_turns_are_never_candidates(ai: GapAI):
    assert gaps.signal_for("임종", 0.0, "할머니 돌아가셨을 때 무슨 말 하셨어요?") is None
    assert gaps.signal_for(None, 0.0, "나 요즘 죽고 싶어, 할머니는 어떻게 버티셨어?") is None
    assert gaps.signal_for("노래", 0.0, "할머니 좋아하시던 노래 뭐였어요?") == ("model", "노래")
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "김지은")
        pid = _persona(owner_id)
        cid = _room(client, h, pid)
        ai.gap = {"topic": "장례"}
        assert _say(client, h, cid, "할머니 장례 때 누가 왔었어요?").status_code == 200
        assert ai.asked() == [] and _questions(pid) == []


# ────────────────────────── SEC-1·LEGAL-1·GF-6 가족과의 대화 ──────────────────────────


def test_member_chats_make_no_questions_but_member_content_flags_do(ai: GapAI):
    with TestClient(app) as client:
        fam = _family(client)
        used = _add_memory(fam.pid)
        cid = _room(client, fam.member, fam.pid)
        # 기억 검색이 빈 묻는 말(low_recall)도, 모델 표시도 가족과의 대화에서는 후보가 아니다
        ai.query_vector = [0.0, 1.0, 0.0]
        assert _say(client, fam.member, cid, "엄마 고향 친구 이름이 뭐였어?").status_code == 200
        ai.gap = {"topic": "고향 친구"}
        assert _say(client, fam.member, cid, "엄마 좋아하던 노래 뭐였어?").status_code == 200
        assert all("flagGaps" not in body for body in ai.replies())
        assert ai.asked() == [] and _questions(fam.pid) == []
        # 같은 말을 만든 분이 하면 후보가 된다
        owner_room = _room(client, fam.owner, fam.pid)
        assert (
            _say(client, fam.owner, owner_room, "할머니 좋아하던 노래 뭐였어요?").status_code == 200
        )
        assert len(ai.asked()) == 1 and "fromMember" not in ai.asked()[-1]

        # 가족이 고침 없이 '내용이 틀려요'만 고르면: 쓰인 기억에 끌 후보 표시(관리자만 봄)와
        # 가족 것으로 보내는 후보(질문은 만든 분 목록에만 생기고 아무것도 반영하지 않는다)
        ai.query_vector = [1.0, 0.0, 0.0]
        ai.gap = None
        _say(client, fam.member, cid, "엄마 고향이 어디였어?")
        reply = _last_reply_id(client, fam.member, cid)
        flagged = _feedback(client, fam.member, reply, tags=["CONTENT"])
        assert flagged.status_code == 200 and flagged.json()["data"]["state"] == "none"
        assert _get(Memory, used).flagged_wrong is True
        assert ai.asked()[-1]["fromMember"] is True
        assert ai.asked()[-1]["candidate"]["signal"] == "feedback"
        # 다시 보내도(평가만 바꿈) 후보를 또 만들지 않는다
        asked = len(ai.asked())
        _feedback(client, fam.member, reply, tags=["CONTENT"], comment="틀렸어요")
        assert len(ai.asked()) == asked
        # 가족의 고침이 있으면 만든 분이 확인할 때만 후보가 된다
        _say(client, fam.member, cid, "엄마 노래 뭐 좋아했어?")
        second = _last_reply_id(client, fam.member, cid)
        pending = _feedback(client, fam.member, second, tags=["CONTENT"], correction="섬집아기")
        assert pending.json()["data"]["state"] == "pending_owner"
        assert len(ai.asked()) == asked


# ────────────────────────── GF-8 고인 대화 상대의 고친 말투 ──────────────────────────


def _seed_styles(pid: str) -> None:
    async def go(db):
        db.add(StyleExample(persona_id=pid, original="그래.", corrected="글쎄, 기억이 잘 안 나네."))
        db.add(StyleExample(persona_id=pid, original="그래.", corrected="왔냐, 우리 강아지."))
        await db.commit()

    _db(go)


def test_deceased_personas_get_no_cannot_remember_style(ai: GapAI):
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "김지은")
        deceased = _persona(owner_id)
        living = _persona(owner_id, basics={**OWNER_BASICS, "status": "alive"})
        for pid in (deceased, living):
            _seed_styles(pid)
        _say(client, h, _room(client, h, deceased), "할머니 저 왔어요")
        assert [s["corrected"] for s in ai.replies()[-1]["styleExamples"]] == ["왔냐, 우리 강아지."]
        _say(client, h, _room(client, h, living), "할머니 저 왔어요")
        assert len(ai.replies()[-1]["styleExamples"]) == 2


# ────────────────────────── GF-11 트랜잭션 ──────────────────────────


def test_question_task_holds_no_transaction_while_the_llm_runs(ai: GapAI, monkeypatch):
    opened = []
    real = gaps.SessionLocal

    def tracked():
        session = real()
        opened.append(session)
        return session

    seen: list[bool] = []

    async def gap_question(**request):
        seen.append(opened[-1].in_transaction())
        return {"action": "new", "question": QUESTION, "topic": "노래"}

    monkeypatch.setattr(gaps, "SessionLocal", tracked)
    monkeypatch.setattr(ai_module.ai_service, "gap_question", gap_question)
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "김지은")
        pid = _persona(owner_id)
        cid = _room(client, h, pid)
        ai.gap = {"topic": "노래"}
        assert _say(client, h, cid, "할머니 좋아하시던 노래 뭐였어요?").status_code == 200
    assert seen == [False]
    assert [(r.question, r.topic) for r in _questions(pid)] == [(QUESTION, "노래")]


# ────────────────────────── GF-12 상한 ──────────────────────────


def test_pending_family_answers_count_toward_the_cap(ai: GapAI):
    with TestClient(app) as client:
        h, owner_id, _ = _signup(client, "김지은")
        pid = _persona(owner_id)
        for i in range(gaps.MAX_OPEN - 1):
            _seed_question(pid, topic=f"주제{i}", question=f"질문 {i}?")
        _seed_question(
            pid,
            topic="기다림",
            question="기다리는 질문?",
            status="answered",
            answer_state="pending_owner",
            answer_text="가족 답",
        )
        cid = _room(client, h, pid)
        ai.gap = {"topic": "새 주제"}
        assert _say(client, h, cid, "할머니 좋아하던 꽃은 뭐였어요?").status_code == 200
        assert ai.asked() == []
        # 가족 답을 빼서 다시 열려도 상한을 넘지 않는다
        waiting = next(r for r in _questions(pid) if r.status == "answered")
        reject = f"{API}/personas/{pid}/gap-questions/{waiting.id}/reject"
        assert client.post(reject, headers=h).status_code == 200
        assert gaps.MAX_OPEN == len([r for r in _questions(pid) if r.status == "open"])


# ────────────────────────── GF-13·SEC-5 넣은 고침 ──────────────────────────


def test_member_cannot_undo_a_correction_the_owner_applied(ai: GapAI):
    with TestClient(app) as client:
        fam = _family(client)
        cid = _room(client, fam.member, fam.pid)
        _say(client, fam.member, cid, "엄마 나 왔어")
        reply = _last_reply_id(client, fam.member, cid)
        sent = _feedback(client, fam.member, reply, tags=["STYLE"], correction="왔나, 우리 아들.")
        fid = sent.json()["data"]["id"]
        approve = f"{API}/personas/{fam.pid}/corrections/{fid}/approve"
        assert client.post(approve, headers=fam.owner).status_code == 200
        styles = select(StyleExample).where(StyleExample.persona_id == fam.pid)
        assert len(_all(styles)) == 1
        for body in (
            {"tags": ["STYLE"], "correction": "다른 말"},
            {"tags": ["LIKE"]},
            {"tags": []},
        ):
            refused = _feedback(client, fam.member, reply, **body)
            assert refused.status_code == 409, body
            assert "만든 분" in refused.json()["detail"]
        assert len(_all(styles)) == 1
        assert _get(Feedback, fid).state == "applied"
        # 같은 고침을 다시 보내는 것(평가·한 줄만 바꿈)은 된다
        same = _feedback(
            client,
            fam.member,
            reply,
            tags=["STYLE"],
            correction="왔나, 우리 아들.",
            comment="좋아요",
        )
        assert same.status_code == 200 and same.json()["data"]["state"] == "applied"
        # 만든 분이 뺀 고침은 고쳐 다시 보낼 수 있다(다시 확인을 기다린다)
        _say(client, fam.member, cid, "엄마 고향 어디야?")
        second = _last_reply_id(client, fam.member, cid)
        content = _feedback(client, fam.member, second, tags=["CONTENT"], correction="남원이지.")
        cfid = content.json()["data"]["id"]
        reject = f"{API}/personas/{fam.pid}/corrections/{cfid}/reject"
        assert client.post(reject, headers=fam.owner).status_code == 200
        again = _feedback(client, fam.member, second, tags=["CONTENT"], correction="전주였지.")
        assert again.status_code == 200 and again.json()["data"]["state"] == "pending_owner"
        # 만든 분 자신은 넣은 고침을 바꿀 수 있다(예전과 같다)
        owner_room = _room(client, fam.owner, fam.pid)
        _say(client, fam.owner, owner_room, "할머니 저 왔어요")
        mine = _last_reply_id(client, fam.owner, owner_room)
        _feedback(client, fam.owner, mine, tags=["STYLE"], correction="왔냐.")
        changed = _feedback(client, fam.owner, mine, tags=["STYLE"], correction="왔구나.")
        assert changed.status_code == 200 and changed.json()["data"]["state"] == "applied"


# ────────────────────────── GF-14 가족의 호칭 ──────────────────────────


def test_members_see_questions_with_their_own_address_term(ai: GapAI):
    with TestClient(app) as client:
        fam = _family(client)  # 만든 분은 '할머니', 가족(철수)은 '엄마'라고 부른다
        qid = _seed_question(fam.pid, topic="즐겨 부른 노래", question=QUESTION, ask_family=True)
        url = f"{API}/personas/{fam.pid}/gap-questions"
        member_view = client.get(url, headers=fam.member).json()["data"]
        assert member_view["questions"][0]["question"] == "엄마께서 즐겨 부르시던 노래가 있었나요?"
        owner_view = client.get(url, headers=fam.owner).json()["data"]
        assert owner_view["questions"][0]["question"] == QUESTION
        answered = client.post(f"{url}/{qid}/answer", headers=fam.member, json={"text": "섬집아기"})
        assert answered.json()["data"]["question"].startswith("엄마께서")
        pending = client.get(url, headers=fam.member).json()["data"]["pendingAnswers"]
        assert pending[0]["question"].startswith("엄마께서")
        # 저장된 질문은 그대로다
        assert _get(GapQuestion, qid).question == QUESTION
    assert gaps.relabel(QUESTION, ("할머니", "그분")) == "그분께서 즐겨 부르시던 노래가 있었나요?"
    assert gaps.relabel(QUESTION, ("", "엄마")) == QUESTION
    assert gaps.relabel(QUESTION, None) == QUESTION
