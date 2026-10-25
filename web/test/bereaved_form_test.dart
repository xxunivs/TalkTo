import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/personas/bereaved_page.dart';
import 'package:talkto_fe/features/personas/widgets/bereaved_form.dart';

import 'legal_fakes.dart';

// 돌아가신 분 대화 상대의 신청 자격과 유족 명단(/p/:id/bereaved, 2026-10-22 법률 자문). 사망진단서 같은
// 서류는 받지 않는다. '그 밖'이면 동의해 준 유족을, 명단은 배우자·자녀(안 계시면 부모)를, 적을 분이 없으면
// '적을 분이 없어요'를 고르고, 유족 확인 문서에 체크해야 남긴다. 시험 자료는 모두 지어낸 것이다.

Map<String, dynamic> _detail({String status = 'deceased', String role = 'owner'}) => {
      'id': 'p1',
      'role': role,
      'step': 'READY',
      'addressTerm': '할머니',
      'relation': '할머니',
      'lifeStatus': status == 'deceased' ? 'DECEASED' : 'LIVING',
      'basics': {'name': '김예시', 'relation': '할머니', 'addressTerm': '할머니', 'status': status, 'myRelation': '손녀'},
    };

const _meta = <String, dynamic>{
  'applicantRelations': {'spouse': '배우자', 'child': '자녀', 'grandchild': '손주', 'parent': '부모', 'other': '그 밖의 친족·지인'},
  'consentingRelations': {'spouse': '배우자', 'child': '자녀', 'grandchild': '손주', 'parent': '부모'},
  'bereavedRelations': {'spouse': '배우자', 'son': '아들', 'daughter': '딸', 'mother': '어머니', 'father': '아버지'},
};

FakeServer _server({Map<String, dynamic>? detail, Map<String, dynamic>? saved, Object? Function()? put}) => FakeServer({
      'GET /personas/p1': () => detail ?? _detail(),
      'GET /personas/p1/bereaved': () => saved,
      'GET /intake/meta': () => _meta,
      'GET /legal/pledge_deceased': () => {
            'title': '돌아가신 분 — 유족 확인',
            'version': '0.5',
            'body': '- 명단의 분들께 알렸고 반대하시는 분이 없어요.\n- 이의를 보내시면 바로 멈추고 7일 안에 지워요.',
          },
      'PUT /personas/p1/bereaved': put ?? () => {'applicantRelation': 'grandchild', 'members': <Object>[], 'noneLeft': true},
      'GET /personas': () => <Object>[],
    });

Widget _app() => MaterialApp.router(
      theme: buildTheme(),
      routerConfig: GoRouter(
        initialLocation: '/p/p1/bereaved',
        routes: [
          GoRoute(
            path: '/p/:id',
            builder: (context, state) => const Scaffold(body: Text('대화 상대 화면')),
            routes: [
              GoRoute(
                path: 'bereaved',
                builder: (context, state) => BereavedPage(personaId: state.pathParameters['id']!),
              ),
            ],
          ),
        ],
      ),
    );

/// 저장하기(앞서 뜬 알림 줄이 단추를 가리지 않게 먼저 걷는다).
Future<void> _save(WidgetTester tester) async {
  tester.state<ScaffoldMessengerState>(find.byType(ScaffoldMessenger)).clearSnackBars();
  await settle(tester);
  await tapIn(tester, find.widgetWithText(FilledButton, '저장하기'));
}

void main() {
  testWidgets('신청 자격 · 그 밖이면 동의해 준 유족 · 명단 · 확인 체크를 차례로 확인하고 남긴다', (tester) async {
    phone(tester, height: 2600);
    final server = _server();
    ApiClient.useAdapterForTests(server);
    await tester.pumpWidget(_app());
    await settle(tester);
    expect(has('사망진단서 같은 서류는 받지 않아요.'), findsOneWidget);
    expect(has('만드시는 분도 고인의 배우자나 자녀라면 명단에 함께 적어 주세요.'), findsOneWidget);
    expect(shown('돌아가신 분 — 유족 확인'), findsOneWidget);
    // 신청 자격 도움말은 아래 '그 밖의 친족·지인'(유족 동의를 받은 분)과 어긋나지 않게
    expect(has('또는 이분들 가운데 한 분의 동의를 받은 분이 신청할 수 있어요.'), findsOneWidget);

    await _save(tester);
    expect(snack('고인과 어떤 관계인지 골라 주세요.'), findsOneWidget);

    // 그 밖의 친족·지인: 동의해 준 유족(배우자·자녀·손주·부모 가운데 한 분)을 꼭 적는다
    await tapIn(tester, shown('그 밖의 친족·지인'));
    expect(shown('동의해 준 유족'), findsOneWidget);
    await _save(tester);
    expect(snack('동의해 준 유족(배우자·자녀·손주·부모 가운데 한 분)의 이름과 관계를 적어 주세요.'), findsOneWidget);
    await tester.enterText(find.widgetWithText(TextField, '이름'), '김하나');
    await tapIn(tester, shown('자녀').last);

    await _save(tester);
    expect(snack("고인의 배우자·자녀(안 계시면 부모)를 적거나 '적을 분이 없어요'를 골라 주세요."), findsOneWidget);

    // 유족 한 분 더하기(이름 · 관계 · 연락처 선택)
    await tapIn(tester, find.text('유족 더하기'));
    await tester.enterText(find.widgetWithText(TextField, '예: 홍철수'), '김하나');
    await tapIn(tester, shown('딸'));
    await tester.enterText(find.byType(TextField).last, 'hana@example.com');
    await tapIn(tester, find.widgetWithText(FilledButton, '더하기'));
    expect(shown('김하나 · 딸'), findsOneWidget);
    expect(shown('hana@example.com'), findsOneWidget);

    await _save(tester);
    expect(snack('확인할 내용에 체크해 주세요.'), findsOneWidget);
    expect(server.count('PUT /personas/p1/bereaved'), 0);

    await tapIn(tester, has('위 내용을 모두 확인했어요.'));
    await _save(tester);
    expect(server.count('PUT /personas/p1/bereaved'), 1);
    expect(server.json('PUT /personas/p1/bereaved'), {
      'applicantRelation': 'other',
      'consentingName': '김하나',
      'consentingRelation': 'child',
      'members': [
        {'name': '김하나', 'relation': 'daughter', 'contact': 'hana@example.com'},
      ],
      'noneLeft': false,
      'pledge': true,
    });
    expect(find.text('대화 상대 화면'), findsOneWidget);
  });

  testWidgets("'적을 분이 없어요'면 명단 없이 남기고, 앞서 적은 것은 채워 보인다", (tester) async {
    phone(tester, height: 2600);
    final server = _server(saved: {
      'applicantRelation': 'grandchild',
      'consentingName': null,
      'consentingRelation': null,
      'members': [
        {'name': '김둘', 'relation': 'son', 'contact': null},
      ],
      'noneLeft': false,
      'declaredAt': '2026-10-22T01:00:00Z',
    });
    ApiClient.useAdapterForTests(server);
    await tester.pumpWidget(_app());
    await settle(tester);
    expect(shown('김둘 · 아들'), findsOneWidget);

    await tapIn(tester, has('적을 분이 없어요(모두 돌아가셨어요)'));
    // 명단과 더하기 단추가 사라진다
    expect(shown('김둘 · 아들'), findsNothing);
    expect(find.text('유족 더하기'), findsNothing);
    await tapIn(tester, has('위 내용을 모두 확인했어요.'));
    await _save(tester);
    expect(server.json('PUT /personas/p1/bereaved'), {
      'applicantRelation': 'grandchild',
      'members': <Object>[],
      'noneLeft': true,
      'pledge': true,
    });
  });

  testWidgets('서버가 막으면(멈춘 대화 상대 등) 그 글을 그대로 보이고 머문다', (tester) async {
    phone(tester, height: 2600);
    final server = _server(
      put: () => const Fail(409, {'detail': '이 대화 상대는 지금 멈췄어요. 10월 29일에 지워요. 자세한 내용은 알림을 확인해 주세요.', 'code': 'stopped'}),
    );
    ApiClient.useAdapterForTests(server);
    await tester.pumpWidget(_app());
    await settle(tester);
    await tapIn(tester, shown('손주'));
    await tapIn(tester, has('적을 분이 없어요(모두 돌아가셨어요)'));
    await tapIn(tester, has('위 내용을 모두 확인했어요.'));
    await _save(tester);
    expect(has('이 대화 상대는 지금 멈췄어요.'), findsWidgets);
    expect(find.text('대화 상대 화면'), findsNothing);
  });

  testWidgets('돌아가신 분이 아니면 적지 않는다', (tester) async {
    phone(tester, height: 2600);
    ApiClient.useAdapterForTests(_server(detail: _detail(status: 'alive')));
    await tester.pumpWidget(_app());
    await settle(tester);
    expect(shown('돌아가신 분의 대화 상대에서만 적어요.'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, '저장하기'), findsNothing);
  });

  test('보내기 전 검사 글은 서버와 같다', () {
    final draft = BereavedDraft();
    expect(draft.check(), '고인과 어떤 관계인지 골라 주세요.');
    draft.applicantRelation = 'spouse';
    expect(draft.check(), "고인의 배우자·자녀(안 계시면 부모)를 적거나 '적을 분이 없어요'를 골라 주세요.");
    draft.members = [
      for (var i = 0; i < bereavedMaxMembers + 1; i++) {'name': '가$i', 'relation': 'son'},
    ];
    expect(draft.check(), '12분까지 적을 수 있어요.');
    draft.noneLeft = true;
    // 만들기 전 확인에서는 문서 체크를 옆 카드가 본다
    expect(draft.check(needPledge: false), isNull);
    expect(draft.check(), '확인할 내용에 체크해 주세요.');
    draft.pledge = true;
    expect(draft.toJson(), {'applicantRelation': 'spouse', 'members': <Object>[], 'noneLeft': true, 'pledge': true});
    draft.dispose();
  });
}
