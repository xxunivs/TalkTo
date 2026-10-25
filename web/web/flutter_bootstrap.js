{{flutter_js}}
{{flutter_build_config}}

// 서비스 워커(오프라인 캐시)를 등록하지 않는다. Flutter 기본값은 예전 빌드를 먼저 보여 줘서
// 새로 배포해도 사용자가 옛 화면을 본다.
// 화면 엔진(CanvasKit)은 구글 CDN(gstatic) 대신 같이 올린 canvaskit/ 에서 읽는다. 로그인 토큰이 있는
// 이 주소에 바깥 스크립트를 들이지 않고, gstatic 이 막힌 회선에서도 빈 화면이 되지 않게.
_flutter.loader.load({
  config: {
    canvasKitBaseUrl: 'canvaskit/',
  },
});
