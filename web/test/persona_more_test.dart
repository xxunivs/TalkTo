import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/personas/persona_edit_page.dart';
import 'package:talkto_fe/features/personas/persona_more_page.dart';
import 'package:talkto_fe/shared/widgets/ui.dart' show keepAll;

// 기억 더 채우기의 '가족을 알려 주세요' 카드 → 가족 알려 주기(정보 고치기의 가족 표만).
// 시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다).

/// 서버 대신 답한다. PUT 으로 받은 답·기본 정보는 상세에 남겨, 다시 읽으면 저장한 대로 보인다.
class _Server implements HttpClientAdapter {
  Map<String, dynamic> detail;
  final log = <String>[];
  final puts = <Map<String, dynamic>>[];

  _Server(this.detail);

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
    Object? data;
    switch (key) {
      case 'GET /intake/meta':
        data = _meta;
      case 'GET /personas/p1':
        data = detail;
      case 'GET /personas/p1/phrase-suggestions':
      case 'GET /personas':
        data = const <Object>[];
      case 'PUT /personas/p1':
        final body = Map<String, dynamic>.from(jsonDecode(utf8.decode(bytes)) as Map);
        puts.add(body);
        detail = {...detail, 'basics': body['basics'], 'answers': body['answers']};
        data = {'updating': true};
      default:
        return _json(404, {'detail': '찾지 못했어요.'});
    }
    return _json(200, {'data': data});
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

/// 글(낱말 가운데서 줄이 바뀌지 않게 넣은 이음표는 뺀다).
Finder _has(String part) => find.byWidgetPredicate(
    (w) => w is Text && (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', '').contains(part));

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

// ── 서버 문항(대화 상대 상세의 questions 와 같은 모양, talkto_api_server intake.py) ──

const _familyTitle = '그분이 알아야 할 가족을 알려 주세요';

/// 가족 표(M2-01). 묶음 이름은 대화 상대마다 다르다(가족·고인 '나와 그분', 나 자신 '가족').
Map<String, dynamic> _familyQ(String section) => {
      'id': 'M2-01',
      'section': section,
      'type': 'family',
      'required': false,
      'max': 10,
      'title': _familyTitle,
      'hint': "대화하다가 '아빠'·'고모'라고 해도 그분이 누구인지 알아들어요. 안 적어도 괜찮아요.",
      'privacy': '살아 계신 가족의 주소·직장·학교·건강 같은 사생활은 적지 말아 주세요.',
      'relationOptions': ['남편', '아들', '딸'],
      'toMeOptions': ['아빠', '고모'],
    };

const _calledMeQ = {
  'id': 'P1-07A',
  'section': '나와 그분',
  'type': 'text',
  'required': true,
  'when': 'notSelf',
  'skipIfBasics': 'calledMe',
  'maxLength': 15,
  'title': '그분은 나를 뭐라고 부르셨어요?',
};
const _levelQ = {
  'id': 'M2-01-level',
  'section': '나와 그분',
  'type': 'single',
  'required': true,
  'when': 'notSelf',
  'title': '나에게는 반말이셨어요, 존댓말이셨어요?',
  'options': ['반말', '존댓말'],
};
const _toneQ = {
  'id': 'M2-01-tone',
  'section': '가족',
  'type': 'single',
  'required': false,
  'title': '나와 이야기하실 때는 말투가 어떠셨어요?',
  'options': ['다른 가족과 같음', '더 다정하고 살가움'],
};
const _oneLineQ = {
  'id': 'C-05',
  'section': '그분은 어떤 분',
  'type': 'text',
  'required': true,
  'maxLength': 60,
  'title': '그분을 한 문장으로 표현한다면?',
  'placeholder': '예: 정 많은 분',
};

const _meta = <String, dynamic>{
  'questions': <Object>[],
  'sections': <Object>[],
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

/// 개편 뒤 만든 돌아가신 할머니(부르던 말은 기본 정보에 있어 P1-07A 는 묻지 않는다).
const _familyBasics = <String, dynamic>{
  'name': '김예시',
  'relation': '할머니',
  'addressTerm': '할머니',
  'status': 'deceased',
  'myName': '가은',
  'myRelation': '손녀',
  'calledMe': '우리 강아지',
  'intakeVersion': 2,
};

const _selfBasics = <String, dynamic>{
  'name': '김가은',
  'relation': '나 자신',
  'addressTerm': '나',
  'status': 'alive',
  'myName': '김가은',
  'myRelation': null,
  'calledMe': '',
};

const _familyQuestions = [_calledMeQ, _levelQ, _oneLineQ];
const _savedAnswers = <String, dynamic>{'M2-01-level': '반말', 'C-05': '정 많은 분'};

/// 대화 상대 상세(만든 분, 다 만듦). 기억 더 채우기 묶음 하나(기본 정보)가 있다.
Map<String, dynamic> _detail(
  Map<String, dynamic> basics,
  List<Map<String, dynamic>> questions, {
  Map<String, dynamic> answers = _savedAnswers,
  String step = 'READY',
}) =>
    {
      'id': 'p1',
      'role': 'owner',
      'step': step,
      'origin': null,
      'addressTerm': basics['addressTerm'],
      'relation': basics['relation'],
      'lifeStatus': basics['status'] == 'deceased' ? 'DECEASED' : 'LIVING',
      'basics': basics,
      'consents': <String, dynamic>{},
      'answers': answers,
      'jobs': <String, dynamic>{},
      'subjectVoices': <String>[],
      'questions': questions,
      'sections': [
        {'id': '나와 그분', 'lead': '그분과 나 사이에 오가던 말부터 여쭤볼게요.'},
      ],
      'moreQuestions': [
        {'id': 'M1-01', 'section': '기본 정보', 'type': 'text', 'title': '태어난 해는요?'},
      ],
      'moreModules': [
        {'id': '기본 정보', 'lead': '태어난 해, 살아오신 곳'},
      ],
    };

late GoRouter _router;

String get _location => _router.routerDelegate.currentConfiguration.uri.toString();

/// 앱 라우터(core/router.dart)처럼 정보 고치기에 ?section= 을 넘긴다. 그 아래에 대화 화면이 있다.
Widget _app(String initial) {
  _router = GoRouter(
    initialLocation: initial,
    routes: [
      GoRoute(
        path: '/p/:id',
        builder: (context, state) => const Scaffold(body: Text('대화 화면')),
        routes: [
          GoRoute(
            path: 'edit',
            builder: (context, state) => PersonaEditPage(
              key: ValueKey(state.uri.toString()),
              personaId: state.pathParameters['id']!,
              section: state.uri.queryParameters['section'],
            ),
          ),
          GoRoute(
            path: 'more',
            builder: (context, state) => PersonaMorePage(personaId: state.pathParameters['id']!),
          ),
        ],
      ),
    ],
  );
  return MaterialApp.router(theme: buildTheme(), routerConfig: _router);
}

Future<_Server> _open(WidgetTester tester, Map<String, dynamic> detail, String location) async {
  final server = _Server(detail);
  ApiClient.useAdapterForTests(server);
  tester.view.physicalSize = const Size(390, 2400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(_app(location));
  await _settle(tester);
  return server;
}

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiClient.clearToken();
  });

  group('가족 알려 주기', () {
    testWidgets('가족 카드를 누르면 가족 표만 열고, 저장하면 기억 더 채우기로 돌아와 카드가 사라진다', (tester) async {
      final server = await _open(tester, _detail(_familyBasics, [..._familyQuestions, _familyQ('나와 그분')]), '/p/p1/more');
      expect(_has('가족을 알려 주세요'), findsOneWidget);
      expect(_has("대화하다가 '아빠'·'고모'라고 해도 그분이 누구인지 알아들어요."), findsWidgets);
      expect(_has('정보 고치기에서 적어요'), findsNothing);

      await _tapIn(tester, _has('가족을 알려 주세요'));
      expect(_location, PersonaEditPage.familyPath('p1'));
      expect(_location, '/p/p1/edit?section=family');
      expect(_has('가족 알려 주기'), findsOneWidget);
      expect(_has(_familyTitle), findsOneWidget);
      // 같은 묶음의 다른 질문·다른 묶음·기본 정보·다른 화면 단추는 없다
      expect(_has('반말이셨어요'), findsNothing);
      expect(_has('그분을 한 문장으로'), findsNothing);
      expect(_has('나와 그분'), findsNothing);
      expect(_has('나에게 어떤 분인가요?'), findsNothing);
      expect(_has('정보 고치기'), findsNothing);
      expect(_has('남긴 글 더하기'), findsNothing);

      await _tapIn(tester, find.text('가족 더하기'));
      await tester.enterText(find.widgetWithText(TextField, '예: 홍철수'), '홍철수');
      await _tapIn(tester, find.text(keepAll('아들')));
      await _tapIn(tester, find.text(keepAll('아빠')));
      await _tapIn(tester, find.text('더하기').last);
      expect(_has('홍철수 · 아들'), findsOneWidget);

      await _tapIn(tester, find.text('저장하기'));
      await _settle(tester);
      expect(server.puts, hasLength(1));
      // 가족 표만 바꾸고 다른 답·기본 정보(질문 차례 포함)는 서버 값 그대로
      expect(server.puts.single['answers'], {
        ..._savedAnswers,
        'M2-01': [
          {'name': '홍철수', 'relation': '아들', 'toMe': '아빠', 'status': 'alive'},
        ],
      });
      expect(server.puts.single['basics'], _familyBasics);
      expect(_location, '/p/p1/more');
      expect(_has('더 알려 주세요'), findsOneWidget);
      expect(_has('가족을 알려 주세요'), findsNothing);
    });

    testWidgets('취소와 위 뒤로 가기는 저장하지 않고 기억 더 채우기로 돌아온다', (tester) async {
      final server = await _open(tester, _detail(_familyBasics, [..._familyQuestions, _familyQ('나와 그분')]), '/p/p1/more');
      await _tapIn(tester, _has('가족을 알려 주세요'));
      expect(_has('가족 알려 주기'), findsOneWidget);
      await _tapIn(tester, find.text('취소'));
      expect(_location, '/p/p1/more');
      expect(_has('가족을 알려 주세요'), findsOneWidget);

      await _tapIn(tester, _has('가족을 알려 주세요'));
      await _tapIn(tester, find.byType(BackButton));
      expect(_location, '/p/p1/more');
      expect(find.text('대화 화면'), findsNothing);
      expect(server.count('PUT /personas/p1'), 0);
    });

    testWidgets('나 자신: 가족 표 묶음 이름(가족)이 달라도 가족 표만 보이고 그것만 저장한다', (tester) async {
      final server = await _open(
        tester,
        _detail(_selfBasics, [_familyQ('가족'), _toneQ, _oneLineQ], answers: const {'C-05': '꼼꼼한 사람'}),
        PersonaEditPage.familyPath('p1'),
      );
      expect(_has('가족 알려 주기'), findsOneWidget);
      expect(_has(_familyTitle), findsOneWidget);
      expect(_has('말투가 어떠셨어요?'), findsNothing);
      expect(_has('나에 대해'), findsNothing);

      await _tapIn(tester, find.text('가족 더하기'));
      // 나 자신은 '나에게는' 칸이 없다
      expect(find.text('나에게는 (내가 부르는 말)'), findsNothing);
      await tester.enterText(find.widgetWithText(TextField, '예: 홍철수'), '김하늘');
      await _tapIn(tester, find.text(keepAll('딸')));
      await _tapIn(tester, find.text('더하기').last);
      await _tapIn(tester, find.text('저장하기'));
      await _settle(tester);
      expect(server.puts.single['answers'], {
        'C-05': '꼼꼼한 사람',
        'M2-01': [
          {'name': '김하늘', 'relation': '딸', 'status': 'alive'},
        ],
      });
      expect(_location, '/p/p1/more');
    });

    testWidgets('가족 표가 없는 대화 상대(예전 서버)면 전체 정보 고치기를 보이고, 취소하면 기억 더 채우기로', (tester) async {
      final server = await _open(tester, _detail(_familyBasics, _familyQuestions), PersonaEditPage.familyPath('p1'));
      expect(_has('가족 알려 주기'), findsNothing);
      expect(_has('할머니 정보 고치기'), findsOneWidget);
      expect(_has('나에게 어떤 분인가요?'), findsOneWidget);
      expect(_has('반말이셨어요'), findsOneWidget);
      await _tapIn(tester, find.text('취소'));
      expect(_location, '/p/p1/more');
      expect(server.count('PUT /personas/p1'), 0);
    });

    testWidgets('주소에 section 이 없으면 예전 그대로: 기본 정보와 모든 질문, 취소하면 대화 화면', (tester) async {
      // 대화 화면 위에 정보 고치기가 열린다
      final server = await _open(tester, _detail(_familyBasics, [..._familyQuestions, _familyQ('나와 그분')]), '/p/p1/edit');
      expect(_has('할머니 정보 고치기'), findsOneWidget);
      expect(_has('나에게 어떤 분인가요?'), findsOneWidget);
      expect(_has('반말이셨어요'), findsOneWidget);
      expect(_has(_familyTitle), findsOneWidget);
      expect(_has('나와 그분'), findsOneWidget);
      expect(_has('기억 더 채우기 — 인생 이야기'), findsOneWidget);
      await _tapIn(tester, find.text('취소'));
      expect(find.text('대화 화면'), findsOneWidget);
      expect(server.count('PUT /personas/p1'), 0);
    });
  });
}
