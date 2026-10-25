import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/ui.dart';
import '../legal/consent_checks.dart';
import '../legal/legal_api.dart';
import '../legal/legal_text.dart';
import 'data/persona_api.dart';
import 'data/persona_rules_api.dart';
import 'data/persona_store.dart';
import 'imported.dart';
import 'widgets/basics_form.dart' show under14ClosedText;

/// 법정대리인 확인(/p/:id/guardian, 만든 분만). 만 19세 미만 아이의 대화 상대는 그 아이의 부모만 만들고,
/// 만 14세 미만이면 법정대리인 동의 두 가지(아이 정보 모으기·해외 회사로 보내기)와 휴대전화 확인 문자까지
/// 받는다(개인정보 보호법 제22조의2). 만 14~18세는 법정대리인 확인만(만들 때 받는다).
///
/// 확인 문자는 TalkTo 가 손으로 보낼 수도 있다(서버가 {queued: true}): 그때는 하루 안에 문자가 오고,
/// 받은 확인 번호를 이 화면에 넣는다. 오류 글은 서버 것을 그대로 보인다.
class GuardianPage extends StatefulWidget {
  final String personaId;

  const GuardianPage({super.key, required this.personaId});

  @override
  State<GuardianPage> createState() => _GuardianPageState();
}

class _GuardianPageState extends State<GuardianPage> {
  Map<String, dynamic>? _detail;
  Map<String, dynamic>? _state;
  Object? _loadError;
  bool _busy = false;
  bool _consent = false;
  bool _overseas = false;
  bool _attest = false;
  final _phone = TextEditingController();
  final _code = TextEditingController();

  /// 확인 문자를 보냈다(또는 TalkTo 가 보내려고 받아 두었다).
  bool _sent = false;
  bool _queued = false;
  DateTime? _expiresAt;
  Timer? _ticker;
  late final Future<Map<String, dynamic>> _doc = LegalApi.doc('guardian_consent');

  String get _id => widget.personaId;
  List<String> get _missing => [for (final m in _state?['missing'] as List? ?? const []) '$m'];
  bool get _child => _state?['mode'] == 'minor_child';

  /// 체크 글(서버 GUARDIAN_LABELS). 이 화면 응답에 없으면 /legal 색인에서 읽는다. 동의 글은 서버 것만 쓴다.
  Future<Map<String, dynamic>>? _labels;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _ticker?.cancel();
    _phone.dispose();
    _code.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([PersonaApi.detail(_id), GuardianApi.status(_id)]);
      if (!mounted) return;
      final labels = results[1]['labels'];
      setState(() {
        _detail = results[0];
        _state = results[1];
        _loadError = null;
        _labels ??= labels is Map && labels.isNotEmpty
            ? Future.value(Map<String, dynamic>.from(labels))
            : LegalApi.guardianLabels().catchError((Object _) => <String, dynamic>{});
        // 다시 열어도(알림 '확인 번호 넣기') TalkTo가 보낼 문자를 기다리는지·확인 번호 남은 시간을 잇는다
        _queued = _queued || results[1]['smsQueued'] == true;
        _expiresAt ??= parseServerTime(results[1]['codeExpiresAt'] as String?);
      });
      if (_expiresAt != null && _ticker == null) {
        _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
          if (mounted) setState(() {});
        });
      }
    } catch (error) {
      if (mounted) setState(() => _loadError = error);
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _agree() => _run(() async {
        await GuardianApi.consent(_id, attest: _missing.contains('attest') && _attest);
        await _load();
        personaStore.load();
      });

  Future<void> _sendSms() => _run(() async {
        final result = await GuardianApi.sendSms(_id, _phone.text.trim());
        if (!mounted) return;
        final expires = parseServerTime(result['expiresAt'] as String?);
        setState(() {
          _sent = true;
          _queued = result['queued'] == true;
          _expiresAt = expires;
        });
        _ticker?.cancel();
        if (expires != null) {
          _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
            if (mounted) setState(() {});
          });
        }
      });

  Future<void> _verify() => _run(() async {
        await GuardianApi.verify(_id, _code.text.trim());
        _ticker?.cancel();
        await _load();
        personaStore.load();
        notifyPersonaEdited(_id);
        if (mounted) showMessage(context, '확인했어요.');
      });

  /// 남은 시간 '9:41'(모르면 null).
  String? get _left {
    final until = _expiresAt;
    if (until == null) return null;
    final seconds = until.difference(DateTime.now()).inSeconds;
    if (seconds <= 0) return '0:00';
    final hours = seconds ~/ 3600;
    final rest = seconds % 3600;
    final clock = '${rest ~/ 60}:${(rest % 60).toString().padLeft(2, '0')}';
    return hours > 0 ? '$hours시간 $clock' : clock;
  }

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    final state = _state;
    if (detail == null || state == null) {
      return Scaffold(
        appBar: AppBar(automaticallyImplyLeading: !Breakpoints.isWide(context)),
        body: _loadError != null
            ? LoadFailed(_loadError!, onRetry: () {
                setState(() => _loadError = null);
                _load();
              })
            : const Loading(),
      );
    }
    final blocked = PersonaBlocked.of(detail, personaId: _id, imported: false);
    if (blocked != null) return blocked;
    final title = personaTitle(detail);
    final missing = _missing;
    final done = state['needed'] != true || missing.isEmpty;
    final notGuardian = (detail['basics'] as Map?)?['notGuardian'] == true;
    return WizardScaffold(
      title: '법정대리인 확인',
      headline: done ? '법정대리인 확인을 마쳤어요' : '법정대리인 확인과 동의',
      lead: done
          ? '이제 $title의 그분 동의 녹음을 해 주세요. 아이가 직접 듣고 답하는 모습을 녹음해요.'
          : _child
              ? '만 14세 미만 아이의 대화 상대는 법정대리인 동의와 휴대전화 확인 문자가 필요해요.'
              : '아이의 대화 상대는 만드는 분이 법정대리인(부모)인지 먼저 확인해야 해요.',
      secondary: TextButton(
        onPressed: () => context.canPop() ? context.pop() : context.go('/p/$_id'),
        child: const Text('돌아가기'),
      ),
      primary: done
          ? FilledButton(onPressed: () => context.go('/p/$_id/consent'), child: const Text('동의 녹음하기'))
          : null,
      children: [
        if (done)
          const Notice(
            '법정대리인 확인을 받았어요.',
            icon: Icons.check_circle_outline_rounded,
            color: AppColors.primary,
            background: AppColors.primarySoft,
          )
        else if (notGuardian)
          const Notice(
            '아이의 대화 상대는 그 아이의 부모님만 만들 수 있어요. 이 대화 상대는 새 녹음 처리와 목소리 다시 만들기를 멈췄어요. '
            '궁금한 점은 고객센터로 알려 주세요.',
          )
        else ...[
          _docCard(title),
          const SizedBox(height: 18),
          if (missing.contains('attest') || missing.contains('consent') || missing.contains('overseas'))
            _agreeCard()
          else if (missing.contains('sms'))
            _smsCard(state),
        ],
      ],
    );
  }

  Widget _docCard(String title) {
    return FutureBuilder<Map<String, dynamic>>(
      future: _doc,
      builder: (context, snap) {
        final doc = snap.data;
        return AppCard(
          padding: const EdgeInsets.fromLTRB(20, 18, 20, 12),
          child: snap.hasError
              ? const ErrorBox('확인 내용을 불러오지 못했어요.')
              : doc == null
                  ? const Padding(padding: EdgeInsets.all(20), child: Loading())
                  : LegalText(fillCallName('${doc['body'] ?? ''}', title), title: '${doc['title'] ?? ''}'),
        );
      },
    );
  }

  /// 법정대리인 확인(빠졌을 때) · 만 14세 미만 동의 두 가지. 체크 글은 서버 것 그대로(읽지 못하면 막는다).
  Widget _agreeCard() {
    final missing = _missing;
    final needsAttest = missing.contains('attest');
    final needsConsent = _child && (missing.contains('consent') || missing.contains('overseas'));
    return FutureBuilder<Map<String, dynamic>>(
      future: _labels,
      builder: (context, snap) {
        final labels = snap.data;
        if (labels == null) return const Padding(padding: EdgeInsets.all(20), child: Loading());
        String label(String key) => '${labels[key] ?? ''}'.trim();
        final missingText = (needsAttest && label('attest').isEmpty) ||
            (needsConsent && (label('consent').isEmpty || label('overseas').isEmpty));
        if (missingText) return const ErrorBox('확인 내용을 불러오지 못했어요. 잠시 뒤 다시 해 주세요.');
        final ready = (!needsAttest || _attest) && (!needsConsent || (_consent && _overseas));
        return AppCard(
          padding: const EdgeInsets.fromLTRB(8, 10, 16, 14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (needsAttest)
                ConsentCheck(
                  tag: '필수',
                  label: label('attest'),
                  value: _attest,
                  onChanged: (v) => setState(() => _attest = v),
                ),
              if (needsConsent) ...[
                ConsentCheck(
                  tag: '필수',
                  label: label('consent'),
                  value: _consent,
                  onChanged: (v) => setState(() => _consent = v),
                ),
                ConsentCheck(
                  tag: '필수',
                  label: label('overseas'),
                  value: _overseas,
                  onChanged: (v) => setState(() => _overseas = v),
                ),
              ],
              const SizedBox(height: 8),
              Padding(
                padding: const EdgeInsets.only(left: 8),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: FilledButton(
                    onPressed: ready && !_busy ? _agree : null,
                    style: FilledButton.styleFrom(minimumSize: const Size(0, 46)),
                    child: const Text('동의하기'),
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  /// 만 14세 미만: 법정대리인 휴대전화로 확인 문자를 받고 확인 번호를 넣는다.
  Widget _smsCard(Map<String, dynamic> state) {
    if (state['under14Open'] == false) return const Notice(under14ClosedText);
    final masked = '${state['phoneMasked'] ?? ''}'.trim();
    final left = _left;
    return AppCard(
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const FieldLabel('법정대리인 휴대전화', hint: "숫자만 적어 주세요. 확인 문자를 보내 드려요."),
          TextField(
            controller: _phone,
            keyboardType: TextInputType.phone,
            maxLength: 11,
            inputFormatters: [FilteringTextInputFormatter.digitsOnly],
            decoration: InputDecoration(hintText: masked.isEmpty ? '01012345678' : masked, counterText: ''),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 10),
          Align(
            alignment: Alignment.centerLeft,
            child: OutlinedButton(
              onPressed: _busy || _phone.text.trim().length < 10 ? null : _sendSms,
              child: Text(_sent ? '확인 문자 다시 받기' : '확인 문자 받기'),
            ),
          ),
          if (_sent || masked.isNotEmpty) ...[
            const SizedBox(height: 16),
            if (_queued)
              const Notice('TalkTo가 확인 문자를 보내 드릴게요(보통 하루 안). 문자가 오면 확인 번호를 넣어 주세요.')
            else if (left != null)
              Text('남은 시간 $left', style: AppText.small.copyWith(color: AppColors.primary, fontWeight: FontWeight.w700)),
            const SizedBox(height: 12),
            const FieldLabel('확인 번호 6자리'),
            TextField(
              controller: _code,
              keyboardType: TextInputType.number,
              maxLength: 6,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              decoration: const InputDecoration(hintText: '123456', counterText: ''),
              onChanged: (_) => setState(() {}),
            ),
            const SizedBox(height: 10),
            Align(
              alignment: Alignment.centerLeft,
              child: FilledButton(
                onPressed: _busy || _code.text.trim().length != 6 ? null : _verify,
                style: FilledButton.styleFrom(minimumSize: const Size(0, 46)),
                child: const Text('확인'),
              ),
            ),
          ],
        ],
      ),
    );
  }
}
