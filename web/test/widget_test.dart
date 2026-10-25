import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/auth/consent_page.dart';
import 'package:talkto_fe/features/auth/login_page.dart';
import 'package:talkto_fe/features/billing/data/billing_api.dart';
import 'package:talkto_fe/features/billing/plans_page.dart';
import 'package:talkto_fe/features/legal/consent_checks.dart';
import 'package:talkto_fe/features/legal/legal_text.dart';
import 'package:talkto_fe/features/personas/material_guides.dart';
import 'package:talkto_fe/features/personas/materials_page.dart';
import 'package:talkto_fe/features/personas/data/persona_store.dart';
import 'package:talkto_fe/features/personas/widgets/answers_form.dart';
import 'package:talkto_fe/features/personas/widgets/basics_form.dart';
import 'package:talkto_fe/shared/korean.dart';
import 'package:talkto_fe/shared/widgets/ui.dart';

Future<Map<String, dynamic>> _noProviders() async => const {};

/// 로그인 화면 아래 사업자 정보(서버 없이).
Future<Map<String, dynamic>> _noBusiness() async => const {};

/// 화면 글은 단어 중간에서 끊기지 않게 보이지 않는 이음표가 들어 있어(keepAll), 그걸 빼고 찾는다.
Finder findShown(String text) => find.byWidgetPredicate(
      (w) => w is Text && w.data?.replaceAll('\u2060', '') == text,
    );

/// Text.rich 까지 포함해 보이는 글(이음표 뺀)로 찾는다.
Finder findRich(String text) => find.byWidgetPredicate(
      (w) => w is Text && (w.data ?? w.textSpan?.toPlainText())?.replaceAll('⁠', '') == text,
    );

const _accountItems = [
  {'type': 'age14', 'label': '만 14세 이상이에요', 'doc': null, 'kind': 'confirm'},
  {'type': 'terms', 'label': '이용약관에 동의해요', 'doc': 'terms', 'kind': 'consent'},
  {'type': 'privacy', 'label': '개인정보 수집·이용 안내를 확인했어요', 'doc': 'privacy', 'kind': 'confirm'},
];

Future<List<Map<String, dynamic>>> _fakeItems() async =>
    [for (final item in _accountItems) Map<String, dynamic>.from(item)];

void main() {
  testWidgets('로그인 화면에서 회원가입으로 바꾸면 이름·비밀번호 확인 칸이 나온다', (tester) async {
    await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const LoginPage(loadProviders: _noProviders, loadBusiness: _noBusiness)));
    // 카카오·구글이 먼저 보이고, 이메일은 한 번 더 눌러야 나온다
    expect(find.text('카카오로 시작하기'), findsOneWidget);
    expect(find.text('Google로 계속하기'), findsOneWidget);
    await tester.ensureVisible(find.text('이메일로 계속하기'));
    await tester.tap(find.text('이메일로 계속하기'));
    await tester.pumpAndSettle();
    expect(find.text('이름'), findsNothing);

    await tester.tap(find.text('회원가입'));
    await tester.pumpAndSettle();
    expect(find.text('이름'), findsOneWidget);
    expect(find.text('비밀번호 확인'), findsOneWidget);

    // 비밀번호가 짧으면 서버에 보내기 전에 막는다
    await tester.enterText(find.byType(TextField).at(0), '홍길동');
    await tester.enterText(find.byType(TextField).at(1), 'me@example.com');
    await tester.enterText(find.byType(TextField).at(2), '1234');
    await tester.ensureVisible(find.text('가입하고 시작하기'));
    await tester.tap(find.text('가입하고 시작하기'));
    await tester.pump();
    expect(findShown('비밀번호는 8자 이상이어야 해요.'), findsOneWidget);
  });

  testWidgets('소셜 로그인에서 실패해 돌아오면 그 이유를 보여준다', (tester) async {
    await tester.pumpWidget(
        MaterialApp(theme: buildTheme(), home: const LoginPage(error: '로그인을 취소했어요.', loadProviders: _noProviders, loadBusiness: _noBusiness)));
    expect(findShown('로그인을 취소했어요.'), findsOneWidget);
  });

  testWidgets('넓은 화면은 소개 판과 로그인 칸을 나란히 둔다', (tester) async {
    tester.view.physicalSize = const Size(1280, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const LoginPage(loadProviders: _noProviders, loadBusiness: _noBusiness)));
    expect(find.text('시작하기'), findsOneWidget);
    expect(find.text('할머니 정말 많이 보고 싶어요'), findsOneWidget);
  });

  testWidgets('선지가 길면 세로로 쌓고 고른 것을 표시한다', (tester) async {
    String? picked;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: StatefulBuilder(
          builder: (context, setState) => ChoiceGroup(
            options: const ['한두 문장으로 짧게', '그보다 길게, 예를 들어가며 자세히'],
            value: picked,
            onChanged: (v) => setState(() => picked = v),
          ),
        ),
      ),
    ));
    await tester.tap(findShown('한두 문장으로 짧게'));
    await tester.pump();
    expect(picked, '한두 문장으로 짧게');
  });

  test('목록 시각은 오늘이면 시각, 어제면 어제로 짧게 쓴다', () {
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day, 15, 7);
    expect(shortTime(today.toUtc().toIso8601String()), '오후 3:07');
    expect(shortTime(today.subtract(const Duration(days: 1)).toUtc().toIso8601String()), '어제');
    // 로컬 SQLite 는 시간대 없이 UTC 를 준다
    final utc = today.toUtc().toIso8601String();
    expect(shortTime(utc.substring(0, utc.length - 1)), '오후 3:07');
    expect(shortTime(null), '');
  });

  test('정보 고치기는 저장된 값으로 칸을 채우고, 목록에 없는 관계는 직접 입력으로 둔다', () {
    final saved = {
      'name': '김영자',
      'relation': '큰이모',
      'addressTerm': '이모',
      'status': 'alive',
      'myName': '김하늘',
      'myRelation': '조카',
      'calledMe': '하늘아',
    };
    final draft = BasicsDraft(basics: saved, relations: const ['어머니', '할머니']);
    expect(draft.relation, '직접 입력');
    expect(draft.toJson(), saved);
    final known = BasicsDraft(basics: {...saved, 'relation': '할머니'}, relations: const ['어머니', '할머니']);
    expect(known.relation, '할머니');
    expect(known.relationOther.text, '');
    draft.dispose();
    known.dispose();
  });

  test('화면에는 성함 대신 호칭으로 부르고, 받침에 맞는 조사를 붙인다', () {
    expect(personaTitle({'name': '김영자', 'addressTerm': '할머니', 'relation': '할머니'}), '할머니');
    expect(personaTitle({'name': '김영자', 'addressTerm': ' ', 'relation': '외할머니'}), '외할머니');
    expect(personaTitle({'name': '김영자'}), '그분');
    expect(josa('할머니', '은', '는'), '할머니는');
    expect(josa('선생님', '은', '는'), '선생님은');
    expect(josa('Mom', '은', '는'), 'Mom는');
  });

  test('요금제 이름에 맞는 조사를 붙인다', () {
    expect(toPlan({'name': 'Memories'}), 'Memories로');
    expect(toPlan({'name': 'Family'}), 'Family로');
    expect(toPlan({'name': '추억'}), '추억으로');
    expect(toPlan({'name': '가족'}), '가족으로');
    expect(toPlan({'name': '가을'}), '가을로'); // ㄹ 받침은 '로'
    expect(toPlan({'name': '추억 요금제'}), '추억 요금제로');
    expect(won(19900), '19,900원');
  });

  test('페르소나마다 남은 대화 횟수를 머리글 글로 보여 주고, 보낸 뒤 바로 줄인다', () {
    final store = BillingStore();
    store.setMe({
      'personas': [
        {'personaId': 'a', 'active': true, 'status': 'ACTIVE', 'conversationsLimit': 30, 'conversationsLeft': 12},
        {'personaId': 'b', 'active': true, 'status': 'TRIAL', 'conversationsLimit': 30, 'conversationsLeft': 3},
        {'personaId': 'c', 'active': true, 'status': 'ADMIN', 'conversationsLeft': null},
      ],
    });
    expect(BillingStore.conversationsText(store.persona('a')), '이번 달 남은 대화 12번');
    store.setConversationsLeft('a', 11);
    expect(BillingStore.conversationsText(store.persona('a')), '이번 달 남은 대화 11번');
    expect(store.persona('a')!['conversationsUsed'], 19);
    // 다른 페르소나는 그대로(결제·횟수는 페르소나마다)
    expect(BillingStore.conversationsText(store.persona('b')), '무료 대화 3번 남음');
    expect(BillingStore.conversationsText(store.persona('c')), isNull); // 제한이 없으면 보이지 않는다
    expect(BillingStore.conversationsText(null), isNull);
    // 다 쓰면 끊지 않고 글로 대답한다
    store.setConversationsLeft('a', 0);
    expect(store.persona('a')!['textOnly'], isTrue);
    expect(BillingStore.conversationsText(store.persona('a')), '대화 횟수를 다 써서 지금은 목소리 대신 글로 대답해요');
  });

  test('결제 전(PRELAUNCH)에는 머리글에 횟수 제한 없음, 상한이 있으면 남은 무료 대화를 보여 준다', () {
    final store = BillingStore();
    store.setMe({
      'paymentsOpen': false,
      'prelaunchConversations': null,
      'personas': [
        {
          'personaId': 'free',
          'active': true,
          'status': 'PRELAUNCH',
          'conversationsLimit': null,
          'conversationsLeft': null,
          'request': {'plan': 'family', 'planName': '가족', 'status': 'REQUESTED'},
        },
        {'personaId': 'capped', 'active': true, 'status': 'PRELAUNCH', 'conversationsLimit': 100, 'conversationsLeft': 40},
      ],
    });
    expect(store.prelaunch, isTrue);
    expect(store.paymentsOpen, isFalse);
    expect(store.requestCount, 1);
    expect(BillingStore.conversationsText(store.persona('free')), '결제 전 무료 · 횟수 제한 없음');
    expect(BillingStore.conversationsText(store.persona('capped')), '무료 대화 40번 남음');
    store.setConversationsLeft('capped', 39);
    expect(BillingStore.conversationsText(store.persona('capped')), '무료 대화 39번 남음');
    // 보낸 뒤 서버가 남은 횟수를 주지 않아도(제한 없음) 그대로
    store.setConversationsLeft('free', null);
    expect(BillingStore.conversationsText(store.persona('free')), '결제 전 무료 · 횟수 제한 없음');

    // 예전 서버(paymentsOpen 없음)는 결제가 열린 것으로 본다
    final old = BillingStore()..setMe({'personas': const []});
    expect(old.paymentsOpen, isTrue);
    expect(old.prelaunch, isFalse);
  });

  group('요금제 화면', () {
    const plans = [
      {'id': 'memories', 'name': '추억', 'price': 9900, 'chargePrice': 4900, 'beta': true, 'conversations': 50},
      {'id': 'family', 'name': '가족', 'price': 29900, 'chargePrice': 14900, 'conversations': 300},
      // 지금 고를 수 없는 요금제. 서버가 목록에 주어도 화면에는 보이지 않는다
      {'id': 'old', 'name': '예전 요금제', 'price': 1000, 'conversations': 1},
    ];

    Map<String, dynamic> me({bool? paymentsOpen, String status = 'PRELAUNCH', Map<String, dynamic>? request}) => {
          if (paymentsOpen != null) 'paymentsOpen': paymentsOpen,
          'prelaunchConversations': null,
          'betaFamilies': 100,
          'plans': plans,
          'personas': [
            {
              'personaId': 'a',
              'title': '할머니',
              'step': 'READY',
              'status': status,
              'active': true,
              'plan': 'trial',
              'conversationsLimit': null,
              'conversationsLeft': null,
              'deceased': false,
              'request': request,
            },
          ],
        };

    Future<void> show(WidgetTester tester, Map<String, dynamic> data, {Size size = const Size(1280, 2400)}) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: PlansPage(
          personaId: 'a',
          loadMe: () async => data,
          loadPayments: () async => const <Map<String, dynamic>>[],
        ),
      ));
      await tester.pumpAndSettle();
    }

    Finder containing(String text) => find.byWidgetPredicate((w) {
          final shown = w is Text ? (w.data ?? w.textSpan?.toPlainText() ?? '') : '';
          return shown.replaceAll('⁠', '').contains(text);
        });

    testWidgets('결제 전: 카드 단추는 구독 신청하기이고, 확인 창에는 결제 체크가 없다', (tester) async {
      await show(tester, me(paymentsOpen: false));
      expect(containing('결제가 열리기 전이에요'), findsOneWidget);
      expect(containing('신청만으로는 돈이 나가지 않아요'), findsWidgets);
      expect(find.text('할머니 · 결제 전 무료로 쓰는 중'), findsOneWidget);
      expect(find.widgetWithText(OutlinedButton, '구독 신청하기'), findsNWidgets(2));
      // 카드는 추억·가족 둘뿐이다(지금 고를 수 없는 요금제는 서버가 줘도 보이지 않는다)
      expect(find.widgetWithText(FilledButton, '구독 신청하기'), findsNothing);
      expect(find.text('예전 요금제'), findsNothing);
      expect(find.text('결제 내역'), findsNothing);
      expect(find.text('해지'), findsNothing);

      await tester.tap(find.widgetWithText(OutlinedButton, '구독 신청하기').first);
      await tester.pumpAndSettle();
      expect(find.text('추억 요금제 구독 신청'), findsOneWidget);
      expect(find.widgetWithText(FilledButton, '신청하기'), findsOneWidget);
      expect(find.text('월 4,900원'), findsOneWidget);
      expect(find.byType(Checkbox), findsNothing);
      expect(find.byType(TextField), findsNothing); // 연락처 칸이 없다
      expect(find.text('결제하기'), findsNothing);
      await tester.tap(find.text('취소'));
      await tester.pumpAndSettle();
    });

    testWidgets('결제 전: 신청한 카드는 신청했어요와 신청 취소, 다른 카드는 신청을 바꾼다', (tester) async {
      await show(
        tester,
        me(paymentsOpen: false, request: {'plan': 'memories', 'planName': '추억', 'status': 'REQUESTED'}),
      );
      expect(find.text('신청했어요 ✓'), findsOneWidget);
      expect(find.text('신청 취소'), findsOneWidget);
      expect(containing('추억 요금제 신청함 · 결제가 열리면 알려 드려요'), findsOneWidget);
      await tester.tap(find.widgetWithText(OutlinedButton, '구독 신청하기').first);
      await tester.pumpAndSettle();
      expect(find.text('가족 요금제로 신청을 바꿀까요?'), findsOneWidget);
      await tester.tap(find.text('취소'));
      await tester.pumpAndSettle();
    });

    testWidgets('결제 전 화면과 신청 확인 창은 휴대폰 폭에서도 넘치지 않는다', (tester) async {
      await show(
        tester,
        me(paymentsOpen: false, request: {'plan': 'family', 'planName': '가족', 'status': 'REQUESTED'}),
        size: const Size(375, 812),
      );
      expect(find.text('신청했어요 ✓'), findsOneWidget);
      final first = find.widgetWithText(OutlinedButton, '구독 신청하기').first;
      await tester.ensureVisible(first);
      await tester.pumpAndSettle();
      await tester.tap(first);
      await tester.pumpAndSettle();
      expect(find.text('추억 요금제로 신청을 바꿀까요?'), findsOneWidget);
      expect(find.widgetWithText(FilledButton, '바꾸기'), findsOneWidget);
      await tester.tap(find.text('취소'));
      await tester.pumpAndSettle();
    });

    testWidgets('결제가 열리면 신청해 둔 요금제를 시작하게 하고, 카드는 지금의 결제 단추다', (tester) async {
      // paymentsOpen 이 없으면(예전 서버) 열린 것으로 본다
      await show(tester, me(status: 'TRIAL', request: {'plan': 'family', 'planName': '가족', 'status': 'REQUESTED'}));
      expect(find.text('신청하신 가족 요금제를 시작할 수 있어요'), findsOneWidget);
      expect(find.widgetWithText(FilledButton, '시작하기'), findsOneWidget);
      expect(find.text('구독 신청하기'), findsNothing);
      expect(find.widgetWithText(OutlinedButton, '이 요금제로 시작하기'), findsNWidgets(2));
      expect(containing('결제가 열리기 전이에요'), findsNothing);
    });

    testWidgets('지금 고를 수 없는 요금제를 예전에 신청했으면 결제가 열려도 시작하기로 이어지지 않는다', (tester) async {
      // 서버가 그 요금제를 목록에 주고, 그 신청도 남아 있어도
      await show(
        tester,
        me(status: 'TRIAL', request: {'plan': 'old', 'planName': '예전 요금제', 'status': 'REQUESTED'}),
      );
      expect(containing('시작할 수 있어요'), findsNothing);
      expect(find.widgetWithText(FilledButton, '시작하기'), findsNothing);
      expect(find.widgetWithText(OutlinedButton, '이 요금제로 시작하기'), findsNWidgets(2));
    });

    testWidgets('두 카드가 들어갈 폭이면 나란히 놓는다(예전 세 카드 기준보다 좁아도)', (tester) async {
      await show(tester, me(paymentsOpen: false), size: const Size(640, 2400));
      final memories = tester.getTopLeft(find.widgetWithText(OutlinedButton, '구독 신청하기').first);
      final family = tester.getTopLeft(find.widgetWithText(OutlinedButton, '구독 신청하기').last);
      // 위아래로 쌓이면 두 단추의 왼쪽 끝이 같다
      expect(family.dx, greaterThan(memories.dx));
    });
  });

  test('자료 종류마다 꺼내는 방법이 있다', () {
    const kinds = ['kakaotalk', 'sms', 'email', 'blog', 'diary', 'sns', 'other'];
    expect([for (final g in materialGuides) g.kind], kinds);
    for (final g in materialGuides) {
      expect(g.sections, isNotEmpty, reason: g.kind);
      expect(g.sections.every((s) => s.steps.isNotEmpty), isTrue, reason: g.kind);
    }
    expect(guideFor('nope').kind, 'other');
    expect(materialTypes, containsAll(['txt', 'eml', 'html', 'csv', 'mbox', 'xml']));
    expect(materialTypes, isNot(contains('zip')));
  });

  testWidgets('선택 질문: 고른 답을 다시 누르면 지우고, 개수 상한과 적어 둔 말을 지킨다', (tester) async {
    final answers = <String, dynamic>{};
    final form = GlobalKey<AnswersFormState>();
    final questions = [
      {'id': 'T1-02', 'section': '마지막 시기', 'type': 'single', 'title': '어떻게', 'options': ['노환으로', '갑작스럽게']},
      {'id': 'M4-02', 'section': '성격', 'type': 'phrases', 'max': 2, 'title': '중요한 것', 'placeholder': '예: 가족'},
    ];
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: Scaffold(
        body: SingleChildScrollView(
          child: AnswersForm(key: form, questions: questions, answers: answers, keys: const {}, onChanged: () {}),
        ),
      ),
    ));
    await tester.tap(findShown('노환으로'));
    await tester.pump();
    expect(answers['T1-02'], '노환으로');
    await tester.tap(findShown('노환으로'));
    await tester.pump();
    expect(answers.containsKey('T1-02'), isFalse);

    final field = find.byType(TextField);
    for (final v in ['가족', '건강', '정직']) {
      await tester.enterText(field, v);
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pump();
    }
    expect(answers['M4-02'], ['가족', '건강']); // 서버가 받는 만큼(2개)만
    await tester.pump(const Duration(seconds: 5)); // 알림이 사라지게

    await tester.tap(find.byTooltip('빼기').first); // 하나를 빼면 다시 넣을 수 있다
    await tester.pump();
    await tester.enterText(field, '화목');
    form.currentState!.commitPending(); // '더하기'를 누르지 않고 저장해도 넣는다
    expect(answers['M4-02'], ['건강', '화목']);
  });

  testWidgets('동의문 본문: 제목·목록·굵은 글씨를 마크다운 기호 없이 그린다', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: const Scaffold(
        body: SingleChildScrollView(
          child: LegalText(
            '첫 문단이에요.\n이어지는 줄이에요.\n\n## 1. 큰 제목\n### 작은 제목\n- 목록 **굵은 말** 끝\n  - 하위 목록',
            title: '이용약관',
          ),
        ),
      ),
    ));
    expect(findRich('이용약관'), findsOneWidget);
    expect(findRich('첫 문단이에요. 이어지는 줄이에요.'), findsOneWidget); // 줄바꿈만 있으면 한 문단
    expect(findRich('1. 큰 제목'), findsOneWidget);
    expect(findRich('작은 제목'), findsOneWidget);
    expect(findRich('목록 굵은 말 끝'), findsOneWidget);
    expect(findRich('하위 목록'), findsOneWidget);
    // 기호(##, -, **)는 화면에 남지 않는다
    expect(find.byWidgetPredicate((w) {
      final shown = w is Text ? (w.data ?? w.textSpan?.toPlainText() ?? '') : '';
      return shown.contains('**') || shown.startsWith('#') || shown.startsWith('- ');
    }), findsNothing);
    // 굵게 표시한 말만 굵다
    final item = tester.widget<Text>(findRich('목록 굵은 말 끝'));
    final bold = <String>[];
    item.textSpan!.visitChildren((span) {
      if (span is TextSpan && span.style?.fontWeight == FontWeight.w700) bold.add(span.text!.replaceAll('\u2060', ''));
      return true;
    });
    expect(bold, ['굵은 말']);
    // 제목은 본문보다 크다
    final heading = tester.widget<Text>(findRich('1. 큰 제목'));
    expect(heading.textSpan!.style!.fontSize, greaterThan(AppText.body.fontSize!));
  });

  testWidgets('동의 한 줄은 미리 체크하지 않고, 문서가 있으면 보기가 있다', (tester) async {
    final changes = <bool>[];
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: Scaffold(
        body: Column(
          children: [
            ConsentCheck(label: '이용약관에 동의해요', value: false, onChanged: changes.add, docKey: 'terms', tag: '필수'),
            ConsentCheck(label: '만 14세 이상이에요', value: false, onChanged: changes.add),
          ],
        ),
      ),
    ));
    final boxes = tester.widgetList<Checkbox>(find.byType(Checkbox)).toList();
    expect(boxes, hasLength(2));
    expect(boxes.every((b) => b.value == false), isTrue);
    expect(find.text('보기'), findsOneWidget); // 문서가 있는 줄에만
    await tester.tap(findRich('[필수] 이용약관에 동의해요'));
    await tester.pump();
    expect(changes, [true]);
  });

  testWidgets('동의 화면: 약관이 바뀐 회원은 바뀐 항목만 다시 보고, 여기서 탈퇴할 수도 있다', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: const ConsentPage(loadItems: _fakeItems, showBusiness: false, needed: ['terms']),
    ));
    await tester.pumpAndSettle();
    expect(findRich('약관과 안내가 바뀌었어요'), findsOneWidget);
    expect(findRich('[필수] 이용약관에 동의해요'), findsOneWidget);
    // 만 14세 확인처럼 그대로인 항목은 다시 묻지 않는다
    expect(findRich('[필수] 만 14세 이상이에요'), findsNothing);
    expect(findRich('[필수] 개인정보 수집·이용 안내를 확인했어요'), findsNothing);
    expect(find.text('바뀐 점 보기'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, '동의하고 계속하기'), findsOneWidget);
    expect(find.widgetWithText(TextButton, '회원 탈퇴'), findsOneWidget);
  });

  testWidgets('동의 화면: 다 체크해야 시작할 수 있고, 모두 동의는 한꺼번에 켜고 끈다', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: const ConsentPage(loadItems: _fakeItems, showBusiness: false, needed: ['age14', 'terms', 'privacy']),
    ));
    await tester.pumpAndSettle();
    expect(findRich('[필수] 만 14세 이상이에요'), findsOneWidget);
    // 미리 체크된 것이 없다(모두 동의 + 항목 3개)
    final boxes = tester.widgetList<Checkbox>(find.byType(Checkbox)).toList();
    expect(boxes, hasLength(4));
    expect(boxes.every((b) => b.value == false), isTrue);

    FilledButton start() => tester.widget<FilledButton>(find.widgetWithText(FilledButton, '동의하고 시작하기'));
    expect(start().onPressed, isNull);

    // 하나씩 체크: 마지막까지 체크해야 켜진다
    for (final label in ['만 14세 이상이에요', '이용약관에 동의해요']) {
      await tester.ensureVisible(findRich('[필수] $label'));
      await tester.tap(findRich('[필수] $label'));
      await tester.pump();
      expect(start().onPressed, isNull);
    }
    await tester.ensureVisible(findRich('[필수] 개인정보 수집·이용 안내를 확인했어요'));
    await tester.tap(findRich('[필수] 개인정보 수집·이용 안내를 확인했어요'));
    await tester.pump();
    expect(start().onPressed, isNotNull);

    // 하나를 끄면 다시 꺼진다
    await tester.tap(findRich('[필수] 이용약관에 동의해요'));
    await tester.pump();
    expect(start().onPressed, isNull);

    // 모두 동의: 켜면 전부, 다시 누르면 전부 끈다
    await tester.ensureVisible(find.text('모두 동의해요'));
    await tester.tap(find.text('모두 동의해요'));
    await tester.pump();
    expect(tester.widgetList<Checkbox>(find.byType(Checkbox)).every((b) => b.value == true), isTrue);
    expect(start().onPressed, isNotNull);
    await tester.tap(find.text('모두 동의해요'));
    await tester.pump();
    expect(tester.widgetList<Checkbox>(find.byType(Checkbox)).every((b) => b.value == false), isTrue);
    expect(start().onPressed, isNull);
    expect(find.text('동의하지 않고 나가기'), findsOneWidget);
  });

  testWidgets('이메일 회원가입은 필수 동의를 다 체크해야 가입 버튼이 켜진다', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: const LoginPage(loadProviders: _noProviders, loadConsentItems: _fakeItems, loadBusiness: _noBusiness),
    ));
    await tester.ensureVisible(find.text('이메일로 계속하기'));
    await tester.tap(find.text('이메일로 계속하기'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('회원가입'));
    await tester.pumpAndSettle();
    FilledButton signup() => tester.widget<FilledButton>(find.widgetWithText(FilledButton, '가입하고 시작하기'));
    expect(signup().onPressed, isNull);
    await tester.ensureVisible(find.text('모두 동의해요'));
    await tester.tap(find.text('모두 동의해요'));
    await tester.pump();
    expect(signup().onPressed, isNotNull);
  });
}
