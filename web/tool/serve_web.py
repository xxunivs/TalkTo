"""로컬 확인용 웹 서버: build/web 을 캐시 없이 내보낸다.

python -m http.server 는 캐시 헤더를 안 보내서, 다시 빌드해도 브라우저가 예전 파일(글꼴 목록 등)을
계속 썼다. 모든 응답에 Cache-Control: no-store 를 붙인다.

예전 빌드가 등록한 Flutter 서비스 워커(오프라인 캐시)는 옛 화면을 먼저 보여 준다. 그 주소로
오는 요청에는 '스스로 지우는' 워커를 돌려줘서 캐시를 비우고 등록을 풀고 새로 고친다.
(지금 빌드는 web/flutter_bootstrap.js 에서 워커를 등록하지 않는다.)

사용: py -3.12 tool/serve_web.py [포트=8090]
"""

import functools
import http.server
import pathlib
import sys

KILL_SWITCH = b"""// TalkTo: remove the old offline cache so the latest build loads.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((key) => caches.delete(key)));
    await self.registration.unregister();
    const windows = await self.clients.matchAll({ type: 'window' });
    windows.forEach((client) => client.navigate(client.url));
  })());
});
"""


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/flutter_service_worker.js":
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(KILL_SWITCH)))
            self.end_headers()
            self.wfile.write(KILL_SWITCH)
            return
        super().do_GET()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    root = pathlib.Path(__file__).resolve().parent.parent / "build" / "web"
    handler = functools.partial(NoCacheHandler, directory=str(root))
    print(f"http://localhost:{port}  ({root})", flush=True)
    http.server.ThreadingHTTPServer(("", port), handler).serve_forever()
