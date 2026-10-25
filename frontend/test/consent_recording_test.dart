import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/network/api_client.dart';
import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/personas/consent_recording_page.dart';
import 'package:talkto_fe/shared/widgets/page_events.dart';
import 'package:talkto_fe/shared/widgets/voice_input.dart';

import 'legal_fakes.dart';

// 그분 동의 녹음(/p/:id/consent): 앱에서 한 번에 녹음해 끝내면 바로 올린다. 다시 듣기·고치기는 없다.
// 시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다).

const _script = '''살아 계신 분의 대화 상대를 만들 때, 그분이 직접 듣고 답하시는 모습을 앱에서 한 번에 녹음해요.

## 1. 확인 단어 — 만든 분이 말해요
> 지금부터 TalkTo 동의 녹음을 시작할게요. 오늘 확인 단어는 '{{확인 단어}}'예요.
- 화면에 나온 확인 단어를 그대로 읽어 주세요.

## 2. 법정대리인 확인 — 부모님이 말해요 [만 14세 미만]
> 저는 {{호칭}}의 {{보호자}}이고, 법정대리인으로서 이 대화 상대를 만드는 데 동의해요.

## 3. 싫으시면 안 하셔도 돼요 — 만든 분이 여쭤봐요
> {{호칭}}, 싫으시면 안 하셔도 돼요. 괜찮으세요?

## 4. 마치기 — 만든 분이 말해요
> 이것으로 동의 녹음을 마칠게요. 고맙습니다.
''';

Map<String, dynamic> _detail({Map<String, dynamic> basics = const {}}) => {
      'id': 'p1',
      'role': 'owner',
      'step': 'UPLOAD',
      'addressTerm': '할머니',
      'relation': '할머니',
      'basics': {'status': 'alive', 'relation': '할머니', 'myRelation': '손녀', 'minor': false, ...basics},
      'actions': <Object>[],
    };

Map<String, dynamic> _status({String status = 'none', String? blockCode, int attempts = 0, bool canStart = true}) => {
      'required': true,
      'mode': blockCode == 'minor_unknown' ? null : 'adult',
      'status': status,
      'attempts': attempts,
      'submittedCount': 0,
      'maxAttempts': 5,
      'latest': null,
      'scriptDoc': blockCode == 'minor_unknown' ? null : 'subject_consent_script',
      'canStart': canStart,
      'blockCode': blockCode,
      'blockMessage': blockCode == 'minor_unknown' ? '먼저 그분이 만 19세 미만인지 알려 주세요.' : null,
      'guardian': null,
    };

FakeServer _server({Map<String, dynamic>? status, Map<String, dynamic>? detail}) => FakeServer({
      'GET /personas/p1': () => detail ?? _detail(),
      'GET /personas/p1/subject-consent': () => status ?? _status(),
      'GET /intake/meta': () => {
            'minorRelations': {'relation': ['아들', '딸'], 'myRelation': ['어머니', '아버지']},
            'under14Open': false,
          },
      'GET /legal/subject_consent_script': () => {'title': '그분 동의 녹음 대본', 'version': '0.1', 'body': _script},
      'POST /personas/p1/subject-consent/start': () => {
            'id': 'r1',
            'attempt': 1,
            'words': ['사과', '바다'],
            'mode': 'adult',
            'scriptDoc': 'subject_consent_script',
            'scriptVersion': '0.1',
            'maxSeconds': 900,
            'maxMb': 32,
          },
      'POST /personas/p1/subject-consent/r1/audio': () => {'id': 'r1', 'status': 'submitted'},
      'POST /personas/p1/subject-consent/r1/abandon': () => {'ok': true},
      'PUT /personas/p1/minor': () => {'ok': true},
      'GET /personas': () => <Object>[],
    });

void main() {
  late FakeRecorder recorder;
  void Function(bool visible)? visibility;

  setUp(() {
    recorder = FakeRecorder();
    VoiceRecorder.create = () => recorder;
    visibility = null;
    PageEvents.listener = (onChange) {
      visibility = onChange;
      return () => visibility = null;
    };
    PageEvents.screenLock = () async => () {};
  });

  Future<void> open(WidgetTester tester, FakeServer server) async {
    ApiClient.useAdapterForTests(server);
    phone(tester, height: 1800);
    // 같은 시험에서 다시 열면 화면을 새로 만든다(앞 화면의 상태를 쓰지 않게)
    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: const ConsentRecordingPage(personaId: 'p1')));
    await settle(tester);
  }

  /// 시작하고 녹음 화면이 뜰 때까지.
  Future<void> startRecording(WidgetTester tester) async {
    await tapIn(tester, find.text('녹음 시작하기'));
    await settle(tester);
  }

  group('대본', () {
    test('## 마다 한 단계, 만 14세 미만 단계는 그 나이만, 자리표시는 화면이 채운다', () {
      final adult = ConsentScript.parse(_script, mode: 'adult', words: ['사과', '바다'], callName: '할머니', guardian: '엄마');
      expect(adult.steps.map((s) => s.number), [1, 3, 4]);
      expect(adult.steps.first.title, '확인 단어 — 만든 분이 말해요');
      expect(adult.steps.first.lines.first.kind, ScriptLineKind.say);
      expect(adult.steps.first.lines.first.text, "지금부터 TalkTo 동의 녹음을 시작할게요. 오늘 확인 단어는 '사과 · 바다'예요.");
      expect(adult.steps.first.lines.last.kind, ScriptLineKind.help);
      expect(adult.steps[1].lines.single.text, '할머니, 싫으시면 안 하셔도 돼요. 괜찮으세요?');
      expect(adult.intro, startsWith('살아 계신 분의 대화 상대를 만들 때'));

      final child = ConsentScript.parse(_script, mode: 'minor_child', words: ['사과', '바다'], callName: '지우', guardian: '엄마');
      expect(child.steps.map((s) => s.number), [1, 2, 3, 4]);
      // 제목의 [만 14세 미만] 표시는 지우고 보인다
      expect(child.steps[1].title, '법정대리인 확인 — 부모님이 말해요');
      expect(child.steps[1].lines.single.text, '저는 지우의 엄마이고, 법정대리인으로서 이 대화 상대를 만드는 데 동의해요.');
      expect(ConsentScript.parse(_script, mode: 'minor_teen', words: const [], callName: '', guardian: '아빠').steps.length, 3);
      expect(guardianWord('아버지'), '아빠');
      expect(guardianWord('어머니'), '엄마');
    });
  });

  testWidgets('시작하면 확인 단어가 크게 보이고, 다음을 눌러도 녹음은 이어지며, 끝내면 바로 올린다(다시 듣기 없음)', (tester) async {
    final server = _server();
    await open(tester, server);
    // 녹음 전 안내(화면 글 그대로)와 대본 첫 문단. 확인 단어는 아직 보이지 않는다
    expect(has('께서 직접 듣고 답하시는 모습을 앱에서 한 번에 녹음해요'), findsOneWidget);
    expect(has('중간에 멈추거나 고칠 수 없어요'), findsOneWidget);
    expect(has('해외 회사로 보내지 않아요'), findsWidgets);
    expect(has('살아 계신 분의 대화 상대를 만들 때'), findsOneWidget);
    expect(find.text('사과 · 바다'), findsNothing);

    await startRecording(tester);
    expect(recorder.started, 1);
    expect(find.text('사과 · 바다'), findsOneWidget);
    expect(has("오늘 확인 단어는 '사과 · 바다'예요."), findsOneWidget);
    expect(find.text('1 / 3'), findsOneWidget);
    // 만 14세 미만 단계는 어른 대본에 없다
    expect(has('법정대리인으로서'), findsNothing);

    await tapIn(tester, find.text('다음'));
    expect(find.text('2 / 3'), findsOneWidget);
    expect(recorder.stopped, 0);
    await tapIn(tester, find.text('다음'));
    expect(find.text('녹음 끝내고 보내기'), findsOneWidget);
    await tapIn(tester, find.text('녹음 끝내고 보내기'));
    await settle(tester, frames: 20);

    expect(recorder.stopped, 1);
    expect(server.count('POST /personas/p1/subject-consent/r1/audio'), 1);
    final body = server.bodies['POST /personas/p1/subject-consent/r1/audio']!.single;
    expect(formField(body, 'interrupted'), 'false');
    expect(formField(body, 'refused'), 'false');
    // 앱(시험)은 m4a, 웹은 브라우저가 녹음할 수 있는 형식(webm 등) 그대로 보낸다
    expect(formField(body, 'format'), 'm4a');
    final steps = jsonDecode(formField(body, 'steps')!) as List;
    expect([for (final s in steps) (s as Map)['step']], [1, 3, 4]);
    // 단계 시각은 녹음 파일 처음(마이크를 켠 때)부터 센다. 첫 단계는 확인 단어가 뜬 때
    final times = [for (final s in steps) ((s as Map)['atMs'] as num).toInt()];
    expect(times.first, inInclusiveRange(0, 5000));
    expect(times, orderedEquals([...times]..sort()));
    // 받아쓰기나 다른 곳으로 보내지 않는다(이 화면은 동의 녹음 주소만 부른다)
    expect(server.log.where((k) => k.contains('transcri') || k.contains('/recordings')), isEmpty);
    expect(has('보냈어요. TalkTo가 듣고 확인하면 알림으로 알려 드릴게요.'), findsOneWidget);
    // 다시 듣기·버리기·고르기 단추가 없다
    for (final word in ['다시 듣기', '듣기', '버리기', '파일 고르기']) {
      expect(find.text(word), findsNothing, reason: word);
    }
    expect(server.count('POST /personas/p1/subject-consent/r1/abandon'), 0);
  });

  testWidgets('녹음하다 화면이 가려지면 곧바로 멈추고 지금까지를 끊긴 녹음으로 올린 뒤 처음부터', (tester) async {
    final server = _server();
    await open(tester, server);
    await startRecording(tester);
    expect(visibility, isNotNull);
    visibility!(false);
    await settle(tester, frames: 20);

    expect(recorder.stopped, 1);
    final body = server.bodies['POST /personas/p1/subject-consent/r1/audio']!.single;
    expect(formField(body, 'interrupted'), 'true');
    expect(formField(body, 'reason'), 'hidden');
    expect(formField(body, 'refused'), 'false');
    expect(has('녹음이 중간에 멈췄어요. 처음부터 다시 녹음해 주세요(확인 단어도 새로 받아요).'), findsOneWidget);
    expect(find.text('녹음 시작하기'), findsOneWidget);
    // 듣기를 놓았다
    expect(visibility, isNull);
  });

  testWidgets('녹음하다 마이크가 끊기면(두 번 잇달아) 끊긴 녹음으로 올리고 처음부터', (tester) async {
    final server = _server();
    await open(tester, server);
    await startRecording(tester);
    // 5초마다 보는데, 한 번 멈춰 보인 것은 넘긴다
    recorder.recordingNow = false;
    for (var i = 0; i < 5; i++) {
      await tester.pump(const Duration(seconds: 1));
    }
    recorder.recordingNow = true;
    for (var i = 0; i < 5; i++) {
      await tester.pump(const Duration(seconds: 1));
    }
    expect(server.count('POST /personas/p1/subject-consent/r1/audio'), 0);
    expect(find.text('사과 · 바다'), findsOneWidget);

    recorder.recordingNow = false;
    for (var i = 0; i < 10; i++) {
      await tester.pump(const Duration(seconds: 1));
    }
    await settle(tester, frames: 20);
    final body = server.bodies['POST /personas/p1/subject-consent/r1/audio']!.single;
    expect(formField(body, 'interrupted'), 'true');
    expect(formField(body, 'reason'), 'mic');
    expect(has('녹음이 중간에 멈췄어요.'), findsOneWidget);
  });

  testWidgets("'그분이 원하지 않으세요'는 확인한 뒤 지금까지를 원하지 않음으로 올린다", (tester) async {
    final server = _server();
    await open(tester, server);
    await startRecording(tester);
    await tapIn(tester, find.text('그분이 원하지 않으세요'));
    expect(has('그분이 원하지 않으시면 만들지 않아요. 지금까지 녹음한 것을 TalkTo로 보내고 멈출까요?'), findsOneWidget);
    await tapIn(tester, find.text('보내고 멈추기'));
    await settle(tester, frames: 20);

    final body = server.bodies['POST /personas/p1/subject-consent/r1/audio']!.single;
    expect(formField(body, 'refused'), 'true');
    // 끊긴 녹음이 아니다(검수 화면이 끊김으로 보이지 않게)
    expect(formField(body, 'interrupted'), 'false');
    expect(formField(body, 'reason'), 'refused');
    expect(has('보냈어요.'), findsOneWidget);
  });

  testWidgets('마이크를 켜지 못하면 확인 단어를 받지 않는다(시도 횟수를 쓰지 않게)', (tester) async {
    final server = _server();
    await open(tester, server);
    recorder.permission = false;
    await startRecording(tester);

    expect(has('마이크를 쓸 수 없어요'), findsOneWidget);
    expect(server.count('POST /personas/p1/subject-consent/start'), 0);
    expect(server.count('POST /personas/p1/subject-consent/r1/abandon'), 0);
    expect(find.text('사과 · 바다'), findsNothing);
    expect(find.text('녹음 시작하기'), findsOneWidget);

    // 허락하면 그때 받는다(앞서 뜬 알림 줄이 단추를 가리지 않게 먼저 걷는다)
    recorder.permission = true;
    tester.state<ScaffoldMessengerState>(find.byType(ScaffoldMessenger)).clearSnackBars();
    await settle(tester);
    await startRecording(tester);
    expect(server.count('POST /personas/p1/subject-consent/start'), 1);
    expect(find.text('사과 · 바다'), findsOneWidget);

    // 녹음하다 화면을 떠나면 그 시도는 쓸 수 없다고 남긴다
    await tester.pumpWidget(const SizedBox());
    await settle(tester);
    expect(server.count('POST /personas/p1/subject-consent/r1/abandon'), 1);
  });

  testWidgets('확인 단어를 받지 못하면 녹음 화면으로 넘어가지 않고 시작 화면에 남는다', (tester) async {
    final server = _server();
    server.routes['POST /personas/p1/subject-consent/start'] =
        () => const Fail(409, {'detail': '오늘은 더 녹음할 수 없어요.', 'code': 'too_many'});
    await open(tester, server);
    await startRecording(tester);

    expect(recorder.started, 1);
    expect(has('오늘은 더 녹음할 수 없어요.'), findsOneWidget);
    expect(find.text('녹음 시작하기'), findsOneWidget);
    expect(find.text('사과 · 바다'), findsNothing);
    expect(visibility, isNull);
  });

  testWidgets('다시 부탁받은 뒤 끊긴 시도가 있어도 검수한 분의 글(서버 카드)을 그대로 보인다', (tester) async {
    final status = _status(status: 'rerequest', attempts: 2)
      ..['latest'] = {'id': 'r2', 'attempt': 2, 'status': 'interrupted', 'noteToOwner': null};
    final detail = _detail()
      ..['actions'] = [
        {
          'kind': 'subject_consent',
          'blocking': false,
          'title': '그분 동의 녹음을 다시 부탁드려요',
          'body': '확인 단어를 또렷하게 읽어 주세요.',
          'route': '/p/p1/consent',
        },
      ];
    await open(tester, _server(status: status, detail: detail));
    expect(has('확인 단어를 또렷하게 읽어 주세요.'), findsOneWidget);
    expect(has('한 번 더 부탁드려요'), findsNothing);
  });

  testWidgets('예전 대화 상대가 만 14세 미만이라고 답하면 만들 수 없다는 글 대신 그대로 받는다는 안내', (tester) async {
    final server = _server(status: _status(blockCode: 'minor_unknown', canStart: false));
    await open(tester, server);
    await tapIn(tester, shown('네'));
    final year = DateTime.now().year - 10;
    await tapIn(tester, find.byKey(const ValueKey('birth-year')));
    await tester.tap(find.text('$year년').last);
    await settle(tester);
    await tapIn(tester, find.byKey(const ValueKey('birth-month')));
    await tester.tap(find.text('1월').last);
    await settle(tester);

    expect(has(under14ExistingText), findsOneWidget);
    expect(has('아직 만들 수 없어요'), findsNothing);
    await tapIn(tester, find.text('알려 드리고 이어서 하기'));
    expect(server.json('PUT /personas/p1/minor'), {'minor': true, 'birthYm': '$year-01'});
  });

  testWidgets('그분이 아들·딸인데 나는 그분의가 부모가 아니면 먼저 고르게 하고 myRelation 을 함께 보낸다', (tester) async {
    final detail = _detail(basics: {'relation': '아들', 'myRelation': '엄마'})..['basics'].remove('minor');
    final server = _server(status: _status(blockCode: 'minor_unknown', canStart: false), detail: detail);
    await open(tester, server);
    await tapIn(tester, shown('네'));
    final year = DateTime.now().year - 16;
    await tapIn(tester, find.byKey(const ValueKey('birth-year')));
    await tester.tap(find.text('$year년').last);
    await settle(tester);
    await tapIn(tester, find.byKey(const ValueKey('birth-month')));
    await tester.tap(find.text('1월').last);
    await settle(tester);
    expect(has('나는 그분의'), findsOneWidget);
    // 고르지 않으면 보내지 않는다
    await tapIn(tester, find.text('알려 드리고 이어서 하기'));
    expect(server.count('PUT /personas/p1/minor'), 0);
    expect(has('나는 그분의 무엇인지 골라 주세요.'), findsOneWidget);
    ScaffoldMessenger.of(tester.element(find.byType(ConsentRecordingPage))).hideCurrentSnackBar();
    await settle(tester);
    // 그 밖: 부모가 아니라고 답한 것(법정대리인 확인 없이)
    await tapIn(tester, shown('그 밖'));
    await tapIn(tester, find.text('알려 드리고 이어서 하기'));
    expect(server.json('PUT /personas/p1/minor'), {'minor': true, 'birthYm': '$year-01', 'myRelation': '그 밖'});
    // 어머니: 부모라 법정대리인 확인 칸이 열리고 함께 보낸다
    await tapIn(tester, shown('어머니'));
    expect(has('법정대리인'), findsWidgets);
  });

  testWidgets('그만두기는 확인한 뒤 끊긴 녹음으로 남기고 처음부터', (tester) async {
    final server = _server();
    await open(tester, server);
    await startRecording(tester);
    await tapIn(tester, find.text('그만두기'));
    expect(has('그만두면 이번 녹음은 쓸 수 없고, 처음부터 다시 녹음해야 해요'), findsOneWidget);
    await tapIn(tester, find.widgetWithText(FilledButton, '그만두기'));
    await settle(tester, frames: 20);

    final body = server.bodies['POST /personas/p1/subject-consent/r1/audio']!.single;
    expect(formField(body, 'interrupted'), 'true');
    expect(formField(body, 'reason'), 'user');
    expect(has('녹음이 중간에 멈췄어요.'), findsOneWidget);
  });

  testWidgets('예전 대화 상대는 먼저 만 19세 미만인지 묻고 PUT /minor 로 한 번 알린다', (tester) async {
    final server = _server(status: _status(blockCode: 'minor_unknown', canStart: false));
    await open(tester, server);
    expect(has('그분은 만 19세 미만인가요?'), findsOneWidget);
    expect(find.text('녹음 시작하기'), findsNothing);
    await tapIn(tester, shown('아니요'));
    await tapIn(tester, find.text('알려 드리고 이어서 하기'));
    expect(server.count('PUT /personas/p1/minor'), 1);
    expect(server.json('PUT /personas/p1/minor'), {'minor': false});
  });

  testWidgets('확인 중·보류·확인됨은 상태 화면만(녹음 단추 없음), 두 번째부터는 앞선 녹음도 함께 본다고 알린다', (tester) async {
    await open(tester, _server(status: _status(status: 'submitted')));
    expect(shown('그분 동의 녹음을 확인하고 있어요'), findsOneWidget);
    expect(find.text('녹음 시작하기'), findsNothing);

    await open(tester, _server(status: _status(status: 'hold')));
    expect(shown('확인할 것이 있어요'), findsOneWidget);

    await open(tester, _server(status: _status(status: 'passed')));
    expect(shown('그분 동의 녹음이 확인됐어요'), findsOneWidget);
    expect(find.text('이어서 만들기'), findsOneWidget);

    await open(tester, _server(status: _status(status: 'rerequest', attempts: 1)));
    expect(has('2번째 녹음이에요. 앞선 녹음도 지우지 않고 함께 확인해요.'), findsOneWidget);
    expect(has('한 번 더 부탁드려요'), findsOneWidget);
  });
}
