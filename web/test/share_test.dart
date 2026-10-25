import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:talkto_fe/core/analytics.dart';
import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/session.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/auth/consent_page.dart';
import 'package:talkto_fe/features/billing/data/billing_api.dart';
import 'package:talkto_fe/features/chat/persona_chat_page.dart';
import 'package:talkto_fe/features/legal/legal_api.dart';
import 'package:talkto_fe/features/share/family_page.dart';
import 'package:talkto_fe/features/share/join_page.dart';
import 'package:talkto_fe/features/share/member_profile_sheet.dart';
import 'package:talkto_fe/features/share/pending_invite.dart';
import 'package:talkto_fe/features/share/roles.dart';
import 'package:talkto_fe/features/share/share_api.dart';

// 시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다).

const _token = 'tok_TEST1234abcdEFGH';

/// 서버가 오류로 답할 때.
class _Fail {
  final int status;
  final Map<String, dynamic> body;

  const _Fail(this.status, this.body);
}

/// 서버 대신 답한다. 'POST /invites/preview' 처럼 메서드와 경로로 고르고, 받은 몸통을 적어 둔다.
/// 정해 두지 않은 주소는 주인 아닌 사람이 받는 것과 같은 404.
class _FakeServer implements HttpClientAdapter {
  final Map<String, Object? Function(dynamic body)> routes;
  final log = <String>[];
  final bodies = <String, dynamic>{};

  _FakeServer(this.routes);

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    final chunks = <int>[];
    if (requestStream != null) {
      await for (final chunk in requestStream) {
        chunks.addAll(chunk);
      }
    }
    final body = chunks.isEmpty ? null : jsonDecode(utf8.decode(chunks));
    final key = '${options.method} ${options.path}';
    log.add(key);
    bodies[key] = body;
    final handler = routes[key];
    final result = handler == null ? const _Fail(404, {'detail': '대화 상대를 찾지 못했어요.'}) : handler(body);
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

/// 화면 글은 단어 중간에서 끊기지 않게 이음표(keepAll)가 들어 있어, 빼고 찾는다.
String _plain(Widget w) {
  if (w is Text) return (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', '');
  if (w is RichText) return w.text.toPlainText().replaceAll('⁠', '');
  return '';
}

Finder _shown(String text) => find.byWidgetPredicate((w) => w is Text && _plain(w) == text);
Finder _has(String part) => find.byWidgetPredicate((w) => w is Text && _plain(w).contains(part));

/// 가짜 서버 응답(Future)을 다 받고 화면을 그린다.
Future<void> _settle(WidgetTester tester) async {
  for (var i = 0; i < 8; i++) {
    await tester.pump(const Duration(milliseconds: 20));
  }
  await tester.pumpAndSettle();
}

Future<void> _tap(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await _settle(tester);
}

void _phone(WidgetTester tester, {double height = 900}) {
  tester.view.physicalSize = Size(360, height);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
}

const _memberLabel = '가족 공유 안내를 읽었고, 만든 분께 내 계정 이름과 내가 적은 이름·관계·부르는 말·말 높이, 들어온 날이 보이는 데 동의해요.';

Map<String, dynamic> _resolveJoin({Map<String, dynamic>? prefill}) => {
      'state': 'join',
      'personaId': 'p1',
      'ownerName': '지은',
      'addressTerm': prefill?['addressTerm'],
      'ownerTitle': '할머니',
      'isSelf': false,
      'origin': null,
      'prefill': prefill ?? const {},
      'fromOwner': prefill != null && prefill.isNotEmpty,
      'relations': const ['아들', '딸', '손자', '사위'],
      'addressSuggestions': const {
        '아들': ['엄마', '어머니'],
        '사위': ['장모님', '어머님'],
      },
      'consent': const {'type': 'family_share_member', 'doc': 'family_share', 'label': _memberLabel},
      'voiceShareable': true,
    };

Widget _joinApp(String token) {
  final router = GoRouter(
    initialLocation: '/join/$token',
    routes: [
      GoRoute(path: '/', builder: (context, state) => const Scaffold(body: Text('첫 화면'))),
      GoRoute(path: '/login', builder: (context, state) => const Scaffold(body: Text('로그인 화면'))),
      GoRoute(
        path: '/join/:token',
        builder: (context, state) => JoinPage(
          token: state.pathParameters['token']!,
          loadProviders: () async => const {'kakao': true, 'google': true},
          inApp: false,
        ),
      ),
      GoRoute(
        path: '/p/:id',
        builder: (context, state) => Scaffold(body: Text('대화 ${state.pathParameters['id']}')),
      ),
    ],
  );
  return MaterialApp.router(theme: buildTheme(), routerConfig: router);
}

FilledButton _filled(WidgetTester tester, String label) =>
    tester.widget<FilledButton>(find.ancestor(of: _shown(label), matching: find.byType(FilledButton)));

void main() {
  late List<String> events;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    events = [];
    Analytics.sender = (name, props) async => events.add('$name ${jsonEncode(props)}');
    PendingInvite.token = null;
    PendingInvite.ownerName = null;
    PendingInvite.savedAt = null;
    LegalApi.forget();
    LinkSharing.canShare = () => false;
    LinkSharing.share = ({required String url, String? text}) async => false;
    LinkSharing.copy = (text) async => true;
  });

  tearDown(() {
    session.user = null;
  });

  group('주소·통계·작은 도우미', () {
    test('초대 열쇠는 통계 화면 이름에 남지 않는다', () {
      expect(Analytics.screenName('/join/abc'), 'join');
      expect(Analytics.screenName('/join/$_token'), 'join');
      expect(Analytics.screenName('/p/x/family'), 'persona_family');
    });

    test('기다리는 초대: 열쇠 모양만 받고, 적어 두고, 하루 지나면 버린다', () async {
      await PendingInvite.save('../p/1');
      expect(PendingInvite.token, isNull);
      await PendingInvite.save(_token, owner: '지은');
      expect(PendingInvite.path, '/join/$_token');
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString(PendingInvite.key), contains(_token));
      // 이름 없이 다시 적어도 이름은 남는다
      await PendingInvite.save(_token);
      expect(PendingInvite.ownerName, '지은');

      PendingInvite.token = null;
      await PendingInvite.load();
      expect(PendingInvite.token, _token);
      expect(PendingInvite.ownerName, '지은');

      final old = DateTime.now().subtract(const Duration(hours: 25)).toIso8601String();
      SharedPreferences.setMockInitialValues({
        PendingInvite.key: jsonEncode({'token': _token, 'savedAt': old, 'ownerName': '지은'}),
      });
      PendingInvite.token = null;
      await PendingInvite.load();
      expect(PendingInvite.token, isNull);

      await PendingInvite.save(_token);
      await PendingInvite.clear();
      expect(PendingInvite.path, isNull);
      expect((await SharedPreferences.getInstance()).getString(PendingInvite.key), isNull);

      // 하루가 지난 것은 이어 갈 주소를 물을 때도 브라우저에서 지운다(다음에 앱을 열 때까지 두지 않는다)
      await PendingInvite.save(_token, owner: '지은');
      PendingInvite.savedAt = DateTime.now().subtract(const Duration(hours: 25));
      expect(PendingInvite.path, isNull);
      expect(PendingInvite.token, isNull);
      expect(PendingInvite.ownerName, isNull);
      await Future<void>.delayed(Duration.zero);
      expect((await SharedPreferences.getInstance()).getString(PendingInvite.key), isNull);
    });

    test('서버 오류의 code 와 덧붙인 값을 읽는다', () async {
      ApiClient.useAdapterForTests(_FakeServer({
        'POST /conversations/c1/messages/text': (_) => const _Fail(403, {
              'detail': '지은님이 가족 공유를 다시 확인하면 이어서 이야기할 수 있어요.',
              'code': 'share_paused',
              'reason': 'consent',
            }),
      }));
      try {
        await ApiClient.post('/conversations/c1/messages/text', {'content': '안녕'});
        fail('오류여야 한다');
      } on ApiException catch (error) {
        expect(error.statusCode, 403);
        expect(error.code, 'share_paused');
        expect(error.extra, {'reason': 'consent'});
        expect(error.message, startsWith('지은님이'));
      }
    });

    test('함께 쓰는 횟수 글 · 보내는 글 · 부르는 말 추천', () {
      final member = {'role': 'member', 'status': 'PRELAUNCH', 'active': true, 'conversationsLimit': 200, 'conversationsLeft': 163};
      expect(BillingStore.conversationsText(member), '함께 쓰는 무료 대화 163번 남음');
      expect(BillingStore.conversationsText({...member, 'status': 'ACTIVE'}), '함께 쓰는 이번 달 남은 대화 163번');
      expect(BillingStore.conversationsText({...member, 'role': 'owner'}), '무료 대화 163번 남음');

      // 다 적어 둔 링크: 확인 카드에서 동의에 체크하고 '네, 맞아요. 함께하기'
      expect(invitePrefilled({'myName': '민호', 'myRelation': '사위', 'addressTerm': '어머님'}), isTrue);
      expect(
        inviteShareText(ownerName: '지은', addressTerm: '어머님', prefilled: true),
        "지은님이 TalkTo에서 어머님과 이야기할 수 있게 초대했어요. ① 링크를 누르고 ② '카카오로 시작하기'를 누른 뒤 "
        "③ 동의에 체크하고 '네, 맞아요. 함께하기'를 누르면 바로 이야기할 수 있어요. "
        '처음 들어오면 ②와 ③ 사이에 동의 화면이 나와요.',
      );
      // 비었거나 꼭 있어야 하는 칸이 빠진 링크: 받는 분이 직접 적고 '함께하기'
      expect(invitePrefilled({'myName': '민호', 'myRelation': '사위'}), isFalse);
      expect(invitePrefilled(const {}), isFalse);
      expect(
        inviteShareText(ownerName: '지은'),
        "지은님이 TalkTo에서 가족이 만든 대화 상대와 이야기할 수 있게 초대했어요. ① 링크를 누르고 "
        "② '카카오로 시작하기'를 누른 뒤 ③ 내 정보를 적고 동의에 체크한 뒤 '함께하기'를 누르면 바로 이야기할 수 있어요. "
        '처음 들어오면 ②와 ③ 사이에 동의 화면이 나와요.',
      );
      expect(inviteTipText(prefilled: true), contains('동의에 체크한 다음 네 맞아요를 누르시면 돼요'));
      expect(inviteTipText(), contains('내 정보를 적고 동의에 체크한 다음 함께하기를 누르시면 돼요'));

      expect(addressSuggestionsFor('사위', {'사위': ['장모님', '어머님']}), ['장모님', '어머님']);
      expect(addressSuggestionsFor('친구', {'사위': ['장모님']}), isEmpty);
      expect(addressSuggestionsFor('며느리'), ['어머님', '아버님']); // 서버 추천이 없으면 앱 안의 표
      expect(callsText('엄마'), "'엄마'라고 불러요");
      expect(callsText('어머님'), "'어머님'이라고 불러요");
      expect(memberLine({'myName': '철수', 'myRelation': '아들', 'addressTerm': '엄마'}), "철수 · 아들 · '엄마'라고 불러요");
      expect(ownerNameOf({'ownerName': ' '}), '가족');
    });

    test('적은 내용 확인: 꼭 있어야 하는 칸과 나를 가리키는 말', () {
      final draft = ProfileDraft({'myName': '철수', 'myRelation': '아들', 'addressTerm': '나', 'speechLevel': '높임'});
      expect(draft.speechLevel, isNull); // 모르는 값은 고르지 않은 것으로
      expect(draft.check(voice: ProfileVoice.member), contains('부르는 말로 쓸 수 없어요'));
      draft.text['addressTerm']!.text = '';
      expect(draft.missing(), ['addressTerm']);
      expect(draft.check(voice: ProfileVoice.member), '그분을 부르는 말을 적어 주세요.');
      // 주인이 초대 전에 적어 두는 것은 모두 선택
      expect(draft.check(voice: ProfileVoice.owner, requireAll: false), isNull);
      draft.text['addressTerm']!.text = '엄마';
      expect(draft.toJson()['calledMe'], isNull);
      draft.dispose();
    });
  });

  group('초대 받기 · 로그인 전', () {
    testWidgets('누가 초대했는지 보이고, 카카오가 먼저다. 초대를 적어 둔다 (휴대폰 폭)', (tester) async {
      _phone(tester);
      final server = _FakeServer({
        'POST /invites/preview': (_) => {
              'state': 'open',
              'ownerName': '지은',
              'addressTerm': '엄마',
              'ownerTitle': '할머니',
              'isSelf': false,
              'expiresAt': '2026-10-25T00:00:00Z',
            },
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(_joinApp(_token));
      await _settle(tester);

      expect(_shown('지은님이 초대했어요'), findsOneWidget);
      expect(_has('지은님이 엄마의 녹음과 이야기로 만든 대화 상대와 이야기할 수 있어요. 로그인하면 바로 이어져요.'), findsOneWidget);
      expect(find.text('카카오로 시작하기'), findsOneWidget);
      expect(find.text('Google로 계속하기'), findsOneWidget);
      expect(tester.getTopLeft(find.text('카카오로 시작하기')).dy,
          lessThan(tester.getTopLeft(find.text('Google로 계속하기')).dy));
      expect(find.text('처음이면 자동으로 가입돼요.'), findsOneWidget);
      // 열쇠는 주소가 아니라 몸통으로만
      expect(server.log, ['POST /invites/preview']);
      expect(server.bodies['POST /invites/preview'], {'token': _token});
      expect(PendingInvite.token, _token);
      expect(PendingInvite.ownerName, '지은');
      expect(tester.takeException(), isNull);

      // 이메일로 계속하면 로그인 화면으로(로그인하면 라우터가 이 초대로 돌려보낸다)
      await _tap(tester, find.text('이메일로 계속하기'));
      expect(find.text('로그인 화면'), findsOneWidget);
      expect(PendingInvite.token, _token);
    });

    testWidgets('이미 들어온 링크를 다시 열면 다시 오셨네요', (tester) async {
      ApiClient.useAdapterForTests(_FakeServer({
        'POST /invites/preview': (_) => {'state': 'used', 'ownerName': '지은', 'ownerTitle': '할머니', 'isSelf': false},
      }));
      await tester.pumpWidget(_joinApp(_token));
      await _settle(tester);
      expect(_shown('다시 오셨네요'), findsOneWidget);
      expect(_shown('로그인하면 바로 이어져요.'), findsOneWidget);
      expect(find.text('카카오로 시작하기'), findsOneWidget);
      expect(PendingInvite.token, _token); // 남겨 둔다
    });

    testWidgets('끝난 링크는 그 까닭을 보이고 적어 둔 초대를 지운다', (tester) async {
      const texts = {
        'expired': '기간이 지난 초대 링크예요. 보내 준 가족에게 새 링크를 부탁해 주세요.',
        'invalid': '초대 링크를 찾지 못했어요. 받은 링크를 다시 확인하거나 보내 준 가족에게 새 링크를 부탁해 주세요.',
        'unavailable': '지금은 이 대화 상대를 함께 쓸 수 없어요. 보내 준 가족에게 알려 주세요.',
      };
      for (final entry in texts.entries) {
        ApiClient.useAdapterForTests(_FakeServer({'POST /invites/preview': (_) => {'state': entry.key}}));
        await tester.pumpWidget(_joinApp(_token));
        await _settle(tester);
        expect(_shown(entry.value), findsOneWidget, reason: entry.key);
        expect(_shown('처음 화면으로'), findsOneWidget);
        expect(PendingInvite.token, isNull, reason: entry.key);
        await tester.pumpWidget(const SizedBox());
      }
    });

    testWidgets('열쇠 모양이 아니면 서버에 묻지 않는다', (tester) async {
      final server = _FakeServer({});
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(_joinApp('x'));
      await _settle(tester);
      expect(_has('초대 링크를 찾지 못했어요'), findsOneWidget);
      expect(server.log, isEmpty);
    });
  });

  testWidgets('초대로 처음 가입하면 동의 화면이 누구의 초대인지 알려 준다', (tester) async {
    Future<List<Map<String, dynamic>>> items() async => [
          {'type': 'age14', 'label': '만 14세 이상이에요', 'doc': null, 'kind': 'confirm'},
        ];
    Widget page() => MaterialApp(
          theme: buildTheme(),
          home: ConsentPage(loadItems: items, showBusiness: false, needed: const ['age14']),
        );
    await tester.pumpWidget(page());
    await _settle(tester);
    expect(_has('초대를 이어 가려면'), findsNothing);

    await PendingInvite.save(_token, owner: '지은');
    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(page());
    await _settle(tester);
    expect(
      _shown("지은님의 초대를 이어 가려면 먼저 아래에 동의해 주세요. '모두 동의해요'를 누르면 한 번에 체크돼요."),
      findsOneWidget,
    );
  });

  group('초대 받기 · 로그인 뒤', () {
    setUp(() {
      session.user = {'id': 'u-m', 'name': '김철수', 'consentsNeeded': <String>[]};
    });

    testWidgets("만든 분이 적어 둔 카드: 고치고 동의해야 '네, 맞아요'가 눌리고, 함께하면 대화로 (휴대폰 폭)", (tester) async {
      _phone(tester, height: 1400);
      await PendingInvite.save(_token, owner: '지은');
      final server = _FakeServer({
        'POST /invites/resolve': (_) => _resolveJoin(prefill: {
              'myName': '철수',
              'myRelation': '아들',
              'addressTerm': '엄마',
              'calledMe': '철수야',
              'speechLevel': '반말',
            }),
        'POST /invites/accept': (_) => {'personaId': 'p1', 'shareId': 's1', 'already': false},
        'GET /personas': (_) => const [],
        'GET /legal/family_share': (_) => {
              'key': 'family_share',
              'title': '가족 공유 안내와 동의',
              'version': '0.6',
              'kind': 'consent',
              'body': '{{호칭}}께서 괜찮다고 하셨어요.',
            },
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(_joinApp(_token));
      await _settle(tester);

      expect(_shown('지은님이 이렇게 적어 두셨어요'), findsOneWidget);
      expect(_shown('맞는지 확인'), findsOneWidget); // 가족 공유 안내가 이 이름으로 부른다
      for (final value in ['철수', '아들', '엄마', '철수야', '반말']) {
        expect(_shown(value), findsOneWidget, reason: value);
      }
      expect(_has('지은님이 초대하면서 적어 둔 내용이에요'), findsOneWidget);
      expect(_has('지은님도 볼 수 없어요'), findsOneWidget);
      // 다만 고쳐 보낸 대답과 보낸 답은 만든 분이 확인한다(가족 공유 안내와 같게)
      expect(_has('다만 내가 고쳐 보낸 대답과 보낸 답은 지은님이 확인할 수 있게 보여요'), findsOneWidget);
      expect(_filled(tester, '네, 맞아요. 함께하기').onPressed, isNull); // 동의 전
      expect(tester.takeException(), isNull);
      // 로그인·가입·동의를 마쳤으니 브라우저에 적어 둔 초대는 지웠다(이 화면은 주소의 열쇠로 이어 간다)
      expect(PendingInvite.token, isNull);
      expect((await SharedPreferences.getInstance()).getString(PendingInvite.key), isNull);

      // 어르신도 읽게 글이 1.15배. 화면 밖 Navigator 에 그려지는 안내 문서 창도 같다
      double scaleAt(Finder finder) => MediaQuery.textScalerOf(tester.element(finder.first)).scale(10) / 10;
      expect(scaleAt(_shown('맞는지 확인')), closeTo(1.15, 0.001));
      await _tap(tester, find.text('보기'));
      expect(_has('그분께서 괜찮다고 하셨어요.'), findsOneWidget); // 받는 분 화면에서 {{호칭}} 은 '그분'
      expect(scaleAt(_has('그분께서 괜찮다고 하셨어요.')), closeTo(1.15, 0.001));
      await _tap(tester, find.byTooltip('닫기'));

      // 한 칸 고치기(그 칸만 띄운다). 고치는 창도 1.15배
      await _tap(tester, _shown('철수야'));
      expect(find.byType(TextField), findsOneWidget);
      expect(scaleAt(find.byType(TextField)), closeTo(1.15, 0.001));
      await tester.enterText(find.byType(TextField), '우리 철수');
      await _tap(tester, find.text('고치기'));
      expect(_shown('우리 철수'), findsOneWidget);

      await _tap(tester, find.byType(Checkbox));
      expect(_filled(tester, '네, 맞아요. 함께하기').onPressed, isNotNull);
      await _tap(tester, _shown('네, 맞아요. 함께하기'));

      expect(server.bodies['POST /invites/accept'], {
        'token': _token,
        'agree': true,
        'profile': {
          'myName': '철수',
          'myRelation': '아들',
          'addressTerm': '엄마',
          'calledMe': '우리 철수',
          'speechLevel': '반말',
          'selfReference': null,
          'callsOwner': null,
        },
      });
      expect(server.log, contains('GET /personas')); // 목록을 새로 읽고
      expect(find.text('대화 p1'), findsOneWidget); // 대화로
      expect(find.text('엄마와 이야기할 수 있어요.'), findsOneWidget);
      expect(PendingInvite.token, isNull);
    });

    testWidgets("꼭 있어야 하는 칸이 비면 '적어 주세요'이고 동의해도 눌리지 않는다", (tester) async {
      ApiClient.useAdapterForTests(_FakeServer({
        'POST /invites/resolve': (_) => _resolveJoin(prefill: {'myName': '철수', 'myRelation': '아들'}),
      }));
      await tester.pumpWidget(_joinApp(_token));
      await _settle(tester);
      expect(_shown('적어 주세요'), findsOneWidget);
      await _tap(tester, find.byType(Checkbox));
      expect(_filled(tester, '네, 맞아요. 함께하기').onPressed, isNull);
      expect(_has("'적어 주세요' 칸을 채워 주세요."), findsOneWidget);
    });

    testWidgets('제가 아니에요: 서버에 묻지 않고 적어 둔 초대를 지운다', (tester) async {
      await PendingInvite.save(_token, owner: '지은');
      final server = _FakeServer({
        'POST /invites/resolve': (_) => _resolveJoin(prefill: {'myName': '철수', 'myRelation': '아들', 'addressTerm': '엄마'}),
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(_joinApp(_token));
      await _settle(tester);
      await _tap(tester, _shown('제가 철수님이 아니에요'));
      expect(_shown('다른 분께 보낸 링크예요'), findsOneWidget);
      expect(_shown('이 링크는 철수님께 보낸 거예요. 지은님께 내 링크를 따로 부탁해 주세요.'), findsOneWidget);
      expect(server.log, ['POST /invites/resolve']);
      expect(PendingInvite.token, isNull);
      expect(events, contains('join_declined {"reason":"not_me"}'));
    });

    testWidgets('적어 둔 것이 없으면 직접 적는다(관계를 고르면 부르는 말 추천)', (tester) async {
      _phone(tester, height: 1600);
      final server = _FakeServer({
        'POST /invites/resolve': (_) => _resolveJoin(),
        'POST /invites/accept': (_) => {'personaId': 'p1', 'shareId': 's1', 'already': false},
        'GET /personas': (_) => const [],
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(_joinApp(_token));
      await _settle(tester);

      expect(_shown('지은님의 할머니께 나는 어떤 사람인가요?'), findsOneWidget);
      expect(find.widgetWithText(TextField, '김철수'), findsOneWidget); // 내 이름은 계정 이름으로
      expect(_shown('엄마'), findsNothing);
      await _tap(tester, _shown('아들'));
      expect(_shown('엄마'), findsOneWidget);
      await _tap(tester, _shown('엄마'));
      await _tap(tester, find.byType(Checkbox));
      await _tap(tester, _shown('함께하기'));
      expect((server.bodies['POST /invites/accept'] as Map)['profile'], {
        'myName': '김철수',
        'myRelation': '아들',
        'addressTerm': '엄마',
        'calledMe': null,
        'speechLevel': null,
        'selfReference': null,
        'callsOwner': null,
      });
      expect(find.text('대화 p1'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('이미 함께하는 사람(받은 링크를 다시 누름)은 바로 대화로', (tester) async {
      ApiClient.useAdapterForTests(_FakeServer({
        'POST /invites/resolve': (_) => {'state': 'member', 'personaId': 'p1'},
        'GET /personas': (_) => const [],
      }));
      await tester.pumpWidget(_joinApp(_token));
      await _settle(tester);
      expect(find.text('대화 p1'), findsOneWidget);
    });

    testWidgets('만든 사람이 자기 링크를 열면 대화로 가거나 다른 계정으로', (tester) async {
      ApiClient.useAdapterForTests(_FakeServer({
        'POST /invites/resolve': (_) => {'state': 'owner', 'personaId': 'p1', 'ownerName': '지은'},
      }));
      await tester.pumpWidget(_joinApp(_token));
      await _settle(tester);
      expect(_shown('내가 만든 대화 상대예요'), findsOneWidget);
      expect(find.text('다른 계정으로 들어가기'), findsOneWidget);
      await _tap(tester, _shown('대화로 가기'));
      expect(find.text('대화 p1'), findsOneWidget);
    });

    testWidgets('다른 분이 이미 쓴 링크면 지금 계정을 알려 주고, 다른 계정으로 들어가면 초대를 이어 간다', (tester) async {
      await PendingInvite.save(_token);
      final server = _FakeServer({
        'POST /invites/resolve': (_) => {'state': 'used', 'ownerName': '지은'},
        'POST /invites/preview': (_) => {'state': 'used', 'ownerName': '지은', 'ownerTitle': '할머니', 'isSelf': false},
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(_joinApp(_token));
      await _settle(tester);
      expect(_shown('이미 다른 분이 쓴 초대 링크예요. 보내 준 가족에게 내 링크를 따로 부탁해 주세요.'), findsOneWidget);
      expect(_has("지금 '김철수' 계정이에요."), findsOneWidget);
      expect(PendingInvite.token, isNull); // 로그인을 마쳤으니 브라우저에서는 지운다

      // 로그아웃하면 로그인 전 화면으로 돌아와 초대를 다시 적어 둔다(다른 계정으로 로그인하면 여기로)
      await _tap(tester, _shown('다른 계정으로 들어가기'));
      expect(server.log, contains('POST /invites/preview'));
      // 이 기기에서만 나간다(서버 로그아웃은 모든 기기의 로그인을 끝낸다)
      expect(server.log, isNot(contains('POST /auth/logout')));
      expect(session.loggedIn, isFalse);
      expect(_shown('다시 오셨네요'), findsOneWidget);
      expect(find.text('카카오로 시작하기'), findsOneWidget);
      expect(PendingInvite.token, _token);
      expect(PendingInvite.ownerName, '지은');
    });

    testWidgets('받아들이는 사이 다른 분이 먼저 쓰면 그 상태로', (tester) async {
      ApiClient.useAdapterForTests(_FakeServer({
        'POST /invites/resolve': (_) => _resolveJoin(prefill: {'myName': '철수', 'myRelation': '아들', 'addressTerm': '엄마'}),
        'POST /invites/accept': (_) => const _Fail(410, {
              'detail': '이미 다른 분이 쓴 초대 링크예요. 보내 준 가족에게 내 링크를 따로 부탁해 주세요.',
              'code': 'invite_used',
            }),
      }));
      await tester.pumpWidget(_joinApp(_token));
      await _settle(tester);
      await _tap(tester, find.byType(Checkbox));
      await _tap(tester, _shown('네, 맞아요. 함께하기'));
      expect(_shown('이미 쓴 초대 링크예요'), findsOneWidget);
    });
  });

  group('가족과 함께(만든 사람)', () {
    setUp(() {
      session.user = {'id': 'u-o', 'name': '박지은', 'consentsNeeded': <String>[]};
    });

    // 서버는 가족 표의 살아 계신 분만 준다(돌아가신 분 줄은 오지 않는다)
    Map<String, dynamic> list({
      bool allowed = true,
      String? reason,
      List<String> need = const [],
      String kind = 'alive',
      int used = 2,
    }) =>
        {
          'allowed': allowed,
          'reason': reason,
          'prelaunch': true,
          'subjectKind': kind,
          'need': need,
          'consentItems': [
            for (final t in need) {'type': t, 'label': '{{호칭}}께 $t 확인'},
          ],
          'cap': 5,
          'used': used,
          'familyRows': const [
            {'familyRow': '철수', 'myName': '철수', 'myRelation': '아들', 'calledMe': '철수야', 'taken': true},
            {'familyRow': '영희', 'myName': '영희', 'myRelation': '딸', 'calledMe': '영희야', 'taken': false},
            {'familyRow': '민수', 'myName': '민수', 'myRelation': '손자', 'calledMe': '민수야', 'taken': true},
          ],
          'addressSuggestions': const {
            '딸': ['엄마', '어머니'],
            '아들': ['엄마', '어머니'],
          },
          'voiceShareable': true,
          'items': const [
            {
              'id': 's1',
              'status': 'joined',
              'label': '철수',
              'createdAt': '2026-10-16T03:00:00Z',
              'joinedAt': '2026-10-17T03:00:00Z',
              'accountName': '김철수',
              'profile': {'myName': '철수', 'myRelation': '아들', 'addressTerm': '엄마', 'familyRow': '철수'},
              'editedBy': 'member',
            },
            {
              'id': 's2',
              'status': 'open',
              'label': '민수',
              'createdAt': '2026-10-17T03:00:00Z',
              'expiresAt': '2026-10-24T03:00:00Z',
              'prefill': {'myName': '민수', 'addressTerm': '할머니', 'familyRow': '민수'},
              'editedBy': null,
            },
          ],
        };

    Map<String, dynamic> owner() => {
          'id': 'p1',
          'role': 'owner',
          'addressTerm': '할머니',
          'relation': '할머니',
          'step': 'READY',
          'origin': null,
          'members': 1,
          'basics': {'myName': '지은', 'relation': '할머니', 'addressTerm': '할머니'},
          'jobs': <String, dynamic>{},
        };

    testWidgets('확인 → 가족 고르기(함께하거나 링크를 보낸 분은 못 고름) → 링크 → 복사·다시 보내기·고치기 (휴대폰 폭)', (tester) async {
      _phone(tester, height: 2200);
      const need = ['family_share', 'family_share_subject', 'family_share_sensitive'];
      final copied = <String>[];
      final shared = <String>[];
      LinkSharing.copy = (text) async {
        copied.add(text);
        return copied.length > 1; // 처음에는 클립보드가 막혔다
      };
      LinkSharing.share = ({required String url, String? text}) async {
        shared.add('$url|$text');
        return true;
      };
      final server = _FakeServer({
        'GET /personas/p1': (_) => owner(),
        'GET /intake/meta': (_) => {
              'myRelations': ['아들', '딸', '손자'],
            },
        'GET /personas/p1/shares': (_) => list(need: need),
        'POST /personas/p1/shares': (_) => {
              'share': {
                'id': 's3',
                'status': 'open',
                'label': '영희',
                'prefill': {'myName': '영희', 'myRelation': '딸', 'addressTerm': '엄마', 'calledMe': '영희야', 'familyRow': '영희'},
              },
              'url': 'https://app.example.test/#/join/tok_NEW00000001',
            },
        'POST /personas/p1/shares/s2/renew': (_) => {
              'share': {
                'id': 's2',
                'status': 'open',
                'label': '민수',
                'prefill': {'myName': '민수', 'addressTerm': '할머니', 'familyRow': '민수'},
              },
              'url': 'https://app.example.test/#/join/tok_RENEW0000001',
            },
        'PATCH /personas/p1/shares/s2': (_) => {
              'id': 's2',
              'status': 'open',
              'label': '민수',
              'prefill': {'myName': '민수', 'myRelation': '손자', 'addressTerm': '할머니', 'familyRow': '민수'},
            },
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const FamilyPage(personaId: 'p1')));
      await _settle(tester);

      expect(_shown('할머니, 가족과 함께'), findsOneWidget);
      expect(_has('결제가 열리기 전에는 무료로 함께 쓸 수 있어요'), findsOneWidget);
      // 확인 글의 {{호칭}} 은 만든 분 쪽 호칭으로
      expect(_has('할머니께 family_share_subject 확인'), findsOneWidget);
      expect(_shown('철수 · 아들 · 함께하는 중'), findsOneWidget);
      expect(_shown('민수 · 손자 · 링크 보냄'), findsOneWidget);
      expect(_has("다만 가족이 고쳐 보낸 대답과 보낸 답은 '더 알려 주실 기억'에서 확인할 수 있어요"), findsOneWidget);

      // 확인 전에는 고를 수 없다
      await _tap(tester, _shown('영희 · 딸'));
      expect(_shown('영희님은 어떤 분인가요?'), findsNothing);
      for (var i = 0; i < need.length; i++) {
        await _tap(tester, find.byType(Checkbox).at(i));
      }
      // 함께하는 가족·링크를 보낸 분은 눌러도 열리지 않는다
      await _tap(tester, _shown('민수 · 손자 · 링크 보냄'));
      expect(_shown('민수님은 어떤 분인가요?'), findsNothing);

      await _tap(tester, _shown('영희 · 딸'));
      expect(_shown('영희님은 어떤 분인가요?'), findsOneWidget);
      expect(find.widgetWithText(TextField, '영희'), findsOneWidget);
      expect(_shown('이분이 할머니를 부르는 말'), findsOneWidget);
      expect(_has("이름·관계·부르는 말까지 적어 두면 받는 분은 동의에 체크하고 '네, 맞아요'만 누르면 돼요."), findsOneWidget);
      await _tap(tester, _shown('엄마'));
      await _tap(tester, _shown('초대 링크 만들기'));
      expect(server.bodies['POST /personas/p1/shares'], {
        'agreements': need,
        'familyRow': '영희',
        'prefill': {'myName': '영희', 'myRelation': '딸', 'addressTerm': '엄마', 'calledMe': '영희야'},
      });

      // 링크 카드: 보내기는 공유 창이 있을 때만, 복사가 막히면 길게 눌러 복사
      expect(_shown('영희님께 보낼 링크가 준비됐어요'), findsOneWidget);
      expect(tester.widget<SelectableText>(find.byType(SelectableText)).data, 'https://app.example.test/#/join/tok_NEW00000001');
      expect(find.text('보내기'), findsNothing);
      await _tap(tester, find.text('링크 복사'));
      expect(find.text('링크를 길게 눌러 복사해 주세요.'), findsOneWidget);
      await _tap(tester, find.text('링크 복사'));
      // 다 적어 둔 링크: 받는 분은 동의에 체크하고 '네, 맞아요. 함께하기'
      expect(copied.last, contains('지은님이 TalkTo에서 엄마와 이야기할 수 있게 초대했어요.'));
      expect(copied.last, contains("③ 동의에 체크하고 '네, 맞아요. 함께하기'를 누르면"));
      expect(copied.last, endsWith('https://app.example.test/#/join/tok_NEW00000001'));
      expect(_has('동의에 체크한 다음 네 맞아요를 누르시면 돼요'), findsOneWidget);
      expect(find.text('링크를 복사했어요. 가족에게 보내 주세요.'), findsOneWidget);
      expect(events, contains('share_link_sent {"method":"copy"}'));

      // 함께 쓰는 가족 · 쓰지 않은 링크
      expect(_shown('함께 쓰는 가족 2/5'), findsOneWidget);
      expect(_shown('철수 · 아들'), findsOneWidget);
      expect(_has("'엄마'라고 불러요 · 10월 17일부터"), findsOneWidget);
      expect(_shown('계정 이름 김철수'), findsOneWidget);
      expect(_shown('민수'), findsOneWidget);

      // 다시 보내기: 같은 자리에 새 링크. 관계를 적어 두지 않아 받는 분이 직접 적는다
      LinkSharing.canShare = () => true;
      await _tap(tester, find.text('다시 보내기'));
      expect(server.log, contains('POST /personas/p1/shares/s2/renew'));
      expect(_shown('민수님께 보낼 링크가 준비됐어요'), findsOneWidget);
      expect(_has('내 정보를 적고 동의에 체크한 다음 함께하기를 누르시면 돼요'), findsOneWidget);
      await _tap(tester, find.text('보내기'));
      expect(shared.single, startsWith('https://app.example.test/#/join/tok_RENEW0000001|지은님이 TalkTo에서 할머니와'));
      expect(shared.single, contains("③ 내 정보를 적고 동의에 체크한 뒤 '함께하기'를 누르면"));
      expect(events, contains('share_link_sent {"method":"share"}'));

      // 그 링크에 관계를 마저 적으면 보내는 글도 '네, 맞아요'로 바뀐다
      await _tap(tester, find.text('고치기'));
      expect(_shown('적어 둔 것 고치기'), findsOneWidget);
      await _tap(tester, _shown('손자'));
      await _tap(tester, find.text('저장하기'));
      expect(server.bodies['PATCH /personas/p1/shares/s2'], {
        'myName': '민수',
        'myRelation': '손자',
        'addressTerm': '할머니',
        'calledMe': null,
        'speechLevel': null,
        'selfReference': null,
        'callsOwner': null,
      });
      expect(_has('동의에 체크한 다음 네 맞아요를 누르시면 돼요'), findsOneWidget);
      await _tap(tester, find.text('보내기'));
      expect(shared.last, contains("③ 동의에 체크하고 '네, 맞아요. 함께하기'를 누르면"));
      expect(tester.takeException(), isNull);
    });

    testWidgets("나 자신 대화 상대: 확인 글과 안내 문서의 {{호칭}} 은 '그분'('나께'가 아니다)", (tester) async {
      _phone(tester, height: 1600);
      ApiClient.useAdapterForTests(_FakeServer({
        'GET /personas/p1': (_) => {
              ...owner(),
              'addressTerm': '나',
              'relation': '나 자신',
              'basics': {'myName': '지은', 'relation': '나 자신', 'addressTerm': '나', 'status': 'self'},
            },
        'GET /intake/meta': (_) => {'myRelations': <String>[]},
        'GET /personas/p1/shares': (_) => list(need: const ['family_share'], kind: 'self'),
        'GET /legal/family_share': (_) => {
              'key': 'family_share',
              'title': '가족 공유 안내와 동의',
              'version': '0.6',
              'kind': 'consent',
              'body': '{{호칭}}께서 직접 괜찮다고 하셨어요.',
            },
      }));
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const FamilyPage(personaId: 'p1')));
      await _settle(tester);
      expect(_has('그분께 family_share 확인'), findsOneWidget);
      expect(_has('나께'), findsNothing);
      await _tap(tester, find.text('가족 공유 안내 보기'));
      expect(_has('그분께서 직접 괜찮다고 하셨어요.'), findsOneWidget);
      expect(_has('나께서'), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('들어온 가족의 부르는 말을 추천에서 골라 고치고, 내보내면 지운다', (tester) async {
      _phone(tester, height: 1800);
      final server = _FakeServer({
        'GET /personas/p1': (_) => owner(),
        'GET /intake/meta': (_) => {
              'myRelations': ['아들', '딸'],
            },
        'GET /personas/p1/shares': (_) => list(),
        'PATCH /personas/p1/shares/s1': (_) => const {'id': 's1', 'status': 'joined', 'label': '철수'},
        'DELETE /personas/p1/shares/s1': (_) => const {
              'ended': true,
              'deletion': {'deleted': true, 'backupUntil': '2026-10-30', 'message': 'TalkTo 서버에서 지웠어요. 서버 백업에는 10월 30일까지 남았다가 저절로 지워져요.'},
            },
        'GET /personas': (_) => const [],
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const FamilyPage(personaId: 'p1')));
      await _settle(tester);

      await _tap(tester, find.text('정보 고치기'));
      expect(_shown('철수님 정보 고치기'), findsOneWidget);
      expect(_shown('어머니'), findsOneWidget); // 아들 → 엄마·어머니
      await _tap(tester, _shown('어머니'));
      await _tap(tester, find.text('저장하기'));
      expect(server.bodies['PATCH /personas/p1/shares/s1'], {
        'myName': '철수',
        'myRelation': '아들',
        'addressTerm': '어머니',
        'calledMe': null,
        'speechLevel': null,
        'selfReference': null,
        'callsOwner': null,
      });
      expect(find.text('고쳤어요.'), findsOneWidget);

      await _tap(tester, find.text('내보내기'));
      expect(find.text('철수님을 내보낼까요?'), findsOneWidget);
      expect(_has('철수님은 더는 할머니와 이야기할 수 없고, 철수님이 나눈 대화도 지워져요.'), findsOneWidget);
      await _tap(tester, find.descendant(of: find.byType(AlertDialog), matching: find.text('내보내기')));
      expect(server.log, contains('DELETE /personas/p1/shares/s1'));
      // 지운 뒤에는 서버 백업에 남는 날까지 서버 글 그대로 알린다(WO 7)
      expect(_has('서버 백업에는 10월 30일까지'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('요금제가 가족 공유를 열지 않으면 요금제 보기만, 링크는 만들 수 없다', (tester) async {
      ApiClient.useAdapterForTests(_FakeServer({
        'GET /personas/p1': (_) => owner(),
        'GET /intake/meta': (_) => {'myRelations': <String>[]},
        'GET /personas/p1/shares': (_) => list(allowed: false, reason: 'plan'),
      }));
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const FamilyPage(personaId: 'p1')));
      await _settle(tester);
      expect(_has('지금은 함께 쓰는 가족이 이야기할 수 없어요. 가족 요금제로 바꾸면 다시 함께 쓸 수 있어요.'), findsOneWidget);
      expect(find.text('요금제 보기'), findsOneWidget);
      expect(_shown('누구를 초대할까요?'), findsNothing);
      expect(find.text('가족과 함께 쓰기 그만두기'), findsOneWidget);
    });

    /// 확인을 받는 서버: 받은 확인은 지우고 남은 것을 돌려준다(그 뒤 목록도 남은 것만).
    _FakeServer confirmServer(List<String> initial, {bool allowed = true, String? reason, int used = 2}) {
      var need = [...initial];
      return _FakeServer({
        'GET /personas/p1': (_) => owner(),
        'GET /intake/meta': (_) => {'myRelations': <String>[]},
        'GET /personas/p1/shares': (_) => list(allowed: allowed, reason: reason, need: need, used: used),
        'POST /personas/p1/shares/confirm': (body) {
          final agreed = [for (final t in (body as Map)['agreements'] as List) '$t'];
          need = [for (final t in need) if (!agreed.contains(t)) t];
          return {'need': need};
        },
      });
    }

    const pausedNotice = '다시 확인할 때까지 함께 쓰는 가족은 이야기할 수 없어요.';
    const resumed = '확인했어요. 함께 쓰는 가족도 다시 이야기할 수 있어요.';

    testWidgets('그분 상태를 바꿔 확인이 거둬지면 자리가 다 차도 다시 확인한다(가족은 그때까지 멈춤) (휴대폰 폭)', (tester) async {
      _phone(tester, height: 1800);
      const need = ['family_share', 'family_share_subject', 'family_share_sensitive'];
      final server = confirmServer(need, used: 5);
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const FamilyPage(personaId: 'p1')));
      await _settle(tester);

      expect(_shown('다시 확인해 주세요'), findsOneWidget);
      expect(_shown('처음 한 번만 확인해 주세요'), findsNothing);
      expect(_shown(pausedNotice), findsOneWidget);
      expect(_has('함께 쓸 수 있는 가족은 5명까지예요'), findsOneWidget);
      expect(_shown('영희 · 딸'), findsNothing); // 자리가 다 차서 고를 가족은 없다
      expect(_filled(tester, '확인했어요').onPressed, isNull);

      for (var i = 0; i < need.length - 1; i++) {
        await _tap(tester, find.byType(Checkbox).at(i));
      }
      expect(_filled(tester, '확인했어요').onPressed, isNull); // 하나라도 빠지면 못 누른다
      await _tap(tester, find.byType(Checkbox).at(need.length - 1));
      await _tap(tester, _shown('확인했어요'));

      expect(server.bodies['POST /personas/p1/shares/confirm'], {'agreements': need});
      // 확인한 뒤 화면을 다시 읽고, 링크는 만들지 않는다
      expect(server.log.where((key) => key == 'GET /personas/p1/shares'), hasLength(2));
      expect(server.log, isNot(contains('POST /personas/p1/shares')));
      expect(find.text(resumed), findsOneWidget);
      expect(_shown('다시 확인해 주세요'), findsNothing);
      expect(_shown(pausedNotice), findsNothing);
      expect(find.byType(Checkbox), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets("처음 확인: '확인했어요'로 확인만 하고, 남은 확인이 오면 그것만 다시 묻는다", (tester) async {
      _phone(tester, height: 1600);
      var need = ['family_share'];
      final server = _FakeServer({
        'GET /personas/p1': (_) => owner(),
        'GET /intake/meta': (_) => {'myRelations': <String>[]},
        'GET /personas/p1/shares': (_) => {...list(need: need), 'used': 0, 'items': const []},
        'POST /personas/p1/shares/confirm': (body) {
          // 그사이 그분 상태가 바뀌어 확인할 것이 하나 늘었다
          need = need.contains('family_share_sensitive') ? <String>[] : ['family_share_sensitive'];
          return {'need': need};
        },
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const FamilyPage(personaId: 'p1')));
      await _settle(tester);

      expect(_shown('처음 한 번만 확인해 주세요'), findsOneWidget);
      expect(_shown(pausedNotice), findsNothing); // 들어온 가족이 없다
      await _tap(tester, find.byType(Checkbox));
      await _tap(tester, _shown('확인했어요'));
      expect(server.bodies['POST /personas/p1/shares/confirm'], {
        'agreements': ['family_share'],
      });
      expect(find.text('아직 확인할 것이 남았어요.'), findsOneWidget);
      expect(_has('할머니께 family_share_sensitive 확인'), findsOneWidget);
      expect(tester.widget<Checkbox>(find.byType(Checkbox)).value, isFalse);
      expect(_filled(tester, '확인했어요').onPressed, isNull);

      await _tap(tester, find.byType(Checkbox));
      await _tap(tester, _shown('확인했어요'));
      expect(server.bodies['POST /personas/p1/shares/confirm'], {
        'agreements': ['family_share_sensitive'],
      });
      expect(find.text('확인했어요.'), findsOneWidget);
      expect(find.byType(Checkbox), findsNothing);
      // 다 확인했으니 바로 초대할 수 있다
      await _tap(tester, _shown('영희 · 딸'));
      expect(_shown('영희님은 어떤 분인가요?'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    for (final reason in ['plan', 'closed']) {
      testWidgets("링크를 만들 수 없을 때($reason)도 '확인했어요'로 다시 확인한다", (tester) async {
        _phone(tester, height: 1600);
        final server = confirmServer(const ['family_share'], allowed: false, reason: reason);
        ApiClient.useAdapterForTests(server);
        await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const FamilyPage(personaId: 'p1')));
        await _settle(tester);

        expect(_has(reason == 'plan' ? '가족 요금제로 바꾸면' : '잠시 가족 초대를 멈췄어요.'), findsOneWidget);
        expect(_shown('누구를 초대할까요?'), findsNothing);
        expect(_shown('다시 확인해 주세요'), findsOneWidget);
        expect(_shown(pausedNotice), findsOneWidget);
        await _tap(tester, find.byType(Checkbox));
        await _tap(tester, _shown('확인했어요'));
        expect(server.bodies['POST /personas/p1/shares/confirm'], {
          'agreements': ['family_share'],
        });
        // 요금제로도 멈춘 가족은 확인만으로는 이어지지 않는다(잠시 멈춤은 새 초대만 막는다)
        expect(find.text(reason == 'plan' ? '확인했어요.' : resumed), findsOneWidget);
        expect(find.byType(Checkbox), findsNothing);
        expect(_shown(pausedNotice), findsNothing);
        expect(tester.takeException(), isNull);
      });
    }

    for (final reason in ['not_ready', 'unsupported']) {
      testWidgets('다 만들기 전 · 함께 쓸 수 없는 대화 상대($reason)는 확인을 묻지 않는다', (tester) async {
        final server = confirmServer(const ['family_share'], allowed: false, reason: reason);
        ApiClient.useAdapterForTests(server);
        await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const FamilyPage(personaId: 'p1')));
        await _settle(tester);

        expect(_has(reason == 'not_ready' ? '다 만든 뒤에 가족과 함께 쓸 수 있어요.' : '아직 가족과 함께 쓸 수 없어요.'), findsOneWidget);
        expect(find.byType(Checkbox), findsNothing);
        expect(_shown('확인했어요'), findsNothing);
        expect(_shown(pausedNotice), findsNothing);
        expect(_shown('누구를 초대할까요?'), findsNothing);
        expect(server.log, isNot(contains('POST /personas/p1/shares/confirm')));
      });
    }

    testWidgets('확인할 것이 없으면 확인 칸도 멈춤 안내도 없다', (tester) async {
      ApiClient.useAdapterForTests(confirmServer(const []));
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const FamilyPage(personaId: 'p1')));
      await _settle(tester);
      expect(_shown('누구를 초대할까요?'), findsOneWidget);
      expect(find.byType(Checkbox), findsNothing);
      expect(_shown('확인했어요'), findsNothing);
      expect(_shown(pausedNotice), findsNothing);
    });

    testWidgets('함께하는 가족이 주소로 들어오면 막힌 안내만(목록은 읽지 않는다)', (tester) async {
      final server = _FakeServer({
        'GET /personas/p1': (_) => {'id': 'p1', 'role': 'member', 'addressTerm': '엄마', 'ownerName': '지은', 'step': 'READY'},
        'GET /intake/meta': (_) => {'myRelations': <String>[]},
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const FamilyPage(personaId: 'p1')));
      await _settle(tester);
      expect(_has('지은님이 만들어 함께 쓰고 있어요'), findsOneWidget);
      expect(server.log, isNot(contains('GET /personas/p1/shares')));
    });
  });

  group('대화 화면(함께하는 가족)', () {
    Map<String, dynamic> member() => {
          'id': 'p1',
          'role': 'member',
          'shareId': 's1',
          'addressTerm': '엄마',
          'relation': null,
          'lifeStatus': 'DECEASED',
          'step': 'READY',
          'hasVoice': true,
          'origin': null,
          'ownerName': '지은',
          'jobs': <String, dynamic>{},
          'voicePending': false,
          'me': {'shareId': 's1', 'myName': '철수', 'myRelation': '아들', 'addressTerm': '엄마', 'calledMe': '철수야'},
        };

    Map<String, dynamic> view({bool textOnly = false, Map<String, dynamic>? paused}) => {
          'personaId': 'p1',
          'role': 'member',
          'status': paused == null ? 'PRELAUNCH' : 'PAUSED',
          'active': paused == null,
          'conversationsLimit': 200,
          'conversationsUsed': 37,
          'conversationsLeft': textOnly ? 0 : 163,
          'conversationsResetAt': '2026-11-01T00:00:00Z',
          'textOnly': textOnly,
          'title': '엄마',
          'ownerName': '지은',
          'paymentsOpen': false,
          'paused': paused,
        };

    _FakeServer chatServer(Map<String, dynamic> billing, {Map<String, Object? Function(dynamic)> extra = const {}}) =>
        _FakeServer({
          'POST /personas/p1/conversation': (_) => {'conversationId': 'c1'},
          'GET /conversations/c1': (_) => {'messages': <Object>[]},
          'GET /billing/personas/p1': (_) => billing,
          ...extra,
        });

    testWidgets('기억 더 채우기·요금제 보기가 없고, 더 보기에는 내 정보 고치기와 그만두기만 (휴대폰 폭)', (tester) async {
      _phone(tester);
      const pausedText = '지은님이 가족 공유를 다시 확인하면 이어서 이야기할 수 있어요. 나눈 대화는 그대로 볼 수 있어요.';
      // 보내는 사이 만든 분의 확인이 거둬졌다(그 뒤로는 요금제 보기도 멈춤을 알려 준다)
      var paused = false;
      final server = chatServer(view(), extra: {
        'GET /billing/personas/p1': (_) => paused ? view(paused: {'reason': 'consent', 'text': pausedText}) : view(),
        'POST /conversations/c1/messages/text': (_) {
          paused = true;
          return const _Fail(403, {'detail': pausedText, 'code': 'share_paused', 'reason': 'consent'});
        },
      });
      ApiClient.useAdapterForTests(server);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: member())));
      await _settle(tester);

      expect(find.text('기억 더 채우기'), findsNothing);
      expect(find.text('질문 더 답하기'), findsNothing);
      expect(find.text('요금제 보기'), findsNothing);
      expect(_has('지은님과 함께'), findsWidgets);
      expect(_has('지은님이 녹음과 이야기로 만든 대화 상대예요.'), findsOneWidget);
      expect(_has('다음에는 받은 링크를 다시 누르면 바로 이어져요.'), findsOneWidget);
      expect(_has('함께 쓰는 남은 대화 163번'), findsOneWidget);
      expect(tester.takeException(), isNull);

      await _tap(tester, find.byTooltip('더 보기'));
      expect(find.text('나에 대한 정보 고치기'), findsOneWidget);
      expect(find.text('함께 쓰기 그만두기'), findsOneWidget);
      expect(_shown("철수 · 아들 · '엄마'라고 불러요"), findsOneWidget);
      for (final hidden in [
        '정보 고치기',
        '녹음 더하기',
        '남긴 글 더하기 (카카오톡·문자·일기)',
        '이 대화 상대 지우기',
        '가족과 함께 쓰기',
        '목소리 만들기',
        '목소리 다시 만들기',
      ]) {
        expect(find.text(hidden), findsNothing, reason: hidden);
      }
      expect(tester.takeException(), isNull);
      await tester.tapAt(const Offset(180, 20)); // 시트 닫기
      await _settle(tester);

      // 만든 분 쪽 사정으로 멈추면 입력 칸 대신 그 까닭(나눈 대화는 그대로)
      await tester.enterText(find.byType(TextField), '엄마 보고 싶어요');
      await _tap(tester, find.byTooltip('보내기'));
      expect(server.log, contains('POST /conversations/c1/messages/text'));
      expect(_shown(pausedText), findsOneWidget);
      expect(find.byType(TextField), findsNothing);
      expect(find.text('요금제 보기'), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('멈췄다가 만든 분이 다시 열면 입력 칸이 돌아온다', (tester) async {
      var paused = false;
      ApiClient.useAdapterForTests(chatServer(view(), extra: {
        'GET /billing/personas/p1': (_) => paused ? view(paused: {'reason': 'owner', 'text': '지금은 이 대화 상대와 이야기할 수 없어요.'}) : view(),
        'POST /conversations/c1/messages/text': (_) => const _Fail(403, {
              'detail': '지금은 이 대화 상대와 이야기할 수 없어요.',
              'code': 'share_paused',
              'reason': 'owner',
            }),
      }));
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: member())));
      await _settle(tester);
      paused = true;
      await tester.enterText(find.byType(TextField), '안녕');
      await _tap(tester, find.byTooltip('보내기'));
      expect(find.byType(TextField), findsNothing);
      // 다시 열린 뒤 요금제 보기를 새로 읽으면 입력 칸으로
      paused = false;
      billingStore.loadPersona('p1'); // 가짜 서버 응답은 화면을 넘기며(pump) 받는다
      await _settle(tester);
      expect(find.byType(TextField), findsOneWidget);
    });

    testWidgets('나에 대한 정보 고치기: 고치면 제목(부르는 말)이 바로 바뀐다', (tester) async {
      _phone(tester, height: 1400);
      final server = chatServer(view(), extra: {
        'GET /intake/meta': (_) => {
              'myRelations': ['아들', '사위'],
            },
        'PATCH /personas/p1/shares/s1': (body) => {
              'shareId': 's1',
              ...(body as Map).cast<String, dynamic>(),
              'editedBy': 'member',
            },
        'GET /personas': (_) => const [],
      });
      ApiClient.useAdapterForTests(server);
      final persona = member();
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: persona)));
      await _settle(tester);
      await _tap(tester, find.byTooltip('더 보기'));
      await _tap(tester, find.text('나에 대한 정보 고치기'));
      final term = find.widgetWithText(TextField, '엄마');
      await tester.enterText(term, '어머니');
      await _tap(tester, find.text('저장하기'));
      expect((server.bodies['PATCH /personas/p1/shares/s1'] as Map)['addressTerm'], '어머니');
      expect(persona['addressTerm'], '어머니');
      expect(_shown('어머니께\n먼저 말을 걸어보세요'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('나 자신 대화 상대면 만든 분을 부르는 말은 묻지 않고, 부르는 말 추천은 서버 것을 쓴다', (tester) async {
      _phone(tester, height: 1400);
      ApiClient.useAdapterForTests(chatServer(view(), extra: {
        'GET /intake/meta': (_) => {
              'myRelations': ['아들', '사위'],
            },
      }));
      final persona = {
        ...member(),
        'isSelf': true,
        'addressSuggestions': const {
          '사위': ['장모님'],
        },
        'me': const {'shareId': 's1', 'myName': '민호', 'myRelation': '사위', 'addressTerm': '엄마'},
      };
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: persona)));
      await _settle(tester);
      await _tap(tester, find.byTooltip('더 보기'));
      await _tap(tester, find.text('나에 대한 정보 고치기'));
      expect(_shown('장모님'), findsOneWidget);
      expect(find.text('장인어른'), findsNothing); // 앱 안의 표(두 분 모두)가 아니라 서버 추천
      await _tap(tester, find.text('더 적기 (선택)'));
      expect(_has('내 앞에서 그분이 자기를 가리키던 말'), findsOneWidget);
      expect(_has('내가 지은님을 부르는 말'), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('요금제 보기가 멈춤을 알려 주면 처음부터 입력 칸 대신 그 까닭', (tester) async {
      ApiClient.useAdapterForTests(chatServer(view(paused: {
        'reason': 'plan',
        'text': '지금은 함께 이야기할 수 없어요. 지은님이 가족 요금제로 바꾸면 다시 이야기할 수 있어요. 나눈 대화는 그대로 볼 수 있어요.',
      })));
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: member())));
      await _settle(tester);
      expect(_has('지은님이 가족 요금제로 바꾸면'), findsOneWidget);
      expect(find.byType(TextField), findsNothing);
      expect(find.text('요금제 보기'), findsNothing);
    });

    testWidgets('함께 쓰는 횟수를 다 쓰면 글로 대답한다는 안내만(요금제 보기 없이)', (tester) async {
      ApiClient.useAdapterForTests(chatServer(view(textOnly: true)));
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: member())));
      await _settle(tester);
      expect(_has('가족이 함께 쓰는 대화를 다 써서 지금은 목소리 대신 글로 대답해요.'), findsOneWidget);
      expect(find.text('요금제 보기'), findsNothing);
      expect(find.byType(TextField), findsOneWidget);
    });

    testWidgets('만든 사람의 더 보기 맨 위에는 가족과 함께 쓰기', (tester) async {
      _phone(tester);
      ApiClient.useAdapterForTests(chatServer({
        'personaId': 'p1',
        'status': 'PRELAUNCH',
        'active': true,
        'conversationsLimit': null,
        'conversationsLeft': null,
      }));
      final persona = {
        'id': 'p1',
        'role': 'owner',
        'members': 2,
        'addressTerm': '할머니',
        'relation': '할머니',
        'step': 'READY',
        'hasVoice': true,
        'origin': null,
        'jobs': <String, dynamic>{},
      };
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: PersonaChatPage(persona: persona)));
      await _settle(tester);
      expect(find.text('기억 더 채우기'), findsOneWidget);
      await _tap(tester, find.byTooltip('더 보기'));
      expect(find.text('가족과 함께 쓰기'), findsOneWidget);
      expect(_shown('가족 2명과 함께 쓰는 중'), findsOneWidget);
      expect(find.text('이 대화 상대 지우기'), findsOneWidget);
      expect(find.text('함께 쓰기 그만두기'), findsNothing);
      expect(
        tester.getTopLeft(find.text('가족과 함께 쓰기')).dy,
        lessThan(tester.getTopLeft(find.text('정보 고치기')).dy),
      );
    });

    testWidgets('기억 인터뷰 받기는 대화 화면 위 기억 더 채우기 옆에 있다(더 보기 안에는 없다)', (tester) async {
      ApiClient.useAdapterForTests(chatServer({
        'personaId': 'p1',
        'status': 'PRELAUNCH',
        'active': true,
        'conversationsLimit': null,
        'conversationsLeft': null,
      }));
      final persona = {
        'id': 'p1',
        'role': 'owner',
        'members': 0,
        'addressTerm': '할머니',
        'relation': '할머니',
        'step': 'READY',
        'hasVoice': true,
        'origin': null,
        'jobs': <String, dynamic>{},
      };
      final router = GoRouter(
        initialLocation: '/chat',
        routes: [
          GoRoute(path: '/chat', builder: (context, state) => PersonaChatPage(persona: persona)),
          GoRoute(
            path: '/p/:id/interview',
            builder: (context, state) => Scaffold(body: Text('인터뷰 ${state.pathParameters['id']}')),
          ),
        ],
      );

      // 넓은 화면: 이름 그대로, 한마디는 글과 함께
      tester.view.physicalSize = const Size(1200, 1400);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(MaterialApp.router(theme: buildTheme(), routerConfig: router));
      await _settle(tester);
      expect(find.text('기억 더 채우기'), findsOneWidget);
      expect(find.text('기억 인터뷰 받기'), findsOneWidget);
      expect(find.text('대화 한마디'), findsOneWidget);
      // 기억 더 채우기 오른쪽
      // 같은 줄(설명 길이가 달라 제목 높이는 조금 다르다)
      expect(tester.getCenter(find.text('기억 인터뷰 받기')).dy,
          closeTo(tester.getCenter(find.text('기억 더 채우기')).dy, 30));
      expect(tester.getTopLeft(find.text('기억 인터뷰 받기')).dx,
          greaterThan(tester.getTopLeft(find.text('기억 더 채우기')).dx));

      // 휴대폰: 줄인 이름, 한마디는 아이콘만(도움말로 이름이 남는다)
      tester.view.physicalSize = const Size(390, 1400);
      await _settle(tester);
      expect(find.text('기억 더 채우기'), findsOneWidget);
      expect(find.text('기억 인터뷰'), findsOneWidget);
      expect(find.text('한마디'), findsNothing);
      expect(find.byTooltip('이 대화에 한마디 남기기'), findsOneWidget);

      // 더 보기 안에서는 빠졌다
      await _tap(tester, find.byTooltip('더 보기'));
      expect(find.text('기억 인터뷰 받기'), findsNothing);
      await tester.tapAt(const Offset(10, 10));
      await _settle(tester);

      await _tap(tester, find.text('기억 인터뷰'));
      expect(find.text('인터뷰 p1'), findsOneWidget);
    });
  });
}
