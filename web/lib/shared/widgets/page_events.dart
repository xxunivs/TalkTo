import 'package:flutter/foundation.dart';

import 'page_events_stub.dart' if (dart.library.js_interop) 'page_events_web.dart' as impl;

/// 화면이 가려지고 다시 보이는 것 · 화면 꺼짐 막기(웹). 그분 동의 녹음이 쓴다: 녹음하는 동안 다른 앱으로
/// 넘어가면 그 녹음은 끊긴 것으로 보고(멈추거나 고치지 못하게), 15분 녹음 중 화면이 꺼지지 않게 한다.
/// 웹이 아니면 아무것도 하지 않는다. 시험에서는 바꿔 끼운다.
class PageEvents {
  PageEvents._();

  /// 시험에서 바꿔 끼우는 화면 가려짐 듣기([listen]).
  @visibleForTesting
  static void Function() Function(void Function(bool visible) onChange) listener = impl.listenVisibility;

  /// 시험에서 바꿔 끼우는 화면 꺼짐 막기([keepScreenOn]).
  @visibleForTesting
  static Future<void Function()> Function() screenLock = impl.keepScreenOn;

  /// 화면이 보이게 되거나(true) 가려지면(false) 부른다. 돌려주는 함수를 부르면 그만 듣는다.
  static void Function() listen(void Function(bool visible) onChange) => listener(onChange);

  /// 화면 꺼짐을 막는다(브라우저가 되면). 돌려주는 함수를 부르면 놓는다. 안 되면 아무것도 하지 않는다.
  static Future<void Function()> keepScreenOn() => screenLock();
}
