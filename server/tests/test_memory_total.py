"""대화 대답 요청(/v1/persona/responses)의 앞 대화(history)와 기억 총수(memoryTotal).

- history: 그 대화방의 최근 메시지 HISTORY_MESSAGES(12 = 6턴)개, 시간순. 가족이 방금 알려 준 것을
  다음 대답에 쓰게 한다. 주인·함께 쓰는 가족·가져온 대화 상대·말로 보내기·실패 뒤 다시 보내기 모두.
- memoryTotal: 그분 기억 총수(검색과 같은 범위 — 꺼 둔 기억·다른 분 기억은 빼고, '내용이 틀려요'
  표시된 기억은 검색처럼 센다). 0 도 보낸다. 검색은 상위 몇 개만 보내서, AI 서버가 이 값으로 기억이
  적은 페르소나를 가린다.

모든 자료는 지어낸 시험용이다. AI 서버는 가짜(httpx MockTransport)다.
"""

import httpx
import pytest
from fastapi.testclient import TestClient
from test_sharing import (  # noqa: F401
    API,
    IMPORTED,
    FakeAI,
    _db,
    _family,
    _get,
    _persona,
    _room,
    _say,
    _signup,
    fake_ai,
    settings,
)

from app.api.v1 import conversations
from app.core.config import get_settings
from app.db.models import ORIGIN_IMPORT, Memory, Persona
from app.main import app
from app.services import ai as ai_module
from app.services import audio as audio_module
from app.services.memory_search import FALLBACK_RECENT

REPLY = "그래, 왔구나."


def _memories(person_id: str, owner_id: str, n: int, **values) -> None:
    """그분 기억 n개(벡터 없이 — 검색은 최근 기억으로 대신한다)."""

    async def go(db):
        for i in range(n):
            db.add(
                Memory(
                    person_id=person_id,
                    created_by_user_id=owner_id,
                    title=f"시험 기억 {i}",
                    body_markdown=f"시험용 기억 내용 {i}",
                    **values,
                )
            )
        await db.commit()

    _db(go)


def _turns(*texts: str) -> list[dict]:
    """주고받은 말(가짜 AI 는 늘 같은 대답)."""
    out: list[dict] = []
    for text in texts:
        out += [{"role": "user", "content": text}, {"role": "assistant", "content": REPLY}]
    return out


def test_owner_turns_send_history_and_memory_total(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        fam = _family(client)
        person_id = _get(Persona, fam.pid).person_id
        room = _room(client, fam.owner, fam.pid)

        # 첫 말: 앞 대화가 없고, 기억이 하나도 없어도 0 을 보낸다
        assert _say(client, fam.owner, room, "할머니 나 왔어").status_code == 200
        first = fake_ai.replies()[-1]
        assert first["history"] == []
        assert first["memoryTotal"] == 0 and first["memories"] == []

        # 쓸 수 있는 기억 25개(하나는 '내용이 틀려요' 표시 — 검색처럼 센다) + 꺼 둔 기억 2개 +
        # 다른 분의 기억 3개
        _memories(person_id, fam.owner_id, 24)
        _memories(person_id, fam.owner_id, 1, flagged_wrong=True)
        _memories(person_id, fam.owner_id, 2, status="INACTIVE")
        other = _get(Persona, _persona(fam.owner_id)).person_id
        _memories(other, fam.owner_id, 3)

        assert _say(client, fam.owner, room, "오늘 김치찌개 먹었어").status_code == 200
        second = fake_ai.replies()[-1]
        # 방금 나눈 말이 다음 요청의 앞 대화로 간다
        assert second["history"] == _turns("할머니 나 왔어")
        # 검색은 일부만 보내고(벡터가 없으면 최근 20개), 총수는 따로 센다
        assert len(second["memories"]) == FALLBACK_RECENT
        assert second["memoryTotal"] == 25
        assert "memoryTotal" not in second["persona"]

        # 최근 6턴(12개)까지 — 가족이 부탁한 4턴보다 넉넉하다
        said = ["할머니 나 왔어", "오늘 김치찌개 먹었어"]
        for i in range(5):
            said.append(f"할머니 {i}번째 얘기")
            assert _say(client, fam.owner, room, said[-1]).status_code == 200
        assert _say(client, fam.owner, room, "마지막 말").status_code == 200
        last = fake_ai.replies()[-1]
        assert conversations.HISTORY_MESSAGES == 12
        assert last["history"] == _turns(*said[-6:])
        assert last["memoryTotal"] == 25


def test_member_room_sends_its_own_history_and_the_same_total(fake_ai: FakeAI):  # noqa: F811
    """함께 쓰는 가족도 자기 방의 앞 대화를 보낸다(주인 방의 말은 섞이지 않는다). 기억 총수는
    그분 것이라 같다."""
    with TestClient(app) as client:
        fam = _family(client)
        person_id = _get(Persona, fam.pid).person_id
        _memories(person_id, fam.owner_id, 3)
        owner_room = _room(client, fam.owner, fam.pid)
        assert _say(client, fam.owner, owner_room, "주인이 한 말").status_code == 200
        assert fake_ai.replies()[-1]["memoryTotal"] == 3

        room = _room(client, fam.member, fam.pid)
        assert room != owner_room
        assert _say(client, fam.member, room, "엄마 나 철수").status_code == 200
        first = fake_ai.replies()[-1]
        assert first["history"] == [] and first["memoryTotal"] == 3
        assert first["speakerHint"] == "철수"

        assert _say(client, fam.member, room, "오늘 회사 쉬었어").status_code == 200
        second = fake_ai.replies()[-1]
        assert second["history"] == _turns("엄마 나 철수")
        assert second["memoryTotal"] == 3


class FlakyAI(FakeAI):
    """대답을 한 번 실패시킬 수 있고, 말로 보낸 녹음을 글로 옮겨 준다."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_reply = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/voice/transcriptions":
            self.calls[path].append({})
            return httpx.Response(
                200, json={"text": "목소리로 왔어요", "provider": "f", "model": "f"}
            )
        if path == "/v1/persona/responses" and self.fail_reply:
            self.calls[path].append({"failed": True})
            return httpx.Response(502, json={"error": {"code": "EMPTY_REPLY"}})
        return super().__call__(request)


@pytest.fixture
def flaky_ai(monkeypatch):
    fake = FlakyAI()
    monkeypatch.setattr(get_settings(), "ai_server_url", "https://ai.example.test")
    transport = httpx.MockTransport(fake)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        ai_module.httpx,
        "AsyncClient",
        lambda *a, **kw: real_client(transport=transport, **kw),
    )
    # 길이 재기(ffmpeg)는 이 시험의 일이 아니다
    monkeypatch.setattr(audio_module, "voice_seconds", lambda *a: 2.0)
    return fake


def test_retry_and_voice_message_keep_history(flaky_ai: FlakyAI):
    """대답을 못 받은 말은 앞 대화에 남지 않고(다시 보내도 두 번 들어가지 않는다), 말로 보낸 것도
    같은 앞 대화·기억 총수를 보낸다. 가져온 대화 상대(fixedPrompt)도 같다."""
    with TestClient(app) as client:
        owner, owner_id, _ = _signup(client, "김지은")
        pid = _persona(owner_id, origin=ORIGIN_IMPORT, instructions=IMPORTED)
        person_id = _get(Persona, pid).person_id
        _memories(person_id, owner_id, 2, source="IMPORT")
        room = _room(client, owner, pid)

        assert _say(client, owner, room, "할머니 나 왔어").status_code == 200
        flaky_ai.fail_reply = True
        failed = _say(client, owner, room, "오늘 비 왔어")
        assert failed.status_code == 503, failed.text
        flaky_ai.fail_reply = False
        assert _say(client, owner, room, "오늘 비 왔어").status_code == 200
        retry = flaky_ai.replies()[-1]
        assert retry["history"] == _turns("할머니 나 왔어")
        assert retry["persona"]["fixedPrompt"] is True and retry["memoryTotal"] == 2

        voice = client.post(
            f"{API}/conversations/{room}/messages/voice",
            headers=owner,
            files={"file": ("v.webm", b"\x1a\x45\xdf\xa3fake-webm", "audio/webm")},
        )
        assert voice.status_code == 200, voice.text
        spoken = flaky_ai.replies()[-1]
        assert spoken["message"] == "목소리로 왔어요"
        assert spoken["history"] == _turns("할머니 나 왔어", "오늘 비 왔어")
        assert spoken["memoryTotal"] == 2
