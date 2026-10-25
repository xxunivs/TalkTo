import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/legal/legal_api.dart';
import 'package:talkto_fe/features/legal/legal_footer.dart';

// 바닥 법정 표시(개인정보 처리방침 링크·사업자 정보)와 랜딩 첫 화면. 시험 값은 모두 지어낸 것이다.

const _business = {
  'name': '예시상호',
  'owner': '김예시',
  'email': 'help@example.com',
  'phone': '010-0000-0000',
  'regNo': '000-00-00000',
  'address': '서울시 예시구 예시로 1, 101동 101호',
  'mailOrderNo': null,
  'hosting': 'Amazon Web Services(AWS)',
};

Future<Map<String, dynamic>> _loadBusiness() async => Map<String, dynamic>.from(_business);

/// 서버 대신 답한다. 'GET /legal' 처럼 메서드와 경로로 고른다.
class _FakeServer implements HttpClientAdapter {
  final Map<String, Object? Function()> routes;
  final log = <String>[];

  _FakeServer(this.routes);

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    final key = '${options.method} ${options.path}';
    log.add(key);
    final handler = routes[key];
    if (handler == null) {
      return ResponseBody.fromString(jsonEncode({'detail': '찾지 못했어요.'}), 404, headers: {
        Headers.contentTypeHeader: ['application/json'],
      });
    }
    return ResponseBody.fromString(jsonEncode({'data': handler()}), 200, headers: {
      Headers.contentTypeHeader: ['application/json'],
    });
  }

  @override
  void close({bool force = false}) {}
}

/// 화면 글은 단어 중간에서 끊기지 않게 이음표(keepAll)가 들어 있어, 빼고 찾는다.
String _plain(Widget w) {
  if (w is Text) return (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', '');
  return '';
}

Finder _shown(String text) => find.byWidgetPredicate((w) => w is Text && _plain(w) == text);
Finder _has(String part) => find.byWidgetPredicate((w) => w is Text && _plain(w).contains(part));

Future<void> _settle(WidgetTester tester) async {
  for (var i = 0; i < 6; i++) {
    await tester.pump(const Duration(milliseconds: 20));
  }
  await tester.pumpAndSettle();
}

void main() {
  setUp(LegalApi.forget);

  testWidgets('기본: 개인정보 처리방침(굵게)·이용약관·환불 정책과 사업자 정보를 글로 그대로 보인다', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: const Scaffold(body: LegalFooter(loadBusiness: _loadBusiness)),
    ));
    await _settle(tester);
    final privacy = tester.widget<Text>(_shown('개인정보 처리방침'));
    expect(privacy.style?.fontWeight, FontWeight.w700);
    expect(_shown('이용약관'), findsOneWidget);
    expect(_shown('환불 정책'), findsOneWidget);
    // 전자상거래법 제10조 제1항 항목이 단추 뒤가 아니라 글로 바로 보인다(값은 서버에서)
    expect(_shown('상호 예시상호 · 대표 김예시'), findsOneWidget);
    expect(_shown('주소 서울시 예시구 예시로 1, 101동 101호'), findsOneWidget);
    expect(_shown('전화 010-0000-0000 · 이메일 help@example.com'), findsOneWidget);
    // 신고번호를 서버가 비워 보내도 '신고 진행 중'이 아니라 신고한 번호를 보인다
    expect(_shown('사업자등록번호 000-00-00000 · 통신판매업 신고번호 제2026-서울용산-1112호'), findsOneWidget);
    expect(_has('신고 진행 중'), findsNothing);
  });

  testWidgets('짧게(compact): 한 줄 링크만, 사업자 정보는 눌러야 창으로', (tester) async {
    ApiClient.useAdapterForTests(_FakeServer({
      'GET /legal': () => {'docs': const [], 'account': const [], 'business': _business},
    }));
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: const Scaffold(body: Center(child: LegalFooter(compact: true))),
    ));
    await _settle(tester);
    expect(_shown('개인정보 처리방침'), findsOneWidget);
    expect(_shown('이용약관'), findsOneWidget);
    expect(_shown('사업자 정보'), findsOneWidget);
    expect(_has('상호 '), findsNothing);

    await tester.tap(_shown('사업자 정보'));
    await _settle(tester);
    expect(find.byType(AlertDialog), findsOneWidget);
    expect(_shown('상호 예시상호 · 대표 김예시'), findsOneWidget);
  });

  testWidgets('개인정보 처리방침은 로그인 없이 열리는 공개 주소(/legal/privacy_policy)로 간다', (tester) async {
    final router = GoRouter(
      routes: [
        GoRoute(
          path: '/',
          builder: (context, state) => const Scaffold(body: Center(child: LegalFooter(compact: true))),
        ),
        GoRoute(
          path: '/legal/:key',
          builder: (context, state) => Scaffold(body: Text('문서 ${state.pathParameters['key']}')),
        ),
      ],
    );
    await tester.pumpWidget(MaterialApp.router(theme: buildTheme(), routerConfig: router));
    await tester.pumpAndSettle();
    await tester.tap(_shown('개인정보 처리방침'));
    await tester.pumpAndSettle();
    expect(find.text('문서 privacy_policy'), findsOneWidget);
  });

  test('LegalApi: 결제 직전 안내·법정대리인 체크 글은 /legal 색인에서, 탈퇴는 서버 본문을 돌려준다', () async {
    final server = _FakeServer({
      'GET /legal': () => {
            'docs': const [],
            'account': const [],
            'business': _business,
            'payment': {
              'notice': '결제하시면 바로 유료 이용이 시작돼요.',
              'items': [
                {'type': 'recurring', 'label': '매달 같은 날 자동으로 결제되는 것에 동의해요.'},
                {'type': 'withdrawal_limit', 'label': '청약철회가 제한된다는 점을 확인했어요.'},
              ],
            },
            'guardian': {'attest': '법정대리인이에요.', 'consent': '동의해요.', 'overseas': '해외 회사로 보내는 데 동의해요.'},
          },
      'DELETE /auth/me': () => {
            'deleted': true,
            'doneAt': '2026-10-23T01:00:00Z',
            'backupUntil': '2026-10-30',
            'vendorOk': null,
            'message': 'TalkTo 서버에서 지웠어요. 서버 백업에는 10월 30일까지 남았다가 저절로 지워져요.',
          },
    });
    ApiClient.useAdapterForTests(server);
    final payment = await LegalApi.payment();
    expect(payment['notice'], '결제하시면 바로 유료 이용이 시작돼요.');
    expect([for (final i in payment['items'] as List) (i as Map)['type']], ['recurring', 'withdrawal_limit']);
    final labels = await LegalApi.guardianLabels();
    expect(labels.keys, containsAll(['attest', 'consent', 'overseas']));
    final result = await LegalApi.deleteAccount();
    expect(result['backupUntil'], '2026-10-30');
    expect(result['message'], contains('서버 백업'));
    // 색인은 한 번만 읽었다
    expect(server.log.where((k) => k == 'GET /legal'), hasLength(1));

    // 서버가 아직 새 블록을 주지 않으면 빈 값(부르는 쪽이 결제 단추를 막는다)
    LegalApi.forget();
    ApiClient.useAdapterForTests(_FakeServer({'GET /legal': () => {'docs': const [], 'business': _business}}));
    expect(await LegalApi.payment(), isEmpty);
    expect(await LegalApi.guardianLabels(), isEmpty);
  });

  test('BusinessInfo 의 값은 서버에서만 — 화면 코드에 주소·전화·등록번호를 적지 않는다', () {
    for (final path in ['lib/features/legal/legal_footer.dart', 'lib/features/legal/legal_text.dart']) {
      final source = File(path).readAsStringSync();
      expect(RegExp(r'01[016789]-?\d{3,4}-?\d{4}').hasMatch(source), isFalse, reason: path);
      expect(RegExp(r'\d{3}-\d{2}-\d{5}').hasMatch(source), isFalse, reason: path);
      expect(source.contains('신고 진행 중'), isFalse, reason: path);
    }
  });

  group('랜딩', () {
    final index = File('landing/index.html').readAsStringSync();

    test("첫 화면(히어로)에 '개인정보 처리방침' 링크가 있다", () {
      final hero = index.substring(index.indexOf('<div class="hero__text">'), index.indexOf('<figure class="hero__visual">'));
      expect(hero, contains('<p class="note"><a href="/privacy.html">개인정보 처리방침</a></p>'));
      // 바닥글 링크도 그대로
      final foot = index.substring(index.indexOf('<footer class="foot">'));
      expect(foot, contains('<a href="/privacy.html"><strong>개인정보 처리방침</strong></a>'));
      expect(foot, contains('통신판매업 신고번호'));
    });

    test('사업자 정보: 서버가 신고번호를 비워 보내도 신고한 번호, 주소·전화는 서버 값으로만', () {
      final main = File('landing/main.js').readAsStringSync();
      expect(main, contains('b.mailOrderNo || "제2026-서울용산-1112호"'));
      expect(main.contains('신고 진행 중'), isFalse);
      expect(main, contains('b.address && "주소 " + b.address'));
      expect(main, contains('b.phone && "전화 " + b.phone'));
    });

    test("치매·'대화가 어려우신 분'을 대상으로 말하지 않는다", () {
      for (final file in Directory('landing').listSync().whereType<File>()) {
        if (!file.path.endsWith('.html') && !file.path.endsWith('.js')) continue;
        final text = file.readAsStringSync();
        expect(text.contains('치매'), isFalse, reason: file.path);
        expect(text.contains('대화가 어려우'), isFalse, reason: file.path);
      }
    });

    test('살아 계신 분은 동의 녹음, 지운 자료는 서버 백업 최대 7일', () {
      expect(index, contains('그분이 직접 말씀하시는 동의 녹음을 TalkTo가 확인한 뒤에 만들어요.'));
      expect(index, contains("앱이 보여 주는 대본대로 그분이 직접 말씀하시는 '동의 녹음'을 받고"));
      expect(index, contains('만 19세 미만 자녀는 그 아이의 부모님만 요건을 갖춰 만들 수 있어요.'));
      expect(index, contains('지금은 준비 중이라 만 14세 미만 대화 상대는 만들 수 없어요.'));
      expect(index, contains('서버 백업에 남은 것도 최대 7일 뒤 저절로 지워져요.'));
      expect(index.contains('녹음과 대화는 바로 지워요.'), isFalse);
    });

    test('약관 페이지 바닥글은 첫 화면 바닥글과 같다(tool/landing_legal_pages.py)', () {
      // 줄 끝(CRLF·LF)은 체크아웃 설정에 따라 다르므로 맞춰서 비교한다
      String foot(String html) {
        final text = html.replaceAll('\r\n', '\n');
        return text.substring(text.indexOf('  <footer class="foot">'), text.indexOf('</footer>') + 9);
      }
      for (final page in ['privacy.html', 'terms.html', 'refund.html']) {
        expect(foot(File('landing/$page').readAsStringSync()), foot(index), reason: page);
      }
    });
  });
}
