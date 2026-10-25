"""음성 감정(AI FISH_EMOTION=inline): 대답 글과 목소리로 읽을 글(speechText)을 따로 저장하고
읽기에 쓴다.

화면·다음 대화 기록에는 대답 글(content)만 간다. AI 서버가 speechText 를 보내지 않으면
(꺼짐·예전 서버) 예전처럼 대답 글을 읽는다. 이미 만든 대답 소리는 다시 만들지 않는다.
"""

from fastapi.testclient import TestClient
from test_sharing import API, FakeAI, _family, _get, _room, _say, fake_ai, settings  # noqa: F401

from app.db.models import Message
from app.main import app


def test_owner_reply_is_read_with_speech_text_and_old_replies_still_work(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        fam = _family(client, voice_id="fish_voice_test", voice_provider="fish")
        room = _room(client, fam.owner, fam.pid)

        # AI 가 speechText 를 보내지 않으면(꺼짐·예전 AI 서버) 저장하지 않고 대답 글을 읽는다
        plain = _say(client, fam.owner, room).json()["data"]["assistantMessage"]
        assert _get(Message, plain["id"]).speech_text is None
        heard = client.post(f"{API}/messages/{plain['id']}/speech", headers=fam.owner)
        assert heard.status_code == 200, heard.text
        assert fake_ai.calls["/v1/voice/speech"][-1] == {
            "text": "그래, 왔구나.",
            "voiceId": "fish_voice_test",
        }

        fake_ai.speech_text = "[작게 웃으며] 그래, 왔구나."
        sent = _say(client, fam.owner, room, "할머니 나 왔어")
        assert sent.status_code == 200, sent.text
        reply = sent.json()["data"]["assistantMessage"]
        assert reply["content"] == "그래, 왔구나." and "speechText" not in reply
        row = _get(Message, reply["id"])
        assert (row.content, row.speech_text) == ("그래, 왔구나.", "[작게 웃으며] 그래, 왔구나.")
        detail = client.get(f"{API}/conversations/{room}", headers=fam.owner).json()["data"]
        assert all("[" not in m["content"] for m in detail["messages"])  # 화면 글에는 표시가 없다

        heard = client.post(f"{API}/messages/{reply['id']}/speech", headers=fam.owner)
        assert heard.status_code == 200, heard.text
        assert fake_ai.calls["/v1/voice/speech"][-1]["text"] == "[작게 웃으며] 그래, 왔구나."
        spoken = len(fake_ai.calls["/v1/voice/speech"])
        # 만든 소리는 저장해 두고 다시 만들지 않는다
        again = client.post(f"{API}/messages/{reply['id']}/speech", headers=fam.owner)
        assert again.status_code == 200
        assert len(fake_ai.calls["/v1/voice/speech"]) == spoken

        # 다음 대화의 앞 대화에는 대답 글만 간다
        _say(client, fam.owner, room, "또 왔어요")
        history = [m["content"] for m in fake_ai.replies()[-1]["history"]]
        assert "[작게 웃으며] 그래, 왔구나." not in history and history[-1] == "그래, 왔구나."
