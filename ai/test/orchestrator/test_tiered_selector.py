"""선택기 TieredSelector — 실제 API 호출 없음, 가짜 데이터."""
import unittest
from unittest.mock import patch

from pydantic import SecretStr

from app.schema.persona import PersonaResponseRequest
from app.setting import Settings
from orchestrator.contracts import MemoryItem, PersonaProfile, SessionState, TurnInput
from orchestrator.memory import (
    CachedEmbedder,
    EmbedOnDemandVectors,
    RequestMemoryStore,
    TieredSelector,
)
from orchestrator.service import build_selector, turn_from_request


# 2차원 가짜 임베딩: '고향' 계열 → x 축, '회사' 계열 → y 축, 나머지 → 대각선 반대
def fake_vec(text: str) -> list[float]:
    if "고향" in text or "남원" in text:
        return [1.0, 0.0]
    if "회사" in text or "출근" in text:
        return [0.0, 1.0]
    return [-1.0, -1.0]


class FakeEmbedder:
    impl = "fake"

    def __init__(self, fail=False):
        self.calls: list[list[str]] = []
        self.fail = fail

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("api down")
        return [fake_vec(t) for t in texts]


class Profiles:
    def __init__(self, prof):
        self.prof = prof

    def profile_for(self, turn):
        return self.prof


LT = [MemoryItem(id="l1", text="남원에서 태어났다.", memory_type="LONG_TERM"),
      MemoryItem(id="l2", text="막내아들 이름은 도윤.", memory_type="LONG_TERM")]
ST = [MemoryItem(id="s1", text="손자가 도윤이라고 소개함", memory_type="SHORT_TERM"),
      MemoryItem(id="s2", text="딸이 요즘 회사 출근이 힘들다고 함", memory_type="SHORT_TERM"),
      MemoryItem(id="s3", text="손녀가 고향 얘기를 꺼냄", memory_type="SHORT_TERM")]
PROF = PersonaProfile(subject_name="홍길동", address_terms=["할머니"], family_names=["이도윤"])


def turn(msg, mems=LT + ST):
    return TurnInput(subject_id="x", message=msg, persona_instructions="당신은 홍길동이다.", be_memories=mems)


def state():
    return SessionState(session_id=None, subject_id="x")


def selector(emb=None, k=5, profiles=PROF):
    emb = emb or FakeEmbedder()
    return TieredSelector(Profiles(profiles), emb, EmbedOnDemandVectors(emb), st_top_k=k, threshold=0.18), emb


class TieredSelectorTest(unittest.TestCase):
    def test_long_all_then_short_ranked(self):
        sel, emb = selector()
        out = sel.select(turn("할머니, 회사 다니기 힘들다"), state(), RequestMemoryStore())
        self.assertEqual([m.id for m in out.memories], ["l1", "l2", "s2"])  # 장기 순서 그대로 + 임계 넘는 단기만
        self.assertEqual(out.strategy, "tiered:lt2+st1/3")
        self.assertEqual(emb.calls[0], ["회사 다니기 힘들다"])  # 호칭 뗀 단서로 질의

    def test_intro_name_does_not_pull(self):
        # "나 도윤이야" 자기소개는 단서에서 빠져 '도윤' 단기 기억(s1)을 끌어오지 않는다
        sel, emb = selector()
        out = sel.select(turn("할머니 나 도윤이야. 고향이 어디였지?"), state(), RequestMemoryStore())
        self.assertEqual(emb.calls[0], ["고향이 어디였지?"])
        self.assertEqual([m.id for m in out.memories][2:], ["s3"])

    def test_greeting_only_skips_search(self):
        sel, emb = selector()
        out = sel.select(turn("할머니, 잘 지냈어?"), state(), RequestMemoryStore())
        self.assertEqual([m.id for m in out.memories], ["l1", "l2"])
        self.assertTrue(out.strategy.endswith("|nocue"))
        self.assertEqual(emb.calls, [])

    def test_top_k(self):
        sel, _ = selector(k=0)
        out = sel.select(turn("고향 회사"), state(), RequestMemoryStore())
        self.assertEqual([m.id for m in out.memories], ["l1", "l2"])

    def test_embed_failure_falls_back(self):
        sel, _ = selector(emb=FakeEmbedder(fail=True), k=2)
        out = sel.select(turn("회사 얘기"), state(), RequestMemoryStore())
        self.assertEqual([m.id for m in out.memories], ["l1", "l2", "s1", "s2"])
        self.assertTrue(out.strategy.endswith("|embed-fail"))

    def test_untyped_is_passthrough(self):
        # BE 가 유형을 안 보내면 전부 장기로 들어와 받은 그대로 나간다 — 임베딩 호출 없음
        sel, emb = selector()
        mems = [MemoryItem(id=f"m{i}", text=t) for i, t in enumerate(["a", "b", "c"])]
        out = sel.select(turn("할머니 고향이 어디야?", mems), state(), RequestMemoryStore())
        self.assertEqual([m.id for m in out.memories], ["m0", "m1", "m2"])
        self.assertEqual(emb.calls, [])

    def test_no_lexicon_uses_raw_message(self):
        sel, emb = selector(profiles=PersonaProfile())
        sel.select(turn("할머니, 회사 힘들어"), state(), RequestMemoryStore())
        self.assertEqual(emb.calls[0], ["할머니, 회사 힘들어"])

    def test_delta_passed(self):
        sel, _ = selector()
        st = state()
        st.delta_memories = [MemoryItem(id="d1", text="새 기억", memory_type="DELTA")]
        out = sel.select(turn("할머니, 잘 지냈어?"), st, RequestMemoryStore())
        self.assertEqual([m.id for m in out.delta], ["d1"])

    def test_cached_embedder(self):
        inner = FakeEmbedder()
        emb = CachedEmbedder(inner)
        emb.embed(["a", "b"])
        emb.embed(["b", "c", "c"])
        self.assertEqual(inner.calls, [["a", "b"], ["c"]])


class WiringTest(unittest.TestCase):
    def test_request_memory_type_maps(self):
        req = PersonaResponseRequest.model_validate({
            "message": "고향?", "persona": {"subjectId": "x", "instructions": "당신은 홍길동이다."},
            "memories": [{"id": "a", "content": "남원", "memoryType": "SHORT_TERM"}, {"id": "b", "content": "c"}]})
        t = turn_from_request(req)
        self.assertEqual([m.memory_type for m in t.be_memories], ["SHORT_TERM", "LONG_TERM"])

    def test_build_selector_flag(self):
        s = Settings(openai_api_key=SecretStr("sk-test"))
        self.assertEqual(build_selector(s).impl, "passthrough_v0")
        s2 = Settings(openai_api_key=SecretStr("sk-test"), orchestrator_selector="tiered")
        self.assertEqual(build_selector(s2, embedder=FakeEmbedder()).impl, "tiered_v1")
        self.assertEqual(build_selector(s2, kind="structured", embedder=FakeEmbedder()).impl, "tiered_v1+restricted")

    def test_env(self):
        with patch.dict("os.environ", {"ORCHESTRATOR_SELECTOR": "tiered", "ORCHESTRATOR_SELECTOR_ST_TOPK": "3",
                                       "OPENAI_API_KEY": "sk-test"}):
            from app.setting import load_settings
            s = load_settings()
        self.assertEqual((s.orchestrator_selector, s.orchestrator_selector_st_top_k), ("tiered", 3))


if __name__ == "__main__":
    unittest.main()
