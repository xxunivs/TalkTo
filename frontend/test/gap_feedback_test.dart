import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/admin/feedback_admin_page.dart';
import 'package:talkto_fe/features/chat/chat_feedback.dart';
import 'package:talkto_fe/features/chat/persona_chat_page.dart';
import 'package:talkto_fe/features/gaps/gap_api.dart';
import 'package:talkto_fe/features/gaps/gap_questions_page.dart';
import 'package:talkto_fe/features/gaps/gap_widgets.dart';
import 'package:talkto_fe/features/share/roles.dart' show memberSendNotice;
import 'package:talkto_fe/shared/widgets/ui.dart' show keepAll;
import 'package:talkto_fe/shared/widgets/voice_input.dart';

// 2026-10-21 개편 §4·§5: 대화 평가(말풍선 ⋯·대화 한마디)와 더 알려 주실 기억(대화를 나올 때 하나, 페르소나 화면 목록).
// 시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다).

class _Fail {
  final int status;
  final Map<String, dynamic> body;

  const _Fail(this.status, this.body);
}

/// 서버 대신 답한다. 'POST /messages/m2/feedback' 처럼 메서드와 경로로 고른다. 받은 요청·몸통·쿼리를 적어 둔다.
/// 정해 두지 않은 주소는 404.
class _Server implements HttpClientAdapter {
  final Map<String, Object? Function()> routes;
  final log = <String>[];
  final bodies = <String, List<Object?>>{};
  final queries = <String, List<Map<String, dynamic>>>{};

  _Server(this.routes);

  int count(String key) => log.where((k) => k == key).length;

  Object? body(String key) => bodies[key]?.last;

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
    final text = utf8.decode(bytes, allowMalformed: true);
    Object? decoded = text;
    try {
      decoded = text.isEmpty ? null : jsonDecode(text);
    } catch (_) {
      // 파일 묶음(multipart)은 글 그대로
    }
    bodies.putIfAbsent(key, () => []).add(decoded);
    queries.putIfAbsent(key, () => []).add(Map<String, dynamic>.from(options.queryParameters));
    final handler = routes[key];
    final result = handler == null ? const _Fail(404, {'detail': '찾지 못했어요.'}) : handler();
    if (result is _Fail) return _json(result.status, result.body);
    if (result is _Page) return _json(200, {'data': result.rows, 'meta': {'page': 1, 'limit': 50, 'total': result.total}});
    return _json(200, {'data': result});
  }

  @override
  void close({bool force = false}) {}
}

/// meta 가 붙는 목록 응답.
class _Page {
  final List<Map<String, dynamic>> rows;
  final int total;

  const _Page(this.rows, this.total);
}

ResponseBody _json(int status, Object body) => ResponseBody.fromString(
      jsonEncode(body),
      status,
      headers: {
        Headers.contentTypeHeader: ['application/json'],
      },
    );

class _FakeRecorder implements VoiceRecorder {
  bool recordingNow = false;

  @override
  Future<bool> hasPermission() async => true;

  @override
  Future<bool> isEncoderSupported(AudioEncoder encoder) async => true;

  @override
  Future<void> start(RecordConfig config, {required String path}) async => recordingNow = true;

  @override
  Future<bool> isRecording() async => recordingNow;

  @override
  Future<String?> stop() async {
    recordingNow = false;
    return 'blob:fake';
  }

  @override
  Future<List<int>> read(String path) async => [1, 2, 3];

  @override
  Future<void> cancel() async => recordingNow = false;

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

Future<void> _settle(WidgetTester tester, {int frames = 16}) async {
  for (var i = 0; i < frames; i++) {
    await tester.pump(const Duration(milliseconds: 30));
  }
}

Future<void> _tap(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pump();
  await tester.tap(finder);
  await _settle(tester);
}

void _phone(WidgetTester tester, {double height = 1400}) {
  tester.view.physicalSize = Size(390, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

Map<String, dynamic> _owner({int gapCount = 0, int gapWaiting = 0, bool hasVoice = false}) => {
      'id': 'p1',
      'role': 'owner',
      'addressTerm': '할머니',
      'relation': '할머니',
      'step': 'READY',
      'hasVoice': hasVoice,
      'origin': null,
      'jobs': <String, dynamic>{},
      'gapCount': gapCount,
      'gapWaiting': gapWaiting,
    };

Map<String, dynamic> _member({int gapCount = 0}) => {
      ..._owner(gapCount: gapCount),
      'role': 'member',
      'ownerName': '지은',
      'me': {'shareId': 's1', 'myName': '철수', 'myRelation': '아들', 'addressTerm': '엄마'},
      'addressTerm': '엄마',
    };

Map<String, Object? Function()> _chatRoutes({List<Object> messages = const []}) => {
      'POST /personas/p1/conversation': () => {'conversationId': 'c1'},
      'GET /conversations/c1': () => {'messages': messages},
      'GET /billing/personas/p1': () => {'personaId': 'p1', 'status': 'PRELAUNCH', 'active': true},
      'POST /conversations/c1/messages/text': () => {
            'userMessage': {'id': 'm1', 'role': 'user', 'content': '할머니 노래 불러 주세요'},
            'assistantMessage': {'id': 'm2', 'role': 'assistant', 'content': '아이고 노래는 무슨'},
            'conversationsLeft': null,
            'textOnly': false,
          },
    };

const _reply = {'id': 'm2', 'role': 'assistant', 'content': '밥은 먹었니? 요즘 바쁘제?'};

Map<String, dynamic> _question(String id, String text, {bool askFamily = false}) => {
      'id': id,
      'personaId': 'p1',
      'topic': '노래',
      'question': text,
      'status': 'open',
      'signal': 'model',
      'askFamily': askFamily,
      'answerState': null,
      'answeredByMe': false,
      'answerText': null,
      'answeredBy': null,
    };

void main() {
  late _FakeRecorder recorder;

  setUp(() {
    SharedPreferences.setMockInitialValues({'talkto.memberTipSeen': true});
    ApiClient.clearToken();
    GapLeave.later.clear();
    recorder = _FakeRecorder();
    VoiceRecorder.create = () => recorder;
    VoiceInput.kakaoInApp = () => false;
  });

  tearDown(() {
    VoiceRecorder.create = () => throw StateError('시험마다 가짜 녹음기를 넣는다');
  });

  group('말풍선 ⋯ 평가', () {
    testWidgets('말투가 달라요 + 고친 한 줄을 보내고, 반영됐다고 알린다. 목소리가 없으면 목소리 고르기는 없다', (tester) async {
      _phone(tester);
      final server = _Server({
        ..._chatRoutes(messages: [_reply]),
        'POST /messages/m2/feedback': () => {'id': 'f1', 'messageId': 'm2', 'state': 'applied', 'tags': ['STYLE']},
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: _owner())));
      await _settle(tester);

      // 말풍선 글을 가리지 않는 작은 ⋯ (대답마다)
      expect(find.byTooltip('이 대답 어땠나요?'), findsOneWidget);
      await _tap(tester, find.byTooltip('이 대답 어땠나요?'));
      expect(find.text('이 대답은 어땠나요?'), findsOneWidget);
      for (final label in ['그분 같았어요', '말투가 달라요', '내용이 틀려요', '너무 길어요']) {
        expect(_chip(label), findsOneWidget, reason: label);
      }
      expect(_chip('목소리가 달라요'), findsNothing);
      // 고친 한 줄은 말투·내용을 고를 때만
      expect(_has('그분이라면 어떻게 말하셨을까요?'), findsNothing);
      await _tap(tester, _chip('말투가 달라요'));
      expect(_has('그분이라면 어떻게 말하셨을까요? (선택)'), findsOneWidget);
      final sheetField = find.descendant(of: find.byType(BottomSheet), matching: find.byType(TextField));
      expect(sheetField, findsOneWidget);
      // 🎤 로도 적는다
      expect(find.descendant(of: find.byType(BottomSheet), matching: find.byTooltip('말로 적기')), findsOneWidget);
      // 만든 분이 고친 말은 바로 반영되므로 확인 안내가 없다
      expect(_has(memberSendNotice), findsNothing);
      await tester.enterText(sheetField, '밥은   묵었나\n우리 강아지');
      await _tap(tester, find.text('보내기'));

      expect(server.count('POST /messages/m2/feedback'), 1);
      expect(server.body('POST /messages/m2/feedback'), {
        'rating': 'DOWN',
        'tags': ['STYLE'],
        'correction': '밥은 묵었나 우리 강아지',
      });
      expect(_snack('고쳐 주신 말투를 다음 대답부터 참고해요.'), findsOneWidget);
      expect(find.byType(BottomSheet), findsNothing);
      // 고른 것이 말풍선 아래에 작게 남는다
      expect(find.text('말투가 달라요'), findsOneWidget);
    });

    testWidgets('그분 같았어요는 UP 이고 고친 한 줄을 묻지 않는다. 다른 고르기와 함께 고르지 않는다', (tester) async {
      _phone(tester);
      final server = _Server({
        ..._chatRoutes(messages: [_reply]),
        'POST /messages/m2/feedback': () => {'id': 'f1', 'state': 'none'},
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(
        MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: _owner(hasVoice: true))),
      );
      await _settle(tester);
      await _tap(tester, find.byTooltip('이 대답 어땠나요?'));
      expect(_chip('목소리가 달라요'), findsOneWidget); // 목소리가 있는 대화 상대
      await _tap(tester, _chip('내용이 틀려요'));
      await _tap(tester, _chip('그분 같았어요'));
      expect(_has('그분이라면 어떻게 말하셨을까요?'), findsNothing);
      await _tap(tester, find.text('보내기'));
      expect(server.body('POST /messages/m2/feedback'), {
        'rating': 'UP',
        'tags': ['LIKE'],
      });
      expect(_snack('알려 주셔서 고마워요.'), findsOneWidget);
    });

    testWidgets('함께 쓰는 가족의 고침은 만든 분 확인을 기다린다고 알린다', (tester) async {
      _phone(tester);
      final server = _Server({
        ..._chatRoutes(messages: [_reply]),
        'POST /messages/m2/feedback': () => {'id': 'f1', 'state': 'pending_owner'},
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: _member())));
      await _settle(tester);
      await _tap(tester, find.byTooltip('이 대답 어땠나요?'));
      // 고친 한 줄을 묻기 전(그분 같았어요·너무 길어요)에는 안내가 없다
      expect(_has(memberSendNotice), findsNothing);
      await _tap(tester, _chip('너무 길어요'));
      expect(_has(memberSendNotice), findsNothing);
      // 말투·내용을 고르면 보내기 전에 바로 보인다: 만든 분이 확인한 뒤 반영, 보낸 것은 만든 분께 보인다
      await _tap(tester, _chip('내용이 틀려요'));
      final notice = find.descendant(
        of: find.byType(BottomSheet),
        matching: _has('만든 분이 확인한 뒤에 반영돼요. 보내신 내용은 만든 분께 보여요.'),
      );
      expect(notice, findsOneWidget);
      expect(server.count('POST /messages/m2/feedback'), 0);
      // 안내는 고친 한 줄 칸 아래, 보내기 단추 위
      final field = find.descendant(of: find.byType(BottomSheet), matching: find.byType(TextField));
      final noticeY = tester.getTopLeft(notice).dy;
      expect(noticeY, greaterThan(tester.getTopLeft(field).dy));
      expect(noticeY, lessThan(tester.getTopLeft(find.text('보내기')).dy));
      // 말투·내용을 풀면 한 줄 칸과 함께 사라지고, 다시 고르면 보인다
      await _tap(tester, _chip('내용이 틀려요'));
      expect(_has(memberSendNotice), findsNothing);
      await _tap(tester, _chip('말투가 달라요'));
      expect(_has(memberSendNotice), findsOneWidget);
      await _tap(tester, _chip('말투가 달라요'));
      await _tap(tester, _chip('너무 길어요'));
      await _tap(tester, _chip('내용이 틀려요'));
      expect(_has(memberSendNotice), findsOneWidget);
      await tester.enterText(
        find.descendant(of: find.byType(BottomSheet), matching: find.byType(TextField)),
        '엄마는 부산 사람이었어요',
      );
      await _tap(tester, find.text('보내기'));
      expect((server.body('POST /messages/m2/feedback') as Map)['correction'], '엄마는 부산 사람이었어요');
      expect(_snack('지은님이 확인하면 반영돼요.'), findsOneWidget);
    });

    test('보낸 뒤 알릴 말', () {
      expect(feedbackThanks((tags: ['CONTENT'], correction: '부산', state: 'applied')), contains('기억에 넣었어요'));
      expect(feedbackThanks((tags: ['STYLE', 'CONTENT'], correction: 'x', state: 'applied')), contains('말투와 내용'));
      expect(feedbackThanks((tags: ['TOO_LONG'], correction: null, state: 'none')), contains('더 그분답게'));
      expect(ratingOf(['LIKE']), 'UP');
      expect(ratingOf(['VOICE', 'TOO_LONG']), 'DOWN');
    });
  });

  group('대화 한마디', () {
    testWidgets('대화 화면 위 작은 단추로 대화 전체에 한 줄을 남긴다', (tester) async {
      _phone(tester);
      final server = _Server({
        ..._chatRoutes(),
        'POST /conversations/c1/feedback': () => {'id': 'cf1', 'conversationId': 'c1', 'comment': 'x', 'reviewed': false},
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: _member())));
      await _settle(tester);
      // 함께 쓰는 가족에게도 보인다(기억 더 채우기는 없다)
      expect(find.text('기억 더 채우기'), findsNothing);
      await _tap(tester, find.text('한마디'));
      expect(find.text('이 대화 어땠나요?'), findsOneWidget);
      await tester.enterText(
        find.descendant(of: find.byType(AlertDialog), matching: find.byType(TextField)),
        '오늘은 정말 엄마랑 이야기하는 것 같았어요',
      );
      await tester.pump();
      await _tap(tester, find.text('남기기'));
      expect(server.body('POST /conversations/c1/feedback'), {'comment': '오늘은 정말 엄마랑 이야기하는 것 같았어요'});
      expect(_snack('남겨 주셔서 고마워요.'), findsOneWidget);
    });
  });

  group('대화를 나올 때 더 알려 주실 기억 하나', () {
    late GoRouter router;

    Widget app(Map<String, dynamic> persona) {
      router = GoRouter(
        initialLocation: '/p/p1',
        routes: [
          GoRoute(
            path: '/',
            builder: (context, state) => const Scaffold(body: Center(child: Text('대화 상대 목록'))),
            routes: [
              GoRoute(path: 'p/:id', builder: (context, state) => PersonaChatPage(persona: persona)),
              GoRoute(path: 'plans', builder: (context, state) => const Scaffold(body: Text('요금제'))),
            ],
          ),
        ],
      );
      return MaterialApp.router(theme: buildTheme(), routerConfig: router);
    }

    Map<String, Object? Function()> routes({List<Map<String, dynamic>>? questions, bool canAskFamily = true}) => {
          ..._chatRoutes(),
          'GET /personas/p1/gap-questions': () => {
                'role': 'owner',
                'questions': questions ?? [_question('g1', '할머니가 즐겨 부르시던 노래가 있었나요?')],
                'pendingAnswers': <Object>[],
                'pendingCorrections': <Object>[],
                'openCount': questions?.length ?? 1,
                'canAskFamily': canAskFamily,
              },
          'POST /personas/p1/gap-questions/g1/answer': () => {
                ..._question('g1', '할머니가 즐겨 부르시던 노래가 있었나요?'),
                'status': 'answered',
                'answerState': 'applied',
              },
        };

    Future<void> talk(WidgetTester tester) async {
      await tester.enterText(find.byType(TextField), '할머니 노래 불러 주세요');
      await tester.pump();
      await _tap(tester, find.byTooltip('보내기'));
    }

    Future<void> leave(WidgetTester tester) async {
      await tester.tap(find.byType(BackButton));
      await _settle(tester, frames: 30);
    }

    testWidgets('대화하는 동안에는 묻지 않고, 나와서 목록에 오면 하나만 묻는다. 답하면 바로 넣는다', (tester) async {
      _phone(tester);
      final server = _Server(routes());
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(app(_owner(gapCount: 1)));
      await _settle(tester);
      await talk(tester);
      expect(_has('아이고 노래는 무슨'), findsOneWidget);
      // 대화 중에는 질문을 읽지도 띄우지도 않는다(배지 숫자만)
      expect(server.count('GET /personas/p1/gap-questions'), 0);
      expect(_has('즐겨 부르시던 노래'), findsNothing);

      await leave(tester);
      expect(find.text('대화 상대 목록'), findsOneWidget);
      expect(server.count('GET /personas/p1/gap-questions'), 1);
      expect(_has('할머니 이야기, TalkTo가 하나만 여쭤볼게요'), findsOneWidget);
      expect(_has('할머니가 즐겨 부르시던 노래가 있었나요?'), findsOneWidget);
      for (final action in ['알려 주기', '잘 모르겠어요', '나중에', '가족에게 물어보기']) {
        expect(find.text(action), findsOneWidget, reason: action);
      }
      expect(_has(memberSendNotice), findsNothing); // 만든 분의 답은 바로 넣는다
      await tester.enterText(
        find.descendant(of: find.byType(BottomSheet), matching: find.byType(TextField)),
        '섬집 아기를 자주 부르셨어요',
      );
      await _tap(tester, find.text('알려 주기'));
      expect(server.body('POST /personas/p1/gap-questions/g1/answer'), {'text': '섬집 아기를 자주 부르셨어요'});
      expect(find.byType(BottomSheet), findsNothing);
      expect(_snack('다음 대화부터 기억해요'), findsOneWidget);
    });

    testWidgets('함께 쓰는 가족이 나올 때의 질문도 보내기 전에 만든 분이 확인한다고 알린다', (tester) async {
      _phone(tester);
      final server = _Server({
        ..._chatRoutes(),
        'GET /personas/p1/gap-questions': () => {
              'role': 'member',
              'questions': [_question('g2', '엄마가 아끼시던 물건이 있었나요?', askFamily: true)],
              'pendingAnswers': <Object>[],
              'pendingCorrections': <Object>[],
              'openCount': 1,
              'canAskFamily': false,
            },
        'POST /personas/p1/gap-questions/g2/answer': () => {
              ..._question('g2', '엄마가 아끼시던 물건이 있었나요?', askFamily: true),
              'status': 'answered',
              'answerState': 'pending_owner',
              'answeredByMe': true,
            },
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(app(_member()));
      await _settle(tester);
      await talk(tester);
      await leave(tester);
      expect(_has('지은님이 가족에게 물어봐 달라고 한 질문이에요'), findsOneWidget);
      expect(
        find.descendant(of: find.byType(BottomSheet), matching: _has(memberSendNotice)),
        findsOneWidget,
      );
      expect(find.text('가족에게 물어보기'), findsNothing);
      await tester.enterText(
        find.descendant(of: find.byType(BottomSheet), matching: find.byType(TextField)),
        '자개 반짇고리요',
      );
      await _tap(tester, find.text('알려 주기'));
      expect(server.body('POST /personas/p1/gap-questions/g2/answer'), {'text': '자개 반짇고리요'});
      expect(_snack('지은님이 확인하면 기억에 넣어요.'), findsOneWidget);
    });

    testWidgets("'나중에'는 서버에 아무것도 보내지 않고, 다음에 나올 때 같은 질문을 다시 묻지 않는다", (tester) async {
      _phone(tester);
      final server = _Server(routes(canAskFamily: false));
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(app(_owner()));
      await _settle(tester);
      await talk(tester);
      await leave(tester);
      // 함께 쓰는 가족이 없으면 가족에게 물어보기는 없다
      expect(find.text('가족에게 물어보기'), findsNothing);
      await _tap(tester, find.text('나중에'));
      expect(find.byType(BottomSheet), findsNothing);
      expect(server.log.where((k) => k.startsWith('POST /personas/p1/gap-questions')), isEmpty);

      router.go('/p/p1');
      await _settle(tester);
      await talk(tester);
      await leave(tester);
      expect(server.count('GET /personas/p1/gap-questions'), 2);
      expect(find.byType(BottomSheet), findsNothing);
      expect(GapLeave.later, contains('g1'));
    });

    testWidgets('대화를 나누지 않고 나오거나, 목록이 아닌 화면으로 가면 묻지 않는다', (tester) async {
      _phone(tester);
      final server = _Server(routes());
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(app(_owner()));
      await _settle(tester);
      await leave(tester);
      expect(server.count('GET /personas/p1/gap-questions'), 0);
      expect(find.byType(BottomSheet), findsNothing);

      router.go('/p/p1');
      await _settle(tester);
      await talk(tester);
      router.go('/plans');
      await _settle(tester, frames: 30);
      expect(find.text('요금제'), findsOneWidget);
      expect(find.byType(BottomSheet), findsNothing);
    });

    testWidgets("'잘 모르겠어요'는 질문을 닫는다", (tester) async {
      _phone(tester);
      final server = _Server({
        ...routes(),
        'POST /personas/p1/gap-questions/g1/dismiss': () => {
              ..._question('g1', '할머니가 즐겨 부르시던 노래가 있었나요?'),
              'status': 'dismissed',
            },
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(app(_owner()));
      await _settle(tester);
      await talk(tester);
      await leave(tester);
      await _tap(tester, find.text('잘 모르겠어요'));
      expect(server.count('POST /personas/p1/gap-questions/g1/dismiss'), 1);
      expect(find.byType(BottomSheet), findsNothing);
      expect(GapLeave.later, isEmpty);
    });
  });

  group('배지와 들어가는 곳', () {
    testWidgets('gapCount 는 머리글 ⋯ 의 작은 숫자로, 더 보기에는 더 알려 주실 기억', (tester) async {
      _phone(tester);
      ApiClient.useAdapterForTests(_Server(_chatRoutes()));
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: _owner(gapCount: 3))));
      await _settle(tester);
      expect(find.descendant(of: find.byType(Badge), matching: find.text('3')), findsOneWidget);
      await _tap(tester, find.byTooltip('더 보기'));
      expect(find.text('더 알려 주실 기억'), findsOneWidget);
      expect(_has('여쭤볼 것 3개'), findsOneWidget);
    });

    testWidgets('열린 질문이 없어도 가족이 보낸 것(gapWaiting)이 기다리면 숫자와 확인해 달라는 말', (tester) async {
      _phone(tester);
      ApiClient.useAdapterForTests(_Server(_chatRoutes()));
      await tester.pumpWidget(
        MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: _owner(gapWaiting: 2))),
      );
      await _settle(tester);
      expect(find.descendant(of: find.byType(Badge), matching: find.text('2')), findsOneWidget);
      await _tap(tester, find.byTooltip('더 보기'));
      expect(_has('가족이 보낸 것 2개 확인해 주세요'), findsOneWidget);
      expect(_has('대화하다 아직 모르는 이야기를 모아 둬요'), findsNothing);
    });

    testWidgets('열린 질문과 가족이 보낸 것이 함께 있으면 숫자를 더하고 둘 다 알린다', (tester) async {
      _phone(tester);
      ApiClient.useAdapterForTests(_Server(_chatRoutes()));
      await tester.pumpWidget(
        MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: _owner(gapCount: 3, gapWaiting: 1))),
      );
      await _settle(tester);
      expect(find.descendant(of: find.byType(Badge), matching: find.text('4')), findsOneWidget);
      await _tap(tester, find.byTooltip('더 보기'));
      expect(_has('가족이 보낸 것 1개 확인해 주세요 · 여쭤볼 것 3개'), findsOneWidget);
    });

    testWidgets('돌아가신 분 목소리 만들기 확인은 신청 자격과 유족 명단 화면을 함께 알려 준다(막지는 않는다)', (tester) async {
      _phone(tester);
      ApiClient.useAdapterForTests(_Server({
        ..._chatRoutes(),
        'GET /legal/pledge_deceased': () => {
              'key': 'pledge_deceased',
              'title': '돌아가신 분 — 유족 확인',
              'body': '위에서 고르고 적은 것과 아래 내용을 모두 확인해 주세요.',
            },
      }));
      await tester.pumpWidget(
        MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: {..._owner(), 'bereavedDeclared': false})),
      );
      await _settle(tester);
      await _tap(tester, find.byTooltip('더 보기'));
      await _tap(tester, find.text('목소리 만들기'));
      expect(find.text('목소리를 만들까요?'), findsOneWidget);
      expect(_has('아직 적지 않으셨으면 먼저 적어 주세요.'), findsOneWidget);
      expect(find.text('신청 자격과 유족 명단 적기'), findsOneWidget);
      // 확인 체크는 그대로 할 수 있다(이미 만든 고인 대화 상대는 막지 않는다 — 대표 답 #3)
      await _tap(tester, _has('위 내용을 모두 확인했어요'));
      expect(tester.widget<FilledButton>(find.widgetWithText(FilledButton, '만들기')).onPressed, isNotNull);
    });

    test('가족이 보낸 것은 만든 분에게만 센다', () {
      expect(gapWaitingOf(_owner(gapWaiting: 2)), 2);
      expect(gapWaitingOf({..._member(), 'gapWaiting': 2}), 0);
      expect(gapWaitingOf(null), 0);
      expect(gapOwnerLine(0, 0), isNull);
      expect(gapOwnerLine(2, 0), '여쭤볼 것 2개 · 아시는 것만 알려 주세요');
    });

    testWidgets('기억 더 채우기의 들어가는 카드도 가족이 보낸 것을 더해 보인다', (tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: const Scaffold(body: GapEntryCard(personaId: 'p1', waiting: 2)),
      ));
      expect(find.descendant(of: find.byType(Badge), matching: find.text('2')), findsOneWidget);
      expect(_has('가족이 보낸 것 2개 확인해 주세요.'), findsOneWidget);

      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: const Scaffold(body: GapEntryCard(personaId: 'p1', count: 3, waiting: 1)),
      ));
      expect(find.descendant(of: find.byType(Badge), matching: find.text('4')), findsOneWidget);
      expect(_has('아직 모르는 이야기도 3개 있어요'), findsOneWidget);

      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: const Scaffold(body: GapEntryCard(personaId: 'p1')),
      ));
      expect(find.descendant(of: find.byType(Badge), matching: find.byType(Text)), findsNothing);
      expect(_has('여기에 모아 둘게요'), findsOneWidget);
    });

    testWidgets('함께 쓰는 가족은 물어봐 달라고 한 질문이 없으면 들어가는 곳이 없다', (tester) async {
      _phone(tester);
      ApiClient.useAdapterForTests(_Server(_chatRoutes()));
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: _member())));
      await _settle(tester);
      expect(find.descendant(of: find.byType(Badge), matching: find.byType(Text)), findsNothing);
      await _tap(tester, find.byTooltip('더 보기'));
      expect(find.text('더 알려 주실 기억'), findsNothing);
    });
  });

  group('더 알려 주실 기억 화면', () {
    Map<String, Object? Function()> pageRoutes({required bool member}) => {
          'GET /personas/p1': () => member ? _member() : _owner(gapCount: 2),
          'GET /personas/p1/gap-questions': () => member
              ? {
                  'role': 'member',
                  'questions': [_question('g2', '할머니가 아끼시던 물건이 있었나요?', askFamily: true)],
                  'pendingAnswers': <Object>[],
                  'pendingCorrections': <Object>[],
                  'openCount': 1,
                  'canAskFamily': false,
                }
              : {
                  'role': 'owner',
                  'questions': [
                    _question('g1', '할머니가 즐겨 부르시던 노래가 있었나요?'),
                    _question('g2', '할머니가 아끼시던 물건이 있었나요?', askFamily: true),
                  ],
                  'pendingAnswers': [
                    {
                      ..._question('g3', '할머니가 좋아하시던 꽃은요?'),
                      'status': 'answered',
                      'answerState': 'pending_owner',
                      'answerText': '봄마다 진달래를 꺾어 오셨어요',
                      'answeredBy': {'name': '철수', 'relation': '아들'},
                      'answeredAt': '2026-10-21T01:00:00Z',
                    },
                  ],
                  'pendingCorrections': [
                    {
                      'id': 'f9',
                      'messageId': 'm2',
                      'tags': ['STYLE'],
                      'kinds': ['STYLE'],
                      'original': '밥은 먹었습니까?',
                      'correction': '밥은 묵었나',
                      'state': 'pending_owner',
                      'answeredBy': {'name': '철수', 'relation': '아들'},
                    },
                  ],
                  'openCount': 2,
                  'canAskFamily': true,
                },
          'POST /personas/p1/gap-questions/g1/answer': () => {
                ..._question('g1', '할머니가 즐겨 부르시던 노래가 있었나요?'),
                'status': 'answered',
                'answerState': 'applied',
              },
          'POST /personas/p1/gap-questions/g2/answer': () => {
                ..._question('g2', '할머니가 아끼시던 물건이 있었나요?', askFamily: true),
                'status': 'answered',
                'answerState': 'pending_owner',
                'answeredByMe': true,
                'answerText': '자개 반짇고리요',
              },
          'POST /personas/p1/gap-questions/g1/ask-family': () => _question('g1', '할머니가 즐겨 부르시던 노래가 있었나요?', askFamily: true),
          'POST /personas/p1/gap-questions/g3/approve': () => {
                ..._question('g3', '할머니가 좋아하시던 꽃은요?'),
                'status': 'answered',
                'answerState': 'applied',
              },
          'POST /personas/p1/corrections/f9/approve': () => {'id': 'f9', 'state': 'applied'},
          'POST /personas/p1/transcriptions': () => {'text': '섬집 아기를 자주 부르셨어요'},
        };

    Future<_Server> open(WidgetTester tester, {required bool member}) async {
      _phone(tester, height: 2400);
      final server = _Server(pageRoutes(member: member));
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const GapQuestionsPage(personaId: 'p1')));
      await _settle(tester);
      return server;
    }

    testWidgets('만든 분: 가족 답·가족 고침을 확인하고, 🎤로 답하고, 가족에게 물어본다', (tester) async {
      final server = await open(tester, member: false);
      expect(_has('할머니 이야기를 더 알려 주세요'), findsOneWidget);
      // 가족이 보낸 답과 고침
      expect(_has('철수(아들)님이 알려 줬어요'), findsOneWidget);
      expect(_has('봄마다 진달래를 꺾어 오셨어요'), findsOneWidget);
      expect(_has('철수(아들)님이 대답을 고쳐 줬어요 · 말투'), findsOneWidget);
      expect(_has('밥은 묵었나'), findsOneWidget);
      await _tap(tester, find.text('기억에 넣기'));
      expect(server.count('POST /personas/p1/gap-questions/g3/approve'), 1);
      // 만든 분이 본 답(글·답한 때)을 함께 보낸다(그 사이에 다른 가족이 다시 답했으면 서버가 409)
      expect(server.body('POST /personas/p1/gap-questions/g3/approve'), {
        'answerText': '봄마다 진달래를 꺾어 오셨어요',
        'answeredAt': '2026-10-21T01:00:00Z',
      });
      expect(_has('봄마다 진달래를'), findsNothing);
      await _tap(tester, find.text('반영하기'));
      expect(server.count('POST /personas/p1/corrections/f9/approve'), 1);
      // 만든 분이 본 고침을 함께 보낸다(가족이 그 사이에 바꿨으면 서버가 409)
      expect(server.body('POST /personas/p1/corrections/f9/approve'), {
        'correction': '밥은 묵었나',
        'tags': ['STYLE'],
      });
      expect(_has('대답을 고쳐 줬어요'), findsNothing);

      // 가족에게 이미 물어본 질문은 표시만, 아닌 질문은 물어보기 단추
      expect(_chip('가족에게도 물어보는 중'), findsOneWidget);
      expect(find.text('가족에게 물어보기'), findsOneWidget);
      await _tap(tester, find.text('가족에게 물어보기'));
      expect(server.body('POST /personas/p1/gap-questions/g1/ask-family'), {'ask': true});
      expect(_chip('가족에게도 물어보는 중'), findsNWidgets(2));

      // 답하기 → 🎤 로 말하면 글로 옮겨 칸에 넣고, 알려 주기로 보낸다
      await _tap(tester, find.text('답하기').first);
      // 만든 분의 답은 바로 기억이 되므로 확인 안내가 없다
      expect(_has(memberSendNotice), findsNothing);
      await _tap(tester, find.byTooltip('말로 적기'));
      expect(_has('말씀하신 내용을 글로 옮겨 드려요'), findsOneWidget);
      await _tap(tester, find.byTooltip('다 말했어요'));
      expect(server.count('POST /personas/p1/transcriptions'), 1);
      await _tap(tester, find.text('알려 주기'));
      expect(server.body('POST /personas/p1/gap-questions/g1/answer'), {'text': '섬집 아기를 자주 부르셨어요'});
      expect(_has('즐겨 부르시던 노래'), findsNothing);
      expect(_snack('다음 대화부터 기억해요'), findsOneWidget);
    });

    testWidgets('함께 쓰는 가족: 물어봐 달라고 한 질문만, 확인 단추·가족에게 물어보기 없이. 답은 만든 분이 확인한 뒤', (tester) async {
      final server = await open(tester, member: true);
      expect(_has('지은님이 여쭤봐 달라고 했어요'), findsOneWidget);
      expect(find.text('가족에게 물어보기'), findsNothing);
      expect(_chip('가족에게도 물어보는 중'), findsNothing);
      expect(find.text('기억에 넣기'), findsNothing);
      // 답 칸을 펴기 전에는 안내가 없고, 펴면 보내기 전에(알려 주기 단추 위에) 보인다
      expect(_has(memberSendNotice), findsNothing);
      await _tap(tester, find.text('답하기'));
      final notice = _has('만든 분이 확인한 뒤에 반영돼요. 보내신 내용은 만든 분께 보여요.');
      expect(notice, findsOneWidget);
      expect(tester.getTopLeft(notice).dy, greaterThan(tester.getTopLeft(find.byType(TextField)).dy));
      expect(tester.getTopLeft(notice).dy, lessThan(tester.getTopLeft(find.text('알려 주기')).dy));
      expect(server.log.where((k) => k.startsWith('POST')), isEmpty);
      await tester.enterText(find.byType(TextField), '자개 반짇고리요');
      await _tap(tester, find.text('알려 주기'));
      expect(server.body('POST /personas/p1/gap-questions/g2/answer'), {'text': '자개 반짇고리요'});
      expect(_snack('지은님이 확인하면 기억에 넣어요.'), findsOneWidget);
      // 보낸 답은 확인을 기다리는 내 답으로
      expect(find.text('확인을 기다리는 내 답'), findsOneWidget);
      expect(_has('자개 반짇고리요'), findsOneWidget);
    });

    testWidgets('만든 분: 가족이 그 사이에 고침을 바꿨으면(409) 반영하지 않고 목록을 새로 읽어 바뀐 고침을 보인다', (tester) async {
      Map<String, dynamic> correction(String text) => {
            'id': 'f9',
            'messageId': 'm2',
            'tags': ['STYLE'],
            'kinds': ['STYLE'],
            'original': '밥은 먹었습니까?',
            'correction': text,
            'state': 'pending_owner',
            'answeredBy': {'name': '철수', 'relation': '아들'},
          };
      var current = '밥은 묵었나';
      var changed = true;
      _phone(tester, height: 2400);
      final server = _Server({
        'GET /personas/p1': () => _owner(),
        'GET /personas/p1/gap-questions': () => {
              'role': 'owner',
              'questions': <Object>[],
              'pendingAnswers': <Object>[],
              'pendingCorrections': [correction(current)],
              'openCount': 0,
              'canAskFamily': true,
            },
        // 처음 누를 때는 가족이 이미 고친 뒤라 서버가 막는다. 새로 본 뒤에는 반영한다
        'POST /personas/p1/corrections/f9/approve': () => changed
            ? const _Fail(409, {'detail': '가족이 고침을 바꿨어요. 목록을 새로 보고 다시 확인해 주세요.'})
            : {'id': 'f9', 'state': 'applied'},
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const GapQuestionsPage(personaId: 'p1')));
      await _settle(tester);
      expect(_has('밥은 묵었나'), findsOneWidget);

      // 가족이 확인을 기다리는 동안 고친 말을 바꿨다
      current = '밥은 묵었나, 아가';
      await _tap(tester, find.text('반영하기'));
      expect(server.body('POST /personas/p1/corrections/f9/approve'), {
        'correction': '밥은 묵었나',
        'tags': ['STYLE'],
      });
      expect(_snack('가족이 고침을 바꿨어요'), findsOneWidget);
      expect(server.count('GET /personas/p1/gap-questions'), 2);
      expect(_has('밥은 묵었나, 아가'), findsOneWidget);

      // 바뀐 고침을 보고 다시 반영하면 그 글을 보낸다
      changed = false;
      await _tap(tester, find.text('반영하기'));
      expect(server.body('POST /personas/p1/corrections/f9/approve'), {
        'correction': '밥은 묵었나, 아가',
        'tags': ['STYLE'],
      });
      expect(_has('대답을 고쳐 줬어요'), findsNothing);
    });

    testWidgets('만든 분: 그 사이에 다른 가족이 다시 답했으면(409) 넣지 않고 목록을 새로 읽어 지금 답을 보인다', (tester) async {
      Map<String, dynamic> pending(String text, Map<String, dynamic> by, String at) => {
            ..._question('g3', '할머니가 좋아하시던 꽃은요?'),
            'status': 'answered',
            'answerState': 'pending_owner',
            'answerText': text,
            'answeredBy': by,
            'answeredAt': at,
          };
      var current = pending('봄마다 진달래를 꺾어 오셨어요', {'name': '철수', 'relation': '아들'}, '2026-10-21T01:00:00Z');
      late final _Server server;
      // 보낸 답(본 것)이 지금 답과 다르면 서버가 막는다
      Object? decide(String key, Object? done) {
        final seen = server.body(key) as Map?;
        final same = seen?['answerText'] == current['answerText'] && seen?['answeredAt'] == current['answeredAt'];
        return same ? done : const _Fail(409, {'detail': '가족 답이 그 사이에 바뀌었어요. 목록을 새로 보고 다시 확인해 주세요.'});
      }

      _phone(tester, height: 2400);
      server = _Server({
        'GET /personas/p1': () => _owner(),
        'GET /personas/p1/gap-questions': () => {
              'role': 'owner',
              'questions': <Object>[],
              'pendingAnswers': [current],
              'pendingCorrections': <Object>[],
              'openCount': 0,
              'canAskFamily': true,
            },
        'POST /personas/p1/gap-questions/g3/approve': () =>
            decide('POST /personas/p1/gap-questions/g3/approve', {...current, 'answerState': 'applied'}),
        'POST /personas/p1/gap-questions/g3/reject': () =>
            decide('POST /personas/p1/gap-questions/g3/reject', _question('g3', '할머니가 좋아하시던 꽃은요?')),
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const GapQuestionsPage(personaId: 'p1')));
      await _settle(tester);
      expect(_has('철수(아들)님이 알려 줬어요'), findsOneWidget);

      // 철수님이 함께 쓰기를 그만두어 질문이 다시 열렸고, 영희님이 다른 답을 보냈다
      current = pending('국화를 제일 좋아하셨어요', {'name': '영희', 'relation': '딸'}, '2026-10-21T03:00:00Z');
      await _tap(tester, find.text('기억에 넣기'));
      expect(server.body('POST /personas/p1/gap-questions/g3/approve'), {
        'answerText': '봄마다 진달래를 꺾어 오셨어요',
        'answeredAt': '2026-10-21T01:00:00Z',
      });
      expect(_snack('가족 답이 그 사이에 바뀌었어요'), findsOneWidget);
      expect(server.count('GET /personas/p1/gap-questions'), 2);
      expect(_has('봄마다 진달래를'), findsNothing);
      expect(_has('영희(딸)님이 알려 줬어요'), findsOneWidget);
      expect(_has('국화를 제일 좋아하셨어요'), findsOneWidget);

      // 새로 본 답을 넣지 않으면 그 글·답한 때를 보낸다. 질문은 다시 여쭤볼 것으로
      await _tap(tester, find.text('넣지 않기'));
      await _tap(tester, find.descendant(of: find.byType(AlertDialog), matching: find.text('넣지 않기')));
      expect(server.body('POST /personas/p1/gap-questions/g3/reject'), {
        'answerText': '국화를 제일 좋아하셨어요',
        'answeredAt': '2026-10-21T03:00:00Z',
      });
      expect(_has('국화를 제일'), findsNothing);
      expect(_has('할머니가 좋아하시던 꽃은요?'), findsOneWidget);
    });
  });

  test('공개 안내(랜딩 자주 묻는 질문)도 지금의 대답 평가(⋯)를 설명한다', () {
    final html = File('landing/index.html').readAsStringSync();
    // 없어진 '자연스러워요·어색해요' 단추를 안내하지 않는다
    expect(html.contains('자연스러워요'), isFalse);
    expect(html.contains('어색해요'), isFalse);
    expect(html, contains('대답 아래 작은 단추(⋯)'));
    expect(html, contains('‘말투가 달라요’·‘내용이 틀려요’'));
  });

  group('관리자 · 대화 평가', () {
    testWidgets('고른 것으로 거르고, 고친 말을 보고, 확인함을 켠다', (tester) async {
      tester.view.physicalSize = const Size(1200, 1600);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final server = _Server({
        'GET /admin/feedback': () => const _Page([
              {
                'id': 'f1',
                'messageId': 'm2',
                'userId': 'u1',
                'rating': 'DOWN',
                'tags': ['STYLE'],
                'comment': null,
                'reviewed': false,
                'createdAt': '2026-10-21T01:00:00Z',
                'correction': '밥은 묵었나',
                'state': 'applied',
                'personaTitle': '할머니',
                'reply': '밥은 먹었습니까?',
                'member': false,
              },
            ], 1),
        'PATCH /admin/feedback/f1': () => {'id': 'f1', 'reviewed': true},
        'GET /admin/conversation-feedback': () => const _Page([
              {
                'id': 'cf1',
                'conversationId': 'c1',
                'personaId': 'p1',
                'comment': '정말 할머니 같았어요',
                'reviewed': false,
                'createdAt': '2026-10-21T02:00:00Z',
                'userId': 'u2',
                'personaTitle': '할머니',
                'member': true,
              },
            ], 1),
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const FeedbackAdminPage()));
      await _settle(tester);
      expect(server.queries['GET /admin/feedback']!.last, {'reviewed': false, 'page': 1, 'limit': 50});
      expect(_has('고친 말: 밥은 묵었나'), findsOneWidget);
      expect(_has('대답: 밥은 먹었습니까?'), findsOneWidget);
      expect(_has('대화 상대: 할머니'), findsOneWidget);
      expect(_chip('반영함'), findsOneWidget);

      // 거르는 칩(위)과 줄의 꼬리표(아래)가 같은 이름이다
      await _tap(tester, _chip('말투가 달라요').first);
      expect(server.queries['GET /admin/feedback']!.last['tag'], 'STYLE');
      await _tap(tester, _chip('고친 말 있는 것만'));
      expect(server.queries['GET /admin/feedback']!.last['corrected'], true);

      await _tap(tester, find.byType(Switch));
      expect(server.body('PATCH /admin/feedback/f1'), {'reviewed': true});
      expect(tester.widget<Switch>(find.byType(Switch)).value, isTrue);

      await _tap(tester, _chip('대화 한마디'));
      expect(server.count('GET /admin/conversation-feedback'), 1);
      expect(_has('정말 할머니 같았어요'), findsOneWidget);
      expect(_has('함께 쓰는 가족'), findsOneWidget);
    });
  });
}
