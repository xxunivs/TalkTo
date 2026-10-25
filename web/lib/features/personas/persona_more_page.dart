import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/ui.dart';
import '../gaps/gap_api.dart' show gapCountOf, gapWaitingOf;
import '../gaps/gap_widgets.dart' show GapEntryCard;
import '../interview/interview_api.dart' show canAskInterview;
import '../interview/interview_request_page.dart' show InterviewEntryCard;
import '../legal/legal_text.dart';
import '../share/roles.dart';
import 'data/persona_api.dart';
import 'data/persona_store.dart';
import 'imported.dart';
import 'persona_edit_page.dart' show PersonaEditPage;
import 'widgets/answers_form.dart';

/// 기억 더 채우기: 다 만든 페르소나를 Persona Intake 의 나머지 질문(모두 선택)으로 더 그분답게 다듬는다.
/// 묶음(기본 프로필·말투·인생 이야기…)을 골라 답하고 저장하면, 서버가 지침과 기억을 다시 만든다.
class PersonaMorePage extends StatefulWidget {
  final String personaId;

  const PersonaMorePage({super.key, required this.personaId});

  @override
  State<PersonaMorePage> createState() => _PersonaMorePageState();
}

class _PersonaMorePageState extends State<PersonaMorePage> {
  Map<String, dynamic>? _detail;
  List<Map<String, dynamic>> _modules = [];
  List<Map<String, dynamic>> _questions = [];
  Map<String, dynamic> _answers = {};

  /// 녹음에서 찾은 말 후보(서버가 suggest 를 붙인 문항, 예: M3-07 말끝에 누르면 더해지는 칩).
  List<String> _suggestions = [];
  final Map<String, GlobalKey> _keys = {};
  final _form = GlobalKey<AnswersFormState>();
  String? _module;
  bool _dirty = false;
  bool _busy = false;
  Object? _error;
  Timer? _jobTimer;

  /// 이번에 '건너뛰기'한 민감한 범주(religion·health). 화면을 다시 열면 다시 묻는다.
  final Set<String> _skipped = {};

  /// 가져온 대화 상대에서 이 화면이 방금 고른 범주 상태('on'|'off'). 서버가 sensitiveMemories 를
  /// 주지 않을 때만 쓴다. [importedSensitiveState]
  final Map<String, String> _importedKnown = {};

  String get _id => widget.personaId;

  /// 민감한 범주별 동의 종류·상세의 consents 이름·화면 이름.
  static const _sensitive = sensitiveCategories;

  bool _consented(String category) => (_detail?['consents'] as Map?)?[_sensitive[category]!.key] == true;

  /// 이 질문이 민감한 범주이고 아직 동의하지 않았으면 그 범주(religion·health), 아니면 null.
  String? _blocked(Map q) {
    final category = q['sensitive'] as String?;
    if (!_sensitive.containsKey(category)) return null;
    return _consented(category!) ? null : category;
  }

  /// 화면에 보이는(답할 수 있는) 질문만. 동의하지 않은 범주 질문은 빼고 센다.
  Iterable<Map<String, dynamic>> get _visible => _questions.where((q) => _blocked(q) == null);

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _jobTimer?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([PersonaApi.detail(_id), PersonaApi.meta()]);
      final detail = results[0];
      // 이 대화 상대의 나머지 질문(만들 때 묻는 질문이 대화 상대마다 달라 나머지도 다르다)
      final meta = PersonaApi.forPersona(results[1], detail);
      if (!mounted) return;
      // 돌아가신 분에게만 묻는 질문(when=deceased)
      final deceased = detail['lifeStatus'] != 'LIVING';
      bool shown(Map q) => q['when'] != 'deceased' || deceased;
      setState(() {
        _detail = detail;
        _questions = [
          for (final q in meta['moreQuestions'] as List? ?? const [])
            if (shown(q)) Map<String, dynamic>.from(q),
        ];
        // 문항이 하나도 없는 주제(질문을 만들 때로 옮겨 빈 묶음)는 0 / 0 카드로 남기지 않는다
        _modules = [
          for (final m in meta['moreModules'] as List? ?? const [])
            if (shown(m) && _questions.any((q) => q['section'] == m['id'])) Map<String, dynamic>.from(m),
        ];
        for (final q in _questions) {
          _keys[q['id']] = GlobalKey();
        }
        _answers = Map<String, dynamic>.from(detail['answers'] ?? {});
        _error = null;
      });
      _watchJobs();
      // 정보 고치기처럼 다 만든 대화 상대의 만든 분에게만(가족·가져온 대화 상대는 질문을 보지 않는다).
      // 다시 읽을 때(저장한 뒤 등)는 앞서 받은 후보를 그대로 쓴다
      if (_suggestions.isEmpty && detail['step'] == 'READY' && !isMember(detail) && !isImportedPersona(detail)) {
        final phrases = await PersonaApi.phraseSuggestions(_id).catchError((_) => <String>[]);
        if (mounted) setState(() => _suggestions = phrases);
      }
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  List<Map<String, dynamic>> _of(String module) => [for (final q in _visible) if (q['section'] == module) q];

  /// 이 주제에 있는 민감한 범주들(동의 여부와 상관없이).
  List<String> _categoriesOf(String module) => [
        for (final c in _sensitive.keys)
          if (_questions.any((q) => q['section'] == module && q['sensitive'] == c)) c,
      ];

  int _answered(String module) => _of(module).where((q) => _has(_answers[q['id']])).length;

  static bool _has(dynamic v) => v != null && (v is! String || v.trim().isNotEmpty) && (v is! List || v.isNotEmpty);

  Map? _job(String kind) => (_detail?['jobs'] as Map?)?[kind] as Map?;
  /// 지침을 다시 만드는 일(UPDATE·EXTEND)의 진행률 글(' · 40%'). 모르면 빈칸.
  String get _percentText {
    for (final kind in const ['UPDATE', 'EXTEND']) {
      final job = _job(kind);
      if (job?['status'] == 'RUNNING' && job?['percent'] != null) return ' · ${job!['percent']}%';
    }
    return '';
  }

  /// 앞서 저장한 답이나 더한 녹음을 반영하는 중(서버가 그동안 저장을 받지 않는다)
  bool get _updating => _job('UPDATE')?['status'] == 'RUNNING' || _job('EXTEND')?['status'] == 'RUNNING';

  bool get _updateFailed => _job('UPDATE')?['status'] == 'FAILED';

  /// 반영하는 동안 몇 초마다 작업 상태만 다시 읽는다(적던 답은 건드리지 않는다).
  void _watchJobs() {
    _jobTimer?.cancel();
    if (!_updating) return;
    _jobTimer = Timer(const Duration(seconds: 4), () async {
      try {
        final fresh = await PersonaApi.detail(_id);
        if (!mounted || _detail == null) return;
        setState(() => _detail = {..._detail!, 'jobs': fresh['jobs'], 'step': fresh['step']});
      } catch (_) {
        // 잠깐 못 읽어도 다음 차례에 다시 읽는다
      }
      if (mounted) _watchJobs();
    });
  }

  Future<void> _save() async {
    _form.currentState?.commitPending(); // 적어 두고 '더하기'를 누르지 않은 말도 넣는다
    setState(() => _busy = true);
    try {
      // 이 화면의 문항만 바꾼다(정보 고치기에서 저장한 처음 질문 답은 서버 값을 둔다).
      // 동의하지 않은 범주(종교·건강)의 답은 보내지 않는다(서버도 받지 않는다).
      final hidden = {for (final q in _questions) if (_blocked(q) != null) q['id'] as String};
      final saved = await PersonaApi.updateAnswers(
        _id,
        answers: {
          for (final e in _answers.entries)
            if (!hidden.contains(e.key.split(':').first)) e.key: e.value,
        },
        ids: [for (final q in _questions) q['id'] as String],
      );
      personaStore.load();
      notifyPersonaEdited(_id);
      if (!mounted) return;
      showMessage(
        context,
        saved['updating'] == true ? '저장했어요. 1~2분 동안 새 답을 익혀요. 그동안에도 대화할 수 있어요.' : '저장했어요.',
      );
      setState(() {
        _dirty = false;
        _module = null;
      });
      await _load();
    } catch (error) {
      // 적던 답은 그대로 둔다(잠시 뒤 다시 저장할 수 있게)
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 더 알려 주실 기억에서 돌아오면 그 수(gapCount·gapWaiting)만 다시 읽는다(적던 답은 건드리지 않는다).
  Future<void> _refreshGapCount() async {
    try {
      final fresh = await PersonaApi.detail(_id);
      if (mounted && _detail != null) {
        setState(() => _detail = {..._detail!, 'gapCount': fresh['gapCount'], 'gapWaiting': fresh['gapWaiting']});
      }
    } catch (_) {
      // 못 읽으면 앞의 숫자를 둔다
    }
  }

  /// 반영하다 실패한 답을 다시 반영한다(답은 이미 저장돼 있다).
  Future<void> _retry() async {
    setState(() => _busy = true);
    try {
      await PersonaApi.updateAnswers(_id, answers: const {}, ids: const []);
      notifyPersonaEdited(_id);
      await _load();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 민감한 범주에 동의하고 그 질문을 보여 준다. 적던 다른 답은 그대로 둔다.
  Future<void> _consent(String category) async {
    setState(() => _busy = true);
    try {
      final result = await PersonaApi.setPersonaConsent(_id, _sensitive[category]!.type, true);
      final consents = result['consents'] ?? (await PersonaApi.detail(_id))['consents'];
      if (!mounted) return;
      setState(() {
        _detail = {..._detail!, 'consents': consents};
        _skipped.remove(category);
      });
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 동의를 거둔다. 서버가 그 범주 답을 지우고 다시 익힌다.
  Future<void> _withdraw(String category) async {
    final label = _sensitive[category]!.label;
    final ok = await confirmAction(
      context,
      '$label 이야기 동의를 거둘까요?',
      '$label 질문에 적은 답을 지우고, 대화 상대가 그 답 없이 다시 익혀요. 지운 답은 되돌릴 수 없어요.',
      action: '동의 거두기',
    );
    if (!ok || !mounted) return;
    setState(() => _busy = true);
    try {
      final result = await PersonaApi.setPersonaConsent(_id, _sensitive[category]!.type, false);
      final fresh = await PersonaApi.detail(_id);
      if (!mounted) return;
      final ids = {for (final q in _questions) if (q['sensitive'] == category) q['id'] as String};
      setState(() {
        // 적던 다른 답은 두고, 이 범주 답만 뺀다
        _answers.removeWhere((key, _) => ids.contains(key.split(':').first));
        _detail = {
          ..._detail!,
          'consents': fresh['consents'] ?? result['consents'],
          'jobs': fresh['jobs'],
          'step': fresh['step'],
        };
      });
      personaStore.load();
      notifyPersonaEdited(_id);
      // 지운 뒤 안내는 서버 글 그대로(서버 백업에 남는 날까지 적혀 있다). 다시 익히면 그 말을 앞에 붙인다
      final deletion = result['deletion'];
      final told = deletion is Map ? '${deletion['message'] ?? ''}'.trim() : '';
      final text = told.isEmpty
          ? (result['updating'] == true ? '동의를 거뒀어요. 적은 답을 지우고 1~2분 동안 다시 익혀요.' : '동의를 거두고 적은 답을 지웠어요.')
          : (result['updating'] == true ? '1~2분 동안 다시 익혀요. $told' : told);
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text(text), duration: Duration(seconds: told.isEmpty ? 4 : 10)));
      _watchJobs();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 가져온 대화 상대: 종교·건강 이야기 동의를 하거나 거둔다. 답이 없어 다시 익히지 않고, 서버가 그 분류의
  /// 가져온 기억을 켜거나 끈다(지우지 않는다).
  Future<void> _chooseImported(String category, bool agreed) async {
    final label = _sensitive[category]!.label;
    if (!agreed) {
      final ok = await confirmAction(
        context,
        '$label 이야기를 대화에 쓰지 않을까요?',
        '예전에 모아 둔 이야기 중 $label에 관한 기억을 대화에 쓰지 않아요. 지우지는 않아서, 다시 동의하면 그대로 돌아와요.',
        action: '쓰지 않기',
        danger: false,
      );
      if (!ok || !mounted) return;
    }
    setState(() => _busy = true);
    try {
      final result = await PersonaApi.setPersonaConsent(_id, _sensitive[category]!.type, agreed);
      if (!mounted) return;
      setState(() {
        _importedKnown[category] = agreed ? 'on' : 'off';
        _detail = {..._detail!, if (result['consents'] != null) 'consents': result['consents']};
      });
      showMessage(
        context,
        agreed ? '동의했어요. $label 이야기도 대화에 써요.' : '동의를 거뒀어요. $label 이야기는 대화에 쓰지 않아요.',
      );
      // 켜짐·꺼짐(sensitiveMemories)은 상세에만 있어 다시 읽는다
      final fresh = await PersonaApi.detail(_id);
      if (!mounted) return;
      setState(() => _detail = {
            ..._detail!,
            'consents': fresh['consents'] ?? _detail!['consents'],
            'sensitiveMemories': fresh['sensitiveMemories'],
          });
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 가져온 대화 상대의 이 화면: 질문 칸 없이 종교·건강 이야기 동의 단추만(plan 단계 8.3).
  Widget _importedConsents(Map<String, dynamic> detail, String title) {
    return WizardScaffold(
      title: '종교·건강 이야기 동의',
      headline: '$title의 종교·건강 이야기',
      lead: '예전에 모아 둔 이야기에는 종교·건강에 관한 기억도 있어요. 처음에는 대화에 쓰도록 켜 두었어요. '
          '동의를 거두면 대화에서 그 기억을 쓰지 않아요.',
      secondary: TextButton(
        onPressed: () => context.canPop() ? context.pop() : context.go('/p/$_id'),
        child: const Text('돌아가기'),
      ),
      children: [
        ImportedSensitivePanel(
          detail: detail,
          busy: _busy,
          known: _importedKnown,
          onChoose: _chooseImported,
          onDetails: (category) => showLegalDoc(context, _sensitive[category]!.type, callName: title),
        ),
      ],
    );
  }

  /// 동의하지 않은 민감한 범주 질문 자리에 보이는 카드.
  Widget _consentCard(String category, String title) {
    final label = _sensitive[category]!.label;
    final docKey = _sensitive[category]!.type;
    if (_skipped.contains(category)) {
      return Align(
        alignment: Alignment.centerLeft,
        child: TextButton.icon(
          onPressed: () => setState(() => _skipped.remove(category)),
          icon: const Icon(Icons.add_rounded, size: 18),
          label: Text('건너뛴 $label 질문 다시 보기'),
        ),
      );
    }
    final lead = category == 'religion'
        ? '종교·신앙은 민감한 정보라서 동의하신 경우에만 여쭤봐요. 종교가 있으셨는지, 기도하거나 의지하던 습관 같은 것을 물어요. '
            '살아 계신 분이면 그분 본인께 직접 여쭤보고, 괜찮다고 하셨을 때만 동의해 주세요.'
        : '건강에 관한 이야기는 민감한 정보라서 동의하신 경우에만 여쭤봐요. 어떻게 돌아가셨는지 큰 갈래만 묻고, 병명이나 진단은 묻지 않아요.';
    return AppCard(
      padding: const EdgeInsets.fromLTRB(20, 18, 16, 12),
      color: AppColors.primarySoft,
      borderColor: Colors.transparent,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.lock_outline_rounded, size: 20, color: AppColors.primary),
              const SizedBox(width: 8),
              Expanded(child: Text('$label 이야기 (선택)', style: AppText.heading)),
            ],
          ),
          const SizedBox(height: 8),
          Text(keepAll(lead), style: AppText.small.copyWith(color: AppColors.ink2, fontSize: 14, height: 1.55)),
          const SizedBox(height: 4),
          Text(
            keepAll('동의하지 않아도 대화 상대를 쓰는 데 아무 문제가 없어요. 동의는 언제든 거둘 수 있어요.'),
            style: AppText.small,
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              FilledButton(
                onPressed: _busy ? null : () => _consent(category),
                style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                child: const Text('동의하고 답하기'),
              ),
              TextButton(
                onPressed: _busy ? null : () => setState(() => _skipped.add(category)),
                child: const Text('건너뛰기'),
              ),
              TextButton(
                onPressed: () => showLegalDoc(context, docKey, callName: title),
                child: const Text('자세히 보기'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Future<void> _leaveModule() async {
    if (_dirty) {
      final drop = await confirmAction(
        context,
        '저장하지 않고 나갈까요?',
        '적은 답은 저장하지 않으면 사라져요.',
        action: '나가기',
      );
      if (!drop) return;
      await _load(); // 저장한 답으로 되돌린다
      if (!mounted) return;
      setState(() => _dirty = false);
    }
    setState(() => _module = null);
  }

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    if (detail == null) {
      return Scaffold(
        appBar: AppBar(),
        body: _error != null
            ? LoadFailed(_error!, onRetry: () {
                setState(() => _error = null);
                _load();
              })
            : const Loading(),
      );
    }
    final title = personaTitle(detail);
    // 함께하는 가족은 질문에 답하거나 종교·건강 동의를 하지 않는다(만든 사람만)
    final blocked = PersonaBlocked.of(detail, personaId: _id, imported: false);
    if (blocked != null) return blocked;
    // 가져온 대화 상대는 답을 더하면 예전 지침을 덮어써서 질문은 숨기고(서버도 막는다),
    // 종교·건강 이야기 동의 단추만 둔다(거두면 그 분류의 가져온 기억을 끈다)
    if (isImportedPersona(detail)) return _importedConsents(detail, title);
    final module = _module;
    if (module != null) {
      final lead = _modules.firstWhere((m) => m['id'] == module, orElse: () => const {})['lead'] as String?;
      // 위 뒤로 가기·휴대폰 뒤로 가기도 '다른 주제'처럼 주제(묶음) 목록으로 돌아간다(적던 답을 묻고)
      return PopScope(
        canPop: false,
        onPopInvokedWithResult: (didPop, _) {
          if (!didPop && !_busy) _leaveModule();
        },
        child: WizardScaffold(
          title: '기억 더 채우기',
          headline: module,
          lead: '${lead ?? ''}${lead == null ? '' : '. '}꼭 다 답하지 않아도 돼요. 아는 것만 답해 주세요.',
          secondary: TextButton.icon(
            onPressed: _busy ? null : _leaveModule,
            icon: const Icon(Icons.arrow_back_rounded, size: 18),
            label: const Text('다른 주제'),
          ),
          primary: FilledButton(
            onPressed: _busy || !_dirty || _updating ? null : _save,
            child: Text(_busy ? '저장하는 중…' : (_updating ? '잠시 뒤 저장할 수 있어요' : '저장하기')),
          ),
          children: [
            if (_updating) ...[
              Notice('앞서 저장한 내용을 익히는 중이에요$_percentText. 끝나면 저장할 수 있어요. 적던 답은 그대로 있어요.',
                  icon: Icons.hourglass_top_rounded),
              const SizedBox(height: 16),
            ],
            if (_of(module).isNotEmpty)
              AnswersForm(
                key: _form,
                questions: _of(module),
                answers: _answers,
                keys: _keys,
                suggestions: _suggestions,
                onChanged: () {
                  if (!_dirty) setState(() => _dirty = true);
                },
                personaId: _id,
              ),
            for (final c in _categoriesOf(module))
              if (!_consented(c))
                Padding(padding: const EdgeInsets.only(top: 16), child: _consentCard(c, title))
              else
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton(
                    onPressed: _busy ? null : () => _withdraw(c),
                    style: TextButton.styleFrom(foregroundColor: AppColors.muted),
                    child: Text('${_sensitive[c]!.label} 이야기 동의 거두기'),
                  ),
                ),
          ],
        ),
      );
    }
    final total = _visible.length;
    final done = _visible.where((q) => _has(_answers[q['id']])).length;
    return WizardScaffold(
      title: '기억 더 채우기',
      headline: '$title에 대해 더 알려 주세요',
      lead: '처음에 답한 질문 말고도 더 알려 주시면, 그만큼 더 그분답게 대답해요. 꼭 다 답하지 않아도 돼요.',
      secondary: TextButton(
        onPressed: () => context.canPop() ? context.pop() : context.go('/p/$_id'),
        child: const Text('돌아가기'),
      ),
      children: [
        if (detail['step'] != 'READY')
          const Notice('아직 만드는 중이에요. 다 만든 뒤에 질문에 더 답할 수 있어요.')
        else ...[
          // 가족 표는 핵심 질문(정보 고치기)에 있다. 아직 비었으면 먼저 권하고, 누르면 가족 표만 연다
          // (가족 알려 주기 — 저장·취소하면 여기로 돌아온다)
          if (_answers['M2-01'] is! List || (_answers['M2-01'] as List).isEmpty) ...[
            AppCard(
              padding: const EdgeInsets.fromLTRB(18, 16, 14, 16),
              color: AppColors.primarySoft,
              onTap: () => context.go(PersonaEditPage.familyPath(_id)),
              child: Row(
                children: [
                  const Icon(Icons.family_restroom_rounded, color: AppColors.primary),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Text('가족을 알려 주세요', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5)),
                        const SizedBox(height: 2),
                        Text(
                          keepAll("대화하다가 '아빠'·'고모'라고 해도 그분이 누구인지 알아들어요."),
                          style: AppText.small,
                        ),
                      ],
                    ),
                  ),
                  const Icon(Icons.chevron_right_rounded, color: AppColors.primary),
                ],
              ),
            ),
            const SizedBox(height: 16),
          ],
          if (_updating) ...[
            Notice('새로 알려 주신 내용을 익히는 중이에요$_percentText. 그동안에도 대화할 수 있어요.',
                icon: Icons.hourglass_top_rounded),
            const SizedBox(height: 16),
          ] else if (_updateFailed) ...[
            ErrorBox("앞서 저장한 답을 익히지 못했어요. 답은 잘 저장돼 있으니 '다시 익히기'를 눌러 주세요."
                "${_job('UPDATE')?['error'] is String ? ' (${_job('UPDATE')!['error']})' : ''}"),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                onPressed: _busy ? null : _retry,
                icon: const Icon(Icons.refresh_rounded, size: 18),
                label: const Text('다시 익히기'),
              ),
            ),
            const SizedBox(height: 8),
          ],
          // 대화하다 아직 모르던 이야기(더 알려 주실 기억). 다녀오면 숫자만 다시 읽는다
          GapEntryCard(
            personaId: _id,
            count: gapCountOf(detail),
            waiting: gapWaitingOf(detail),
            onReturn: _refreshGapCount,
          ),
          const SizedBox(height: 16),
          SectionLabel('답한 질문 $done / $total'),
          for (final m in _modules)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: AppCard(
                padding: const EdgeInsets.fromLTRB(18, 16, 12, 16),
                onTap: () => setState(() => _module = m['id'] as String),
                child: Row(
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(m['id'] as String, style: AppText.heading),
                          const SizedBox(height: 3),
                          Text(keepAll(m['lead'] as String? ?? ''), style: AppText.small),
                        ],
                      ),
                    ),
                    const SizedBox(width: 10),
                    Text(
                      '${_answered(m['id'] as String)} / ${_of(m['id'] as String).length}',
                      style: AppText.small.copyWith(fontWeight: FontWeight.w700),
                    ),
                    const Icon(Icons.chevron_right_rounded, color: AppColors.muted),
                  ],
                ),
              ),
            ),
          const SizedBox(height: 8),
          const Notice(
            '짐작한 것보다 직접 보고 들은 것만 적어 주세요. 모르는 질문은 건너뛰어도 괜찮아요.',
            icon: Icons.lightbulb_outline_rounded,
          ),
          // 적기 어려우면 말로: 대표가 만든 분께 직접 여쭤본다(나 자신 대화 상대는 빼고)
          if (canAskInterview(detail)) ...[
            const SizedBox(height: 16),
            InterviewEntryCard(personaId: _id),
          ],
        ],
      ],
    );
  }
}
