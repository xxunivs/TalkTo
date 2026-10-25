import 'dart:js_interop';
import 'dart:js_interop_unsafe';

import 'package:web/web.dart' as web;

/// 탭이 가려지거나(다른 앱·다른 탭·화면 꺼짐) 다시 보일 때 부른다. 돌려주는 함수를 부르면 그만 듣는다.
void Function() listenVisibility(void Function(bool visible) onChange) {
  final listener = ((web.Event _) {
    onChange(web.document.visibilityState == 'visible');
  }).toJS;
  web.document.addEventListener('visibilitychange', listener);
  return () => web.document.removeEventListener('visibilitychange', listener);
}

/// 화면 꺼짐 막기(Screen Wake Lock). 지원하지 않거나 거절되면 그냥 넘어간다.
Future<void Function()> keepScreenOn() async {
  try {
    if (!web.window.navigator.has('wakeLock')) return () {};
    final sentinel = await web.window.navigator.wakeLock.request('screen').toDart;
    return () {
      try {
        sentinel.release();
      } catch (_) {
        // 이미 풀렸다(화면이 가려지면 브라우저가 먼저 푼다)
      }
    };
  } catch (_) {
    return () {};
  }
}
