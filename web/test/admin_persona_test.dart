import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/admin/guardian_sms_page.dart';
import 'package:talkto_fe/features/admin/legal_admin_api.dart';
import 'package:talkto_fe/features/admin/legal_admin_links.dart';
import 'package:talkto_fe/features/admin/payments_admin_page.dart';
import 'package:talkto_fe/features/admin/persona_admin_page.dart';
import 'package:talkto_fe/features/admin/persona_lists_page.dart';
import 'package:talkto_fe/shared/widgets/ui.dart';

// 관리자 · 대화 상대 관리(유족 문안 복사·이의 접수)·멈춘 목록·예전 대화 상대·결제·환불·확인 문자.
// 시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다). 아무것도 자동으로 보내지 않는다.

const _smsText = '[TalkTo] 안녕하세요, 김가족님. 예시 문안이에요. 알려 주시면 바로 대화를 멈추고 7일 안에 지워요.';

Map<String, dynamic> _payment({String code = 'no_chat_7d', bool refunded = false}) => {
      'id': 'pay1',
      'orderId': 'ORD-0001',
      'approvedAt': '2026-10-21T01:00:00Z',
      'kind': 'first',
      'plan': '가족',
      'amount': 14900,
      'status': 'DONE',
      'personaId': 'p1',
      'personaLabel': "'할머니' 대화 상대",
      'ownerMasked': 'ex***@example.com',
      'firstChatAt': null,
      'firstChatBy': null,
      'trialUsed': false,
      'personaFirstPaidAt': '2026-10-21T01:00:00Z',
      'periodStart': '2026-10-21T01:00:00Z',
      'periodEnd': '2026-11-21T01:00:00Z',
      'refundHint': {
        'code': code,
        'text': code == 'no_chat_7d' ? '결제 후 7일 안이고 대화가 없어요 — 전액 환불 대상' : '일반 환불 사유 없음',
      },
      'refundedAt': refunded ? '2026-10-22T01:00:00Z' : null,
      'refundAmount': refunded ? 14900 : null,
      'refundNote': refunded ? 'no_chat_7d' : null,
    };

Map<String, dynamic> _legal({Map<String, dynamic>? hold, Map<String, dynamic>? prorated}) => {
      'persona': {
        'id': 'p1',
        'title': '할머니',
        'name': '김예시',
        'status': 'deceased',
        'step': 'READY',
        'createdAt': '2026-10-01T01:00:00Z',
        'origin': null,
      },
      'owner': {'masked': 'ex***@example.com', 'name': '가은'},
      'bereaved': {
        'applicantRelation': 'grandchild',
        'consentingName': null,
        'consentingRelation': null,
        'members': [
          {'name': '김가족', 'relation': 'son', 'contact': '010-0000-0000'},
          {'name': '이가족', 'relation': 'daughter', 'contact': null},
        ],
        'noneLeft': false,
        'declaredAt': '2026-10-02T01:00:00Z',
      },
      'minor': null,
      'hold': hold,
      'objections': hold == null
          ? const []
          : [
              {'reason': 'objection', 'fromName': '김가족', 'fromRelation': '아들', 'receivedAt': '2026-10-22T01:00:00Z'},
            ],
      'subjectConsent': null,
      'templates': [
        {'memberIndex': 0, 'name': '김가족', 'contact': '010-0000-0000', 'channel': 'sms', 'text': _smsText},
      ],
      'billing': {
        'trialEndsAt': null,
        'trialUsed': false,
        'payments': [_payment()],
        'proratedRefund': prorated,
      },
    };

class _FakeApi extends LegalAdminApi {
  Map<String, dynamic> legal;
  final objections = <Map<String, dynamic>>[];
  final refunds = <Map<String, dynamic>>[];
  final sent = <String>[];
  Map<String, dynamic> smsMeta;
  List<Map<String, dynamic>> smsRows;
  Object? smsError;

  _FakeApi({
    Map<String, dynamic>? legal,
    this.smsMeta = const {'provider': 'manual'},
    this.smsRows = const [],
    this.smsError,
  }) : legal = legal ?? _legal();

  @override
  Future<Map<String, dynamic>> persona(String personaId) async => legal;

  @override
  Future<Map<String, dynamic>> objection(
    String personaId, {
    required String reason,
    String? fromName,
    String? fromRelation,
    String? note,
  }) async {
    objections.add({'reason': reason, 'fromName': fromName, 'fromRelation': fromRelation, 'note': note});
    // 서버(holds.take_objection)처럼: 멈추지 않았으면 멈추고, 시스템이 건 멈춤이면 까닭을 바꾸고, 이의면 그대로
    final before = legal['hold'] is Map ? '${(legal['hold'] as Map)['reason']}' : null;
    final replaced = before == 'subject_refused' || before == 'minor_consent_missing';
    final kept = before != null && !replaced;
    legal = _legal(hold: {'reason': kept ? before : reason, 'holdAt': '2026-10-23T01:00:00Z', 'deleteAfter': '2026-10-30T01:00:00Z'});
    return {
      'already': before != null,
      'replaced': replaced,
      'reason': kept ? before : reason,
      'holdAt': '2026-10-23T01:00:00Z',
      'deleteAfter': '2026-10-30T01:00:00Z',
      'refund': {'paymentId': 'pay1', 'amount': 14900, 'remainingDays': 28, 'periodDays': 31, 'refund': 13457},
    };
  }

  @override
  Future<Map<String, dynamic>> refund(String paymentId, {required int amount, required String reason, String? note}) async {
    refunds.add({'paymentId': paymentId, 'amount': amount, 'reason': reason});
    return {'ok': true};
  }

  @override
  Future<({List<Map<String, dynamic>> rows, int total, Map<String, dynamic> meta})> payments({
    String? q,
    String? personaId,
    String? userId,
    int page = 1,
    int size = 50,
  }) async =>
      (rows: [_payment(), _payment(code: 'none', refunded: true)], total: 2, meta: <String, dynamic>{});

  @override
  Future<List<Map<String, dynamic>>> holds() async => [
        {
          'personaId': 'p1',
          'title': '할머니',
          'ownerMasked': 'ex***@example.com',
          'reason': 'objection',
          'holdAt': '2026-10-23T01:00:00Z',
          'deleteAfter': '2026-10-30T01:00:00Z',
        },
      ];

  @override
  Future<List<Map<String, dynamic>>> unable() async => [
        {
          'personaId': 'p2',
          'kind': 'unable',
          'title': '아버지',
          'ownerMasked': 'ex***@example.com',
          'step': 'READY',
          'hasVoice': true,
          'voiceProvider': 'fish',
          'createdAt': '2026-09-01T01:00:00Z',
          'readyAt': '2026-09-02T01:00:00Z',
          'lastChatAt': '2026-10-20T01:00:00Z',
          'members': 1,
        },
        {'personaId': 'p3', 'kind': 'minor_not_guardian', 'title': '조카', 'step': 'READY', 'hasVoice': false, 'members': 0},
      ];

  @override
  Future<({List<Map<String, dynamic>> rows, int total, Map<String, dynamic> meta})> consents({
    String status = 'submitted',
    int page = 1,
    int size = 50,
  }) async =>
      (rows: const <Map<String, dynamic>>[], total: 3, meta: <String, dynamic>{'total': 3});

  @override
  Future<({List<Map<String, dynamic>> rows, int total, Map<String, dynamic> meta})> guardianSms() async {
    if (smsError != null) throw smsError!;
    return (rows: smsRows, total: smsRows.length, meta: smsMeta);
  }

  @override
  Future<Map<String, dynamic>> guardianSmsSent(String personaId) async {
    sent.add(personaId);
    smsRows = const [];
    return {'ok': true};
  }
}

String _plain(Widget w) => switch (w) {
      Text() => (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', ''),
      SelectableText() => w.data ?? '',
      _ => '',
    };
Finder _has(String part) => find.byWidgetPredicate((w) => (w is Text || w is SelectableText) && _plain(w).contains(part));
Finder _pill(String label) => find.byWidgetPredicate((w) => w is Pill && w.label == label);
Finder _chip(String label) => find.byWidgetPredicate((w) => w is StatusChip && w.label == label);

Future<void> _pump(WidgetTester tester, Widget page) async {
  tester.view.physicalSize = const Size(1000, 4000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: page));
  await tester.pumpAndSettle();
}

/// 서버 대신 몸통 그대로 답한다('GET /admin/guardian-sms' 처럼 메서드와 경로로 고른다).
class _Server implements HttpClientAdapter {
  final Map<String, Object? Function()> routes;
  final log = <String>[];

  _Server(this.routes);

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    final key = '${options.method} ${options.path}';
    log.add(key);
    final handler = routes[key];
    return ResponseBody.fromString(
      jsonEncode(handler == null ? {'detail': '찾지 못했어요.'} : handler()),
      handler == null ? 404 : 200,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );
  }

  @override
  void close({bool force = false}) {}
}

/// 복사한 글을 적어 둔다(실제 클립보드 대신).
List<String> _clipboard(WidgetTester tester) {
  final copied = <String>[];
  tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(SystemChannels.platform, (call) async {
    if (call.method == 'Clipboard.setData') copied.add('${(call.arguments as Map)['text']}');
    return null;
  });
  addTearDown(() => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(SystemChannels.platform, null));
  return copied;
}

void main() {
  testWidgets('대화 상대 관리: 유족 명단과 문자 문안이 보이고, 복사만 한다(자동 발송 없음)', (tester) async {
    final copied = _clipboard(tester);
    await _pump(tester, AdminPersonaPage(personaId: 'p1', api: _FakeApi()));
    expect(_has('신청 자격과 유족 명단'), findsOneWidget);
    expect(_has('손주'), findsWidgets);
    expect(_has('김가족 · 010-0000-0000'), findsOneWidget);
    expect(_has('TalkTo는 자동으로 보내지 않아요.'), findsOneWidget);
    expect(_has(_smsText), findsOneWidget);
    expect(_has('받는 번호'), findsOneWidget);
    expect(find.textContaining('보내기'), findsNothing);
    // 멈추지 않은 대화 상대에는 남은 날 환불 제안 카드가 없다
    expect(_has('남은 날 환불 제안'), findsNothing);

    await tester.tap(find.text('문자 복사'));
    await tester.pumpAndSettle();
    expect(copied, [_smsText]);
    // 결제·체험 줄
    expect(_chip('전액 환불 대상'), findsOneWidget);
  });

  testWidgets('이의 접수: 확인창에서 취소하면 아무것도 안 하고, 멈추기를 누르면 접수하고 남은 날 환불 제안을 보인다', (tester) async {
    final api = _FakeApi();
    await _pump(tester, AdminPersonaPage(personaId: 'p1', api: api));
    await tester.tap(_pill('고인이 생전에 원하지 않으심'));
    await tester.pump();
    await tester.enterText(find.widgetWithText(TextField, '보낸 분 이름'), '김가족');
    await tester.ensureVisible(find.widgetWithText(FilledButton, '이의 접수'));
    await tester.tap(find.widgetWithText(FilledButton, '이의 접수'));
    await tester.pumpAndSettle();
    expect(_has('이 대화 상대를 바로 멈추고 7일 뒤 지워요. 계속할까요?'), findsOneWidget);
    await tester.tap(find.text('취소'));
    await tester.pumpAndSettle();
    expect(api.objections, isEmpty);

    await tester.tap(find.widgetWithText(FilledButton, '이의 접수'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('멈추기'));
    await tester.pumpAndSettle();
    expect(api.objections, [
      {'reason': 'prior_wish', 'fromName': '김가족', 'fromRelation': '', 'note': ''},
    ]);
    expect(_has('남은 날 환불 제안'), findsWidgets);
    expect(_has('13,457원'), findsOneWidget);
    // 멈춘 뒤에는 해제·미루기만
    expect(find.text('멈춤 해제'), findsOneWidget);
    expect(find.text('지우는 날 미루기'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, '이의 접수'), findsNothing);
    expect(find.widgetWithText(OutlinedButton, '이의 기록 더하기'), findsOneWidget);
  });

  testWidgets('멈춘 대화 상대에도 다른 분의 이의를 기록만 더한다(지우는 날은 그대로)', (tester) async {
    final api = _FakeApi(
      legal: _legal(hold: {'reason': 'prior_wish', 'holdAt': '2026-10-23T01:00:00Z', 'deleteAfter': '2026-10-30T01:00:00Z'}),
    );
    await _pump(tester, AdminPersonaPage(personaId: 'p1', api: api));
    expect(_has('멈춘 대화 상대'), findsOneWidget);
    await tester.tap(_pill('유족 이의'));
    await tester.pump();
    await tester.enterText(find.widgetWithText(TextField, '보낸 분 이름'), '이가족');
    await tester.enterText(find.widgetWithText(TextField, '고인·그분과의 관계'), '딸');
    final add = find.widgetWithText(OutlinedButton, '이의 기록 더하기');
    await tester.ensureVisible(add);
    await tester.tap(add);
    await tester.pumpAndSettle();
    expect(_has('이미 멈춘 대화 상대예요. 기록만 더해요(지우는 날은 그대로).'), findsOneWidget);
    await tester.tap(find.text('취소'));
    await tester.pumpAndSettle();
    expect(api.objections, isEmpty);

    await tester.tap(add);
    await tester.pumpAndSettle();
    await tester.tap(find.text('기록 더하기'));
    await tester.pumpAndSettle();
    expect(api.objections, [
      {'reason': 'objection', 'fromName': '이가족', 'fromRelation': '딸', 'note': ''},
    ]);
    expect(_has('이의 기록을 더했어요. 지우는 날은 그대로예요.'), findsOneWidget);
    // 멈춤 해제·미루기는 그대로 있다
    expect(find.text('멈춤 해제'), findsOneWidget);
  });

  testWidgets('동의 녹음 결과로 멈춘 대화 상대에 이의가 오면 까닭을 이의로 바꾼다고 확인하고 알린다', (tester) async {
    final api = _FakeApi(
      legal: _legal(hold: {'reason': 'subject_refused', 'holdAt': '2026-10-23T01:00:00Z', 'deleteAfter': '2026-10-30T01:00:00Z'}),
    );
    await _pump(tester, AdminPersonaPage(personaId: 'p1', api: api));
    await tester.tap(_pill('유족 이의'));
    await tester.pump();
    await tester.enterText(find.widgetWithText(TextField, '보낸 분 이름'), '이가족');
    final add = find.widgetWithText(OutlinedButton, '이의 기록 더하기');
    await tester.ensureVisible(add);
    await tester.tap(add);
    await tester.pumpAndSettle();
    expect(_has(systemHoldObjectionText), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, '이의로 바꾸기'));
    await tester.pumpAndSettle();
    expect(api.objections.single['reason'], 'objection');
    expect(_has('까닭을 이의로 바꿨어요. 지우는 날을 다시 정하고 만든 분께 다시 알렸어요.'), findsOneWidget);
  });

  test('이의 접수 뒤 알림 글은 서버 응답을 따른다', () {
    expect(objectionDoneText({'already': false}, held: true), '멈췄어요. 만든 분께 알림을 남겼어요.');
    expect(objectionDoneText({'already': true, 'replaced': false}, held: false), '이의 기록을 더했어요. 지우는 날은 그대로예요.');
    expect(objectionDoneText({'replaced': true}, held: true), startsWith('까닭을 이의로 바꿨어요.'));
    expect(objectionDoneText(const {}, held: true), '이의 기록을 더했어요. 지우는 날은 그대로예요.');
  });

  testWidgets('멈춘 대화 상대: 서버가 계산한 남은 날 환불 제안을 보이고, 환불했어요는 일할 까닭으로 적는다', (tester) async {
    final api = _FakeApi(
      legal: _legal(
        hold: {'reason': 'objection', 'holdAt': '2026-10-23T01:00:00Z', 'deleteAfter': '2026-10-30T01:00:00Z'},
        prorated: {'paymentId': 'pay1', 'amount': 14900, 'remainingDays': 20, 'periodDays': 31, 'refund': 9612},
      ),
    );
    await _pump(tester, AdminPersonaPage(personaId: 'p1', api: api));
    expect(_has('9,612원'), findsWidgets);
    expect(_has('남은 20일 / 31일'), findsWidgets);
    await tester.ensureVisible(find.widgetWithText(OutlinedButton, '환불했어요').first);
    await tester.tap(find.widgetWithText(OutlinedButton, '환불했어요').first);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, '적기'));
    await tester.pumpAndSettle();
    expect(api.refunds, [
      {'paymentId': 'pay1', 'amount': 9612, 'reason': 'prorated'},
    ]);
  });

  testWidgets('결제·환불: 판단 배지와 환불 기록, 환불했어요는 금액·까닭을 적는다(토스는 부르지 않음)', (tester) async {
    final api = _FakeApi();
    await _pump(tester, PaymentsAdminPage(api: api));
    expect(_has('환불은 토스 상점관리자에서 먼저 하고'), findsOneWidget);
    expect(_chip('전액 환불 대상'), findsOneWidget);
    expect(_has('결제 후 첫 대화'), findsWidgets);
    expect(_has('환불했어요: '), findsOneWidget);
    // 까닭 코드는 이름으로
    expect(_has('결제 후 7일 안 대화 없음'), findsOneWidget);
    // 환불을 이미 적은 줄에는 단추가 없다
    expect(find.widgetWithText(OutlinedButton, '환불했어요'), findsOneWidget);
    await tester.tap(find.widgetWithText(OutlinedButton, '환불했어요'));
    await tester.pumpAndSettle();
    expect(_has('여기서는 돈이 오가지 않고 기록만 남아요.'), findsOneWidget);
    // 전액 환불 대상이면 그 까닭과 결제 금액을 미리 채운다
    await tester.tap(find.widgetWithText(FilledButton, '적기'));
    await tester.pumpAndSettle();
    expect(api.refunds, [
      {'paymentId': 'pay1', 'amount': 14900, 'reason': 'no_chat_7d'},
    ]);
  });

  testWidgets('멈춘 목록·예전 대화 상대 목록(단추 없음)', (tester) async {
    await _pump(tester, HoldsAdminPage(api: _FakeApi()));
    expect(_has('유족 이의'), findsOneWidget);
    expect(_has('지우는 때 '), findsWidgets);

    await _pump(tester, UnableListPage(api: _FakeApi()));
    expect(_has('대화가 어려우신 분(예전 상태) · ex***@example.com · READY'), findsOneWidget);
    expect(_has('부모가 아닌 분이 만든 아이의 대화 상대 · READY'), findsOneWidget);
    expect(find.byType(FilledButton), findsNothing);
  });

  testWidgets('확인 문자: manual 이면 번호·문자 글 복사와 보냈어요, off 면 꺼져 있다고만', (tester) async {
    final copied = _clipboard(tester);
    final api = _FakeApi(smsRows: [
      {
        'personaId': 'p9',
        'title': '예시 딸',
        'phone': '01000000000',
        'code': '123456',
        'text': '[TalkTo] 확인 번호 123456을 넣어 주세요(24시간 안).',
        'requestedAt': '2026-10-23T01:00:00Z',
      },
    ]);
    await _pump(tester, GuardianSmsPage(api: api));
    expect(_has('TalkTo는 자동으로 보내지 않아요.'), findsOneWidget);
    await tester.tap(find.text('문자 글 복사'));
    await tester.pumpAndSettle();
    expect(copied, ['[TalkTo] 확인 번호 123456을 넣어 주세요(24시간 안).']);
    await tester.tap(find.widgetWithText(FilledButton, '보냈어요'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, '보냈어요').last);
    await tester.pumpAndSettle();
    expect(api.sent, ['p9']);
    expect(_has('보낼 확인 문자가 없어요.'), findsOneWidget);

    await _pump(tester, GuardianSmsPage(key: const ValueKey('off'), api: _FakeApi(smsMeta: const {'provider': 'off'})));
    expect(_has('SMS_PROVIDER=off'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, '보냈어요'), findsNothing);

    await _pump(
      tester,
      GuardianSmsPage(
        key: const ValueKey('error'),
        api: _FakeApi(smsError: const ApiException('지금은 확인 문자를 보낼 수 없어요.', statusCode: 409, code: 'sms_unavailable')),
      ),
    );
    expect(_has('SMS_PROVIDER=off'), findsOneWidget);

    // 서버가 provider 를 주지 않으면 /intake/meta 의 under14Open 으로 가른다(off 기본 → 꺼짐)
    expect(GuardianSmsPage.active(const {'under14Open': false}), isFalse);
    expect(GuardianSmsPage.active(const {'under14Open': true}), isTrue);
    expect(GuardianSmsPage.active(const {'provider': 'log', 'under14Open': true}), isFalse);

    // 꺼졌어도 그 전에 쌓인 요청은 보이고, 유효 시간이 지난 확인 번호는 따로 알린다
    await _pump(
      tester,
      GuardianSmsPage(
        key: const ValueKey('stale'),
        api: _FakeApi(smsMeta: const {'under14Open': false}, smsRows: [
          {
            'personaId': 'p8',
            'title': '예시 아들',
            'phone': '01000000000',
            'code': '654321',
            'text': '[TalkTo] 확인 번호 654321',
            'requestedAt': '2026-10-21T01:00:00Z',
            'expired': true,
          },
        ]),
      ),
    );
    expect(_has('SMS_PROVIDER=off'), findsOneWidget);
    expect(_has('확인 번호의 유효 시간이 지났어요.'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, '보냈어요'), findsOneWidget);
  });

  test('확인 문자 대기열: 서버가 provider 를 주지 않으면 /intake/meta 의 under14Open 을 붙여 가른다', () async {
    Map<String, Object?> queue({Map<String, Object?> meta = const {'total': 0}}) =>
        {'success': true, 'data': const [], 'meta': meta};
    var server = _Server({
      'GET /admin/guardian-sms': queue,
      'GET /intake/meta': () => {
            'success': true,
            'data': {'under14Open': false},
          },
    });
    ApiClient.useAdapterForTests(server);
    var result = await const LegalAdminApi().guardianSms();
    expect(result.meta['under14Open'], isFalse);
    expect(GuardianSmsPage.active(result.meta), isFalse);

    // 서버가 provider 를 주면 그것만 본다(/intake/meta 는 읽지 않음)
    server = _Server({
      'GET /admin/guardian-sms': () => queue(meta: const {'total': 0, 'provider': 'manual'}),
    });
    ApiClient.useAdapterForTests(server);
    result = await const LegalAdminApi().guardianSms();
    expect(GuardianSmsPage.active(result.meta), isTrue);
    expect(server.log, ['GET /admin/guardian-sms']);
  });

  testWidgets('관리자 첫 화면 카드: 수를 붙이고, 확인 문자는 manual 일 때만', (tester) async {
    await _pump(tester, Scaffold(body: SingleChildScrollView(child: LegalAdminLinks(api: _FakeApi()))));
    expect(_has('동의 녹음 검수 3건'), findsOneWidget);
    expect(_has('멈춘 대화 상대 1'), findsOneWidget);
    expect(_has('대화가 어려우신 분 대화 상대 2'), findsOneWidget);
    expect(_has('결제·환불'), findsOneWidget);
    expect(_has('확인 문자 보내기 0건'), findsOneWidget);

    await _pump(
      tester,
      Scaffold(
        body: SingleChildScrollView(
          child: LegalAdminLinks(key: const ValueKey('off'), api: _FakeApi(smsMeta: const {'provider': 'off'})),
        ),
      ),
    );
    expect(_has('확인 문자 보내기'), findsNothing);
  });
}
