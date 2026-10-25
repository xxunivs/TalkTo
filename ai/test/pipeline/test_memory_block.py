"""기억 블록: 글 모양, 순서 고정(프롬프트 캐시), 기억 수 상한 경고 — 실제 API 호출 없음."""
import random
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

from app.schema.persona import PersonaResponseRequest
from app.setting import Settings
from pipeline.s3_synthesis.c_build_system_prompt import (
    build_memory_block,
    generate_persona_response,
)

MEMS = [SimpleNamespace(id=f"{i:02d}-aaaa", title=("고향" if i == 0 else ""), content=f"기억 {i}번 내용.")
        for i in range(6)]
# 이 레포의 머리말에는 '(물으면만)' 기억 안내가 더 붙어 있다(팀 4층 레포에는 없는 문장)
HEAD = ("참고 기억(대화 화제가 닿으면 지명·햇수 등 구체 사실을 그대로 녹이고, 인사·짧은 "
        "안부엔 억지로 꺼내지 않는다. 없는 내용은 지어내지 않는다. 제목에 '(물으면만)'이 붙은 "
        "기억은 사용자가 그 이야기를 먼저 꺼내거나 물었을 때만 쓰고, 먼저 꺼내거나 넌지시 "
        "비치지 않는다):")


def _request(n: int) -> PersonaResponseRequest:
    return PersonaResponseRequest.model_validate({
        "message": "고향이 어디야?",
        "memories": [{"id": f"m{i:03d}", "content": f"기억 {i}"} for i in range(n)],
        "persona": {"subjectId": "s", "instructions": "당신은 김가상이다."},
    })


def _mock_openai() -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content="응."))]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


class MemoryBlockTest(unittest.TestCase):
    def test_format(self) -> None:
        self.assertEqual(build_memory_block([]), "")
        self.assertEqual(build_memory_block(MEMS[:2]), HEAD + "\n- [고향] 기억 0번 내용.\n- 기억 1번 내용.")

    def test_same_memories_same_text_whatever_order(self) -> None:
        # BE 가 매 턴 다른 순서로 보내도 같은 글자 → 고정 앞부분이 그대로라 프롬프트 캐시가 유지된다
        want = build_memory_block(MEMS)
        for seed in range(5):
            shuffled = MEMS[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(build_memory_block(shuffled), want)
        self.assertEqual(want.splitlines()[1:], [f"- {'[고향] ' if i == 0 else ''}기억 {i}번 내용." for i in range(6)])


class MemoryFullMaxTest(unittest.TestCase):
    def test_default_400(self) -> None:
        self.assertEqual(Settings().memory_full_max, 400)

    def test_over_limit_warns_but_keeps_all(self) -> None:
        settings = Settings(openai_api_key=SecretStr("k"), memory_full_max=3)
        with patch("app.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai()
            with self.assertLogs("pipeline.s3_synthesis.c_build_system_prompt", "WARNING") as logs:
                generate_persona_response(_request(4), settings=settings)
            messages = cls.return_value.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("MEMORY_FULL_MAX", logs.output[0])
        self.assertEqual(sum(ln.startswith("- 기억") for m in messages for ln in m["content"].splitlines()), 4)

    def test_at_limit_no_warning(self) -> None:
        settings = Settings(openai_api_key=SecretStr("k"), memory_full_max=4)
        with patch("app.external_api.openai_client.OpenAI") as cls:
            cls.return_value = _mock_openai()
            with self.assertNoLogs("pipeline.s3_synthesis.c_build_system_prompt", "WARNING"):
                generate_persona_response(_request(4), settings=settings)


    def test_orchestrator_trace_marks_over_limit(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from orchestrator.llm import FakeLLM
        from orchestrator.service import build_default, orchestrate_persona_response

        with tempfile.TemporaryDirectory() as d:
            settings = Settings(openai_api_key=SecretStr("k"), memory_full_max=3, orchestrator_trace_dir=d)
            orch = build_default(settings)
            orch.llm = FakeLLM()
            with self.assertLogs("pipeline.s3_synthesis.c_build_system_prompt", "WARNING"):
                orchestrate_persona_response(_request(4), settings=settings, orchestrator=orch)
            orchestrate_persona_response(_request(3), settings=settings, orchestrator=orch)
            rows = [json.loads(ln) for f in Path(d).glob("*.jsonl") for ln in f.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["flags"], {"memory_over_full_max": {"n": 4, "max": 3}})
        self.assertNotIn("flags", rows[1])


if __name__ == "__main__":
    unittest.main()
