import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/router.dart';
import 'package:talkto_fe/core/session.dart';
import 'package:talkto_fe/features/legal/support_page.dart';
import 'package:talkto_fe/features/share/pending_invite.dart';

// 시험 자료는 모두 지어낸 것이다.
const _token = 'tok_TEST1234abcd';

/// 라우터 맨 위 redirect 를 그 주소로 한 번 돌린 결과(그대로면 null).
/// 고객센터(GET /support)만 답하는 가짜 서버. 다른 주소는 404.
class _SupportServer implements HttpClientAdapter {
  final log = <String>[];

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    log.add('${options.method} ${options.path}');
    final ok = options.method == 'GET' && options.path == '/support';
    return ResponseBody.fromString(
      jsonEncode(ok
          ? {
              'data': {'kakaoChannelUrl': null, 'hours': '평일 10시~18시', 'email': 'help@example.com', 'inquiryCode': 'K7M3-Q9XA'},
            }
          : {'detail': '없음'}),
      ok ? 200 : 404,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

Future<String?> _top(WidgetTester tester, String location) async {
  await tester.pumpWidget(const MaterialApp(home: SizedBox()));
  final context = tester.element(find.byType(SizedBox));
  final config = router.configuration;
  return config.topRedirect(context, config.buildTopLevelGoRouterState(config.findMatch(Uri.parse(location))));
}

void main() {
  tearDown(() {
    session.user = null;
    PendingInvite.token = null;
    PendingInvite.ownerName = null;
    PendingInvite.savedAt = null;
  });

  testWidgets('예전 똑 닮은 목소리 주소(/p/:id/voice)는 그 대화 화면으로 보낸다', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: SizedBox()));
    final context = tester.element(find.byType(SizedBox));
    final config = router.configuration;
    final result = await config.redirect(
      context,
      config.findMatch(Uri.parse('/p/abc-123/voice')),
      redirectHistory: [],
    );
    expect(result.isError, isFalse);
    expect(result.uri.toString(), '/p/abc-123');
  });

  group('가족 초대(/join/:token)', () {
    testWidgets('로그인 전에도 초대 화면은 그대로 열린다(다른 화면은 로그인으로)', (tester) async {
      expect(await _top(tester, '/join/$_token'), isNull);
      expect(await _top(tester, '/p/abc'), '/login');
    });

    testWidgets('로그인한 뒤 /login 은 적어 둔 초대로, 없으면 첫 화면으로', (tester) async {
      session.user = {'id': 'u1', 'name': '시험', 'consentsNeeded': <String>[]};
      expect(await _top(tester, '/login'), '/');
      PendingInvite.token = _token;
      PendingInvite.savedAt = DateTime.now();
      expect(await _top(tester, '/login'), '/join/$_token');
      // 하루가 지난 초대는 버린다
      PendingInvite.savedAt = DateTime.now().subtract(const Duration(hours: 25));
      expect(await _top(tester, '/login'), '/');
    });

    testWidgets('필수 동의가 남았으면 초대는 동의 화면을 거쳐 돌아온다', (tester) async {
      session.user = {'id': 'u1', 'name': '시험', 'consentsNeeded': ['terms']};
      expect(await _top(tester, '/join/$_token'), '/consent?from=%2Fjoin%2F$_token');
    });

    testWidgets('동의를 마친 /consent 는 from 이 없으면 적어 둔 초대로 간다', (tester) async {
      session.user = {'id': 'u1', 'name': '시험', 'consentsNeeded': <String>[]};
      expect(await _top(tester, '/consent'), '/');
      PendingInvite.token = _token;
      PendingInvite.savedAt = DateTime.now();
      expect(await _top(tester, '/consent'), '/join/$_token');
      // from 이 있으면 그것이 먼저다
      expect(await _top(tester, '/consent?from=%2Fplans'), '/plans');
    });

    testWidgets('초대 화면과 가족과 함께 화면 주소가 있다', (tester) async {
      expect(router.configuration.findMatch(Uri.parse('/join/$_token')).isError, isFalse);
      expect(router.configuration.findMatch(Uri.parse('/p/abc/family')).isError, isFalse);
    });

    testWidgets('가입할 때 모든 동의를 마친 가족은 초대 화면과 대화를 바로 연다', (tester) async {
      session.user = {'id': 'u-m', 'name': '시험', 'consentsNeeded': <String>[]};
      PendingInvite.token = _token;
      PendingInvite.savedAt = DateTime.now();
      expect(await _top(tester, '/join/$_token'), isNull);
      expect(await _top(tester, '/consent'), '/join/$_token');
      expect(await _top(tester, '/p/abc'), isNull);
      expect(await _top(tester, '/'), isNull);
    });
  });

  group('모든 필수 동의는 가입할 때', () {
    const voice = ['voice_data', 'voice_synthesis', 'overseas_elevenlabs'];

    testWidgets('녹음·목소리 동의가 빠졌으면 새 대화 상대 만들기도 다른 화면처럼 동의 화면부터', (tester) async {
      session.user = {'id': 'u1', 'name': '시험', 'consentsNeeded': voice};
      expect(await _top(tester, '/new'), '/consent?from=%2Fnew');
      expect(await _top(tester, '/plans'), '/consent?from=%2Fplans');
      expect(await _top(tester, '/join/$_token'), '/consent?from=%2Fjoin%2F$_token');
      expect(await _top(tester, '/consent'), isNull);
    });

    testWidgets('동의를 마쳤으면 새 대화 상대 만들기를 바로 연다(만들기 전에 따로 묻는 화면이 없다)', (tester) async {
      session.user = {'id': 'u1', 'name': '시험', 'consentsNeeded': <String>[]};
      expect(await _top(tester, '/new'), isNull);
      // 예전 만들기 전 동의 주소(/consent?for=create)는 돌아갈 곳으로 보낸다
      expect(await _top(tester, '/consent?for=create&from=%2Fnew'), '/new');
      expect(await _top(tester, '/consent?for=create'), '/');
    });

    testWidgets('예전 만들기 전 동의 주소도 동의가 빠졌으면 동의 화면 그대로(돌아갈 곳은 from)', (tester) async {
      session.user = {'id': 'u1', 'name': '시험', 'consentsNeeded': voice};
      expect(await _top(tester, '/consent?for=create&from=%2Fnew'), isNull);
    });

    testWidgets('관리자는 동의 화면을 거치지 않는다', (tester) async {
      session.user = {'id': 'admin', 'name': '시험', 'role': 'ADMIN', 'consentsNeeded': voice};
      expect(await _top(tester, '/new'), isNull);
    });
  });

  testWidgets('고객센터 주소가 있다(내 계정 메뉴에서, 대화 화면에서는 그 대화 상대 아래)', (tester) async {
    expect(router.configuration.findMatch(Uri.parse('/support')).isError, isFalse);
    expect(router.configuration.findMatch(Uri.parse('/p/abc/support')).isError, isFalse);
    session.user = {'id': 'u1', 'name': '시험', 'consentsNeeded': <String>[]};
    expect(await _top(tester, '/support'), isNull);
    expect(await _top(tester, '/p/abc/support'), isNull);
  });

  testWidgets('그분 동의 녹음·법정대리인·유족 확인·알림 주소가 있고, 관리자 새 화면은 관리자만', (tester) async {
    String path(String location) => router.configuration.findMatch(Uri.parse(location)).fullPath;
    expect(path('/p/abc/consent'), '/p/:id/consent');
    expect(path('/p/abc/guardian'), '/p/:id/guardian');
    expect(path('/p/abc/bereaved'), '/p/:id/bereaved');
    expect(path('/notices'), '/notices');
    expect(path('/admin/consents'), '/admin/consents');
    expect(path('/admin/consents/r1'), '/admin/consents/:id');
    // 대화 상대 관리(/admin/personas/:id)와 기억 인터뷰 넣기(/admin/personas/:id/interview)는 다른 화면이다
    expect(path('/admin/personas/abc'), '/admin/personas/:id');
    expect(path('/admin/personas/abc/interview'), '/admin/personas/:id/interview');
    for (final admin in ['/admin/holds', '/admin/unable', '/admin/payments', '/admin/guardian-sms']) {
      expect(path(admin), admin);
    }

    session.user = {'id': 'u1', 'name': '시험', 'consentsNeeded': <String>[]};
    expect(await _top(tester, '/p/abc/consent'), isNull);
    expect(await _top(tester, '/notices'), isNull);
    expect(await _top(tester, '/admin/consents'), '/');
    expect(await _top(tester, '/admin/guardian-sms'), '/');
    // 동의 게이트는 새 화면에도 같다
    session.user = {'id': 'u1', 'name': '시험', 'consentsNeeded': ['terms']};
    expect(await _top(tester, '/p/abc/consent'), '/consent?from=%2Fp%2Fabc%2Fconsent');
    session.user = {'id': 'admin', 'name': '시험', 'role': 'ADMIN', 'consentsNeeded': <String>[]};
    expect(await _top(tester, '/admin/consents'), isNull);
  });

  // 바뀐 약관을 다시 받는 동안(동의 게이트)에도 물어볼 곳이 있어야 한다. 서버 GET /support 도 동의 전에 열린다.
  // 바깥 틀(목록·요금제를 읽어 동의 전에는 403)은 거치지 않는다. 이 시험은 실제 라우터를 움직이니 맨 끝에 둔다.
  testWidgets('필수 동의 전에도 동의 화면의 고객센터(/consent/support)는 열린다', (tester) async {
    session.user = {'id': 'u1', 'name': '시험', 'consentsNeeded': ['terms']};
    expect(SupportPage.beforeConsentPath, '/consent/support');
    expect(await _top(tester, '/consent/support'), isNull);
    // 바깥 틀 안의 고객센터는 동의 화면을 거친다
    expect(await _top(tester, '/support'), '/consent?from=%2Fsupport');

    final server = _SupportServer();
    ApiClient.useAdapterForTests(server);
    await tester.pumpWidget(MaterialApp.router(routerConfig: router));
    router.go('/consent/support');
    await tester.pumpAndSettle();
    expect(router.routerDelegate.currentConfiguration.uri.path, '/consent/support');
    final page = tester.widget<SupportPage>(find.byType(SupportPage));
    expect(page.beforeConsent, isTrue);
    expect(find.text('K7M3-Q9XA'), findsOneWidget);
    expect(server.log, contains('GET /support'));
    // 목록·요금제(바깥 틀)는 읽지 않는다
    expect(server.log.where((k) => k.startsWith('GET /personas') || k.startsWith('GET /billing')), isEmpty);
  });
}
