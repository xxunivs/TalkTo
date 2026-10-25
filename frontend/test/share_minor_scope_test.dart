import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/share/family_page.dart';
import 'package:talkto_fe/features/share/member_profile_sheet.dart';

import 'legal_fakes.dart';

// 아이(만 19세 미만)의 대화 상대는 법정대리인과 직계가족(부모·조부모)만 함께 쓴다(2026-10-22 법률 자문).
// 서버가 minorScope 와 관계 목록을 주면 화면은 그 목록에서만 고르게 하고(직접 적는 칸 없음), 서버가
// share_minor_scope 로 막으면 그 글을 그대로 보인다. 멈춤·법정대리인 확인 전이면 초대 대신 그 까닭.
// 시험 자료는 모두 지어낸 것이다.

const _scoped = ['어머니', '아버지', '할머니', '할아버지', '외할머니', '외할아버지'];
const _scopeError = '아이의 대화 상대는 법정대리인과 직계가족(부모·조부모)만 함께 쓸 수 있어요. 관계를 목록에서 골라 주세요.';

Map<String, dynamic> _owner({Map<String, dynamic>? gates}) => {
      'id': 'p1',
      'role': 'owner',
      'addressTerm': '지우',
      'relation': '딸',
      'step': 'READY',
      'origin': null,
      'members': 1,
      'basics': {'myName': '하늘', 'relation': '딸', 'addressTerm': '지우', 'status': 'alive', 'minor': true},
      'jobs': <String, dynamic>{},
      if (gates != null) 'gates': gates,
    };

Map<String, dynamic> _list({bool allowed = true, String? reason}) => {
      'allowed': allowed,
      'reason': reason,
      'prelaunch': true,
      'subjectKind': 'alive',
      'need': <String>[],
      'consentItems': <Object>[],
      'cap': 5,
      'used': 1,
      'minorScope': true,
      'relations': _scoped,
      'familyRows': <Object>[],
      'addressSuggestions': <String, dynamic>{},
      'voiceShareable': true,
      'items': const [
        {
          'id': 's1',
          'status': 'joined',
          'label': '민준',
          'createdAt': '2026-10-16T03:00:00Z',
          'joinedAt': '2026-10-17T03:00:00Z',
          'accountName': '김민준',
          'profile': {'myName': '민준', 'myRelation': '삼촌', 'addressTerm': '지우'},
          'editedBy': 'member',
        },
      ],
    };

Future<void> _open(WidgetTester tester, FakeServer server) async {
  phone(tester, height: 2200);
  ApiClient.useAdapterForTests(server);
  await tester.pumpWidget(const SizedBox());
  await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const FamilyPage(personaId: 'p1')));
  await settle(tester);
}

void main() {
  testWidgets('아이의 대화 상대: 범위를 알리고, 가족 정보 고치기의 관계는 서버 목록에서만 고른다', (tester) async {
    var reject = true;
    final server = FakeServer({
      'GET /personas/p1': () => _owner(),
      'GET /intake/meta': () => {
            'myRelations': ['아들', '딸', '삼촌', '이모'],
          },
      'GET /personas/p1/shares': () => _list(),
      'PATCH /personas/p1/shares/s1': () => reject
          ? const Fail(400, {'detail': _scopeError, 'code': 'share_minor_scope', 'field': 'myRelation'})
          : const {'id': 's1', 'status': 'joined', 'label': '민준'},
    });
    await _open(tester, server);
    expect(has(minorScopeText), findsWidgets);

    await tapIn(tester, find.text('정보 고치기'));
    // 서버가 준 직계가족 목록만 보이고(삼촌·이모 같은 /intake/meta 의 관계는 없다) 직접 적는 칸이 없다
    expect(shown('할아버지'), findsOneWidget);
    expect(shown('외할머니'), findsOneWidget);
    expect(shown('이모'), findsNothing);
    expect(find.widgetWithText(TextField, '목록에 없으면 직접 적어 주세요 (예: 큰아들)'), findsNothing);

    // 범위 밖 관계를 그대로 저장하면 서버 글 그대로
    await tapIn(tester, find.text('저장하기'));
    expect(server.json('PATCH /personas/p1/shares/s1')['myRelation'], '삼촌');
    expect(has(_scopeError), findsOneWidget);

    reject = false;
    await tapIn(tester, shown('할아버지'));
    await tapIn(tester, find.text('저장하기'));
    expect(server.json('PATCH /personas/p1/shares/s1')['myRelation'], '할아버지');
    expect(find.text('고쳤어요.'), findsOneWidget);
  });

  testWidgets('법정대리인 확인 전·멈춤이면 초대 대신 서버 글과 갈 곳', (tester) async {
    await _open(
      tester,
      FakeServer({
        'GET /personas/p1': () => _owner(gates: {
              'audio': false,
              'voice': false,
              'build': false,
              'chat': true,
              'invite': false,
              'code': 'guardian',
              'message': '먼저 법정대리인 확인을 마쳐 주세요.',
            }),
        'GET /intake/meta': () => {'myRelations': <String>[]},
        'GET /personas/p1/shares': () => _list(allowed: false, reason: 'guardian'),
      }),
    );
    expect(has('먼저 법정대리인 확인을 마쳐 주세요.'), findsOneWidget);
    expect(find.widgetWithText(TextButton, '확인하기'), findsOneWidget);
    expect(shown('누구를 초대할까요?'), findsNothing);

    await _open(
      tester,
      FakeServer({
        'GET /personas/p1': () => _owner(gates: {
              'audio': false,
              'voice': false,
              'build': false,
              'chat': false,
              'invite': false,
              'code': 'stopped',
              'message': '이 대화 상대는 지금 멈췄어요. 10월 29일에 지워요. 자세한 내용은 알림을 확인해 주세요.',
            }),
        'GET /intake/meta': () => {'myRelations': <String>[]},
        'GET /personas/p1/shares': () => _list(allowed: false, reason: 'stopped'),
      }),
    );
    expect(has('이 대화 상대는 지금 멈췄어요. 10월 29일에 지워요.'), findsOneWidget);
    // 멈춘 대화 상대는 갈 곳 단추가 없다
    expect(find.widgetWithText(TextButton, '확인하기'), findsNothing);
    expect(find.widgetWithText(TextButton, '적기'), findsNothing);
  });

  testWidgets('관계 칸: 아이의 대화 상대면 목록만, 아니면 직접 적을 수도 있다', (tester) async {
    phone(tester);
    Widget form(ProfileDraft draft, {required bool minorScope}) => MaterialApp(
          theme: buildTheme(),
          home: Scaffold(
            body: SingleChildScrollView(
              child: ProfileForm(
                draft: draft,
                voice: ProfileVoice.member,
                title: '지우',
                ownerName: '하늘',
                relations: _scoped,
                minorScope: minorScope,
                keys: const ['myRelation', 'myName', 'addressTerm'],
              ),
            ),
          ),
        );
    final scoped = ProfileDraft(const {});
    await tester.pumpWidget(form(scoped, minorScope: true));
    expect(shown('외할아버지'), findsOneWidget);
    expect(has(minorScopeText), findsOneWidget);
    expect(find.widgetWithText(TextField, '목록에 없으면 직접 적어 주세요 (예: 큰아들)'), findsNothing);
    await tapIn(tester, shown('외할아버지'));
    expect(scoped.value('myRelation'), '외할아버지');
    scoped.dispose();

    final open = ProfileDraft(const {});
    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(form(open, minorScope: false));
    expect(find.widgetWithText(TextField, '목록에 없으면 직접 적어 주세요 (예: 큰아들)'), findsOneWidget);
    expect(has(minorScopeText), findsNothing);
    open.dispose();
  });
}
