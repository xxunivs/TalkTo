"""tools/export_manual_persona.py — 지어낸 자료로 묶음 모양·지침 조립·쓰기 막기를 본다(가족 자료를 쓰지 않는다)."""
import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.s3_synthesis.common_rules import DEATH_POLICIES, render_rules

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("export_manual_persona",
                                               REPO / "tools" / "export_manual_persona.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

# 전부 지어낸 글이다.
PROMPT_LINES = [
    "당신은 지어낸 인물 '시험 할머니'의 페르소나다.",
    "",
    "# 가족 목록",
    "- 시험 손자: 바다",
    "- 시험 손녀: 하늘",
    "[장기기억]은 참고만 하고, [tags: sensitive] 는 물을 때만 쓴다.",
]
PROMPT_LF = "\n".join(PROMPT_LINES) + "\n"
MEMORIES = [
    {"id": "food_02", "category": "요리", "title": "시험 국수", "content": "지어낸 국수 이야기."},
    {"id": "place_01", "category": "장소", "title": "시험 바닷가", "content": "지어낸 바닷가 이야기.",
     "tags": ["sensitive"]},
    {"id": "food_01", "category": "요리", "title": "시험 떡", "content": "지어낸 떡 이야기."},
    {"id": "walk_01", "category": "생활", "title": "시험 산책", "content": "지어낸 산책 이야기.", "tags": []},
]
VECTORS = [{"id": m["id"], "embedding": [0.1 * i, 0.2, 0.3, 0.4]} for i, m in enumerate(MEMORIES, 1)]
VOICE = "0123456789abcdef0123456789abcdef"
ANSWER = "그렇게 됐다"


class ExportManualPersonaTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.src = self.root / "sample_manual"
        (self.src / "s3_synthesis").mkdir(parents=True)
        (self.src / "s2_extraction").mkdir(parents=True)
        self.prompt_bytes = PROMPT_LF.replace("\n", "\r\n").encode("utf-8")
        self.prompt_path = self.src / "s3_synthesis" / "c_system_prompt.txt"
        self.prompt_path.write_bytes(self.prompt_bytes)
        self.write_memories(MEMORIES)
        (self.src / "s3_synthesis" / "d_memory_embedding.json").write_text(json.dumps(VECTORS), encoding="utf-8")
        self.voice_file = self.root / "voice.txt"
        self.voice_file.write_text(VOICE + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_memories(self, items) -> None:
        (self.src / "s2_extraction" / "e_recording_memory.json").write_text(
            json.dumps(items, ensure_ascii=False), encoding="utf-8")

    def bundle(self, **kw) -> dict:
        kw.setdefault("voice_id_file", self.voice_file)
        return tool.build_bundle(self.src, built_at="2026-10-17T00:00:00Z", provenance=False, **kw)

    def test_bundle_shape(self) -> None:
        b = self.bundle()
        self.assertEqual((b["format"], b["version"]), ("talkto-persona-bundle", 1))
        self.assertEqual(b["persona"], {"name": "sample", "relation": "할머니", "addressTerm": "할머니",
                                        "status": "deceased"})
        # CRLF → LF 말고는 그대로, sha 는 LF 글 기준
        self.assertEqual(b["prompt"]["legacy"], PROMPT_LF)
        self.assertNotIn("\r", b["prompt"]["legacy"])
        self.assertEqual(b["source"]["promptSha256"], hashlib.sha256(PROMPT_LF.encode("utf-8")).hexdigest())
        self.assertEqual(b["source"]["subject"], "sample_manual")
        self.assertEqual(b["source"]["promptRev"], "disk")
        self.assertIsNone(b["source"]["toolCommit"])
        # 기억: 순서·제목 그대로(표시는 BE 가 붙임), sensitive 는 불리언
        self.assertEqual([m["legacyId"] for m in b["memories"]], [m["id"] for m in MEMORIES])
        self.assertEqual([m["title"] for m in b["memories"]], [m["title"] for m in MEMORIES])
        self.assertEqual([m["sensitive"] for m in b["memories"]], [False, True, False, False])
        self.assertTrue(all(set(m) == {"legacyId", "category", "title", "content", "sensitive"}
                            for m in b["memories"]))
        # 확인용 벡터: legacyId 순 앞 3건
        self.assertEqual([p["legacyId"] for p in b["probeEmbeddings"]], ["food_01", "food_02", "place_01"])
        self.assertEqual(b["probeEmbeddings"][0]["embedding"], VECTORS[2]["embedding"])
        self.assertEqual(b["voice"], {"voiceId": "fish_" + VOICE})
        # 원본은 읽기만 한다
        self.assertEqual(self.prompt_path.read_bytes(), self.prompt_bytes)

    def test_overrides_and_optional_parts(self) -> None:
        (self.src / "s3_synthesis" / "d_memory_embedding.json").unlink()
        b = self.bundle(voice_id_file=None, name="다른 이름", relation="외할머니", address_term="외할매")
        self.assertEqual(b["persona"]["name"], "다른 이름")
        self.assertEqual((b["persona"]["relation"], b["persona"]["addressTerm"]), ("외할머니", "외할매"))
        self.assertEqual(b["probeEmbeddings"], [])
        self.assertIsNone(b["voice"])
        self.assertIn("probes     0 (only the old list-form", "\n".join(tool.summary(b)))

    def test_embedding_file_shapes(self) -> None:
        # a_chat.load_embeddings 가 읽는 모양은 다 받는다. 확인용으로 싣는 것은 옛 목록 모양뿐이다.
        path = self.src / "s3_synthesis" / "d_memory_embedding.json"
        by_id = {v["id"]: v["embedding"] for v in VECTORS}
        for name, raw in [("pipeline", {"embeddings": VECTORS}), ("id map", by_id), ("empty pipeline", {})]:
            with self.subTest(name):
                path.write_text(json.dumps(raw), encoding="utf-8")
                self.assertEqual(self.bundle()["probeEmbeddings"], [])
        for name, raw in [("bad embeddings", {"embeddings": "x"}), ("scalar", "x"),
                          ("too long", [{"id": "food_01", "embedding": [0.1] * 4097}])]:
            with self.subTest(name):
                path.write_text(json.dumps(raw), encoding="utf-8")
                with self.assertRaises(tool.BundleError):
                    self.bundle()

    def test_rules_come_from_rule_code(self) -> None:
        prompt = self.bundle()["prompt"]
        self.assertEqual(prompt["rules"].count("{{death_rule}}"), 1)
        self.assertEqual(set(prompt["deathRules"]), set(DEATH_POLICIES))
        for policy in DEATH_POLICIES:
            with self.subTest(policy):
                expected = render_rules("layered", public=False, deceased=True, has_family=True,
                                        death_policy=policy,
                                        vars={"family_list": "아래 가족 목록", "death_answer": ANSWER})
                text = tool.compose_instructions(prompt, death_policy=policy, death_answer=ANSWER)
                self.assertTrue(text.startswith(expected + "\n\n"))
                self.assertIn("아래 가족 목록", expected)
                self.assertNotIn("위 가족 목록", expected)

    def test_empty_answer_drops_the_quoted_clause(self) -> None:
        prompt = self.bundle()["prompt"]
        for policy in DEATH_POLICIES:
            with self.subTest(policy):
                plain = tool.compose_instructions(prompt, death_policy=policy)
                answered = tool.compose_instructions(prompt, death_policy=policy, death_answer=ANSWER)
                self.assertNotIn("{{", plain)
                self.assertNotIn('""', plain)
                if "{{death_answer}}" in prompt["deathRules"][policy]:
                    self.assertIn(f'"{ANSWER}" 정도로', answered)
                    self.assertNotIn(ANSWER, plain)
                else:
                    self.assertEqual(plain, answered)

    def test_guarded_order_listener_and_verbatim(self) -> None:
        prompt = self.bundle()["prompt"]
        text = tool.compose_instructions(prompt)
        self.assertTrue(text.endswith("\n\n" + PROMPT_LF))
        rules_at = text.index("# 안전 가드레일 (최우선)")
        bridge_at = text.index("# 아래 페르소나 설명과 함께 쓰는 법")
        self.assertLess(text.index("# 대화 흐름 원칙"), rules_at)
        self.assertLess(rules_at, bridge_at)
        self.assertLess(bridge_at, text.index(PROMPT_LINES[0]))
        self.assertIn("'(물으면만)'", prompt["bridge"])
        # 옛 글의 사인 답·예시보다 위 사망 규칙이 앞선다고 짚는다(옛 글은 고치지 않는다)
        self.assertIn("돌아가신 경위·사인·임종을 물으면", prompt["bridge"])
        self.assertTrue(prompt["bridge"].endswith("\n- {{listener_line}}"))
        # 대화하는 사람이 없으면 그 줄째 없이, 있으면 옛 본문 바로 앞에 한 항목으로
        self.assertNotIn("지금 대화하는 사람", text)
        self.assertNotIn("\n- \n", text)
        line = "지금 대화하는 사람은 아래 가족 목록의 '바다'이다."
        with_listener = tool.compose_instructions(prompt, listener_line=line)
        self.assertIn("\n- " + line + "\n\n" + PROMPT_LINES[0], with_listener)
        self.assertEqual(tool.compose_instructions(prompt, mode="verbatim"), PROMPT_LF)
        with self.assertRaises(tool.BundleError):
            tool.compose_instructions(prompt, death_answer="{{x}}")
        # BE 처럼 한마디의 공백·줄바꿈은 한 칸으로 줄인다
        spaced = tool.compose_instructions(prompt, death_answer="  그렇게\n 됐다 ")
        self.assertEqual(spaced, tool.compose_instructions(prompt, death_answer=ANSWER))

    def test_rejects_bad_sources(self) -> None:
        self.prompt_path.write_text("앞 {{빈칸}} 뒤\n", encoding="utf-8")
        with self.assertRaises(tool.BundleError):
            self.bundle()
        self.prompt_path.write_bytes(self.prompt_bytes)
        bad_sets = [
            [{**MEMORIES[0], "tags": ["private"]}],
            [MEMORIES[0], {**MEMORIES[2], "id": MEMORIES[0]["id"]}],
            [{**MEMORIES[0], "usageScope": "자유"}],
            [{**MEMORIES[0], "content": "  "}],
            [{**MEMORIES[0], "title": "가" * 101}],
            [],
        ]
        for items in bad_sets:
            with self.subTest(items=len(items)):
                self.write_memories(items)
                with self.assertRaises(tool.BundleError):
                    self.bundle()
        self.write_memories(MEMORIES)
        self.voice_file.write_text("not-a-model-id", encoding="utf-8")
        with self.assertRaises(tool.BundleError):
            self.bundle()

    def test_write_refuses_repo_and_source_folder(self) -> None:
        b = self.bundle()
        inside_repo = REPO / "tools" / "should_not_be_written.json"
        with self.assertRaises(tool.BundleError):
            tool.write_bundle(b, inside_repo, src=self.src)
        self.assertFalse(inside_repo.exists())
        with self.assertRaises(tool.BundleError):
            tool.write_bundle(b, self.src / "bundle.json", src=self.src)
        self.assertFalse((self.src / "bundle.json").exists())

    def test_main_writes_outside_and_prints_counts_only(self) -> None:
        out = self.root / "export" / "bundle.json"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = tool.main(["--src", str(self.src), "--out", str(out), "--voice-id-file", str(self.voice_file)])
        self.assertEqual(code, 0)
        written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(written["memories"]), 4)
        self.assertFalse(out.with_name(out.name + ".tmp").exists())
        printed = buf.getvalue()
        self.assertIn("memories   4 (sensitive 1", printed)
        self.assertIn(hashlib.sha256(out.read_bytes()).hexdigest(), printed)
        for secret in [VOICE, *PROMPT_LINES[0:1], *PROMPT_LINES[3:]]:
            self.assertNotIn(secret, printed)
        for m in MEMORIES:
            for key in ("id", "title", "content"):
                self.assertNotIn(m[key], printed)
        self.assertEqual(self.prompt_path.read_bytes(), self.prompt_bytes)


if __name__ == "__main__":
    unittest.main()
