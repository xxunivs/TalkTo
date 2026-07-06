"""Run the STT bake-off (RTZR vs ElevenLabs Scribe) on one or more recordings.

Usage:
    python -m evaluation.bakeoff.run_bakeoff <audio files...> [options]

Options:
    --gold PATH          gold transcript text file (single audio only)
    --gold-dir PATH      directory with {audio stem}.txt gold transcripts
    --keywords PATH      keyword list for RTZR boosting, one per line
    --num-speakers N     expected speaker count hint for both providers
    --providers LIST     comma-separated subset (default: rtzr,elevenlabs)
    --out PATH           output directory (default: evaluation/bakeoff/results)

Requires RTZR_CLIENT_ID / RTZR_CLIENT_SECRET / ELEVENLABS_API_KEY in .env.
Outputs stay local: results contain transcripts and must never be committed.
"""

import argparse
import re
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from evaluation.bakeoff.metrics import cer, full_text, speaker_stats
from evaluation.bakeoff.providers import ProviderError, transcribe_rtzr, transcribe_scribe

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "evaluation" / "bakeoff" / "results"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="STT bake-off: RTZR vs ElevenLabs Scribe")
    parser.add_argument("audio", nargs="+", type=Path)
    parser.add_argument("--gold", type=Path, default=None)
    parser.add_argument("--gold-dir", type=Path, default=None)
    parser.add_argument("--keywords", type=Path, default=None)
    parser.add_argument("--num-speakers", type=int, default=None)
    parser.add_argument("--providers", type=str, default="rtzr,elevenlabs")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def load_gold(args: argparse.Namespace, audio_path: Path) -> str | None:
    text = None
    if args.gold and len(args.audio) == 1:
        text = args.gold.read_text(encoding="utf-8")
    elif args.gold_dir:
        candidate = args.gold_dir / f"{audio_path.stem}.txt"
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8")
    if text is None:
        return None
    # gold lines may carry speaker labels like "할머니: ..." — score text only
    return "\n".join(
        re.sub(r"^[^:\n]{1,10}:\s*", "", line) for line in text.splitlines()
    )


def run_provider(
    name: str, audio_path: Path, args: argparse.Namespace, keywords: list[str] | None
) -> dict:
    started = time.monotonic()
    if name == "rtzr":
        client_id = os.environ.get("RTZR_CLIENT_ID")
        client_secret = os.environ.get("RTZR_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise ProviderError("RTZR_CLIENT_ID / RTZR_CLIENT_SECRET not set in .env")
        result = transcribe_rtzr(
            audio_path,
            client_id=client_id,
            client_secret=client_secret,
            keywords=keywords,
            spk_count=args.num_speakers,
        )
    elif name == "elevenlabs":
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            raise ProviderError("ELEVENLABS_API_KEY not set in .env")
        result = transcribe_scribe(
            audio_path, api_key=api_key, num_speakers=args.num_speakers
        )
    else:
        raise ProviderError(f"Unknown provider: {name}")
    result["latencySeconds"] = round(time.monotonic() - started, 1)
    return result


def format_ms(ms: int) -> str:
    seconds = ms // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def write_report(out_dir: Path, audio_path: Path, results: dict[str, dict], gold: str | None) -> None:
    lines = [f"# Bake-off: {audio_path.name}", ""]
    lines.append("| provider | segments | speakers | latency(s) | CER |")
    lines.append("|---|---|---|---|---|")
    for name, result in results.items():
        segments = result["segments"]
        stats = speaker_stats(segments)
        cer_cell = f"{result['cer']:.4f}" if result.get("cer") is not None else "-"
        lines.append(
            f"| {name} | {len(segments)} | {len(stats)} | {result['latencySeconds']} | {cer_cell} |"
        )
    lines.append("")

    for name, result in results.items():
        lines.append(f"## {name} — speaker breakdown")
        for label, entry in sorted(speaker_stats(result["segments"]).items()):
            lines.append(
                f"- {label}: {entry['segments']} segments, "
                f"{format_ms(entry['talkMs'])} talk time, {entry['chars']} chars"
            )
        lines.append("")

    for name, result in results.items():
        lines.append(f"## {name} — transcript")
        for segment in result["segments"]:
            lines.append(
                f"- [{format_ms(segment['startMs'])}] {segment['speakerLabel']}: "
                f"{segment['transcriptText']}"
            )
        lines.append("")

    if gold is None:
        lines.append("> CER 없음: gold 전사가 주어지지 않았습니다. --gold 또는 --gold-dir 사용.")

    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    args = parse_args()
    provider_names = [name.strip() for name in args.providers.split(",") if name.strip()]

    keywords = None
    if args.keywords and args.keywords.exists():
        keywords = [
            line.strip() for line in args.keywords.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    summary_rows = []
    for audio_path in args.audio:
        if not audio_path.exists():
            print(f"[skip] not found: {audio_path}", file=sys.stderr)
            continue
        out_dir = args.out / audio_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        gold = load_gold(args, audio_path)

        results: dict[str, dict] = {}
        for name in provider_names:
            print(f"[{audio_path.name}] {name} 전사 시작...")
            try:
                result = run_provider(name, audio_path, args, keywords)
            except (ProviderError, Exception) as error:
                print(f"[{audio_path.name}] {name} 실패: {error}", file=sys.stderr)
                continue
            if gold:
                result["cer"] = cer(gold, full_text(result["segments"]))
            (out_dir / f"{name}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            results[name] = result
            stats = speaker_stats(result["segments"])
            cer_text = f", CER {result['cer']:.4f}" if result.get("cer") is not None else ""
            print(
                f"[{audio_path.name}] {name} 완료: {len(result['segments'])} segments, "
                f"화자 {len(stats)}명, {result['latencySeconds']}s{cer_text}"
            )
            summary_rows.append((audio_path.name, name, result))

        if results:
            write_report(out_dir, audio_path, results, gold)
            print(f"[{audio_path.name}] 리포트: {out_dir / 'report.md'}")

    if not summary_rows:
        print("완료된 전사가 없습니다.", file=sys.stderr)
        return 1

    print("\n=== 요약 ===")
    for audio_name, provider_name, result in summary_rows:
        cer_text = f" CER={result['cer']:.4f}" if result.get("cer") is not None else ""
        print(
            f"{audio_name} / {provider_name}: {len(result['segments'])} seg, "
            f"{result['latencySeconds']}s{cer_text}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
