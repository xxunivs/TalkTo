import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/personas/imported.dart';
import 'package:talkto_fe/features/personas/owner_actions.dart';
import 'package:talkto_fe/features/personas/persona_flow_page.dart';

import 'legal_fakes.dart';

// 만든 분이 할 일(2026-10-22 법률 자문): 무엇을 막고 무엇을 할지는 서버(gates·actions·subjectConsent·hold)가
// 정하고 화면은 그대로 그린다. 새 칸이 없는 예전 응답이면 모두 됨·할 일 없음. 시험 자료는 모두 지어낸 것이다.

void main() {
  test('새 칸이 없는 예전 응답은 모두 됨·할 일 없음', () {
    const old = {'id': 'p1', 'role': 'owner', 'step': 'READY'};
    final gates = PersonaGates.of(old);
    expect([gates.audio, gates.voice, gates.build, gates.chat, gates.invite], everyElement(isTrue));
    expect(ownerActionsOf(old), isEmpty);
    expect(holdOf(old), isNull);
    expect(subjectConsentOf(old), isNull);
    expect(ownerChipOf(old), isNull);
    expect(blockingActionOf(old), isNull);
    expect(setupActionsOf(Map<String, dynamic>.from(old)), isEmpty);
    // 메뉴의 녹음 더하기·목소리 만들기도 그대로
    final menu = personaActionsOf(old, hasVoice: true);
    expect(menu.recordings, isTrue);
    expect(menu.voice, '목소리 다시 만들기');
  });

  test('막힌 일은 메뉴에서 숨기고, 예전 대화가 어려우신 분도 숨긴다', () {
    final held = {
      'id': 'p1',
      'role': 'owner',
      'step': 'READY',
      'basics': {'status': 'alive'},
      'gates': {'audio': false, 'voice': false, 'build': false, 'chat': true, 'invite': true, 'code': 'subject_consent'},
    };
    final menu = personaActionsOf(held, hasVoice: true);
    expect(menu.recordings, isFalse);
    expect(menu.voice, isNull);
    expect(menu.share, isTrue);

    final frozen = personaActionsOf({
      'id': 'p1',
      'role': 'owner',
      'step': 'READY',
      'basics': {'status': 'unable'},
    }, hasVoice: false);
    expect(frozen.recordings, isFalse);
    expect(frozen.voice, isNull);
  });

  test('만들기 단계 카드: 서버 카드가 없으면 그분 동의 녹음 상태로 하나', () {
    Map<String, dynamic> detail(String status) => {
          'id': 'p1',
          'subjectConsent': {'required': true, 'status': status},
        };
    expect(setupActionsOf(detail('none')).single['route'], '/p/p1/consent');
    expect(setupActionsOf(detail('submitted')).single['title'], '그분 동의 녹음을 확인하고 있어요');
    expect(setupActionsOf(detail('hold')).single['title'], '확인할 것이 있어요');
    expect(setupActionsOf(detail('passed')), isEmpty);
    expect(setupActionsOf({'id': 'p1', 'subjectConsent': {'required': false, 'status': 'none'}}), isEmpty);
    // 서버 카드가 있으면 그것 그대로
    final server = {
      'id': 'p1',
      'subjectConsent': {'required': true, 'status': 'none'},
      'actions': [
        {'kind': 'guardian', 'blocking': false, 'title': '법정대리인 확인을 마쳐 주세요', 'route': '/p/p1/guardian'},
      ],
    };
    expect(setupActionsOf(server).single['kind'], 'guardian');
  });

  test('꼬리표: 멈추면 멈춤, 할 일이 있으면 확인이 필요해요', () {
    expect(ownerChipOf({'hold': {'reason': 'objection'}, 'actions': <Object>[]}), '멈춤');
    expect(
      ownerChipOf({
        'actions': [
          {'kind': 'subject_consent', 'blocking': false},
        ],
      }),
      '확인이 필요해요',
    );
    final stopped = {
      'actions': [
        {'kind': 'unable_frozen', 'blocking': false},
        {'kind': 'stopped', 'blocking': true},
      ],
    };
    expect(blockingActionOf(stopped)?['kind'], 'stopped');
  });

  testWidgets('카드 단추: 갈 곳에 맞는 글, 다시 부탁받았으면 다시 녹음하기, 멈춘 카드에는 지금 지우기', (tester) async {
    phone(tester);
    var deleted = 0;
    Widget card(Map<String, dynamic> action, {String? status}) => MaterialApp(
          theme: buildTheme(),
          home: Scaffold(
            body: OwnerActionCard(action: action, consentStatus: status, onDelete: () => deleted++),
          ),
        );

    await tester.pumpWidget(card({'kind': 'subject_consent', 'title': '그분 동의 녹음을 받아 주세요', 'route': '/p/p1/consent'}));
    expect(find.text('동의 녹음하기'), findsOneWidget);
    expect(find.text('지금 지우기'), findsNothing);
    await tester.pumpWidget(card({'kind': 'subject_consent', 'route': '/p/p1/consent'}, status: 'rerequest'));
    expect(find.text('다시 녹음하기'), findsOneWidget);
    // 확인 중·보류 카드도 같은 화면으로 가지만 녹음을 시작할 수 없으니 '자세히 보기'
    for (final kind in ['subject_consent_review', 'subject_consent_hold']) {
      await tester.pumpWidget(card({'kind': kind, 'route': '/p/p1/consent'}, status: 'submitted'));
      expect(find.text('자세히 보기'), findsOneWidget, reason: kind);
      expect(find.text('동의 녹음하기'), findsNothing, reason: kind);
    }
    await tester.pumpWidget(card({'kind': 'bereaved', 'blocking': false, 'route': '/p/p1/bereaved'}));
    expect(find.text('신청 자격과 유족 명단 적기'), findsOneWidget);
    await tester.pumpWidget(card({'kind': 'guardian', 'route': '/p/p1/guardian'}));
    expect(find.text('법정대리인 확인하기'), findsOneWidget);
    // 대화가 어려우신 분 안내 카드는 갈 곳이 없어 단추가 없다
    await tester.pumpWidget(card({'kind': 'unable_frozen', 'title': '새 녹음 처리와 목소리 다시 만들기를 멈췄어요'}));
    expect(find.byType(FilledButton), findsNothing);
    expect(find.byType(OutlinedButton), findsNothing);

    await tester.pumpWidget(card({
      'kind': 'stopped',
      'blocking': true,
      'title': '이 대화 상대를 멈췄어요',
      'body': '고인의 가족께서 이의를 보내오셔서 대화를 멈췄어요. 10월 29일에 지워요.',
    }));
    expect(has('고인의 가족께서 이의를 보내오셔서 대화를 멈췄어요.'), findsOneWidget);
    await tester.tap(find.text('지금 지우기'));
    await tester.pump();
    expect(deleted, 1);
  });

  testWidgets('만들다 멈춘 돌아가신 분: 다시 시도가 409 bereaved 면 유족 명단 칸이 있는 질문 단계로', (tester) async {
    final server = FakeServer({
      'GET /personas/p1': () => {
            'id': 'p1',
            'role': 'owner',
            'step': 'BUILDING',
            'addressTerm': '할아버지',
            'relation': '할아버지',
            'basics': {'status': 'deceased', 'relation': '할아버지', 'myRelation': '손자'},
            'answers': <String, dynamic>{},
            'jobs': {
              'BUILD': {'status': 'FAILED', 'error': '만들지 못했어요.'},
            },
            'actions': <Object>[],
          },
      'POST /personas/p1/build': () => const Fail(409, {'detail': '먼저 신청 자격과 유족 명단을 적어 주세요.', 'code': 'bereaved'}),
      'POST /personas/p1/back-to-questions': () => null,
      'GET /personas': () => <Object>[],
    });
    ApiClient.useAdapterForTests(server);
    phone(tester, height: 1800);
    await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const PersonaFlowPage(personaId: 'p1')));
    await settle(tester);
    await tapIn(tester, find.text('다시 시도'));
    await settle(tester);

    expect(server.count('POST /personas/p1/build'), 1);
    expect(server.count('POST /personas/p1/back-to-questions'), 1);
    expect(has('먼저 신청 자격과 유족 명단을 적어 주세요.'), findsNothing);
  });
}
