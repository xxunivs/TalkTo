import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/chat/persona_chat_page.dart';
import 'package:talkto_fe/features/personas/data/persona_api.dart';
import 'package:talkto_fe/features/personas/persona_edit_page.dart';
import 'package:talkto_fe/features/personas/persona_more_page.dart';
import 'package:talkto_fe/features/personas/steps/questions_step.dart';
import 'package:talkto_fe/features/personas/widgets/answers_form.dart';
import 'package:talkto_fe/features/personas/widgets/basics_form.dart';
import 'package:talkto_fe/shared/widgets/ui.dart' show keepAll, Pill;
import 'package:talkto_fe/shared/widgets/voice_input.dart';

// 2026-10-21 개편: 만들 때 필수 질문(기억이 안 나요 칩·M6-08)과 🎤 말로 적기.
// 시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다).

/// 서버가 오류로 답할 때.
class _Fail {
  final int status;
  final Map<String, dynamic> body;

  const _Fail(this.status, this.body);
}

/// 서버 대신 답한다. 'POST /personas/p1/transcriptions' 처럼 메서드와 경로로 고른다.
/// 받은 요청과 몸통(글로 바꾼 것)을 적어 둔다. 정해 두지 않은 주소는 404.
class _Server implements HttpClientAdapter {
  final Map<String, Object? Function()> routes;
  final log = <String>[];
  final bodies = <String, List<String>>{};

  _Server(this.routes);

  int count(String key) => log.where((k) => k == key).length;

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

/// 마이크 대신. 녹음하면 [bytes] 를 준다.
class _FakeRecorder implements VoiceRecorder {
  bool permission = true;
  bool micWorks = true;
  bool recordingNow = false;
  List<int> bytes = [1, 2, 3, 4];
  int started = 0;
  int stopped = 0;
  int cancelled = 0;
  RecordConfig? config;

  @override
  Future<bool> hasPermission() async => permission;

  @override
  Future<bool> isEncoderSupported(AudioEncoder encoder) async => encoder == AudioEncoder.opus;

  @override
  Future<void> start(RecordConfig config, {required String path}) async {
    started++;
    this.config = config;
    recordingNow = micWorks;
  }

  @override
  Future<bool> isRecording() async => recordingNow;

  @override
  Future<String?> stop() async {
    stopped++;
    recordingNow = false;
    return 'blob:fake';
  }

  @override
  Future<List<int>> read(String path) async => bytes;

  @override
  Future<void> cancel() async {
    cancelled++;
    recordingNow = false;
  }

  @override
  Future<void> dispose() async {}
}

String _plain(Widget w) {
  if (w is Text) return (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', '');
  if (w is RichText) return w.text.toPlainText().replaceAll('⁠', '');
  return '';
}

Finder _has(String part) => find.byWidgetPredicate((w) => w is Text && _plain(w).contains(part));

/// 칩(Pill)은 낱말 가운데서 줄이 바뀌지 않게 이음표를 넣어 그린다.
Finder _chip(String label) => find.text(keepAll(label));

Finder _snack(String part) => find.descendant(of: find.byType(SnackBar), matching: _has(part));

Future<void> _settle(WidgetTester tester, {int frames = 12}) async {
  for (var i = 0; i < frames; i++) {
    await tester.pump(const Duration(milliseconds: 30));
  }
}

void _phone(WidgetTester tester, {double height = 1600}) {
  tester.view.physicalSize = Size(390, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

/// 보이는 곳으로 내려 누른다.
Future<void> _tapIn(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder.first);
  await _settle(tester, frames: 4);
  await tester.tap(finder.first);
  await _settle(tester);
}

/// 칩 하나(골라졌는지 본다).
Pill _pill(WidgetTester tester, String label) =>
    tester.widget<Pill>(find.ancestor(of: _chip(label), matching: find.byType(Pill)).first);

// ── 서버 문항(대화 상대 상세의 questions 와 같은 모양) ──

const _unknown = {'value': '__unknown__', 'label': '기억이 안 나요'};
const _none = {'value': '__none__', 'label': '따로 없었어요'};

const _calledMe = {
  'id': 'P1-07A',
  'section': '나와 그분',
  'type': 'text',
  'required': true,
  'maxLength': 15,
  'title': '그분은 나를 뭐라고 부르셨어요?',
};
const _selfWord = {
  'id': 'M2-01-self',
  'section': '나와 그분',
  'type': 'text',
  'required': true,
  'when': 'notSelf',
  'maxLength': 15,
  'title': '나와 이야기하실 때 자기를 뭐라고 하셨어요?',
  'placeholder': '예: 할머니가',
  'skipOption': _none,
};
const _firstWord = {
  'id': 'M3-01',
  'section': '그분이 하시던 말',
  'type': 'text',
  'required': true,
  'maxLength': 30,
  'title': '나를 만나거나 내 전화를 받으시면 첫마디는요?',
  'placeholder': '예: 우리 강아지 왔나',
  'skipOption': _unknown,
};
const _thing = {
  'id': 'M6-08',
  'section': '그분은 어떤 분',
  'type': 'text',
  'required': true,
  'maxLength': 20,
  'title': '그분 하면 떠오르는 것 하나 (음식·물건·장소·냄새)',
  'placeholder': '예: 된장찌개',
};
const _thingWhy = {
  'id': 'M6-08-why',
  'section': '그분은 어떤 분',
  'type': 'text',
  'required': false,
  'maxLength': 60,
  'title': '왜 떠오르나요? (선택)',
  'placeholder': '예: 갈 때마다 끓여 주셨어요',
};
const _optional = {
  'id': 'C-05',
  'section': '그분은 어떤 분',
  'type': 'text',
  'required': false,
  'maxLength': 60,
  'title': '그분을 한 문장으로 표현한다면?',
};

const _family = {'relation': '할머니', 'status': 'deceased', 'calledMe': ''};

// ── 서버가 실제로 주는 모양(talkto_api_server intake.py) ──
//
// /intake/meta 의 questions 는 intake.QUESTIONS 그대로다: required 는 개편 뒤 가족·고인(intakeVersion 2)
// 기준이고, title 은 돌아가신 분 문구에 titleLiving·titleSelf 갈래가 붙어 있다. 대화 상대마다 무엇이
// 필수인지·어떤 문구인지는 상세(GET /personas/:id)의 questions(intake.questions_for)가 정한다.

const _legacyRequired = {'C-01', 'C-02', 'C-05', 'M4-01'};

const _metaQuestions = <Map<String, dynamic>>[
  {
    'id': 'P1-07A',
    'section': '나와 그분',
    'type': 'text',
    'required': true,
    'when': 'notSelf',
    'skipIfBasics': 'calledMe',
    'maxLength': 15,
    'title': '그분은 나를 뭐라고 부르셨어요?',
    'titleLiving': '그분은 나를 뭐라고 부르세요?',
    'placeholder': '예: 도윤아',
  },
  {
    'id': 'M3-01',
    'section': '그분이 하시던 말',
    'type': 'text',
    'required': true,
    'maxLength': 30,
    'title': '나를 만나거나 내 전화를 받으시면 첫마디를 뭐라고 하셨어요?',
    'titleLiving': '나를 만나거나 내 전화를 받으시면 첫마디를 뭐라고 하세요?',
    'titleSelf': '전화를 받으면 첫마디를 뭐라고 하셨나요?',
    'placeholder': '예: 왔나, 밥은?',
    'skipOption': _unknown,
  },
  {
    'id': 'C-01',
    'section': '그분이 하시던 말',
    'type': 'single',
    'required': true,
    'title': '그분은 한 번 말씀하실 때 보통 얼마나 길게 하셨어요?',
    'titleLiving': '그분은 한 번 말씀하실 때 보통 얼마나 길게 하세요?',
    'options': ['한두 문장으로 짧게', '서너 문장 정도'],
  },
  {
    'id': 'C-02',
    'section': '그분이 하시던 말',
    'type': 'single',
    'required': true,
    'title': '이야기할 때 주로 어떤 식이셨어요?',
    'titleLiving': '이야기할 때 주로 어떤 식이세요?',
    'options': ['결론이나 답을 바로 말한다', '되물어 보면서 상대 이야기를 끌어낸다'],
  },
  {
    'id': 'C-07',
    'section': '그분이 해 주신 것',
    'type': 'multi',
    'required': true,
    'max': 3,
    'title': '그분은 나를 어떻게 챙기셨어요?',
    'titleLiving': '그분은 나를 어떻게 챙기세요?',
    'titleSelf': '사랑하는 사람에게 마음을 어떻게 표현하셨어요?',
    'options': ['음식·밥을 챙겨서', '잘 표현하지 않는 편'],
  },
  {
    'id': 'M4-01',
    'section': '그분은 어떤 분',
    'type': 'multi',
    'required': true,
    'max': 5,
    'title': '성격을 가장 잘 나타내는 말을 골라주세요.',
    'options': ['다정함', '말수가 적음'],
  },
  {
    'id': 'M6-08',
    'section': '그분은 어떤 분',
    'type': 'text',
    'required': true,
    'maxLength': 20,
    'title': '그분 하면 떠오르는 것 하나를 알려 주세요.',
    'placeholder': '예: 된장찌개',
  },
  {
    'id': 'M6-08-why',
    'section': '그분은 어떤 분',
    'type': 'text',
    'required': false,
    'maxLength': 60,
    'title': '왜 떠오르나요? (선택)',
    'placeholder': '예: 갈 때마다 끓여 주셨어요',
  },
  {
    'id': 'C-05',
    'section': '그분은 어떤 분',
    'type': 'text',
    'required': false,
    'maxLength': 60,
    'title': '그분을 한 문장으로 표현한다면?',
    'placeholder': '예: 정 많은 분',
  },
  {
    'id': 'M9-01-death',
    'section': '조심할 이야기',
    'type': 'single',
    'required': true,
    'compact': true,
    'when': 'deceased',
    'title': '그분의 사망·장례·임종',
    'options': ['자유', '물으면만', '짧게 넘김', '금지'],
    'labels': {'자유': '편하게 이야기해요', '물으면만': '물어볼 때만', '짧게 넘김': '짧게 넘겨요', '금지': '이야기하지 않아요'},
  },
];

/// GET /intake/meta. PersonaApi.meta() 가 한 번 읽고 기억하니 이 파일의 시험은 모두 이것을 쓴다.
const _meta = <String, dynamic>{
  'questions': _metaQuestions,
  'sections': [
    {'id': '그분이 하시던 말', 'lead': '실제 하시던 말 그대로가 가장 좋아요.'},
  ],
  'moreQuestions': <Object>[],
  'moreModules': <Object>[],
  'relations': ['할머니', '할아버지'],
  'myRelations': ['손녀', '손자'],
  // 서버는 2026-10-22부터 '대화가 어려우신 분'(unable)을 고를 수 있게 주지 않는다(예전 대화 상대만 남아 있다)
  'statuses': {
    'alive': '네, 살아 계세요',
    'deceased': '아니요, 돌아가셨어요',
  },
  'legacyRequired': ['C-01', 'C-02', 'C-05', 'M4-01'],
};

/// 서버 intake.questions_for(basics) 와 같은 것: 상세의 questions.
/// 개편 뒤 가족·고인(intakeVersion 2, 나 자신 아님)은 meta 의 required 그대로, 예전 대화 상대·나 자신은
/// 예전 필수 넷(C-01·C-02·C-05·M4-01)만 필수다. 문구는 서버 intake.question_title 그대로(나 자신은 개편 전
/// 문구 그대로 — titleSelf 가 있으면 그것, 없으면 title. 살아 계신 분은 titleLiving 이 있으면 그것, 그 밖은
/// title). 개편 뒤 대화 상대의 M9-01-death 에는 미리 채울 답이 붙는다. 서버에서 뽑은 실제 모양은
/// intake_be_payload_test.dart 가 본다.
List<Map<String, dynamic>> _questionsFor(Map<String, dynamic> basics) {
  final self = basics['relation'] == '나 자신';
  final v2 = basics['intakeVersion'] == 2 && !self;
  final living = basics['status'] == 'alive' || basics['status'] == 'unable';
  return [
    for (final q in _metaQuestions)
      {
        ...q,
        'required': v2 ? q['required'] == true : _legacyRequired.contains(q['id']),
        'title': self ? q['titleSelf'] ?? q['title'] : (living && q['titleLiving'] != null ? q['titleLiving'] : q['title']),
        if (v2 && q['id'] == 'M9-01-death') 'default': '물으면만',
      },
  ];
}

/// 대화 상대 상세(GET /personas/p1, 만든 분). questions·sections 는 서버가 이 대화 상대에 맞춘 것.
Map<String, dynamic> _detailFor(
  Map<String, dynamic> basics, {
  String step = 'READY',
  Map<String, dynamic> answers = const {},
}) =>
    {
      'id': 'p1',
      'role': 'owner',
      'step': step,
      'origin': null,
      'addressTerm': basics['addressTerm'] ?? '할머니',
      'relation': basics['relation'],
      'lifeStatus': basics['status'] == 'deceased' ? 'DECEASED' : 'LIVING',
      'pledgeDoc': basics['status'] == 'deceased' ? 'pledge_deceased' : 'pledge_living',
      'basics': basics,
      'consents': <String, dynamic>{},
      'answers': answers,
      'jobs': <String, dynamic>{},
      'subjectVoices': <String>[],
      'questions': _questionsFor(basics),
      'sections': _meta['sections'],
    };

/// 예전에 만든(개편 전, intakeVersion 없음) 돌아가신 할머니.
const _v1Basics = <String, dynamic>{
  'name': '김예시',
  'relation': '할머니',
  'addressTerm': '할머니',
  'status': 'deceased',
  'myName': '가은',
  'myRelation': '손녀',
  'calledMe': '',
};

/// 나 자신.
const _selfBasics = <String, dynamic>{
  'name': '김가은',
  'relation': '나 자신',
  'addressTerm': '나',
  'status': 'alive',
  'myName': '김가은',
  'myRelation': null,
  'calledMe': '',
};

/// 예전 필수 넷만 답한 것.
const _legacyAnswers = <String, dynamic>{
  'C-01': '한두 문장으로 짧게',
  'C-02': '결론이나 답을 바로 말한다',
  'C-05': '정 많은 분',
  'M4-01': ['다정함'],
};

/// 질문 제목 옆 표시('필수'·'선택'). 제목이 [title] 을 담은 줄에서 찾는다. 없으면 null.
String? _badgeOf(WidgetTester tester, String title) {
  final line = find.byWidgetPredicate((w) => w is RichText && _plain(w).contains(title));
  if (line.evaluate().isEmpty) return null;
  for (final label in ['필수', '선택']) {
    if (find.descendant(of: line.first, matching: find.text(label)).evaluate().isNotEmpty) return label;
  }
  return null;
}

/// 글 칸(placeholder 로 찾는다).
Finder _input(String placeholder) =>
    find.byWidgetPredicate((w) => w is TextField && w.decoration?.hintText == placeholder);

/// 정보 고치기 화면(그 아래에 대화 화면이 있어 저장하면 돌아간다).
Widget _editApp() => MaterialApp.router(
      theme: buildTheme(),
      routerConfig: GoRouter(
        initialLocation: '/p/p1/edit',
        routes: [
          GoRoute(
            path: '/p/:id',
            builder: (context, state) => const Scaffold(body: Text('대화 화면')),
            routes: [
              GoRoute(path: 'edit', builder: (context, state) => const PersonaEditPage(personaId: 'p1')),
              GoRoute(path: 'more', builder: (context, state) => const PersonaMorePage(personaId: 'p1')),
            ],
          ),
        ],
      ),
    );

/// 정보 고치기·만들기 화면이 부르는 곳(상세는 [detail]).
_Server _personaServer(Map<String, dynamic> detail, {List<String> phrases = const []}) => _Server({
      'GET /intake/meta': () => _meta,
      'GET /personas/p1': () => detail,
      'GET /personas/p1/phrase-suggestions': () => phrases,
      'GET /legal/pledge_deceased': () => {'title': '만들기 전 확인', 'body': '확인 본문'},
      'GET /legal/pledge_living': () => {'title': '만들기 전 확인', 'body': '확인 본문'},
      'PUT /personas/p1': () => {'updating': true},
      'PUT /personas/p1/answers': () => null,
      // 돌아가신 분: 앞서 적어 둔 신청 자격·유족 명단. 만들기 전에 다시 남긴다
      'GET /personas/p1/bereaved': () => {'applicantRelation': 'grandchild', 'members': <Object>[], 'noneLeft': true},
      'PUT /personas/p1/bereaved': () => null,
      'POST /personas/p1/build': () => null,
    });

Widget _form(
  List<Map<String, dynamic>> questions,
  Map<String, dynamic> answers, {
  Map<String, dynamic>? basics = _family,
  String? personaId,
  VoidCallback? onChanged,
}) =>
    MaterialApp(
      theme: buildTheme(),
      home: Scaffold(
        body: SingleChildScrollView(
          child: AnswersForm(
            questions: questions,
            answers: answers,
            keys: const {},
            onChanged: onChanged ?? () {},
            basics: basics,
            personaId: personaId,
          ),
        ),
      ),
    );

TextField _field(WidgetTester tester, int index) => tester.widget<TextField>(find.byType(TextField).at(index));

void main() {
  late _FakeRecorder recorder;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiClient.clearToken();
    recorder = _FakeRecorder();
    VoiceRecorder.create = () => recorder;
    VoiceInput.kakaoInApp = () => false;
  });

  tearDown(() {
    VoiceRecorder.create = () => throw StateError('시험마다 가짜 녹음기를 넣는다');
    VoiceInput.kakaoInApp = () => false;
  });

  group('기억이 안 나요 · 따로 없었어요', () {
    testWidgets('고르면 답은 __unknown__ 이고 적은 글을 비우고 칸을 잠근다. 다시 누르면 거둔다', (tester) async {
      final answers = <String, dynamic>{};
      var changed = 0;
      await tester.pumpWidget(_form(const [_firstWord], answers, onChanged: () => changed++));
      await tester.enterText(find.byType(TextField), '왔나');
      await tester.pump();
      expect(answers['M3-01'], '왔나');

      await tester.tap(_chip('기억이 안 나요'));
      await tester.pump();
      expect(answers['M3-01'], '__unknown__');
      expect(_field(tester, 0).enabled, isFalse);
      expect(_field(tester, 0).controller!.text, isEmpty);
      // 칸에는 '__unknown__' 같은 저장 값이 보이지 않는다
      expect(find.textContaining('__'), findsNothing);
      // 필수 질문이지만 답한 것으로 센다
      expect(AnswersForm.firstMissing(const [_firstWord], answers), isNull);
      expect(AnswersForm.progress(const [_firstWord], answers, _family).requiredDone, 1);

      await tester.tap(_chip('기억이 안 나요'));
      await tester.pump();
      expect(answers.containsKey('M3-01'), isFalse);
      expect(_field(tester, 0).enabled, isTrue);
      expect(AnswersForm.firstMissing(const [_firstWord], answers), 'M3-01');
      expect(changed, greaterThanOrEqualTo(3));
    });

    testWidgets("'따로 없었어요'는 __none__ 으로, 저장해 둔 값은 칩이 골라진 채로 그린다", (tester) async {
      final answers = <String, dynamic>{'M2-01-self': '__none__'};
      await tester.pumpWidget(_form(const [_selfWord], answers));
      expect(_chip('따로 없었어요'), findsOneWidget);
      expect(_field(tester, 0).enabled, isFalse);
      expect(_field(tester, 0).controller!.text, isEmpty);
      expect(AnswersForm.skipOf(_selfWord)?.value, '__none__');
      expect(AnswersForm.skipOf(_thing), isNull);
    });

    testWidgets('칩은 서버가 skipOption 을 준 문항에만 보인다', (tester) async {
      await tester.pumpWidget(_form(const [_thing, _optional], {}));
      expect(_chip('기억이 안 나요'), findsNothing);
      expect(_chip('따로 없었어요'), findsNothing);
    });
  });

  group('M6-08 떠오르는 것 + 이유', () {
    testWidgets('두 칸을 한 덩어리로 그리고, 떠오르는 것만 필수다', (tester) async {
      final answers = <String, dynamic>{};
      await tester.pumpWidget(_form(const [_thing, _thingWhy, _optional], answers));
      expect(_has('그분 하면 떠오르는 것 하나'), findsOneWidget);
      expect(_has('왜 떠오르나요? (선택)'), findsOneWidget);
      // 필수 표시는 떠오르는 것과 C-05(선택)에만. 이유 칸은 제목의 '(선택)'으로 알 수 있어 표시를 따로 달지 않는다
      expect(find.text('필수'), findsOneWidget);
      expect(find.text('선택'), findsOneWidget);

      expect(AnswersForm.firstMissing(const [_thing, _thingWhy], answers), 'M6-08');
      await tester.enterText(find.byType(TextField).at(1), '갈 때마다 끓여 주셨어요');
      await tester.pump();
      expect(AnswersForm.firstMissing(const [_thing, _thingWhy], answers), 'M6-08');
      await tester.enterText(find.byType(TextField).at(0), '된장찌개');
      await tester.pump();
      expect(answers, {'M6-08': '된장찌개', 'M6-08-why': '갈 때마다 끓여 주셨어요'});
      expect(AnswersForm.firstMissing(const [_thing, _thingWhy], answers), isNull);
    });
  });

  group('그분이 나를 부르던 말(P1-07A)', () {
    test('기본 정보에 있으면 묻지 않고 빠진 필수로 치지 않는다. 없으면 묻는다', () {
      const filled = {'relation': '할머니', 'status': 'deceased', 'calledMe': '우리 강아지'};
      expect(AnswersForm.asks(_calledMe, filled), isFalse);
      expect(AnswersForm.firstMissing(const [_calledMe], {}, filled), isNull);
      expect(AnswersForm.progress(const [_calledMe], {}, filled).required, 0);
      expect(AnswersForm.asks(_calledMe, _family), isTrue);
      expect(AnswersForm.firstMissing(const [_calledMe], {}, _family), 'P1-07A');
    });

    test('나 자신에게는 나와 이야기하실 때 문항을 묻지 않아 빠진 필수로 치지 않는다', () {
      const self = {'relation': '나 자신', 'status': 'alive'};
      expect(AnswersForm.firstMissing(const [_selfWord], {}, self), isNull);
      expect(AnswersForm.firstMissing(const [_selfWord], {}, _family), 'M2-01-self');
    });
  });

  group('대화 상대마다 다른 질문(서버 상세의 questions)', () {
    test('상세의 questions 로 그린다. 주지 않는 예전 서버면 /intake/meta 로', () {
      final v1 = PersonaApi.forPersona(_meta, _detailFor(_v1Basics));
      Map q(String id) => (v1['questions'] as List).cast<Map>().firstWhere((q) => q['id'] == id);
      // meta 는 개편 뒤 기준(C-05 선택, M3-01·M6-08 필수)이지만, 예전 대화 상대의 상세는 예전 필수 넷
      expect(q('C-05')['required'], isTrue);
      expect(q('M3-01')['required'], isFalse);
      expect(q('M6-08')['required'], isFalse);
      expect(v1['relations'], _meta['relations']);
      expect(PersonaApi.sectionLeads(v1), {'그분이 하시던 말': '실제 하시던 말 그대로가 가장 좋아요.'});

      final old = PersonaApi.forPersona(_meta, {
        'answers': <String, dynamic>{},
        'moreQuestions': [_optional],
      });
      expect(old['questions'], _metaQuestions);
      expect(old['moreQuestions'], [_optional]);
    });

    test('상세에 questions 가 없는 서버면 requiredQuestions 와 기본 정보로 필수·문구를 맞춘다', () {
      Map<String, dynamic> fitted(Map<String, dynamic> basics, List<String> requiredQuestions) {
        final detail = _detailFor(basics)
          ..remove('questions')
          ..remove('sections')
          ..['requiredQuestions'] = requiredQuestions;
        final questions = PersonaApi.forPersona(_meta, detail)['questions'] as List;
        return {for (final q in questions.cast<Map>()) q['id'] as String: q};
      }

      // 예전 대화 상대: 예전 넷만 필수(C-05 포함), 개편 뒤 새 질문은 선택
      final v1 = fitted(_v1Basics, const ['C-01', 'C-02', 'C-05', 'M4-01']);
      expect(v1['C-05']!['required'], isTrue);
      for (final id in ['P1-07A', 'M3-01', 'C-07', 'M6-08', 'M9-01-death']) {
        expect(v1[id]!['required'], isFalse, reason: id);
      }
      expect(v1['M3-01']!['title'], '나를 만나거나 내 전화를 받으시면 첫마디를 뭐라고 하셨어요?');
      expect(AnswersForm.firstMissing([for (final q in v1.values) Map<String, dynamic>.from(q)], Map.of(_legacyAnswers), _v1Basics), isNull);

      // 개편 뒤 가족: 서버 목록은 기본 정보가 대신한 P1-07A 를 빼고 주지만, 그 칸을 비우면 다시 필수다
      final v2Basics = {..._v1Basics, 'intakeVersion': 2, 'status': 'alive', 'calledMe': '우리 강아지'};
      final v2 = fitted(v2Basics, const ['M3-01', 'C-01', 'C-02', 'C-07', 'M4-01', 'M6-08']);
      expect(v2['P1-07A']!['required'], isTrue);
      expect(v2['C-05']!['required'], isFalse);
      expect(v2['M6-08']!['required'], isTrue);
      // 살아 계신 분은 지금 말 문구
      expect(v2['P1-07A']!['title'], '그분은 나를 뭐라고 부르세요?');
      expect(v2['C-07']!['title'], '그분은 나를 어떻게 챙기세요?');

      // 나 자신: 예전 넷. 문구는 서버 intake.question_title 과 같게 개편 전 문구 그대로 — titleSelf 가
      // 있으면 그것, 없으면 title(늘 살아 계셔도 현재형으로 바꾸지 않는다)
      final self = fitted(_selfBasics, const ['C-01', 'C-02', 'C-05', 'M4-01']);
      expect(self['C-05']!['required'], isTrue);
      expect(self['M3-01']!['required'], isFalse);
      expect(self['M3-01']!['title'], '전화를 받으면 첫마디를 뭐라고 하셨나요?');
      expect(self['C-01']!['title'], '그분은 한 번 말씀하실 때 보통 얼마나 길게 하셨어요?');
      expect(self['P1-07A']!['required'], isFalse);
    });

    testWidgets('상세에 questions 가 없는 서버여도 예전 대화 상대 정보 고치기는 새 질문 없이 저장되고 C-05 를 막는다', (tester) async {
      final detail = _detailFor(_v1Basics, answers: Map.of(_legacyAnswers))
        ..remove('questions')
        ..remove('sections')
        ..['requiredQuestions'] = ['C-01', 'C-02', 'C-05', 'M4-01'];
      final server = _personaServer(detail);
      ApiClient.useAdapterForTests(server);
      _phone(tester, height: 4000);
      await tester.pumpWidget(_editApp());
      await _settle(tester);
      expect(_badgeOf(tester, '그분을 한 문장으로'), '필수');
      expect(_badgeOf(tester, '그분 하면 떠오르는 것'), '선택');

      await tester.enterText(_input('예: 정 많은 분'), '');
      await tester.pump();
      await _tapIn(tester, find.text('저장하기'));
      expect(_snack('아직 답하지 않은 필수 질문이 있어요.'), findsOneWidget);
      expect(server.count('PUT /personas/p1'), 0);

      await tester.enterText(_input('예: 정 많은 분'), '정 많은 분');
      await tester.pump(const Duration(seconds: 5));
      await _settle(tester);
      await _tapIn(tester, find.text('저장하기'));
      expect(server.count('PUT /personas/p1'), 1);
      expect((jsonDecode(server.bodies['PUT /personas/p1']!.single) as Map)['answers'], _legacyAnswers);
    });

    test('그분에 맞춘 필수: 개편 뒤 가족은 PART 0, 예전 대화 상대·나 자신은 예전 넷(C-05 포함)', () {
      Set<String> required(Map<String, dynamic> basics) => {
            for (final q in _questionsFor(basics))
              if (q['required'] == true && AnswersForm.asks(q, basics)) q['id'] as String,
          };
      expect(
        required({..._v1Basics, 'intakeVersion': 2, 'calledMe': '우리 강아지'}),
        {'M3-01', 'C-01', 'C-02', 'C-07', 'M4-01', 'M6-08', 'M9-01-death'},
      );
      expect(required({..._v1Basics, 'intakeVersion': 2}), contains('P1-07A'));
      expect(required(_v1Basics), _legacyRequired);
      expect(required(_selfBasics), _legacyRequired);
      // 예전 대화 상대는 개편 뒤 새 질문(M6-08 등) 없이 예전 넷만 답하면 되고, C-05 가 비면 막는다
      expect(AnswersForm.firstMissing(_questionsFor(_v1Basics), Map.of(_legacyAnswers), _v1Basics), isNull);
      expect(
        AnswersForm.firstMissing(_questionsFor(_v1Basics), Map.of(_legacyAnswers)..remove('C-05'), _v1Basics),
        'C-05',
      );
    });

    testWidgets('만들기(개편 뒤 가족): 필수를 다 답해야 만든다. 기억이 안 나요도 답이고, 미리 채운 조심할 이야기는 확인만', (tester) async {
      final basics = <String, dynamic>{..._v1Basics, 'intakeVersion': 2, 'calledMe': '우리 강아지'};
      final server = _personaServer(const {});
      ApiClient.useAdapterForTests(server);
      _phone(tester, height: 3400);
      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: QuestionsStep(
          // 서버는 미리 채운 답(M9-01 기본 등급)을 상세의 answers 에 실어 준다(intake.with_defaults)
          detail: _detailFor(basics, step: 'QUESTIONS', answers: {'M9-01-death': '물으면만'}),
          onChanged: () {},
        ),
      ));
      await _settle(tester);
      // 기본 정보에 그분이 나를 부르던 말이 있으면 묻지 않는다
      expect(_has('그분은 나를 뭐라고 부르셨어요?'), findsNothing);
      expect(_badgeOf(tester, '첫마디를 뭐라고 하셨어요?'), '필수');
      expect(_badgeOf(tester, '그분 하면 떠오르는 것'), '필수');
      // C-05 는 개편 뒤 선택이지만 문항은 그대로 있다
      expect(_badgeOf(tester, '그분을 한 문장으로'), '선택');
      // 미리 채운 조심할 이야기는 골라진 채다
      expect(_pill(tester, '물어볼 때만').selected, isTrue);
      // 글 칸마다 🎤 (첫마디·떠오르는 것·이유·한 문장)
      expect(find.byTooltip('말로 적기'), findsNWidgets(4));

      await _tapIn(tester, _has('위 내용을 모두 확인했어요'));
      await _tapIn(tester, find.text('대화 상대 만들기'));
      expect(_snack('아직 답하지 않은 필수 질문이 있어요.'), findsOneWidget);
      expect(server.count('POST /personas/p1/build'), 0);

      for (final chip in ['기억이 안 나요', '한두 문장으로 짧게', '결론이나 답을 바로 말한다', '음식·밥을 챙겨서', '다정함']) {
        await _tapIn(tester, _chip(chip));
      }
      await tester.enterText(_input('예: 된장찌개'), '된장찌개');
      // 알림(스낵바)이 아래 단추를 덮고 있으니 사라질 때까지 기다린다
      await tester.pump(const Duration(seconds: 5));
      await _settle(tester);
      await _tapIn(tester, find.text('대화 상대 만들기'));
      expect(server.count('POST /personas/p1/build'), 1);
      // 돌아가신 분은 신청 자격·유족 명단을 먼저 남기고(확인 체크와 함께) 만든다
      expect(server.count('PUT /personas/p1/bereaved'), 1);
      expect(server.log.indexOf('PUT /personas/p1/bereaved'), lessThan(server.log.indexOf('POST /personas/p1/build')));
      final declared = jsonDecode(server.bodies['PUT /personas/p1/bereaved']!.single) as Map;
      expect(declared, {'applicantRelation': 'grandchild', 'members': <Object>[], 'noneLeft': true, 'pledge': true});
      final sent = jsonDecode(server.bodies['POST /personas/p1/build']!.single) as Map;
      expect(sent['answers'], {
        'M9-01-death': '물으면만',
        'M3-01': '__unknown__',
        'C-01': '한두 문장으로 짧게',
        'C-02': '결론이나 답을 바로 말한다',
        'C-07': ['음식·밥을 챙겨서'],
        'M4-01': ['다정함'],
        'M6-08': '된장찌개',
      });
    });

    testWidgets('나 자신: 예전 필수 넷(C-05 포함)만 필수이고 문구는 서버가 맞춘 것(titleSelf). 넷만 답하면 만든다', (tester) async {
      final server = _personaServer(const {});
      ApiClient.useAdapterForTests(server);
      _phone(tester, height: 3400);
      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: QuestionsStep(detail: _detailFor(_selfBasics, step: 'QUESTIONS'), onChanged: () {}),
      ));
      await _settle(tester);
      expect(find.text('필수'), findsNWidgets(4));
      for (final title in ['얼마나 길게', '주로 어떤 식이', '성격을 가장 잘', '그분을 한 문장으로']) {
        expect(_badgeOf(tester, title), '필수', reason: title);
      }
      // 개편 뒤 새 필수(첫마디·챙김·떠오르는 것)는 나 자신에게 선택이고, 문구는 서버가 맞춘 예전 것이다
      expect(_has('전화를 받으면 첫마디를 뭐라고 하셨나요?'), findsOneWidget);
      expect(_has('나를 만나거나'), findsNothing);
      expect(_has('사랑하는 사람에게 마음을 어떻게 표현하셨어요?'), findsOneWidget);
      expect(_has('그분은 나를 어떻게 챙기셨어요?'), findsNothing);
      expect(_badgeOf(tester, '전화를 받으면 첫마디'), '선택');
      expect(_badgeOf(tester, '그분 하면 떠오르는 것'), '선택');
      // 나와 이야기하실 때(나를 부르던 말)·돌아가신 분 질문은 묻지 않는다
      expect(_has('그분은 나를 뭐라고 부르'), findsNothing);
      expect(_has('그분의 사망·장례·임종'), findsNothing);

      await _tapIn(tester, _has('위 내용을 모두 확인했어요'));
      for (final chip in ['한두 문장으로 짧게', '결론이나 답을 바로 말한다', '다정함']) {
        await _tapIn(tester, _chip(chip));
      }
      await _tapIn(tester, find.text('대화 상대 만들기'));
      // C-05 가 비었으면 서버(400)까지 가지 않고 화면에서 알린다
      expect(_snack('아직 답하지 않은 필수 질문이 있어요.'), findsOneWidget);
      expect(server.count('POST /personas/p1/build'), 0);

      await tester.enterText(_input('예: 정 많은 분'), '정 많은 분');
      await tester.pump(const Duration(seconds: 5));
      await _settle(tester);
      await _tapIn(tester, find.text('대화 상대 만들기'));
      expect(server.count('POST /personas/p1/build'), 1);
      final sent = jsonDecode(server.bodies['POST /personas/p1/build']!.single) as Map;
      expect(sent['answers'], _legacyAnswers);
    });

    testWidgets('살아 계신 분은 서버가 맞춘 지금 말 문구로 그리고, 임종 이야기는 묻지도 채우지도 않는다', (tester) async {
      final alive = <String, dynamic>{..._v1Basics, 'status': 'alive', 'intakeVersion': 2};
      final answers = <String, dynamic>{};
      await tester.pumpWidget(_form(_questionsFor(alive), answers, basics: alive));
      expect(_has('그분은 나를 뭐라고 부르세요?'), findsOneWidget);
      expect(_has('부르셨어요?'), findsNothing);
      expect(_has('첫마디를 뭐라고 하세요?'), findsOneWidget);
      expect(_has('그분은 나를 어떻게 챙기세요?'), findsOneWidget);
      expect(_has('그분의 사망·장례·임종'), findsNothing);
      expect(answers, isEmpty);
    });

    testWidgets('정보 고치기(예전 대화 상대): 새 질문 없이 저장되고, 비운 C-05 는 화면에서 막는다', (tester) async {
      final server = _personaServer(_detailFor(_v1Basics, answers: Map.of(_legacyAnswers)));
      ApiClient.useAdapterForTests(server);
      _phone(tester, height: 4000);
      await tester.pumpWidget(_editApp());
      await _settle(tester);
      expect(_badgeOf(tester, '그분을 한 문장으로'), '필수');
      expect(_badgeOf(tester, '그분 하면 떠오르는 것'), '선택');
      expect(_badgeOf(tester, '첫마디를 뭐라고 하셨어요?'), '선택');
      expect(_badgeOf(tester, '그분은 나를 뭐라고 부르셨어요?'), '선택');
      // 예전 대화 상대에는 미리 채울 답이 없고(서버도 채우지 않는다) 임종 이야기도 선택이다
      expect(_badgeOf(tester, '그분의 사망·장례·임종'), '선택');
      expect(_pill(tester, '물어볼 때만').selected, isFalse);

      await tester.enterText(_input('예: 정 많은 분'), '');
      await tester.pump();
      await _tapIn(tester, find.text('저장하기'));
      expect(_snack('아직 답하지 않은 필수 질문이 있어요.'), findsOneWidget);
      expect(server.count('PUT /personas/p1'), 0);

      await tester.enterText(_input('예: 정 많은 분'), '늘 웃으시던 분');
      await tester.pump(const Duration(seconds: 5));
      await _settle(tester);
      await _tapIn(tester, find.text('저장하기'));
      expect(server.count('PUT /personas/p1'), 1);
      final sent = jsonDecode(server.bodies['PUT /personas/p1']!.single) as Map;
      expect(sent['answers'], {..._legacyAnswers, 'C-05': '늘 웃으시던 분'});
      expect(find.text('대화 화면'), findsOneWidget);
    });

    testWidgets("정보 고치기(개편 뒤): 그분 상태를 '돌아가셨어요'로 바꾸면 임종 이야기가 미리 채워져 고르지 않아도 저장된다",
        (tester) async {
      final alive = <String, dynamic>{..._v1Basics, 'status': 'alive', 'intakeVersion': 2, 'calledMe': '우리 강아지'};
      const answers = <String, dynamic>{
        'M3-01': '__unknown__',
        'C-01': '한두 문장으로 짧게',
        'C-02': '결론이나 답을 바로 말한다',
        'C-07': ['음식·밥을 챙겨서'],
        'M4-01': ['다정함'],
        'M6-08': '된장찌개',
      };
      final server = _personaServer(_detailFor(alive, answers: Map.of(answers)));
      ApiClient.useAdapterForTests(server);
      _phone(tester, height: 4000);
      await tester.pumpWidget(_editApp());
      await _settle(tester);
      expect(_has('그분의 사망·장례·임종'), findsNothing);

      await _tapIn(tester, _chip('아니요, 돌아가셨어요'));
      expect(_badgeOf(tester, '그분의 사망·장례·임종'), '필수');
      expect(_pill(tester, '물어볼 때만').selected, isTrue);

      await _tapIn(tester, find.text('저장하기'));
      expect(_snack('아직 답하지 않은 필수 질문이 있어요.'), findsNothing);
      expect(server.count('PUT /personas/p1'), 1);
      final sent = jsonDecode(server.bodies['PUT /personas/p1']!.single) as Map;
      expect((sent['basics'] as Map)['status'], 'deceased');
      expect(sent['answers'], {...answers, 'M9-01-death': '물으면만'});
    });

    testWidgets("정보 고치기: '그분이 나를 부르던 말'을 비우면 그 질문(P1-07A)이 필수로 다시 보인다", (tester) async {
      final basics = <String, dynamic>{..._v1Basics, 'intakeVersion': 2, 'calledMe': '우리 강아지'};
      final server = _personaServer(_detailFor(basics, answers: const {'M9-01-death': '물으면만'}));
      ApiClient.useAdapterForTests(server);
      _phone(tester, height: 4000);
      await tester.pumpWidget(_editApp());
      await _settle(tester);
      expect(_has('그분은 나를 뭐라고 부르셨어요?'), findsNothing);
      final calledMe = find.byWidgetPredicate((w) => w is TextField && w.controller?.text == '우리 강아지');
      await tester.enterText(calledMe, '');
      await _settle(tester);
      expect(_badgeOf(tester, '그분은 나를 뭐라고 부르셨어요?'), '필수');
    });
  });

  group('기억 더 채우기', () {
    testWidgets('녹음에서 찾은 말 후보를 말끝(M3-07)처럼 서버가 suggest 를 붙인 문항에 보인다', (tester) async {
      final detail = {
        ..._detailFor(_v1Basics, answers: Map.of(_legacyAnswers)),
        'moreQuestions': [
          {
            'id': 'M3-07',
            'section': '말버릇',
            'type': 'phrases',
            'suggest': true,
            'title': '말끝에 자주 붙이시던 말이 있었나요?',
            'placeholder': '예: ~했제',
          },
        ],
        'moreModules': [
          {'id': '말버릇', 'lead': '입에 붙은 말을 알려 주세요'},
        ],
      };
      final server = _personaServer(detail, phrases: ['밥은 묵었나']);
      ApiClient.useAdapterForTests(server);
      _phone(tester, height: 2400);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const PersonaMorePage(personaId: 'p1')));
      await _settle(tester);
      expect(server.count('GET /personas/p1/phrase-suggestions'), 1);
      await _tapIn(tester, find.text('말버릇'));
      expect(_has('녹음에서 찾은 말'), findsOneWidget);
      await _tapIn(tester, _chip('+ 밥은 묵었나'));
      expect(_has('“밥은 묵었나”'), findsOneWidget);
    });

    testWidgets('가져온 대화 상대에는 후보를 읽지 않는다', (tester) async {
      final server = _personaServer({..._detailFor(_v1Basics), 'origin': 'import'});
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const PersonaMorePage(personaId: 'p1')));
      await _settle(tester);
      expect(server.count('GET /personas/p1'), greaterThanOrEqualTo(1));
      expect(server.count('GET /personas/p1/phrase-suggestions'), 0);
    });
  });

  group('🎤 말로 적기', () {
    _Server transcribing({Object? Function()? reply}) => _Server({
          'POST /personas/p1/transcriptions': reply ?? () => {'text': '우리 강아지 왔나'},
        });

    testWidgets('누르면 녹음(흐른 시간·안내), 다시 누르면 글로 옮겨 칸에 넣는다. 소리는 audio 칸으로 보낸다', (tester) async {
      final server = transcribing();
      ApiClient.useAdapterForTests(server);
      final answers = <String, dynamic>{};
      await tester.pumpWidget(_form(const [_firstWord], answers, personaId: 'p1'));

      await tester.tap(find.byTooltip('말로 적기'));
      await tester.pump();
      expect(recorder.started, 1);
      // 말로 보내기와 같은 설정(울림·잡음 줄이기), 웹이 아니면 m4a
      expect(recorder.config!.echoCancel, isTrue);
      expect(recorder.config!.encoder, AudioEncoder.aacLc);
      expect(_has('말씀하신 내용을 글로 옮겨 드려요'), findsOneWidget);
      expect(find.text('0:00 / 3:00'), findsOneWidget);
      await tester.pump(const Duration(seconds: 1));
      expect(find.text('0:01 / 3:00'), findsOneWidget);

      await tester.tap(find.byTooltip('다 말했어요'));
      await _settle(tester);
      expect(recorder.stopped, 1);
      expect(server.count('POST /personas/p1/transcriptions'), 1);
      final body = server.bodies['POST /personas/p1/transcriptions']!.single;
      expect(body, contains('name="audio"'));
      expect(body, contains('filename="voice.m4a"'));
      expect(_field(tester, 0).controller!.text, '우리 강아지 왔나');
      expect(answers['M3-01'], '우리 강아지 왔나');
      expect(_has('말씀하신 내용을 글로 옮겨 드려요'), findsNothing);

      // 한 번 더 말하면 적어 둔 글 뒤에 잇는다(고쳐서 저장한다)
      await tester.tap(find.byTooltip('말로 적기'));
      await tester.pump();
      await tester.tap(find.byTooltip('다 말했어요'));
      await _settle(tester);
      expect(answers['M3-01'], '우리 강아지 왔나 우리 강아지 왔나');
    });

    testWidgets('3분이 되면 저절로 멈추고 글로 옮긴다', (tester) async {
      final server = transcribing(reply: () => {'text': '된장찌개 냄새가 나면 늘 할머니 집 부엌이 생각나요'});
      ApiClient.useAdapterForTests(server);
      final answers = <String, dynamic>{};
      await tester.pumpWidget(_form(const [_thing], answers, personaId: 'p1'));
      await tester.tap(find.byTooltip('말로 적기'));
      await tester.pump();
      for (var i = 0; i < 179; i++) {
        await tester.pump(const Duration(seconds: 1));
      }
      expect(recorder.stopped, 0);
      expect(find.text('2:59 / 3:00'), findsOneWidget);
      await tester.pump(const Duration(seconds: 1));
      await _settle(tester);
      expect(recorder.stopped, 1);
      expect(server.count('POST /personas/p1/transcriptions'), 1);
      // 칸 길이(20자)를 넘는 글은 앞부분만 넣고 알린다
      expect(answers['M6-08'], '된장찌개 냄새가 나면 늘 할머니 집');
      expect(_snack('20자까지 적을 수 있어서 앞부분만 넣었어요'), findsOneWidget);
      expect(maxAnswerVoiceSeconds, 180);
    });

    testWidgets("마이크를 막았으면 '글로 적어 주세요'라고 하고 아무것도 보내지 않는다", (tester) async {
      final server = transcribing();
      ApiClient.useAdapterForTests(server);
      recorder.permission = false;
      await tester.pumpWidget(_form(const [_firstWord], {}, personaId: 'p1'));
      await tester.tap(find.byTooltip('말로 적기'));
      await _settle(tester);
      expect(_snack('마이크를 쓸 수 없어요. 글로 적어 주세요.'), findsOneWidget);
      expect(recorder.started, 0);
      expect(server.log, isEmpty);
    });

    testWidgets('카카오톡 안에서 마이크가 안 되면 다른 브라우저로 열기를 권한다', (tester) async {
      ApiClient.useAdapterForTests(transcribing());
      recorder.permission = false;
      VoiceInput.kakaoInApp = () => true;
      final opened = <String>[];
      final before = VoiceInput.openOutside;
      VoiceInput.openOutside = opened.add;
      addTearDown(() => VoiceInput.openOutside = before);
      await tester.pumpWidget(_form(const [_firstWord], {}, personaId: 'p1'));
      await tester.tap(find.byTooltip('말로 적기'));
      await _settle(tester);
      expect(_has('카카오톡 안에서는 말로 적기가 안 될 수 있어요'), findsOneWidget);
      await tester.tap(find.text('다른 브라우저로 열기'));
      await _settle(tester);
      expect(opened, hasLength(1));

      // '글로 적을게요'는 창만 닫는다
      await tester.tap(find.byTooltip('말로 적기'));
      await _settle(tester);
      await tester.tap(find.text('글로 적을게요'));
      await _settle(tester);
      expect(opened, hasLength(1));
      expect(_has('카카오톡 안에서는'), findsNothing);
    });

    testWidgets('못 알아들으면(422 no_speech) 서버 안내를 보이고 칸은 그대로 둔다', (tester) async {
      ApiClient.useAdapterForTests(transcribing(
        reply: () => const _Fail(422, {'detail': '말씀을 알아듣지 못했어요. 다시 한번 말씀해 주세요.', 'code': 'no_speech'}),
      ));
      final answers = <String, dynamic>{'M3-01': '왔나'};
      await tester.pumpWidget(_form(const [_firstWord], answers, personaId: 'p1'));
      await tester.tap(find.byTooltip('말로 적기'));
      await tester.pump();
      await tester.tap(find.byTooltip('다 말했어요'));
      await _settle(tester);
      expect(_snack('말씀을 알아듣지 못했어요. 다시 한번 말씀해 주세요.'), findsOneWidget);
      expect(answers['M3-01'], '왔나');
      expect(_field(tester, 0).controller!.text, '왔나');
    });

    testWidgets('너무 자주 하면(429) 서버 안내를 그대로 보인다', (tester) async {
      ApiClient.useAdapterForTests(transcribing(
        reply: () => const _Fail(429, {'detail': '잠시 뒤 다시 말씀해 주세요.'}),
      ));
      await tester.pumpWidget(_form(const [_firstWord], {}, personaId: 'p1'));
      await tester.tap(find.byTooltip('말로 적기'));
      await tester.pump();
      await tester.tap(find.byTooltip('다 말했어요'));
      await _settle(tester);
      expect(_snack('잠시 뒤 다시 말씀해 주세요.'), findsOneWidget);
    });

    testWidgets("취소하면 녹음을 버리고 보내지 않는다. '기억이 안 나요'를 고르면 🎤 도 잠긴다", (tester) async {
      final server = transcribing();
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(_form(const [_firstWord], {}, personaId: 'p1'));
      await tester.tap(find.byTooltip('말로 적기'));
      await tester.pump();
      await tester.tap(find.text('취소'));
      await _settle(tester);
      expect(recorder.cancelled, 1);
      expect(server.log, isEmpty);

      await tester.tap(_chip('기억이 안 나요'));
      await tester.pump();
      final mic = tester.widget<IconButton>(
        find.descendant(of: find.byTooltip('말로 적기'), matching: find.byType(IconButton)),
      );
      expect(mic.onPressed, isNull);
    });

    testWidgets('말 목록(입버릇)도 말로 적어 칸에 넣고, 저장할 때 더해진다', (tester) async {
      ApiClient.useAdapterForTests(transcribing(reply: () => {'text': '밥은 먹었냐'}));
      final answers = <String, dynamic>{};
      final key = GlobalKey<AnswersFormState>();
      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: Scaffold(
          body: SingleChildScrollView(
            child: AnswersForm(
              key: key,
              questions: const [
                {'id': 'C-09', 'section': '그분이 하시던 말', 'type': 'phrases', 'required': true, 'title': '자주 하시던 말'},
              ],
              answers: answers,
              keys: const {},
              onChanged: () {},
              personaId: 'p1',
            ),
          ),
        ),
      ));
      await tester.tap(find.byTooltip('말로 적기'));
      await tester.pump();
      await tester.tap(find.byTooltip('다 말했어요'));
      await _settle(tester);
      expect(_field(tester, 0).controller!.text, '밥은 먹었냐');
      key.currentState!.commitPending();
      expect(answers['C-09'], ['밥은 먹었냐']);
    });

    testWidgets('대화 상대를 모르면(personaId 없음) 🎤 를 달지 않는다', (tester) async {
      await tester.pumpWidget(_form(const [_firstWord], {}));
      expect(find.byTooltip('말로 적기'), findsNothing);
    });

    testWidgets("기본 정보의 '그분이 나를 부르던 말': 받아쓸 곳(transcribe)이 없으면(새로 만들 때) 칸만, 있으면 🎤", (tester) async {
      _phone(tester);
      final draft = BasicsDraft(basics: _v1Basics, relations: const ['할머니', '할아버지']);
      addTearDown(draft.dispose);
      Widget basics({Future<String> Function(VoiceClip clip)? transcribe, VoidCallback? onChanged}) => MaterialApp(
            theme: buildTheme(),
            home: Scaffold(
              body: SingleChildScrollView(
                child: BasicsForm(draft: draft, meta: _meta, transcribe: transcribe, onChanged: onChanged),
              ),
            ),
          );

      // 새로 만들 때(아직 대화 상대가 없다): 🎤 없이 글 칸만
      await tester.pumpWidget(basics());
      expect(_input('예: 하늘아, 우리 강아지'), findsOneWidget);
      expect(find.byTooltip('말로 적기'), findsNothing);

      // 정보 고치기: 이 칸에만 🎤(성함·호칭·내 이름은 그대로 글 칸)
      final sent = <VoiceClip>[];
      var changed = 0;
      await tester.pumpWidget(basics(
        transcribe: (clip) async {
          sent.add(clip);
          return '우리 강아지';
        },
        onChanged: () => changed++,
      ));
      expect(find.byTooltip('말로 적기'), findsOneWidget);
      expect(
        find.descendant(
          of: find.ancestor(of: _input('예: 하늘아, 우리 강아지'), matching: find.byType(VoiceField)),
          matching: find.byTooltip('말로 적기'),
        ),
        findsOneWidget,
      );
      await _tapIn(tester, find.byTooltip('말로 적기'));
      await _tapIn(tester, find.byTooltip('다 말했어요'));
      expect(sent, hasLength(1));
      expect(draft.calledMe.text, '우리 강아지');
      expect(draft.toJson()['calledMe'], '우리 강아지');
      // 같은 화면의 질문(P1-07A 보이기·숨기기)이 따라 바뀌게 알린다
      expect(changed, greaterThanOrEqualTo(1));
    });

    testWidgets("정보 고치기: '그분이 나를 부르던 말'을 말로 채우면 그 질문(P1-07A)이 사라지고 기본 정보로 저장된다", (tester) async {
      final basics = <String, dynamic>{..._v1Basics, 'intakeVersion': 2, 'calledMe': ''};
      const answers = <String, dynamic>{
        'M3-01': '__unknown__',
        'C-01': '한두 문장으로 짧게',
        'C-02': '결론이나 답을 바로 말한다',
        'C-07': ['음식·밥을 챙겨서'],
        'M4-01': ['다정함'],
        'M6-08': '된장찌개',
        'M9-01-death': '물으면만',
      };
      final server = _personaServer(_detailFor(basics, answers: Map.of(answers)));
      server.routes['POST /personas/p1/transcriptions'] = () => {'text': '우리 강아지'};
      ApiClient.useAdapterForTests(server);
      _phone(tester, height: 4000);
      await tester.pumpWidget(_editApp());
      await _settle(tester);
      // 기본 정보 칸이 비어 있으니 질문으로 묻는다(그 질문에도 🎤)
      expect(_badgeOf(tester, '그분은 나를 뭐라고 부르셨어요?'), '필수');
      final basicsMic = find.descendant(
        of: find.ancestor(of: _input('예: 하늘아, 우리 강아지'), matching: find.byType(VoiceField)),
        matching: find.byTooltip('말로 적기'),
      );
      expect(basicsMic, findsOneWidget);

      await _tapIn(tester, basicsMic);
      expect(recorder.started, 1);
      await _tapIn(tester, find.byTooltip('다 말했어요'));
      expect(server.count('POST /personas/p1/transcriptions'), 1);
      expect(server.bodies['POST /personas/p1/transcriptions']!.single, contains('name="audio"'));
      expect(tester.widget<TextField>(_input('예: 하늘아, 우리 강아지')).controller!.text, '우리 강아지');
      expect(_has('그분은 나를 뭐라고 부르셨어요?'), findsNothing);

      await _tapIn(tester, find.text('저장하기'));
      expect(server.count('PUT /personas/p1'), 1);
      final sent = jsonDecode(server.bodies['PUT /personas/p1']!.single) as Map;
      expect((sent['basics'] as Map)['calledMe'], '우리 강아지');
      expect(sent['answers'], answers);
    });
  });

  group('녹음기(대화의 말로 보내기와 같이 쓴다)', () {
    testWidgets('시간을 세다가 한도에서 onLimit 을 부르고, 멈추면 소리와 파일 이름을 준다', (tester) async {
      var limits = 0;
      final capture = VoiceCapture(maxSeconds: 3, onLimit: () => limits++, recorder: recorder);
      expect(await capture.start(), isNull);
      expect(capture.recording, isTrue);
      await tester.pump(const Duration(seconds: 2));
      expect(capture.seconds.value, 2);
      expect(limits, 0);
      await tester.pump(const Duration(seconds: 1));
      expect(limits, 1);
      final clip = await capture.stop();
      expect(clip!.bytes, [1, 2, 3, 4]);
      expect(clip.filename, 'voice.m4a');
      expect(capture.recording, isFalse);
      await tester.pump(const Duration(seconds: 3));
      expect(limits, 1); // 멈춘 뒤에는 부르지 않는다
      capture.dispose();
    });

    testWidgets('허락이 없거나 마이크를 켜지 못하면 그 까닭을, 빈 소리는 오류를 준다', (tester) async {
      recorder.permission = false;
      final denied = VoiceCapture(recorder: recorder);
      expect(await denied.start(), VoiceStartFailure.denied);
      expect(recorder.started, 0);
      denied.dispose();

      recorder
        ..permission = true
        ..micWorks = false;
      final busy = VoiceCapture(recorder: recorder);
      expect(await busy.start(), VoiceStartFailure.busy);
      expect(busy.recording, isFalse);
      busy.dispose();

      recorder
        ..micWorks = true
        ..bytes = [];
      final empty = VoiceCapture(recorder: recorder);
      await empty.start();
      await expectLater(empty.stop(), throwsA(isA<ApiException>()));
      empty.dispose();
    });

    testWidgets('대화 화면의 말로 보내기도 같은 녹음기를 쓴다(file 칸으로 보내고 바로 대답을 받는다)', (tester) async {
      _phone(tester, height: 900);
      final server = _Server({
        'POST /personas/p1/conversation': () => {'conversationId': 'c1'},
        'GET /conversations/c1': () => {'messages': <Object>[]},
        'GET /billing/personas/p1': () => {'personaId': 'p1', 'status': 'PRELAUNCH', 'active': true},
        'POST /conversations/c1/messages/voice': () => {
              'userMessage': {'id': 'm1', 'role': 'user', 'content': '할머니 보고 싶어요'},
              'assistantMessage': {'id': 'm2', 'role': 'assistant', 'content': '나도 보고 싶다'},
              'conversationsLeft': null,
              'textOnly': false,
            },
      });
      ApiClient.useAdapterForTests(server);
      final persona = {
        'id': 'p1',
        'role': 'owner',
        'addressTerm': '할머니',
        'relation': '할머니',
        'step': 'READY',
        'hasVoice': false,
        'origin': null,
        'jobs': <String, dynamic>{},
      };
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: persona)));
      await _settle(tester);
      await tester.tap(find.byTooltip('말로 하기'));
      await _settle(tester);
      expect(recorder.started, 1);
      expect(_has('듣고 있어요'), findsOneWidget);
      // 말로 보내기는 2분에서 저절로 보낸다
      for (var i = 0; i < maxVoiceSeconds; i++) {
        await tester.pump(const Duration(seconds: 1));
      }
      await _settle(tester);
      expect(recorder.stopped, 1);
      expect(server.count('POST /conversations/c1/messages/voice'), 1);
      expect(server.bodies['POST /conversations/c1/messages/voice']!.single, contains('name="file"'));
      expect(_has('나도 보고 싶다'), findsOneWidget);
      // 대화 화면 위 단추 이름이 바뀌었다
      expect(find.text('기억 더 채우기'), findsOneWidget);
      expect(find.text('질문 더 답하기'), findsNothing);
    });
  });
}
