import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/analytics.dart';
import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/session.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/admin/dev_stats_page.dart';

/// 서버 GET /admin/stats 응답 모양(prelaunch_spec B) 그대로의 가짜.
Map<String, dynamic> fakeStats(int days) => {
      'generatedAt': '2026-10-13T09:30:00Z',
      'paymentsOpen': false,
      'days': days,
      'totals': {
        'users': 1234,
        'deletedUsers': 3,
        'personas': 88,
        'personasReady': 61,
        'conversations': 420,
        'messages': 9876,
        'voiceMessages': 321,
        'replyPlays': 555,
        'subscriptionRequests': 17,
        'feedbackUp': 40,
        'feedbackDown': 5,
        'errors24h': 2,
      },
      'active': {'d1': 12, 'd7': 45, 'd30': 130},
      'funnel': [
        {'key': 'signed_up', 'label': '가입', 'users': 200},
        {'key': 'consented', 'label': '동의 완료', 'users': 180},
        {'key': 'persona_created', 'label': '대화 상대 만들기 시작', 'users': 90},
        {'key': 'recordings_uploaded', 'label': '녹음 올림', 'users': 70},
        {'key': 'build_done', 'label': '다 만듦', 'users': 60},
        {'key': 'first_message', 'label': '첫 대화', 'users': 50},
        {'key': 'engaged', 'label': '대화 10번 이상', 'users': 25},
        {'key': 'subscription_requested', 'label': '구독 신청', 'users': 10},
      ],
      'personasByStep': {'UPLOAD': 10, 'SPEAKERS': 5, 'QUESTIONS': 7, 'BUILDING': 5, 'READY': 61},
      'lifeStatus': {'deceased': 50, 'alive': 30, 'unable': 8},
      'voicesByProvider': {'fish': 12, 'minimax': 40, 'elevenlabs': 18, 'elevenlabs_pvc': 3, 'none': 27},
      'requestsByPlan': {'memories': 9, 'family': 6, 'premium': 2},
      'builds': {'started': 70, 'done': 61, 'failed': 4, 'avgSeconds': 200.4},
      'daily': [
        for (var i = 0; i < days; i++)
          {
            'date': '2026-${(i < 30 ? 9 : 10).toString().padLeft(2, '0')}-${(i % 28 + 1).toString().padLeft(2, '0')}',
            'signups': i % 5,
            'activeUsers': 10 + i % 7,
            'messages': 100 + i * 3,
            'builds': i % 3,
            'requests': i % 4 == 0 ? 1 : 0,
          },
      ],
      'topScreens': [
        {'screen': 'persona_chat', 'views': 900, 'users': 120},
        {'screen': 'home', 'views': 700, 'users': 130},
        {'screen': 'consent_create', 'views': 40, 'users': 35},
        {'screen': 'mystery_screen', 'views': 3, 'users': 1},
      ],
      'recentRequests': [
        {
          'createdAt': '2026-10-12T08:00:00',
          'email': 'son@example.com',
          'personaTitle': '할머니',
          'relation': '외할머니',
          'plan': 'family',
          'planName': '가족',
          'status': 'REQUESTED',
        },
        {
          'createdAt': '2026-10-11T08:00:00Z',
          'email': 'daughter@example.com',
          'personaTitle': '아버지',
          'relation': '아버지',
          'plan': 'memories',
          'planName': null,
          'status': 'CANCELED',
        },
      ],
      'recentSignups': [
        {'createdAt': '2026-10-13T01:00:00Z', 'email': 'new@example.com', 'method': 'kakao'},
        // 카카오가 이메일을 주지 않은 가입자: 빈칸 대신 이름
        {'createdAt': '2026-10-13T02:00:00Z', 'email': null, 'name': '김지우', 'method': 'kakao'},
      ],
    };

/// 서버 users·userStages(사람별 진행 단계) 모양 그대로의 가짜. [extra]명은 '가입만' 사람을 더 붙인다.
Map<String, dynamic> fakePeople({int extra = 0}) {
  Map<String, dynamic> person(String? email, String stage, String label, {Map<String, dynamic> more = const {}}) => {
        'createdAt': '2026-10-20T01:00:00Z',
        'email': email,
        'name': '이름',
        'method': 'email',
        'stage': stage,
        'stageLabel': label,
        'personaCount': 0,
        'readyCount': 0,
        'conversationCount': 0,
        'messageCount': 0,
        'lastChatAt': null,
        'lastActiveAt': '2026-10-20T01:00:00Z',
        'requestStatus': null,
        'requestPlan': null,
        'subscriptionPlan': null,
        'role': null,
        'memberOf': 0,
        'familyJoined': 0,
        ...more,
      };
  return {
    'userStages': [
      {'key': 'signed_up', 'label': '가입만', 'reached': 5 + extra, 'current': 1 + extra, 'branch': false},
      {'key': 'consented', 'label': '동의 완료', 'reached': 4, 'current': 0, 'branch': false},
      {'key': 'persona_started', 'label': '만들기 시작', 'reached': 3, 'current': 0, 'branch': false},
      {'key': 'materials', 'label': '녹음·자료 올림', 'reached': 3, 'current': 0, 'branch': false},
      {'key': 'voice', 'label': '목소리 고름', 'reached': 2, 'current': 0, 'branch': false},
      {'key': 'building', 'label': '만드는 중', 'reached': 2, 'current': 0, 'branch': false},
      {'key': 'ready', 'label': '완성', 'reached': 2, 'current': 0, 'branch': false},
      {'key': 'chatting', 'label': '대화 중', 'reached': 2, 'current': 1, 'branch': false},
      {'key': 'requested', 'label': '구독 신청', 'reached': 2, 'current': 1, 'branch': false},
      {'key': 'subscribed', 'label': '결제함', 'reached': 1, 'current': 1, 'branch': false},
      {'key': 'family_member', 'label': '가족 초대로 들어옴', 'reached': 1, 'current': 1, 'branch': true},
    ],
    'users': [
      person('fam@example.com', 'family_member', '가족 초대로 들어옴',
          more: {'method': 'google', 'role': 'member', 'memberOf': 1, 'conversationCount': 1, 'messageCount': 3}),
      person('req@example.com', 'requested', '구독 신청', more: {
        'personaCount': 2,
        'readyCount': 1,
        'conversationCount': 2,
        'messageCount': 1234,
        'requestStatus': 'REQUESTED',
        'requestPlan': 'family',
        'role': 'owner',
        'familyJoined': 2,
      }),
      person('chat@example.com', 'chatting', '대화 중', more: {'personaCount': 1, 'readyCount': 1, 'messageCount': 7}),
      person('paid@example.com', 'subscribed', '결제함', more: {'subscriptionPlan': 'memories', 'personaCount': 1}),
      // 카카오가 이메일을 주지 않은 가입자: 빈칸 대신 이름
      person(null, 'signed_up', '가입만', more: {'name': '박가입', 'method': 'kakao'}),
      for (var i = 0; i < extra; i++) person('more$i@example.com', 'signed_up', '가입만'),
    ],
  };
}

void main() {
  testWidgets('사람별 진행 단계: 단계별 사람 수·사람 목록·단계 칩으로 거르기', (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: DevStatsPage(loader: (days) async => {...fakeStats(days), ...fakePeople()}),
    ));
    await tester.pumpAndSettle();

    expect(find.text('사람별 진행 단계'), findsOneWidget);
    // 단계별 사람 수: 지금 사람이 없는 단계(목소리 고름)도 막대로는 보인다. 가족은 갈래
    expect(find.text('목소리 고름'), findsOneWidget);
    expect(find.textContaining('5명', findRichText: true), findsWidgets);
    expect(find.text('갈래 · 제 대화 상대 없이 가족으로 들어온 사람'), findsOneWidget);
    // 사람 목록: 단계 배지와 칸들
    expect(find.text('req@example.com'), findsOneWidget);
    expect(find.text('박가입 · 이메일 없음'), findsOneWidget);
    expect(find.text('1 / 2'), findsOneWidget); // 대화 상대 완성/전체
    expect(find.text('1,234'), findsWidgets);
    expect(find.text('가족 · 신청함'), findsOneWidget);
    expect(find.text('결제함 · 추억'), findsOneWidget);
    expect(find.text('초대받은 가족'), findsOneWidget);
    expect(find.text('가족 2명 들어옴'), findsOneWidget);
    expect(find.text('가족 초대로 들어옴'), findsNWidgets(2)); // 막대 이름 + 배지
    expect(find.text('구글'), findsOneWidget);

    // 단계 칩: 지금 사람이 있는 단계만, 고르면 그 단계 사람만
    expect(find.text('전체 5'), findsOneWidget);
    expect(find.text('목소리 고름 0'), findsNothing);
    await tester.ensureVisible(find.text('구독 신청 1'));
    await tester.tap(find.text('구독 신청 1'));
    await tester.pumpAndSettle();
    expect(find.text('req@example.com'), findsOneWidget);
    expect(find.text('chat@example.com'), findsNothing);
    expect(find.text('fam@example.com'), findsNothing);

    await tester.tap(find.text('전체 5'));
    await tester.pumpAndSettle();
    expect(find.text('chat@example.com'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('사람이 많으면 50명씩 끊어 보고 더 본다', (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: DevStatsPage(loader: (days) async => {...fakeStats(days), ...fakePeople(extra: 60)}),
    ));
    await tester.pumpAndSettle();
    expect(find.text('전체 65'), findsOneWidget);
    expect(find.text('more44@example.com'), findsOneWidget);
    expect(find.text('more45@example.com'), findsNothing);
    expect(find.text('더 보기 (남은 15명)'), findsOneWidget);

    await tester.ensureVisible(find.text('더 보기 (남은 15명)'));
    await tester.tap(find.text('더 보기 (남은 15명)'));
    await tester.pumpAndSettle();
    expect(find.text('more59@example.com'), findsOneWidget);
    expect(find.textContaining('더 보기'), findsNothing);

    // 칩으로 거르면 처음 50명부터 다시
    await tester.ensureVisible(find.text('가입만 61'));
    await tester.tap(find.text('가입만 61'));
    await tester.pumpAndSettle();
    expect(find.text('더 보기 (남은 11명)'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('사용 통계 화면이 서버 통계를 그대로 그린다(PC 폭)', (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final asked = <int>[];
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: DevStatsPage(loader: (days) async {
        asked.add(days);
        return fakeStats(days);
      }),
    ));
    expect(find.text('통계를 불러오는 중이에요…'), findsOneWidget);
    await tester.pumpAndSettle();

    expect(asked, [30]);
    expect(find.text('개발자 화면 · 사용 통계'), findsOneWidget);
    expect(find.text('결제 전 모드 (구독 신청만)'), findsOneWidget);
    // 숫자 카드
    expect(find.text('1,234'), findsOneWidget);
    expect(find.text('9,876'), findsOneWidget);
    expect(find.text('활동 사용자 · 7일'), findsOneWidget);
    // 퍼널: 앞 단계 대비 % (동의 180/200 = 90%)
    expect(find.text('대화 상대 만들기 시작'), findsOneWidget);
    expect(find.textContaining('90%', findRichText: true), findsWidgets);
    // 분포·요약
    // 지금 목소리(Fish)와 다시 만들기 전의 예전 목소리(종료)
    expect(find.text('Fish drama-3'), findsOneWidget);
    expect(find.text('MiniMax (전화 녹음, 종료)'), findsOneWidget);
    expect(find.text('대화가 어려우신 분(예전 상태)'), findsOneWidget);
    expect(find.text('3분 20초'), findsOneWidget);
    // 표: 연락용 구독 신청(대화 상대 + 관계), 많이 본 화면, 최근 가입
    expect(find.text('son@example.com'), findsOneWidget);
    expect(find.text('할머니 (외할머니)'), findsOneWidget);
    expect(find.text('김지우 · 이메일 없음'), findsOneWidget);
    expect(find.text('아버지'), findsOneWidget);
    expect(find.text('신청함'), findsOneWidget);
    expect(find.text('추억'), findsWidgets); // planName 이 없으면 요금제 이름표
    expect(find.text('대화 상대(만들기·대화) · persona_chat'), findsOneWidget);
    expect(find.text('만들기 전 동의 (예전) · consent_create'), findsOneWidget);
    expect(find.text('mystery_screen'), findsOneWidget);
    expect(find.text('카카오'), findsNWidgets(2));
    expect(find.text('신청자 이메일 복사 (1)'), findsOneWidget);

    // 일별 막대: 고르면 합계 줄이 바뀐다
    expect(find.textContaining('가입 · 합계'), findsOneWidget);
    await tester.tap(find.text('메시지').last); // 숫자 카드 '메시지' 말고 일별 고르기 칩
    await tester.pumpAndSettle();
    expect(find.textContaining('메시지 · 합계'), findsOneWidget);

    // 기간을 바꾸면 그 기간으로 다시 읽는다
    await tester.tap(find.text('7일'));
    await tester.pumpAndSettle();
    expect(asked, [30, 7]);
    expect(find.text('최근 7일'), findsWidgets);
    expect(tester.takeException(), isNull);
  });

  testWidgets('휴대폰 폭에서도 넘치지 않는다', (tester) async {
    tester.view.physicalSize = const Size(360, 780);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: DevStatsPage(loader: (days) async => {...fakeStats(days), ...fakePeople()}),
    ));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    // 사람별 진행 단계(막대·칩·옆으로 미는 표)도 넘치지 않는다
    await tester.scrollUntilVisible(find.text('전체 5'), 400, scrollable: find.byType(Scrollable).first);
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await tester.scrollUntilVisible(find.text('최근 가입'), 400, scrollable: find.byType(Scrollable).first);
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  // 경고 글은 단어 중간에서 끊기지 않게 이음표가 들어 있어(keepAll) 빼고 찾는다
  Finder shown(String part) => find.byWidgetPredicate(
        (w) => w is Text && (w.data ?? '').replaceAll('⁠', '').contains(part),
      );

  testWidgets('목소리 회사(Fish) 문제는 OpenAI 가 아니라 목소리 경고로 띄운다', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: DevStatsPage(
        // 서버는 목소리 회사 문제를 voiceProviderTrouble 에 따로 보낸다
        loader: (days) async => {
          ...fakeStats(days),
          'aiProviderTrouble': null,
          'voiceProviderTrouble': {'code': 'VOICE_PROVIDER_CREDITS', 'at': '2026-10-17T01:00:00Z'},
        },
      ),
    ));
    await tester.pumpAndSettle();
    expect(shown('Fish Audio 크레딧이 떨어져'), findsOneWidget);
    expect(shown('OpenAI'), findsNothing);
  });

  testWidgets('AI 회사(OpenAI) 문제와 목소리 회사 문제는 함께 뜰 수 있다', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: DevStatsPage(
        loader: (days) async => {
          ...fakeStats(days),
          'aiProviderTrouble': {'code': 'AI_PROVIDER_QUOTA', 'at': '2026-10-17T01:00:00Z'},
          'voiceProviderTrouble': {'code': 'VOICE_PROVIDER_KEY', 'at': '2026-10-17T02:00:00Z'},
        },
      ),
    ));
    await tester.pumpAndSettle();
    expect(shown('OpenAI 크레딧이 떨어져'), findsOneWidget);
    expect(shown('Fish Audio 키가 막혔어요'), findsOneWidget);
  });

  testWidgets('서버 데이터가 비어 있거나 모양이 달라도 그린다', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: DevStatsPage(loader: (days) async => {'days': days}),
    ));
    await tester.pumpAndSettle();
    expect(find.text('아직 퍼널 자료가 없어요.'), findsOneWidget);
    expect(find.text('아직 가입한 사람이 없어요.'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('불러오지 못하면 알려 주고 다시 불러온다', (tester) async {
    var calls = 0;
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(),
      home: DevStatsPage(loader: (days) async {
        calls++;
        if (calls == 1) throw const ApiException('이 화면은 열 수 없어요.');
        return fakeStats(days);
      }),
    ));
    await tester.pumpAndSettle();
    expect(find.text('통계를 불러오지 못했어요.'), findsOneWidget);
    expect(find.text('이 화면은 열 수 없어요.'), findsOneWidget);

    await tester.tap(find.text('다시 불러오기'));
    await tester.pumpAndSettle();
    expect(find.text('통계를 불러오지 못했어요.'), findsNothing);
    expect(find.text('son@example.com'), findsOneWidget);
  });

  test('통계 화면 이름은 주소에서 id 를 뺀 모양이다', () {
    expect(Analytics.screenName('/'), 'home');
    expect(Analytics.screenName('/p/abc-123'), 'persona_chat');
    expect(Analytics.screenName('/p/abc-123/more'), 'persona_more');
    expect(Analytics.screenName('/p/abc-123/recordings'), 'persona_recordings');
    expect(Analytics.screenName('/p/abc-123/materials'), 'persona_materials');
    expect(Analytics.screenName('/p/abc-123/materials/add'), 'persona_material_add');
    expect(Analytics.screenName('/p/abc-123/edit'), 'persona_edit');
    expect(Analytics.screenName('/new'), 'persona_create');
    expect(Analytics.screenName('/plans'), 'plans');
    expect(Analytics.screenName('/account'), 'account');
    expect(Analytics.screenName('/consent'), 'consent');
    expect(Analytics.screenName('/legal/terms'), 'legal');
    expect(Analytics.screenName('/login'), 'login');
    expect(Analytics.screenName('/admin'), 'admin');
    expect(Analytics.screenName('/admin/stats'), 'admin_stats');
  });

  test('화면이 바뀔 때만 남기고, 같은 화면 연속은 한 번만 보낸다', () async {
    final sent = <String>[];
    Analytics.sender = (name, props) async => sent.add(props.isEmpty ? name : '$name $props');
    addTearDown(() => session.user = null);

    // 로그인 전에는 보내지 않는다
    Analytics.trackLocation(Uri.parse('/login'));
    expect(sent, isEmpty);

    session.user = {'id': 'u1', 'role': 'USER'};
    Analytics.trackLocation(Uri.parse('/p/a'));
    Analytics.trackLocation(Uri.parse('/p/b')); // 다른 대화 상대라도 같은 화면
    Analytics.trackLocation(Uri.parse('/plans?persona=a'));
    Analytics.trackLocation(Uri.parse('/legal/terms'));
    Analytics.event('more_tile_clicked');
    await Future<void>.delayed(Duration.zero);
    expect(sent, [
      'screen_view {screen: persona_chat}',
      'screen_view {screen: plans}',
      'plans_viewed {withPersona: true}',
      'screen_view {screen: legal}',
      'legal_doc_opened {doc: terms}',
      'more_tile_clicked',
    ]);

    // 동의 화면은 하나다(모든 동의를 가입할 때 받는다). 주소 뒤(from·예전 for=create)는 보지 않는다
    sent.clear();
    Analytics.trackLocation(Uri.parse('/consent?from=%2Fnew'));
    Analytics.trackLocation(Uri.parse('/consent?for=create&from=%2Fnew'));
    Analytics.trackLocation(Uri.parse('/consent'));
    Analytics.trackLocation(Uri.parse('/support'));
    Analytics.trackLocation(Uri.parse('/p/abc-123/support'));
    await Future<void>.delayed(Duration.zero);
    expect(sent, [
      'screen_view {screen: consent}',
      'screen_view {screen: support}',
      'screen_view {screen: persona_support}',
    ]);

    // 보내다 실패해도 조용히 넘어간다
    Analytics.sender = (name, props) async => throw const ApiException('x');
    Analytics.trackLocation(Uri.parse('/account'));
    await Future<void>.delayed(Duration.zero);
  });
}
