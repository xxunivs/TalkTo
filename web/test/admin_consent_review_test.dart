import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:talkto_fe/core/theme.dart';
import 'package:talkto_fe/features/admin/consent_review_page.dart';
import 'package:talkto_fe/features/admin/legal_admin_api.dart';
import 'package:talkto_fe/features/admin/legal_admin_common.dart';
import 'package:talkto_fe/shared/widgets/ui.dart';

// 관리자 · 그분 동의 녹음 검수. 시험 자료는 모두 지어낸 것이다(가족 자료를 쓰지 않는다).

Map<String, dynamic> _detail({
  String status = 'submitted',
  bool refusedFlag = false,
  bool hasStory = true,
  Map<String, dynamic>? review,
}) =>
    {
      'id': 'r1',
      'attempt': 2,
      'mode': 'adult',
      'status': status,
      'refusedFlag': refusedFlag,
      // 서버 꼴 그대로: 확인 단어는 목록
      'words': ['사과', '바다'],
      'wordsIssuedAt': '2026-10-23T01:00:00Z',
      'submittedAt': '2026-10-23T01:20:00Z',
      'durationMs': 610000,
      'sizeBytes': 2400000,
      'sha256': 'abcdef0123456789abcdef',
      'contentType': 'audio/webm',
      'gapSeconds': 590,
      'hasStoryRecordings': hasStory,
      'steps': [
        {'step': 1, 'atMs': 0},
        {'step': 2, 'atMs': 65000},
      ],
      'interrupted': false,
      'reason': null,
      'audioUrl': 'https://example.com/a.webm',
      'persona': {
        'id': 'p1',
        'title': '예시 어머니',
        'name': '김예시',
        'relation': '어머니',
        'myRelation': '아들',
        'status': 'alive',
        'minor': false,
        'step': 'UPLOAD',
        'createdAt': '2026-10-20T01:00:00Z',
      },
      'owner': {'masked': 'ex***@example.com', 'joinedAt': '2026-10-01T01:00:00Z'},
      'history': [
        {
          'id': 'r0',
          'attempt': 1,
          'status': 'superseded',
          'words': ['나무', '기차'],
          'issuedAt': '2026-10-22T01:00:00Z',
          'submittedAt': '2026-10-22T01:30:00Z',
          'durationMs': 300000,
          'audioUrl': 'https://example.com/b.webm',
        },
      ],
      'recordings': hasStory
          ? [
              {'id': 'rec1', 'name': '통화 녹음.m4a', 'durationMs': 120000, 'url': 'https://example.com/c.m4a'},
            ]
          : <Map<String, dynamic>>[],
      'guardian': null,
      // 서버 CHECKLIST 꼴({key, title, detail?, failExample, rerequest})과 판정 안내
      'checklist': [
        for (var i = 1; i <= 10; i++)
          {
            'key': '$i',
            'title': i == 1 ? '본인 목소리인가' : '항목 $i',
            if (i == 1) 'detail': '이야기 녹음의 화자와 동일한가',
            'failExample': '예시 $i번',
          },
      ],
      'guide': ['서버가 준 안내 한 줄'],
      'review': review,
    };

class _FakeApi extends LegalAdminApi {
  Map<String, dynamic> detail;
  final reviews = <Map<String, dynamic>>[];
  final listed = <String>[];

  _FakeApi(this.detail);

  @override
  Future<Map<String, dynamic>> consent(String id) async => detail;

  @override
  Future<Map<String, dynamic>> review(
    String id, {
    required Map<String, bool> checklist,
    required String verdict,
    String? noteToOwner,
    String? adminNote,
    bool allowMore = false,
  }) async {
    reviews.add({
      'id': id,
      'checklist': checklist,
      'verdict': verdict,
      'noteToOwner': noteToOwner,
      'adminNote': adminNote,
      'allowMore': allowMore,
    });
    return {'id': id, 'status': verdict == 'pass' ? 'passed' : verdict};
  }

  @override
  Future<({List<Map<String, dynamic>> rows, int total, Map<String, dynamic> meta})> consents({
    String status = 'submitted',
    int page = 1,
    int size = 50,
  }) async {
    listed.add(status);
    return (
      rows: [
        {
          'id': 'r1',
          'personaId': 'p1',
          'title': '예시 어머니',
          'attempt': 2,
          'submittedCount': 2,
          'interruptedCount': 1,
          'mode': 'adult',
          'status': 'submitted',
          'refusedFlag': true,
          'submittedAt': '2026-10-23T01:20:00Z',
          'ownerMasked': 'ex***@example.com',
          'ageBand': '만 19세 이상',
        },
      ],
      total: 1,
      meta: <String, dynamic>{'total': 1},
    );
  }
}

String _plain(Widget w) => switch (w) {
      Text() => (w.data ?? w.textSpan?.toPlainText() ?? '').replaceAll('⁠', ''),
      SelectableText() => w.data ?? '',
      _ => '',
    };
Finder _has(String part) => find.byWidgetPredicate((w) => (w is Text || w is SelectableText) && _plain(w).contains(part));
Finder _pill(String label) => find.byWidgetPredicate((w) => w is Pill && w.label == label);

Future<void> _open(WidgetTester tester, _FakeApi api) async {
  tester.view.physicalSize = const Size(1000, 4200);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: ConsentReviewDetailPage(id: 'r1', api: api)));
  await tester.pumpAndSettle();
}

bool _enabled(WidgetTester tester, String label) => tester.widget<Pill>(_pill(label)).onTap != null;

Future<void> _check(WidgetTester tester, int no) async {
  // 검수 10항목의 체크 칸(위에서부터 1~10)
  await tester.tap(find.byType(Checkbox).at(no - 1));
  await tester.pump();
}

void main() {
  group('판정 규칙(서버와 같게)', () {
    Map<String, bool> all(bool value) => {for (var i = 1; i <= 10; i++) '$i': value};

    test('통과는 10개 모두 + 이야기 녹음이 있어야', () {
      bool pass(Map<String, bool> checks, {bool story = true}) => verdictAllowed('pass',
          checks: checks, reviewable: true, refusedFlag: false, adminNote: '', hasStoryRecordings: story);
      expect(pass(all(true)), isTrue);
      expect(pass(all(true), story: false), isFalse);
      for (final no in ['4', '5', '6', '9', '10', '1']) {
        expect(pass({...all(true), no: false}), isFalse, reason: no);
      }
    });

    test('원하지 않음은 녹음 중 표시나 내부 메모가 있어야, 재요청·보류는 늘 된다', () {
      bool allowed(String v, {bool flag = false, String note = ''}) => verdictAllowed(v,
          checks: all(false), reviewable: true, refusedFlag: flag, adminNote: note, hasStoryRecordings: true);
      expect(allowed('refused'), isFalse);
      expect(allowed('refused', flag: true), isTrue);
      expect(allowed('refused', note: '인지 상태가 의심스러움'), isTrue);
      expect(allowed('refused', note: '   '), isFalse);
      expect(allowed('rerequest'), isTrue);
      expect(allowed('hold'), isTrue);
    });

    test('검수 대기·보류가 아니면 아무 판정도 못 고른다', () {
      for (final v in consentVerdictLabels.keys) {
        expect(
          verdictAllowed(v, checks: all(true), reviewable: false, refusedFlag: true, adminNote: 'x', hasStoryRecordings: true),
          isFalse,
        );
      }
    });

    test('재요청 글은 실패 항목에 맞춰 채운다(2·3은 한 줄, 6번은 글 없음)', () {
      final checks = {...all(true), '2': false, '3': false, '6': false, '9': false};
      final note = rerequestNote(checks);
      expect(note.split('\n').first, rerequestHead);
      expect(note, contains(rerequestLines['23']));
      expect(RegExp(RegExp.escape(rerequestLines['23']!)).allMatches(note), hasLength(1));
      expect(note, contains(rerequestLines['9']));
      expect(note.contains(rerequestLines['1']!), isFalse);
      // 모두 실패해도 만든 분께 남길 말 한도(500자) 안
      expect(rerequestNote(all(false)).length, lessThanOrEqualTo(500));
      expect(consentChecklist, hasLength(10));
    });

    test('확인 단어는 서버 목록이든 띄어 쓴 글이든 같은 꼴로', () {
      expect(adminWords(['사과', '바다']), '사과 · 바다');
      expect(adminWords('사과 바다'), '사과 · 바다');
      expect(adminWords(null), '');
    });
  });

  testWidgets('상세: 통과는 10개를 모두 체크해야 켜지고, 4번을 빼면 다시 꺼진다', (tester) async {
    final api = _FakeApi(_detail());
    await _open(tester, api);
    expect(_has('사과 · 바다'), findsOneWidget);
    expect(_has('확인 단어 나무 · 기차'), findsOneWidget);
    expect(_has('['), findsNothing);
    expect(_has('확인 단어를 받은 뒤 오래 걸렸어요'), findsOneWidget);
    expect(_has('앞선 녹음 1개'), findsOneWidget);
    expect(_has('통화 녹음.m4a'), findsOneWidget);
    expect(_has('판정 안내'), findsOneWidget);
    // 10항목·판정 안내는 서버 글(제목 — 설명, 불통과 예시)
    expect(_has('1. 본인 목소리인가 — 이야기 녹음의 화자와 동일한가'), findsOneWidget);
    expect(_has('불통과 예시: 예시 1번'), findsOneWidget);
    expect(_has('서버가 준 안내 한 줄'), findsOneWidget);
    expect(_has('인지 상태가 의심스러우면'), findsNothing);
    expect(_enabled(tester, '통과'), isFalse);
    expect(_enabled(tester, '원하지 않음'), isFalse);
    expect(_enabled(tester, '재요청'), isTrue);
    expect(_enabled(tester, '보류'), isTrue);

    for (var no = 1; no <= 10; no++) {
      await _check(tester, no);
    }
    expect(_enabled(tester, '통과'), isTrue);
    await _check(tester, 4);
    expect(_enabled(tester, '통과'), isFalse);
  });

  testWidgets('상세: 재요청을 고르면 실패 항목 글을 미리 채우고, 저장하면 그 글로 보낸다', (tester) async {
    final api = _FakeApi(_detail());
    await _open(tester, api);
    for (final no in [1, 2, 3, 5, 6, 7, 8, 9, 10]) {
      await _check(tester, no);
    }
    await tester.tap(_pill('재요청'));
    await tester.pump();
    final note = tester.widget<TextField>(find.byType(TextField).first).controller!.text;
    expect(note, rerequestNote({for (var i = 1; i <= 10; i++) '$i': i != 4}));
    expect(note, contains(rerequestLines['4']));

    await tester.tap(find.text('판정 저장'));
    await tester.pumpAndSettle();
    expect(api.reviews, hasLength(1));
    expect(api.reviews.single['verdict'], 'rerequest');
    expect((api.reviews.single['checklist'] as Map)['4'], isFalse);
    expect(api.reviews.single['noteToOwner'], note);
  });

  testWidgets('상세: 재요청에서 보류로 바꾸면 미리 채운 글을 비우고, 만든 분께 남길 말 없이 저장한다', (tester) async {
    final api = _FakeApi(_detail());
    await _open(tester, api);
    TextEditingController note() => tester.widget<TextField>(find.byType(TextField).first).controller!;
    await tester.tap(_pill('재요청'));
    await tester.pump();
    expect(note().text, isNotEmpty);
    await tester.tap(_pill('보류'));
    await tester.pump();
    expect(note().text, isEmpty);
    // 남길 말은 재요청일 때만 적는다
    expect(tester.widget<TextField>(find.byType(TextField).first).enabled, isFalse);

    await tester.tap(find.text('판정 저장'));
    await tester.pumpAndSettle();
    expect(api.reviews, hasLength(1));
    expect(api.reviews.single['verdict'], 'hold');
    expect(api.reviews.single['noteToOwner'], isNull);
  });

  testWidgets('상세: 원하지 않음은 내부 메모를 적어야 켜지고, 저장 전에 한 번 더 묻는다', (tester) async {
    final api = _FakeApi(_detail());
    await _open(tester, api);
    expect(_enabled(tester, '원하지 않음'), isFalse);
    await tester.enterText(find.byType(TextField).last, '대화 중 거부 의사가 분명함');
    await tester.pump();
    expect(_enabled(tester, '원하지 않음'), isTrue);
    await tester.tap(_pill('원하지 않음'));
    await tester.pump();
    await tester.tap(find.text('판정 저장'));
    await tester.pumpAndSettle();
    expect(_has('이 대화 상대를 바로 멈추고 7일 뒤 지워요.'), findsOneWidget);
    await tester.tap(find.text('취소'));
    await tester.pumpAndSettle();
    expect(api.reviews, isEmpty);
  });

  testWidgets("상세: 녹음 중 '원하지 않으세요'를 누른 녹음은 원하지 않음을 바로 고를 수 있다", (tester) async {
    await _open(tester, _FakeApi(_detail(refusedFlag: true)));
    expect(_has("녹음 중 '그분이 원하지 않으세요'를 눌렀어요."), findsOneWidget);
    expect(_enabled(tester, '원하지 않음'), isTrue);
  });

  testWidgets('상세: 이야기 녹음이 없으면 10개를 모두 체크해도 통과할 수 없다', (tester) async {
    await _open(tester, _FakeApi(_detail(hasStory: false)));
    for (var no = 1; no <= 10; no++) {
      await _check(tester, no);
    }
    expect(_enabled(tester, '통과'), isFalse);
    expect(_has('비교할 이야기 녹음이 아직 없어요.'), findsOneWidget);
  });

  testWidgets('상세: 이미 통과한 녹음은 판정 단추가 없다', (tester) async {
    await _open(
      tester,
      _FakeApi(_detail(status: 'passed', review: {
        'checklist': {for (var i = 1; i <= 10; i++) '$i': true},
        'verdict': 'pass',
        'reviewedAt': '2026-10-23T03:00:00Z',
      })),
    );
    expect(find.text('판정 저장'), findsNothing);
    expect(_pill('통과'), findsNothing);
    expect(_has('판정했어요: 통과'), findsOneWidget);
  });

  testWidgets('목록: 검수 대기부터, 원하지 않으세요 표시가 보이고 거르기로 다시 읽는다', (tester) async {
    final api = _FakeApi(_detail());
    await tester.pumpWidget(MaterialApp(theme: buildTheme(), home: ConsentReviewListPage(api: api)));
    await tester.pumpAndSettle();
    expect(api.listed, ['submitted']);
    expect(_has('예시 어머니 · 만 19세 이상'), findsOneWidget);
    expect(_has("녹음 중 '원하지 않으세요'를 눌렀어요"), findsOneWidget);
    expect(_has('중간에 멈춤 1번'), findsOneWidget);
    await tester.tap(_pill('보류'));
    await tester.pumpAndSettle();
    expect(api.listed.last, 'hold');
  });
}
