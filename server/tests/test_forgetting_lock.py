"""고인 대화 상대는 잊었다고 하지 않는다(잠금) — app/services/forgetting.py.

- 고친 말투 예시(corrections.style_examples)와 만들 때 질문 M8-08 '모르는 걸 물으면' 답
  (intake.build_intake)에서 잊었다는 말이 든 것은 고인 대화 상대에게 보내지 않는다.
- 잊지 말라는 당부·잊지 않았다는 말은 잡지 않는다. 살아 계신 분은 그대로다.

모든 자료는 지어낸 시험용이다.
"""

import pytest
from fastapi.testclient import TestClient
from test_persona_setup import BASICS
from test_sharing import OWNER_BASICS, _db, _persona, _signup

from app.db.models import StyleExample
from app.main import app
from app.services import corrections, forgetting, intake

FORGOT = [
    "기억이 안 나",
    "기억이 이젠 잘 안 나",
    "생각이 이젠 잘 안 나네",
    "기억이 이제는 안 나",
    "기억이 요즘 통 안 나네",
    "기억이 다 안 나",
    "그건 기억에 없구나",
    "기억이 없어",
    "깜빡했다",
    "깜박했어",
    "다 잊어서 그래",
    "다 잊은 지 오래다",
    "가물거려",
    "글쎄, 기억이 잘 안 나네.",
    "기억 안 난다",
    "기억이 하나도 안 났어",
    "기억이 나지 않는구나",
    "기억이 잘 나질 않아",
    "기억을 못 하겠다",
    "기억 못해",
    "생각이 잘 안 나네",
    "생각 안 난다",
    "생각이 나지 않아",
    "아이고, 그건 다 까먹었다",
    "그동안 까먹었네",
    "그걸 잊어버렸구나",
    "다 잊어 버렸어",
    "잊어먹었지 뭐",
    "다 잊었다",
    "다 잊혀졌어",
    "가물가물하다",
    "내가 그걸 까먹었지 말이야",
    "까먹지 말라더니 다 까먹었네",
]
NOT_FORGOT = [
    # 당부(잊지 마라)
    "잊지 마라",
    "잊지 말고 밥 챙겨 먹어",
    "잊어버리지 마라",
    "까먹지 마라",
    "까먹지 말고 챙겨 먹어",
    "잊어먹지 말거라",
    "잊어버리지 마세요",
    "잊어버리면 안 된다",
    "까먹으면 안 돼",
    "속상한 건 다 잊어버려라",
    "까먹을라, 적어 둬라",
    "다 잊고 푹 쉬어라",
    # 잊지 않았다는 말
    "안 까먹었다",
    "안까먹어",
    "절대 안 잊어버리지",
    "못 잊어",
    "그 맛은 못 잊었지",
    "잊혀지지 않는다",
    "그날이 잊혀지질 않아",
    "잊어버린 적 없다",
    "까먹은 적이 없지",
    "잊을 수가 없지",
    "네 생각이 안 나는 날이 없다",
    # 모른다·기억난다는 잊은 말이 아니다
    "그건 내가 잘 모르겠구나",
    "글쎄다, 나는 모르겠다",
    "기억나는구나",
    "생각이 나네",
    "왔냐, 우리 강아지.",
    "",
    None,
]


@pytest.mark.parametrize("text", FORGOT)
def test_forgetting_phrases_are_caught(text):
    assert forgetting.says_forgot(text), text


@pytest.mark.parametrize("text", NOT_FORGOT)
def test_reminders_and_remembering_are_not_caught(text):
    assert not forgetting.says_forgot(text), text


def test_deceased_style_examples_drop_every_forgetting_form():
    kept = ["왔냐, 우리 강아지.", "잊지 말고 밥 챙겨 먹어라."]
    dropped = ["아이고, 다 까먹었다.", "그건 잊어버렸구나.", "생각이 잘 안 나네.", "가물가물하다."]
    with TestClient(app) as client:
        _, owner_id, _ = _signup(client, "김지은")
        deceased = _persona(owner_id)
        living = _persona(owner_id, basics={**OWNER_BASICS, "status": "alive"})

        async def seed(db):
            for pid in (deceased, living):
                for line in [*dropped, *kept]:
                    db.add(StyleExample(persona_id=pid, original="그래.", corrected=line))
            await db.commit()

        _db(seed)

        def sent(pid: str, *, dead: bool) -> set[str]:
            async def go(db):
                return await corrections.style_examples(db, pid, deceased=dead)

            return {row["corrected"] for row in _db(go)}

        assert sent(deceased, dead=True) == set(kept)
        # 살아 계신 분은 그대로
        assert sent(living, dead=False) == {*dropped, *kept}


V2 = intake.with_version(intake.validate_basics(BASICS))
M8_08 = "모르는 것을 물으면 (모른다고 할 때는 늘 이 말투로)"


def _m8_08(basics: dict, phrase: str) -> list[str]:
    reactions = intake.build_intake(basics, {"M8-08": phrase})["intakeContext"][
        "situationalReactions"
    ]
    return [r["response"] for r in reactions if r["situation"] == M8_08]


def test_deceased_m8_08_forgetting_phrase_is_not_sent():
    assert V2["status"] == "deceased"
    for phrase in ("아이고, 기억이 잘 안 나네", "그건 다 까먹었다", "잊어버렸구나"):
        assert _m8_08(V2, phrase) == [], phrase
    # 모른다는 말(보기 글과 같은 것)은 그대로 간다
    assert _m8_08(V2, "그건 내가 잘 모르겠구나") == ["그건 내가 잘 모르겠구나"]
    # 살아 계신 분은 그대로
    for status in ("alive", "unable"):
        living = {**V2, "status": status}
        assert _m8_08(living, "기억이 잘 안 나네") == ["기억이 잘 안 나네"]
