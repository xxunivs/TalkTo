import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import '../interview/interview_api.dart';
import '../personas/data/persona_api.dart';

/// 관리자 화면 · 기억 인터뷰(관리자만).
///
///   /admin/interviews               신청 목록(상태로 거르기, 전화번호 없이)
///   /admin/interviews/:id           신청 하나: 연락처·상태·메모 + 들은 내용 넣기
///   /admin/personas/:id/interview   신청 없이 대화 상대에 바로 들은 내용 넣기
///
/// 들은 내용은 글로만 넣는다(녹음 파일은 올리지 않는다): 질문 번호별 답(지금 답에 합친다)·기억 카드
/// (바로 대화에 쓰인다). 답을 넣은 뒤 '다시 만들기'를 누르면 지침이 새 답으로 다시 만들어진다.
const interviewStatusFilters = ['requested', 'scheduled', 'done', 'cancelled'];

String _date(Object? value) {
  final text = '${value ?? ''}';
  if (text.length < 16) return text;
  final at = DateTime.tryParse(text)?.toLocal();
  if (at == null) return text.substring(0, 16);
  String two(int n) => n.toString().padLeft(2, '0');
  return '${at.year}-${two(at.month)}-${two(at.day)} ${two(at.hour)}:${two(at.minute)}';
}

String _schedule(Map row) => [
      if ((row['days'] as List? ?? const []).isNotEmpty) (row['days'] as List).join('·'),
      if ('${row['timeText'] ?? ''}'.isNotEmpty) '${row['timeText']}',
      interviewMethodLabels[row['method']] ?? '${row['method'] ?? ''}',
    ].join(' · ');

class InterviewAdminListPage extends StatefulWidget {
  final InterviewApi api;

  const InterviewAdminListPage({super.key, this.api = const InterviewApi()});

  @override
  State<InterviewAdminListPage> createState() => _InterviewAdminListPageState();
}

class _InterviewAdminListPageState extends State<InterviewAdminListPage> {
  String? _status = 'requested';
  List<Map<String, dynamic>>? _rows;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final rows = await widget.api.adminList(status: _status);
      if (mounted) setState(() => _rows = rows);
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    return Scaffold(
      appBar: AppBar(
        title: const Text('기억 인터뷰 신청'),
        leading: IconButton(
          tooltip: '개발자 화면',
          icon: const Icon(Icons.arrow_back_rounded),
          onPressed: () => context.go('/admin'),
        ),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 40),
        child: PageBody(
          maxWidth: 760,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  Pill(
                    label: '전체',
                    selected: _status == null,
                    onTap: () {
                      setState(() => _status = null);
                      _load();
                    },
                  ),
                  for (final s in interviewStatusFilters)
                    Pill(
                      label: interviewStatusLabels[s] ?? s,
                      selected: _status == s,
                      onTap: () {
                        setState(() => _status = s);
                        _load();
                      },
                    ),
                ],
              ),
              const SizedBox(height: 18),
              if (_error != null)
                LoadFailed(_error!, onRetry: _load)
              else if (rows == null)
                const Padding(padding: EdgeInsets.all(40), child: Loading())
              else if (rows.isEmpty)
                const Notice('이 상태의 신청이 없어요.')
              else
                for (final row in rows)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: AppCard(
                      padding: const EdgeInsets.fromLTRB(18, 14, 12, 14),
                      onTap: () => context.go('/admin/interviews/${row['id']}'),
                      child: Row(
                        children: [
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  [
                                    row['wiped'] == true ? '(지운 연락처)' : '${row['name'] ?? ''}',
                                    if ('${row['relation'] ?? ''}'.isNotEmpty) '${row['relation']}',
                                    '대화 상대: ${(row['persona'] as Map?)?['title'] ?? '(지움)'}',
                                  ].join(' · '),
                                  style: AppText.label,
                                ),
                                const SizedBox(height: 4),
                                Text(_schedule(row), style: AppText.small),
                                Text('신청 ${_date(row['createdAt'])}', style: AppText.small),
                              ],
                            ),
                          ),
                          StatusChip(
                            interviewStatusLabels[row['status']] ?? '${row['status']}',
                            positive: row['status'] == 'requested',
                          ),
                          const Icon(Icons.chevron_right_rounded, color: AppColors.muted),
                        ],
                      ),
                    ),
                  ),
            ],
          ),
        ),
      ),
    );
  }
}

class InterviewAdminDetailPage extends StatefulWidget {
  final String requestId;
  final InterviewApi api;

  const InterviewAdminDetailPage({super.key, required this.requestId, this.api = const InterviewApi()});

  @override
  State<InterviewAdminDetailPage> createState() => _InterviewAdminDetailPageState();
}

class _InterviewAdminDetailPageState extends State<InterviewAdminDetailPage> {
  Map<String, dynamic>? _row;
  Object? _error;
  bool _busy = false;
  final _note = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final row = await widget.api.adminDetail(widget.requestId);
      if (!mounted) return;
      setState(() {
        _row = row;
        _error = null;
        _note.text = '${row['adminNote'] ?? ''}';
      });
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  Future<void> _update({String? status, String? note}) async {
    setState(() => _busy = true);
    try {
      final row = await widget.api.adminUpdate(widget.requestId, status: status, note: note);
      if (!mounted) return;
      setState(() => _row = row);
      showMessage(context, note != null ? '메모를 저장했어요.' : '상태를 바꿨어요.');
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _line(String label, String value, {bool copy = false}) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(width: 92, child: Text(label, style: AppText.small)),
          Expanded(child: SelectableText(value, style: AppText.body)),
          if (copy && value.isNotEmpty)
            IconButton(
              tooltip: '복사',
              icon: const Icon(Icons.copy_rounded, size: 18),
              onPressed: () {
                Clipboard.setData(ClipboardData(text: value));
                showMessage(context, '복사했어요.');
              },
            ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final row = _row;
    return Scaffold(
      appBar: AppBar(
        title: const Text('기억 인터뷰 신청'),
        leading: IconButton(
          tooltip: '신청 목록',
          icon: const Icon(Icons.arrow_back_rounded),
          onPressed: () => context.go('/admin/interviews'),
        ),
      ),
      body: row == null
          ? (_error != null ? LoadFailed(_error!, onRetry: _load) : const Loading())
          : SingleChildScrollView(
              padding: const EdgeInsets.fromLTRB(16, 16, 16, 48),
              child: PageBody(
                maxWidth: 760,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    AppCard(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('신청 내용', style: AppText.heading),
                          const SizedBox(height: 8),
                          if (row['wiped'] == true)
                            const Notice('끝난 지 30일이 지나 이름·연락처·희망 일정을 지웠어요.'),
                          _line('받으실 분', '${row['name'] ?? ''}'),
                          _line('관계', '${row['relation'] ?? ''}'),
                          _line('전화번호', '${row['phone'] ?? ''}', copy: true),
                          _line('희망', _schedule(row)),
                          _line('통화 녹음', row['recordingOk'] == true ? '동의함' : '동의하지 않음 — 녹음하지 않아요'),
                          _line('대화 상대', '${(row['persona'] as Map?)?['title'] ?? '(지움)'}'),
                          _line('신청', _date(row['createdAt'])),
                        ],
                      ),
                    ),
                    const SizedBox(height: 16),
                    const SectionLabel('상태'),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (final s in interviewStatusFilters)
                          Pill(
                            label: interviewStatusLabels[s] ?? s,
                            selected: row['status'] == s,
                            onTap: _busy || row['status'] == s ? null : () => _update(status: s),
                          ),
                      ],
                    ),
                    const SizedBox(height: 18),
                    const SectionLabel('메모'),
                    TextField(
                      controller: _note,
                      maxLines: 3,
                      maxLength: 1000,
                      decoration: const InputDecoration(
                        hintText: '일정·진행 메모. 연락처 같은 개인 정보는 적지 않아요(30일 뒤에도 남아요).',
                      ),
                    ),
                    Align(
                      alignment: Alignment.centerRight,
                      child: OutlinedButton(
                        onPressed: _busy ? null : () => _update(note: _note.text),
                        child: const Text('메모 저장'),
                      ),
                    ),
                    const SizedBox(height: 24),
                    if (row['persona'] is Map)
                      InterviewEntryPanel(
                        personaId: '${(row['persona'] as Map)['id']}',
                        requestId: widget.requestId,
                        api: widget.api,
                      )
                    else
                      const Notice('대화 상대를 지워서 들은 내용을 넣을 수 없어요.'),
                  ],
                ),
              ),
            ),
    );
  }
}

/// 신청 없이 대화 상대에 바로 들은 내용을 넣는 화면.
class InterviewPersonaAdminPage extends StatelessWidget {
  final String personaId;
  final InterviewApi api;

  const InterviewPersonaAdminPage({super.key, required this.personaId, this.api = const InterviewApi()});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('들은 내용 넣기'),
        leading: IconButton(
          tooltip: '신청 목록',
          icon: const Icon(Icons.arrow_back_rounded),
          onPressed: () => context.go('/admin/interviews'),
        ),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 48),
        child: PageBody(maxWidth: 760, child: InterviewEntryPanel(personaId: personaId, api: api)),
      ),
    );
  }
}

/// 들은 내용 넣기: 질문 번호별 답 · 기억 카드 · 다시 만들기.
class InterviewEntryPanel extends StatefulWidget {
  final String personaId;
  final String? requestId;
  final InterviewApi api;

  /// 질문 목록(서버 /intake/meta 의 questions + moreQuestions). 시험에서는 바꿔 넣는다.
  final Future<Map<String, dynamic>> Function()? meta;

  const InterviewEntryPanel({
    super.key,
    required this.personaId,
    this.requestId,
    this.api = const InterviewApi(),
    this.meta,
  });

  @override
  State<InterviewEntryPanel> createState() => _InterviewEntryPanelState();
}

class _InterviewEntryPanelState extends State<InterviewEntryPanel> {
  Map<String, dynamic>? _data;
  List<Map<String, dynamic>> _questions = [];
  Object? _error;
  bool _busy = false;
  String? _questionId;
  bool _replace = false;
  bool _askedOnly = false;
  Timer? _poll;
  final _answer = TextEditingController();
  final _memoryTitle = TextEditingController();
  final _memoryText = TextEditingController();

  String get _pid => widget.personaId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _poll?.cancel();
    _answer.dispose();
    _memoryTitle.dispose();
    _memoryText.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([
        widget.api.adminPersona(_pid),
        (widget.meta ?? PersonaApi.meta)(),
      ]);
      if (!mounted) return;
      final meta = results[1];
      final seen = <String>{};
      setState(() {
        _data = results[0];
        _error = null;
        _questions = [
          for (final q in [...(meta['questions'] as List? ?? const []), ...(meta['moreQuestions'] as List? ?? const [])])
            if (q is Map && q['type'] != 'family' && seen.add('${q['id']}')) Map<String, dynamic>.from(q),
        ];
      });
      _watch();
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  Map? get _rebuild => _data?['rebuild'] as Map?;
  bool get _rebuilding => _rebuild?['status'] == 'RUNNING';

  /// 다시 만드는 동안 몇 초마다 상태만 다시 읽는다.
  void _watch() {
    _poll?.cancel();
    if (!_rebuilding) return;
    _poll = Timer(const Duration(seconds: 3), () async {
      try {
        final fresh = await widget.api.adminPersona(_pid);
        if (mounted) setState(() => _data = fresh);
      } catch (_) {
        // 다음 차례에 다시 읽는다
      }
      if (mounted) _watch();
    });
  }

  Map<String, dynamic>? get _question => _questions.where((q) => q['id'] == _questionId).firstOrNull;

  /// [done] 은 다 했을 때 보일 글(일을 마친 뒤 정해지면 글을 돌려주는 함수).
  Future<void> _run(Future<void> Function() action, Object done) async {
    setState(() => _busy = true);
    try {
      await action();
      if (!mounted) return;
      showMessage(context, done is String Function() ? done() : done);
      final fresh = await widget.api.adminPersona(_pid);
      if (mounted) setState(() => _data = fresh);
      _watch();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _addAnswer() => _run(() async {
        await widget.api.addAnswer(
          _pid,
          questionId: _questionId!,
          text: _answer.text.trim(),
          replace: _replace,
          requestId: widget.requestId,
        );
        _answer.clear();
        _replace = false;
      }, "넣었어요. 다 넣은 뒤 '다시 만들기'를 눌러 주세요.");

  Future<void> _addMemory() => _run(() async {
        await widget.api.addMemory(
          _pid,
          text: _memoryText.text.trim(),
          title: _memoryTitle.text,
          askedOnly: _askedOnly,
        );
        _memoryText.clear();
        _memoryTitle.clear();
        _askedOnly = false;
      }, '기억 카드를 넣었어요. 다음 대화부터 쓰여요.');

  Future<void> _removeMemory(String id) async {
    final ok = await confirmAction(context, '이 기억 카드를 지울까요?', '지우면 대화에서 더 쓰지 않아요.');
    if (!ok || !mounted) return;
    await _run(() => widget.api.removeMemory(_pid, id), '지웠어요.');
  }

  /// 넣은 답 하나 빼기(만든 분이 빼 달라고 할 때). 그 답이 더한 부분만 지금 답에서 빠진다.
  Future<void> _removeAnswer(String id) async {
    final ok = await confirmAction(
      context,
      '이 답을 뺄까요?',
      "이 답이 더한 부분만 지금 답에서 빠져요. 다 고친 뒤 '다시 만들기'를 눌러 주세요.",
      action: '빼기',
    );
    if (!ok || !mounted) return;
    var done = "뺐어요. 다 고친 뒤 '다시 만들기'를 눌러 주세요.";
    await _run(() async {
      final removed = await widget.api.removeAnswer(_pid, id);
      if (removed['requiredMissing'] == true) {
        done = '뺐어요. 꼭 답할 질문(${removed['questionId']})이 비었어요. 만든 분께 다시 채워 달라고 해 주세요.';
      }
    }, () => done);
  }

  Future<void> _startRebuild() => _run(() => widget.api.rebuild(_pid), '다시 만들기를 시작했어요. 1~2분 걸려요.');

  /// 가족이 '기억이 안 나요'·'따로 없었어요'·'모름'을 고른 답의 저장 값 → 보이는 글(문항에 skipOption 이 없을 때).
  static const _skipLabels = {'__unknown__': '기억이 안 나요/모름', '__none__': '따로 없었어요'};

  /// 지금 답을 한 줄로. 저장 값(__unknown__ 등)은 그 문항의 단추 이름으로 보인다.
  static String _current(Map question, Object? value) {
    if (value == null) return '(아직 답 없음)';
    if (value is List) return value.join(' / ');
    final skip = question['skipOption'];
    if (value is String && skip is Map && value == skip['value'] && skip['label'] is String) return '${skip['label']}';
    return _skipLabels[value] ?? '$value';
  }

  @override
  Widget build(BuildContext context) {
    final data = _data;
    if (data == null) {
      return _error != null ? LoadFailed(_error!, onRetry: _load) : const Padding(padding: EdgeInsets.all(32), child: Loading());
    }
    final persona = Map<String, dynamic>.from(data['persona'] as Map? ?? const {});
    final imported = persona['origin'] == 'import';
    final answers = Map<String, dynamic>.from(data['answers'] as Map? ?? const {});
    final entries = [for (final e in data['entries'] as List? ?? const []) Map<String, dynamic>.from(e as Map)];
    final memories = [for (final m in data['memories'] as List? ?? const []) Map<String, dynamic>.from(m as Map)];
    final question = _question;
    final rebuild = _rebuild;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('들은 내용 넣기 · ${persona['title'] ?? ''}', style: AppText.title),
        const SizedBox(height: 6),
        const Text(
          '녹음 파일은 올리지 않아요. 들은 이야기를 글로만 넣어요. '
          '건강·종교 이야기는 만든 분이 그 이야기에 동의한 경우에만 그 질문 답으로 넣어요.',
          style: AppText.small,
        ),
        if (persona['self'] == true) ...[
          const SizedBox(height: 10),
          const Notice('나 자신 대화 상대예요. 기억 인터뷰는 만든 분(그분이 아닌 가족)께만 해요.'),
        ],
        const SizedBox(height: 20),
        // ── 질문 번호별 답 ──
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text('질문 답 넣기', style: AppText.heading),
              const SizedBox(height: 10),
              if (imported)
                const Notice('가져온 대화 상대는 질문 답을 넣지 않아요. 아래 기억 카드로 넣어 주세요.')
              else ...[
                DropdownButtonFormField<String>(
                  key: const ValueKey('interview-question'),
                  isExpanded: true,
                  value: _questionId,
                  hint: const Text('질문 고르기'),
                  items: [
                    for (final q in _questions)
                      DropdownMenuItem(
                        value: '${q['id']}',
                        child: Text('${q['id']} · ${q['title']}', overflow: TextOverflow.ellipsis),
                      ),
                  ],
                  onChanged: _busy ? null : (v) => setState(() => _questionId = v),
                ),
                if (question != null) ...[
                  const SizedBox(height: 10),
                  Text('지금 답: ${_current(question, answers[question['id']])}', style: AppText.small),
                  if ((question['options'] as List?)?.isNotEmpty == true)
                    Text('고를 수 있는 답: ${(question['options'] as List).join(' / ')}', style: AppText.small),
                  if (question['type'] == 'phrases' || question['type'] == 'multi')
                    const Text('한 줄에 하나씩 적어 주세요.', style: AppText.small),
                ],
                const SizedBox(height: 10),
                TextField(
                  controller: _answer,
                  minLines: 2,
                  maxLines: 6,
                  maxLength: 2000,
                  decoration: const InputDecoration(hintText: '들은 답'),
                  onChanged: (_) => setState(() {}),
                ),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  controlAffinity: ListTileControlAffinity.leading,
                  value: _replace,
                  onChanged: _busy ? null : (v) => setState(() => _replace = v ?? false),
                  title: const Text('지금 답 바꾸기 (끄면 긴 글·목록에 덧붙여요)'),
                ),
                Align(
                  alignment: Alignment.centerRight,
                  child: FilledButton(
                    onPressed: _busy || _questionId == null || _answer.text.trim().isEmpty ? null : _addAnswer,
                    child: const Text('답 넣기'),
                  ),
                ),
              ],
              if (entries.isNotEmpty) ...[
                const SizedBox(height: 14),
                const SectionLabel('인터뷰로 넣은 답'),
                for (final e in entries.reversed)
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: Padding(
                          padding: const EdgeInsets.only(top: 10, bottom: 6),
                          child: Text(
                            '${e['questionId']} ${e['questionTitle'] ?? ''}\n${e['text']}  (${_date(e['createdAt'])})',
                            style: AppText.small.copyWith(color: AppColors.ink2),
                          ),
                        ),
                      ),
                      IconButton(
                        tooltip: '이 답 빼기',
                        onPressed: _busy || _rebuilding ? null : () => _removeAnswer('${e['id']}'),
                        icon: const Icon(Icons.delete_outline_rounded, size: 20),
                      ),
                    ],
                  ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 16),
        // ── 기억 카드 ──
        AppCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text('기억 카드 넣기', style: AppText.heading),
              const SizedBox(height: 4),
              const Text('질문에 딱 맞지 않는 이야기 하나. 넣자마자 대화에서 찾혀요.', style: AppText.small),
              const SizedBox(height: 10),
              TextField(
                controller: _memoryTitle,
                maxLength: 60,
                decoration: const InputDecoration(hintText: '주제 (예: 즐겨 부르시던 노래)', counterText: ''),
              ),
              const SizedBox(height: 8),
              TextField(
                controller: _memoryText,
                minLines: 3,
                maxLines: 8,
                maxLength: 2000,
                decoration: const InputDecoration(hintText: '기억 내용'),
                onChanged: (_) => setState(() {}),
              ),
              CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                value: _askedOnly,
                onChanged: _busy ? null : (v) => setState(() => _askedOnly = v ?? false),
                title: const Text('물어볼 때만 (먼저 꺼내지 않아요)'),
              ),
              Align(
                alignment: Alignment.centerRight,
                child: FilledButton(
                  onPressed: _busy || _memoryText.text.trim().isEmpty ? null : _addMemory,
                  child: const Text('기억 카드 넣기'),
                ),
              ),
              if (memories.isNotEmpty) ...[
                const SizedBox(height: 10),
                const SectionLabel('인터뷰 기억 카드'),
                for (final m in memories)
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: Padding(
                          padding: const EdgeInsets.only(top: 10, bottom: 6),
                          child: Text('${m['title']}\n${m['text']}', style: AppText.small.copyWith(color: AppColors.ink2)),
                        ),
                      ),
                      IconButton(
                        tooltip: '지우기',
                        onPressed: _busy ? null : () => _removeMemory('${m['id']}'),
                        icon: const Icon(Icons.delete_outline_rounded, size: 20),
                      ),
                    ],
                  ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 16),
        // ── 다시 만들기 ──
        if (!imported)
          AppCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text('다시 만들기', style: AppText.heading),
                const SizedBox(height: 4),
                const Text('넣은 답으로 말투·지침을 다시 만들어요. 그동안에도 대화할 수 있어요.', style: AppText.small),
                if (rebuild != null) ...[
                  const SizedBox(height: 8),
                  Text(
                    _rebuilding
                        ? '다시 만드는 중${rebuild['percent'] != null ? ' · ${rebuild['percent']}%' : ''}'
                        : '마지막: ${rebuild['status']} · ${_date(rebuild['createdAt'])}'
                            '${rebuild['error'] != null ? ' · ${rebuild['error']}' : ''}',
                    style: AppText.small.copyWith(fontWeight: FontWeight.w700),
                  ),
                ],
                const SizedBox(height: 10),
                Align(
                  alignment: Alignment.centerRight,
                  child: FilledButton.icon(
                    onPressed: _busy || _rebuilding || persona['step'] != 'READY' ? null : _startRebuild,
                    icon: const Icon(Icons.refresh_rounded, size: 18),
                    label: const Text('다시 만들기'),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

/// 개발자 화면(/admin)에서 기억 인터뷰 신청 목록으로 가는 링크.
class InterviewAdminLink extends StatelessWidget {
  const InterviewAdminLink({super.key});

  @override
  Widget build(BuildContext context) {
    return AppCard(
      padding: const EdgeInsets.fromLTRB(16, 14, 12, 14),
      onTap: () => context.go('/admin/interviews'),
      child: const Row(
        children: [
          Icon(Icons.record_voice_over_outlined, color: AppColors.ink),
          SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('기억 인터뷰 신청', style: AppText.label),
                SizedBox(height: 2),
                Text('신청 목록·연락처·상태, 들은 내용 넣기와 다시 만들기', style: AppText.small),
              ],
            ),
          ),
          Icon(Icons.chevron_right_rounded, color: AppColors.muted),
        ],
      ),
    );
  }
}
