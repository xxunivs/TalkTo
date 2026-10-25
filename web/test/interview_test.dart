import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/analytics.dart';
import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/router.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/admin/interview_admin_page.dart';
import 'package:talkto_fe/features/interview/interview_api.dart';
import 'package:talkto_fe/features/interview/interview_request_page.dart';

// 시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다).

/// 대화 상대 상세(GET /personas/p1)만 답하는 가짜 서버.
class _DetailServer implements HttpClientAdapter {
  final Map<String, dynamic> detail;

  _DetailServer(this.detail);

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    if (options.method == 'GET' && options.path == '/personas/p1') {
      return ResponseBody.fromString(jsonEncode({'data': detail}), 200, headers: {
        Headers.contentTypeHeader: ['application/json'],
      });
    }
    return ResponseBody.fromString(jsonEncode({'detail': '없음'}), 404, headers: {
      Headers.contentTypeHeader: ['application/json'],
    });
  }

  @override
  void close({bool force = false}) {}
}

Map<String, dynamic> _detail({String relation = '할머니', String role = 'owner'}) => {
      'id': 'p1',
      'name': '김예시',
      'relation': relation,
      'addressTerm': relation == '나 자신' ? '나' : '할머니',
      'lifeStatus': 'DECEASED',
      'step': 'READY',
      'role': role,
      'origin': null,
      'basics': {'name': '김예시', 'relation': relation, 'myName': '가은', 'myRelation': '손녀'},
      'answers': <String, dynamic>{},
      'jobs': <String, dynamic>{},
    };

class _FakeInterviewApi extends InterviewApi {
  Map<String, dynamic> current;
  final applied = <Map<String, dynamic>>[];
  int stateCalls = 0;
  int cancels = 0;
  List<Map<String, dynamic>> rows = const [];

  _FakeInterviewApi(this.current);

  @override
  Future<Map<String, dynamic>> state(String personaId) async {
    stateCalls++;
    return current;
  }

  @override
  Future<Map<String, dynamic>> apply(String personaId, Map<String, dynamic> body) async {
    applied.add(body);
    current = {
      ...current,
      'available': false,
      'request': {
        'id': 'r1',
        'status': 'requested',
        'days': body['days'],
        'timeText': body['timeText'],
        'method': body['method'],
        'recordingOk': body['agreeRecording'],
      },
    };
    return current;
  }

  @override
  Future<Map<String, dynamic>> cancel(String personaId) async {
    cancels++;
    current = {
      ...current,
      'available': true,
      'request': {...(current['request'] as Map), 'status': 'cancelled'},
    };
    return current;
  }

  @override
  Future<List<Map<String, dynamic>>> adminList({String? status}) async =>
      [for (final r in rows) if (status == null || r['status'] == status) r];

  // ── 관리자: 들은 내용 넣기 ──
  /// 대화 상대에 지금 저장된 답(persona.answers).
  Map<String, dynamic> storedAnswers = {'X-01': '예전 답'};
  final answers = <Map<String, dynamic>>[];
  final memories = <Map<String, dynamic>>[];
  int rebuilds = 0;

  @override
  Future<Map<String, dynamic>> adminPersona(String personaId) async => {
        'persona': {'id': personaId, 'title': '할머니', 'step': 'READY', 'origin': null, 'self': false},
        'requests': const [],
        'entries': [
          for (final a in answers)
            {
              'id': 'e${answers.indexOf(a)}',
              'questionId': a['questionId'],
              'questionTitle': '예시 질문',
              'text': a['text'],
              'createdAt': '2026-10-21T01:00:00Z',
            },
        ],
        'memories': [
          for (final m in memories) {'id': 'm${memories.indexOf(m)}', 'title': m['title'] ?? '기억 인터뷰', 'text': m['text']},
        ],
        'answers': storedAnswers,
        'rebuild': rebuilds == 0 ? null : {'status': 'DONE', 'createdAt': '2026-10-21T01:00:00Z'},
      };

  @override
  Future<Map<String, dynamic>> addAnswer(
    String personaId, {
    required String questionId,
    required String text,
    bool replace = false,
    String? requestId,
  }) async {
    answers.add({'questionId': questionId, 'text': text, 'replace': replace, 'requestId': requestId});
    return {'entryId': 'e1'};
  }

  final removedEntries = <String>[];

  /// 넣은 답 빼기. 빼면 그 질문이 비는 것으로 본다(requiredMissing).
  @override
  Future<Map<String, dynamic>> removeAnswer(String personaId, String entryId) async {
    removedEntries.add(entryId);
    final index = int.parse(entryId.substring(1));
    final questionId = answers[index]['questionId'];
    answers.removeAt(index);
    return {'deleted': true, 'questionId': questionId, 'changed': true, 'answer': null, 'requiredMissing': true};
  }

  @override
  Future<Map<String, dynamic>> addMemory(String personaId, {required String text, String? title, bool askedOnly = false}) async {
    memories.add({'text': text, 'title': title, 'askedOnly': askedOnly});
    return {'id': 'm1'};
  }

  @override
  Future<Map<String, dynamic>> rebuild(String personaId) async {
    rebuilds++;
    return {'rebuild': {'status': 'RUNNING'}};
  }
}

const _days = ['월', '화', '수', '목', '금', '토', '일'];

Map<String, dynamic> _fresh({String? reason}) => {
      'available': reason == null,
      'reason': reason,
      'request': null,
      'prefill': {'name': '가은', 'relation': '손녀'},
      'days': _days,
    };

/// 화면 글은 단어 중간에서 끊기지 않게 이음표(keepAll)가 들어 있어, 빼고 찾는다.
String _plain(Widget w) {
  if (w is Text) return (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', '');
  if (w is RichText) return w.text.toPlainText().replaceAll('⁠', '');
  return '';
}

Finder _text(String s) => find.byWidgetPredicate((w) => w is Text && _plain(w).contains(s));

/// 글이 꼭 그것인 칩·단추(keepAll 이음표를 빼고 견준다).
Finder _exact(String s) => find.byWidgetPredicate((w) => w is Text && _plain(w) == s);

Widget _app(Widget home) => MaterialApp(theme: buildTheme(), home: home);

ButtonStyleButton _button(WidgetTester tester, String label) => tester.widget<ButtonStyleButton>(
      find.ancestor(of: find.text(label), matching: find.byWidgetPredicate((w) => w is ButtonStyleButton)).first,
    );

Future<void> _tap(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void main() {
  test('신청 단추는 만든 분의, 나 자신이 아닌 대화 상대에만', () {
    expect(canAskInterview(_detail()), isTrue);
    expect(canAskInterview(_detail(role: 'member')), isFalse);
    expect(canAskInterview(_detail(relation: '나 자신')), isFalse);
    expect(canAskInterview(null), isFalse);
    expect(interviewTitle, '기억 인터뷰 받기');
    expect(interviewSubtitle, '대표가 직접 이야기를 들어 드려요(초기 가족 무료)');
  });

  test("'상담'이라는 말을 쓰지 않는다", () {
    for (final path in [
      'lib/features/interview/interview_api.dart',
      'lib/features/interview/interview_request_page.dart',
      'lib/features/admin/interview_admin_page.dart',
    ]) {
      expect(File(path).readAsStringSync().contains('상담'), isFalse, reason: path);
    }
  });

  test('관리자 주소와 신청 주소가 있고, 통계에는 id 를 남기지 않는다', () {
    for (final path in ['/admin/interviews', '/admin/interviews/r1', '/admin/personas/p1/interview', '/p/p1/interview']) {
      expect(router.configuration.findMatch(Uri.parse(path)).isError, isFalse, reason: path);
    }
    expect(Analytics.screenName('/admin/interviews/abc-123'), 'admin_interviews');
    expect(Analytics.screenName('/admin/personas/abc-123/interview'), 'admin_personas');
    expect(Analytics.screenName('/admin/stats'), 'admin_stats');
    expect(Analytics.screenName('/p/abc-123/interview'), 'persona_interview');
  });

  testWidgets('신청서: 이름·관계를 미리 채우고, 필수 동의 전에는 보낼 수 없다', (tester) async {
    tester.view.physicalSize = const Size(900, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    ApiClient.useAdapterForTests(_DetailServer(_detail()));
    final api = _FakeInterviewApi(_fresh());
    await tester.pumpWidget(_app(InterviewRequestPage(personaId: 'p1', api: api)));
    await tester.pumpAndSettle();

    final fields = find.byType(TextField);
    expect(tester.widget<TextField>(fields.at(0)).controller!.text, '가은');
    expect(tester.widget<TextField>(fields.at(1)).controller!.text, '손녀');
    // 그분 본인께는 연락하지 않는다는 안내
    expect(_text('본인께는 연락드리지 않아요'), findsOneWidget);
    expect(_button(tester, '신청하기').onPressed, isNull);

    await tester.enterText(fields.at(2), '010-1234-5678');
    await _tap(tester, _exact('토'));
    await _tap(tester, _exact('월'));
    await _tap(tester, _exact('오후'));
    await _tap(tester, _exact('영상 통화'));
    // 동의 전에는 여전히 막혀 있다(미리 체크하지 않는다)
    expect(tester.widget<Checkbox>(find.byType(Checkbox).at(0)).value, isFalse);
    expect(tester.widget<Checkbox>(find.byType(Checkbox).at(1)).value, isFalse);
    expect(_button(tester, '신청하기').onPressed, isNull);
    await _tap(tester, find.byType(Checkbox).at(0));
    expect(_button(tester, '신청하기').onPressed, isNotNull);

    await _tap(tester, find.text('신청하기'));
    expect(api.applied, hasLength(1));
    final body = api.applied.single;
    expect(body['name'], '가은');
    expect(body['relation'], '손녀');
    expect(body['phone'], '010-1234-5678');
    expect(body['days'], ['월', '토']); // 요일 순서대로
    expect(body['timeText'], '오후');
    expect(body['method'], 'video');
    expect(body['agreeUse'], isTrue);
    expect(body['agreeRecording'], isFalse);
    expect(_text('신청했어요'), findsWidgets);
    expect(find.text('신청 취소하기'), findsOneWidget);

    // 취소하면 다시 신청할 수 있다
    await _tap(tester, find.text('신청 취소하기'));
    await _tap(tester, find.descendant(of: find.byType(AlertDialog), matching: find.text('신청 취소하기')));
    expect(api.cancels, 1);
    expect(find.text('다시 신청하기'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('안내 글은 그분 상태에 맞춘다: 돌아가신 분은 어떤 분이셨는지, 살아 계신 분은 어떤 분이신지', (tester) async {
    tester.view.physicalSize = const Size(900, 2400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    ApiClient.useAdapterForTests(_DetailServer(_detail()));
    await tester.pumpWidget(_app(InterviewRequestPage(personaId: 'p1', api: _FakeInterviewApi(_fresh()))));
    await tester.pumpAndSettle();
    expect(_text('할머니가 어떤 분이셨는지'), findsOneWidget);
    expect(_text('어떤 분이신지'), findsNothing);

    final living = _detail();
    living['lifeStatus'] = 'LIVING';
    living['basics'] = {...(living['basics'] as Map), 'status': 'alive'};
    ApiClient.useAdapterForTests(_DetailServer(living));
    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(_app(InterviewRequestPage(personaId: 'p1', api: _FakeInterviewApi(_fresh()))));
    await tester.pumpAndSettle();
    expect(_text('할머니가 어떤 분이신지'), findsOneWidget);
    expect(_text('어떤 분이셨는지'), findsNothing);
  });

  testWidgets('나 자신 대화 상대는 신청서 대신 까닭을 보인다', (tester) async {
    ApiClient.useAdapterForTests(_DetailServer(_detail(relation: '나 자신')));
    final api = _FakeInterviewApi(_fresh(reason: '내 이야기로 만든 대화 상대는 기억 인터뷰를 신청할 수 없어요.'));
    await tester.pumpWidget(_app(InterviewRequestPage(personaId: 'p1', api: api)));
    await tester.pumpAndSettle();
    expect(_text('신청할 수 없어요'), findsOneWidget);
    expect(find.text('신청하기'), findsNothing);
    expect(find.byType(TextField), findsNothing);
  });

  testWidgets('함께 쓰는 가족은 신청 화면을 쓰지 않는다', (tester) async {
    ApiClient.useAdapterForTests(_DetailServer({..._detail(role: 'member'), 'ownerName': '가은'}));
    final api = _FakeInterviewApi(_fresh());
    await tester.pumpWidget(_app(InterviewRequestPage(personaId: 'p1', api: api)));
    await tester.pumpAndSettle();
    expect(api.stateCalls, 0);
    expect(find.text('신청하기'), findsNothing);
    expect(_text('만든 분만'), findsOneWidget);
  });

  testWidgets('관리자 목록: 상태로 거르고 전화번호는 보이지 않는다', (tester) async {
    final api = _FakeInterviewApi(_fresh())
      ..rows = [
        {
          'id': 'r1',
          'status': 'requested',
          'name': '가은',
          'relation': '손녀',
          'days': ['월'],
          'timeText': '오후',
          'method': 'phone',
          'createdAt': '2026-10-21T01:00:00Z',
          'persona': {'id': 'p1', 'title': '할머니'},
        },
        {
          'id': 'r2',
          'status': 'done',
          'name': null,
          'wiped': true,
          'days': <String>[],
          'method': 'video',
          'createdAt': '2026-09-01T01:00:00Z',
          'persona': {'id': 'p2', 'title': '아버지'},
        },
      ];
    await tester.pumpWidget(_app(InterviewAdminListPage(api: api)));
    await tester.pumpAndSettle();
    expect(_text('가은 · 손녀 · 대화 상대: 할머니'), findsOneWidget);
    expect(_text('(지운 연락처)'), findsNothing); // 기본은 '신청했어요'만
    await _tap(tester, _exact('전체'));
    expect(_text('(지운 연락처)'), findsOneWidget);
    expect(_text('010'), findsNothing);
  });

  testWidgets('들은 내용 넣기: 질문 번호로 답 넣기 · 기억 카드 · 다시 만들기', (tester) async {
    tester.view.physicalSize = const Size(900, 2600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final api = _FakeInterviewApi(_fresh());
    Future<Map<String, dynamic>> meta() async => {
          'questions': [
            {'id': 'M2-01', 'type': 'family', 'title': '가족 표'},
            {'id': 'X-01', 'type': 'textarea', 'title': '예시 질문'},
          ],
          'moreQuestions': [
            {'id': 'X-02', 'type': 'phrases', 'title': '예시 말버릇'},
          ],
        };
    await tester.pumpWidget(_app(Scaffold(
      body: SingleChildScrollView(
        child: InterviewEntryPanel(personaId: 'p1', requestId: 'r1', api: api, meta: meta),
      ),
    )));
    await tester.pumpAndSettle();
    // 가족 표는 여기서 넣지 않는다
    await _tap(tester, find.byKey(const ValueKey('interview-question')));
    expect(find.textContaining('M2-01'), findsNothing);
    await _tap(tester, find.text('X-01 · 예시 질문').last);
    expect(_text('지금 답: 예전 답'), findsOneWidget);
    expect(_button(tester, '답 넣기').onPressed, isNull);
    await tester.enterText(find.byType(TextField).at(0), '인터뷰로 들은 이야기');
    await tester.pumpAndSettle();
    await _tap(tester, find.text('답 넣기'));
    expect(api.answers.single, {'questionId': 'X-01', 'text': '인터뷰로 들은 이야기', 'replace': false, 'requestId': 'r1'});
    expect(_text('인터뷰로 들은 이야기'), findsOneWidget);
    // 넣은 답 빼기: 한 번 묻고, 빼면 목록에서 사라지고, 꼭 답할 질문이 비면 알려 준다
    await _tap(tester, find.byTooltip('이 답 빼기'));
    expect(_text('이 답을 뺄까요?'), findsOneWidget);
    await _tap(tester, find.text('빼기').last);
    expect(api.removedEntries, ['e0']);
    expect(_text('인터뷰로 들은 이야기'), findsNothing);
    expect(_text('꼭 답할 질문(X-01)이 비었어요'), findsOneWidget);
    await tester.enterText(find.byType(TextField).at(0), '인터뷰로 들은 이야기');
    await tester.pumpAndSettle();
    await _tap(tester, find.text('답 넣기'));

    await tester.enterText(find.byType(TextField).at(1), '즐겨 부르시던 노래');
    await tester.enterText(find.byType(TextField).at(2), '설거지하며 흥얼거리셨다');
    await tester.pumpAndSettle();
    await _tap(tester, _exact('물어볼 때만 (먼저 꺼내지 않아요)'));
    await _tap(tester, find.text('기억 카드 넣기').last);
    expect(api.memories.single, {'text': '설거지하며 흥얼거리셨다', 'title': '즐겨 부르시던 노래', 'askedOnly': true});

    await _tap(tester, find.text('다시 만들기').last);
    expect(api.rebuilds, 1);
    expect(tester.takeException(), isNull);
  });

  testWidgets("들은 내용 넣기: '기억이 안 나요'·'따로 없었어요'로 저장된 답은 그 단추 이름으로 보인다", (tester) async {
    tester.view.physicalSize = const Size(900, 2600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final api = _FakeInterviewApi(_fresh())
      ..storedAnswers = {'M3-01': '__unknown__', 'M2-01-self': '__none__', 'P1-05': '__unknown__'};
    Future<Map<String, dynamic>> meta() async => {
          'questions': [
            {
              'id': 'M3-01',
              'type': 'text',
              'title': '첫마디',
              'skipOption': {'value': '__unknown__', 'label': '기억이 안 나요'},
            },
            {'id': 'M2-01-self', 'type': 'text', 'title': '자기를 뭐라고'},
          ],
          'moreQuestions': [
            {
              'id': 'P1-05',
              'type': 'text',
              'title': '고향',
              'skipOption': {'value': '__unknown__', 'label': '모름'},
            },
          ],
        };
    await tester.pumpWidget(_app(Scaffold(
      body: SingleChildScrollView(child: InterviewEntryPanel(personaId: 'p1', api: api, meta: meta)),
    )));
    await tester.pumpAndSettle();
    Future<void> pick(String label) async {
      await _tap(tester, find.byKey(const ValueKey('interview-question')));
      await _tap(tester, find.text(label).last);
    }

    await pick('M3-01 · 첫마디');
    expect(_text('지금 답: 기억이 안 나요'), findsOneWidget);
    await pick('P1-05 · 고향');
    expect(_text('지금 답: 모름'), findsOneWidget);
    // 문항에 단추 정의가 없어도 저장 값 그대로 보이지 않는다
    await pick('M2-01-self · 자기를 뭐라고');
    expect(_text('지금 답: 따로 없었어요'), findsOneWidget);
    expect(_text('__'), findsNothing);
  });
}
