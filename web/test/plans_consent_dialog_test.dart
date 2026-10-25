import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/billing/plans_page.dart';

import 'legal_fakes.dart';

// 결제 직전 확인(2026-10-22 법률 자문 · 약관 5번): 결제 취소(청약철회) 안내를 한 번 더 보이고, 체크 글은
// 서버(GET /legal 의 payment) 것 그대로 쓴다(결제 기록의 증빙 글과 같게). 읽지 못하면 결제하지 않는다.
// 실제 결제는 열지 않는다(화면만 본다). 시험 자료는 모두 지어낸 것이다.

const _notice = '결제하시면 바로 유료 이용이 시작돼요. (서버 안내 글)';
const _recurring = '매달 같은 날 자동으로 결제되는 것에 동의해요. (서버 체크 글)';
const _withdrawal = '결제 후 대화를 이용하면 단순 변심에 의한 청약철회가 제한된다는 점을 확인했어요. (서버 체크 글)';

Map<String, dynamic> _payment() => {
      'notice': _notice,
      'items': [
        {'type': 'recurring', 'label': _recurring},
        {'type': 'withdrawal_limit', 'label': _withdrawal},
      ],
    };

Map<String, dynamic> _me({String status = 'TRIAL', String? periodEnd}) => {
      'paymentsOpen': true,
      'plans': [
        {'id': 'memories', 'name': '추억', 'price': 9900, 'chargePrice': 4900, 'conversations': 50},
        {'id': 'family', 'name': '가족', 'price': 29900, 'chargePrice': 14900, 'conversations': 300},
      ],
      'personas': [
        {
          'personaId': 'a',
          'title': '할머니',
          'step': 'READY',
          'status': status,
          'active': status != 'NO_TRIAL',
          'plan': 'trial',
          'trialWillStart': status == 'NO_TRIAL' ? false : null,
          'periodEnd': periodEnd,
          'deceased': false,
        },
      ],
      'trialPersonasPerAccount': 1,
    };

Future<void> _show(WidgetTester tester, Map<String, dynamic> me, Future<Map<String, dynamic>> Function() loadPayment) async {
  tester.view.physicalSize = const Size(1280, 2400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  // 바닥 사업자 정보(GET /legal)는 비어 있어도 된다
  ApiClient.useAdapterForTests(FakeServer({'GET /legal': () => <String, dynamic>{}}));
  // 같은 시험에서 다시 열면 화면을 새로 만든다(앞 화면이 읽은 것을 쓰지 않게)
  await tester.pumpWidget(const SizedBox());
  await tester.pumpWidget(MaterialApp(
    theme: buildTheme(),
    home: PlansPage(
      personaId: 'a',
      loadMe: () async => me,
      loadPayments: () async => const <Map<String, dynamic>>[],
      loadPayment: loadPayment,
    ),
  ));
  await tester.pumpAndSettle();
}

Future<void> _openDialog(WidgetTester tester) async {
  final start = find.widgetWithText(OutlinedButton, '이 요금제로 시작하기').first;
  await tester.ensureVisible(start);
  await tester.pumpAndSettle();
  await tester.tap(start);
  await tester.pumpAndSettle();
}

FilledButton _payButton(WidgetTester tester) => tester.widget<FilledButton>(find.widgetWithText(FilledButton, '결제하기'));

void main() {
  testWidgets('결제 직전에 서버 안내를 다시 보이고, 서버 체크 글 두 개를 다 체크해야 결제할 수 있다', (tester) async {
    await _show(tester, _me(), () async => _payment());
    await _openDialog(tester);

    expect(has(_notice), findsOneWidget);
    expect(has(_recurring), findsOneWidget);
    expect(has(_withdrawal), findsOneWidget);
    // 예전 '맞춤 콘텐츠' 까닭은 쓰지 않는다
    expect(has('맞춤 콘텐츠'), findsNothing);
    expect(_payButton(tester).onPressed, isNull);

    await tester.tap(has(_recurring));
    await tester.pumpAndSettle();
    expect(_payButton(tester).onPressed, isNull);
    await tester.tap(has(_withdrawal));
    await tester.pumpAndSettle();
    expect(_payButton(tester).onPressed, isNotNull);
    await tester.tap(find.text('취소'));
    await tester.pumpAndSettle();
  });

  testWidgets('안내를 불러오지 못하면 결제 단추를 막고, 다시 불러올 수 있다', (tester) async {
    var calls = 0;
    await _show(tester, _me(), () async {
      calls++;
      if (calls == 1) throw const ApiException('연결이 끊겼어요.');
      return _payment();
    });
    await _openDialog(tester);
    expect(has('결제 전 안내를 불러오지 못했어요. 잠시 뒤 다시 해 주세요.'), findsOneWidget);
    expect(find.byType(Checkbox), findsNothing);
    expect(_payButton(tester).onPressed, isNull);

    await tester.tap(find.text('다시 불러오기'));
    await tester.pumpAndSettle();
    expect(calls, 2);
    expect(has(_notice), findsOneWidget);
    expect(find.byType(Checkbox), findsNWidgets(2));
    await tester.tap(find.text('취소'));
    await tester.pumpAndSettle();
  });

  testWidgets('체크 글 하나라도 비어 오면 결제하지 않는다', (tester) async {
    await _show(tester, _me(), () async => {
          'notice': _notice,
          'items': [
            {'type': 'recurring', 'label': _recurring},
          ],
        });
    await _openDialog(tester);
    expect(has('결제 전 안내를 불러오지 못했어요.'), findsOneWidget);
    expect(_payButton(tester).onPressed, isNull);
    await tester.tap(find.text('취소'));
    await tester.pumpAndSettle();
  });

  testWidgets('체험을 쓰지 못한 대화 상대에는 첫 결제 7일 안 전액 환불을 알리고, 이용 기간 끝은 다음 결제일 전날이다', (tester) async {
    await _show(tester, _me(status: 'NO_TRIAL'), () async => _payment());
    expect(has('무료 체험을 쓰지 않은 대화 상대는 처음 결제한 날부터 7일 안에 말씀하시면 대화하셨더라도 전액 돌려드려요.'), findsWidgets);
    expect(has('결제 취소(청약철회)와 환불은 이용약관과 환불 정책을 따라요.'), findsOneWidget);

    await _show(tester, _me(status: 'CANCELING', periodEnd: '2026-11-22T03:00:00Z'), () async => _payment());
    // 11월 22일이 다음 결제일이면 이용 기간은 11월 21일까지
    expect(has('11월 21일까지 쓸 수 있고'), findsOneWidget);
  });
}
