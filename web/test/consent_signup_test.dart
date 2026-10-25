import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/session.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/auth/consent_page.dart';
import 'package:talkto_fe/features/auth/login_page.dart';
import 'package:talkto_fe/features/legal/legal_api.dart';
import 'package:talkto_fe/features/personas/persona_create_page.dart';
import 'package:talkto_fe/features/share/pending_invite.dart';

// 모든 필수 동의는 가입할 때 받는다(초대받은 가족도). 대화 상대를 만들기 전에 따로 묻지 않는다.
// 시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다).

const _token = 'tok_TEST1234abcdEFGH';

const _all = [
  'age14',
  'service',
  'terms',
  'privacy',
  'overseas',
  'overseas_fish',
  'voice_data',
  'voice_synthesis',
  'overseas_elevenlabs',
];
const _voice = ['voice_data', 'voice_synthesis', 'overseas_elevenlabs'];

/// 서버 GET /legal 의 account 와 같은 모양(서버 순서 그대로).
const _accountItems = [
  {'type': 'age14', 'label': '만 14세 이상이에요', 'doc': null, 'kind': 'confirm'},
  {'type': 'service', 'label': 'TalkTo의 대화 상대는 AI가 만든 것이라는 점을 이해했어요', 'doc': 'service', 'kind': 'confirm'},
  {'type': 'terms', 'label': '이용약관에 동의해요', 'doc': 'terms', 'kind': 'consent'},
  {'type': 'privacy', 'label': '개인정보 수집·이용 안내를 확인했어요', 'doc': 'privacy', 'kind': 'confirm'},
  {'type': 'overseas', 'label': '해외 회사에 맡기는 처리를 확인했어요', 'doc': 'overseas', 'kind': 'confirm'},
  {'type': 'overseas_fish', 'label': 'Fish Audio(미국)로 보내는 데 동의해요', 'doc': 'overseas', 'kind': 'consent'},
  {'type': 'voice_data', 'label': '녹음 파일 처리에 동의해요', 'doc': 'voice_data', 'kind': 'consent'},
  {'type': 'voice_synthesis', 'label': '그분 목소리 만들기에 동의해요', 'doc': 'voice_synthesis', 'kind': 'consent'},
  {'type': 'overseas_elevenlabs', 'label': 'ElevenLabs(미국)로 보내는 데 동의해요', 'doc': 'overseas', 'kind': 'consent'},
];

Future<List<Map<String, dynamic>>> _items() async => [for (final item in _accountItems) Map<String, dynamic>.from(item)];

Future<Map<String, dynamic>> _noProviders() async => const {};

/// 서버가 오류로 답할 때.
class _Fail {
  final int status;
  final Map<String, dynamic> body;

  const _Fail(this.status, this.body);
}

/// 서버 대신 답한다. 'POST /consents/accept' 처럼 메서드와 경로로 고르고, 받은 몸통을 적어 둔다.
class _FakeServer implements HttpClientAdapter {
  final Map<String, Object? Function(dynamic body)> routes;
  final log = <String>[];
  final bodies = <String, dynamic>{};

  _FakeServer(this.routes);

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    final chunks = <int>[];
    if (requestStream != null) {
      await for (final chunk in requestStream) {
        chunks.addAll(chunk);
      }
    }
    final body = chunks.isEmpty ? null : jsonDecode(utf8.decode(chunks));
    final key = '${options.method} ${options.path}';
    log.add(key);
    bodies[key] = body;
    final handler = routes[key];
    final result = handler == null ? const _Fail(404, {'detail': '찾지 못했어요.'}) : handler(body);
    if (result is _Fail) return _json(result.status, result.body);
    return _json(200, {'data': result});
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

/// 화면 글은 단어 중간에서 끊기지 않게 이음표(keepAll)가 들어 있어, 빼고 찾는다.
String _plain(Widget w) {
  if (w is Text) return (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', '');
  if (w is RichText) return w.text.toPlainText().replaceAll('⁠', '');
  return '';
}

Finder _shown(String text) => find.byWidgetPredicate((w) => w is Text && _plain(w) == text);
Finder _has(String part) => find.byWidgetPredicate((w) => w is Text && _plain(w).contains(part));
Finder _item(String label) => _shown('[필수] $label');

/// 가짜 서버 응답(Future)을 다 받고 화면을 그린다.
Future<void> _settle(WidgetTester tester) async {
  for (var i = 0; i < 8; i++) {
    await tester.pump(const Duration(milliseconds: 20));
  }
  await tester.pumpAndSettle();
}

Future<void> _tap(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await _settle(tester);
}

/// 동의 화면 하나와 그 화면이 보내는 곳들.
Widget _consentApp(String initial) => MaterialApp.router(
      theme: buildTheme(),
      routerConfig: GoRouter(
        initialLocation: initial,
        routes: [
          GoRoute(path: '/', builder: (context, state) => const Scaffold(body: Text('첫 화면'))),
          GoRoute(path: '/new', builder: (context, state) => const Scaffold(body: Text('새 대화 상대'))),
          GoRoute(path: '/join/:token', builder: (context, state) => const Scaffold(body: Text('초대 화면'))),
          GoRoute(
            path: '/consent',
            builder: (context, state) => ConsentPage(
              from: state.uri.queryParameters['from'],
              loadItems: _items,
              showBusiness: false,
            ),
          ),
          GoRoute(path: '/consent/support', builder: (context, state) => const Scaffold(body: Text('고객센터 화면'))),
        ],
      ),
    );

/// 새 대화 상대 만들기 화면과 그 화면이 보내는 곳들.
GoRouter _createRouter() => GoRouter(
      initialLocation: '/new',
      routes: [
        GoRoute(path: '/', builder: (context, state) => const Scaffold(body: Text('첫 화면'))),
        GoRoute(path: '/new', builder: (context, state) => const PersonaCreatePage()),
        GoRoute(path: '/consent', builder: (context, state) => Scaffold(body: Text('동의 ${state.uri}'))),
        GoRoute(path: '/p/:id', builder: (context, state) => Scaffold(body: Text('만든 대화 상대 ${state.pathParameters['id']}'))),
      ],
    );

const _meta = {'statuses': <String, dynamic>{}, 'relations': <String>[], 'myRelations': <String>[]};

void main() {
  setUp(() {
    LegalApi.forget();
    PendingInvite.token = null;
    PendingInvite.ownerName = null;
    PendingInvite.savedAt = null;
  });

  tearDown(() {
    session.user = null;
  });

  testWidgets('초대받아 처음 온 가족도 아홉 가지 모두 한 화면에서 동의하고 초대 화면으로', (tester) async {
    session.user = {'id': 'u-m', 'name': '김철수', 'consentsNeeded': _all};
    PendingInvite.token = _token;
    PendingInvite.ownerName = '지은';
    final server = _FakeServer({
      'POST /consents/accept': (_) => {'consentsNeeded': <String>[]},
    });
    ApiClient.useAdapterForTests(server);
    await tester.pumpWidget(_consentApp('/consent?from=%2Fjoin%2F$_token'));
    await _settle(tester);

    expect(_has('지은님의 초대를 이어 가려면'), findsOneWidget);
    expect(_shown('TalkTo는 이런 서비스예요'), findsOneWidget);
    expect(_has('대화 상대를 만든 가족은 가족 공유로 다른 가족을 초대해'), findsOneWidget);
    // 만들 때 따로 묻는다는 예전 안내는 없다
    expect(_has('처음 대화 상대를 만들 때'), findsNothing);
    for (final item in _accountItems) {
      expect(_item(item['label']!), findsOneWidget);
    }
    expect(find.text('동의하지 않고 나가기'), findsOneWidget);
    expect(find.text('지금은 만들지 않기'), findsNothing);

    await _tap(tester, find.text('모두 동의해요'));
    await _tap(tester, _shown('동의하고 시작하기'));
    expect(server.bodies['POST /consents/accept'], {'types': _all});
    expect(session.needsConsent, isFalse);
    expect(session.user!.containsKey('creatorConsentsNeeded'), isFalse);
    expect(_shown('초대 화면'), findsOneWidget);
  });

  testWidgets('동의하기 전에도 고객센터에 물어볼 수 있다(동의하지 않고 나가기 옆)', (tester) async {
    session.user = {'id': 'u1', 'name': '김철수', 'consentsNeeded': _all};
    final server = _FakeServer({});
    ApiClient.useAdapterForTests(server);
    await tester.pumpWidget(_consentApp('/consent'));
    await _settle(tester);
    expect(find.text('동의하지 않고 나가기'), findsOneWidget);
    await _tap(tester, find.text('고객센터'));
    expect(_shown('고객센터 화면'), findsOneWidget);
    // 동의는 보내지 않았다
    expect(server.log, isNot(contains('POST /consents/accept')));
    expect(session.needsConsent, isTrue);
  });

  testWidgets('예전에 여섯 가지만 받은 가족은 남은 녹음·목소리 동의를 바뀐 내용으로 받는다', (tester) async {
    session.user = {'id': 'u-m', 'name': '김철수', 'consentsNeeded': _voice};
    final server = _FakeServer({
      'POST /consents/accept': (_) => {'consentsNeeded': <String>[]},
    });
    ApiClient.useAdapterForTests(server);
    await tester.pumpWidget(_consentApp('/consent?from=%2Fnew'));
    await _settle(tester);
    expect(_shown('약관과 안내가 바뀌었어요'), findsOneWidget);
    for (final label in ['녹음 파일 처리에 동의해요', '그분 목소리 만들기에 동의해요', 'ElevenLabs(미국)로 보내는 데 동의해요']) {
      expect(_item(label), findsOneWidget);
    }
    expect(_item('이용약관에 동의해요'), findsNothing);
    expect(_item('만 14세 이상이에요'), findsNothing);
    expect(find.text('회원 탈퇴'), findsOneWidget);

    await _tap(tester, find.text('모두 동의해요'));
    await _tap(tester, _shown('동의하고 계속하기'));
    expect(server.bodies['POST /consents/accept'], {'types': _voice});
    expect(_shown('새 대화 상대'), findsOneWidget);
  });

  testWidgets('이메일 회원가입 칸에도 아홉 가지 모두', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: const LoginPage(loadProviders: _noProviders, loadConsentItems: _items),
    ));
    await tester.ensureVisible(find.text('이메일로 계속하기'));
    await tester.tap(find.text('이메일로 계속하기'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('회원가입'));
    await tester.pumpAndSettle();
    for (final item in _accountItems) {
      expect(_item(item['label']!), findsOneWidget);
    }
    // 체크 칸: 모두 동의 + 아홉 가지
    expect(find.byType(Checkbox), findsNWidgets(10));
  });

  testWidgets('동의를 마친 분은 새 대화 상대를 만들 때 더 묻지 않는다', (tester) async {
    session.user = {'id': 'u1', 'name': '김철수', 'consentsNeeded': <String>[]};
    final server = _FakeServer({
      'GET /intake/meta': (_) => _meta,
      'POST /personas': (_) => {'id': 'p-new'},
    });
    ApiClient.useAdapterForTests(server);
    await tester.pumpWidget(MaterialApp.router(theme: buildTheme(), routerConfig: _createRouter()));
    await _settle(tester);
    await _tap(tester, _shown('다음: 녹음 올리기'));
    expect(server.log, contains('POST /personas'));
    expect(server.log, isNot(contains('POST /consents/accept')));
    expect(_shown('만든 대화 상대 p-new'), findsOneWidget);
  });

  testWidgets('만들기가 동의 게이트에 막히면 그 자리에서 서버 안내를 보인다(따로 가는 동의 화면이 없다)', (tester) async {
    session.user = {'id': 'u1', 'name': '김철수', 'consentsNeeded': <String>[]};
    final server = _FakeServer({
      'GET /intake/meta': (_) => _meta,
      'POST /personas': (_) => const _Fail(403, {
            'detail': '서비스 이용 동의가 필요해요.',
            'code': 'consent_required',
            'missing': ['voice_data'],
          }),
    });
    ApiClient.useAdapterForTests(server);
    final router = _createRouter();
    await tester.pumpWidget(MaterialApp.router(theme: buildTheme(), routerConfig: router));
    await _settle(tester);
    await _tap(tester, _shown('다음: 녹음 올리기'));
    expect(router.routerDelegate.currentConfiguration.uri.path, '/new');
    expect(_has('서비스 이용 동의가 필요해요.'), findsWidgets);
    expect(_has('for=create'), findsNothing);
  });

  testWidgets('가입 동의 화면 아래에 개인정보 처리방침 링크(바닥글)가 있다', (tester) async {
    session.user = {'id': 'u1', 'name': '김철수', 'consentsNeeded': _all};
    ApiClient.useAdapterForTests(_FakeServer({}));
    await tester.pumpWidget(_consentApp('/consent'));
    await _settle(tester);
    expect(_shown('개인정보 처리방침'), findsOneWidget);
    expect(_shown('이용약관'), findsOneWidget);
    expect(_shown('사업자 정보'), findsOneWidget);
  });

  testWidgets('다시 동의 화면에서 탈퇴하면 서버가 준 지운 뒤 안내를 창으로 보이고, 확인한 뒤 로그아웃한다', (tester) async {
    SharedPreferences.setMockInitialValues({});
    session.user = {'id': 'u1', 'name': '김철수', 'consentsNeeded': const ['terms']};
    const message = 'TalkTo 서버에서 지웠어요. 서버 백업에는 10월 30일까지 남았다가 저절로 지워지고, '
        "해외 회사에 남는 자료는 '해외 회사로 보내는 정보'에 적은 대로예요.";
    final server = _FakeServer({
      'DELETE /auth/me': (_) => {
            'deleted': true,
            'doneAt': '2026-10-23T01:00:00Z',
            'backupUntil': '2026-10-30',
            'vendorOk': null,
            'message': message,
          },
      'POST /auth/logout': (_) => {'ok': true},
    });
    ApiClient.useAdapterForTests(server);
    await tester.pumpWidget(_consentApp('/consent'));
    await _settle(tester);
    await _tap(tester, find.widgetWithText(TextButton, '회원 탈퇴'));
    await tester.enterText(find.byType(TextField), '탈퇴');
    await tester.pump();
    await _tap(tester, find.widgetWithText(FilledButton, '탈퇴하기'));
    expect(server.bodies['DELETE /auth/me'], {'confirm': '탈퇴'});
    expect(_shown('탈퇴했어요'), findsOneWidget);
    expect(_has('서버 백업에는 10월 30일까지 남았다가 저절로 지워지고'), findsOneWidget);
    expect(_has('그동안 TalkTo와 함께해 주셔서 고마워요.'), findsOneWidget);
    // 안내를 확인하기 전에는 로그아웃하지 않는다
    expect(server.log, isNot(contains('POST /auth/logout')));
    expect(session.user, isNotNull);
    await _tap(tester, find.widgetWithText(FilledButton, '확인'));
    expect(server.log, contains('POST /auth/logout'));
    expect(session.user, isNull);
  });
}
