"""말투 덧붙임(STYLE_LATE_NOTE): 넣는 범위와 자리 — 실제 API 호출 없음."""
import unittest
from unittest.mock import patch

from pydantic import SecretStr

from infra.schema.persona import PersonaResponseRequest
from infra.setting import Settings
from orchestrator.contracts import MemorySelection, PrecheckResult, SessionState, Turn, TurnInput
from orchestrator.service import build_assembler
from pipeline.s3_synthesis.c_build_system_prompt import STYLE_LATE_NOTE, generate_persona_response, late_style_note
from test.pipeline.test_memory_block import _mock_openai

SHORT = "# 말투\n- 기본은 1~2문장으로 짧게. 상담사처럼 풀어 설명하지 않고, 매 턴 되묻지 않는다."
LONG = "# 말투\n- 끊어서 짧게 답하지 않는다. 예를 들어가며 자세히 말하되 훈계하지 않고, 매 턴 되묻지 않는다."
CARD = "# 말투 카드 (실제 통화 전사에서 자동으로 잰 것)\n- 한 문장 평균 13자 안팎으로 짧게 말한다."
WITH = f"당신은 김가상이다.\n\n{SHORT}\n\n{CARD}"


def _request(instructions: str) -> PersonaResponseRequest:
    return PersonaResponseRequest.model_validate({
        "message": "밥 먹었어?", "memories": [], "memoryTotal": 300,  # 기억 많은 페르소나(기억 적음 절 없음)
        "history": [{"role": "user", "content": "할머니"}, {"role": "assistant", "content": "어, 왜."}],
        "persona": {"subjectId": "s", "instructions": instructions},
    })


def _sent(settings: Settings, instructions: str) -> list[dict]:
    with patch("infra.external_api.openai_client.OpenAI") as cls:
        cls.return_value = _mock_openai()
        generate_persona_response(_request(instructions), settings=settings)
        return cls.return_value.chat.completions.create.call_args.kwargs["messages"]


class LateStyleNoteTest(unittest.TestCase):
    def test_only_card_and_short_style(self) -> None:
        self.assertEqual(late_style_note(WITH), STYLE_LATE_NOTE)
        self.assertEqual(late_style_note(f"당신은 김가상이다.\n\n{SHORT}"), "")  # 카드 없음
        self.assertEqual(late_style_note(f"당신은 김가상이다.\n\n{LONG}\n\n{CARD}"), "")  # 말이 긴 페르소나
        self.assertNotIn("714", STYLE_LATE_NOTE)  # 실험 대상자 한 사람의 숫자는 뺐다

    def test_not_for_polite_listener_or_visitors(self) -> None:
        # 덧붙임 예시("어.", "응.", "뭐?")는 반말이다. 가족 공유에서 존댓말로 대하는 사위가 대화하거나
        # 공개 본인 페르소나(방문자에게 존댓말)면 넣지 않는다. 반말 듣는 사람(아들)은 그대로 넣는다.
        from infra.schema.persona import PersonaAssemblyRequest
        from pipeline.s3_synthesis.c_build_system_prompt import assemble_persona_instructions
        from test.pipeline.test_family_owner_row import MEMBER_ROWS, POLITE_ROWS, SUBJECT

        utts = ["어, 그래. 밥은 먹었어?", "그러니까 조금만 넣어.", "어, 알았다."] * 14

        def built(rows) -> str:
            req = PersonaAssemblyRequest.model_validate({
                "subjectContext": SUBJECT, "speechExamples": utts,
                "intakeContext": {"basicProfile": {"status": "사망"}, "audience": "나와 내가 초대한 사람만",
                                  "familyMap": rows}})
            return assemble_persona_instructions(req, Settings(openai_api_key=SecretStr("k"))).instructions

        casual, polite = built(MEMBER_ROWS), built(POLITE_ROWS)
        for text in (casual, polite):  # 둘 다 실험 범위의 글자(카드 + 짧은 말투)는 있다
            self.assertIn("# 말투 카드", text)
            self.assertIn("- 기본은 1~2문장으로 짧게.", text)
        self.assertEqual(late_style_note(casual), STYLE_LATE_NOTE)
        self.assertEqual(late_style_note(polite), "")
        visitors = ("당신은 김공개 본인의 말투로 말하는 페르소나다.\n\n# 방문자 (누가 올지 모른다)\n"
                    "- 처음 보는 사람에게 하듯 존댓말로 말한다.\n\n" + SHORT + "\n\n" + CARD)
        self.assertEqual(late_style_note(visitors), "")
        # 기존 경로와 중간계층 조립기도 같은 판단을 쓴다
        sent = _sent(Settings(openai_api_key=SecretStr("k")), polite)
        self.assertNotIn(STYLE_LATE_NOTE, [m["content"] for m in sent])
        turn = TurnInput(subject_id="s", message="밥 먹었어?", persona_instructions=polite)
        args = (SessionState(session_id=None, subject_id="s"), MemorySelection(memories=[], strategy="test"), PrecheckResult())
        for kind in ("legacy", "structured"):
            secs = build_assembler(Settings(openai_api_key=SecretStr("k")), kind).assemble(turn, *args).sections
            self.assertNotIn(STYLE_LATE_NOTE, [s.content for s in secs])

    def test_default_on_right_before_last_message(self) -> None:
        self.assertEqual(Settings().style_late_note, "on")
        msgs = _sent(Settings(openai_api_key=SecretStr("k")), WITH)
        self.assertEqual(msgs[-2], {"role": "system", "content": STYLE_LATE_NOTE})
        self.assertEqual(msgs[-1], {"role": "user", "content": "밥 먹었어?"})
        self.assertEqual(msgs[-3], {"role": "assistant", "content": "어, 왜."})  # 앞 대화 뒤

    def test_off_or_out_of_scope_leaves_messages_as_before(self) -> None:
        off = _sent(Settings(openai_api_key=SecretStr("k"), style_late_note="off"), WITH)
        plain = _sent(Settings(openai_api_key=SecretStr("k")), f"당신은 김가상이다.\n\n{SHORT}")
        for msgs in (off, plain):
            self.assertNotIn(STYLE_LATE_NOTE, [m["content"] for m in msgs])

    def test_orchestrator_assemblers_same_place(self) -> None:
        turn = TurnInput(subject_id="s", message="밥 먹었어?", persona_instructions=WITH, memory_total=300,
                         history=[Turn(role="user", content="할머니"), Turn(role="assistant", content="어, 왜.")])
        args = (SessionState(session_id=None, subject_id="s"), MemorySelection(memories=[], strategy="test"), PrecheckResult())
        for kind in ("legacy", "structured"):
            with self.subTest(kind):
                a = build_assembler(Settings(openai_api_key=SecretStr("k")), kind)
                secs = a.assemble(turn, *args).sections
                self.assertEqual((secs[-2].role, secs[-2].content), ("system", STYLE_LATE_NOTE))
                self.assertEqual((secs[-3].role, secs[-1].content), ("assistant", "밥 먹었어?"))
                off = build_assembler(Settings(openai_api_key=SecretStr("k"), style_late_note="off"), kind)
                secs = off.assemble(turn, *args).sections
                self.assertNotIn(STYLE_LATE_NOTE, [s.content for s in secs])

    def test_fixed_prompt_keeps_mvp_messages(self) -> None:
        # 가져온 지침(fixedPrompt)은 4층 생성기 글이 아니다: 카드·짧은 말투 글자가 있어도 덧붙임을 넣지 않고
        # 지침 + 기억 + 대화만 보낸다(중간계층을 켜도 같은 기존 경로)
        from fastapi.testclient import TestClient

        from infra.setting import get_settings
        from main import app

        body = _request(WITH).model_dump(by_alias=True)
        body["persona"]["fixedPrompt"] = True
        app.dependency_overrides[get_settings] = lambda: Settings(
            openai_api_key=SecretStr("k"), persona_orchestrator=True, orchestrator_assembler="structured")
        self.addCleanup(app.dependency_overrides.pop, get_settings, None)
        with patch("infra.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai()
            r = TestClient(app).post("/v1/persona/responses", json=body)
            msgs = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
        self.assertEqual(r.status_code, 200)
        self.assertEqual(msgs, [{"role": "system", "content": WITH},
                                {"role": "user", "content": "할머니"}, {"role": "assistant", "content": "어, 왜."},
                                {"role": "user", "content": "밥 먹었어?"}])


if __name__ == "__main__":
    unittest.main()
