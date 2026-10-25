import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/personas/persona_create_page.dart';
import 'package:talkto_fe/features/personas/widgets/basics_form.dart';
import 'package:talkto_fe/shared/widgets/ui.dart';

import 'legal_fakes.dart';

// 만 19세 미만 대화 상대(2026-10-22 법률 자문): 살아 계신 분이면 만 19세 미만인지 묻고, 그렇다면 태어난 해와 달 ·
// 부모·자녀 관계로 묶기 · 법정대리인 확인 · 다른 쪽 부모님. 만 14세 미만은 아직 받지 않으면 만들 수 없다.
// 예전 '대화가 어려우신 분'은 새로 고를 수 없고 읽기만. 시험 자료는 모두 지어낸 것이다.

const _meta = <String, dynamic>{
  'relations': ['할머니', '어머니', '아들', '딸'],
  'myRelations': ['손녀', '딸', '어머니', '아버지'],
  // 서버는 '대화가 어려우신 분'을 더는 주지 않는다
  'statuses': {'alive': '네, 살아 계세요', 'deceased': '아니요, 돌아가셨어요'},
  'minorRelations': {
    'relation': ['아들', '딸'],
    'myRelation': ['어머니', '아버지'],
  },
  'guardianBases': {'both_agreed': '다른 쪽 부모님의 동의를 받았어요', 'sole_custody': '제가 혼자 친권을 가지고 있어요'},
  'under14Open': false,
  'questions': <Object>[],
};

Widget _form(BasicsDraft draft, {VoidCallback? onChanged, Map<String, dynamic> meta = _meta}) => MaterialApp(
      theme: buildTheme(),
      home: Scaffold(
        body: SingleChildScrollView(child: BasicsForm(draft: draft, meta: meta, onChanged: onChanged)),
      ),
    );

String _ym(int yearsAgo) => '${DateTime.now().year - yearsAgo}-01';

void main() {
  setUp(() {
    // 법정대리인 확인 글(GET /legal)은 서버 글을 쓴다
    ApiClient.useAdapterForTests(FakeServer({
      'GET /legal': () => {
            'guardian': {'attest': '저는 이 아이의 법정대리인(부모)이에요. (서버 글)'},
          },
    }));
  });

  testWidgets('살아 계신 분이면 만 19세 미만인지 묻고, 네면 관계를 부모·자녀로 묶고 법정대리인 확인을 받는다', (tester) async {
    phone(tester, height: 2400);
    final draft = BasicsDraft();
    await tester.pumpWidget(_form(draft));
    await tapIn(tester, shown('할머니'));
    expect(has('그분은 만 19세 미만인가요?'), findsNothing);
    await tapIn(tester, shown('네, 살아 계세요'));
    expect(has('그분은 지금 살아 계신가요?'), findsOneWidget);
    expect(has("'그분 동의 녹음'을 해 주세요"), findsOneWidget);
    expect(has('그분은 만 19세 미만인가요?'), findsOneWidget);
    expect(draft.toJson().containsKey('minor'), isFalse);

    await tapIn(tester, shown('아니요'));
    expect(draft.toJson()['minor'], isFalse);
    expect(draft.toJson().containsKey('birthYm'), isFalse);

    await tapIn(tester, shown('네'));
    // 앞서 고른 할머니는 아이의 관계가 아니라 비운다. 보이는 관계는 아들·딸, 나는 어머니·아버지
    expect(draft.relation, isNull);
    expect(shown('할머니'), findsNothing);
    expect(shown('아들'), findsOneWidget);
    expect(shown('손녀'), findsNothing);
    expect(shown('아버지'), findsOneWidget);
    expect(has('아이의 대화 상대는 그 아이의 부모(법정대리인)만 만들 수 있어요.'), findsOneWidget);
    expect(has('(서버 글)'), findsOneWidget);
    expect(has('다른 쪽 부모님의 동의를 받았어요'), findsOneWidget);

    await tapIn(tester, shown('딸'));
    await tapIn(tester, shown('어머니'));
    await tapIn(tester, has('(서버 글)'));
    await tapIn(tester, has('제가 혼자 친권을 가지고 있어요'));
    draft.birthYm = _ym(16);
    expect(draft.createBlock(_meta), isNull);
    expect(draft.toJson(), containsPair('minor', true));
    expect(draft.toJson(), containsPair('birthYm', _ym(16)));
    expect(draft.toJson(), containsPair('guardianBasis', 'sole_custody'));
    expect(draft.toJson(), containsPair('guardianAttest', true));
    expect(draft.toJson(), containsPair('relation', '딸'));
    expect(draft.toJson(), containsPair('myRelation', '어머니'));
    draft.dispose();
  });

  testWidgets('태어난 해와 달을 고르고, 만 14세 미만인데 아직 받지 않으면 안내하고 만들기 단추를 막는다', (tester) async {
    phone(tester, height: 2400);
    ApiClient.useAdapterForTests(FakeServer({
      'GET /intake/meta': () => _meta,
      'GET /billing/me': () => {'personas': <Object>[]},
      'GET /legal': () => {'guardian': <String, dynamic>{}},
    }));
    await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const PersonaCreatePage()));
    await settle(tester);
    await tapIn(tester, shown('딸'));
    await tapIn(tester, shown('네, 살아 계세요'));
    await tapIn(tester, shown('네'));
    // 서버 글을 읽지 못하면 같은 뜻의 기본 글
    expect(has('저는 이 아이의 법정대리인(부모)이에요.'), findsOneWidget);

    final year = DateTime.now().year - 10;
    await tapIn(tester, find.byKey(const ValueKey('birth-year')));
    await tester.tap(find.text('$year년').last);
    await settle(tester);
    await tapIn(tester, find.byKey(const ValueKey('birth-month')));
    await tester.tap(find.text('1월').last);
    await settle(tester);
    expect(has(under14ClosedText), findsOneWidget);
    final next = tester.widget<FilledButton>(find.widgetWithText(FilledButton, '다음: 녹음 올리기'));
    expect(next.onPressed, isNull);

    // 만 14세가 넘으면 다시 만들 수 있다
    await tapIn(tester, find.byKey(const ValueKey('birth-year')));
    await tester.tap(find.text('${DateTime.now().year - 17}년').last);
    await settle(tester);
    expect(has(under14ClosedText), findsNothing);
    expect(tester.widget<FilledButton>(find.widgetWithText(FilledButton, '다음: 녹음 올리기')).onPressed, isNotNull);
  });

  testWidgets('돌아가신 분·나 자신은 만 19세 미만인지 묻지 않는다', (tester) async {
    phone(tester, height: 2400);
    final draft = BasicsDraft();
    await tester.pumpWidget(_form(draft));
    await tapIn(tester, shown('할머니'));
    await tapIn(tester, shown('아니요, 돌아가셨어요'));
    expect(has('그분은 만 19세 미만인가요?'), findsNothing);
    expect(draft.toJson().containsKey('minor'), isFalse);
    draft.dispose();
  });

  testWidgets('이미 답한 아이의 대화 상대는 만 19세 미만·태어난 달을 읽기만, 다른 쪽 부모님만 고친다', (tester) async {
    phone(tester, height: 2400);
    final draft = BasicsDraft(
      basics: {
        'name': '김지우',
        'relation': '딸',
        'addressTerm': '지우',
        'status': 'alive',
        'myName': '김하늘',
        'myRelation': '어머니',
        'calledMe': '엄마',
        'minor': true,
        'birthYm': '2012-03',
        'guardianBasis': 'both_agreed',
      },
      relations: const ['딸'],
    );
    await tester.pumpWidget(_form(draft));
    expect(has('만 19세 미만 · 2012년 3월생'), findsOneWidget);
    expect(has('그분은 만 19세 미만인가요?'), findsNothing);
    await tapIn(tester, has('제가 혼자 친권을 가지고 있어요'));
    final json = draft.toJson();
    expect(json['minor'], isTrue);
    expect(json['birthYm'], '2012-03');
    expect(json['guardianBasis'], 'sole_custody');
    // 법정대리인 확인은 만들 때만 보낸다
    expect(json.containsKey('guardianAttest'), isFalse);
    draft.dispose();
  });

  testWidgets('예전에 만들어 아직 답하지 않은 살아 계신 분은 고칠 때 묻지 않는다(동의 녹음을 시작할 때 따로 묻는다)', (tester) async {
    phone(tester, height: 2400);
    final draft = BasicsDraft(
      basics: {
        'name': '김영자',
        'relation': '할머니',
        'addressTerm': '할머니',
        'status': 'alive',
        'myName': '김하늘',
        'myRelation': '손녀',
        'calledMe': '하늘아',
      },
      relations: const ['할머니'],
    );
    await tester.pumpWidget(_form(draft));
    expect(has("'그분 동의 녹음'을 해 주세요"), findsOneWidget);
    expect(has('그분은 만 19세 미만인가요?'), findsNothing);
    final json = draft.toJson();
    expect(json.containsKey('minor'), isFalse);
    expect(json.containsKey('birthYm'), isFalse);
    expect(json.containsKey('guardianAttest'), isFalse);
    draft.dispose();
  });

  testWidgets("예전 '대화가 어려우신 분'은 상태를 고르지 못하고 읽기만 한다", (tester) async {
    phone(tester, height: 2400);
    final draft = BasicsDraft(
      basics: {
        'name': '김영자',
        'relation': '할머니',
        'addressTerm': '할머니',
        'status': 'unable',
        'myName': '김하늘',
        'myRelation': '손녀',
        'calledMe': '하늘아',
      },
      relations: const ['할머니'],
    );
    await tester.pumpWidget(_form(draft));
    expect(has('대화가 어려우신 분(예전에 고른 상태) — 지금은 새 녹음 처리와 목소리 다시 만들기를 멈췄어요.'), findsOneWidget);
    expect(shown('네, 살아 계세요'), findsNothing);
    expect(has('그분은 만 19세 미만인가요?'), findsNothing);
    expect(draft.toJson()['status'], 'unable');
    draft.dispose();
  });

  test('나이는 태어난 달의 다음 달 1일부터 센다(서버 ages.age_on 과 같다)', () {
    expect(minorAge('2012-03', DateTime(2026, 3, 31)), 13);
    expect(minorAge('2012-03', DateTime(2026, 4, 1)), 14);
    expect(minorAge('2012-12', DateTime(2026, 12, 31)), 13);
    expect(minorAge('2012-12', DateTime(2027, 1, 1)), 14);
    expect(minorAge('2012-13'), isNull);
    expect(minorAge(null), isNull);
    expect(keepAll('네'), '네');
  });
}
