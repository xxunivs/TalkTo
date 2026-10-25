import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/personas/data/persona_api.dart';
import 'package:talkto_fe/features/personas/persona_edit_page.dart';
import 'package:talkto_fe/features/personas/steps/questions_step.dart';
import 'package:talkto_fe/features/personas/widgets/answers_form.dart';
import 'package:talkto_fe/shared/widgets/ui.dart' show SectionLabel;
import 'package:talkto_fe/shared/widgets/voice_input.dart';

// 2026-10-21 개편 §1: 만들 때 질문 화면이 서버가 대화 상대마다 정한 필수(requiredQuestions)·문구
// (title·titleLiving·titleSelf)를 그대로 쓰는지, 서버 코드(talkto_api_server app/services/intake.py)에서
// 뽑은 실제 모양(test/fixtures/intake_be_payloads.json)으로 본다. 예전 대화 상대(v1)·개편 뒤 가족(v2)·나 자신.
// 자료의 상세 questions 는 meta.questions 에 questionsDiff(서버 intake.questions_for 가 바꾼 칸)를 덮은 것이고,
// complete 는 서버 validate_answers(final=True)를 통과한 답이다. 서버 문항이 바뀌면 자료를 다시 뽑는다.
// 시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다).

final Map<String, dynamic> _fixture =
    jsonDecode(File('test/fixtures/intake_be_payloads.json').readAsStringSync()) as Map<String, dynamic>;

/// 고쳐도 자료가 바뀌지 않게 늘 새로 만든다.
Map<String, dynamic> _copy(Object? value) => jsonDecode(jsonEncode(value)) as Map<String, dynamic>;

/// GET /intake/meta
Map<String, dynamic> _meta() => _copy(_fixture['meta']);

List<Map<String, dynamic>> _sections() => [for (final s in _meta()['sections'] as List) Map<String, dynamic>.from(s)];

/// 서버에서 뽑은 대화 상대 하나.
class _Case {
  final String name;

  _Case(this.name);

  Map<String, dynamic> get _raw => (_fixture['cases'] as Map)[name] as Map<String, dynamic>;

  Map<String, dynamic> get basics => _copy(_raw['basics']);

  /// 상세의 requiredQuestions(서버 intake.required_ids, 질문 차례대로)
  List<String> get required => [for (final id in _raw['requiredQuestions'] as List) '$id'];

  /// 상세의 questions(서버 intake.questions_for)
  List<Map<String, dynamic>> get questions {
    final meta = _meta()['questions'] as List;
    final diff = _raw['questionsDiff'] as List;
    expect(diff.length, meta.length);
    return [
      for (var i = 0; i < meta.length; i++)
        {...Map<String, dynamic>.from(meta[i] as Map), ...Map<String, dynamic>.from(diff[i] as Map)},
    ];
  }

  /// 상세의 answers(서버 intake.with_defaults — 미리 채운 조심할 이야기)
  Map<String, dynamic> get prefilled => _copy(_raw['answers']);

  /// 서버가 꼭 받는 답을 다 채운 것(서버 검사 통과)
  Map<String, dynamic> get complete => _copy(_raw['complete']);

  bool get self => basics['relation'] == '나 자신';

  /// GET /personas/p1(만든 분). [withQuestions] 가 false 면 questions·sections 를 주지 않는 서버.
  Map<String, dynamic> detail({String step = 'READY', Map<String, dynamic>? answers, bool withQuestions = true}) {
    final b = basics;
    return {
      'id': 'p1',
      'role': 'owner',
      'step': step,
      'origin': null,
      'addressTerm': b['addressTerm'],
      'relation': b['relation'],
      'lifeStatus': b['status'] == 'deceased' ? 'DECEASED' : 'LIVING',
      'pledgeDoc': b['status'] == 'deceased' ? 'pledge_deceased' : (b['status'] == 'unable' ? 'pledge_unable' : 'pledge_living'),
      'basics': b,
      'consents': <String, dynamic>{},
      'answers': answers ?? prefilled,
      'jobs': <String, dynamic>{},
      'subjectVoices': <String>[],
      'requiredQuestions': required,
      if (withQuestions) 'questions': questions,
      if (withQuestions) 'sections': _sections(),
    };
  }
}

const _caseNames = ['v1Deceased', 'v1Alive', 'v2Deceased', 'v2DeceasedCalledMe', 'v2Alive', 'v2Unable', 'self'];

final _cases = [for (final name in _caseNames) _Case(name)];

_Case _case(String name) => _cases.firstWhere((c) => c.name == name);

/// 화면이 이 대화 상대에게 묻는(보이는) 필수 질문(차례대로).
List<String> _shownRequired(List<Map<String, dynamic>> questions, Map<String, dynamic> basics) => [
      for (final q in questions)
        if (q['required'] == true && AnswersForm.asks(q, basics)) q['id'] as String,
    ];

/// 보이는 필수가 서버 requiredQuestions 와 같다. 개편 뒤 가족은 차례까지(PART 0 차례), 예전 넷은 서버가
/// LEGACY_REQUIRED 차례로 주니 같은 질문들인지만 본다.
void _expectRequired(List<Map<String, dynamic>> questions, _Case c) {
  final shown = _shownRequired(questions, c.basics);
  if (c.basics['intakeVersion'] == 2 && !c.self) {
    expect(shown, c.required, reason: c.name);
  } else {
    expect(shown, unorderedEquals(c.required), reason: c.name);
  }
}

Map<String, dynamic> _byId(List<Map<String, dynamic>> questions, String id) => questions.firstWhere((q) => q['id'] == id);

class _Fail {
  final int status;
  final Map<String, dynamic> body;

  const _Fail(this.status, this.body);
}

/// 서버 대신 답한다. 받은 요청과 몸통을 적어 둔다. 정해 두지 않은 주소는 404.
class _Server implements HttpClientAdapter {
  final Map<String, Object? Function()> routes;
  final log = <String>[];
  final bodies = <String, List<String>>{};

  _Server(this.routes);

  int count(String key) => log.where((k) => k == key).length;

  Map<String, dynamic> body(String key) => jsonDecode(bodies[key]!.last) as Map<String, dynamic>;

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    final bytes = <int>[];
    if (requestStream != null) {
      await for (final chunk in requestStream) {
        bytes.addAll(chunk);
      }
    }
    final key = '${options.method} ${options.path}';
    log.add(key);
    bodies.putIfAbsent(key, () => []).add(utf8.decode(bytes, allowMalformed: true));
    final handler = routes[key];
    final result = handler == null ? const _Fail(404, {'detail': '찾지 못했어요.'}) : handler();
    if (result is _Fail) return _json(result.status, result.body);
    return _json(200, {'data': result});
  }

  @override
  void close({bool force = false}) {}
}

ResponseBody _json(int status, Object body) => ResponseBody.fromString(
      jsonEncode(body),
      status,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );

_Server _server(Map<String, dynamic> detail) => _Server({
      'GET /intake/meta': _meta,
      'GET /personas/p1': () => detail,
      'GET /personas/p1/phrase-suggestions': () => <String>[],
      for (final doc in ['pledge_deceased', 'pledge_living', 'pledge_unable'])
        'GET /legal/$doc': () => {'title': '만들기 전 확인', 'body': '확인 본문'},
      'PUT /personas/p1': () => {'updating': true},
      'PUT /personas/p1/answers': () => null,
      // 돌아가신 분: 앞서 적어 둔 신청 자격·유족 명단. 만들기 전에 다시 남긴다
      'GET /personas/p1/bereaved': () => {'applicantRelation': 'child', 'members': <Object>[], 'noneLeft': true},
      'PUT /personas/p1/bereaved': () => null,
      'POST /personas/p1/build': () => null,
    });

String _plain(Widget w) {
  if (w is Text) return (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', '');
  if (w is RichText) return w.text.toPlainText().replaceAll('⁠', '');
  return '';
}

Finder _has(String part) => find.byWidgetPredicate((w) => w is Text && _plain(w).contains(part));

Finder _snack(String part) => find.descendant(of: find.byType(SnackBar), matching: _has(part));

/// 질문 제목 옆 표시('필수'·'선택'). 제목이 [title] 인 줄에서 찾는다. 없으면 null.
String? _badgeOf(WidgetTester tester, String title) {
  final line = find.byWidgetPredicate((w) => w is RichText && _plain(w).startsWith(title));
  if (line.evaluate().isEmpty) return null;
  for (final label in ['필수', '선택']) {
    if (find.descendant(of: line.first, matching: find.text(label)).evaluate().isNotEmpty) return label;
  }
  return null;
}

/// 묶음 이름 줄(SectionLabel) 가운데 만들 때 질문의 묶음 것만, 화면 차례대로.
List<String> _sectionLabels(WidgetTester tester) {
  final ids = {for (final s in _sections()) s['id'] as String};
  return [
    for (final label in tester.widgetList<SectionLabel>(find.byType(SectionLabel)))
      if (ids.contains(label.text.split(' · ').first)) label.text,
  ];
}

/// 서버 문항으로 본 묶음 이름 줄: 서버 묶음 차례대로, 보이는 질문이 있는 묶음만, 필수가 있으면 몇 개인지.
List<String> _expectedSections(_Case c) {
  final shown = [for (final q in c.questions) if (AnswersForm.asks(q, c.basics)) q];
  return [
    for (final s in _sections())
      if (shown.any((q) => q['section'] == s['id']))
        () {
          final need = shown.where((q) => q['section'] == s['id'] && q['required'] == true).length;
          return need == 0 ? s['id'] as String : '${s['id']} · 필수 $need개';
        }(),
  ];
}

Future<void> _settle(WidgetTester tester, {int frames = 12}) async {
  for (var i = 0; i < frames; i++) {
    await tester.pump(const Duration(milliseconds: 30));
  }
}

Future<void> _tapIn(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder.first);
  await _settle(tester, frames: 4);
  await tester.tap(finder.first);
  await _settle(tester);
}

void _tall(WidgetTester tester) {
  tester.view.physicalSize = const Size(390, 2400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

/// 정보 고치기 화면(그 아래에 대화 화면이 있어 저장하면 돌아간다).
Widget _editApp() => MaterialApp.router(
      theme: buildTheme(),
      routerConfig: GoRouter(
        initialLocation: '/p/p1/edit',
        routes: [
          GoRoute(
            path: '/p/:id',
            builder: (context, state) => const Scaffold(body: Text('대화 화면')),
            routes: [GoRoute(path: 'edit', builder: (context, state) => const PersonaEditPage(personaId: 'p1'))],
          ),
        ],
      ),
    );

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiClient.clearToken();
    // 🎤 는 누르지 않는다(누르면 시험이 알 수 있게 실패한다)
    VoiceRecorder.create = () => throw StateError('이 시험은 녹음하지 않는다');
    VoiceInput.kakaoInApp = () => false;
  });

  group('서버에서 뽑은 자료', () {
    test('개편 뒤 가족은 PART 0 의 필수(차례대로), 예전 대화 상대·나 자신은 예전 넷', () {
      const partZero = [
        'P1-07A', 'M2-01-level', 'M2-01-self', 'M3-01', 'M3-02', 'C-09', 'C-01', 'C-02', 'C-07', 'C-03', //
        'M4-01', 'M6-08', 'M7-01', 'M9-01-death',
      ];
      const legacy = ['C-01', 'C-02', 'C-05', 'M4-01'];
      expect(_case('v2Deceased').required, partZero);
      // 기본 정보에 그분이 나를 부르던 말이 있으면 P1-07A 는 빠진다
      expect(_case('v2DeceasedCalledMe').required, [...partZero]..remove('P1-07A'));
      // 살아 계신·대화가 어려우신 분에게는 임종 이야기를 묻지 않는다
      expect(_case('v2Alive').required, [...partZero]..remove('M9-01-death'));
      expect(_case('v2Unable').required, [...partZero]..remove('M9-01-death'));
      for (final name in ['v1Deceased', 'v1Alive', 'self']) {
        expect(_case(name).required, legacy, reason: name);
        expect(_case(name).basics.containsKey('intakeVersion'), isFalse, reason: name);
      }
      // 개편 뒤 돌아가신 분만 조심할 이야기(임종)가 미리 채워져 온다
      expect(_case('v2Deceased').prefilled, {'M9-01-death': '물으면만'});
      for (final name in ['v1Deceased', 'v2Alive', 'self']) {
        expect(_case(name).prefilled, isEmpty, reason: name);
      }
      expect(_meta()['legacyRequired'], legacy);
      expect([for (final s in _sections()) s['id']], ['나와 그분', '그분이 하시던 말', '그분이 해 주신 것', '그분은 어떤 분', '조심할 이야기']);
    });
  });

  group('대화 상대마다 다른 질문(서버 상세 그대로)', () {
    test('상세의 questions 를 그대로 쓴다. 화면에 보이는 필수 = 서버 requiredQuestions(같은 차례)', () {
      for (final c in _cases) {
        final merged = PersonaApi.forPersona(_meta(), c.detail());
        expect(merged['questions'], c.questions, reason: c.name);
        expect(merged['sections'], _sections(), reason: c.name);
        final questions = [for (final q in merged['questions'] as List) Map<String, dynamic>.from(q as Map)];
        _expectRequired(questions, c);
        expect(AnswersForm.progress(questions, c.prefilled, c.basics).required, c.required.length, reason: c.name);
      }
    });

    test('질문을 주지 않는 서버(requiredQuestions 만)여도 서버 intake.questions_for 와 같은 필수·문구·보기 글', () {
      for (final c in _cases) {
        final fitted = PersonaApi.forPersona(_meta(), c.detail(withQuestions: false))['questions'] as List;
        final server = c.questions;
        expect(fitted.length, server.length, reason: c.name);
        for (var i = 0; i < server.length; i++) {
          // 미리 채울 답(default)만 서버 상세에만 있다(상세의 answers 에 이미 채워져 온다)
          final want = Map<String, dynamic>.from(server[i])..remove('default');
          expect(fitted[i], want, reason: '${c.name} ${server[i]['id']}');
        }
        final questions = [for (final q in fitted) Map<String, dynamic>.from(q as Map)];
        _expectRequired(questions, c);
      }
    });

    test('문구: 돌아가신 분은 과거형, 살아 계신·대화가 어려우신 분은 titleLiving, 나 자신은 titleSelf(없으면 title)', () {
      String title(String name, String id) => _byId(_case(name).questions, id)['title'] as String;
      for (final name in ['v1Deceased', 'v2Deceased', 'v2DeceasedCalledMe']) {
        expect(title(name, 'C-07'), '그분은 나를 어떻게 챙기셨어요?', reason: name);
        expect(title(name, 'M3-01'), '나를 만나거나 내 전화를 받으시면 첫마디를 뭐라고 하셨어요?', reason: name);
        expect(title(name, 'P1-07A'), '그분은 나를 뭐라고 부르셨어요?', reason: name);
      }
      for (final name in ['v1Alive', 'v2Alive', 'v2Unable']) {
        expect(title(name, 'C-07'), '그분은 나를 어떻게 챙기세요?', reason: name);
        expect(title(name, 'M3-01'), '나를 만나거나 내 전화를 받으시면 첫마디를 뭐라고 하세요?', reason: name);
        expect(title(name, 'P1-07A'), '그분은 나를 뭐라고 부르세요?', reason: name);
      }
      // 나 자신: '나에게' 하던 말·챙김 대신 예전 문구(titleSelf)
      expect(title('self', 'C-07'), '사랑하는 사람에게 마음을 어떻게 표현하셨어요?');
      expect(title('self', 'M3-01'), '전화를 받으면 첫마디를 뭐라고 하셨나요?');
      expect(title('self', 'C-09'), '자주 하시던 말이나 입버릇이 있나요?');
      // 나 자신은 개편 대상이 아니다: titleSelf 가 없는 문항도 현재형(titleLiving)이 아니라 예전 문구(title)
      expect(title('self', 'C-01'), '그분은 한 번 말씀하실 때 보통 얼마나 길게 하셨어요?');
      expect(title('self', 'C-02'), '이야기할 때 주로 어떤 식이셨어요?');
      // 화면은 서버 문구 그대로: 모든 문항이 그 문항의 title·titleLiving·titleSelf 가운데 하나다
      for (final c in _cases) {
        for (final q in c.questions) {
          final m = _byId([for (final q in _meta()['questions'] as List) Map<String, dynamic>.from(q as Map)], q['id']);
          expect([m['title'], m['titleLiving'], m['titleSelf']], contains(q['title']), reason: '${c.name} ${q['id']}');
        }
      }
    });

    test('서버가 받는 답이면 빠진 필수가 없고, 필수 하나를 비우면 그 질문을 짚는다. 표시 값도 답이다', () {
      for (final c in _cases) {
        final questions = c.questions;
        expect(AnswersForm.firstMissing(questions, c.complete, c.basics), isNull, reason: c.name);
        for (final id in c.required) {
          if (c.prefilled.containsKey(id)) continue; // 미리 채운 답은 비워도 서버·화면이 다시 채운다
          expect(
            AnswersForm.firstMissing(questions, c.complete..remove(id), c.basics),
            id,
            reason: '${c.name} $id',
          );
        }
        // 미리 채운 답만 있으면 서버 필수 가운데 첫 빈 질문
        expect(
          AnswersForm.firstMissing(questions, c.prefilled, c.basics),
          c.required.firstWhere((id) => !c.prefilled.containsKey(id)),
          reason: c.name,
        );
      }
      // '기억이 안 나요'·'따로 없었어요'(서버 skipOption 값)를 고른 것도 답이다
      final v2 = _case('v2Deceased').complete;
      expect(v2['M3-01'], '__unknown__');
      expect(v2['M2-01-self'], '__none__');
    });
  });

  group('만들기 화면(질문 단계)', () {
    // 예전 '대화가 어려우신 분'(v2Unable)은 새로 만들 수 없어(서버가 만들기를 막는다) 고치기에서만 본다
    for (final name in ['v2Deceased', 'v2DeceasedCalledMe', 'v2Alive', 'v1Deceased', 'self']) {
      testWidgets('$name: 서버 필수에만 필수 표시, 묶음은 서버 차례, 다 답해야 만든다', (tester) async {
        final c = _case(name);
        final server = _server(const {});
        ApiClient.useAdapterForTests(server);
        _tall(tester);
        await tester.pumpWidget(MaterialApp(
          theme: buildTheme(),
          home: QuestionsStep(detail: c.detail(step: 'QUESTIONS'), onChanged: () {}),
        ));
        await _settle(tester);

        // 필수 표시는 서버가 정한 필수에만(보이는 질문만 센다)
        expect(find.text('필수'), findsNWidgets(c.required.length));
        for (final id in c.required) {
          expect(_badgeOf(tester, _byId(c.questions, id)['title'] as String), '필수', reason: id);
        }
        expect(_has('필수 ${c.prefilled.length} / ${c.required.length}'), findsOneWidget);
        // 묶음은 서버 차례(나와 그분 → … → 조심할 이야기), 필수 수도 서버 문항으로 센 것
        expect(_sectionLabels(tester), _expectedSections(c));
        for (final s in _sections()) {
          if (_expectedSections(c).any((label) => label.split(' · ').first == s['id'])) {
            expect(_has(s['lead'] as String), findsOneWidget, reason: s['id'] as String);
          }
        }
        // 묻지 않는 질문은 그리지 않는다(나 자신에게 '나와 그분', 기본 정보로 답한 부르던 말, 살아 계신 분의 임종)
        for (final q in c.questions) {
          if (!AnswersForm.asks(q, c.basics)) {
            expect(_badgeOf(tester, q['title'] as String), isNull, reason: '${q['id']}');
          }
        }

        await _tapIn(tester, _has('위 내용을 모두 확인했어요'));
        await _tapIn(tester, find.text('대화 상대 만들기'));
        expect(_snack('아직 답하지 않은 필수 질문이 있어요.'), findsOneWidget);
        expect(server.count('POST /personas/p1/build'), 0);
      });
    }

    testWidgets('서버가 받는 답이면 그대로 만든다(개편 뒤 가족·예전 대화 상대·나 자신)', (tester) async {
      for (final name in ['v2Deceased', 'v2Alive', 'v1Deceased', 'self']) {
        final c = _case(name);
        final server = _server(const {});
        ApiClient.useAdapterForTests(server);
        _tall(tester);
        await tester.pumpWidget(MaterialApp(
          theme: buildTheme(),
          home: QuestionsStep(key: ValueKey(name), detail: c.detail(step: 'QUESTIONS', answers: c.complete), onChanged: () {}),
        ));
        await _settle(tester);
        expect(_has('필수 질문은 다 답했어요'), findsOneWidget, reason: name);
        await _tapIn(tester, _has('위 내용을 모두 확인했어요'));
        await _tapIn(tester, find.text('대화 상대 만들기'));
        expect(_snack('아직 답하지 않은 필수 질문이 있어요.'), findsNothing, reason: name);
        expect(server.count('POST /personas/p1/build'), 1, reason: name);
        expect(server.body('POST /personas/p1/build')['answers'], c.complete, reason: name);
        // 돌아가신 분만 신청 자격·유족 명단을 먼저 남긴다
        expect(server.count('PUT /personas/p1/bereaved'), c.basics['status'] == 'deceased' ? 1 : 0, reason: name);
        await tester.pumpWidget(const SizedBox());
        await tester.pump(const Duration(seconds: 5));
      }
    });
  });

  group('정보 고치기', () {
    for (final name in ['v1Deceased', 'v2DeceasedCalledMe', 'v2Alive', 'v2Unable', 'self']) {
      testWidgets('$name: 서버 필수만 막고, 서버가 받는 답이면 저장한다', (tester) async {
        final c = _case(name);
        final server = _server(c.detail(answers: c.complete));
        ApiClient.useAdapterForTests(server);
        _tall(tester);
        await tester.pumpWidget(_editApp());
        await _settle(tester);
        expect(find.text('필수'), findsNWidgets(c.required.length));
        expect(_sectionLabels(tester), _expectedSections(c));

        await _tapIn(tester, find.text('저장하기'));
        expect(_snack('아직 답하지 않은 필수 질문이 있어요.'), findsNothing);
        expect(server.count('PUT /personas/p1'), 1);
        final sent = server.body('PUT /personas/p1');
        expect(sent['answers'], c.complete);
        expect(find.text('대화 화면'), findsOneWidget);
      });
    }
  });
}
