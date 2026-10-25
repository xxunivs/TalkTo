"""약관·안내 0.10 — 2026-10-22 법률 자문 반영(SPEC §12, §12.19).

결제 취소 문구, 대화가 어려우신 분 제외, 살아 계신 분의 그분 동의 녹음, 만 19세 미만, 돌아가신 분의
유족 확인, 처리방침의 새 항목, 새 문서 셋(동의 녹음 대본 둘, 법정대리인 확인과 동의). 대표 답
(2026-10-23): 만 14세 미만은 아직 만들 수 없다는 줄을 두고, Tally 는 적지 않고, 지울 때 알림은
실제로 지운 곳만 '지웠어요'라고 한다. 문서 원문의 글을 정해 두는 시험이라 글을 고치면 여기도 함께
고친다.
"""

import asyncio
import hashlib
import re
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Consent, ConsentRecord, User
from app.db.session import SessionLocal
from app.main import app
from app.services import legal

API = "/api/v1"
# 바뀐 점은 이력이라 예전 낱말이 남는다. 나머지 문서는 지금 쓰는 글이다
ACTIVE = [key for key in legal.docs() if key != "changes"]
SCRIPTS = ("subject_consent_script", "subject_consent_child")
NEW_DOCS = (*SCRIPTS, "guardian_consent")
# 새 대화 상대별 동의 종류(SPEC §12.14)
NEW_PREFIXES = (
    "subject_consent",
    "guardian_attest",
    "guardian_consent",
    "guardian_overseas",
    "guardian_sms",
    "bereaved_pledge",
)
# 대표 답 1(2026-10-23): 만 14세 미만 확인 문자를 보낼 수 있을 때까지(SMS_PROVIDER=off) 막는다
UNDER14_CLOSED = "지금은 준비 중이라 만 14세 미만 대화 상대는 만들 수 없어요"


def _between(text: str, start: str, stop: str) -> str:
    begin = text.index(start)
    end = text.find(stop, begin + len(start))
    return text[begin : end if end != -1 else len(text)]


def _steps(key: str) -> list[tuple[str, str]]:
    """대본을 '## ' 줄마다 (제목, 본문)으로 나눈다(화면과 같은 규칙 — SPEC §5.2)."""
    parts = re.split(r"^## (.+)$", legal.doc(key).body, flags=re.M)
    return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts), 2)]


def _entry() -> str:
    return _between(legal.body("changes"), "(버전 0.10)", "\n## ")


# ────────────────────────── 버전과 다시 동의 ──────────────────────────


def test_versions_match_the_plan():
    # SPEC §0.2. 0.10 은 0.9 다음 판이다(버전은 문자열로만 견준다 — 1.0 은 예전 기본값과 겹친다)
    want = {
        "terms": ("0.10", "consent"),
        "privacy": ("0.10", "confirm"),
        "overseas": ("0.10", "confirm"),
        "voice_data": ("0.10", "consent"),
        "voice_synthesis": ("0.10", "consent"),
        "service": ("0.9", "confirm"),
        "privacy_policy": ("0.10", "notice"),
        "changes": ("0.10", "notice"),
        "payment": ("0.8", "consent"),
        "refund": ("0.8", "notice"),
        "recording_pledge": ("0.5", "pledge"),
        "pledge_living": ("0.5", "pledge"),
        "pledge_deceased": ("0.5", "pledge"),
        "family_share": ("0.10", "consent"),
        "sensitive_health": ("0.6", "consent"),
        "subject_consent_script": ("0.1", "script"),
        "subject_consent_child": ("0.1", "script"),
        "guardian_consent": ("0.1", "consent"),
    }
    assert {key: (legal.doc(key).version, legal.doc(key).kind) for key in want} == want
    assert all(d.version != "1.0" for d in legal.docs().values())
    assert legal.doc("pledge_deceased").title == "돌아가신 분 — 유족 확인"
    assert legal.doc("guardian_consent").title == (
        "만 19세 미만 대화 상대 — 법정대리인 확인과 동의"
    )
    assert legal.doc("subject_consent_script").title == "그분 동의 녹음 대본"
    assert legal.doc("subject_consent_child").title == "그분 동의 녹음 대본 — 만 19세 미만 아이"
    # 새 문서 키는 동의 기록의 doc 칸(40자)에 들어간다
    longest = ConsentRecord.__table__.c.doc.type.length
    assert all(len(key) <= longest for key in NEW_DOCS)


def test_every_account_is_asked_again_for_seven_items():
    # SPEC §0.3: 0.9 에 모두 동의한 계정(초대받은 가족 포함)은 다음 요청에서 일곱 가지를 다시
    # 받는다. 만 14세 이상·'TalkTo는 이런 서비스예요'는 그대로다
    before = {t: "0.9" for t in legal.ACCOUNT_TYPES} | {"age14": legal.DEFAULT_VERSION}
    again = [
        "terms",
        "privacy",
        "overseas",
        "overseas_fish",
        "voice_data",
        "voice_synthesis",
        "overseas_elevenlabs",
    ]
    assert [t for t in legal.ACCOUNT_TYPES if legal.current_version(t) != before[t]] == again

    email = f"legal-010-{uuid.uuid4().hex[:8]}@example.com"
    with TestClient(app) as client:
        signup = client.post(
            f"{API}/auth/signup",
            json={
                "email": email,
                "password": "password123",
                "name": "홍길동",
                "agreements": legal.ACCOUNT_TYPES,
            },
        )
        assert signup.status_code == 201, signup.text
        h = {"Authorization": f"Bearer {signup.json()['data']['bearerToken']}"}
        assert client.get(f"{API}/auth/me", headers=h).json()["data"]["consentsNeeded"] == []

        async def back_to_09():
            async with SessionLocal() as db:
                user = await db.scalar(select(User).where(User.email == email))
                rows = (await db.scalars(select(Consent).where(Consent.user_id == user.id))).all()
                for row in rows:
                    row.version = before[row.consent_type]
                await db.commit()

        asyncio.run(back_to_09())
        assert client.get(f"{API}/auth/me", headers=h).json()["data"]["consentsNeeded"] == again
        blocked = client.get(f"{API}/personas", headers=h)
        assert blocked.status_code == 403 and blocked.json()["missing"] == again


# ────────────────────────── 대화가 어려우신 분 ──────────────────────────


def test_unable_pledge_is_retired_but_kept():
    # WO 2: 새로 만들 수 없다. 문서는 retired/ 에 글 그대로 두고(예전 확인의 증빙), 이름은 잇는다
    assert legal.doc("pledge_unable") is None
    meta, body = legal._parse((legal.RETIRED_DIR / "pledge_unable.md").read_text(encoding="utf-8"))
    assert (meta["key"], meta["title"], meta["version"]) == (
        "pledge_unable",
        "대화가 어려우신 분 — 본인 허락 확인",
        "0.4",
    )
    assert "그분이 직접 설명을 듣고" in body
    # 이미 만든 unable 대화 상대의 voice_clone 거두기 기록은 그 문서의 판(0.4)·해시를 남긴다
    # (기본값 0.3·해시 없음으로 덮지 않게). retired/ 글은 예전 원문과 한 글자도 다르지 않다
    assert legal.version_of("pledge_unable") == "0.4"
    assert (
        legal.text_sha256("pledge_unable")
        == hashlib.sha256(legal.fill(body.strip("\n") + "\n").encode("utf-8")).hexdigest()
    )
    assert legal.body("pledge_unable") is None
    assert legal.current_version("voice_clone:x") == legal.DEFAULT_VERSION

    class Bag(list):
        add = list.append

    withdrawn = legal.log(Bag(), "u", "voice_clone:x", False, doc="pledge_unable")
    assert (withdrawn.version, withdrawn.text_sha256) == (
        "0.4",
        legal.text_sha256("pledge_unable"),
    )
    title = legal.title_of("voice_clone:x", "pledge_unable")
    assert title == legal.RETIRED_DOC_TITLES["pledge_unable"] and "지금은 받지 않아요" in title
    # 살아 있는 문서는 그 문서 이름 그대로, 모르는 문서는 종류 이름
    assert legal.title_of("voice_clone:x", "pledge_living") == "살아 계신 분 — 본인 허락 확인"
    assert legal.title_of("voice_clone:x", "nope") == "그분 목소리 만들기 확인"
    # 이미 만든 unable 대화 상대의 가족 공유 확인은 살아 계신 분과 같은 글로 남는다
    assert legal.SHARE_OWNER_LABELS["unable"] == legal.SHARE_OWNER_LABELS["alive"]
    with TestClient(app) as client:
        keys = {d["key"] for d in client.get(f"{API}/legal").json()["data"]["docs"]}
        assert "pledge_unable" not in keys
        assert client.get(f"{API}/legal/pledge_unable").status_code == 404


def test_unable_and_dementia_wording_is_gone():
    # '대화가 어려우신 분'을 대상으로 말하는 글은 없고, '치매'는 이용약관 2번의 한 줄에만 있다
    for key in ACTIVE:
        assert "대화가 어려우" not in legal.body(key), key
    assert sum(legal.body(key).count("치매") for key in ACTIVE) == 1
    terms = legal.body("terms")
    rule = "- 치매 등으로 스스로 설명을 듣고 허락하시기 어려운 분의 대화 상대는 만들 수 없어요."
    assert rule in _between(terms, "## 2. 대화 상대 만들기", "\n## ")
    assert "(예 / 돌아가셨어요)" in legal.body("sensitive_health")
    # OpenAI 로 보내는 상태는 '해외 회사로 보내는 정보'·처리방침과 같다(만 19세 미만인지 포함)
    assert (
        "OpenAI(미국)에는 돌아가셨는지 살아 계신지(살아 계시면 만 19세 미만인지)만 알려요"
        in legal.body("sensitive_health")
    )


def test_no_new_counseling_word():
    # '상담'은 화면 글에 쓰지 않는다(기억 인터뷰). 이미 있는 두 문장만 남는다
    kept = ("치료·상담 서비스가 아니에요", "자살예방상담전화")
    for key in ACTIVE:
        body = legal.body(key)
        for text in kept:
            body = body.replace(text, "")
        assert "상담" not in body, key
    assert "상담" not in _entry()
    new_texts = [
        *legal.PAYMENT_LABELS.values(),
        legal.PAYMENT_NOTICE,
        *legal.GUARDIAN_LABELS.values(),
        *legal.RETIRED_DOC_TITLES.values(),
        *(legal.PERSONA_TYPES[p][1] for p in NEW_PREFIXES),
    ]
    assert all("상담" not in text for text in new_texts)


# ────────────────────────── 결제 취소와 환불 ──────────────────────────


def test_withdrawal_wording_follows_the_lawyer():
    # WO 1-1~1-5: 맞춤 콘텐츠라는 까닭과 '약속과 다를 때'는 없다
    for key in ("terms", "refund", "payment"):
        body = legal.body(key)
        for word in (
            "맞춤 디지털 콘텐츠",
            "맞춤 콘텐츠",
            "약속과 다를 때",
            "디지털 콘텐츠 제공이 시작된 것이라",
        ):
            assert word not in body, (key, word)
        flat = body.replace("**", "")
        for text in (
            "결제하시면 바로 유료 이용이 시작돼요",
            "단순 변심에 의한 결제 취소(청약철회)가 제한돼요(전자상거래법 제17조 제2항 제5호)",
            "무료 체험을 쓰지 않은 대화 상대",
            "대화하셨더라도",
            "표시·광고나 안내한 내용, 계약 내용과 다르게 드렸을 때",
            "받으신 날부터 3개월 안에, 그 사실을 아셨거나 아실 수 있었던 날부터 30일 안에",
            "(전자상거래법 제17조 제3항)",
            "앱의 '고객센터'(카카오톡 채널)",
            "결제일부터 다음 달 같은 날의 전날까지",
        ):
            assert text in flat, (key, text)
    # 3영업일과 전화 채널(terms·payment 는 이번에 더함, refund 는 원래 있음)
    for key in ("terms", "payment", "refund"):
        assert "3영업일 안에" in legal.body(key), key
        assert legal.fill("전화({{전화}})") in legal.body(key), key
    # 체험 규칙 자체(계정 하나에 대화 상대 n명)는 3-1 에 그대로 있다
    assert (
        "무료 체험은 **계정 하나에 대화 상대 {{trial_personas}}명**이에요"
        in legal.doc("terms").body
    )
    # 해지해도 이미 결제한 기간이 끝날 때까지는 쓸 수 있다(그대로)
    assert "해지해도 이미 결제한 기간이 끝날 때까지는 쓸 수 있고" in legal.body("terms")


def test_payment_labels_are_in_the_payment_document():
    # 결제 직전 체크 두 글은 payment.md 에 한 글자도 다르지 않게 있다(증빙 해시가 체크 글을 덮는다)
    assert legal.PAYMENT_LABELS == {
        "recurring": (
            "매달 같은 날 자동으로 결제되는 것에 동의해요. 언제든 해지할 수 있다는 것을 "
            "안내받았어요."
        ),
        "withdrawal_limit": (
            "결제하면 바로 유료 이용이 시작되고, 결제 후 대화를 이용하면 단순 변심에 의한 "
            "청약철회가 제한된다는 점을 확인했어요(결제 후 7일 안에 대화하지 않았으면 전액 환불, "
            "체험을 쓰지 않은 대화 상대는 첫 결제 후 7일 안 전액 환불, 안내와 다르게 제공된 경우 "
            "환불은 그대로 보장돼요)."
        ),
    }
    payment = legal.body("payment")
    for label in legal.PAYMENT_LABELS.values():
        assert f'- 확인받는 문장: "{label}"' in payment, label
    assert legal.PAYMENT_NOTICE == (
        "결제하시면 바로 유료 이용이 시작돼요. 결제한 뒤 이 대화 상대와 대화를 시작하면(초대한 "
        "가족이 시작해도 같아요) 단순 변심에 의한 결제 취소(청약철회)가 제한돼요. 다만 결제한 "
        "날부터 7일 안에 한 번도 대화하지 않으셨거나, 무료 체험을 쓰지 않은 대화 상대를 처음 "
        "결제한 날부터 7일 안이거나, 표시·광고나 안내와 다르게 드렸을 때는 전액 돌려드려요. "
        "한 번 결제한 이용 기간은 결제일부터 다음 달 같은 날의 전날까지예요."
    )
    # 계약 내용(WO 1-8)을 알림과 받을 수 있는 이메일로 남긴다는 것
    assert "'계약 내용'을 앱의 알림에 남기고" in payment
    # 업체 이름은 쓰지 않는다(결제 화면 글)
    for text in (legal.PAYMENT_NOTICE, *legal.PAYMENT_LABELS.values()):
        for vendor in ("Fish", "ElevenLabs", "OpenAI", "MiniMax"):
            assert vendor not in text


def test_legal_index_carries_payment_and_guardian_labels():
    with TestClient(app) as client:
        index = client.get(f"{API}/legal").json()["data"]
        assert index["payment"] == {
            "notice": legal.PAYMENT_NOTICE,
            "items": [
                {"type": "recurring", "label": legal.PAYMENT_LABELS["recurring"]},
                {"type": "withdrawal_limit", "label": legal.PAYMENT_LABELS["withdrawal_limit"]},
            ],
        }
        assert index["guardian"] == legal.GUARDIAN_LABELS
        keys = {d["key"]: d for d in index["docs"]}
        for key in NEW_DOCS:
            assert key in keys, key
        assert keys["subject_consent_script"]["kind"] == "script"
        script = client.get(f"{API}/legal/subject_consent_script").json()["data"]
        # 서버는 대본의 화면 자리표시를 채우지 않는다
        assert "{{확인 단어}}" in script["body"] and "{{호칭}}" in script["body"]
        child = client.get(f"{API}/legal/subject_consent_child").json()["data"]
        assert "{{보호자}}" in child["body"]


# ────────────────────────── 살아 계신 분 · 만 19세 미만 ──────────────────────────


def test_new_documents_leave_only_screen_slots():
    # 새 문서 셋: 화면이 바꾸는 자리표시만 남고 서버가 아는 것은 모두 채운다
    for key in NEW_DOCS:
        slots = set(re.findall(r"\{\{([^{}]+)\}\}", legal.body(key)))
        assert slots <= {"확인 단어", "호칭", "보호자"}, (key, slots)
    assert {"확인 단어", "호칭"} <= set(re.findall(r"\{\{([^{}]+)\}\}", legal.body(SCRIPTS[0])))
    child = set(re.findall(r"\{\{([^{}]+)\}\}", legal.body(SCRIPTS[1])))
    assert child == {"확인 단어", "호칭", "보호자"}
    assert "{{" not in legal.body("guardian_consent")


def test_consent_scripts_have_their_steps():
    adult = _steps("subject_consent_script")
    child = _steps("subject_consent_child")
    assert len(adult) == 9 and len(child) == 10
    # 단계마다 소리 내어 읽을 글('> ')이 있다. [만 14세 미만]은 아이 대본 2단계에만
    for title, body in adult + child:
        assert any(line.startswith("> ") for line in body.splitlines()), title
    assert [t for t, _ in adult + child if "[만 14세 미만]" in t] == [child[1][0]]
    assert child[1][0].startswith("2. 법정대리인 확인")
    # 첫 문단(단계 앞)은 녹음 전 안내: 동의 녹음은 서울에만 두고 어느 해외 회사로도 보내지 않는다
    for key in SCRIPTS:
        intro = legal.doc(key).body.split("\n## ", 1)[0]
        assert "TalkTo 서버(서울)에만 두고 어떤 해외 회사로도 보내지 않으며" in intro, key
        assert "글로 옮기거나 목소리를 만드는 데 쓰지 않아요" in intro, key

    # 어른: 확인 단어 → 설명 → 미국 회사(무엇을·왜·언제까지, 따로 묻기) → 싫으시면 → 날짜·성함 →
    # 낭독 → 이해 확인 질문 셋 → 그만두는 방법 → 마치기
    abroad = adult[2][1]
    for text in ("Fish Audio", "ElevenLabs", "OpenAI", "지울 때까지"):
        assert text in abroad, text
    assert abroad.strip().splitlines()[-2] == "> 미국에 있는 회사로 보내는 것도 괜찮으세요?"
    assert "{{확인 단어}}" in adult[0][1]
    assert "> 싫으시면 안 하셔도 돼요. 괜찮으세요?" in adult[3][1]
    for question in (
        "> 뭘 만드는 거라고 했죠?",
        "> 싫어지시면 어떻게 하면 돼요?",
        "> 싫으시면 안 하셔도 돼요. 정말 괜찮으세요?",
    ):
        assert question in adult[6][1], question
    assert (
        "AI 목소리" in adult[5][1] and "미국에 있는 회사로 보내지는 것에도 동의해요" in adult[5][1]
    )

    # 아이(만 14~18세도 이 대본 — 아이 본인 동의가 근거라 해외 설명이 빠지면 안 된다)
    for text in (
        "Fish Audio",
        "ElevenLabs",
        "OpenAI",
        "지울 때까지",
        "> 미국에 있는 회사로 보내는 것도 괜찮아?",
    ):
        assert text in child[3][1], text
    assert "AI 목소리" in child[6][1]
    assert "아이가 싫다고 하면 만들지 않아요" in child[4][1]
    for question in (
        "> 뭘 만든다고 했지?",
        "> 싫어지면 어떻게 하면 돼?",
        "> 싫으면 안 해도 돼. 정말 괜찮아?",
    ):
        assert question in child[7][1], question


def test_living_subject_consent_is_disclosed():
    # WO 3-7: 그분 본인(만 14세 미만은 법정대리인)께 따로 받는 국외 이전 동의와, 동의 녹음은 해외로
    # 보내지 않는다는 것
    separate = "살아 계신 분의 정보는 그분 본인(만 14세 미만은 법정대리인)께 따로 동의를 받아요"
    overseas = legal.body("overseas").replace("**", "")
    assert f"{separate}(개인정보 보호법 제28조의8 제1항 제1호)" in overseas
    policy = legal.body("privacy_policy")
    assert f"{separate}(같은 항 제1호)" in _between(policy, "## 5. 해외로 옮기는", "\n## 6.")
    assert (
        "그분 동의 녹음은 TalkTo 서버(서울)에만 두고 어떤 해외 회사로도 보내지 않으며" in overseas
    )
    # Fish Audio: 통과 전에 막는 것은 그분 녹음뿐이다(WO 3-8 — 이미 만든 살아 계신 분은 통과 전에도
    # 대답 글과 목소리 아이디가 가서 목소리로 대답한다)
    fish = _between(overseas, "### Fish Audio (미국)", "\n### ")
    assert (
        "그분 목소리로 고른 녹음 부분(살아 계신 분이면 그분 동의 녹음이 확인된 뒤에만), "
        "대답할 글, 만든 목소리의 아이디\n"
    ) in fish
    assert "살아 계신 분이면 그분 동의 녹음이 확인된 뒤에만)" in _between(
        overseas, "### ElevenLabs (미국)", "\n## "
    )
    assert "만 19세 미만인지(태어난 해·달은 보내지 않아요)" in overseas
    voice_data = legal.body("voice_data")
    section = _between(voice_data, "## 살아 계신 분의 녹음과 그분 동의 녹음", "\n## ")
    assert "그 전에는 녹음을 TalkTo 서버(서울)에 저장만 해요" in section
    assert "해외 회사로 보내지 않고, 목소리를 만드는 데 쓰지 않아요" in section
    # 만든 분 확약은 남기되 동의 녹음을 함께 받는다(pledge_living)
    living = legal.body("pledge_living").replace("**", "")
    assert "그분 동의 녹음을 함께 받아요." in living
    assert "미국에 있는 회사로 보내져(목소리: Fish Audio, 글로 옮기기: ElevenLabs·OpenAI)" in living
    assert "법정대리인(부모)" in living
    pledge = legal.body("recording_pledge").replace("**", "")
    assert (
        "미국에 있는 회사로 보내져" in pledge
        and "그분 동의 녹음이 검수를 통과하기 전에는" in pledge
    )
    synthesis = legal.body("voice_synthesis")
    assert "그분 동의 녹음이 TalkTo의 검수를 통과해야 하고" in synthesis
    assert "가족을 대표하는 분" not in synthesis


def test_minor_rules_in_the_terms():
    # WO 4: 만 14세 미만 금지 줄 대신 만 19세 미만 요건. 가입은 그대로 만 14세 이상
    terms = legal.body("terms")
    assert "만 14세 미만인 분(어린 자녀 등)을 대화 상대로 만드는 일" not in terms
    assert "- 만 14세 이상만 가입할 수 있어요." in terms
    part = _between(terms, "- **만 19세 미만인 분**", "\n- **돌아가신 분**")
    for text in (
        "그 아이의 법정대리인(부모)인 회원만 만들 수 있어요",
        "법정대리인 휴대전화로 보내는 확인 문자",
        UNDER14_CLOSED,
        "아이가 싫다고 하면 나이와 상관없이 만들지 않아요",
        "혼자 친권을 가지고 있다는 것을 확인해 주셔야 해요",
        "그때부터 6개월 안에 받지 못하면 대화를 멈추고 지워요",
        "직계가족(부모·조부모)만 쓸 수 있어요",
        "떠나신 자녀의 대화 상대는 아래 돌아가신 분의 규칙을 따라요",
    ):
        assert text in part, text
    # 만 14세 미만이 닫혀 있다는 줄(대표 답 1): 이용약관·법정대리인 문서·처리방침·바뀐 점
    assert UNDER14_CLOSED in _between(
        legal.body("guardian_consent"), "## 만 14세 미만 — 법정대리인 동의", "\n### "
    )
    assert UNDER14_CLOSED in legal.body("privacy_policy")
    assert "만 14세 미만 아이의 대화 상대는 지금은 준비 중이라 아직 만들 수 없어요" in _entry()
    # 가족 공유 범위(2-1·가족 공유 안내)
    share = "법정대리인과 법정대리인이 초대한 직계가족(부모·조부모)만 함께 쓸 수 있어요"
    assert share in _between(terms, "## 2-1. 가족 공유", "\n## ")
    # 가족 공유 허락은 아이 본인과 법정대리인 둘 다(가족 공유 안내의 살아 계신 분 확인과 같게 —
    # 만 14~18세는 아이 본인의 동의가 근거다)
    assert "만 19세 미만이면 법정대리인," not in terms
    assert "(만 19세 미만이면 법정대리인)" not in terms
    assert (
        "그분 본인(만 19세 미만이면 아이 본인과 법정대리인 모두, 돌아가셨다면 만들 때 적은 명단의 "
        "가족)의 허락 없이 가족 공유를 켜거나"
    ) in _between(terms, "## 2. 대화 상대 만들기", "\n## 2-1.")
    assert (
        "그분 본인이 직접 괜찮다고 하셔야 하고(만 19세 미만이면 아이 본인과 법정대리인 모두)"
        in _between(terms, "## 2-1. 가족 공유", "\n## ")
    )
    owner_check = _between(legal.body("family_share"), "- **살아 계신 분 — 그분 허락**", "\n- ")
    assert "직접 괜찮다고 하셨어요." in owner_check and "법정대리인으로서 확인했어요" in owner_check
    assert (
        "아이의 대화 상대는 법정대리인(부모)과 법정대리인이 초대한 직계가족(부모·조부모)만 함께 "
        "쓸 수 있어요"
    ) in legal.body("family_share")
    assert "만 14세 미만은 가입할 수 없어서 초대를 받아도 들어올 수 없어요" in legal.body(
        "family_share"
    )


def test_guardian_consent_document_and_labels():
    body = legal.body("guardian_consent").replace("**", "")
    assert legal.GUARDIAN_LABELS == {
        "attest": (
            "저는 이 아이의 법정대리인(부모)이에요. 다른 쪽 부모님의 동의를 받았거나 제가 혼자 "
            "친권을 가지고 있어요."
        ),
        "consent": "만 14세 미만인 아이의 정보를 모으고 쓰는 데 법정대리인으로서 동의해요.",
        "overseas": (
            "아이의 정보를 미국의 Fish Audio·ElevenLabs·OpenAI로 보내는 데 법정대리인으로서 "
            "동의해요."
        ),
    }
    # 국외 이전 동의 항목(제28조의8 제2항): 받는 곳·나라·보내는 것·때와 방법·보관·거부와 결과
    abroad = _between(body, "### 2) 아이 정보를 해외 회사로 보내는 데 동의", "\n## ")
    for text in (
        "Hanabi AI, Inc.(Fish Audio, 미국)",
        "Eleven Labs Inc.(미국)",
        "OpenAI OpCo, LLC(미국)",
        "보내는 것:",
        "암호화(HTTPS)",
        "보관:",
        "동의하지 않으실 수 있어요. 동의하지 않으시면 아이의 대화 상대를 만들 수 없어요.",
    ):
        assert text in abroad, text
    assert "개인정보 보호법 제22조의2" in body
    assert body.count("동의하지 않으실 수 있어요.") == 2


def test_new_persona_consent_types():
    # 새 대화 상대별 종류(SPEC §12.14). 문서·이름·버전, 그리고 consent_type 칸(80자)에 들어가는지
    assert {p: legal.PERSONA_TYPES[p] for p in NEW_PREFIXES} == {
        "subject_consent": (None, "그분 동의 녹음 — 검수 통과"),
        "guardian_attest": ("guardian_consent", "법정대리인 확인"),
        "guardian_consent": (
            "guardian_consent",
            "만 14세 미만 아이 정보 수집·이용 — 법정대리인 동의",
        ),
        "guardian_overseas": (
            "guardian_consent",
            "만 14세 미만 아이 정보를 해외 회사로 보내는 데 — 법정대리인 동의",
        ),
        "guardian_sms": ("guardian_consent", "법정대리인 휴대전화 확인 문자"),
        "bereaved_pledge": ("pledge_deceased", "돌아가신 분 — 유족 확인"),
    }
    # 다시 묻는 것은 올리기 전 확인 둘뿐(동의 녹음·유족 확인은 각자의 화면이 정한다)
    assert legal.REASK_ON_CHANGE == {"recording_pledge", "material_pledge"}
    pid = str(uuid.uuid4())
    longest = Consent.__table__.c.consent_type.type.length
    for prefix in NEW_PREFIXES:
        kind = legal.persona_type(prefix, pid)
        assert len(kind) <= longest, kind
        assert legal.default_doc(kind) == legal.PERSONA_TYPES[prefix][0]
    assert legal.current_version(f"guardian_sms:{pid}") == legal.version_of("guardian_consent")
    assert legal.current_version(f"bereaved_pledge:{pid}") == legal.version_of("pledge_deceased")
    # 동의 녹음 통과는 녹음할 때 보인 대본 이름으로, 대본을 모르면 종류 이름으로
    assert legal.title_of(f"subject_consent:{pid}", "subject_consent_child") == (
        "그분 동의 녹음 대본 — 만 19세 미만 아이"
    )
    assert legal.title_of(f"subject_consent:{pid}") == "그분 동의 녹음 — 검수 통과"
    assert legal.title_of(f"guardian_overseas:{pid}") == legal.PERSONA_TYPES["guardian_overseas"][1]


def test_log_keeps_the_script_hash_it_was_given():
    # 검수 통과 기록은 검수하는 날이 아니라 녹음할 때 보인 대본의 판·해시를 남긴다(SPEC §2.6)
    class Bag:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

    bag = Bag()
    pid = str(uuid.uuid4())
    old = "ab" * 32
    row = legal.log(
        bag,
        None,
        legal.persona_type("subject_consent", pid),
        True,
        doc="subject_consent_script",
        version="0.0-old",
        sha256=old,
        channel="admin_review",
    )
    assert bag.rows == [row]
    assert (row.doc, row.version, row.text_sha256, row.persona_id, row.channel) == (
        "subject_consent_script",
        "0.0-old",
        old,
        pid,
        "admin_review",
    )
    # 주지 않으면 지금처럼 지금 문서의 해시
    now = legal.log(bag, None, f"guardian_sms:{pid}", True, doc="guardian_consent", channel="sms")
    assert now.text_sha256 == legal.text_sha256("guardian_consent")
    assert now.version == legal.version_of("guardian_consent")


# ────────────────────────── 돌아가신 분 ──────────────────────────


def test_deceased_rules_and_objections():
    # WO 5: 신청 자격·유족 명단·생전 의사·이의가 오면 바로 멈추고 7일 안에 지움. 서류는 받지 않는다
    pledge = legal.body("pledge_deceased").replace("**", "")
    for text in (
        "고인의 배우자·자녀·손주·부모 가운데 한 분",
        "모두에게 알렸으며 반대하는 분이 없어요",
        "'적을 분이 없어요(모두 돌아가셨어요)'",
        "원하지 않으셨다는 이야기를 들은 적이 없어요",
        "대화가 바로 멈추고 7일 안에 지워진다",
        "이름으로 먼저 알림을 보내지 않아요",
        "사망진단서 같은 서류는 받지 않아요",
        legal.fill(
            "이의는 이메일({{문의 이메일}}), 전화({{전화}}), 앱의 '고객센터'(카카오톡 채널)로"
        ),
    ):
        assert text in pledge, text
    assert "가족을 대표" not in pledge and "멈추거나 지울 수 있다" not in pledge
    terms = legal.body("terms").replace("**", "")
    deceased = _between(terms, "- 돌아가신 분:", "\n- ")
    assert "사망진단서 같은 서류는 받지 않아요" in deceased
    assert "고인의 이름으로 먼저 알림을 보내지 않아요" in deceased
    assert "가족을 대표하는 분" not in terms
    # 고인 이의·그분 철회·다른 쪽 부모의 반대는 '바로 멈추고 7일 안에 지워요'(2번·2-1·처리방침
    # 6번·9번)
    stop = "바로 대화를 멈추고 7일 안에 지워요"
    assert stop in _between(terms, "## 2. 대화 상대 만들기", "\n## 2-1.")
    assert "대화 상대를 바로 멈추고 7일 안에 지워요" in _between(terms, "## 2-1.", "\n## 2-2.")
    policy = legal.body("privacy_policy")
    assert stop in _between(policy, "## 6.", "\n## 7.")
    assert "받으면 바로 대화를 멈추고 7일 안에 지워요" in _between(policy, "## 9.", "\n## 10.")
    share = legal.body("family_share")
    assert "대화 상대가 바로 멈추고 7일 안에 지워진다는 것을 이해해요" in share
    assert (
        "만들 때 적은 유족 명단의 가족 가운데 반대하는 분이 없어요."
        in legal.SHARE_OWNER_LABELS["deceased"]["family_share"]
    )
    # 환불 정책의 '잘못 없이 멈출 때' 예시
    assert (
        "(돌아가신 분의 명단 가족이나 아이의 다른 쪽 부모님이 이의를 보내오신 경우 등)"
        in legal.body("refund")
    )
    # 대표 답 3: 이미 만든 고인 대화 상대는 카드로 부탁만 하고 대화를 막지 않는다
    assert "적으시기 전에도 대화는 지금처럼 하실 수 있어요" in _entry()


# ────────────────────────── 처리방침 ──────────────────────────


def test_privacy_policy_new_items():
    policy = legal.body("privacy_policy")
    assert "회원님과 TalkTo가 정보를 다루는 모든 분(회원이 아닌 분 포함)의 개인정보를" in policy
    purposes = _between(policy, "## 1. 개인정보를 처리하는 목적", "\n## 2.")
    for text in (
        "그분 동의 녹음 검수",
        "법정대리인 확인",
        "유족 확인과 이의 처리",
        "지운 일시 기록",
        "1:1 인터뷰를 요청하는 연락의 관리",
    ):
        assert text in purposes, text
    items = _between(policy, "## 2. 처리하는 개인정보와 보관 기간", "\n## 3.")
    for head in (
        "- **그분 동의 녹음**(그분 본인의 동의 · 제15조 제1항 제1호",
        "- **만 19세 미만인 분의 대화 상대**(만 14세 미만은 법정대리인의 동의 · 제22조의2, "
        "만 14~18세는 아이 본인의 동의 · 제15조 제1항 제1호, 만드신 경우)",
        "- **유족 명단**(만든 분이 적은 고인의 가족 정보 · 정당한 이익 · 제15조 제1항 제6호",
        "- **이의 접수 기록**(정당한 이익",
        "- **인터뷰 요청 연락 기록**(정당한 이익",
        "- **지운 기록**(",
        "- **앱 안 알림**(",
    ):
        assert head in items, head
    # 법무 확인이 필요한 근거·기간은 원문에 표시해 둔다(SPEC §20)
    for head in ("- **그분 동의 녹음**", "- **유족 명단**", "- **이의 접수 기록**"):
        assert "[법무확인필요]" in _between(items, head, "\n- "), head
    interview = _between(items, "- **인터뷰 요청 연락 기록**", "\n- ")
    for text in (
        "이름 또는 필명, 연락한 채널, 연락한 날, 회신 여부",
        "사별 내용이나 고인 정보는 남기지 않아요",
        "캠페인이 끝나고 1개월 안에 지우고",
        "채널 주소만 남겨요",
        "6개월 안에 지우며 목소리를 만드는 데 쓰지 않아요",
    ):
        assert text in interview, text
    assert "확인 문자를 보낸 뒤에는 가린 번호만 남겨요" in items
    assert "결제한 뒤 처음 대화한 때(이용 기간마다, 초대한 가족의 대화 포함)" in items
    third = _between(policy, "## 3. 다른 회사에 넘기는 일", "\n## 4.")
    assert "- **유족 명단의 분께 알림**" in third and "자동으로 보내지 않고" in third
    rights = _between(policy, "## 9.", "\n## 10.")
    assert "(회원이 아니셔도 돼요)" in rights
    assert "- 만 14세 미만은 가입할 수 없어요." in rights
    assert "살아 계신 분의 대화 상대를 가족과 함께 쓰려면" in _between(policy, "## 7.", "\n## 8.")
    history = _between(policy, "## 14.", "\n## ")
    assert "이 처리방침(버전 0.10, 0.9 다음 판)은" in history and "예전 판(버전 0.9)" in history
    # 대표 답 4: Tally 폼은 모두 닫아 위탁 목록에 적지 않는다. 문자 업체도 없다(확인 문자는 대표가)
    for key in legal.docs():
        assert "Tally" not in legal.body(key), key
    assert "문자 업체에 맡기지 않아요" in items
    privacy = legal.body("privacy")
    for head in ("- **그분 동의 녹음**", "- **만 19세 미만인 분의 대화 상대**", "- **유족 명단**"):
        assert head in _between(privacy, "### 대화 상대를 만드는 분", "\n### "), head
    assert "- **앱 안 알림**" in _between(privacy, "### 모든 계정", "\n### ")


def test_deletion_notice_wording_in_documents():
    # WO 7 · 대표 답 2: 실제로 지운 곳만 '지웠어요'. 백업이 남는 날을 알리고 지운 일시를 기록한다
    days = get_settings().deletion_backup_days
    for key in legal.docs():
        assert "맡긴 회사의 운영 시스템에서 지웠어요" not in legal.body(key), key
    privacy = legal.body("privacy")
    assert f"서버 백업에는 최대 {days}일 남았다가 저절로 지워져요" in privacy
    assert (
        "지울 때는 지운 일시를 기록하고, 서버 백업이 지워지는 날을 화면으로 알려 드려요" in privacy
    )
    assert "지운 기록은 지운 날부터 5년" in privacy
    policy = legal.body("privacy_policy")
    assert f"서버 백업이 지워지는 날(지운 날부터 {days}일)" in _between(policy, "## 6.", "\n## 7.")
    terms = legal.body("terms")
    assert "탈퇴를 마치면 지운 일시를 기록하고, 서버 백업에 남는 날짜를 알려 드려요" in terms
    assert "지운 기록(무엇을 언제 지웠는지)은 지운 날부터 5년" in terms


def test_deletion_mail_is_promised_only_when_mail_is_on(monkeypatch):
    # 처리방침 6번: 고객 메일이 꺼져 있으면(SES 샌드박스·USER_MAIL_ENABLED 꺼짐) '이메일로도'를
    # 적지 않는다. 처리방침은 알림 문서라 다시 동의를 받지 않는다
    def notice() -> str:
        return _between(legal.body("privacy_policy"), "## 6.", "\n## 7.")

    assert legal.doc("privacy_policy").kind == "notice"
    monkeypatch.setattr(legal.mailer, "user_mail_configured", lambda: False)
    off = notice()
    days = get_settings().deletion_backup_days
    assert f"서버 백업이 지워지는 날(지운 날부터 {days}일)을 화면으로 알려 드려요." in off
    assert "이메일로도" not in off
    monkeypatch.setattr(legal.mailer, "user_mail_configured", lambda: True)
    assert "을 화면으로(받을 수 있는 이메일이 있으면 이메일로도) 알려 드려요." in notice()


# ────────────────────────── 바뀐 점과 시행일 ──────────────────────────


def test_changes_entry_and_effective_date_agree():
    changes = legal.body("changes")
    day = r"(\d{4}년 \d{1,2}월 \d{1,2}일)"
    head = re.search(rf"^## {day} \(버전 0\.10\) — 법률 자문 반영", changes, re.M)
    assert head and changes.index(head.group(0)) < changes.index("(버전 0.9)")
    terms = re.search(rf"이 약관\(버전 0\.10, 0\.9 다음 판\)은 {day}에", legal.body("terms"))
    policy = re.search(
        rf"이 처리방침\(버전 0\.10, 0\.9 다음 판\)은 {day}부터", legal.body("privacy_policy")
    )
    # 시행일(배포하는 날)은 세 곳이 같아야 한다 — 바꿀 때 함께 바꾼다
    assert terms and policy and head.group(1) == terms.group(1) == policy.group(1)
    assert "결제에 관한 4번·5번의 바뀐 내용은 결제가 열린 뒤부터 적용해요" in legal.body("terms")
    entry = _entry()
    assert "버전 0.10은 0.9 다음 판이에요" in entry
    for key in (
        "terms",
        "privacy",
        "overseas",
        "voice_data",
        "voice_synthesis",
        "privacy_policy",
        "recording_pledge",
        "pledge_living",
        "pledge_deceased",
        "family_share",
        "sensitive_health",
        "payment",
        "refund",
        *NEW_DOCS,
    ):
        assert legal.doc(key).title.split(" (")[0] in entry, key
    assert "대화가 어려우신 분 — 본인 허락 확인**은 더 받지 않아요" in entry
    assert "법률상담" not in changes
