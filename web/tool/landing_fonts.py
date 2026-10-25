"""랜딩페이지(landing/) 글꼴을 쓰는 글자만 남겨 다시 만든다. 랜딩 글을 고치면 꼭 다시 돌린다.

    python tool/landing_fonts.py      (fontTools 가 있는 파이썬으로)

글자 = landing/ 의 모든 .html·.js + 서버에서 받아 바꾸는 글(요금·사업자 정보) + 영문·숫자.
약관 본문(legal.js 가 서버에서 받는 글)은 시스템 글꼴로 그리므로 넣지 않는다.
빠진 글자는 시스템 글꼴로 그려져 제목 한가운데 모양이 바뀐다.
"""

import json
import sys
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

FE = Path(__file__).resolve().parents[1]
LANDING = FE / "landing"
SOURCES = FE / "assets" / "fonts"
FONTS = (
    ("GowunBatang-Bold.ttf", "gowun-batang-bold.woff"),
    ("NotoSansKR-Regular.ttf", "noto-sans-kr-regular.woff"),
    ("NotoSansKR-Bold.ttf", "noto-sans-kr-bold.woff"),
)
API = ("https://app.talkto.co.kr/api/v1/billing/plans", "https://app.talkto.co.kr/api/v1/legal")


def page_text() -> str:
    text = "".join(
        path.read_text(encoding="utf-8")
        for pattern in ("*.html", "*.js")
        for path in sorted(LANDING.glob(pattern))
    )
    for url in API:
        try:
            text += json.dumps(json.load(urllib.request.urlopen(url, timeout=10)), ensure_ascii=False)
        except Exception as error:  # noqa: BLE001 — 서버가 없어도 페이지 글자는 만든다
            print("서버 글을 못 받았어요:", url, error)
    chars = set(text) | {chr(c) for c in range(0x20, 0x7F)} | set("₩·…“”‘’—–©")
    return "".join(sorted(c for c in chars if c.isprintable()))


def main() -> None:
    chars = page_text()
    (LANDING / "fonts").mkdir(exist_ok=True)
    for src, out in FONTS:
        opts = subset.Options()
        opts.flavor = "woff"
        opts.layout_features = ["*"]
        opts.desubroutinize = True
        font = subset.load_font(str(SOURCES / src), opts)
        sub = subset.Subsetter(opts)
        sub.populate(text=chars)
        sub.subset(font)
        path = LANDING / "fonts" / out
        subset.save_font(font, str(path), opts)
        print(f"{out}: {path.stat().st_size // 1024}KB")
    # 한글이 모두 들어갔는지 본다(고운바탕은 자주 쓰는 2,350자만 있어 드문 글자는 빠질 수 있다)
    hangul = {c for c in chars if "가" <= c <= "힣"}
    for _, out in FONTS:
        cmap = TTFont(str(LANDING / "fonts" / out)).getBestCmap()
        missing = sorted(c for c in hangul if ord(c) not in cmap)
        if missing:
            print(f"  {out} 에 없는 글자: {''.join(missing)}")
            if "noto" in out:
                sys.exit(1)


if __name__ == "__main__":
    main()
