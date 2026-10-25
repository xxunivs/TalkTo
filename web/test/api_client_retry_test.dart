import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:talkto_fe/core/network/api_client.dart';

/// 서버 대신 정해 둔 순서대로 답한다. 받은 요청(경로·토큰·본문 길이)을 적어 둔다.
class _FakeServer implements HttpClientAdapter {
  final List<ResponseBody Function(RequestOptions, int)> replies;
  final List<String> log = [];
  _FakeServer(this.replies);

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    var length = 0;
    if (requestStream != null) {
      await for (final chunk in requestStream) {
        length += chunk.length;
      }
    }
    log.add('${options.method} ${options.path} ${options.headers['Authorization']} $length');
    return replies.removeAt(0)(options, length);
  }

  @override
  void close({bool force = false}) {}
}

ResponseBody _json(int status, Object body) => ResponseBody.fromString(
      jsonEncode(body),
      status,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );

FormData _upload() => FormData.fromMap({
      'kind': 'sms',
      'file': MultipartFile.fromBytes(List.filled(3000, 7), filename: 'a.txt'),
    });

void main() {
  late int loggedOut;

  setUp(() {
    loggedOut = 0;
    ApiClient.onUnauthorized = () => loggedOut++;
    ApiClient.onTokens = (_, __) {};
    ApiClient.setTokens('old', 'refresh-1');
  });

  test('만료된 토큰으로 파일을 올리면 새 토큰을 받아 같은 파일을 다시 보낸다', () async {
    final server = _FakeServer([
      (_, __) => _json(401, {'detail': '로그인이 풀렸어요.'}),
      (_, __) => _json(200, {'data': {'bearerToken': 'new', 'refreshToken': 'refresh-2'}}),
      (_, __) => _json(200, {'data': 'ok'}),
    ]);
    ApiClient.useAdapterForTests(server);
    final result = await ApiClient.post('/personas/p/materials', _upload()).timeout(const Duration(seconds: 5));
    expect(result, 'ok');
    expect(server.log[0], startsWith('POST /personas/p/materials Bearer old '));
    expect(server.log[1], startsWith('POST /auth/refresh'));
    expect(server.log[2], startsWith('POST /personas/p/materials Bearer new '));
    // 다시 보낸 본문도 처음과 같은 크기다(파일이 빠지지 않았다)
    expect(server.log[2].split(' ').last, server.log[0].split(' ').last);
    expect(loggedOut, 0);
  });

  test('다시 보낸 요청이 실패해도 멈추지 않고, 다음 요청은 그대로 된다', () async {
    final server = _FakeServer([
      (_, __) => _json(401, {'detail': 'x'}),
      (_, __) => _json(200, {'data': {'bearerToken': 'new', 'refreshToken': 'refresh-2'}}),
      (_, __) => _json(429, {'detail': '요청이 너무 많아요. 잠시 뒤 다시 해 주세요.'}),
      (_, __) => _json(200, {'data': 'second'}),
    ]);
    ApiClient.useAdapterForTests(server);
    await expectLater(
      ApiClient.post('/personas/p/materials', _upload()).timeout(const Duration(seconds: 5)),
      throwsA(isA<ApiException>().having((e) => e.statusCode, 'status', 429)),
    );
    expect(await ApiClient.get('/me').timeout(const Duration(seconds: 5)), 'second');
  });

  test('토큰 갱신이 잠깐 실패(서버 오류)하면 로그아웃하지 않고, 정말 만료면 로그아웃한다', () async {
    ApiClient.useAdapterForTests(_FakeServer([
      (_, __) => _json(401, {'detail': 'x'}),
      (_, __) => _json(503, {'detail': '잠시만요'}),
    ]));
    // 처음 받은 401 을 그대로 넘기지 않는다(부른 쪽이 '로그인이 풀렸다'로 알고 저장한 로그인을 지우지 않게)
    await expectLater(
      ApiClient.get('/auth/me').timeout(const Duration(seconds: 5)),
      throwsA(isA<ApiException>().having((e) => e.statusCode, 'status', isNull)),
    );
    expect(loggedOut, 0);

    ApiClient.useAdapterForTests(_FakeServer([
      (_, __) => _json(401, {'detail': 'x'}),
      (_, __) => _json(401, {'detail': '다시 로그인'}),
    ]));
    await expectLater(ApiClient.get('/auth/me').timeout(const Duration(seconds: 5)), throwsA(isA<ApiException>()));
    expect(loggedOut, 1);
  });

  test('로그인·가입이 401 이면 토큰을 새로 받으려 하지 않는다', () async {
    final server = _FakeServer([(_, __) => _json(401, {'detail': '비밀번호가 달라요'})]);
    ApiClient.useAdapterForTests(server);
    await expectLater(ApiClient.post('/auth/login', {'email': 'a', 'password': 'b'}), throwsA(isA<ApiException>()));
    expect(server.log, hasLength(1));
  });
}
