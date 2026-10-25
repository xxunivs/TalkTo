import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/personas/guardian_page.dart';
import 'package:talkto_fe/features/personas/widgets/basics_form.dart' show under14ClosedText;

import 'legal_fakes.dart';

// 법정대리인 확인(/p/:id/guardian, 2026-10-22 법률 자문): 만 14세 미만은 법정대리인 동의 두 가지와
// 휴대전화 확인 문자, 만 14~18세는 법정대리인 확인만. 체크 글은 서버 것 그대로, 확인 문자는 TalkTo 가
// 손으로 보낼 수도 있다({queued: true}). 시험 자료는 모두 지어낸 것이다.

const _labels = {
  'attest': '저는 이 아이의 법정대리인(부모)이에요. (서버 글)',
  'consent': '만 14세 미만인 아이의 정보를 모으고 쓰는 데 법정대리인으로서 동의해요. (서버 글)',
  'overseas': '아이의 정보를 미국의 회사로 보내는 데 법정대리인으로서 동의해요. (서버 글)',
};

Map<String, dynamic> _detail() => {
      'id': 'p1',
      'role': 'owner',
      'step': 'UPLOAD',
      'addressTerm': '지우',
      'relation': '딸',
      'basics': {'status': 'alive', 'relation': '딸', 'myRelation': '어머니', 'minor': true, 'birthYm': '2016-05'},
    };

Map<String, dynamic> _state(String mode, List<String> missing, {bool open = true}) => {
      'needed': missing.isNotEmpty,
      'mode': mode,
      'guardianOk': missing.isEmpty,
      'missing': missing,
      'notGuardian': false,
      'phoneMasked': null,
      'smsVerifiedAt': null,
      'under14Open': open,
      'labels': _labels,
    };

Future<void> _open(WidgetTester tester, FakeServer server) async {
  phone(tester, height: 2200);
  ApiClient.useAdapterForTests(server);
  await tester.pumpWidget(const SizedBox());
  await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const GuardianPage(personaId: 'p1')));
  await settle(tester);
}

FilledButton _button(WidgetTester tester, String text) => tester.widget<FilledButton>(find.widgetWithText(FilledButton, text));

void main() {
  testWidgets('만 14세 미만: 동의 두 가지 → 확인 문자(TalkTo 가 보냄) → 확인 번호', (tester) async {
    var missing = ['consent', 'overseas', 'sms'];
    final server = FakeServer({
      'GET /personas/p1': _detail,
      'GET /personas/p1/guardian': () => _state('minor_child', missing),
      'GET /legal/guardian_consent': () => {'title': '만 19세 미만 대화 상대 — 법정대리인 확인과 동의', 'body': '법정대리인 확인 본문'},
      'POST /personas/p1/guardian/consent': () {
        missing = ['sms'];
        return _state('minor_child', missing);
      },
      'POST /personas/p1/guardian/sms': () => {'queued': true, 'expiresAt': '2099-01-01T00:00:00Z', 'phoneMasked': '010-****-5678'},
      'POST /personas/p1/guardian/sms/verify': () {
        missing = [];
        return _state('minor_child', missing);
      },
      'GET /personas': () => <Object>[],
    });
    await _open(tester, server);
    expect(has('만 14세 미만 아이의 대화 상대는 법정대리인 동의와 휴대전화 확인 문자가 필요해요.'), findsOneWidget);
    expect(has('법정대리인 확인 본문'), findsOneWidget);
    expect(has(_labels['consent']!), findsOneWidget);
    expect(has(_labels['overseas']!), findsOneWidget);
    expect(_button(tester, '동의하기').onPressed, isNull);

    await tapIn(tester, has(_labels['consent']!));
    expect(_button(tester, '동의하기').onPressed, isNull);
    await tapIn(tester, has(_labels['overseas']!));
    await tapIn(tester, find.widgetWithText(FilledButton, '동의하기'));
    expect(server.json('POST /personas/p1/guardian/consent'), {'consent': true, 'overseas': true});

    // 확인 문자: 숫자만, TalkTo 가 보내 드리면 하루 안에 온다
    expect(shown('법정대리인 휴대전화'), findsOneWidget);
    await tester.enterText(find.byType(TextField).first, '010-1234-5678');
    await settle(tester);
    expect(tester.widget<TextField>(find.byType(TextField).first).controller!.text, '01012345678');
    await tapIn(tester, find.text('확인 문자 받기'));
    expect(server.json('POST /personas/p1/guardian/sms'), {'phone': '01012345678'});
    expect(has('TalkTo가 확인 문자를 보내 드릴게요(보통 하루 안). 문자가 오면 확인 번호를 넣어 주세요.'), findsOneWidget);

    await tester.enterText(find.byType(TextField).last, '123456');
    await settle(tester);
    await tapIn(tester, find.widgetWithText(FilledButton, '확인'));
    expect(server.json('POST /personas/p1/guardian/sms/verify'), {'code': '123456'});
    expect(shown('법정대리인 확인을 마쳤어요'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, '동의 녹음하기'), findsOneWidget);
  });

  testWidgets('만 14~18세: 법정대리인 확인만(빠졌으면 함께 보낸다)', (tester) async {
    final server = FakeServer({
      'GET /personas/p1': _detail,
      'GET /personas/p1/guardian': () => _state('minor_teen', ['attest']),
      'GET /legal/guardian_consent': () => {'title': '법정대리인 확인과 동의', 'body': '본문'},
      'POST /personas/p1/guardian/consent': () => _state('minor_teen', []),
      'GET /personas': () => <Object>[],
    });
    await _open(tester, server);
    expect(has('아이의 대화 상대는 만드는 분이 법정대리인(부모)인지 먼저 확인해야 해요.'), findsOneWidget);
    expect(has(_labels['consent']!), findsNothing);
    expect(shown('법정대리인 휴대전화'), findsNothing);
    await tapIn(tester, has(_labels['attest']!));
    await tapIn(tester, find.widgetWithText(FilledButton, '동의하기'));
    expect(server.json('POST /personas/p1/guardian/consent'), {'consent': true, 'overseas': true, 'attest': true});
  });

  testWidgets('다시 열어도(알림에서) TalkTo가 보낼 문자를 기다리는 중이면 그 안내와 확인 번호 칸', (tester) async {
    await _open(
      tester,
      FakeServer({
        'GET /personas/p1': _detail,
        'GET /personas/p1/guardian': () => {
              ..._state('minor_child', ['sms']),
              'phoneMasked': '010-****-5678',
              'smsQueued': true,
              'codeExpiresAt': '2099-01-01T00:00:00Z',
            },
        'GET /legal/guardian_consent': () => {'title': '법정대리인 확인과 동의', 'body': '본문'},
      }),
    );
    expect(has('TalkTo가 확인 문자를 보내 드릴게요(보통 하루 안). 문자가 오면 확인 번호를 넣어 주세요.'), findsOneWidget);
    expect(shown('확인 번호 6자리'), findsOneWidget);
    expect(find.text('확인 문자 다시 받기'), findsNothing);
  });

  testWidgets('확인 문자를 아직 보낼 수 없으면 그 안내만', (tester) async {
    await _open(
      tester,
      FakeServer({
        'GET /personas/p1': _detail,
        'GET /personas/p1/guardian': () => _state('minor_child', ['sms'], open: false),
        'GET /legal/guardian_consent': () => {'title': '법정대리인 확인과 동의', 'body': '본문'},
      }),
    );
    expect(has(under14ClosedText), findsOneWidget);
    expect(find.text('확인 문자 받기'), findsNothing);
  });
}
