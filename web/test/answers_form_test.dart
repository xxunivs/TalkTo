import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/personas/data/persona_api.dart';
import 'package:talkto_fe/features/personas/widgets/answers_form.dart';
import 'package:talkto_fe/shared/widgets/ui.dart';

/// 서버 /intake/meta 의 '조심할 이야기' 줄(M9-01)과 같은 모양.
const _grades = ['자유', '물으면만', '짧게 넘김', '금지'];
const _gradeLabels = {'자유': '편하게 이야기해요', '물으면만': '물어볼 때만', '짧게 넘김': '짧게 넘겨요', '금지': '이야기하지 않아요'};
const _health = {
  'id': 'M9-01-health',
  'section': '조심할 이야기',
  'type': 'single',
  'required': false,
  'compact': true,
  'title': '그분의 건강·앓으신 병',
  'options': _grades,
  'labels': _gradeLabels,
};
const _death = {
  'id': 'M9-01-death',
  'section': '조심할 이야기',
  'type': 'single',
  'required': false,
  'compact': true,
  'when': 'deceased',
  'title': '그분의 사망·장례·임종',
  'options': _grades,
  'labels': _gradeLabels,
};
const _plain = {
  'id': 'C-03',
  'section': '성격',
  'type': 'single',
  'required': false,
  'title': '상대가 힘들어하면 그분은 어떻게 하셨어요?',
  'options': ['같이 울고 웃는다', '말없이 곁에 있다'],
};

/// Text.rich 까지 포함해 보이는 글(이음표 뺀)로 찾는다.
Finder _shown(String text) => find.byWidgetPredicate(
      (w) => w is Text && (w.data ?? w.textSpan?.toPlainText())?.replaceAll('⁠', '') == text,
    );

Widget _form(
  List<Map<String, dynamic>> questions,
  Map<String, dynamic> answers, {
  Map<String, dynamic>? basics,
  List<String> suggestions = const [],
  Map<String, String> leads = const {},
}) =>
    MaterialApp(
      theme: buildTheme(),
      home: Scaffold(
        body: SingleChildScrollView(
          child: AnswersForm(
            questions: questions,
            answers: answers,
            keys: const {},
            onChanged: () {},
            basics: basics,
            suggestions: suggestions,
            sectionLeads: leads,
          ),
        ),
      ),
    );

void main() {
  testWidgets('돌아가신 분만 묻는 줄(when=deceased)은 살아 계신 분·나 자신에게 보이지 않는다', (tester) async {
    await tester.pumpWidget(_form(const [_health, _death], {}, basics: const {'relation': '할머니', 'status': 'deceased'}));
    expect(_shown('그분의 사망·장례·임종'), findsOneWidget);

    for (final basics in const [
      {'relation': '어머니', 'status': 'alive'},
      // 예전에 만든 '대화가 어려우신 분'(지금은 새로 고를 수 없지만 고치기·더 채우기에는 남아 있다)
      {'relation': '아버지', 'status': 'unable'},
      {'relation': '나 자신', 'status': 'alive'},
    ]) {
      await tester.pumpWidget(_form(const [_health, _death], {}, basics: basics));
      expect(_shown('그분의 건강·앓으신 병'), findsOneWidget);
      expect(_shown('그분의 사망·장례·임종'), findsNothing);
    }
    // 기본 정보가 없으면(기억 더 채우기는 미리 거른다) 다 보인다
    expect(AnswersForm.asks(_death, null), isTrue);
    expect(AnswersForm.asks(const {'when': 'notSelf'}, const {'relation': '나 자신'}), isFalse);
  });

  testWidgets('촘촘한 줄(compact): 제목이 작고, 쉬운 말 칩을 누르면 원래 등급 값으로 저장한다', (tester) async {
    final answers = <String, dynamic>{};
    await tester.pumpWidget(_form(const [_health, _plain], answers, basics: const {'relation': '할머니', 'status': 'deceased'}));
    double size(String title) => tester.widget<Text>(_shown(title)).textSpan!.style!.fontSize!;
    expect(size('그분의 건강·앓으신 병'), lessThan(size('상대가 힘들어하면 그분은 어떻게 하셨어요?')));

    await tester.tap(find.text(keepAll('물어볼 때만')));
    await tester.pump();
    expect(answers['M9-01-health'], '물으면만');
    expect(tester.takeException(), isNull);
  });

  testWidgets('녹음에서 찾은 말 후보는 suggest 가 붙은 문항에만 보인다', (tester) async {
    final answers = <String, dynamic>{};
    final questions = [
      {'id': 'C-09', 'section': '말투', 'type': 'phrases', 'suggest': true, 'title': '입버릇', 'placeholder': '예: 밥은'},
      {'id': 'M8-11', 'section': '말투', 'type': 'phrases', 'title': '먼저 묻던 말', 'placeholder': '예: 어디냐'},
    ];
    await tester.pumpWidget(_form(questions, answers, suggestions: const ['아이고야']));
    expect(find.text(keepAll('+ 아이고야')), findsOneWidget);
    expect(find.text('녹음에서 찾은 말 · 누르면 더해져요'), findsOneWidget);

    await tester.tap(find.text(keepAll('+ 아이고야')));
    await tester.pump();
    expect(answers['C-09'], ['아이고야']);
    expect(answers.containsKey('M8-11'), isFalse);
  });

  testWidgets('묶음 제목 아래 서버가 준 한 줄 안내를 보인다', (tester) async {
    final meta = <String, dynamic>{
      'sections': [
        {'id': '성격', 'lead': '가족이 기억하는 그분 모습 그대로 알려 주세요.'},
        {'id': '함께한 기억'},
      ],
    };
    final leads = PersonaApi.sectionLeads(meta);
    expect(leads, {'성격': '가족이 기억하는 그분 모습 그대로 알려 주세요.'});
    expect(PersonaApi.sectionLeads(const {}), isEmpty); // 예전 서버(sections 없음)

    await tester.pumpWidget(_form(const [_plain], {}, leads: leads));
    expect(find.text('성격'), findsOneWidget);
    expect(_shown('가족이 기억하는 그분 모습 그대로 알려 주세요.'), findsOneWidget);
  });

  test('진행 표시는 이 대화 상대에게 묻는 질문만 센다', () {
    final questions = <Map<String, dynamic>>[
      {'id': 'C-01', 'required': true},
      {'id': 'C-05', 'required': true},
      Map<String, dynamic>.from(_health),
      Map<String, dynamic>.from(_death),
      {'id': 'M2-01-self', 'when': 'notSelf'},
    ];
    final answers = <String, dynamic>{'C-01': '한두 문장으로 짧게', 'C-05': '  ', 'M9-01-death': '금지', 'M2-01-self': []};
    final living = AnswersForm.progress(questions, answers, const {'relation': '어머니', 'status': 'alive'});
    expect((living.required, living.requiredDone, living.done, living.total), (2, 1, 1, 4));
    final deceased = AnswersForm.progress(questions, answers, const {'relation': '할머니', 'status': 'deceased'});
    expect((deceased.required, deceased.requiredDone, deceased.done, deceased.total), (2, 1, 2, 5));
  });

  testWidgets('필수와 선택이 섞이면 질문마다 표시를 달고, 묶음 이름에 필수 수를 붙인다', (tester) async {
    const required = {
      'id': 'C-05',
      'section': '성격',
      'type': 'text',
      'required': true,
      'title': '그분을 한 문장으로 말한다면?',
    };
    await tester.pumpWidget(_form(const [required, _plain], {}));
    expect(find.text('필수'), findsOneWidget);
    expect(find.text('선택'), findsOneWidget);
    expect(find.text('성격 · 필수 1개'), findsOneWidget);

    // 모두 선택인 목록(기억 더 채우기)에는 표시를 달지 않는다
    await tester.pumpWidget(_form(const [_plain], {}));
    expect(find.text('선택'), findsNothing);
    expect(find.text('성격'), findsOneWidget);
  });

  test('민감한 범주 질문은 그 범주에 동의했을 때만 묻는다', () {
    const health = {'id': 'T1-02', 'sensitive': 'health'};
    expect(AnswersForm.consented(health, null), isFalse);
    expect(AnswersForm.consented(health, const {'sensitiveHealth': true}), isTrue);
    expect(AnswersForm.consented(const {'id': 'M1-09', 'sensitive': 'religion'}, const {'sensitiveHealth': true}), isFalse);
    expect(AnswersForm.consented(_plain, null), isTrue);
  });
}
