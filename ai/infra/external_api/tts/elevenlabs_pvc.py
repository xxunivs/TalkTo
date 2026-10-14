"""ElevenLabs Professional Voice Cloning(PVC) API.

    만들기  POST /v1/voices/pvc                         {name, language}
    재료    POST /v1/voices/pvc/{id}/samples             files[] (mp3 192kbps 이상, 30분 안팎씩)
    확인    POST /v1/voices/pvc/{id}/verification        files[] + extra_text (수동 확인 신청)
    학습    POST /v1/voices/pvc/{id}/train               {model_id}
    상태    GET  /v1/voices/{id}                         fine_tuning.state[model] · voice_verification
    지우기  DELETE /v1/voices/{id}                       (슬롯 반환)

PVC 는 목소리 주인 확인을 통과해야 학습된다. 본인은 화면 글 읽기(captcha)로, 그게 안 되는
경우(고인 등)는 서류를 보내는 수동 확인으로 한다. 수동 확인은 ElevenLabs 사람이 사안마다 본다.
(docs: elevenlabs.io/docs/api-reference/voices/pvc · 2026-10 확인)
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from infra.error import TTSProviderError

logger = logging.getLogger(__name__)

BASE = "https://api.elevenlabs.io"
# 학습할 모델: v4 를 먼저 시도하고, 아직 API 로 PVC 학습을 받지 않으면 v2 다국어로 한다.
# (v4 출시 2026-09-28. PVC 학습은 화면에서만 확인됐고 API 는 확인 전 — 실제 목록으로 고른다)
_TRAIN_PREFERENCE = ("eleven_v4", "eleven_v4_turbo", "eleven_multilingual_v2")
_TRAINING = {"queued", "fine_tuning", "delayed"}
_V2 = "eleven_multilingual_v2"


class ElevenLabsPVC:
    def __init__(self, api_key: str, *, timeout_seconds: float = 120.0) -> None:
        self.api_key = api_key
        self.timeout = timeout_seconds

    def _headers(self) -> dict[str, str]:
        return {"xi-api-key": self.api_key}

    def _check(self, response: httpx.Response, what: str) -> dict:
        if response.status_code >= 400:
            raise TTSProviderError(
                f"ElevenLabs PVC {what} 오류 {response.status_code}: {response.text[:300]}"
            )
        return response.json() if response.content else {}

    def _request(self, method: str, path: str, what: str, **kwargs) -> dict:
        try:
            response = httpx.request(
                method, f"{BASE}{path}", headers=self._headers(), timeout=self.timeout, **kwargs
            )
        except httpx.HTTPError as error:
            raise TTSProviderError(f"ElevenLabs PVC {what} 요청 실패: {error}") from error
        return self._check(response, what)

    def create(self, name: str, *, language: str = "ko", description: str | None = None) -> str:
        body = {"name": name, "language": language}
        if description:
            body["description"] = description
        data = self._request("POST", "/v1/voices/pvc", "만들기", json=body)
        if not data.get("voice_id"):
            raise TTSProviderError("PVC 만들기 응답에 voice_id 가 없다.")
        return str(data["voice_id"])

    def add_samples(self, voice_id: str, files: list[Path]) -> list[dict]:
        """재료 파일을 하나씩 올린다(큰 파일 여럿을 한 요청에 담지 않게)."""
        uploaded: list[dict] = []
        for path in files:
            with path.open("rb") as handle:
                data = self._request(
                    "POST",
                    f"/v1/voices/pvc/{voice_id}/samples",
                    "재료 올리기",
                    # 이미 깨끗한 구간만 골라 크기를 맞춰 보낸다. 잡음 제거는 깨끗한 소리를 망칠 수 있다(문서).
                    data={"remove_background_noise": "false"},
                    files=[("files", (path.name, handle, "audio/mpeg"))],
                )
            uploaded.extend(data if isinstance(data, list) else data.get("samples", [data]))
        return uploaded

    def request_verification(
        self, voice_id: str, documents: list[tuple[str, bytes, str]], extra_text: str
    ) -> None:
        files = [("files", (name, content, ctype)) for name, content, ctype in documents]
        self._request(
            "POST",
            f"/v1/voices/pvc/{voice_id}/verification",
            "확인 신청",
            data={"extra_text": extra_text},
            files=files,
        )

    def trainable_models(self) -> list[str]:
        data = self._request("GET", "/v1/models", "모델 목록")
        models = data if isinstance(data, list) else data.get("models", [])
        allowed = {m.get("model_id") for m in models if m.get("can_be_finetuned")}
        return [m for m in _TRAIN_PREFERENCE if m in allowed] or ["eleven_multilingual_v2"]

    def train(self, voice_id: str) -> str:
        """쓸 수 있는 가장 좋은 모델로 학습을 건다. 고른 모델을 돌려준다."""
        last: TTSProviderError | None = None
        for model in self.trainable_models():
            try:
                self._request(
                    "POST", f"/v1/voices/pvc/{voice_id}/train", "학습", json={"model_id": model}
                )
                return model
            except TTSProviderError as error:  # 이 모델은 아직 안 받는다 → 다음 모델
                logger.warning("pvc train %s with %s failed: %s", voice_id, model, error)
                last = error
        raise last or TTSProviderError("학습할 모델이 없다.")

    def status(self, voice_id: str) -> dict:
        """{verification: pending|verified|rejected, training: not_started|training|done|failed,
        progress: 0~1|None, message}"""
        voice = self._request("GET", f"/v1/voices/{voice_id}", "상태")
        return summarize(voice)

    def delete(self, voice_id: str) -> None:
        self._request("DELETE", f"/v1/voices/{voice_id}", "지우기")


def summarize(voice: dict) -> dict:
    """GET /v1/voices/{id} 응답을 BE 가 쓰는 네 칸으로 줄인다."""
    tuning = voice.get("fine_tuning") or {}
    verify = voice.get("voice_verification") or {}
    states = {k: v for k, v in (tuning.get("state") or {}).items() if v}
    progress = [v for v in (tuning.get("progress") or {}).values() if isinstance(v, (int, float))]
    messages = [m for m in (tuning.get("message") or {}).values() if m] if isinstance(
        tuning.get("message"), dict
    ) else ([tuning["message"]] if tuning.get("message") else [])

    # 우리가 합성에 쓰는 모델(v4 계열 · v2 다국어)이 끝나야 '완료'다. 다른 모델(flash 등)만 먼저
    # 끝났다고 PVC 로 바꾸면, 쓰는 모델은 아직 학습 중이라 제 목소리가 안 난다.
    usable = {k: v for k, v in states.items() if k.startswith("eleven_v4") or k == _V2}
    if "fine_tuned" in usable.values():
        training = "done"
    elif _TRAINING & set(states.values()):
        training = "training"
    elif "failed" in usable.values() or (not usable and "failed" in states.values()):
        training = "failed"
    elif "fine_tuned" in states.values():
        training = "training"  # 쓰지 않는 모델만 끝났다 — 쓰는 모델을 기다린다
    else:
        training = "not_started"

    # 학습이 시작됐다면 확인은 이미 통과한 것이다
    if verify.get("is_verified") or training != "not_started":
        verification = "verified"
    elif verify.get("verification_failures"):
        # 우리는 글 읽기 확인을 하지 않으니, 실패 기록이 있으면 서류 확인에서 거절된 것이다
        verification = "rejected"
    else:
        verification = "pending"
    return {
        "verification": verification,
        "training": training,
        "progress": max(progress) if progress else None,
        "message": messages[0] if messages else None,
    }
