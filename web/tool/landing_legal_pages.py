"""랜딩의 약관 페이지 세 개(terms·refund·privacy.html)를 같은 틀로 만든다.

본문은 비워 두고 legal.js 가 서버 원문을 받아 채운다. 바닥글은 index.html 에서 가져오므로
틀이나 바닥글을 고치면 이 스크립트를 다시 돌린다(그다음 tool/landing_fonts.py).

    python tool/landing_legal_pages.py
"""

from pathlib import Path

LANDING = Path(__file__).resolve().parents[1] / "landing"

PAGES = [
    ("terms.html", "terms", "이용약관", "TalkTo를 쓰는 데 필요한 약속을 정한 이용약관이에요."),
    ("refund.html", "refund", "환불 정책", "TalkTo의 해지, 결제 취소(청약철회)와 환불 기준이에요."),
    ("privacy.html", "privacy_policy", "개인정보 처리방침", "TalkTo가 회원님의 개인정보를 어떻게 처리하고 지키는지 알려 드려요."),
]

NAV = [
    ("terms.html", "이용약관"),
    ("refund.html", "환불 정책"),
    ("privacy.html", "<strong>개인정보 처리방침</strong>"),
]

TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} · TalkTo</title>
  <meta name="description" content="{description}" />
  <link rel="canonical" href="https://talkto.co.kr/{file}" />
  <meta name="theme-color" content="#F4F1EA" />
  <link rel="icon" href="/favicon.ico" sizes="any" />
  <link rel="icon" type="image/png" sizes="48x48" href="/favicon-48.png" />
  <link rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png" />
  <link rel="apple-touch-icon" href="/img/icon-192.png" />
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content="TalkTo" />
  <meta property="og:title" content="{title} · TalkTo" />
  <meta property="og:url" content="https://talkto.co.kr/{file}" />
  <meta property="og:image" content="https://talkto.co.kr/img/og.png" />
  <link rel="preload" href="/fonts/gowun-batang-bold.woff" as="font" type="font/woff" crossorigin />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <a class="skip" href="#main">본문으로 건너뛰기</a>

  <header class="top top--plain">
    <div class="wrap top__inner">
      <a class="brand" href="/" aria-label="TalkTo 처음으로">
        <img src="/img/logo.svg" alt="TalkTo" width="112" height="27" />
      </a>
      <a class="btn btn--small" href="https://app.talkto.co.kr/">시작하기</a>
    </div>
  </header>

  <main id="main" class="legal">
    <div class="wrap legal__inner">
      <nav class="legal__tabs" aria-label="약관 문서">
{tabs}
      </nav>
      <h1 class="title legal__title">{title}</h1>
      <p class="legal__meta"><span data-doc-version></span></p>
      <article class="legal__body" data-doc="{key}" aria-busy="true">
        <p class="legal__note">문서를 불러오는 중이에요…</p>
        <noscript><p class="legal__note">이 문서는 자바스크립트를 켜야 보여요. 바닥글의 이메일로 알려 주시면 글로 보내 드려요.</p></noscript>
      </article>
    </div>
  </main>

{footer}

  <script src="/main.js" defer></script>
  <script src="/legal.js" defer></script>
</body>
</html>
"""


def footer() -> str:
    """index.html 의 바닥글을 그대로 가져온다(사업자 정보·링크가 늘 같게)."""
    page = (LANDING / "index.html").read_text(encoding="utf-8")
    start = page.index('  <footer class="foot">')
    end = page.index("</footer>", start) + len("</footer>")
    return page[start:end]


def main() -> None:
    foot = footer()
    for file, key, title, description in PAGES:
        current = ' aria-current="page"'
        tabs = "\n".join(
            f'        <a href="/{href}"{current if href == file else ""}>{label}</a>'
            for href, label in NAV
        )
        html = TEMPLATE.format(
            file=file, key=key, title=title, description=description, tabs=tabs, footer=foot
        )
        (LANDING / file).write_text(html, encoding="utf-8")
        print("wrote", file)


if __name__ == "__main__":
    main()
