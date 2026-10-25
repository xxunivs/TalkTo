import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/session.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/admin/support_lookup.dart';
import 'package:talkto_fe/features/legal/support_page.dart';
import 'package:talkto_fe/features/shell/app_shell.dart';

// 고객센터: 카카오톡 채널(주소가 없으면 단추를 숨긴다)·받는 때·녹음과 사진은 보내지 않는 약속·문의 코드·이메일.
// 시험 자료는 모두 지어낸 것이다.

const _personaId = '3f2a9c1d-0000-4000-8000-000000000000';

Map<String, dynamic> _support({String? kakao = 'https://pf.kakao.com/_test/chat'}) => {
      'kakaoChannelUrl': kakao,
      'hours': '평일 10시~18시',
      'email': 'help@example.com',
      'inquiryCode': 'K7M3-Q9XA',
    };

/// 화면 글은 단어 중간에서 끊기지 않게 이음표(keepAll)가 들어 있어, 빼고 찾는다(Text·SelectableText 만).
String? _plain(Widget w) {
  if (w is Text) return (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', '');
  if (w is SelectableText) return (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', '');
  return null;
}

Finder _has(String part) => find.byWidgetPredicate((w) => _plain(w)?.contains(part) ?? false);

void main() {
  tearDown(() => session.user = null);

  test('문의 줄: 회원 코드 · 대화 상대 짧은 코드 · 앱 버전', () {
    expect(SupportPage.personaCode(_personaId), '3F2A9C');
    expect(SupportPage.pathFor(_personaId), '/p/$_personaId/support');
    expect(
      SupportPage.inquiryLine('K7M3-Q9XA', personaId: _personaId, build: '20261021093000'),
      '문의 코드 K7M3-Q9XA · 대화 상대 3F2A9C · 앱 20261021093000',
    );
    expect(SupportPage.inquiryLine('K7M3-Q9XA', build: ''), '문의 코드 K7M3-Q9XA · 앱 로컬');
  });

  testWidgets('카카오톡 채널·받는 때·약속·문의 코드 복사·이메일 (휴대폰 폭)', (tester) async {
    tester.view.physicalSize = const Size(1080, 2340);
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);
    final opened = <String>[];
    final copied = <String>[];
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: SupportPage(
        personaId: _personaId,
        load: () async => _support(),
        openUrl: opened.add,
        copy: (text) async => copied.add(text),
      ),
    ));
    await tester.pumpAndSettle();

    expect(_has('카카오톡 채널로 물어보세요'), findsOneWidget);
    expect(_has('평일 10시~18시'), findsOneWidget);
    expect(_has('녹음·사진 파일은 카카오톡으로 보내지 마세요.'), findsOneWidget);
    expect(_has("녹음은 앱의 '녹음 올리기'로 올려 주세요"), findsOneWidget);
    expect(_has('채팅 첫 줄에 붙여 주세요'), findsOneWidget);
    expect(find.text('K7M3-Q9XA'), findsOneWidget);
    expect(_has('대화 상대 3F2A9C'), findsOneWidget);
    expect(_has('help@example.com'), findsOneWidget);
    // 앱 안 문의 게시판은 없다(글을 적어 보내는 칸이 없다)
    expect(find.byType(TextField), findsNothing);

    await tester.ensureVisible(find.text('문의 코드 복사하기'));
    await tester.tap(find.text('문의 코드 복사하기'));
    await tester.pumpAndSettle();
    expect(copied, ['문의 코드 K7M3-Q9XA · 대화 상대 3F2A9C · 앱 로컬']);
    expect(find.text('문의 코드를 복사했어요. 채팅 첫 줄에 붙여 주세요.'), findsOneWidget);

    await tester.ensureVisible(find.text('카카오톡 채널로 문의하기'));
    await tester.tap(find.text('카카오톡 채널로 문의하기'));
    await tester.pumpAndSettle();
    expect(opened, ['https://pf.kakao.com/_test/chat']);
  });

  test('카카오톡 채널 주소: https://pf.kakao.com 만 연다(서버가 거른 것을 화면에서도 한 번 더)', () {
    expect(SupportPage.channelUrl('https://pf.kakao.com/_nNwwX/chat'), 'https://pf.kakao.com/_nNwwX/chat');
    expect(SupportPage.channelUrl(' https://pf.kakao.com/_nNwwX/chat '), 'https://pf.kakao.com/_nNwwX/chat');
    for (final bad in [
      null,
      '',
      'javascript:alert(1)',
      'http://pf.kakao.com/_nNwwX/chat',
      'https://evil.example/_nNwwX/chat',
      'https://pf.kakao.com.evil.example/_nNwwX/chat',
      'https://pf.kakao.com@evil.example/_nNwwX/chat',
      'https://evil.example@pf.kakao.com/_nNwwX/chat',
      'https://pf.kakao.com:8443/_nNwwX/chat',
      'https://pf.kakao.com/_nNwwX/chat?next=https://evil.example',
      'https://pf.kakao.com/_nNwwX/chat#x',
    ]) {
      expect(SupportPage.channelUrl(bad), isNull, reason: '$bad');
    }
  });

  testWidgets('카카오톡 채널 주소가 비어 있으면 단추를 숨기고 이메일로 안내한다', (tester) async {
    for (final kakao in [null, '', '  ', 'javascript:alert(1)', 'https://evil.example/x']) {
      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: SupportPage(key: ValueKey(kakao), load: () async => _support(kakao: kakao), openUrl: (_) => fail('열면 안 돼요')),
      ));
      await tester.pumpAndSettle();
      expect(find.text('카카오톡 채널로 문의하기'), findsNothing);
      expect(_has('이메일로 물어보세요'), findsOneWidget);
      expect(_has('help@example.com'), findsOneWidget);
      // 내 계정 메뉴에서 열면 대화 상대 코드는 붙이지 않는다
      expect(_has('대화 상대 '), findsNothing);
      expect(_has('문의 코드 K7M3-Q9XA · 앱'), findsOneWidget);
    }
  });

  testWidgets('동의 화면에서 열면(바깥 틀 밖) 넓은 화면에서도 뒤로 가기가 있다', (tester) async {
    tester.view.physicalSize = const Size(1280, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    for (final beforeConsent in [false, true]) {
      final router = GoRouter(
        initialLocation: '/',
        routes: [
          GoRoute(path: '/', builder: (context, state) => const Scaffold(body: Text('동의 화면'))),
          GoRoute(
            path: '/s',
            builder: (context, state) => SupportPage(beforeConsent: beforeConsent, load: () async => _support()),
          ),
        ],
      );
      await tester.pumpWidget(MaterialApp.router(key: ValueKey(beforeConsent), theme: buildTheme(), routerConfig: router));
      router.push('/s');
      await tester.pumpAndSettle();
      // 바깥 틀 안(내 계정 메뉴)에서는 넓은 화면에 왼쪽 목록이 있어 뒤로 가기를 두지 않는다
      expect(find.byType(BackButton), beforeConsent ? findsOneWidget : findsNothing, reason: '$beforeConsent');
    }
  });

  testWidgets('불러오지 못하면 까닭과 다시 불러오기', (tester) async {
    var calls = 0;
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: SupportPage(
        load: () async {
          calls++;
          if (calls == 1) throw const ApiException('잠시 뒤 다시 해 주세요.');
          return _support();
        },
      ),
    ));
    await tester.pumpAndSettle();
    expect(_has('잠시 뒤 다시 해 주세요.'), findsOneWidget);
    await tester.tap(find.text('다시 불러오기'));
    await tester.pumpAndSettle();
    expect(find.text('K7M3-Q9XA'), findsOneWidget);
  });

  testWidgets('관리자: 문의 줄을 붙여 넣으면 회원과 짚은 대화 상대가 보인다', (tester) async {
    final asked = <String>[];
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: Scaffold(
        body: SingleChildScrollView(
          child: SupportLookupCard(lookup: (q) async {
            asked.add(q);
            if (q == '안녕하세요') throw const ApiException("문의 코드를 찾지 못했어요. 'K7M3-Q9XA' 같은 8자예요.");
            return {
              'code': 'K7M3-Q9XA',
              'personaCode': '3F2A9C',
              'users': [
                {
                  'id': 'u-1',
                  'name': '김철수',
                  'status': 'ACTIVE',
                  'createdAt': '2026-10-20T01:00:00Z',
                  'providers': ['kakao'],
                  'emailMasked': 'k***@example.com',
                  'personas': [
                    {'id': _personaId, 'code': '3F2A9C', 'title': '할머니', 'role': 'owner', 'step': 'READY', 'matched': true},
                    {'id': 'p-2', 'code': 'AB12CD', 'title': '엄마', 'role': 'member', 'step': 'READY', 'matched': false},
                  ],
                },
              ],
            };
          }),
        ),
      ),
    ));
    await tester.enterText(find.byType(TextField), '문의 코드 K7M3-Q9XA · 대화 상대 3F2A9C · 앱 로컬');
    await tester.tap(find.text('찾기'));
    await tester.pumpAndSettle();
    expect(asked, ['문의 코드 K7M3-Q9XA · 대화 상대 3F2A9C · 앱 로컬']);
    expect(find.text('김철수'), findsOneWidget);
    expect(find.text('▶ 3F2A9C · 할머니 · 만든 분 · READY'), findsOneWidget);
    expect(find.text('AB12CD · 엄마 · 함께 쓰는 가족 · READY'), findsOneWidget);

    await tester.enterText(find.byType(TextField), '안녕하세요');
    await tester.tap(find.text('찾기'));
    await tester.pumpAndSettle();
    expect(_has('문의 코드를 찾지 못했어요.'), findsOneWidget);
    expect(find.text('김철수'), findsNothing);
  });

  testWidgets('내 계정 메뉴에 고객센터가 있다', (tester) async {
    session.user = {'id': 'u1', 'name': '김철수', 'consentsNeeded': <String>[]};
    final router = GoRouter(
      initialLocation: '/',
      routes: [
        GoRoute(path: '/', builder: (context, state) => const Scaffold(body: Center(child: AccountMenuButton()))),
        GoRoute(path: '/support', builder: (context, state) => const Scaffold(body: Text('고객센터 화면'))),
      ],
    );
    await tester.pumpWidget(MaterialApp.router(theme: buildTheme(), routerConfig: router));
    await tester.tap(find.byType(AccountMenuButton));
    await tester.pumpAndSettle();
    await tester.tap(find.text('고객센터'));
    await tester.pumpAndSettle();
    expect(find.text('고객센터 화면'), findsOneWidget);
  });

  testWidgets('전화 줄(서버 사업자 정보의 전화)과 개인정보 처리방침 링크가 있다', (tester) async {
    tester.view.physicalSize = const Size(1080, 3000);
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);
    final copied = <String>[];
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: SupportPage(
        load: () async => _support(),
        loadBusiness: () async => const {'phone': '010-0000-0000'},
        openUrl: (_) {},
        copy: (text) async => copied.add(text),
      ),
    ));
    await tester.pumpAndSettle();
    expect(_has('전화 010-0000-0000'), findsOneWidget);
    await tester.ensureVisible(find.text('번호 복사'));
    await tester.tap(find.text('번호 복사'));
    await tester.pumpAndSettle();
    expect(copied, ['010-0000-0000']);
    expect(find.text('개인정보 처리방침'), findsOneWidget);

    // 전화 값이 없으면 줄을 숨긴다
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: SupportPage(key: const ValueKey('none'), load: () async => _support(), loadBusiness: () async => const {}),
    ));
    await tester.pumpAndSettle();
    expect(_has('전화 '), findsNothing);
  });
}
