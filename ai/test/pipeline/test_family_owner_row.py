"""가족 공유 — 함께 쓰는 가족의 조립본에 오는 주인 줄(isOwner)·듣는 사람 줄의 isMember 와 가족 표 안내 문장.
가짜 데이터만.

BE 는 isOwner 를 함께 쓰는 가족(멤버) 조립본의 주인 줄에만, isMember 를 그 조립본의 듣는 사람 줄에만 보낸다.
주인 조립본에는 둘 다 없으니 주인 출력은 예전 그대로다(기존 골든: test_persona_prompt·test_persona_assembler·
test_common_rules). 나 자신 대화 상대는 주인 줄이 없어서, 존댓말 문장은 isMember 로만 켜진다.
"""
import unittest

from pydantic import SecretStr

from infra.schema.persona import PersonaAssemblyRequest
from infra.schema.subject import FamilyNote, IntakeContext, SubjectContext
from infra.setting import Settings
from pipeline.persona_prompt.from_intake import spec_from_intake
from pipeline.persona_prompt.render import FAMILY_HEAD, render_layered_prompt
from pipeline.s3_synthesis.c_build_system_prompt import (
    assemble_persona_instructions,
    assemble_persona_prompt,
    family_line,
)
from pipeline.s3_synthesis.common_rules import family_guide

SUBJECT = {"subject": {"name": "김순자", "addressTerm": "할머니"}}
LISTENER = {"name": "홍철수", "relation": "아들", "addressTermTo": "철수야", "addressTermFrom": "엄마",
            "selfReference": "엄마가", "speechLevel": "반말", "talksWithPersona": True, "isListener": True,
            "note": "서울에서 일함"}
OWNER = {"name": "홍지은", "relation": "손녀", "addressTermTo": "우리 강아지", "addressTermFrom": "할머니",
         "selfReference": "할머니가", "speechLevel": "반말", "tone": "다정", "listenerCalls": "지은이",
         "talksWithPersona": True, "isOwner": True}
OTHER = {"name": "홍영희", "relation": "딸", "addressTermTo": "영희야"}
MEMBER_ROWS = [LISTENER, OWNER, OTHER]
POLITE_ROWS = [dict(LISTENER, name="박민호", relation="사위", addressTermTo="박 서방", addressTermFrom="장모님",
                    selfReference=None, speechLevel="존댓말", note=None), OWNER, OTHER]
# 나 자신 대화 상대(그분 = 만든 사람)에 며느리가 들어온 조립본: 주인 줄 없이 듣는 사람 줄에 isMember
SELF_MEMBER = {"name": "이수진", "relation": "며느리", "addressTermTo": "얘야", "addressTermFrom": "아버님",
               "speechLevel": "존댓말", "talksWithPersona": True, "isListener": True, "isMember": True}
SELF_ROWS = [SELF_MEMBER, OTHER]

OWNER_BIT = "이 대화 상대를 만든 가족"
OWNER_SENTENCE = ("가족 표의 부르는 말·상황별 말·실제로 하던 말은 '이 대화 상대를 만든 가족'인 홍지은이 적었다. "
                  "그 속의 애칭이나 옛일은 홍지은의 것이니, 지금 대화하는 사람에게는 그 사람 줄의 부르는 말과 말 높이를 "
                  "쓰고 홍지은과의 옛일을 지금 대화하는 사람과 함께한 일처럼 말하지 않는다.")
POLITE_SENTENCE = "지금 대화하는 사람에게는 예시 문장이 반말로 적혀 있어도 어미를 존댓말로 바꿔 말한다."
LAYERED_OWNER_LINE = ('- 홍지은 (손녀): 이 대화 상대를 만든 가족 / 부르는 말 "우리 강아지" / 이 사람 앞에서 나는 "할머니가"'
                      ' / 이 사람은 나를 "할머니"라고 부름 / 사용자는 "지은이"라고 부름 / 반말 / 톤: 다정')
LEGACY_OWNER_LINE = ('- 홍지은 (손녀) (이 대화 상대를 만든 가족) — 이 사람을 "우리 강아지"라고 부른다: 다정'
                     ' · 사용자는 "지은이"라고 부름')


def _intake(rows) -> IntakeContext:
    return IntakeContext.model_validate(
        {"basicProfile": {"status": "사망"}, "audience": "나와 내가 초대한 사람만", "familyMap": rows})


def _layered(rows) -> str:
    return render_layered_prompt(spec_from_intake(SubjectContext(**SUBJECT), _intake(rows)))


def _legacy(rows) -> str:
    return assemble_persona_prompt(subject_context=SubjectContext(**SUBJECT), persons_results=[],
                                   sensitivity_results=[], segments_by_recording=[], subject_labels=[],
                                   intake_context=_intake(rows))


def _notes(rows) -> list[FamilyNote]:
    return _intake(rows).family_map


def _without_owner_key(rows) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "isOwner"} for r in rows]


def _without_member_key(rows) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "isMember"} for r in rows]


class SchemaTest(unittest.TestCase):
    def test_family_note_accepts_is_owner(self) -> None:
        notes = _notes(MEMBER_ROWS)
        self.assertEqual([n.is_owner for n in notes], [None, True, None])
        self.assertTrue(FamilyNote.model_validate({"name": "홍지은", "is_owner": True}).is_owner)

    def test_old_payload_has_no_owner(self) -> None:
        self.assertTrue(all(n.is_owner is None for n in _notes(_without_owner_key(MEMBER_ROWS))))

    def test_spec_relation_carries_is_owner(self) -> None:
        spec = spec_from_intake(SubjectContext(**SUBJECT), _intake(MEMBER_ROWS))
        self.assertEqual([r.is_owner for r in spec.relations], [None, True, None])

    def test_is_member_is_carried_and_optional(self) -> None:
        self.assertEqual([n.is_member for n in _notes(SELF_ROWS)], [True, None])
        self.assertTrue(all(n.is_member is None for n in _notes(MEMBER_ROWS)))  # 예전 BE 는 보내지 않는다
        spec = spec_from_intake(SubjectContext(**SUBJECT), _intake(SELF_ROWS))
        self.assertEqual([r.is_member for r in spec.relations], [True, None])


class LayeredOwnerRowTest(unittest.TestCase):
    def test_owner_row_bit_comes_first(self) -> None:
        self.assertIn("\n" + LAYERED_OWNER_LINE + "\n", _layered(MEMBER_ROWS))

    def test_listener_and_other_rows_unchanged(self) -> None:
        text = _layered(MEMBER_ROWS)
        self.assertIn('\n- 홍철수 (아들): 지금 대화하는 사람 / 부르는 말 "철수야" / 이 사람 앞에서 나는 "엄마가"'
                      ' / 이 사람은 나를 "엄마"라고 부름 / 반말 / 메모: 서울에서 일함\n', text)
        self.assertIn('\n- 홍영희 (딸): 부르는 말 "영희야"', text)
        self.assertEqual(text.count(OWNER_BIT), 2)  # 주인 줄 한 칸 + 안내 문장 속 따옴표 하나

    def test_guide_line_under_family_head(self) -> None:
        text = _layered(MEMBER_ROWS)
        guide = text[text.index(FAMILY_HEAD) + len(FAMILY_HEAD) + 1:].split("\n")[0]
        self.assertTrue(guide.startswith("아래 가족 중 '지금 대화하는 사람'으로 적힌 사람이 지금 대화하는 상대다."))
        self.assertTrue(guide.endswith(OWNER_SENTENCE))  # 주인 문장은 기존 안내 뒤에 붙는다
        self.assertNotIn(POLITE_SENTENCE, guide)

    def test_polite_listener_gets_polite_sentence(self) -> None:
        text = _layered(POLITE_ROWS)
        guide = text[text.index(FAMILY_HEAD) + len(FAMILY_HEAD) + 1:].split("\n")[0]
        self.assertTrue(guide.endswith(OWNER_SENTENCE + " " + POLITE_SENTENCE))
        self.assertIn('\n- 박민호 (사위): 지금 대화하는 사람 / 부르는 말 "박 서방" / 이 사람은 나를 "장모님"라고 부름'
                      " / 존댓말\n", text)  # 이 칸은 예전 그대로(받침 조사 없음 — 주인 출력이 바뀌지 않게)

    def test_without_owner_row_output_is_exactly_as_before(self) -> None:
        for rows in (MEMBER_ROWS, POLITE_ROWS):
            old = _layered(_without_owner_key(rows))
            self.assertNotIn(OWNER_BIT, old)
            self.assertNotIn(POLITE_SENTENCE, old)
            falsy = [dict(r, isOwner=False) for r in _without_owner_key(rows)]
            self.assertEqual(_layered(falsy), old)
            nulls = [dict(r, isOwner=None) for r in _without_owner_key(rows)]
            self.assertEqual(_layered(nulls), old)

    def test_is_owner_on_listener_row_changes_nothing(self) -> None:
        # 주인 조립본에는 isOwner 가 오지 않지만, 와도 듣는 사람 줄이면 예전 그대로다
        rows = [dict(LISTENER, isOwner=True, speechLevel="존댓말"), OTHER]
        self.assertEqual(_layered(rows), _layered(_without_owner_key(rows)))

    def test_self_persona_member_gets_polite_sentence_only(self) -> None:
        text = _layered(SELF_ROWS)
        guide = text[text.index(FAMILY_HEAD) + len(FAMILY_HEAD) + 1:].split("\n")[0]
        self.assertEqual(guide, "아래 가족 중 '지금 대화하는 사람'으로 적힌 사람이 지금 대화하는 상대다. " + POLITE_SENTENCE)
        self.assertNotIn(OWNER_BIT, text)
        self.assertIn('\n- 이수진 (며느리): 지금 대화하는 사람 / 부르는 말 "얘야" / 이 사람은 나를 "아버님"라고 부름'
                      " / 존댓말\n", text)  # isMember 는 칸을 더하지 않는다

    def test_without_member_flag_output_is_exactly_as_before(self) -> None:
        old = _layered(_without_member_key(SELF_ROWS))
        self.assertNotIn(POLITE_SENTENCE, old)
        for flag in (False, None):
            rows = [dict(r, isMember=flag) for r in _without_member_key(SELF_ROWS)]
            self.assertEqual(_layered(rows), old)
        casual = [dict(SELF_MEMBER, speechLevel="반말"), OTHER]
        self.assertEqual(_layered(casual), _layered(_without_member_key(casual)))


class LegacyOwnerRowTest(unittest.TestCase):
    def test_family_line_marks_owner(self) -> None:
        self.assertEqual(family_line("홍지은", _notes(MEMBER_ROWS)[1]), LEGACY_OWNER_LINE)

    def test_legacy_prompt_has_owner_line_and_guide(self) -> None:
        prompt = _legacy(POLITE_ROWS)
        self.assertIn("\n" + LEGACY_OWNER_LINE + "\n", prompt)
        self.assertIn(OWNER_SENTENCE + " " + POLITE_SENTENCE + "\n- 박민호 (사위) (지금 대화하는 사람)", prompt)

    def test_legacy_without_owner_row_is_exactly_as_before(self) -> None:
        old = _legacy(_without_owner_key(MEMBER_ROWS))
        self.assertNotIn(OWNER_BIT, old)
        self.assertEqual(_legacy([dict(r, isOwner=False) for r in _without_owner_key(MEMBER_ROWS)]), old)

    def test_legacy_self_persona_member(self) -> None:
        prompt = _legacy(SELF_ROWS)
        self.assertIn(POLITE_SENTENCE + "\n- 이수진 (며느리) (지금 대화하는 사람)", prompt)
        self.assertEqual(_legacy([dict(r, isMember=None) for r in _without_member_key(SELF_ROWS)]),
                         _legacy(_without_member_key(SELF_ROWS)))

    def test_deceased_marker_wins_over_owner(self) -> None:
        note = FamilyNote.model_validate({"name": "홍영감", "relation": "남편", "deceased": True, "isOwner": True})
        self.assertEqual(family_line("홍영감", note), "- 홍영감 (남편) (돌아가심)")


class FamilyGuideTest(unittest.TestCase):
    def test_owner_sentence_only_with_owner_row(self) -> None:
        self.assertIn(OWNER_SENTENCE, family_guide(_notes(MEMBER_ROWS)))
        self.assertNotIn("이 대화 상대를 만든 가족", family_guide(_notes(_without_owner_key(MEMBER_ROWS))))
        self.assertEqual(family_guide([]), "")

    def test_polite_sentence_needs_polite_listener_and_owner_row(self) -> None:
        self.assertIn(POLITE_SENTENCE, family_guide(_notes(POLITE_ROWS)))
        self.assertNotIn(POLITE_SENTENCE, family_guide(_notes(MEMBER_ROWS)))  # 듣는 사람 반말
        self.assertNotIn(POLITE_SENTENCE, family_guide(_notes(_without_owner_key(POLITE_ROWS))))  # 주인 줄 없음
        no_level = [dict(POLITE_ROWS[0], speechLevel=None), *POLITE_ROWS[1:]]
        self.assertNotIn(POLITE_SENTENCE, family_guide(_notes(no_level)))
        no_listener = [r for r in POLITE_ROWS if not r.get("isListener")]
        self.assertNotIn(POLITE_SENTENCE, family_guide(_notes(no_listener)))

    def test_member_flag_turns_on_polite_sentence_without_owner_row(self) -> None:
        self.assertTrue(family_guide(_notes(SELF_ROWS)).endswith(POLITE_SENTENCE))
        self.assertNotIn(POLITE_SENTENCE, family_guide(_notes([dict(SELF_MEMBER, speechLevel="반말"), OTHER])))
        self.assertNotIn(POLITE_SENTENCE, family_guide(_notes([dict(SELF_MEMBER, isMember=False), OTHER])))
        # isMember 는 듣는 사람 줄에서만 본다
        stray = [_without_member_key([SELF_MEMBER])[0], dict(OTHER, isMember=True)]
        self.assertNotIn(POLITE_SENTENCE, family_guide(_notes(stray)))
        # 주인 줄도 있으면(나 자신이 아닌 대화 상대) 문장은 한 번만
        both = [dict(POLITE_ROWS[0], isMember=True), *POLITE_ROWS[1:]]
        self.assertEqual(family_guide(_notes(both)), family_guide(_notes(POLITE_ROWS)))
        self.assertEqual(family_guide(_notes(both)).count(POLITE_SENTENCE), 1)

    def test_owner_on_listener_row_is_not_an_owner_row(self) -> None:
        rows = [dict(LISTENER, isOwner=True, speechLevel="존댓말"), OTHER]
        self.assertEqual(family_guide(_notes(rows)), family_guide(_notes(_without_owner_key(rows))))

    def test_particles_follow_last_syllable(self) -> None:
        rows = [LISTENER, dict(OWNER, name="김영수")]
        guide = family_guide(_notes(rows))
        self.assertIn("만든 가족'인 김영수가 적었다.", guide)
        self.assertIn("김영수와의 옛일을", guide)
        self.assertIn("만든 가족'인 홍지은이 적었다.", family_guide(_notes(MEMBER_ROWS)))

    def test_layered_relations_and_family_notes_agree(self) -> None:
        spec = spec_from_intake(SubjectContext(**SUBJECT), _intake(POLITE_ROWS))
        self.assertEqual(family_guide(spec.relations), family_guide(_notes(POLITE_ROWS)))


class AssemblyFlagTest(unittest.TestCase):
    def test_both_builders_render_the_owner_row(self) -> None:
        req = PersonaAssemblyRequest.model_validate(
            {"subjectContext": SUBJECT,
             "intakeContext": {"basicProfile": {"status": "사망"}, "audience": "나와 내가 초대한 사람만",
                               "familyMap": POLITE_ROWS}})
        s = Settings(openai_api_key=SecretStr("sk-test"))
        legacy = assemble_persona_instructions(req, s).instructions
        layered = assemble_persona_instructions(req, s.model_copy(update={"persona_prompt_builder": "layered"}))
        self.assertIn(LEGACY_OWNER_LINE, legacy)
        self.assertIn(LAYERED_OWNER_LINE, layered.instructions)
        for text in (legacy, layered.instructions):
            self.assertIn(OWNER_SENTENCE + " " + POLITE_SENTENCE, text)


if __name__ == "__main__":
    unittest.main()
