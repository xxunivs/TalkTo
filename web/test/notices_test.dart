import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/notices/notice_api.dart';
import 'package:talkto_fe/features/notices/notices_page.dart';

import 'legal_fakes.dart';

// 앱 안 알림(/notices): 푸시 없이 앱을 열었을 때 여기서 본다. 글은 서버(TalkTo)가 정한 그대로 보이고,
// 누르면 읽음으로 하고 앱 안 주소로만 간다. 시험 자료는 모두 지어낸 것이다.

List<Map<String, dynamic>> _rows() => [
      {
        'id': 'n1',
        'kind': 'subject_consent_rerequest',
        'title': '그분 동의 녹음을 한 번 더 부탁드려요',
        'body': '오늘 날짜와 성함을 그분이 직접 말씀하시는 부분이 필요해요.',
        'action': {'label': '다시 녹음하기', 'route': '/p/p1/consent'},
        'personaId': 'p1',
        'createdAt': '2026-10-22T01:00:00Z',
        'readAt': null,
      },
      {
        'id': 'n2',
        'kind': 'subject_consent_hold',
        'title': '그분 동의 녹음을 확인하고 있어요',
        'body': "'할머니' 대화 상대의 그분 동의 녹음에서 확인할 것이 있어 TalkTo가 따로 연락드릴게요.",
        'action': null,
        'personaId': 'p1',
        'createdAt': '2026-10-21T01:00:00Z',
        'readAt': '2026-10-21T02:00:00Z',
      },
      {
        'id': 'n3',
        'kind': 'stopped',
        'title': "'할머니' 대화 상대를 멈췄어요",
        'body': '자세한 내용은 대화 상대 화면에서 볼 수 있어요.',
        // 앱 밖 주소는 따라가지 않는다
        'action': {'label': '자세히 보기', 'route': '//example.com/p1'},
        'personaId': 'p1',
        'createdAt': '2026-10-20T01:00:00Z',
        'readAt': null,
      },
    ];

FakeServer _server({int unread = 2}) => FakeServer(
      {
        'POST /notices/n1/read': () => {'ok': true},
        'POST /notices/n3/read': () => {'ok': true},
        'POST /notices/read-all': () => {'ok': true},
      },
      raw: {
        'GET /notices': () => {
              'success': true,
              'data': _rows(),
              'meta': {'unread': unread, 'limit': 50},
            },
      },
    );

Widget _app() {
  final router = GoRouter(
    initialLocation: '/notices',
    routes: [
      GoRoute(
        path: '/notices',
        builder: (context, state) => const Scaffold(body: NoticesPage()),
      ),
      GoRoute(path: '/p/:id/consent', builder: (context, state) => const Text('동의 녹음 화면')),
    ],
  );
  return MaterialApp.router(theme: buildTheme(), routerConfig: router);
}

void main() {
  testWidgets('서버 글 그대로 보이고, 누르면 읽음으로 하고 앱 안 주소로 간다', (tester) async {
    phone(tester);
    final server = _server();
    ApiClient.useAdapterForTests(server);
    await tester.pumpWidget(_app());
    await settle(tester);

    expect(shown('그분 동의 녹음을 한 번 더 부탁드려요'), findsOneWidget);
    expect(has('오늘 날짜와 성함을 그분이 직접 말씀하시는 부분이 필요해요.'), findsOneWidget);
    expect(shown('다시 녹음하기'), findsOneWidget);
    expect(noticeStore.unread, 2);
    // 안 읽은 것만 묻지 않고 모두 받는다(최신 50개)
    expect(server.queries['GET /notices']!.last, {'unread': 0});

    await tapIn(tester, shown('그분 동의 녹음을 한 번 더 부탁드려요'));
    await settle(tester);
    expect(server.count('POST /notices/n1/read'), 1);
    expect(find.text('동의 녹음 화면'), findsOneWidget);
    expect(noticeStore.unread, 1);
  });

  testWidgets('앱 밖 주소는 따라가지 않고, 모두 읽음은 한 번에 남긴다', (tester) async {
    phone(tester);
    final server = _server();
    ApiClient.useAdapterForTests(server);
    await tester.pumpWidget(_app());
    await settle(tester);

    await tapIn(tester, shown("'할머니' 대화 상대를 멈췄어요"));
    expect(server.count('POST /notices/n3/read'), 1);
    expect(find.text('동의 녹음 화면'), findsNothing);
    expect(shown("'할머니' 대화 상대를 멈췄어요"), findsOneWidget);

    await tapIn(tester, find.text('모두 읽음'));
    expect(server.count('POST /notices/read-all'), 1);
    expect(noticeStore.unread, 0);
    // 다 읽으면 '모두 읽음' 단추가 사라진다
    expect(find.text('모두 읽음'), findsNothing);
  });

  testWidgets('종은 안 읽은 수만 작게 보이고, 없으면 숫자가 없다', (tester) async {
    phone(tester);
    ApiClient.useAdapterForTests(_server(unread: 3));
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: Scaffold(appBar: AppBar(actions: const [NoticeBell()])),
    ));
    // 시험 시계 안에서는 기다리지 않고 부른 뒤 그린다
    noticeStore.load();
    await settle(tester);
    expect(find.text('3'), findsOneWidget);
    expect(find.byTooltip('알림 3개'), findsOneWidget);

    ApiClient.useAdapterForTests(_server(unread: 0));
    noticeStore.load();
    await settle(tester);
    expect(find.text('3'), findsNothing);
    expect(find.byTooltip('알림'), findsOneWidget);
  });

  testWidgets('알림이 없으면 차분히 알린다', (tester) async {
    phone(tester);
    ApiClient.useAdapterForTests(FakeServer({}, raw: {
      'GET /notices': () => {
            'success': true,
            'data': <Object>[],
            'meta': {'unread': 0},
          },
    }));
    await tester.pumpWidget(_app());
    await settle(tester);
    expect(shown('아직 알림이 없어요.'), findsOneWidget);
  });
}
