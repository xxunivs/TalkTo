import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import 'legal_admin_api.dart';
import 'legal_admin_common.dart';

/// 관리자 화면 · 그분 동의 녹음 검수(관리자만).
///
///   /admin/consents       검수 목록(기본 검수 대기, 오래된 것부터 · '원하지 않으세요'를 누른 것 먼저)
///   /admin/consents/:id   녹음 하나: 듣기 · 확인 단어 · 시각·길이 · 단계 · 앞선 녹음 · 이야기 녹음(목소리 비교)
///                         · 법정대리인 · 10항목 · 판정(통과·재요청·보류·원하지 않음) · 만든 분께 남길 말 · 내부 메모
///
/// 녹음은 TalkTo 서버(서울)에만 있고 서명 주소(15분)로 듣기만 한다. 해외 회사로 보내지 않고 내려받기 단추도 없다.
/// 판정 단추는 서버 규칙과 같게 막는다(통과는 10개 모두 + 이야기 녹음이 있어야, 원하지 않음은 녹음 중 표시나 내부
/// 메모가 있어야). 서버가 다시 확인한다.

/// 검수 10항목(베타/31 체크리스트 원문): (제목, 불통과 예시).
const consentChecklist = [
  ('본인 목소리인가 — 이야기 녹음의 화자와 동일한가', '낭독자와 이야기 화자가 다름'),
  ('날짜를 본인이 말했는가', '신청자가 "오늘 9월 17일이잖아" 하고 대신 말함'),
  ('성함을 본인이 말했는가', '신청자가 대신 답함'),
  ('"괜찮아요?"에 본인이 답했는가 ⭐ 가장 중요', '무응답 / 신청자가 "괜찮대요" 하고 넘어감'),
  ('낭독을 본인이 했는가', '신청자가 대신 읽음 → 즉시 불통과'),
  ('유도·압박 정황이 없는가', '"그냥 해, 빨리", 반복 종용, 짜증 섞인 톤'),
  ('철회 안내에 반응했는가', '마지막 확인에 아무 반응 없음'),
  ('첫머리에 확인 단어가 있고, 끊김·편집 없는 한 파일인가', '단어 없음 · 다른 날 단어 · 잘린 흔적 → 재요청'),
  (
    '해외 회사로 보낸다는 설명에 본인이 괜찮다고 답했는가 (국외 이전 별도 동의 · 동의문 §7)',
    '설명 없음 / 신청자가 대신 답함 → 불통과',
  ),
  (
    '이해 확인 질문 3개에 본인이 스스로 답했는가 — ① "뭘 만드는 거라고 했죠?" ② "싫어지면 어떻게 하면 돼요?" '
        '③ "싫으시면 안 하셔도 돼요. 정말 괜찮으세요?"',
    '답 없음 · 신청자 말을 그대로 따라 함 → 불통과',
  ),
];

/// 판정 안내(화면 고정 글).
const consentVerdictGuide = [
  '10개 전부 통과 → 통과',
  '1~3, 7번 일부 미흡 → 재요청',
  '4·5·9·10번 하나라도 불통과 → 재요청 필수(동의 불성립)',
  '6번 유도·압박 정황 → 보류(만든 분께 전화로 확인, 그분이 원치 않으시면 원하지 않음)',
  '인지 상태가 의심스러우면 만들지 않음(원하지 않음으로 처리)',
  '의학적 판단이 아니라, 본인 의사를 표현하실 수 있는가만 봅니다.',
  '목소리가 아이로 들리는데 만 19세 이상으로 답한 대화 상대면 보류(만든 분께 확인)',
];

/// 판정 → 화면 이름. 순서도 화면 순서.
const consentVerdictLabels = {
  'pass': '통과',
  'rerequest': '재요청',
  'hold': '보류',
  'refused': '원하지 않음',
};

/// 재요청 때 만든 분께 보낼 말(머리 + 실패 항목별 한 줄). 관리자가 고칠 수 있다.
const rerequestHead = '녹음 잘 받았어요. 번거로우시겠지만 한 번 더 부탁드려요. 그분 목소리를 다루는 일이라 이 부분은 꼭 확인이 필요해요.';
const rerequestLines = {
  '1': '동의 녹음 속 목소리가 올리신 녹음 속 그분과 같은 분인지 확인하기 어려웠어요.',
  '23': '오늘 날짜와 성함을 그분이 직접 말씀하시는 부분이 필요해요. 대신 말씀하시거나 미리 알려 드리지 말아 주세요.',
  '4': "'괜찮으세요?'라고 여쭤보시고 그분이 직접 대답하시는 부분까지 녹음해 주세요.",
  '5': '낭독은 그분이 직접 하셔야 해요. 한 줄씩 읽어 드리고 그분이 따라 읽으시게 해 주세요.',
  '7': "마지막에 '나중에 싫어지시면 말씀만 하세요'라고 알려 드리고 그분이 대답하시는 부분이 필요해요.",
  '8': '녹음 첫머리에 화면에 나온 확인 단어를 말씀해 주시고, 처음부터 끝까지 한 번에 녹음해 주세요.',
  '9': '미국 회사로 보낸다는 설명을 듣고 그분이 직접 괜찮다고 하시는 부분이 필요해요.',
  '10': '이해 확인 질문 세 가지에 그분이 스스로 답하시는 부분이 필요해요. 대신 답하거나 따라 말하게 하지 말아 주세요.',
};

/// 체크하지 않은 항목에 맞춰 재요청 글을 채운다(6번은 보류라 글이 없다).
String rerequestNote(Map<String, bool> checks) {
  bool failed(String no) => checks[no] != true;
  final lines = [
    rerequestHead,
    if (failed('1')) rerequestLines['1']!,
    if (failed('2') || failed('3')) rerequestLines['23']!,
    for (final no in const ['4', '5', '7', '8', '9', '10'])
      if (failed(no)) rerequestLines[no]!,
  ];
  return lines.join('\n');
}

/// 이 판정을 고를 수 있나(서버 규칙과 같다).
bool verdictAllowed(
  String verdict, {
  required Map<String, bool> checks,
  required bool reviewable,
  required bool refusedFlag,
  required String adminNote,
  required bool hasStoryRecordings,
}) {
  if (!reviewable) return false;
  return switch (verdict) {
    'pass' => hasStoryRecordings && [for (var i = 1; i <= 10; i++) checks['$i'] == true].every((ok) => ok),
    'refused' => refusedFlag || adminNote.trim().isNotEmpty,
    _ => true,
  };
}

/// 목록 거르기: (서버 status, 이름).
const _filters = [
  ('submitted', '검수 대기'),
  ('hold', '보류'),
  ('rerequest', '재요청'),
  ('passed', '통과'),
  ('refused', '원하지 않음'),
  ('interrupted', '중간에 멈춤'),
  ('all', '전체'),
];

class ConsentReviewListPage extends StatefulWidget {
  final LegalAdminApi api;

  const ConsentReviewListPage({super.key, this.api = const LegalAdminApi()});

  @override
  State<ConsentReviewListPage> createState() => _ConsentReviewListPageState();
}

class _ConsentReviewListPageState extends State<ConsentReviewListPage> {
  static const _size = 50;

  String _status = 'submitted';
  List<Map<String, dynamic>>? _rows;
  int _total = 0;
  int _page = 1;
  bool _loadingMore = false;
  Object? _error;
  int _request = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final request = ++_request;
    setState(() {
      _rows = null;
      _error = null;
      _page = 1;
    });
    try {
      final result = await widget.api.consents(status: _status, size: _size);
      if (!mounted || request != _request) return;
      setState(() {
        _rows = result.rows;
        _total = result.total;
      });
    } catch (error) {
      if (mounted && request == _request) setState(() => _error = error);
    }
  }

  Future<void> _more() async {
    if (_loadingMore) return;
    final request = _request;
    setState(() => _loadingMore = true);
    try {
      final result = await widget.api.consents(status: _status, page: _page + 1, size: _size);
      if (!mounted || request != _request) return;
      setState(() {
        _page++;
        _rows = [...?_rows, ...result.rows];
        _total = result.total;
      });
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    return AdminFrame(
      title: '그분 동의 녹음 검수',
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final (status, label) in _filters)
                Pill(
                  label: label,
                  selected: _status == status,
                  onTap: () {
                    setState(() => _status = status);
                    _load();
                  },
                ),
            ],
          ),
          const SizedBox(height: 18),
          if (_error != null || rows == null || rows.isEmpty)
            adminListState(
              error: _error,
              loading: rows == null,
              empty: rows?.isEmpty ?? false,
              emptyText: '이 상태의 동의 녹음이 없어요.',
              onRetry: _load,
            )
          else ...[
            Text('${rows.length} / $_total건', style: AppText.small),
            const SizedBox(height: 8),
            for (final row in rows)
              Padding(padding: const EdgeInsets.only(bottom: 10), child: _row(row)),
            if (rows.length < _total)
              Center(
                child: TextButton.icon(
                  onPressed: _loadingMore ? null : _more,
                  icon: const Icon(Icons.expand_more_rounded),
                  label: Text(_loadingMore ? '읽는 중…' : '더 보기'),
                ),
              ),
          ],
        ],
      ),
    );
  }

  Widget _row(Map<String, dynamic> row) {
    final status = '${row['status']}';
    final refused = row['refusedFlag'] == true;
    final counts = [
      '${row['attempt'] ?? '?'}번째 녹음',
      if ((row['submittedCount'] as num? ?? 0) > 0) '보낸 녹음 ${row['submittedCount']}개',
      if ((row['interruptedCount'] as num? ?? 0) > 0) '중간에 멈춤 ${row['interruptedCount']}번',
    ].join(' · ');
    return AppCard(
      padding: const EdgeInsets.fromLTRB(18, 14, 12, 14),
      borderColor: refused ? AppColors.danger : null,
      onTap: () => context.go('/admin/consents/${row['id']}'),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  [
                    '${row['title'] ?? '(지움)'}',
                    '${row['ageBand'] ?? consentModeLabels[row['mode']] ?? ''}',
                    if ('${row['ownerMasked'] ?? ''}'.isNotEmpty) '${row['ownerMasked']}',
                  ].where((s) => s.isNotEmpty).join(' · '),
                  style: AppText.label,
                ),
                const SizedBox(height: 4),
                Text(counts, style: AppText.small),
                if (row['submittedAt'] != null) Text('보낸 때 ${adminDate(row['submittedAt'])}', style: AppText.small),
                if (refused) ...[
                  const SizedBox(height: 4),
                  Text(
                    "녹음 중 '원하지 않으세요'를 눌렀어요",
                    style: AppText.small.copyWith(color: AppColors.danger, fontWeight: FontWeight.w700),
                  ),
                ],
              ],
            ),
          ),
          StatusChip(consentStatusLabels[status] ?? status, positive: status == 'submitted'),
          const Icon(Icons.chevron_right_rounded, color: AppColors.muted),
        ],
      ),
    );
  }
}

class ConsentReviewDetailPage extends StatefulWidget {
  final String id;
  final LegalAdminApi api;

  const ConsentReviewDetailPage({super.key, required this.id, this.api = const LegalAdminApi()});

  @override
  State<ConsentReviewDetailPage> createState() => _ConsentReviewDetailPageState();
}

class _ConsentReviewDetailPageState extends State<ConsentReviewDetailPage> {
  Map<String, dynamic>? _row;
  Object? _error;
  bool _busy = false;

  final Map<String, bool> _checks = {for (var i = 1; i <= 10; i++) '$i': false};
  String? _verdict;
  bool _allowMore = false;
  final _note = TextEditingController();
  final _adminNote = TextEditingController();

  /// 관리자가 만든 분께 남길 말을 직접 고쳤나(고쳤으면 체크를 바꿔도 다시 채우지 않는다)
  bool _noteEdited = false;
  final _audio = AdminAudio();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _note.dispose();
    _adminNote.dispose();
    _audio.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final row = await widget.api.consent(widget.id);
      if (!mounted) return;
      setState(() {
        _row = row;
        _error = null;
        _fill(row);
      });
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  /// 지난 판정(보류를 다시 판정할 때)이 있으면 그 값으로 채운다.
  void _fill(Map<String, dynamic> row) {
    final review = row['review'] is Map ? Map<String, dynamic>.from(row['review'] as Map) : null;
    final saved = review?['checklist'] is Map ? review!['checklist'] as Map : const {};
    for (var i = 1; i <= 10; i++) {
      _checks['$i'] = saved['$i'] == true;
    }
    final verdict = '${review?['verdict'] ?? ''}';
    _verdict = switch (verdict) {
      'pass' || 'passed' => 'pass',
      'rerequest' || 'hold' || 'refused' => verdict,
      _ => null,
    };
    _note.text = '${review?['noteToOwner'] ?? ''}';
    _adminNote.text = '${review?['adminNote'] ?? ''}';
    _noteEdited = _note.text.trim().isNotEmpty;
    _allowMore = false;
    _sanitize();
  }

  bool get _reviewable => const {'submitted', 'hold'}.contains(_row?['status']);
  bool get _refusedFlag => _row?['refusedFlag'] == true;
  bool get _hasStory => _row?['hasStoryRecordings'] == true;

  bool _allowed(String verdict) => verdictAllowed(
        verdict,
        checks: _checks,
        reviewable: _reviewable,
        refusedFlag: _refusedFlag,
        adminNote: _adminNote.text,
        hasStoryRecordings: _hasStory,
      );

  /// 고른 판정이 더는 안 되면 풀고, 재요청 글을 실패 항목에 맞춰 다시 채운다.
  /// 재요청이 아니면 미리 채운 글은 비운다(고쳐 쓴 글은 다시 재요청을 고를 때를 위해 둔다).
  void _sanitize() {
    if (_verdict != null && !_allowed(_verdict!)) _verdict = null;
    if (_verdict != 'rerequest') _allowMore = false;
    if (_verdict == 'rerequest' && !_noteEdited) {
      _note.text = rerequestNote(_checks);
    } else if (_verdict != 'rerequest' && !_noteEdited) {
      _note.text = '';
    }
  }

  void _change(VoidCallback change) => setState(() {
        change();
        _sanitize();
      });

  Future<void> _save() async {
    final verdict = _verdict;
    if (verdict == null || !_allowed(verdict)) return;
    if (verdict == 'pass' &&
        !await confirmAction(context, '통과로 저장할까요?', '통과하면 그때부터 그분 녹음 처리와 목소리 만들기가 열려요. 만든 분께 알림이 가요.',
            action: '통과', danger: false)) {
      return;
    }
    if (!mounted) return;
    if (verdict == 'refused' &&
        !await confirmAction(context, '원하지 않음으로 저장할까요?', '이 대화 상대를 바로 멈추고 7일 뒤 지워요. 만든 분께 알림이 가요.',
            action: '원하지 않음')) {
      return;
    }
    if (!mounted) return;
    setState(() => _busy = true);
    try {
      await widget.api.review(
        widget.id,
        checklist: Map.of(_checks),
        verdict: verdict,
        // 만든 분께 남길 말은 재요청 알림에만 쓰인다(통과·보류 기록에 재요청 글이 남지 않게)
        noteToOwner: verdict == 'rerequest' ? _note.text : null,
        adminNote: _adminNote.text,
        allowMore: _allowMore,
      );
      if (!mounted) return;
      showMessage(context, '판정을 저장했어요. 만든 분께 알림을 남겼어요.');
      await _load();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final row = _row;
    return AdminFrame(
      title: '그분 동의 녹음',
      back: '/admin/consents',
      backLabel: '검수 목록',
      actions: [IconButton(tooltip: '새로고침', onPressed: _load, icon: const Icon(Icons.refresh_rounded))],
      body: row == null
          ? (_error != null ? LoadFailed(_error!, onRetry: _load) : const Padding(padding: EdgeInsets.all(40), child: Loading()))
          : Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _who(row),
                _recording(row),
                _history(row),
                _stories(row),
                if (row['guardian'] is Map) _guardian(Map<String, dynamic>.from(row['guardian'] as Map)),
                _checklist(row),
                _decision(row),
              ],
            ),
    );
  }

  Widget _who(Map<String, dynamic> row) {
    final persona = row['persona'] is Map ? Map<String, dynamic>.from(row['persona'] as Map) : <String, dynamic>{};
    final owner = row['owner'] is Map ? Map<String, dynamic>.from(row['owner'] as Map) : <String, dynamic>{};
    final minor = persona['minor'] == true;
    return AdminSection(
      '대화 상대',
      trailing: persona['id'] == null
          ? null
          : TextButton(
              onPressed: () => context.go('/admin/personas/${persona['id']}'),
              child: const Text('대화 상대 관리'),
            ),
      children: [
        AdminLine('호칭', '${persona['title'] ?? ''}'),
        AdminLine('성함', '${persona['name'] ?? ''}'),
        AdminLine('관계', '그분은 나의 ${persona['relation'] ?? '?'} · 나는 그분의 ${persona['myRelation'] ?? '?'}'),
        AdminLine('상태', lifeStatusLabels[persona['status']] ?? '${persona['status'] ?? ''}'),
        AdminLine('나이 구간', consentModeLabels[row['mode']] ?? '${row['mode'] ?? ''}'),
        if (minor) ...[
          AdminLine('태어난 해·달', '${persona['birthYm'] ?? ''} (만 ${persona['age'] ?? '?'}세)'),
          AdminLine('부모 양쪽', guardianBasisLabels[persona['guardianBasis']] ?? '${persona['guardianBasis'] ?? ''}'),
        ],
        AdminLine('단계', '${persona['step'] ?? ''}'),
        AdminLine('만든 날', adminDate(persona['createdAt'])),
        AdminLine('만든 분', '${owner['masked'] ?? ''} · 가입 ${adminDate(owner['joinedAt'])}'),
      ],
    );
  }

  Widget _recording(Map<String, dynamic> row) {
    final words = adminWords(row['words']);
    final sha = '${row['sha256'] ?? ''}';
    final steps = [for (final s in row['steps'] as List? ?? const []) if (s is Map) Map<String, dynamic>.from(s)];
    final url = row['audioUrl'] as String?;
    final gap = (row['gapSeconds'] as num?)?.toDouble();
    final minor = row['mode'] == 'minor_teen' || row['mode'] == 'minor_child';
    final status = '${row['status']}';
    return AdminSection(
      '지금 녹음 · ${row['attempt'] ?? '?'}번째',
      trailing: StatusChip(consentStatusLabels[status] ?? status, positive: status == 'passed'),
      children: [
        if (_refusedFlag) ...[
          ErrorBox(minor ? "녹음 중 '아이가 싫다고 했어요'를 눌렀어요." : "녹음 중 '그분이 원하지 않으세요'를 눌렀어요."),
          const SizedBox(height: 10),
        ],
        if (row['interrupted'] == true) ...[
          Notice('중간에 멈춘 녹음이에요${row['reason'] != null ? ' (${row['reason']})' : ''}. 검수 대상이 아니에요.'),
          const SizedBox(height: 10),
        ],
        const Text('확인 단어', style: AppText.small),
        SelectableText(
          words.isEmpty ? '—' : words,
          style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w800, letterSpacing: 1, color: AppColors.ink),
        ),
        const SizedBox(height: 8),
        Align(alignment: Alignment.centerLeft, child: AdminAudioButton(audio: _audio, url: url, label: '동의 녹음 듣기')),
        const SizedBox(height: 8),
        AdminLine('단어 받은 때', adminDate(row['wordsIssuedAt'])),
        AdminLine('보낸 때', adminDate(row['submittedAt'])),
        AdminLine('길이 · 크기', '${adminClock(row['durationMs'])} · ${adminSize(row['sizeBytes'])} · ${row['contentType'] ?? ''}'),
        AdminLine('파일 해시', sha.length > 12 ? '${sha.substring(0, 12)}…' : sha),
        if (gap != null && gap > 300) ...[
          const SizedBox(height: 6),
          const Notice('확인 단어를 받은 뒤 오래 걸렸어요 — 여러 번 녹음했거나 편집했을 수 있어요(8번 확인).'),
        ],
        if (steps.isNotEmpty) ...[
          const SizedBox(height: 10),
          const Text('단계 (누르면 그 자리부터 들어요)', style: AppText.small),
          const SizedBox(height: 4),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final s in steps)
                ActionChip(
                  label: Text('단계 ${s['step']} — ${adminClock(s['atMs'])}'),
                  onPressed: url == null
                      ? null
                      : () async {
                          try {
                            await _audio.toggle(url, at: Duration(milliseconds: (s['atMs'] as num? ?? 0).toInt()));
                          } catch (_) {
                            if (mounted) showMessage(context, '재생하지 못했어요. 주소가 지났으면 새로고침해 주세요.');
                          }
                        },
                ),
            ],
          ),
        ],
      ],
    );
  }

  Widget _history(Map<String, dynamic> row) {
    final history = [for (final h in row['history'] as List? ?? const []) if (h is Map) Map<String, dynamic>.from(h)];
    final earlier = history.where((h) => '${h['id']}' != widget.id).toList();
    if (earlier.isEmpty) return const SizedBox.shrink();
    return AdminSection(
      '앞선 녹음 ${earlier.length}개',
      children: [
        for (final h in earlier)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        '${h['attempt'] ?? '?'}번째 · 확인 단어 ${adminWords(h['words'])} · ${adminClock(h['durationMs'])}',
                        style: AppText.label,
                      ),
                    ),
                    StatusChip(consentStatusLabels[h['status']] ?? '${h['status']}', positive: h['status'] == 'passed'),
                  ],
                ),
                Text(
                  [
                    if (h['issuedAt'] != null) '단어 ${adminDate(h['issuedAt'])}',
                    if (h['submittedAt'] != null) '보냄 ${adminDate(h['submittedAt'])}',
                    if (h['reviewedAt'] != null) '판정 ${adminDate(h['reviewedAt'])}',
                    if (h['checklist'] is Map)
                      '통과 항목 ${(h['checklist'] as Map).values.where((v) => v == true).length}/10',
                  ].join(' · '),
                  style: AppText.small,
                ),
                if ('${h['noteToOwner'] ?? ''}'.trim().isNotEmpty)
                  Text('남긴 말: ${h['noteToOwner']}', style: AppText.small),
                if (h['audioUrl'] != null) ...[
                  const SizedBox(height: 4),
                  AdminAudioButton(audio: _audio, url: h['audioUrl'] as String?),
                ],
              ],
            ),
          ),
      ],
    );
  }

  Widget _stories(Map<String, dynamic> row) {
    final recordings = [for (final r in row['recordings'] as List? ?? const []) if (r is Map) Map<String, dynamic>.from(r)];
    return AdminSection(
      '이야기 녹음 (1번 목소리 비교)',
      children: [
        if (!_hasStory || recordings.isEmpty)
          const Notice('비교할 이야기 녹음이 아직 없어요. 녹음이 올라온 뒤에 통과할 수 있어요.')
        else
          for (final r in recordings)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 3),
              child: Row(
                children: [
                  Expanded(child: Text('${r['name'] ?? '녹음'} · ${adminClock(r['durationMs'])}', style: AppText.body)),
                  AdminAudioButton(audio: _audio, url: r['url'] as String?),
                ],
              ),
            ),
      ],
    );
  }

  Widget _guardian(Map<String, dynamic> g) {
    String at(String key) => g[key] == null ? '아직' : adminDate(g[key]);
    return AdminSection(
      '법정대리인',
      children: [
        AdminLine('법정대리인 확인', at('attestAt')),
        AdminLine('아이 정보 동의', at('consentAt')),
        AdminLine('해외 회사 동의', at('overseasAt')),
        AdminLine('확인 문자', g['smsVerifiedAt'] == null ? '아직' : '${adminDate(g['smsVerifiedAt'])} · ${g['phoneMasked'] ?? ''}'),
      ],
    );
  }

  /// 서버가 준 10항목({key, title, detail?, failExample})이 있으면 그 글, 없으면 화면에 둔 글.
  (String, String) _item(Map<String, dynamic> row, int index) {
    final server = row['checklist'];
    if (server is List && index < server.length && server[index] is Map) {
      final m = server[index] as Map;
      final title = '${m['title'] ?? ''}'.trim();
      final detail = '${m['detail'] ?? ''}'.trim();
      final fail = '${m['failExample'] ?? ''}'.trim();
      if (title.isNotEmpty) {
        return (detail.isEmpty ? title : '$title — $detail', fail.isNotEmpty ? fail : consentChecklist[index].$2);
      }
    }
    return consentChecklist[index];
  }

  /// 판정 안내: 서버 글(guide)이 있으면 그것, 없으면 화면에 둔 글.
  List<String> _guide(Map<String, dynamic> row) {
    final server = [for (final line in row['guide'] as List? ?? const []) '$line'.trim()]..removeWhere((l) => l.isEmpty);
    return server.isEmpty ? consentVerdictGuide : server;
  }

  Widget _checklist(Map<String, dynamic> row) {
    return AdminSection(
      '검수 10항목',
      children: [
        for (var i = 0; i < 10; i++)
          InkWell(
            borderRadius: BorderRadius.circular(8),
            onTap: _reviewable && !_busy ? () => _change(() => _checks['${i + 1}'] = !(_checks['${i + 1}'] ?? false)) : null,
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Checkbox(
                  value: _checks['${i + 1}'] ?? false,
                  onChanged: _reviewable && !_busy ? (v) => _change(() => _checks['${i + 1}'] = v ?? false) : null,
                ),
                Expanded(
                  child: Padding(
                    padding: const EdgeInsets.only(top: 10, bottom: 8),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('${i + 1}. ${_item(row, i).$1}', style: AppText.label),
                        const SizedBox(height: 2),
                        Text('불통과 예시: ${_item(row, i).$2}', style: AppText.small),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        const SizedBox(height: 8),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(color: AppColors.sunk, borderRadius: BorderRadius.circular(12)),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('판정 안내', style: AppText.label),
              const SizedBox(height: 4),
              for (final line in _guide(row)) Text('· $line', style: AppText.small.copyWith(color: AppColors.ink2)),
            ],
          ),
        ),
      ],
    );
  }

  Widget _decision(Map<String, dynamic> row) {
    final review = row['review'] is Map ? Map<String, dynamic>.from(row['review'] as Map) : null;
    return AdminSection(
      '판정',
      children: [
        if (!_reviewable)
          Notice(review == null
              ? '이 녹음은 지금 판정할 수 없어요(검수 대기나 보류만 판정해요).'
              : '판정했어요: ${consentVerdictLabels[review['verdict']] ?? consentStatusLabels[row['status']] ?? ''}'
                  ' · ${adminDate(review['reviewedAt'])}${review['reviewer'] != null ? ' · ${review['reviewer']}' : ''}'),
        if (_reviewable) ...[
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final e in consentVerdictLabels.entries)
                Opacity(
                  opacity: _allowed(e.key) ? 1 : 0.4,
                  child: Pill(
                    label: e.value,
                    selected: _verdict == e.key,
                    onTap: _allowed(e.key) && !_busy ? () => _change(() => _verdict = e.key) : null,
                  ),
                ),
            ],
          ),
          const SizedBox(height: 6),
          if (!_hasStory)
            const Text('이야기 녹음이 없으면 통과할 수 없어요.', style: AppText.small)
          else if (!_allowed('pass'))
            const Text('10개 항목을 모두 체크해야 통과할 수 있어요.', style: AppText.small),
          if (!_allowed('refused'))
            const Text("원하지 않음은 녹음 중 '원하지 않으세요' 표시가 있거나 내부 메모를 적어야 고를 수 있어요.",
                style: AppText.small),
          if (_verdict == 'rerequest')
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _allowMore,
              onChanged: _busy ? null : (v) => setState(() => _allowMore = v ?? false),
              title: const Text('한 번 더 녹음 허락(녹음 상한 +1)', style: AppText.body),
            ),
        ],
        const SizedBox(height: 12),
        Row(
          children: [
            const Expanded(child: Text('만든 분께 남길 말', style: AppText.label)),
            if (_reviewable && _verdict == 'rerequest')
              TextButton(
                onPressed: () => setState(() {
                  _noteEdited = false;
                  _note.text = rerequestNote(_checks);
                }),
                child: const Text('실패 항목으로 다시 채우기'),
              ),
          ],
        ),
        const SizedBox(height: 6),
        TextField(
          controller: _note,
          enabled: _reviewable && !_busy && _verdict == 'rerequest',
          maxLines: 6,
          minLines: 3,
          maxLength: 500,
          decoration: const InputDecoration(hintText: '재요청일 때만 보내요. 실패 항목에 맞춰 미리 채워요. 고쳐도 돼요.'),
          onChanged: (_) => _noteEdited = true,
        ),
        const SizedBox(height: 8),
        const Text('내부 메모', style: AppText.label),
        const SizedBox(height: 6),
        TextField(
          controller: _adminNote,
          enabled: _reviewable && !_busy,
          maxLines: 4,
          minLines: 2,
          maxLength: 1000,
          decoration: const InputDecoration(hintText: '만든 분께는 보이지 않아요. 원하지 않음이면 까닭을 적어 주세요.'),
          onChanged: (_) => _change(() {}),
        ),
        const SizedBox(height: 8),
        if (_reviewable)
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton(
              onPressed: _verdict != null && _allowed(_verdict!) && !_busy ? _save : null,
              child: Text(_busy ? '저장하는 중…' : '판정 저장'),
            ),
          ),
      ],
    );
  }
}
