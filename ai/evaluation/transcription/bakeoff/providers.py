"""STT provider clients for the transcription bake-off (RTZR vs ElevenLabs Scribe).

Both providers return segments in the transcript_segments contract shape:
segmentIndex, startMs, endMs, speakerLabel, transcriptText.
"""

import json
import time
from pathlib import Path

import httpx

RTZR_BASE = "https://openapi.vito.ai"
ELEVENLABS_BASE = "https://api.elevenlabs.io"


class ProviderError(RuntimeError):
    pass


def transcribe_rtzr(
    audio_path: Path,
    *,
    client_id: str,
    client_secret: str,
    keywords: list[str] | None = None,
    spk_count: int | None = None,
    poll_interval_seconds: float = 5.0,
    timeout_seconds: float = 1800.0,
) -> dict:
    auth = httpx.post(
        f"{RTZR_BASE}/v1/authenticate",
        data={"client_id": client_id, "client_secret": client_secret},
        timeout=30,
    )
    auth.raise_for_status()
    token = auth.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    config: dict = {"use_itn": True, "use_diarization": True}
    if spk_count:
        config["diarization"] = {"spk_count": spk_count}
    if keywords:
        config["keywords"] = keywords

    with audio_path.open("rb") as audio_file:
        submit = httpx.post(
            f"{RTZR_BASE}/v1/transcribe",
            headers=headers,
            files={"file": (audio_path.name, audio_file)},
            data={"config": json.dumps(config, ensure_ascii=False)},
            timeout=300,
        )
    submit.raise_for_status()
    transcribe_id = submit.json()["id"]

    deadline = time.monotonic() + timeout_seconds
    while True:
        poll = httpx.get(
            f"{RTZR_BASE}/v1/transcribe/{transcribe_id}", headers=headers, timeout=30
        )
        poll.raise_for_status()
        body = poll.json()
        status = body.get("status")
        if status == "completed":
            break
        if status == "failed":
            raise ProviderError(f"RTZR transcription failed: {body}")
        if time.monotonic() > deadline:
            raise ProviderError(f"RTZR polling timed out after {timeout_seconds}s")
        time.sleep(poll_interval_seconds)

    segments = []
    for utterance in body.get("results", {}).get("utterances", []):
        text = (utterance.get("msg") or "").strip()
        if not text:
            continue
        start_ms = int(utterance.get("start_at", 0))
        segments.append(
            {
                "segmentIndex": len(segments),
                "startMs": start_ms,
                "endMs": start_ms + int(utterance.get("duration", 0)),
                "speakerLabel": f"SPK_{utterance.get('spk', 0)}",
                "transcriptText": text,
            }
        )
    return {"provider": "rtzr", "model": "whisper-diarization(vito)", "segments": segments}


def transcribe_scribe(
    audio_path: Path,
    *,
    api_key: str,
    model_id: str = "scribe_v1",
    language_code: str | None = "ko",
    num_speakers: int | None = None,
    timeout_seconds: float = 1800.0,
) -> dict:
    data: dict = {
        "model_id": model_id,
        "diarize": "true",
        "tag_audio_events": "false",
    }
    if language_code:
        data["language_code"] = language_code
    if num_speakers:
        data["num_speakers"] = str(num_speakers)

    with audio_path.open("rb") as audio_file:
        response = httpx.post(
            f"{ELEVENLABS_BASE}/v1/speech-to-text",
            headers={"xi-api-key": api_key},
            files={"file": (audio_path.name, audio_file)},
            data=data,
            timeout=timeout_seconds,
        )
    if response.status_code != 200:
        raise ProviderError(f"Scribe error {response.status_code}: {response.text[:500]}")
    body = response.json()

    segments = _group_scribe_words(body.get("words") or [])
    if not segments and (body.get("text") or "").strip():
        segments = [
            {
                "segmentIndex": 0,
                "startMs": 0,
                "endMs": 0,
                "speakerLabel": "SPK_0",
                "transcriptText": body["text"].strip(),
            }
        ]
    return {"provider": "elevenlabs", "model": model_id, "segments": segments}


def _group_scribe_words(words: list[dict]) -> list[dict]:
    segments: list[dict] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current and current["transcriptText"].strip():
            segments.append(
                {
                    "segmentIndex": len(segments),
                    "startMs": current["startMs"],
                    "endMs": current["endMs"],
                    "speakerLabel": current["speakerLabel"],
                    "transcriptText": current["transcriptText"].strip(),
                }
            )
        current = None

    for word in words:
        if word.get("type") == "audio_event":
            continue
        speaker = word.get("speaker_id") or "speaker_0"
        label = f"SPK_{speaker.rsplit('_', 1)[-1]}"
        start_ms = int(float(word.get("start") or 0) * 1000)
        end_ms = int(float(word.get("end") or 0) * 1000)
        if current is None or current["speakerLabel"] != label:
            flush()
            current = {
                "speakerLabel": label,
                "startMs": start_ms,
                "endMs": end_ms,
                "transcriptText": "",
            }
        current["transcriptText"] += word.get("text") or ""
        current["endMs"] = max(current["endMs"], end_ms)
    flush()
    return segments
