/// 브라우저 주소 다루기. 웹이 아니면(데스크톱 빌드 등) 아무것도 하지 않는다.
library;

export 'browser_stub.dart' if (dart.library.js_interop) 'browser_web.dart';
