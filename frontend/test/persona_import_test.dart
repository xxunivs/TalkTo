import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/router.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/admin/import_api.dart';
import 'package:talkto_fe/features/admin/persona_import_page.dart';
import 'package:talkto_fe/features/personas/imported.dart';
import 'package:talkto_fe/features/personas/persona_more_page.dart';

// 시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다).

/// 서버 묶음 모양(app/schemas/persona_import.py PersonaBundle, format talkto-persona-bundle v1)의 가짜.
Map<String, dynamic> sampleBundle({bool voice = true}) => {
      'format': 'talkto-persona-bundle',
      'version': 1,
      'source': {'subject': 'sample_manual', 'promptSha256': 'ab' * 32, 'builtAt': '2026-10-17T01:00:00Z'},
      'persona': {'name': '김예시', 'relation': '할머니', 'addressTerm': '할머니', 'status': 'deceased'},
      'prompt': {
        'legacy': '예시 지침 본문',
        'rules': '예시 공통 규칙 {{death_rule}}',
        'deathRules': {'deny': '예시', 'brief': '예시', 'ask_only': '예시 {{death_answer}}', 'open': '예시'},
        'bridge': '예시 연결 문단 {{listener_line}}',
      },
      'memories': [
        {'legacyId': 'm1', 'category': '가족', 'title': '시장 나들이', 'content': '예시 본문 하나', 'sensitive': false},
        {'legacyId': 'm2', 'category': '건강', 'title': '무릎', 'content': '예시 본문 둘', 'sensitive': true},
        {'legacyId': 'm3', 'category': '신앙', 'title': '기도', 'content': '예시 본문 셋', 'sensitive': true},
      ],
      'probeEmbeddings': [
        {'legacyId': 'm1', 'embedding': [0.1, 0.2, 0.3]},
      ],
      if (voice) 'voice': {'voiceId': 'fish_${'0' * 32}'},
    };

final _users = <Map<String, dynamic>>[
  {
    'id': 'u-admin',
    'name': '운영자',
    'role': 'ADMIN',
    'status': 'ACTIVE',
    'createdAt': '2026-10-01T00:00:00Z',
    'providers': <String>[],
    'emailMasked': 'o***@example.com',
    'personaCount': 0,
  },
  {
    'id': 'u-kakao',
    'name': '시험 주인',
    'role': 'USER',
    'status': 'ACTIVE',
    'createdAt': '2026-10-12T00:00:00Z',
    'providers': ['kakao'],
    'emailMasked': null,
    'personaCount': 1,
    'importedPersonas': [
      {'id': 'p-old', 'name': '박예시', 'createdAt': '2026-10-15T12:00:00Z'},
    ],
  },
];

/// 서버 대신 부른 기록을 남기는 가짜.
class FakeImportApi extends PersonaImportApi {
  final searches = <String>[];
  final bodies = <Map<String, dynamic>>[];
  final removed = <String>[];
  Object? importError;

  @override
  Future<List<Map<String, dynamic>>> searchUsers(String q) async {
    searches.add(q);
    return _users;
  }

  @override
  Future<Map<String, dynamic>> importPersona(Map<String, dynamic> body) async {
    bodies.add(body);
    if (importError != null) throw importError!;
    // 서버 persona_import.run 이 돌려주는 모양(내용 없이 건수·해시·경고)
    final prompt = body['prompt'] as Map;
    final result = {
      'dryRun': body['dryRun'],
      'personaId': body['dryRun'] == true ? null : 'p-new',
      'target': {
        'id': 'u-kakao',
        'name': '시험 주인',
        'role': 'USER',
        'status': 'ACTIVE',
        'createdAt': '2026-10-12T00:00:00Z',
        'providers': ['kakao'],
        'emailMasked': null,
        'personaCount': 1,
      },
      'basics': {'name': '김예시', 'relation': '할머니', 'addressTerm': '할머니', 'status': 'deceased', 'myName': '-'},
      'prompt': {
        'mode': prompt['mode'],
        'deathPolicy': prompt['deathPolicy'],
        'deathAnswerSet': prompt['deathAnswer'] != null,
        'chars': 1234,
        'sha256': 'cd' * 32,
        'legacyChars': 7,
        'legacySha256': 'ab' * 32,
        'legacyUnchanged': true,
      },
      'memories': {
        'total': 3,
        'ready': 3,
        'sensitive': 2,
        'religion': 1,
        'health': 1,
        'categories': {'가족': 1, '건강': 1, '신앙': 1},
      },
      'embedding': {'model': 'text-embedding-3-small', 'dimensions': 1536, 'mvpModelMatch': 0.9987, 'mvpDimensions': 3},
      'voice': {
        'attach': 'after_owner_pledge',
        'status': 'pending',
        'hasVoiceId': true,
        'probe': {'ok': true, 'contentType': 'audio/mpeg', 'audio': base64Encode(List.filled(300, 7))},
      },
      'check': {'query': '고향 이야기 해 주세요', 'found': 2},
      // 서버가 실수로 긴 글을 실어도 화면에 그리지 않는다
      'promptPreview': '예' * 500,
      'warnings': ['이 계정의 대화 상대가 한도를 넘어요(경고만)'],
    };
    return result;
  }

  @override
  Future<void> removeImport(String personaId) async => removed.add(personaId);
}

Widget _app(FakeImportApi api, {Map<String, dynamic>? bundle, List<int>? bytes}) => MaterialApp(
      theme: buildTheme(),
      home: PersonaImportPage(
        api: api,
        pickBundle: () async => (
          name: 'bundle.json',
          bytes: bytes ?? utf8.encode(jsonEncode(bundle ?? sampleBundle())),
        ),
      ),
    );

ButtonStyleButton _button(WidgetTester tester, String label) => tester.widget<ButtonStyleButton>(
      find.ancestor(of: find.text(label), matching: find.byWidgetPredicate((w) => w is ButtonStyleButton)).first,
    );

Future<void> _tap(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

Finder _inDialog(String text) => find.descendant(of: find.byType(AlertDialog), matching: find.text(text));

/// 대화 상대 서버 대신(가져온 대화 상대 하나). 거두면 그 범주 기억을 끄고 동의하면 켠다(서버
/// persona_setup.persona_consent 의 import 길). [serverState] 가 false 면 예전 서버처럼
/// 상세에 sensitiveMemories 를 싣지 않는다.
class _FakePersonaServer implements HttpClientAdapter {
  final bool serverState;
  final log = <String>[];
  final consents = <String, bool>{'sensitiveReligion': false, 'sensitiveHealth': false};
  final memories = <String, String>{'religion': 'on', 'health': 'on'};

  _FakePersonaServer({this.serverState = true});

  Map<String, dynamic> get _consents => {
        'voice': false,
        'recordingPledge': false,
        'materialPledge': false,
        ...consents,
      };

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    final chunks = <int>[];
    if (requestStream != null) {
      await for (final chunk in requestStream) {
        chunks.addAll(chunk);
      }
    }
    final body = chunks.isEmpty ? null : jsonDecode(utf8.decode(chunks));
    log.add('${options.method} ${options.path}${body == null ? '' : ' ${jsonEncode(body)}'}');
    Object? data;
    if (options.method == 'GET' && options.path == '/personas/p1') {
      data = {
        'id': 'p1',
        'name': '김예시',
        'relation': '할머니',
        'addressTerm': '할머니',
        'lifeStatus': 'DECEASED',
        'step': 'READY',
        'hasVoice': false,
        'origin': 'import',
        'voicePending': false,
        'basics': {'name': '김예시', 'relation': '할머니', 'addressTerm': '할머니', 'status': 'deceased'},
        'answers': <String, dynamic>{},
        'recordings': const [],
        'jobs': <String, dynamic>{},
        'consents': _consents,
        if (serverState) 'sensitiveMemories': {...memories},
      };
    } else if (options.method == 'GET' && options.path == '/intake/meta') {
      data = {
        'moreModules': [
          {'id': '예시 주제', 'lead': '예시 주제 안내'},
        ],
        'moreQuestions': [
          {'id': 'X-01', 'section': '예시 주제', 'type': 'text', 'label': '예시 질문'},
          {'id': 'X-02', 'section': '예시 주제', 'type': 'text', 'label': '예시 종교 질문', 'sensitive': 'religion'},
        ],
      };
    } else if (options.method == 'POST' && options.path == '/personas/p1/consents') {
      final category = '${body['type']}'.replaceFirst('sensitive_', '');
      final agreed = body['agreed'] == true;
      consents[category == 'religion' ? 'sensitiveReligion' : 'sensitiveHealth'] = agreed;
      memories[category] = agreed ? 'on' : 'off';
      data = {'consents': _consents, 'updating': false};
    } else {
      return ResponseBody.fromString(jsonEncode({'detail': 'not found'}), 404, headers: {
        Headers.contentTypeHeader: ['application/json'],
      });
    }
    return ResponseBody.fromString(jsonEncode({'data': data}), 200, headers: {
      Headers.contentTypeHeader: ['application/json'],
    });
  }

  @override
  void close({bool force = false}) {}
}

/// 가짜 서버 응답(Future)을 다 받고 화면을 그린다.
Future<void> _settle(WidgetTester tester) async {
  for (var i = 0; i < 6; i++) {
    await tester.pump(const Duration(milliseconds: 20));
  }
  await tester.pumpAndSettle();
}

Widget _morePage() => MaterialApp(theme: buildTheme(), home: const PersonaMorePage(personaId: 'p1'));

/// 안내(Notice) 글은 단어 중간에서 끊기지 않게 이음표가 들어 있어(keepAll) 빼고 찾는다.
Finder _shown(String part) => find.byWidgetPredicate(
      (w) => w is Text && (w.data ?? '').replaceAll('\u2060', '').contains(part),
    );

void main() {
  testWidgets('계정 고르기 → 묶음 → 시험해 보기 → 가져오기 → 되돌리기', (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final api = FakeImportApi();
    await tester.pumpWidget(_app(api));
    await tester.pumpAndSettle();

    // 처음에 최근 가입 순으로 후보를 읽는다. 관리자·카카오 표시가 보인다
    expect(api.searches, ['']);
    expect(find.text('시험 주인'), findsOneWidget);
    expect(find.text('카카오'), findsOneWidget);
    expect(find.text('관리자'), findsOneWidget);
    expect(find.textContaining('o***@example.com'), findsOneWidget);
    expect(_button(tester, '시험해 보기 (쓰지 않음)').onPressed, isNull);

    // 관리자 계정을 고르면 카카오가 없다고 알려 준다
    await _tap(tester, find.text('운영자'));
    expect(_shown('카카오 로그인이 없어요'), findsOneWidget);
    await _tap(tester, find.text('시험 주인'));
    expect(_shown('카카오 로그인이 없어요'), findsNothing);

    // 묶음을 고르면 건수만 보이고, 비어 있던 칸이 묶음 값으로 채워진다
    await _tap(tester, find.text('묶음 파일 고르기 (.json)'));
    expect(find.text('3건 · 민감(물으면만) 2건 · 종교·건강 범주 2건'), findsOneWidget);
    expect(find.textContaining('abababababababab'), findsOneWidget); // sha 앞 16자
    expect(find.text('예시 지침 본문'), findsNothing); // 본문은 그리지 않는다
    expect(find.text('김예시'), findsOneWidget); // 성함 칸
    expect(_button(tester, '가져오기').onPressed, isNull); // 시험해 보기 전에는 못 가져온다
    // 묶음 자리는 DECISIONS 대로(레포 밖 TalkTo\페르소나 가져오기), 주인 이름은 옛 가족 목록 글자 그대로
    expect(_shown('레포 밖(TalkTo\\페르소나 가져오기)'), findsOneWidget);
    expect(_shown('OneDrive 밖'), findsNothing);
    expect(_shown('가족 목록에 적힌 짧은 이름과 글자까지 똑같이'), findsOneWidget);
    expect(_shown("'종교·건강 이야기 동의'에서 그 범주 동의를 거두면 꺼지고"), findsOneWidget);

    // 시험해 보기(dryRun)
    await _tap(tester, find.text('시험해 보기 (쓰지 않음)'));
    expect(api.bodies, hasLength(1));
    final dry = api.bodies.single;
    expect(dry['dryRun'], isTrue);
    expect(dry['targetUserId'], 'u-kakao');
    expect(dry['requireProvider'], 'kakao');
    // 한마디는 비우면 넣지 않는다(지어내지 않는다)
    expect(dry['prompt'], {'mode': 'guarded', 'deathPolicy': 'ask_only'});
    expect(dry['overrides'], {'name': '김예시', 'relation': '할머니', 'addressTerm': '할머니'});
    expect(dry['voice'], {'attach': 'after_owner_pledge', 'probe': true});
    expect(dry.containsKey('checkQuery'), isFalse); // 비우면 서버 기본 질문
    expect((dry['bundle'] as Map)['memories'], hasLength(3));

    // 미리보기: 핵심 확인·건수·임베딩·경고, 미리 들어보기 단추. 소리·긴 글은 그리지 않는다
    expect(find.text('기억 READY 3 / 3'), findsOneWidget);
    expect(find.text('임베딩 text-embedding-3-small · 차원 1536'), findsOneWidget);
    expect(find.text('시험 질문으로 찾은 기억 2건'), findsOneWidget);
    expect(find.text('예전 지침 본문 그대로'), findsOneWidget);
    expect(find.text('목소리 미리 듣기 성공 · 귀로 확인해 주세요'), findsOneWidget);
    expect(find.text('1,536'), findsOneWidget);
    expect(find.text('0.9987'), findsOneWidget);
    expect(find.text('cdcdcdcdcdcdcdcd…'), findsOneWidget); // 지침 sha 는 앞 16자만
    expect(_shown('한도를 넘어요'), findsOneWidget);
    expect(find.text('미리 들어보기'), findsOneWidget);
    expect(find.text('(소리 · 화면에 그리지 않음)'), findsOneWidget);
    expect(find.text('(긴 글 500자 · 화면에 그리지 않음)'), findsOneWidget);
    expect(_button(tester, '가져오기').onPressed, isNotNull);

    // 시험해 본 뒤 입력을 바꾸면 다시 시험해야 가져올 수 있다
    final answer = find.widgetWithText(TextField, '물으면 할 한마디 (선택)');
    await tester.ensureVisible(answer);
    await tester.enterText(answer, '예시 한마디');
    await tester.pumpAndSettle();
    expect(_button(tester, '가져오기').onPressed, isNull);
    expect(_shown('입력이 바뀌었어요'), findsOneWidget);
    await _tap(tester, find.text('시험해 보기 (쓰지 않음)'));
    expect(api.bodies.last['prompt'], {'mode': 'guarded', 'deathPolicy': 'ask_only', 'deathAnswer': '예시 한마디'});
    expect(_button(tester, '가져오기').onPressed, isNotNull);

    // 가져오기: 한 번 묻고, 미리보기와 같은 내용을 dryRun false 로 보낸다
    await _tap(tester, find.text('가져오기'));
    expect(find.text('이 계정에 가져올까요?'), findsOneWidget);
    await _tap(tester, _inDialog('가져오기'));
    final real = api.bodies.last;
    expect(real['dryRun'], isFalse);
    expect({...real, 'dryRun': true}, {...api.bodies[api.bodies.length - 2]});
    expect(find.text('p-new'), findsWidgets); // 결과 · 되돌리기 칸
    expect(_button(tester, '가져오기').onPressed, isNull); // 두 번 가져오지 않는다
    expect(api.searches, ['', '']); // 가져온 뒤 대화 상대 수·가져온 목록을 새로 본다

    // 되돌리기
    await _tap(tester, find.text('되돌리기'));
    expect(find.text('가져온 대화 상대를 지울까요?'), findsOneWidget);
    await _tap(tester, _inDialog('되돌리기'));
    expect(api.removed, ['p-new']);
    expect(api.searches, ['', '', '']); // 대화 상대 수를 새로 본다
    expect(tester.takeException(), isNull);
  });

  testWidgets('나중에 다시 열어도 고른 계정의 가져온 대화 상대로 되돌리기 id 를 찾는다', (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final api = FakeImportApi();
    await tester.pumpWidget(_app(api));
    await tester.pumpAndSettle();
    expect(find.textContaining('가져온 대화 상대 1'), findsOneWidget); // 후보 표
    expect(find.text('박예시 · 2026-10-15'), findsNothing); // 계정을 고르기 전
    await _tap(tester, find.text('시험 주인'));
    await _tap(tester, find.text('박예시 · 2026-10-15'));
    expect(find.widgetWithText(TextField, 'p-old'), findsOneWidget);
    await _tap(tester, find.text('되돌리기'));
    await _tap(tester, _inDialog('되돌리기'));
    expect(api.removed, ['p-old']);
  });

  testWidgets('예전 글 그대로(verbatim)는 사망 정책을 보내지 않고, 카카오 검사는 끌 수 있다', (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final api = FakeImportApi();
    await tester.pumpWidget(_app(api, bundle: sampleBundle(voice: false)));
    await tester.pumpAndSettle();
    await _tap(tester, find.text('시험 주인'));
    await _tap(tester, find.text('묶음 파일 고르기 (.json)'));
    expect(find.text('묶음에 목소리가 없어요. 글 대화만 해요.'), findsOneWidget);
    await _tap(tester, find.text('예전 글 그대로 (verbatim)'));
    expect(find.text('사망·임종 이야기 (사망 정책)'), findsNothing);
    await _tap(tester, find.textContaining('카카오로 이은 계정만 받기'));
    await _tap(tester, find.text('시험해 보기 (쓰지 않음)'));

    final body = api.bodies.single;
    expect(body['prompt'], {'mode': 'verbatim'});
    // 서버 기본값이 kakao 라 끌 때는 null 을 보낸다
    expect(body.containsKey('requireProvider'), isTrue);
    expect(body['requireProvider'], isNull);
    expect(body['voice'], {'attach': 'none', 'probe': false});
  });

  testWidgets('서버가 거절하면 이유를 보이고 가져오기는 막힌 채다', (tester) async {
    tester.view.physicalSize = const Size(1400, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final api = FakeImportApi()..importError = Exception('같은 묶음을 이미 가져왔어요');
    await tester.pumpWidget(_app(api));
    await tester.pumpAndSettle();
    await _tap(tester, find.text('시험 주인'));
    await _tap(tester, find.text('묶음 파일 고르기 (.json)'));
    await _tap(tester, find.text('시험해 보기 (쓰지 않음)'));
    expect(_shown('같은 묶음을 이미 가져왔어요'), findsOneWidget);
    expect(_button(tester, '가져오기').onPressed, isNull);
  });

  testWidgets('묶음이 아닌 파일은 읽지 않는다', (tester) async {
    final api = FakeImportApi();
    await tester.pumpWidget(_app(api, bytes: utf8.encode('{"hello": 1}')));
    await tester.pumpAndSettle();
    await _tap(tester, find.text('묶음 파일 고르기 (.json)'));
    expect(_shown('묶음 파일을 읽지 못했어요'), findsOneWidget);
  });

  testWidgets('휴대폰 폭에서도 넘치지 않는다', (tester) async {
    tester.view.physicalSize = const Size(360, 780);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final api = FakeImportApi();
    await tester.pumpWidget(_app(api));
    await tester.pumpAndSettle();
    await _tap(tester, find.text('시험 주인'));
    await _tap(tester, find.text('묶음 파일 고르기 (.json)'));
    await _tap(tester, find.text('시험해 보기 (쓰지 않음)'));
    await tester.scrollUntilVisible(find.text('7. 되돌리기'), 400, scrollable: find.byType(Scrollable).first);
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('관리자 주소 /admin/import 가 있다', (tester) async {
    expect(router.configuration.findMatch(Uri.parse('/admin/import')).isError, isFalse);
  });

  group('가져온 대화 상대(origin: import)', () {
    test('만든 대화 상대는 관리 단추가 그대로이고, 가족과 함께 쓰기가 더해진다', () {
      final normal = personaActionsOf({'origin': null}, hasVoice: true);
      expect(
        normal,
        (
          edit: true,
          more: '기억 더 채우기 (더 그분답게)',
          recordings: true,
          voice: '목소리 다시 만들기',
          share: true,
          leave: false,
          profile: false,
        ),
      );
      expect(personaActionsOf({}, hasVoice: false).voice, '목소리 만들기');
      // 서버가 role 을 주어도 주인이면 같다
      expect(personaActionsOf({'role': 'owner'}, hasVoice: true), normal);
    });

    test('초대로 함께하는 가족은 내 정보 고치기와 그만두기만 있다(가져온 대화 상대여도)', () {
      const member = (
        edit: false,
        more: null,
        recordings: false,
        voice: null,
        share: false,
        leave: true,
        profile: true,
      );
      expect(personaActionsOf({'role': 'member'}, hasVoice: true), member);
      expect(personaActionsOf({'role': 'member', 'origin': 'import', 'voicePending': true}, hasVoice: false), member);
    });

    test('가져온 대화 상대는 덮어쓰는 단추를 숨기고, 기다리는 목소리 붙이기와 종교·건강 동의만 둔다', () {
      final pending = {'origin': 'import', 'voicePending': true};
      expect(
        personaActionsOf(pending, hasVoice: false),
        (
          edit: false,
          more: '종교·건강 이야기 동의',
          recordings: false,
          voice: '목소리 붙이기',
          // 가져온 대화 상대도 가족과 함께 쓸 수 있다
          share: true,
          leave: false,
          profile: false,
        ),
      );
      // 이미 붙었으면 다시 만들기도 없다
      expect(personaActionsOf(pending, hasVoice: true).voice, isNull);
      expect(personaActionsOf({'origin': 'import'}, hasVoice: false).voice, isNull);
      expect(hasPendingVoice({'voicePending': true}), isFalse); // 가져온 대화 상대만
    });

    test('종교·건강 기억 상태는 서버 sensitiveMemories 를 먼저, 없으면 방금 고른 값·동의로 읽는다', () {
      // 동의 전에도 켜진 채로 들어와서, consents 가 false 여도 서버가 on 이면 켜짐
      final fresh = {
        'consents': {'sensitiveReligion': false, 'sensitiveHealth': false},
        'sensitiveMemories': {'religion': 'off', 'health': 'on'},
      };
      expect(importedSensitiveState(fresh, 'religion'), 'off');
      expect(importedSensitiveState(fresh, 'health'), 'on');
      expect(importedSensitiveState(fresh, 'religion', known: 'on'), 'off'); // 서버 값이 먼저
      expect(importedSensitiveCategories(fresh), ['religion', 'health']);
      // 그 분류 기억이 없는 범주('none', 서버 persona_import.sensitive_states)는 보이지 않는다
      expect(importedSensitiveCategories({'sensitiveMemories': {'religion': 'none', 'health': 'on'}}), ['health']);
      expect(importedSensitiveCategories({'sensitiveMemories': {'religion': 'none', 'health': 'none'}}), isEmpty);
      expect(importedSensitiveState({'sensitiveMemories': {'religion': 'none'}}, 'religion'), isNull);

      // 예전 서버(sensitiveMemories 없음): 동의했으면 켜짐, 아니면 모름(처음 그대로 켜짐인지 거둬서 꺼짐인지)
      final old = {
        'consents': {'sensitiveReligion': true, 'sensitiveHealth': false},
      };
      expect(importedSensitiveState(old, 'religion'), 'on');
      expect(importedSensitiveState(old, 'health'), isNull);
      expect(importedSensitiveState(old, 'health', known: 'off'), 'off');
      expect(importedSensitiveCategories(old), ['religion', 'health']);
    });

    testWidgets('동의 단추: 켜짐·꺼짐·동의 여부에 맞는 단추만 보인다', (tester) async {
      final chosen = <String>[];
      Widget panel(Map detail) => MaterialApp(
            theme: buildTheme(),
            home: Scaffold(
              body: SingleChildScrollView(
                child: ImportedSensitivePanel(
                  detail: detail,
                  onChoose: (c, agreed) => chosen.add('$c:$agreed'),
                ),
              ),
            ),
          );

      // 아직 고르지 않음: 켜진 채로 동의 전 → 동의하기 · 대화에 쓰지 않기
      await tester.pumpWidget(panel({
        'consents': {'sensitiveReligion': false, 'sensitiveHealth': false},
        'sensitiveMemories': {'religion': 'on', 'health': 'on'},
      }));
      expect(find.text('종교 이야기'), findsOneWidget);
      expect(find.text('건강 이야기'), findsOneWidget);
      expect(find.text('대화에 쓰고 있어요 · 아직 동의 전이에요'), findsNWidgets(2));
      await tester.tap(find.text('대화에 쓰지 않기').first);
      await tester.tap(find.text('동의하기').last);
      expect(chosen, ['religion:false', 'health:true']);

      // 거둔 범주는 '동의하고 다시 쓰기', 동의한 범주는 '동의 거두기'
      chosen.clear();
      await tester.pumpWidget(panel({
        'consents': {'sensitiveReligion': false, 'sensitiveHealth': true},
        'sensitiveMemories': {'religion': 'off', 'health': 'on'},
      }));
      expect(find.text('대화에 쓰지 않아요'), findsOneWidget);
      expect(find.text('대화에 쓰고 있어요 · 동의했어요'), findsOneWidget);
      expect(find.text('대화에 쓰지 않기'), findsNothing);
      await tester.tap(find.text('동의하고 다시 쓰기'));
      await tester.tap(find.text('건강 이야기 동의 거두기'));
      expect(chosen, ['religion:true', 'health:false']);

      // 그 분류 기억이 하나도 없으면 단추 없이 안내만
      await tester.pumpWidget(panel({
        'consents': const {},
        'sensitiveMemories': const {'religion': 'none', 'health': 'none'},
      }));
      expect(_shown('종교·건강에 관한 기억은 없어요'), findsOneWidget);
      expect(find.text('동의하기'), findsNothing);
    });

    testWidgets('기억 더 채우기 주소는 질문 없이 동의 단추만 보이고, 거두면 그 범주 기억이 꺼진다', (tester) async {
      tester.view.physicalSize = const Size(1200, 1400);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final server = _FakePersonaServer();
      ApiClient.useAdapterForTests(server);

      await tester.pumpWidget(_morePage());
      await _settle(tester);
      expect(_shown('김예시의 종교·건강 이야기'), findsNothing); // 제목은 호칭으로
      expect(_shown('할머니의 종교·건강 이야기'), findsOneWidget);
      // 질문 칸(주제 카드·질문)은 없다
      expect(find.text('예시 주제'), findsNothing);
      expect(find.text('예시 질문'), findsNothing);
      expect(find.textContaining('답한 질문'), findsNothing);
      expect(find.text('대화에 쓰고 있어요 · 아직 동의 전이에요'), findsNWidgets(2));

      // 종교 이야기를 대화에 쓰지 않기 → 한 번 묻고 → 서버에 동의 거둠 → 다시 읽은 상태가 꺼짐
      await tester.tap(find.text('대화에 쓰지 않기').first);
      await _settle(tester);
      expect(find.text('종교 이야기를 대화에 쓰지 않을까요?'), findsOneWidget);
      await tester.tap(_inDialog('쓰지 않기'));
      await _settle(tester);
      expect(server.log, contains('POST /personas/p1/consents {"type":"sensitive_religion","agreed":false}'));
      expect(server.log.where((l) => l == 'GET /personas/p1'), hasLength(2));
      expect(find.text('대화에 쓰지 않아요'), findsOneWidget);
      expect(find.text('동의하고 다시 쓰기'), findsOneWidget);

      // 다시 동의하면 켜지고 '동의 거두기'만 남는다
      await tester.tap(find.text('동의하고 다시 쓰기'));
      await _settle(tester);
      expect(server.log.last, 'GET /personas/p1');
      expect(server.memories['religion'], 'on');
      expect(find.text('대화에 쓰고 있어요 · 동의했어요'), findsOneWidget);
      expect(find.text('종교 이야기 동의 거두기'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('예전 서버(sensitiveMemories 없음)에서도 이 화면에서 고른 값을 보인다 (휴대폰 폭)', (tester) async {
      tester.view.physicalSize = const Size(360, 1400);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.reset);
      final server = _FakePersonaServer(serverState: false);
      ApiClient.useAdapterForTests(server);

      await tester.pumpWidget(_morePage());
      await _settle(tester);
      expect(find.text('아직 동의 전이에요'), findsNWidgets(2));
      await tester.tap(find.text('대화에 쓰지 않기').last);
      await _settle(tester);
      await tester.tap(_inDialog('쓰지 않기'));
      await _settle(tester);
      expect(server.log, contains('POST /personas/p1/consents {"type":"sensitive_health","agreed":false}'));
      expect(find.text('대화에 쓰지 않아요'), findsOneWidget);
      expect(find.text('동의하고 다시 쓰기'), findsOneWidget);
    });

    testWidgets('쓰지 않는 화면에 바로 들어오면 안내만 보인다', (tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: buildTheme(),
        home: const PersonaBlocked(personaId: 'p1', title: '할머니'),
      ));
      expect(_shown('이 기능은 지금 쓸 수 없어요'), findsOneWidget);
      expect(find.text('대화로 돌아가기'), findsOneWidget);
    });

    testWidgets('함께하는 가족이 쓰지 않는 화면에 들어오면 누가 만들었는지 알려 준다', (tester) async {
      final blocked = PersonaBlocked.of(
        {'role': 'member', 'addressTerm': '엄마', 'ownerName': '지은', 'origin': 'import'},
        personaId: 'p1',
      );
      expect(blocked?.reason, BlockReason.member);
      await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: blocked!));
      expect(_shown('지은님이 만들어 함께 쓰고 있어요'), findsOneWidget);
      expect(find.text('엄마'), findsOneWidget); // 제목은 함께하는 가족 쪽 호칭
      // 주인에게는 막지 않는다(가져온 대화 상대는 고르는 화면에서만)
      expect(PersonaBlocked.of({'role': 'owner'}, personaId: 'p1'), isNull);
      expect(PersonaBlocked.of({'origin': 'import'}, personaId: 'p1', imported: false), isNull);
    });
  });
}
