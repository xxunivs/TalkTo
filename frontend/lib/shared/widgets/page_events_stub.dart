/// 웹이 아니면(앱·시험) 화면 가려짐을 알리지 않는다.
void Function() listenVisibility(void Function(bool visible) onChange) => () {};

/// 웹이 아니면 화면 꺼짐을 막지 않는다.
Future<void Function()> keepScreenOn() async => () {};
