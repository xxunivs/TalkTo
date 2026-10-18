"""TalkTo ElevenLabs 계정에 쌓인 대답 읽기 기록(history)을 지운다. 한 번 돌리는 정리 도구.

이제 새 기록은 쓰자마자 지운다(infra/external_api/elevenlabs_records.py). 이 도구는 그 전에 쌓인 것을 지운다.
목소리는 지우지 않는다(기록만). 받아쓰기 기록은 목록을 보는 API 가 없어 여기서 지울 수 없다
(ElevenLabs 화면에서 지운다).

    .venv\\Scripts\\python.exe tools\\purge_elevenlabs_history.py           # 세기만(기본)
    .venv\\Scripts\\python.exe tools\\purge_elevenlabs_history.py --apply   # 정말 지우기

화면에는 건수·날짜·종류·목소리 수만 찍는다. 기록의 글·목소리 이름은 찍지 않는다. 키는 .env 에서 읽고 찍지 않는다.
"""

import argparse
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infra.external_api import elevenlabs_records as records
from infra.setting import load_settings


def _day(unix: object) -> str:
    if not isinstance(unix, (int, float)):
        return "?"
    return datetime.fromtimestamp(unix, UTC).strftime("%Y-%m-%d")


def summarize(items: list[dict]) -> list[str]:
    if not items:
        return ["대답 읽기 기록: 0건"]
    days = sorted(_day(item.get("date_unix")) for item in items)
    sources = Counter(str(item.get("source") or "?") for item in items)
    voices = {item.get("voice_id") for item in items if item.get("voice_id")}
    return [
        f"대답 읽기 기록: {len(items)}건 ({days[0]} ~ {days[-1]})",
        "종류: " + ", ".join(f"{name} {count}건" for name, count in sorted(sources.items())),
        f"목소리 {len(voices)}개에서 나온 기록",
    ]


def purge(api_key: str, *, apply: bool, out=print) -> tuple[int, int]:
    """(찾은 건수, 지운 건수). apply 가 아니면 세기만 한다."""
    items = records.list_history(api_key)
    for line in summarize(items):
        out(line)
    if not apply:
        out("세기만 했어요. 지우려면 --apply 를 붙여 다시 돌려 주세요.")
        return len(items), 0
    deleted = 0
    for index, item in enumerate(items, 1):
        history_id = item.get("history_item_id")
        if isinstance(history_id, str) and records.delete_history_item(api_key, history_id):
            deleted += 1
        if index % 50 == 0 or index == len(items):
            out(f"  {index}/{len(items)} 처리, {deleted}건 지움")
    left = len(records.list_history(api_key))
    out(f"지운 기록 {deleted}건 · 남은 기록 {left}건")
    return len(items), deleted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="정말 지운다(없으면 세기만)")
    parser.add_argument("--env", default=str(ROOT / ".env"), help="키를 읽을 .env")
    args = parser.parse_args(argv)
    settings = load_settings(args.env)
    if settings.elevenlabs_api_key is None:
        print("ELEVENLABS_API_KEY 가 없어요.", file=sys.stderr)
        return 1
    purge(settings.elevenlabs_api_key.get_secret_value(), apply=args.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
