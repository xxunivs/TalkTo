import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/session.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/auth/auth_callback_page.dart';
import 'package:talkto_fe/features/auth/login_page.dart';
import 'package:talkto_fe/features/billing/plans_page.dart';
import 'package:talkto_fe/features/chat/persona_chat_page.dart';
import 'package:talkto_fe/features/personas/persona_flow_page.dart';
import 'package:talkto_fe/features/personas/recordings_page.dart';
import 'package:talkto_fe/features/personas/steps/questions_step.dart';
import 'package:talkto_fe/shared/widgets/voice_input.dart';

// 시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다).

/// 서버가 오류로 답할 때.
class _Fail {
  final int status;
  final Map<String, dynamic> body;

  const _Fail(this.status, this.body);
}

/// 서버 대신 답한다. 'GET /personas/p1' 처럼 메서드와 경로로 고른다. 받은 요청을 차례로 적어 둔다.
/// 정해 두지 않은 주소는 404.
class _FakeServer implements HttpClientAdapter {
  final Map<String, Object? Function()> routes;
  final log = <String>[];

  _FakeServer(this.routes);

  int count(String key) => log.where((k) => k == key).length;

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    if (requestStream != null) {
      await for (final _ in requestStream) {}
    }
    final key = '${options.method} ${options.path}';
    log.add(key);
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

String _plain(Widget w) {
  if (w is Text) return (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', '');
  if (w is RichText) return w.text.toPlainText().replaceAll('⁠', '');
  return '';
}

Finder _has(String part) => find.byWidgetPredicate((w) => w is Text && _plain(w).contains(part));

/// 가짜 서버 응답(Future)을 받고 화면을 그린다. 도는 표시(로딩)가 있어도 멈추지 않게 정해진 만큼만 넘긴다.
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

/// 만들 때 질문(필수 아님 하나)과 녹음 파일 크기 상한. 이 파일의 시험이 모두 같은 값을 쓴다(앱이 기억해 둔다).
const _meta = {
  'questions': [
    {
      'id': 'C-03',
      'section': '성격',
      'type': 'single',
      'required': false,
      'title': '상대가 힘들어하면 그분은 어떻게 하셨어요?',
      'options': ['같이 울고 웃는다', '말없이 곁에 있다'],
    },
  ],
  'sections': <Object>[],
  'relations': ['할머니'],
  'myRelations': ['손자'],
  'statuses': <String, dynamic>{},
  'maxUploadMb': 300,
};

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    ApiClient.onUnauthorized = null;
    ApiClient.onTokens = null;
    ApiClient.onConsentRequired = null;
    ApiClient.clearToken();
  });

  tearDown(() {
    session.user = null;
  });

  group('로그인 위조 막기(#/auth?ticket=)', () {
    Widget app(String location) => MaterialApp.router(
          theme: buildTheme(),
          routerConfig: GoRouter(
            initialLocation: location,
            routes: [
              GoRoute(path: '/', builder: (context, state) => const Scaffold(body: Text('첫 화면'))),
              GoRoute(
                path: '/login',
                builder: (context, state) => Scaffold(body: Text('로그인 ${state.uri.queryParameters['error']}')),
              ),
              GoRoute(
                path: '/auth',
                builder: (context, state) => AuthCallbackPage(ticket: state.uri.queryParameters['ticket']),
              ),
            ],
          ),
        );

    testWidgets('이미 로그인했으면 받은 표를 쓰지 않는다(남의 계정으로 바뀌지 않는다)', (tester) async {
      final server = _FakeServer({
        'POST /auth/oauth/exchange': () => {'bearerToken': 'other', 'refreshToken': 'other-r'},
      });
      ApiClient.useAdapterForTests(server);
      session.user = {'id': 'me', 'name': '나', 'consentsNeeded': <String>[]};
      await tester.pumpWidget(app('/auth?ticket=ATTACKER_TICKET'));
      await _settle(tester);
      expect(find.text('첫 화면'), findsOneWidget);
      expect(server.log, isNot(contains('POST /auth/oauth/exchange')));
      expect(session.user?['id'], 'me');
    });

    testWidgets('로그인 전이면 표를 바꾸고, 실패는 정해진 이름(code)으로 로그인 화면에 넘긴다', (tester) async {
      ApiClient.useAdapterForTests(_FakeServer({
        'POST /auth/oauth/exchange': () => const _Fail(401, {'detail': '로그인 시간이 지났어요. 다시 시도해 주세요.'}),
      }));
      await tester.pumpWidget(app('/auth?ticket=OLD'));
      await _settle(tester);
      expect(find.text('로그인 expired'), findsOneWidget);

      await tester.pumpWidget(app('/auth'));
      await _settle(tester);
      expect(find.text('로그인 state'), findsOneWidget);

      final server = _FakeServer({
        'POST /auth/oauth/exchange': () => {'bearerToken': 'b', 'refreshToken': 'r'},
        'GET /auth/me': () => {'id': 'u1', 'name': '시험', 'consentsNeeded': <String>[]},
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(app('/auth?ticket=GOOD'));
      await _settle(tester);
      expect(find.text('첫 화면'), findsOneWidget);
      expect(session.user?['id'], 'u1');
    });
  });

  group('주소에 붙은 글을 그대로 보이지 않는다', () {
    test('로그인: 정해 둔 이름과 지금 서버 문장만 알아보고, 나머지는 한 가지 글로', () {
      expect(loginErrorText(null), isNull);
      expect(loginErrorText(''), isNull);
      expect(loginErrorText('cancelled'), '로그인을 취소했어요.');
      expect(loginErrorText('expired'), '로그인 시간이 지났어요. 다시 시도해 주세요.');
      expect(loginErrorText('카카오 로그인에 실패했어요. 잠시 후 다시 시도해 주세요.'),
          '카카오 로그인에 실패했어요. 잠시 후 다시 시도해 주세요.');
      expect(loginErrorText('사용할 수 없는 계정이에요.'), '사용할 수 없는 계정이에요.');
      const phishing = '보안 점검으로 계정이 잠겼어요. 010-0000-0000 으로 인증번호를 알려 주세요';
      expect(loginErrorText(phishing), '로그인하지 못했어요. 다시 시도해 주세요.');
    });

    testWidgets('로그인 화면은 남이 만든 주소의 글 대신 정해진 글을 보인다', (tester) async {
      const phishing = '보안 점검으로 계정이 잠겼어요. 010-0000-0000 으로 연락해 주세요';
      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: LoginPage(error: phishing, loadProviders: () async => const {}, loadBusiness: () async => const {}),
      ));
      await tester.pump();
      expect(_has('010-0000-0000'), findsNothing);
      expect(_has('로그인하지 못했어요. 다시 시도해 주세요.'), findsOneWidget);
    });

    test('결제: 정해 둔 이름과 지금 서버 문장만 알아보고, 나머지는 한 가지 글로', () {
      expect(paymentErrorText(null), isNull);
      expect(paymentErrorText('cancelled'), '결제를 취소했어요.');
      expect(paymentErrorText('지금은 결제할 수 없어요. 잠시 뒤 다시 해 주세요.'), '지금은 결제할 수 없어요. 잠시 뒤 다시 해 주세요.');
      expect(paymentErrorText('환불 계좌를 010-0000-0000 로 보내 주세요'), '결제하지 못했어요. 요금제 화면에서 다시 해 주세요.');
    });

    Map<String, dynamic> me(String status) => {
          'paymentsOpen': false,
          'prelaunchConversations': null,
          'plans': [
            {'id': 'family', 'name': '가족', 'price': 29900, 'conversations': 200},
          ],
          'personas': [
            {
              'personaId': 'a',
              'title': '할머니',
              'step': 'READY',
              'status': status,
              'active': true,
              'plan': status == 'ACTIVE' ? 'family' : 'trial',
              'planName': '가족',
              'periodEnd': '2026-11-18T00:00:00Z',
              'conversationsLimit': null,
              'conversationsLeft': null,
              'deceased': false,
            },
          ],
        };

    Future<void> showPlans(WidgetTester tester, Map<String, dynamic> data, {String? result, String? error}) async {
      tester.view.physicalSize = const Size(1280, 2400);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: PlansPage(
          key: UniqueKey(),
          personaId: 'a',
          result: result,
          error: error,
          loadMe: () async => data,
          loadPayments: () async => const <Map<String, dynamic>>[],
        ),
      ));
      await tester.pumpAndSettle();
    }

    testWidgets('결제했어요는 새로 읽은 구독이 결제된 상태일 때만, 결제 오류 글은 정해진 글만', (tester) async {
      // 결제가 열리기 전(PRELAUNCH)에 누가 ?result=success 주소를 보내도 '결제했어요'를 보이지 않는다
      await showPlans(tester, me('PRELAUNCH'), result: 'success');
      expect(_has('결제했어요. 고마워요.'), findsNothing);

      await showPlans(tester, me('ACTIVE'), result: 'success');
      expect(_has('결제했어요. 고마워요.'), findsOneWidget);

      await showPlans(tester, me('PRELAUNCH'), error: '환불 계좌를 010-0000-0000 로 보내 주세요');
      expect(_has('010-0000-0000'), findsNothing);
      expect(_has('결제하지 못했어요. 요금제 화면에서 다시 해 주세요.'), findsOneWidget);
    });
  });

  group('로그인 유지', () {
    testWidgets('앱을 열 때 서버를 바꾸는 중(5xx)이면 조금 뒤 한 번 더 읽어 로그인을 이어 간다', (tester) async {
      SharedPreferences.setMockInitialValues({'talkto.access': 'a1', 'talkto.refresh': 'r1'});
      Session.retryDelay = Duration.zero;
      var calls = 0;
      final server = _FakeServer({
        'GET /auth/me': () => ++calls == 1
            ? const _Fail(503, {'detail': '잠시만요'})
            : {'id': 'u1', 'name': '시험', 'consentsNeeded': <String>[]},
      });
      ApiClient.useAdapterForTests(server);
      final s = Session();
      await tester.runAsync(s.init);
      expect(server.count('GET /auth/me'), 2);
      expect(s.loggedIn, isTrue);
    });

    testWidgets('토큰 갱신이 잠깐 실패하면(401 → 갱신 5xx) 저장한 로그인을 지우지 않는다', (tester) async {
      SharedPreferences.setMockInitialValues({'talkto.access': 'a1', 'talkto.refresh': 'r1'});
      Session.retryDelay = Duration.zero;
      final server = _FakeServer({
        'GET /auth/me': () => const _Fail(401, {'detail': '로그인이 풀렸어요.'}),
        'POST /auth/refresh': () => const _Fail(503, {'detail': '잠시만요'}),
      });
      ApiClient.useAdapterForTests(server);
      final s = Session();
      await tester.runAsync(s.init);
      expect(s.loggedIn, isFalse);
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('talkto.access'), 'a1');
      expect(prefs.getString('talkto.refresh'), 'r1');
    });

    testWidgets('갱신 토큰이 정말 만료(401)면 저장한 로그인을 지운다', (tester) async {
      SharedPreferences.setMockInitialValues({'talkto.access': 'a1', 'talkto.refresh': 'r1'});
      Session.retryDelay = Duration.zero;
      ApiClient.useAdapterForTests(_FakeServer({
        'GET /auth/me': () => const _Fail(401, {'detail': '로그인이 풀렸어요.'}),
        'POST /auth/refresh': () => const _Fail(401, {'detail': '다시 로그인해 주세요.'}),
      }));
      final s = Session();
      await tester.runAsync(s.init);
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('talkto.access'), isNull);
    });

    testWidgets('이 기기에서만 나가기는 서버 로그아웃(모든 기기)을 부르지 않는다', (tester) async {
      final server = _FakeServer({'POST /auth/logout': () => null});
      ApiClient.useAdapterForTests(server);
      final s = Session()..user = {'id': 'u1', 'name': '시험'};
      await tester.runAsync(s.signOutHere);
      expect(s.loggedIn, isFalse);
      expect(server.log, isEmpty);

      s.user = {'id': 'u1', 'name': '시험'};
      await tester.runAsync(s.logout);
      expect(server.log, ['POST /auth/logout']);
      expect(s.loggedIn, isFalse);
    });
  });

  group('녹음 올리기 전 확인', () {
    const mb = 1024 * 1024;
    ({List<({String name, int size})> keep, String? message}) screen(
      List<({String name, int size})> files, {
      int existing = 0,
    }) =>
        screenRecordings(files, name: (f) => f.name, size: (f) => f.size, existing: existing, maxBytes: 300 * mb);

    test('너무 큰 파일은 빼고 이름을 알린다(나머지는 올린다)', () {
      final result = screen([(name: 'a.m4a', size: 5 * mb), (name: '가족영상.mp4', size: 900 * mb)]);
      expect([for (final f in result.keep) f.name], ['a.m4a']);
      expect(result.message, '파일 하나는 300MB까지예요: 가족영상.mp4');
    });

    test('이미 올린 것과 합쳐 20개를 넘으면 아무것도 올리지 않고 남은 개수를 알린다', () {
      final files = [for (var i = 0; i < 3; i++) (name: '$i.m4a', size: mb)];
      final over = screen(files, existing: 18);
      expect(over.keep, isEmpty);
      expect(over.message, '녹음은 20개까지 올릴 수 있어요. 지금은 2개 더 올릴 수 있어요.');
      final full = screen(files, existing: 20);
      expect(full.keep, isEmpty);
      expect(full.message, contains('필요 없는 녹음을 빼고'));
      final ok = screen(files, existing: 17);
      expect(ok.keep, hasLength(3));
      expect(ok.message, isNull);
    });
  });

  group('말로 보내기 녹음', () {
    test('브라우저가 녹음할 수 있는 형식을 고른다(아이폰 사파리 18.4 전은 m4a, 그마저 없으면 wav)', () async {
      Future<VoiceFormat> pick(Set<AudioEncoder> ok) => pickVoiceFormat((e) async => ok.contains(e), web: true);
      expect((await pick({AudioEncoder.opus, AudioEncoder.aacLc})).filename, 'voice.webm');
      final safari = await pick({AudioEncoder.aacLc});
      expect(safari.encoder, AudioEncoder.aacLc);
      expect(safari.filename, 'voice.m4a');
      final none = await pick({});
      expect(none.encoder, AudioEncoder.wav);
      expect(none.filename, 'voice.wav');
      // 알아보다 오류가 나도 다음 형식으로
      final broken = await pickVoiceFormat((e) async => throw Exception('x'), web: true);
      expect(broken.filename, 'voice.wav');
      expect((await pickVoiceFormat((e) async => false, web: false)).filename, 'voice.m4a');
    });

    test('울림·잡음 줄이기를 켜고 말소리에 맞게 작게 담는다', () {
      final config = voiceRecordConfig(AudioEncoder.opus);
      expect(config.echoCancel, isTrue);
      expect(config.noiseSuppress, isTrue);
      expect(config.autoGain, isTrue);
      expect(config.numChannels, 1);
      expect(config.bitRate, 32000);
      expect(voiceRecordConfig(AudioEncoder.wav).sampleRate, 16000);
      expect(maxVoiceSeconds, 120);
    });
  });

  group('처음 읽기에 실패하면 다시 불러오기', () {
    Map<String, dynamic> uploadDetail(List<Map<String, dynamic>> recordings) => {
          'id': 'p1',
          'step': 'UPLOAD',
          'addressTerm': '할머니',
          'relation': '할머니',
          'basics': {'status': 'deceased'},
          'consents': {'recordingPledge': true},
          'jobs': <String, dynamic>{},
          'recordings': recordings,
        };

    testWidgets('대화 상대 화면: 못 읽으면 다시 불러오기 단추가 있고, 누르면 다시 읽는다', (tester) async {
      _phone(tester);
      var fail = true;
      final server = _FakeServer({
        'GET /personas/p1': () => fail ? const _Fail(400, {'detail': '잠시 문제가 생겼어요.'}) : uploadDetail(const []),
        'GET /intake/meta': () => _meta,
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const PersonaFlowPage(personaId: 'p1')));
      await _settle(tester);
      expect(find.text('다시 불러오기'), findsOneWidget);
      fail = false;
      await tester.tap(find.text('다시 불러오기'));
      await _settle(tester);
      expect(_has('녹음을 올려주세요'), findsOneWidget);
    });

    testWidgets('대화 상대 화면: 서버를 바꾸는 중(5xx)이면 조금 뒤 저절로 한 번 더 읽는다', (tester) async {
      _phone(tester);
      var calls = 0;
      ApiClient.useAdapterForTests(_FakeServer({
        'GET /personas/p1': () => ++calls == 1 ? const _Fail(502, {'detail': 'x'}) : uploadDetail(const []),
        'GET /intake/meta': () => _meta,
      }));
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const PersonaFlowPage(personaId: 'p1')));
      await _settle(tester);
      expect(find.text('다시 불러오기'), findsOneWidget);
      await tester.pump(const Duration(seconds: 3));
      await _settle(tester);
      expect(calls, 2);
      expect(_has('녹음을 올려주세요'), findsOneWidget);
    });

    testWidgets('녹음 빼기: 두 번 눌러도 한 번만 지우고, 위 화면을 새로 읽어 다시 그려져도 지운 녹음이 돌아오지 않는다',
        (tester) async {
      _phone(tester);
      Map<String, dynamic> rec(String id) =>
          {'id': id, 'originalName': '$id.m4a', 'sizeBytes': 2048, 'status': 'UPLOADED', 'phoneBand': false};
      var recordings = [rec('r1'), rec('r2')];
      final server = _FakeServer({
        'GET /personas/p1': () => uploadDetail(recordings),
        'GET /intake/meta': () => _meta,
        'DELETE /personas/p1/recordings/r1': () {
          recordings = [rec('r2')];
          return recordings;
        },
      });
      ApiClient.useAdapterForTests(server);
      var personaId = 'p1';
      late StateSetter rebuild;
      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: StatefulBuilder(builder: (context, setState) {
          rebuild = setState;
          // 위에서 새 위젯으로 다시 그리는 것(창 크기·주소가 바뀔 때)과 같다
          return PersonaFlowPage(key: const ValueKey('p1'), personaId: personaId);
        }),
      ));
      await _settle(tester);
      expect(find.text('r1.m4a'), findsOneWidget);
      final remove = find.byTooltip('빼기').first;
      await tester.tap(remove);
      await tester.tap(remove);
      await _settle(tester);
      expect(server.count('DELETE /personas/p1/recordings/r1'), 1);
      expect(server.count('GET /personas/p1'), 2); // 지운 뒤 위 화면도 새로 읽었다
      expect(find.text('r1.m4a'), findsNothing);
      rebuild(() => personaId = 'p1');
      await _settle(tester);
      expect(find.text('r1.m4a'), findsNothing);
      expect(find.text('r2.m4a'), findsOneWidget);
    });

    testWidgets('대화 화면: 대화를 못 읽으면 보내기·마이크를 끄고, 다시 불러오기로 이어 간다', (tester) async {
      _phone(tester, height: 900);
      var fail = true;
      ApiClient.useAdapterForTests(_FakeServer({
        'POST /personas/p1/conversation': () => fail ? const _Fail(400, {'detail': '잠시 문제가 생겼어요.'}) : {'conversationId': 'c1'},
        'GET /conversations/c1': () => {'messages': <Object>[]},
        'GET /billing/personas/p1': () => {'personaId': 'p1', 'status': 'PRELAUNCH', 'active': true},
      }));
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
      expect(find.text('다시 불러오기'), findsOneWidget);
      await tester.enterText(find.byType(TextField), '안녕하세요');
      await tester.pump();
      IconButton button(String tooltip) =>
          tester.widget<IconButton>(find.ancestor(of: find.byTooltip(tooltip), matching: find.byType(IconButton)).first);
      expect(button('보내기').onPressed, isNull);
      expect(button('말로 하기').onPressed, isNull);

      fail = false;
      await tester.tap(find.text('다시 불러오기'));
      await _settle(tester);
      expect(find.text('다시 불러오기'), findsNothing);
      expect(button('보내기').onPressed, isNotNull);
      expect(button('말로 하기').onPressed, isNotNull);
    });
  });

  group('질문 답하기', () {
    Map<String, dynamic> detail() => {
          'id': 'p1',
          'step': 'QUESTIONS',
          'addressTerm': '할머니',
          'relation': '할머니',
          'pledgeDoc': 'pledge_deceased',
          'basics': {'status': 'deceased'},
          'consents': <String, dynamic>{},
          'answers': <String, dynamic>{},
          'jobs': <String, dynamic>{},
          'subjectVoices': <String>[],
        };

    _FakeServer server({bool saveFails = false}) => _FakeServer({
          'GET /intake/meta': () => _meta,
          'GET /personas/p1/phrase-suggestions': () => <String>[],
          'GET /legal/pledge_deceased': () => {'title': '만들기 전 확인', 'body': '확인 본문'},
          // 돌아가신 분: 앞서 적어 둔 신청 자격·유족 명단(만들기 전 확인 위에 채워진다)
          'GET /personas/p1/bereaved': () => {'applicantRelation': 'grandchild', 'members': <Object>[], 'noneLeft': true},
          'PUT /personas/p1/answers': () => saveFails ? const _Fail(503, {'detail': '잠시만요'}) : null,
          'POST /personas/p1/back-to-speakers': () => null,
        });

    Future<void> open(WidgetTester tester, {VoidCallback? onChanged}) async {
      _phone(tester, height: 900);
      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: QuestionsStep(detail: detail(), onChanged: onChanged ?? () {}),
      ));
      await _settle(tester);
    }

    testWidgets('확인에 체크하지 않고 만들기를 누르면 그 자리에서 알리고 확인 칸으로 내려간다', (tester) async {
      ApiClient.useAdapterForTests(server());
      await open(tester);
      await tester.tap(find.text('대화 상대 만들기'));
      await _settle(tester);
      expect(find.descendant(of: find.byType(SnackBar), matching: _has('확인 내용을 읽고 체크해 주세요')), findsOneWidget);
      expect(tester.getTopLeft(_has('위 내용을 모두 확인했어요')).dy, lessThan(900));
    });

    testWidgets("적고 바로 '이전'을 두 번 눌러도: 답을 먼저 저장하고, 단계는 한 번만 옮긴다", (tester) async {
      final fake = server();
      ApiClient.useAdapterForTests(fake);
      var changed = 0;
      await open(tester, onChanged: () => changed++);
      await tester.tap(_has('같이 울고 웃는다'));
      await tester.pump();
      final back = find.text('이전');
      await tester.tap(back);
      await tester.tap(back);
      await _settle(tester);
      expect(fake.count('POST /personas/p1/back-to-speakers'), 1);
      expect(fake.log.indexOf('PUT /personas/p1/answers'), lessThan(fake.log.indexOf('POST /personas/p1/back-to-speakers')));
      expect(changed, 1);
    });

    testWidgets('저절로 저장하지 못하면 알리고 다시 시도할 수 있다', (tester) async {
      ApiClient.useAdapterForTests(server(saveFails: true));
      await open(tester);
      await tester.tap(_has('같이 울고 웃는다'));
      await tester.pump(const Duration(milliseconds: 800));
      await _settle(tester);
      expect(_has('적은 답을 저장하지 못했어요.'), findsOneWidget);
      ApiClient.useAdapterForTests(server());
      await tester.tap(find.text('다시 시도'));
      await _settle(tester);
      expect(_has('적은 답을 저장하지 못했어요.'), findsNothing);
    });

    testWidgets('적고 바로 화면을 떠나도 마지막 답을 저장한다', (tester) async {
      final fake = server();
      ApiClient.useAdapterForTests(fake);
      await open(tester);
      await tester.tap(_has('같이 울고 웃는다'));
      await tester.pump();
      await tester.pumpWidget(const MaterialApp(home: Text('다른 화면')));
      await _settle(tester);
      expect(fake.count('PUT /personas/p1/answers'), 1);
    });
  });
}
