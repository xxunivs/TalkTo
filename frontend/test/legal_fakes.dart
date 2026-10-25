import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:record/record.dart';

import 'package:talkto_fe/shared/widgets/voice_input.dart';

// 2026-10-22 법률 자문 화면 시험이 같이 쓰는 가짜 서버·녹음기. 시험 자료는 모두 지어낸 것이다.

/// 서버가 오류로 답할 때.
class Fail {
  final int status;
  final Map<String, dynamic> body;

  const Fail(this.status, this.body);
}

/// 서버 대신 답한다. 'GET /personas/p1' 처럼 메서드와 경로로 고르고, 받은 요청과 몸통(글)을 적어 둔다.
/// 정해 두지 않은 주소는 404. 답이 [Fail] 이면 그 상태로, [Map]·[List]·null 이면 {data: 답}.
/// [withMeta] 의 주소는 {data, meta} 그대로 돌려준다(목록 + 안 읽은 수).
class FakeServer implements HttpClientAdapter {
  final Map<String, Object? Function()> routes;
  final Map<String, Object? Function()> raw;
  final log = <String>[];
  final bodies = <String, List<String>>{};
  final queries = <String, List<Map<String, dynamic>>>{};

  FakeServer(this.routes, {this.raw = const {}});

  int count(String key) => log.where((k) => k == key).length;

  /// 마지막으로 받은 JSON 몸통.
  Map<String, dynamic> json(String key) => Map<String, dynamic>.from(jsonDecode(bodies[key]!.last) as Map);

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    final bytes = <int>[];
    if (requestStream != null) {
      await for (final chunk in requestStream) {
        bytes.addAll(chunk);
      }
    }
    final key = '${options.method} ${options.path}';
    log.add(key);
    bodies.putIfAbsent(key, () => []).add(utf8.decode(bytes, allowMalformed: true));
    queries.putIfAbsent(key, () => []).add(options.queryParameters);
    final whole = raw[key];
    if (whole != null) return _json(200, whole());
    final handler = routes[key];
    final result = handler == null ? const Fail(404, {'detail': '찾지 못했어요.'}) : handler();
    if (result is Fail) return _json(result.status, result.body);
    return _json(200, {'data': result});
  }

  @override
  void close({bool force = false}) {}
}

ResponseBody _json(int status, Object? body) => ResponseBody.fromString(
      jsonEncode(body),
      status,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );

/// multipart 몸통에서 칸 하나의 값.
String? formField(String body, String name) {
  final match = RegExp('name="$name"\\r\\n\\r\\n([^\\r]*)\\r\\n').firstMatch(body);
  return match?.group(1);
}

/// 마이크 대신. 녹음하면 [bytes] 를 준다.
class FakeRecorder implements VoiceRecorder {
  bool permission = true;
  bool recordingNow = false;
  List<int> bytes = [1, 2, 3, 4];
  int started = 0;
  int stopped = 0;

  @override
  Future<bool> hasPermission() async => permission;

  @override
  Future<bool> isEncoderSupported(AudioEncoder encoder) async => encoder == AudioEncoder.opus;

  @override
  Future<void> start(RecordConfig config, {required String path}) async {
    started++;
    recordingNow = true;
  }

  @override
  Future<bool> isRecording() async => recordingNow;

  @override
  Future<String?> stop() async {
    stopped++;
    recordingNow = false;
    return 'blob:fake';
  }

  @override
  Future<List<int>> read(String path) async => bytes;

  @override
  Future<void> cancel() async => recordingNow = false;

  @override
  Future<void> dispose() async {}
}

String plainText(Widget w) {
  if (w is Text) return (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', '');
  if (w is RichText) return w.text.toPlainText().replaceAll('⁠', '');
  return '';
}

/// 글 일부로 찾는다(이음표 keepAll 을 빼고).
Finder has(String part) => find.byWidgetPredicate((w) => w is Text && plainText(w).contains(part));

/// 글 전체로 찾는다(이음표를 빼고).
Finder shown(String text) => find.byWidgetPredicate((w) => w is Text && plainText(w) == text);

Finder snack(String part) => find.descendant(of: find.byType(SnackBar), matching: has(part));

/// 가짜 서버 응답(Future)을 받고 그린다. 숨 쉬는 원처럼 계속 도는 그림이 있어 pumpAndSettle 대신 나눠 그린다.
Future<void> settle(WidgetTester tester, {int frames = 12}) async {
  for (var i = 0; i < frames; i++) {
    await tester.pump(const Duration(milliseconds: 30));
  }
}

/// 보이는 곳으로 내려 누른다.
Future<void> tapIn(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder.first);
  await settle(tester, frames: 4);
  await tester.tap(finder.first);
  await settle(tester);
}

void phone(WidgetTester tester, {double height = 1600}) {
  tester.view.physicalSize = Size(390, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}
