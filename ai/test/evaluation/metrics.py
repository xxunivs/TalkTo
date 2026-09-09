"""Metrics for the bake-off: CER against a gold transcript and speaker statistics."""

import re
import unicodedata

_PUNCT = re.compile(r"[.,?!~…'\"`·\-()\[\]]")


def normalize_for_cer(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = _PUNCT.sub("", text)
    return "".join(text.split())


def cer(reference: str, hypothesis: str) -> float:
    ref = normalize_for_cer(reference)
    hyp = normalize_for_cer(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0

    previous = list(range(len(hyp) + 1))
    for i, ref_char in enumerate(ref, start=1):
        current = [i] + [0] * len(hyp)
        for j, hyp_char in enumerate(hyp, start=1):
            cost = 0 if ref_char == hyp_char else 1
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + cost,
            )
        previous = current
    return previous[-1] / len(ref)


def speaker_stats(segments: list[dict]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for segment in segments:
        label = segment["speakerLabel"]
        entry = stats.setdefault(label, {"segments": 0, "talkMs": 0, "chars": 0})
        entry["segments"] += 1
        entry["talkMs"] += max(0, segment["endMs"] - segment["startMs"])
        entry["chars"] += len(segment["transcriptText"])
    return stats


def full_text(segments: list[dict]) -> str:
    return " ".join(segment["transcriptText"] for segment in segments)
