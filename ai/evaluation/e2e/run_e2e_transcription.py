"""End-to-end test of POST /v1/analysis/transcriptions against real recordings.

Spins up a local static file server over the audio directory (simulating the
backend's presigned URLs), sends every recording through the running AI server,
and scores the responses against gold transcripts (same normalization as the
bake-off: speaker labels stripped, whitespace/punctuation removed).

Usage:
    python -m evaluation.e2e.run_e2e_transcription [options]

Options:
    --audio-dir PATH   recordings directory (default: TalkTo_PersonaAI_AI/data/voice_raw)
    --gold-dir PATH    gold transcripts, {audio stem}.txt (default: evaluation/bakeoff/gold)
    --server URL       AI server base URL (default: http://localhost:8400)
    --file-port N      port for the local audio file server (default: 8401)
    --only NAMES       comma-separated audio stems to run (default: all)
    --glossary PATH    keyword file (one per line) sent as glossary — enables
                       the correction pass (e.g. evaluation/bakeoff/gold/keywords.txt)
    --out PATH         output directory (default: evaluation/e2e/results)

Requires the AI server to be running (uvicorn app.main:app). Results contain
transcripts and must never be committed (directory is gitignored).
"""

import argparse
import functools
import json
import re
import sys
import threading
import time
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from dotenv import dotenv_values

from evaluation.bakeoff.metrics import cer, full_text, speaker_stats

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIO_DIR = REPO_ROOT.parent / "TalkTo_PersonaAI_AI" / "data" / "voice_raw"
DEFAULT_GOLD_DIR = REPO_ROOT / "evaluation" / "bakeoff" / "gold"
DEFAULT_OUT = REPO_ROOT / "evaluation" / "e2e" / "results"

# 골드 10·11번은 통화 후반부가 누락된 정리본이라 CER 채점에서 제외한다
# (10번은 bake-off에서, 11번은 7/6 E2E에서 확인: 골드 888자 vs 실제 발화 1,275자)
CER_EXCLUDED_STEMS = {"할머니 목소리 10", "할머니 목소리 11"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, default=DEFAULT_AUDIO_DIR)
    parser.add_argument("--gold-dir", type=Path, default=DEFAULT_GOLD_DIR)
    parser.add_argument("--server", type=str, default="http://localhost:8400")
    parser.add_argument("--file-port", type=int, default=8401)
    parser.add_argument("--only", type=str, default=None)
    parser.add_argument("--glossary", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def natural_key(path: Path) -> tuple:
    return tuple(
        int(part) if part.isdigit() else part
        for part in re.split(r"(\d+)", path.stem)
    )


def load_gold(gold_dir: Path, stem: str) -> str | None:
    candidate = gold_dir / f"{stem}.txt"
    if not candidate.exists():
        return None
    text = candidate.read_text(encoding="utf-8")
    # gold lines carry speaker labels like "할머니: ..." — score text only
    return "\n".join(
        re.sub(r"^[^:\n]{1,10}:\s*", "", line) for line in text.splitlines()
    )


def start_file_server(directory: Path, port: int) -> ThreadingHTTPServer:
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    handler.log_message = lambda *args, **kwargs: None
    server = ThreadingHTTPServer(("localhost", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def transcribe(
    client: httpx.Client,
    server: str,
    audio_url: str,
    index: int,
    glossary: list[str],
) -> tuple[int, dict, float]:
    body = {
        "jobId": f"00000000-0000-0000-0000-{index:012d}",
        "recordingId": f"11111111-0000-0000-0000-{index:012d}",
        "audioUrl": audio_url,
        "audioMimeType": "audio/wav",
        "mode": "full",
        "language": "ko",
        "speakerDiarization": True,
        "glossary": glossary,
    }
    started = time.monotonic()
    response = client.post(f"{server}/v1/analysis/transcriptions", json=body)
    return response.status_code, response.json(), round(time.monotonic() - started, 1)


def main() -> int:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    env = dotenv_values(REPO_ROOT / ".env")
    token = (env.get("AI_SERVER_TOKEN") or "").strip()
    headers = {"x-ai-server-token": token} if token else {}

    glossary: list[str] = []
    if args.glossary and args.glossary.exists():
        glossary = [
            line.strip()
            for line in args.glossary.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    audio_files = sorted(args.audio_dir.glob("*.wav"), key=natural_key)
    if args.only:
        wanted = {name.strip() for name in args.only.split(",")}
        audio_files = [path for path in audio_files if path.stem in wanted]
    if not audio_files:
        print(f"no audio files found in {args.audio_dir}", file=sys.stderr)
        return 1

    file_server = start_file_server(args.audio_dir, args.file_port)
    rows = []
    try:
        with httpx.Client(headers=headers, timeout=1800) as client:
            health = client.get(f"{args.server}/health")
            health.raise_for_status()

            for index, audio_path in enumerate(audio_files, start=1):
                url = (
                    f"http://localhost:{args.file_port}/"
                    f"{urllib.parse.quote(audio_path.name)}"
                )
                print(f"[{audio_path.stem}] 전사 요청...", flush=True)
                status, body, latency = transcribe(
                    client, args.server, url, index, glossary
                )
                if status != 200:
                    print(
                        f"[{audio_path.stem}] 실패 {status}: {json.dumps(body, ensure_ascii=False)[:200]}",
                        file=sys.stderr,
                    )
                    rows.append({"stem": audio_path.stem, "status": status, "latency": latency})
                    continue

                (args.out / f"{audio_path.stem}.json").write_text(
                    json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                segments = body["segments"]
                stats = speaker_stats(segments)
                confidences = [
                    s["confidence"] for s in segments if s.get("confidence") is not None
                ]
                gold = load_gold(args.gold_dir, audio_path.stem)
                row = {
                    "stem": audio_path.stem,
                    "status": status,
                    "latency": latency,
                    "segments": len(segments),
                    "corrections": sum(1 for s in segments if s.get("correctedText")),
                    "needsReview": sum(1 for s in segments if s.get("needsReview")),
                    "speakers": len(stats),
                    "durationMs": max(s["endMs"] for s in segments),
                    "meanConfidence": (
                        round(sum(confidences) / len(confidences), 3)
                        if confidences
                        else None
                    ),
                    "cer": (
                        round(cer(gold, full_text(segments)), 4)
                        if gold is not None
                        else None
                    ),
                    "cerExcluded": audio_path.stem in CER_EXCLUDED_STEMS,
                }
                rows.append(row)
                print(
                    f"[{audio_path.stem}] {latency}s, {row['segments']} seg, "
                    f"화자 {row['speakers']}, 교정 {row['corrections']}, "
                    f"CER {row['cer']}"
                    f"{' (채점 제외)' if row['cerExcluded'] else ''}",
                    flush=True,
                )
    finally:
        file_server.shutdown()

    write_summary(args.out, rows)
    print(f"\n요약: {args.out / 'summary.md'}")
    failures = [row for row in rows if row["status"] != 200]
    return 1 if failures else 0


def write_summary(out_dir: Path, rows: list[dict]) -> None:
    lines = [
        "# E2E 전사 결과 (POST /v1/analysis/transcriptions)",
        "",
        f"실행: {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "| 파일 | 상태 | 처리(s) | 길이(분) | 세그먼트 | 화자 | 교정 | 리뷰 | 평균 confidence | CER |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        if row["status"] != 200:
            lines.append(
                f"| {row['stem']} | ❌ {row['status']} | {row['latency']} "
                "| - | - | - | - | - | - | - |"
            )
            continue
        duration_min = round(row["durationMs"] / 60_000, 1)
        cer_cell = "-" if row["cer"] is None else f"{row['cer']:.4f}"
        if row["cerExcluded"]:
            cer_cell += " (제외: 골드 후반부 누락)"
        lines.append(
            f"| {row['stem']} | 200 | {row['latency']} | {duration_min} "
            f"| {row['segments']} | {row['speakers']} "
            f"| {row.get('corrections', '-')} | {row.get('needsReview', '-')} "
            f"| {row['meanConfidence']} | {cer_cell} |"
        )

    scored = [
        row["cer"]
        for row in rows
        if row["status"] == 200 and row["cer"] is not None and not row["cerExcluded"]
    ]
    if scored:
        lines += [
            "",
            f"채점 대상 {len(scored)}건 CER 평균 {sum(scored) / len(scored):.4f} / "
            f"최소 {min(scored):.4f} / 최대 {max(scored):.4f}",
            "",
            "> CER은 bake-off와 동일 기준(공백·부호 제거, 화자 라벨 제거). "
            "골드가 의미기반 정리본이라 절대값은 상한선으로 해석.",
        ]
    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
