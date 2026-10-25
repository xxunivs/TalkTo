"""⑤ 생성 모델 어댑터 (모델 교체, 스트리밍 지점)"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.external_api.openai_client import chat_client
from app.setting import Settings
from orchestrator.contracts import Generation

PROVIDER = "openai"


class OpenAIChatLLM:
    impl = "openai_chat_v0"

    def __init__(self, settings: Settings, model: str | None = None) -> None:
        self.settings = settings
        self.model = model or settings.openai_chat_model

    def generate(self, messages: list[dict[str, str]]) -> Generation:
        # BE 45초 한도 안에서 끝나도록 대화용 시간 한도 (기본 20초 x 2번)
        client = chat_client(self.settings, "persona response")
        t0 = time.perf_counter()
        # 추론 정도: 기본값은 답 전 사고가 길어 느림 (app/setting.py openai_chat_reasoning_effort)
        effort = getattr(self.settings, "openai_chat_reasoning_effort", None)
        extra = {"reasoning_effort": effort} if effort else {}
        response = client.chat.completions.create(model=self.model, messages=messages, **extra)
        latency = time.perf_counter() - t0
        usage = {}
        u = getattr(response, "usage", None)
        for k in ("prompt_tokens", "completion_tokens"):
            v = getattr(u, k, None) if u is not None else None
            if isinstance(v, int):
                usage[k] = v
        text = (response.choices[0].message.content or "").strip()
        return Generation(
            text=text, provider=PROVIDER, model=self.model, usage=usage, latency_s=latency
        )


class FakeLLM:
    """API 없는 고정 답 (테스트, CLI --mock 용)"""

    impl = "fake"

    def __init__(
        self, reply: str | Callable[[list[dict[str, str]]], str] = "어, 그래. 밥은 먹었니?"
    ) -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def generate(self, messages: list[dict[str, str]]) -> Generation:
        self.calls.append(messages)
        text = self.reply(messages) if callable(self.reply) else self.reply
        return Generation(text=text, provider="fake", model="fake", latency_s=0.0)
