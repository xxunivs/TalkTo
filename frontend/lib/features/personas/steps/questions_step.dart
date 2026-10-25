import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../../core/theme.dart';
import '../../../shared/widgets/ui.dart';
import '../../legal/consent_checks.dart';
import '../../legal/legal_api.dart';
import '../../legal/legal_text.dart';
import '../data/persona_api.dart';
import '../data/persona_rules_api.dart';
import '../owner_actions.dart';
import '../persona_flow_page.dart' show PersonaMenu;
import '../widgets/answers_form.dart';
import '../widgets/bereaved_form.dart';
import '../../../shared/korean.dart';

/// 3단계: 핵심 질문. 문항은 서버가 이 대화 상대에게 정한 그대로 그린다(상세의 questions·sections,
/// 없으면 /intake/meta — [PersonaApi.forPersona]). 2026-10-21 개편 뒤 만든 가족 대화 상대는 '나와 그분'부터
/// 13개 남짓이 필수이고, 예전에 만든 것·나 자신은 예전 필수 그대로다(서버가 정한다).
/// 글 칸마다 🎤 로 말해서 적을 수 있다. 답하는 동안 서버는 뒤에서 녹음을 분석한다.
class QuestionsStep extends StatefulWidget {
  final Map<String, dynamic> detail;
  final VoidCallback onChanged;

  const QuestionsStep({super.key, required this.detail, required this.onChanged});

  @override
  State<QuestionsStep> createState() => _QuestionsStepState();
}

class _QuestionsStepState extends State<QuestionsStep> {
  late final Map<String, dynamic> _answers = Map<String, dynamic>.from(widget.detail['answers'] ?? {});
  final Map<String, GlobalKey> _keys = {};
  List<Map<String, dynamic>> _questions = [];
  Map<String, String> _leads = {};
  List<String> _suggestions = [];
  Map<String, dynamic>? _analysis;
  /// 만들기 전 확인(그분 상태에 맞는 확인 문서 pledgeDoc). 체크하면 voiceConsent 로 보낸다.
  bool _voiceConsent = false;
  late Future<Map<String, dynamic>> _pledge = LegalApi.doc(pledgeDocOf(widget.detail));
  bool _busy = false;

  /// '이전'(목소리 다시 고르기)을 보내는 중(두 번 눌러 409 가 뜨지 않게).
  bool _moving = false;
  String? _error;
  Timer? _saveTimer;
  Timer? _pollTimer;

  /// 저절로 저장하지 못했다('저장하지 못했어요 · 다시 시도'를 보인다).
  bool _saveFailed = false;
  final _form = GlobalKey<AnswersFormState>();

  /// 만들기 전 확인 카드(체크하지 않고 누르면 그리로 내려 보인다).
  final _pledgeKey = GlobalKey();

  /// 돌아가신 분: 신청 자격과 유족 명단(만들기 전에 서버에 먼저 남긴다). 확인 문서와 체크는 [_pledgeCard] 가 한다.
  BereavedDraft? _bereaved;
  final _bereavedKey = GlobalKey();
  Map<String, dynamic> _formMeta = const {};

  /// 돌아가신 분(나 자신 아님)의 대화 상대인가. 확인 문서가 유족 확인이면 그렇다.
  bool get _deceased => pledgeDocOf(widget.detail) == 'pledge_deceased';

  String get _id => widget.detail['id'];

  @override
  void initState() {
    super.initState();
    _analysis = (widget.detail['jobs'] as Map?)?['ANALYZE'];
    PersonaApi.meta().then((all) {
      if (!mounted) return;
      // 이 대화 상대의 질문(필수 묶음은 대화 상대마다 다르다)
      final meta = PersonaApi.forPersona(all, widget.detail);
      setState(() {
        _formMeta = all;
        // 민감한 범주(종교·건강) 질문은 그 범주에 동의했을 때만 묻는다(동의는 '기억 더 채우기'에서)
        final consents = widget.detail['consents'] as Map?;
        _questions = [
          for (final q in meta['questions'] as List)
            if (AnswersForm.consented(q as Map, consents)) Map<String, dynamic>.from(q),
        ];
        _leads = PersonaApi.sectionLeads(meta);
        for (final q in _questions) {
          _keys[q['id']] = GlobalKey();
        }
      });
    }).catchError((Object error) {
      if (mounted) setState(() => _error = error.toString());
    });
    _loadSuggestions();
    if (_analysis?['status'] == 'RUNNING') _pollTimer = Timer(const Duration(seconds: 3), _poll);
    if (_deceased) _loadBereaved();
  }

  /// 앞서 적어 둔 신청 자격·유족 명단이 있으면 채운다(만들다 돌아온 때).
  Future<void> _loadBereaved() async {
    _bereaved = BereavedDraft();
    try {
      final saved = await BereavedApi.load(_id);
      if (!mounted || saved == null) return;
      setState(() {
        _bereaved?.dispose();
        _bereaved = BereavedDraft(saved);
      });
    } catch (_) {
      // 못 읽어도 새로 적으면 된다
    }
  }

  @override
  void didUpdateWidget(covariant QuestionsStep oldWidget) {
    super.didUpdateWidget(oldWidget);
    // 정보 고치기에서 그분 상태나 '나 자신'을 바꾸면 확인 문서도 바뀐다. 앞서 한 체크는 쓰지 않는다
    if (pledgeDocOf(oldWidget.detail) != pledgeDocOf(widget.detail)) {
      _pledge = LegalApi.doc(pledgeDocOf(widget.detail));
      _voiceConsent = false;
      _error = '그분 상태가 바뀌어서 만들기 전 확인 내용도 바뀌었어요. 확인 내용을 다시 읽고 체크해 주세요.';
      if (_deceased && _bereaved == null) _loadBereaved();
    }
  }

  @override
  void dispose() {
    // 적고 바로 다른 화면으로 가도(700ms 안) 마지막으로 적은 답을 저장한다
    _flushSave();
    _pollTimer?.cancel();
    _bereaved?.dispose();
    super.dispose();
  }

  Future<void> _loadSuggestions() async {
    try {
      final phrases = await PersonaApi.phraseSuggestions(_id);
      if (mounted) setState(() => _suggestions = phrases);
    } catch (_) {
      // 후보가 없어도 답할 수 있다
    }
  }

  Future<void> _poll() async {
    try {
      final detail = await PersonaApi.detail(_id);
      if (!mounted) return;
      final was = _analysis?['status'];
      setState(() => _analysis = (detail['jobs'] as Map?)?['ANALYZE']);
      if (was == 'RUNNING' && _analysis?['status'] == 'DONE') _loadSuggestions();
    } catch (_) {
      // 다음 번에 다시
    }
    if (mounted && _analysis?['status'] == 'RUNNING') {
      _pollTimer = Timer(const Duration(seconds: 3), _poll);
    }
  }

  /// 답이 바뀌면 진행 표시를 고치고, 잠시 뒤 저절로 저장한다.
  void _changed() {
    setState(() {});
    _saveTimer?.cancel();
    _saveTimer = Timer(const Duration(milliseconds: 700), _save);
  }

  /// 지금 답을 저장한다. 못 하면 '저장하지 못했어요 · 다시 시도'를 보인다(말없이 버리지 않는다).
  Future<void> _save() async {
    _saveTimer?.cancel();
    try {
      await PersonaApi.saveAnswers(_id, Map<String, dynamic>.from(_answers));
      if (mounted && _saveFailed) setState(() => _saveFailed = false);
    } catch (_) {
      if (mounted) setState(() => _saveFailed = true);
    }
  }

  /// 저장을 기다리는 답이 있으면 바로 보낸다(화면을 떠날 때). 기다리지 않는다.
  void _flushSave() {
    if (_saveTimer?.isActive != true) return;
    _saveTimer!.cancel();
    unawaited(PersonaApi.saveAnswers(_id, Map<String, dynamic>.from(_answers)).catchError((_) {}));
  }

  Future<void> _reanalyze() async {
    final voices = [for (final key in widget.detail['subjectVoices'] as List? ?? const []) key as String];
    try {
      await PersonaApi.chooseVoices(_id, voices);
      setState(() => _analysis = {'status': 'RUNNING', 'detail': '다시 시작하는 중', 'percent': 0});
      _pollTimer = Timer(const Duration(seconds: 3), _poll);
    } catch (error) {
      if (mounted) showMessage(context, error);
    }
  }

  Future<void> _back() async {
    if (_moving) return;
    setState(() => _moving = true);
    // 적어 두고 '더하기'를 누르지 않은 말과, 아직 저장하지 않은 답을 먼저 저장한다(단계를 옮기기 전에)
    _form.currentState?.commitPending();
    if (_saveTimer?.isActive == true || _saveFailed) await _save();
    if (_saveFailed) {
      // 저장하지 못한 채 단계를 옮기면 적은 답이 사라진다. 여기 남아 다시 시도하게 한다
      if (mounted) {
        setState(() => _moving = false);
        showMessage(context, '적은 답을 저장하지 못했어요. 잠시 뒤 다시 해 주세요.');
      }
      return;
    }
    try {
      await PersonaApi.backToSpeakers(_id);
      widget.onChanged();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _moving = false);
    }
  }

  Future<void> _build() async {
    _form.currentState?.commitPending(); // 적어 두고 '더하기'를 누르지 않은 말도 넣는다
    final basics = (widget.detail['basics'] as Map?)?.cast<String, dynamic>();
    if (!AnswersForm.checkRequired(context, _questions, _answers, _keys, basics: basics)) return;
    // 돌아가신 분: 신청 자격과 유족 명단을 먼저(확인 문서 체크는 아래 카드)
    final bereaved = _deceased ? _bereaved : null;
    final problem = bereaved?.check(needPledge: false);
    if (problem != null) {
      setState(() => _error = problem);
      final target = _bereavedKey.currentContext;
      if (target != null) {
        Scrollable.ensureVisible(target, duration: const Duration(milliseconds: 300), alignment: 0.2);
      }
      showMessage(context, problem);
      return;
    }
    if (!_voiceConsent) {
      const message = '만들기 전에 아래 확인 내용을 읽고 체크해 주세요.';
      setState(() => _error = message);
      // 확인 칸은 긴 질문 아래에 있고 버튼은 화면 아래에 붙어 있다. 그리로 내려 보이고 스낵바로도 알린다
      final target = _pledgeKey.currentContext;
      if (target != null) {
        Scrollable.ensureVisible(target, duration: const Duration(milliseconds: 300), alignment: 0.3);
      }
      showMessage(context, message);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    _saveTimer?.cancel();
    try {
      if (bereaved != null) {
        // 유족 확인 문서(아래 카드)에 체크했으니 그 확인과 함께 남긴다. 남기지 못하면 만들지 않는다
        bereaved.pledge = true;
        await BereavedApi.save(_id, bereaved.toJson());
      }
      await PersonaApi.build(_id, _answers, voiceConsent: true);
      widget.onChanged();
    } catch (error) {
      // 안내는 긴 화면 맨 아래에 그려진다. 누른 자리에서도 보이게 스낵바로도 알린다
      if (mounted) {
        setState(() => _error = error.toString());
        showMessage(context, error);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final title = personaTitle(widget.detail);
    final wide = Breakpoints.isWide(context);
    final basics = (widget.detail['basics'] as Map?)?.cast<String, dynamic>();
    // 그분 동의 녹음·법정대리인 확인 전이면 만들 수 없다(서버가 정한다). 유족 확인은 아래에서 함께 적는다
    final gates = PersonaGates.of(widget.detail);
    final held = !gates.build && gates.code != 'bereaved';
    final actions = ownerActionsOf(widget.detail);

    return WizardScaffold(
      title: title,
      step: 3,
      actions: [PersonaMenu(detail: widget.detail)],
      headline: '${josa(title, '은', '는')}\n어떤 분이셨나요?',
      lead: '녹음을 살펴보는 동안 그분 이야기를 들려주세요. \'필수\' 표시가 붙은 질문만 꼭 답하면 되고, '
          '\'선택\' 질문은 아는 것만 적어도 괜찮아요. 적기 어려우면 🎤 단추를 누르고 말씀하세요. 글로 옮겨 드려요. '
          '많이 알려 주실수록 첫 대화부터 그분다워요. 적은 내용은 저절로 저장돼요.',
      secondary: TextButton.icon(
        onPressed: _moving || _busy ? null : _back,
        icon: const Icon(Icons.arrow_back_rounded, size: 18),
        label: Text(wide ? '목소리 다시 고르기' : '이전'),
      ),
      primary: FilledButton(
        onPressed: _busy || held ? null : _build,
        child: Text(_busy ? '잠시만요…' : '대화 상대 만들기'),
      ),
      children: [
        for (final action in actions) ...[
          OwnerActionCard(action: action, consentStatus: subjectConsentOf(widget.detail)?['status'] as String?),
          const SizedBox(height: 12),
        ],
        if (held && actions.isEmpty && (gates.message ?? '').isNotEmpty) ...[
          Notice(gates.message!),
          const SizedBox(height: 12),
        ],
        _analysisStatus(),
        const SizedBox(height: 18),
        if (_questions.isEmpty)
          const Padding(padding: EdgeInsets.all(40), child: Loading())
        else ...[
          _progress(basics),
          const SizedBox(height: 18),
          AnswersForm(
            key: _form,
            questions: _questions,
            answers: _answers,
            keys: _keys,
            suggestions: _suggestions,
            onChanged: _changed,
            basics: basics,
            sectionLeads: _leads,
            personaId: _id,
          ),
        ],
        const SectionLabel('그분이 남긴 글 (선택)'),
        AppCard(
          padding: const EdgeInsets.fromLTRB(20, 16, 14, 16),
          onTap: () => context.push('/p/$_id/materials'),
          child: Row(
            children: [
              const Icon(Icons.note_add_outlined, color: AppColors.primary),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('카카오톡 대화·문자·일기도 있나요?', style: AppText.heading),
                    const SizedBox(height: 4),
                    Text(
                      keepAll('그분이 남긴 글이 있으면 더할 수 있어요. 글을 옮겨 오는 방법을 차례로 알려 드려요. 꼭 하지 않아도 괜찮아요.'),
                      style: AppText.small,
                    ),
                  ],
                ),
              ),
              const Icon(Icons.chevron_right_rounded, color: AppColors.muted),
            ],
          ),
        ),
        const SizedBox(height: 22),
        const SectionLabel('만들기 전 확인'),
        if (_deceased && _bereaved != null) ...[
          KeyedSubtree(
            key: _bereavedKey,
            child: BereavedForm(
              draft: _bereaved!,
              meta: _formMeta,
              title: title,
              showPledge: false,
              onChanged: () {
                if (_error != null) setState(() => _error = null);
              },
            ),
          ),
          const SizedBox(height: 16),
        ],
        _pledgeCard(title),
        if (_error != null) ...[const SizedBox(height: 16), ErrorBox(_error!)],
      ],
    );
  }

  /// 그분 상태(돌아가심·살아 계심·나 자신)에 맞는 확인 문서를 그대로 보여 주고 한 번 체크받는다.
  Widget _pledgeCard(String title) {
    return FutureBuilder<Map<String, dynamic>>(
      future: _pledge,
      builder: (context, snap) {
        final doc = snap.data;
        return AppCard(
          key: _pledgeKey,
          padding: const EdgeInsets.fromLTRB(20, 18, 16, 10),
          borderColor: _voiceConsent ? AppColors.primary : null,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (snap.hasError) ...[
                const ErrorBox('확인 내용을 불러오지 못했어요.'),
                TextButton.icon(
                  onPressed: () => setState(() {
                    _pledge = LegalApi.doc(pledgeDocOf(widget.detail));
                  }),
                  icon: const Icon(Icons.refresh_rounded, size: 18),
                  label: const Text('다시 불러오기'),
                ),
              ] else if (doc == null)
                const Padding(padding: EdgeInsets.all(20), child: Loading())
              else ...[
                Text(doc['title'] as String? ?? '', style: AppText.heading),
                const SizedBox(height: 12),
                LegalText(fillCallName(doc['body'] as String? ?? '', title)),
              ],
              const Divider(height: 18),
              ConsentCheck(
                label: '위 내용을 모두 확인했어요',
                value: _voiceConsent,
                enabled: doc != null,
                onChanged: (v) => setState(() {
                  _voiceConsent = v;
                  if (v) _error = null;
                }),
              ),
            ],
          ),
        );
      },
    );
  }

  /// "필수 2 / 4 · 선택 5 / 20" (이 대화 상대에게 묻는 질문만 센다). 필수를 다 답하면 그렇다고 알린다.
  Widget _progress(Map<String, dynamic>? basics) {
    final p = AnswersForm.progress(_questions, _answers, basics);
    final allRequired = p.requiredDone >= p.required;
    final optional = '선택 ${p.done - p.requiredDone} / ${p.total - p.required}';
    final row = Padding(
      padding: const EdgeInsets.only(left: 4),
      child: Row(
        children: [
          Icon(
            allRequired ? Icons.check_circle_rounded : Icons.error_outline_rounded,
            size: 18,
            color: allRequired ? AppColors.primary : AppColors.clay,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              allRequired
                  ? '필수 질문은 다 답했어요 · $optional'
                  : '필수 ${p.requiredDone} / ${p.required} · $optional',
              style: AppText.small.copyWith(color: AppColors.ink2, fontWeight: FontWeight.w700),
            ),
          ),
        ],
      ),
    );
    if (!_saveFailed) return row;
    // '적은 내용은 저절로 저장돼요'라고 했으니, 못 했으면 말없이 넘어가지 않는다
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        row,
        const SizedBox(height: 8),
        Notice(
          '적은 답을 저장하지 못했어요.',
          icon: Icons.cloud_off_rounded,
          color: AppColors.danger,
          background: AppColors.dangerSoft,
          trailing: TextButton(onPressed: _save, child: const Text('다시 시도')),
        ),
      ],
    );
  }

  Widget _analysisStatus() {
    final job = _analysis;
    if (job == null) return const SizedBox.shrink();
    if (job['status'] == 'FAILED') {
      return Notice(
        '녹음을 살펴보다가 멈췄어요. ${job['error'] ?? ''}',
        icon: Icons.error_outline_rounded,
        color: AppColors.danger,
        background: AppColors.dangerSoft,
        trailing: TextButton(onPressed: _reanalyze, child: const Text('다시 살펴보기')),
      );
    }
    final running = job['status'] == 'RUNNING';
    final notice = Notice(
      running
          ? '녹음을 살펴보고 있어요 · ${job['detail'] ?? ''}'
          : '녹음을 다 살펴봤어요. 녹음에서 찾은 그분 말버릇을 아래에 모아 두었어요.',
      icon: running ? Icons.hourglass_top_rounded : Icons.check_circle_outline_rounded,
      color: AppColors.primary,
      background: AppColors.primarySoft,
    );
    if (!running || job['percent'] == null) return notice;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        notice,
        const SizedBox(height: 10),
        Center(child: PercentBar(percent: job['percent'] as num)),
      ],
    );
  }
}

/// 만들기·목소리 다시 만들기 전에 보여 줄 확인 문서. 서버가 상세에 pledgeDoc 으로 준다
/// (없으면 기본 정보의 상태로 고른다). 예전 '대화가 어려우신 분'(unable)의 확인 문서는 더 받지 않아
/// (pledge_unable 은 내렸다) 살아 계신 분 것으로 본다 — 그 대화 상대는 녹음 처리·목소리 만들기가 멈춰 있다.
String pledgeDocOf(Map<String, dynamic> detail) {
  final doc = detail['pledgeDoc'] as String?;
  if (doc == 'pledge_unable') return 'pledge_living';
  if (doc != null && doc.isNotEmpty) return doc;
  final status = (detail['basics'] as Map?)?['status'] ?? (detail['lifeStatus'] == 'LIVING' ? 'alive' : 'deceased');
  return switch (status) {
    'alive' || 'unable' => 'pledge_living',
    _ => 'pledge_deceased',
  };
}
