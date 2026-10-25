import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/ui.dart';
import '../legal/consent_checks.dart';
import '../personas/data/persona_api.dart';
import '../personas/imported.dart';
import 'interview_api.dart';

/// 기억 인터뷰 받기(/p/:id/interview): 만든 분이 대표에게 인터뷰를 신청한다(초기 가족 무료).
///
/// 인터뷰 받는 분은 만든 분이다. 이름·관계는 만들 때 적은 '내 이름'·'그분과의 관계'로 미리 채우고 고칠 수
/// 있다. 연락처·바라는 요일과 시간·전화/영상을 받고, 동의 둘(인터뷰 내용을 넣는 데 동의 — 필수, 통화
/// 녹음 — 선택)을 받는다. 미리 체크하지 않는다. 함께 쓰는 가족은 이 화면을 쓰지 않는다.
class InterviewRequestPage extends StatefulWidget {
  final String personaId;
  final InterviewApi api;

  const InterviewRequestPage({super.key, required this.personaId, this.api = const InterviewApi()});

  @override
  State<InterviewRequestPage> createState() => _InterviewRequestPageState();
}

class _InterviewRequestPageState extends State<InterviewRequestPage> {
  Map<String, dynamic>? _detail;
  Map<String, dynamic>? _state;
  Object? _error;
  bool _busy = false;

  final _name = TextEditingController();
  final _relation = TextEditingController();
  final _phone = TextEditingController();
  final _time = TextEditingController();
  final Set<String> _days = {};
  String _method = 'phone';
  bool _agreeUse = false;
  bool _agreeRecording = false;

  /// 새로 신청하는 중(끝난 신청이 있어도 다시 신청할 수 있다)
  bool _again = false;

  static const _timeChips = ['오전', '점심 무렵', '오후', '저녁'];

  String get _id => widget.personaId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _name.dispose();
    _relation.dispose();
    _phone.dispose();
    _time.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final detail = await PersonaApi.detail(_id);
      if (!mounted) return;
      // 함께하는 가족은 상세만 읽고 막는다(신청은 만든 분만)
      if (!canAskInterview(detail) && PersonaBlocked.of(detail, personaId: _id, imported: false) != null) {
        setState(() => _detail = detail);
        return;
      }
      final state = await widget.api.state(_id);
      if (!mounted) return;
      final prefill = Map<String, dynamic>.from(state['prefill'] as Map? ?? const {});
      setState(() {
        _detail = detail;
        _state = state;
        _error = null;
        if (_name.text.isEmpty) _name.text = '${prefill['name'] ?? ''}';
        if (_relation.text.isEmpty) _relation.text = '${prefill['relation'] ?? ''}';
      });
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  Map<String, dynamic>? get _request {
    final request = _state?['request'];
    return request is Map ? Map<String, dynamic>.from(request) : null;
  }

  bool get _open => const {'requested', 'scheduled'}.contains(_request?['status']);

  bool get _ready =>
      _agreeUse &&
      _name.text.trim().isNotEmpty &&
      _relation.text.trim().isNotEmpty &&
      _phone.text.replaceAll(RegExp(r'\D'), '').length >= 9 &&
      _days.isNotEmpty &&
      _time.text.trim().isNotEmpty;

  Future<void> _submit() async {
    setState(() => _busy = true);
    try {
      final days = (_state?['days'] as List? ?? const []).map((d) => '$d').where(_days.contains).toList();
      final state = await widget.api.apply(_id, {
        'name': _name.text.trim(),
        'relation': _relation.text.trim(),
        'phone': _phone.text.trim(),
        'days': days.isEmpty ? _days.toList() : days,
        'timeText': _time.text.trim(),
        'method': _method,
        'agreeUse': _agreeUse,
        'agreeRecording': _agreeRecording,
      });
      if (!mounted) return;
      setState(() {
        _state = state;
        _again = false;
      });
      showMessage(context, '신청했어요. 대표가 적어 주신 시간에 연락드릴게요.');
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _cancel() async {
    final ok = await confirmAction(
      context,
      '신청을 취소할까요?',
      '취소해도 나중에 다시 신청할 수 있어요.',
      action: '신청 취소하기',
    );
    if (!ok || !mounted) return;
    setState(() => _busy = true);
    try {
      final state = await widget.api.cancel(_id);
      if (!mounted) return;
      setState(() => _state = state);
      showMessage(context, '신청을 취소했어요.');
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _back() => context.canPop() ? context.pop() : context.go('/p/$_id');

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
    final blocked = PersonaBlocked.of(detail, personaId: _id, imported: false);
    if (blocked != null) return blocked;
    final title = personaTitle(detail);
    final reason = _state?['reason'] as String?;
    final request = _request;
    // 신청이 없거나, 끝난 신청 뒤에 '다시 신청하기'를 눌렀을 때만 신청서를 보인다
    final showForm = reason == null && (request == null || (!_open && _again));
    // 돌아가신 분은 지난 말로, 살아 계신 분(대화할 수 있음·어려움)은 지금 말로(기본 정보가 없으면 lifeStatus)
    final status = (detail['basics'] as Map?)?['status'];
    final deceased = status != null ? status == 'deceased' : detail['lifeStatus'] != 'LIVING';
    return WizardScaffold(
      title: interviewTitle,
      headline: interviewTitle,
      lead: '$interviewSubtitle. 만드신 분께 ${josa(title, '이', '가')} '
          '${deceased ? '어떤 분이셨는지' : '어떤 분이신지'} 전화나 영상 통화로 여쭤보고, '
          '들은 이야기를 대화 상대의 기억과 말투에 넣어 드려요.',
      secondary: TextButton(onPressed: _back, child: const Text('돌아가기')),
      primary: showForm
          ? FilledButton(
              onPressed: _busy || !_ready ? null : _submit,
              child: Text(_busy ? '신청하는 중…' : '신청하기'),
            )
          : null,
      children: [
        if (reason != null)
          Notice(reason)
        else if (request != null && !showForm)
          _requestCard(request)
        else
          ..._form(title),
      ],
    );
  }

  Widget _requestCard(Map<String, dynamic> request) {
    final status = '${request['status']}';
    final days = (request['days'] as List? ?? const []).join('·');
    final method = interviewMethodLabels[request['method']] ?? '';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AppCard(
          color: AppColors.primarySoft,
          borderColor: Colors.transparent,
          padding: const EdgeInsets.fromLTRB(20, 18, 18, 18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Icon(Icons.record_voice_over_outlined, color: AppColors.primary),
                  const SizedBox(width: 10),
                  Expanded(child: Text(interviewStatusLabels[status] ?? status, style: AppText.heading)),
                ],
              ),
              const SizedBox(height: 10),
              if (_open)
                Text(
                  keepAll('대표가 적어 주신 시간에 연락드릴게요. 모르는 번호로 전화가 와도 받아 주세요.'),
                  style: AppText.small.copyWith(color: AppColors.ink2, fontSize: 14, height: 1.55),
                ),
              if (status == 'done')
                Text(
                  keepAll('들려주신 이야기를 대화 상대에 넣고 있어요. 더 들려주실 이야기가 있으면 다시 신청해 주세요.'),
                  style: AppText.small.copyWith(color: AppColors.ink2, fontSize: 14, height: 1.55),
                ),
              if (days.isNotEmpty || '${request['timeText'] ?? ''}'.isNotEmpty) ...[
                const SizedBox(height: 10),
                Text(
                  keepAll([
                    if (days.isNotEmpty) days,
                    if ('${request['timeText'] ?? ''}'.isNotEmpty) '${request['timeText']}',
                    if (method.isNotEmpty) method,
                  ].join(' · ')),
                  style: AppText.small.copyWith(fontWeight: FontWeight.w700),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 14),
        if (_open)
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton(
              onPressed: _busy ? null : _cancel,
              style: TextButton.styleFrom(foregroundColor: AppColors.muted, minimumSize: const Size(0, 48)),
              child: const Text('신청 취소하기'),
            ),
          )
        else
          Align(
            alignment: Alignment.centerLeft,
            child: OutlinedButton.icon(
              onPressed: _busy ? null : () => setState(() => _again = true),
              style: OutlinedButton.styleFrom(minimumSize: const Size(0, 48)),
              icon: const Icon(Icons.add_rounded, size: 18),
              label: const Text('다시 신청하기'),
            ),
          ),
      ],
    );
  }

  Widget _field(String? label, TextEditingController controller,
      {String? hint, TextInputType? keyboard, List<TextInputFormatter>? formatters, int maxLength = 20}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (label != null) FieldLabel(label),
          TextField(
            controller: controller,
            keyboardType: keyboard,
            inputFormatters: formatters,
            maxLength: maxLength,
            style: const TextStyle(fontSize: 17),
            decoration: InputDecoration(hintText: hint, counterText: ''),
            onChanged: (_) => setState(() {}),
          ),
        ],
      ),
    );
  }

  List<Widget> _form(String title) {
    final days = [for (final d in _state?['days'] as List? ?? const ['월', '화', '수', '목', '금', '토', '일']) '$d'];
    return [
      Notice(
        '인터뷰는 만드신 분(${_name.text.trim().isEmpty ? '신청하시는 분' : _name.text.trim()})께 여쭤봐요. '
        '$title 본인께는 연락드리지 않아요.',
        icon: Icons.lightbulb_outline_rounded,
      ),
      const SizedBox(height: 22),
      _field('인터뷰 받으실 분 이름', _name, hint: '예: 김가은'),
      _field('그분과의 관계', _relation, hint: '예: 손녀'),
      _field(
        '연락받을 전화번호',
        _phone,
        hint: '예: 010-1234-5678',
        keyboard: TextInputType.phone,
        formatters: [FilteringTextInputFormatter.allow(RegExp(r'[0-9\- ]'))],
      ),
      const FieldLabel('연락받기 편한 요일', hint: '여러 개 골라도 돼요.'),
      Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          for (final d in days)
            Pill(
              label: d,
              selected: _days.contains(d),
              onTap: () => setState(() => _days.contains(d) ? _days.remove(d) : _days.add(d)),
            ),
        ],
      ),
      const SizedBox(height: 22),
      const FieldLabel('연락받기 편한 시간'),
      Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          for (final t in _timeChips)
            Pill(
              label: t,
              selected: _time.text.trim() == t,
              onTap: () => setState(() => _time.text = t),
            ),
        ],
      ),
      const SizedBox(height: 10),
      _field(null, _time, hint: '예: 오후 2시~5시', maxLength: 60),
      const FieldLabel('어떻게 이야기할까요?'),
      ChoiceGroup(
        options: const ['phone', 'video'],
        value: _method,
        label: (o) => interviewMethodLabels[o] ?? o,
        onChanged: (v) => setState(() => _method = v),
      ),
      const SizedBox(height: 26),
      const SectionLabel('동의'),
      ConsentCheck(
        tag: '필수',
        label: '인터뷰 내용을 이 대화 상대의 기억·말투에 넣는 데 동의해요',
        value: _agreeUse,
        docKey: 'interview',
        onChanged: (v) => setState(() => _agreeUse = v),
      ),
      ConsentCheck(
        tag: '선택',
        label: '통화 녹음에 동의해요 (녹음 파일은 서비스에 올리지 않아요)',
        value: _agreeRecording,
        onChanged: (v) => setState(() => _agreeRecording = v),
      ),
      const SizedBox(height: 8),
      const Notice('적어 주신 연락처와 일정은 인터뷰를 마치거나 취소한 뒤 30일이 지나면 지워요.'),
    ];
  }
}

/// 기억 더 채우기 화면 아래의 기억 인터뷰 받기 카드(만든 분만, 나 자신 대화 상대는 빼고 — [canAskInterview]).
class InterviewEntryCard extends StatelessWidget {
  final String personaId;

  const InterviewEntryCard({super.key, required this.personaId});

  @override
  Widget build(BuildContext context) {
    return AppCard(
      padding: const EdgeInsets.fromLTRB(18, 16, 14, 16),
      color: AppColors.primarySoft,
      borderColor: Colors.transparent,
      onTap: () => context.push('/p/$personaId/interview'),
      child: Row(
        children: [
          const Icon(Icons.record_voice_over_outlined, color: AppColors.primary),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(interviewTitle, style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5)),
                const SizedBox(height: 2),
                Text(keepAll('$interviewSubtitle. 적기 어려운 이야기는 말로 들려주세요.'), style: AppText.small),
              ],
            ),
          ),
          const Icon(Icons.chevron_right_rounded, color: AppColors.primary),
        ],
      ),
    );
  }
}
