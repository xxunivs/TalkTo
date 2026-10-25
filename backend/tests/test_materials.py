"""글 자료 더하기: 파일·붙여넣기·넣기 켜고 끄기·지우기. 아직 대화에는 쓰지 않는다."""

from fastapi.testclient import TestClient
from test_persona_setup import BASICS, FakeAI, _signup, fake_ai  # noqa: F401

from app.main import app
from app.services.storage import get_storage

API = "/api/v1"

KAKAO = (
    "엄마 님과 카카오톡 대화\n"
    "저장한 날짜 : 2026-10-13 10:00\n"
    "\n"
    "--------------- 2024년 3월 2일 토요일 ---------------\n"
    "[엄마] [오후 2:15] 밥은 먹었니\n"
    "[길동] [오후 2:16] 네 먹었어요\n"
)


def test_add_list_toggle_delete_materials(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "materials@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]
        empty = client.get(f"{API}/personas/{pid}/materials", headers=h).json()["data"]
        assert empty["items"] == [] and empty["usedInConversation"] is False
        assert empty["kinds"]["kakaotalk"] == "카카오톡 대화"

        # 파일 여러 개: 윈도 카카오톡(BOM 붙은 UTF-8)과 오래된 CP949 메모
        added = client.post(
            f"{API}/personas/{pid}/materials",
            headers=h,
            data={"pledge": "true", "kind": "kakaotalk"},
            files=[
                ("files", ("KakaoTalk_엄마.txt", "﻿".encode() + KAKAO.encode(), "text/plain")),
                ("files", ("메모.txt", "오래된 메모 내용".encode("cp949"), "text/plain")),
            ],
        )
        assert added.status_code == 200, added.text
        items = added.json()["data"]["items"]
        assert {i["title"] for i in items} == {"KakaoTalk_엄마", "메모"}
        kakao = next(i for i in items if i["title"] == "KakaoTalk_엄마")
        assert kakao["source"] == "file" and kakao["include"] is True
        assert kakao["preview"].startswith("엄마 님과 카카오톡 대화")
        memo = next(i for i in items if i["title"] == "메모")
        assert memo["preview"] == "오래된 메모 내용"

        # 붙여넣기(휴대폰 문자처럼 파일로 못 꺼내는 것)
        pasted = client.post(
            f"{API}/personas/{pid}/materials",
            headers=h,
            data={
                "pledge": "true",
                "kind": "sms",
                "text": "할머니: 우리 강아지 잘 지내니\n나: 네!",
            },
        )
        assert pasted.status_code == 200, pasted.text
        sms = next(i for i in pasted.json()["data"]["items"] if i["kind"] == "sms")
        assert sms["source"] == "text" and sms["title"].startswith("할머니: 우리 강아지")

        # 넣지 않기로 바꾸고 이름 고치기
        toggled = client.patch(
            f"{API}/personas/{pid}/materials/{sms['id']}",
            headers=h,
            json={"include": False, "title": "할머니 문자"},
        ).json()["data"]
        sms = next(i for i in toggled["items"] if i["id"] == sms["id"])
        assert sms["include"] is False and sms["title"] == "할머니 문자"
        assert toggled["includedCount"] == 2

        # 지우면 저장한 파일도 지운다
        storage = get_storage()
        key = f"personas/{pid}/materials/{kakao['id']}.txt"
        assert storage.exists(key)
        left = client.delete(f"{API}/personas/{pid}/materials/{kakao['id']}", headers=h)
        assert left.status_code == 200 and len(left.json()["data"]["items"]) == 2
        assert not storage.exists(key)


def test_material_rules(fake_ai: FakeAI):  # noqa: F811
    with TestClient(app) as client:
        h = _signup(client, "material-rules@example.com")
        pid = client.post(f"{API}/personas", headers=h, json=BASICS).json()["data"]["id"]

        def post(**kw):
            return client.post(f"{API}/personas/{pid}/materials", headers=h, **kw)

        assert post(data={"pledge": "true", "kind": "nope", "text": "x"}).status_code == 400
        assert post(data={"pledge": "true", "kind": "diary"}).status_code == 400  # 파일도 글도 없음
        zipped = post(
            data={"pledge": "true", "kind": "kakaotalk"},
            files=[("files", ("대화.zip", b"PK\x03\x04", "application/zip"))],
        )
        assert zipped.status_code == 400 and "압축" in zipped.json()["detail"]
        pdf = post(
            data={"pledge": "true", "kind": "blog"},
            files=[("files", ("글.pdf", b"%PDF", "application/pdf"))],
        )
        assert pdf.status_code == 400 and "붙여넣어" in pdf.json()["detail"]
        binary = post(
            data={"pledge": "true", "kind": "other"},
            files=[("files", ("x.txt", b"\x00\x01\x02", "text/plain"))],
        )
        assert binary.status_code == 400
        # 메일(.eml)·문자 백업(.xml)·Gmail 전체 백업(.mbox)은 받는다
        ok = post(
            data={"pledge": "true", "kind": "email"},
            files=[
                ("files", ("편지.eml", b"Subject: hi\n\nhello", "message/rfc822")),
                ("files", ("sms.xml", b"<smses><sms body='hi'/></smses>", "text/xml")),
                ("files", ("All mail.mbox", b"From x\nSubject: a\n\nbody", "application/mbox")),
            ],
        )
        assert ok.status_code == 200, ok.text
        assert len(ok.json()["data"]["items"]) == 3

        # 남의 페르소나에는 못 넣고 못 본다
        other = _signup(client, "material-other@example.com")
        assert client.get(f"{API}/personas/{pid}/materials", headers=other).status_code == 404
        item = ok.json()["data"]["items"][0]["id"]
        other_pid = client.post(f"{API}/personas", headers=other, json=BASICS).json()["data"]["id"]
        assert (
            client.delete(f"{API}/personas/{other_pid}/materials/{item}", headers=other).status_code
            == 404
        )

        # 페르소나를 지우면 자료도 지워진다
        assert client.delete(f"{API}/personas/{pid}", headers=h).status_code == 200
        assert not get_storage().exists(f"personas/{pid}/materials/{item}.eml")
