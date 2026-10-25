import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:talkto_fe/features/personas/widgets/answers_form.dart';
import 'package:talkto_fe/shared/widgets/ui.dart';

/// 서버 /intake/meta 의 가족 표 문항(M2-01)과 같은 모양.
const _family = {
  'id': 'M2-01',
  'section': '가족',
  'type': 'family',
  'required': false,
  'max': 10,
  'title': '그분이 알아야 할 가족을 알려 주세요',
  'privacy': '살아 계신 가족의 주소·직장·학교·건강 같은 사생활은 적지 말아 주세요.',
  'relationOptions': ['남편', '아들', '딸'],
  'toMeOptions': ['아빠', '고모'],
};
const _self = {
  'id': 'M2-01-self',
  'section': '가족',
  'type': 'text',
  'when': 'notSelf',
  'title': '나와 이야기하실 때 그분은 자기를 뭐라고 하셨어요?',
};

Widget _form(Map<String, dynamic> answers, {Map<String, dynamic>? basics}) => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: AnswersForm(
            questions: const [_family, _self],
            answers: answers,
            keys: const {},
            onChanged: () {},
            basics: basics ?? const {'relation': '할머니', 'myName': '홍길동', 'myRelation': '손자'},
          ),
        ),
      ),
    );

void main() {
  testWidgets('가족 표: 한 사람을 더하고 고친 대로 답에 남고, 빼면 지워진다', (tester) async {
    tester.view.physicalSize = const Size(375, 812);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final answers = <String, dynamic>{};
    await tester.pumpWidget(_form(answers));
    // 맨 위의 '나' 줄(기본 정보에서 온다). 가족과 함께 쓰면 대화하는 사람이 여럿이라 '만든 사람(나)'로 적는다
    expect(find.text(keepAll('나 · 홍길동')), findsOneWidget);
    expect(find.text(keepAll('그분의 손자 · 이 대화 상대를 만든 사람(나) · 기본 정보에서 고쳐요')), findsOneWidget);
    expect(find.textContaining('지금 대화하는 사람'), findsNothing);

    await tester.tap(find.text('가족 더하기'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, '예: 홍철수'), '홍철수');
    await tester.tap(find.text(keepAll('아들')));
    await tester.tap(find.text(keepAll('아빠')));
    await tester.pump();
    await tester.ensureVisible(find.text('더하기').last);
    await tester.tap(find.text('더하기').last);
    await tester.pumpAndSettle();
    expect(answers['M2-01'], [
      {'name': '홍철수', 'relation': '아들', 'toMe': '아빠', 'status': 'alive'},
    ]);
    expect(find.text(keepAll('홍철수 · 아들')), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(find.byTooltip('빼기'));
    await tester.pump();
    expect(answers.containsKey('M2-01'), isFalse);
  });

  testWidgets('나 자신 대화 상대는 \'나에게는\' 칸과 나와 이야기하실 때 문항이 없다', (tester) async {
    await tester.pumpWidget(_form({}, basics: const {'relation': '나 자신', 'myName': '홍길동'}));
    expect(find.text(keepAll('나와 이야기하실 때 그분은 자기를 뭐라고 하셨어요?')), findsNothing);
    await tester.tap(find.text('가족 더하기'));
    await tester.pumpAndSettle();
    expect(find.text('나에게는 (내가 부르는 말)'), findsNothing);
    expect(find.text('나와의 관계'), findsOneWidget);
  });
}
