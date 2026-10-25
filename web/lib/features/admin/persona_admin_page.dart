import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import 'legal_admin_api.dart';
import 'legal_admin_common.dart';
import 'payments_admin_page.dart' show PaymentFacts, showRefundDialog;

/// 관리자 화면 · 대화 상대 관리(/admin/personas/:id, 관리자만). 서버 GET /admin/personas/{id}/legal.
///
/// - 돌아가신 분: 신청 자격과 유족 명단(연락처 포함), 명단의 분께 보낼 문자·메일 문안 — `복사`만 한다(자동 발송 없음).
/// - 이의 접수: 바로 멈추고 7일 뒤 지운다(만든 분께 알림). 멈춤 해제·지우는 날 미루기.
/// - 만 19세 미만·그분 동의 녹음 상태, 결제·체험 줄과 남은 날 환불 제안 금액.
class AdminPersonaPage extends StatefulWidget {
  final String personaId;
  final LegalAdminApi api;

  const AdminPersonaPage({super.key, required this.personaId, this.api = const LegalAdminApi()});

  @override
  State<AdminPersonaPage> createState() => _AdminPersonaPageState();
}

class _AdminPersonaPageState extends State<AdminPersonaPage> {
  Map<String, dynamic>? _data;
  Object? _error;
  bool _busy = false;

  /// 이의 접수 뒤 서버가 준 남은 날 환불 제안({paymentId, amount, remainingDays, periodDays, refund}).
  Map<String, dynamic>? _refund;

  String _reason = 'objection';
  final _fromName = TextEditingController();
  final _fromRelation = TextEditingController();
  final _note = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _fromName.dispose();
    _fromRelation.dispose();
    _note.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final data = await widget.api.persona(widget.personaId);
      if (mounted) {
        setState(() {
          _data = data;
          _error = null;
        });
      }
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  static Map<String, dynamic>? _obj(Object? value) => value is Map ? Map<String, dynamic>.from(value) : null;
  static List<Map<String, dynamic>> _list(Object? value) =>
      [for (final v in value is List ? value : const []) if (v is Map) Map<String, dynamic>.from(v)];

  Future<void> _run(Future<void> Function() work, String done) async {
    setState(() => _busy = true);
    try {
      await work();
      if (!mounted) return;
      showMessage(context, done);
      await _load();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 지금 멈춘 까닭이 시스템이 건 것(동의 녹음에서 원하지 않음·아이 본인 동의 기한 넘김)인가. 이의를 받으면
  /// 서버가 까닭을 이의로 바꾸고 지우는 날을 앞당길 수 있다(뒤에 동의 녹음이 통과해도 이의가 풀리지 않게).
  bool get _systemHold => systemHoldReasons.contains(_obj(_data?['hold'])?['reason']);

  /// 이의 접수. 이미 이의로 멈춘 대화 상대면 서버가 기록만 더한다(지우는 날은 그대로, 만든 분 알림 없음).
  /// 시스템이 멈춘 것이면 까닭을 이의로 바꾸고 만든 분께 다시 알린다(replaced).
  Future<void> _objection({bool held = false}) async {
    final ok = await (!held
        ? confirmAction(context, '이의 접수', '이 대화 상대를 바로 멈추고 7일 뒤 지워요. 계속할까요?', action: '멈추기')
        : _systemHold
            ? confirmAction(context, '이의로 바꾸기', systemHoldObjectionText, action: '이의로 바꾸기')
            : confirmAction(context, '이의 기록 더하기', '이미 멈춘 대화 상대예요. 기록만 더해요(지우는 날은 그대로).',
                action: '기록 더하기', danger: false));
    if (!ok || !mounted) return;
    setState(() => _busy = true);
    try {
      final result = await widget.api.objection(
        widget.personaId,
        reason: _reason,
        fromName: _fromName.text,
        fromRelation: _fromRelation.text,
        note: _note.text,
      );
      _refund = _obj(result['refund']);
      _fromName.clear();
      _fromRelation.clear();
      _note.clear();
      if (!mounted) return;
      // 서버가 한 일로 알린다(화면을 연 뒤 다른 관리자가 멈추거나 풀었을 수 있다)
      showMessage(context, objectionDoneText(result, held: held));
      await _load();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 메모 한 칸(과 며칠)을 받는 작은 창. 취소하면 null.
  /// (닫히는 동안에도 칸이 글을 읽으므로 controller 는 따로 버리지 않는다)
  Future<({String note, int days})?> _ask(String title, String action, {bool days = false}) {
    final note = TextEditingController();
    var count = 7;
    return showDialog<({String note, int days})>(
      context: context,
      builder: (dialog) => StatefulBuilder(
        builder: (dialog, setInner) => AlertDialog(
          title: Text(title),
          content: SizedBox(
            width: 420,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (days) ...[
                  Row(
                    children: [
                      IconButton(
                        onPressed: count > 1 ? () => setInner(() => count--) : null,
                        icon: const Icon(Icons.remove_rounded),
                      ),
                      Text('$count일 미루기', style: AppText.label),
                      IconButton(
                        onPressed: count < 30 ? () => setInner(() => count++) : null,
                        icon: const Icon(Icons.add_rounded),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                ],
                TextField(
                  controller: note,
                  maxLength: 300,
                  maxLines: 2,
                  decoration: const InputDecoration(hintText: '까닭 메모(고인·사별 이야기는 적지 않아요)'),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(dialog), child: const Text('취소')),
            FilledButton(
              onPressed: () => Navigator.pop(dialog, (note: note.text, days: count)),
              child: Text(action),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _clear() async {
    final answer = await _ask('멈춤 해제', '해제하기');
    if (answer == null || !mounted) return;
    await _run(() async {
      await widget.api.clearHold(widget.personaId, note: answer.note);
      _refund = null;
    }, '멈춤을 풀었어요. 만든 분께 알림을 남겼어요.');
  }

  Future<void> _extend() async {
    final answer = await _ask('지우는 날 미루기', '미루기', days: true);
    if (answer == null || !mounted) return;
    await _run(
      () => widget.api.extendHold(widget.personaId, days: answer.days, note: answer.note),
      '지우는 날을 ${answer.days}일 미뤘어요.',
    );
  }

  /// 남은 날 환불 제안 카드: 방금 이의를 접수했으면 그 답, 멈춘 대화 상대면 서버가 지금 계산한 값.
  Map<String, dynamic>? _refundOf(Map<String, dynamic> data) {
    final offer = _refund ?? (_obj(data['hold']) != null ? _obj(_obj(data['billing'])?['proratedRefund']) : null);
    return offer != null && (offer['refund'] as num? ?? 0) > 0 ? offer : null;
  }

  @override
  Widget build(BuildContext context) {
    final data = _data;
    final refund = data == null ? null : _refundOf(data);
    return AdminFrame(
      title: '대화 상대 관리',
      actions: [IconButton(tooltip: '새로고침', onPressed: _load, icon: const Icon(Icons.refresh_rounded))],
      body: data == null
          ? (_error != null
              ? LoadFailed(_error!, onRetry: _load)
              : const Padding(padding: EdgeInsets.all(40), child: Loading()))
          : Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _persona(data),
                _holdOrObjection(data),
                if (refund != null) _refundCard(refund),
                if (_obj(data['bereaved']) != null || (_obj(data['persona'])?['status'] == 'deceased'))
                  _bereaved(_obj(data['bereaved'])),
                if (_list(data['templates']).isNotEmpty) _templates(_list(data['templates'])),
                if (_obj(data['minor']) != null) _minor(_obj(data['minor'])!),
                _billing(_obj(data['billing'])),
              ],
            ),
    );
  }

  Widget _persona(Map<String, dynamic> data) {
    final persona = _obj(data['persona']) ?? const {};
    final owner = _obj(data['owner']) ?? const {};
    final consent = _obj(data['subjectConsent']);
    return AdminSection(
      '${persona['title'] ?? '대화 상대'}',
      children: [
        AdminLine('성함', '${persona['name'] ?? ''}'),
        AdminLine('상태', lifeStatusLabels[persona['status']] ?? '${persona['status'] ?? ''}'),
        AdminLine('단계', '${persona['step'] ?? ''}'),
        AdminLine('만든 날', adminDate(persona['createdAt'])),
        if (persona['origin'] != null) AdminLine('가져온 곳', '${persona['origin']}'),
        AdminLine('만든 분', [owner['masked'], owner['name']].where((v) => '${v ?? ''}'.isNotEmpty).join(' · ')),
        if (consent != null)
          AdminLine(
            '그분 동의 녹음',
            '${consentStatusLabels[consent['status']] ?? consent['status'] ?? ''} · 녹음 ${consent['attempts'] ?? 0}번',
          ),
      ],
    );
  }

  Widget _holdOrObjection(Map<String, dynamic> data) {
    final hold = _obj(data['hold']);
    final objections = _list(data['objections']);
    return AdminSection(
      hold != null ? '멈춘 대화 상대' : '이의 접수',
      children: [
        if (hold != null) ...[
          AdminLine('까닭', holdReasonLabels[hold['reason']] ?? '${hold['reason'] ?? ''}'),
          AdminLine('멈춘 때', adminDate(hold['holdAt'])),
          AdminLine('지우는 때', adminDate(hold['deleteAfter'])),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              OutlinedButton(onPressed: _busy ? null : _clear, child: const Text('멈춤 해제')),
              OutlinedButton(onPressed: _busy ? null : _extend, child: const Text('지우는 날 미루기')),
            ],
          ),
          const SizedBox(height: 16),
          // 다른 분도 반대하면 기록을 남긴다(먼저 온 이의가 철회돼도 이 기록이 보이게)
          Text(
            keepAll(_systemHold
                ? '유족·다른 쪽 부모님·그분 본인이 반대를 알려 오면 접수해요. 까닭을 이의로 바꾸고 지우는 날은 7일 뒤보다 '
                    '늦으면 앞당겨요(뒤에 동의 녹음이 확인돼도 풀리지 않게). 만든 분께 다시 알려요.'
                : '다른 유족·다른 쪽 부모님·그분 본인이 또 반대를 알려 오면 기록만 더해요. 지우는 날은 그대로예요.'),
            style: AppText.small,
          ),
          const SizedBox(height: 10),
          ..._objectionForm(held: true),
        ] else ...[
          Text(
            keepAll('유족·다른 쪽 부모님·그분 본인이 반대를 알려 오면 접수해요. 바로 멈추고 7일 뒤 지우며, '
                '만든 분께 알림이 가요. 결제한 기간이 남아 있으면 남은 날만큼 돌려드려요.'),
            style: AppText.small,
          ),
          const SizedBox(height: 10),
          ..._objectionForm(held: false),
        ],
        if (objections.isNotEmpty) ...[
          const SizedBox(height: 12),
          const Text('이의 기록', style: AppText.label),
          for (final o in objections)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                [
                  adminDate(o['receivedAt']),
                  holdReasonLabels[o['reason']] ?? '${o['reason'] ?? ''}',
                  if ('${o['fromName'] ?? ''}'.isNotEmpty) '${o['fromName']}',
                  if ('${o['fromRelation'] ?? ''}'.isNotEmpty) '${o['fromRelation']}',
                  if (o['outcome'] == 'deleted') '지움 ${adminDate(o['outcomeAt'])}',
                  if (o['outcome'] == 'cancelled') '해제 ${adminDate(o['outcomeAt'])}',
                  if ('${o['note'] ?? ''}'.isNotEmpty) '메모: ${o['note']}',
                ].join(' · '),
                style: AppText.small,
              ),
            ),
        ],
      ],
    );
  }

  /// 이의 까닭·보낸 분·메모 칸. 멈춘 대화 상대면 단추가 '이의 기록 더하기'(멈춤·지우는 날은 그대로).
  List<Widget> _objectionForm({required bool held}) {
    return [
      Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          for (final r in objectionReasons)
            Pill(label: holdReasonLabels[r] ?? r, selected: _reason == r, onTap: () => setState(() => _reason = r)),
        ],
      ),
      const SizedBox(height: 12),
      Row(
        children: [
          Expanded(
            child: TextField(
              controller: _fromName,
              maxLength: 40,
              decoration: const InputDecoration(hintText: '보낸 분 이름', counterText: ''),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: TextField(
              controller: _fromRelation,
              maxLength: 20,
              decoration: const InputDecoration(hintText: '고인·그분과의 관계', counterText: ''),
            ),
          ),
        ],
      ),
      const SizedBox(height: 10),
      TextField(
        controller: _note,
        maxLength: 1000,
        maxLines: 3,
        minLines: 2,
        decoration: const InputDecoration(hintText: '메모(받은 경로·날짜). 고인·사별 이야기는 적지 않아요.'),
      ),
      Align(
        alignment: Alignment.centerRight,
        child: held
            ? OutlinedButton(
                onPressed: _busy ? null : () => _objection(held: true),
                child: const Text('이의 기록 더하기'),
              )
            : FilledButton(
                onPressed: _busy ? null : _objection,
                style: FilledButton.styleFrom(backgroundColor: AppColors.danger, minimumSize: const Size(0, 44)),
                child: const Text('이의 접수'),
              ),
      ),
    ];
  }

  Widget _refundCard(Map<String, dynamic> refund) {
    final amount = (refund['refund'] as num?)?.toInt();
    return AdminSection(
      '남은 날 환불 제안',
      children: [
        Text(
          '${adminWon(refund['refund'])} (결제 ${adminWon(refund['amount'])} 중 '
          '남은 ${refund['remainingDays'] ?? '?'}일 / ${refund['periodDays'] ?? '?'}일)',
          style: AppText.body,
        ),
        const SizedBox(height: 4),
        const Text('토스 상점관리자에서 돌려준 뒤 기록을 남겨 주세요.', style: AppText.small),
        if (refund['paymentId'] != null && (amount ?? 0) > 0)
          Align(
            alignment: Alignment.centerRight,
            child: OutlinedButton(
              onPressed: () async {
                final done = await showRefundDialog(
                  context,
                  widget.api,
                  paymentId: '${refund['paymentId']}',
                  amount: amount,
                  reason: 'prorated',
                );
                if (done && mounted) {
                  setState(() => _refund = null);
                  await _load();
                }
              },
              child: const Text('환불했어요'),
            ),
          ),
      ],
    );
  }

  Widget _bereaved(Map<String, dynamic>? b) {
    if (b == null) {
      return const AdminSection(
        '신청 자격과 유족 명단',
        children: [Notice('아직 적지 않았어요. 만든 분께 안내 카드가 보여요.')],
      );
    }
    final members = _list(b['members']);
    final history = b['history'] is List ? b['history'] as List : const [];
    return AdminSection(
      '신청 자격과 유족 명단',
      children: [
        AdminLine('신청 자격', applicantRelationLabels[b['applicantRelation']] ?? '${b['applicantRelation'] ?? ''}'),
        if (b['applicantRelation'] == 'other')
          AdminLine(
            '동의해 준 유족',
            '${b['consentingName'] ?? ''} · '
                '${applicantRelationLabels[b['consentingRelation']] ?? b['consentingRelation'] ?? ''}',
          ),
        AdminLine(
          '적은 때',
          [adminDate(b['declaredAt']), if (b['pledgeVersion'] != null) '확약 ${b['pledgeVersion']}판'].join(' · '),
        ),
        // 덮어쓴 선언은 지우지 않고 서버 history 에 쌓인다(이의 뒤 명단을 바꾸는 일을 알아보게)
        if (history.isNotEmpty) AdminLine('고쳐 적음', '${history.length}번(예전 선언은 서버 기록에 남아 있어요)'),
        const SizedBox(height: 6),
        if (b['noneLeft'] == true)
          const Text('적을 분이 없어요(모두 돌아가셨어요)를 골랐어요.', style: AppText.body)
        else
          for (final m in members)
            AdminLine(
              bereavedRelationLabels[m['relation']] ?? '${m['relation'] ?? ''}',
              [m['name'], m['contact']].where((v) => '${v ?? ''}'.isNotEmpty).join(' · '),
              copy: '${m['contact'] ?? ''}'.isNotEmpty,
            ),
      ],
    );
  }

  Widget _templates(List<Map<String, dynamic>> templates) {
    return AdminSection(
      '유족께 알릴 문안(직접 보내요)',
      children: [
        Text(
          keepAll('TalkTo는 자동으로 보내지 않아요. 복사해서 대표 휴대전화·메일로 직접 보내 주세요.'),
          style: AppText.small,
        ),
        for (final t in templates) ...[
          const SizedBox(height: 12),
          Text('${t['name'] ?? ''} · ${t['channel'] == 'email' ? '메일' : '문자'}', style: AppText.label),
          if ('${t['contact'] ?? ''}'.isNotEmpty)
            AdminLine(t['channel'] == 'email' ? '받는 메일' : '받는 번호', '${t['contact']}', copy: true),
          if ('${t['subject'] ?? ''}'.isNotEmpty) AdminLine('메일 제목', '${t['subject']}', copy: true),
          const SizedBox(height: 4),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(color: AppColors.sunk, borderRadius: BorderRadius.circular(12)),
            child: SelectableText('${t['text'] ?? ''}', style: AppText.body),
          ),
          Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: '${t['text'] ?? ''}'));
                if (mounted) showMessage(context, '복사했어요.');
              },
              icon: const Icon(Icons.copy_rounded, size: 16),
              label: Text(t['channel'] == 'email' ? '메일 본문 복사' : '문자 복사'),
            ),
          ),
        ],
      ],
    );
  }

  Widget _minor(Map<String, dynamic> m) {
    return AdminSection(
      '만 19세 미만',
      children: [
        AdminLine('태어난 해·달', '${m['birthYm'] ?? ''}${m['age'] != null ? ' (만 ${m['age']}세)' : ''}'),
        AdminLine('부모 양쪽', guardianBasisLabels[m['guardianBasis']] ?? '${m['guardianBasis'] ?? ''}'),
        if (m['guardianOk'] != null)
          AdminLine(
            '법정대리인 확인',
            m['guardianOk'] == true
                ? '마쳤어요'
                : '아직 · ${[for (final k in m['missing'] as List? ?? const []) guardianMissingLabels[k] ?? '$k'].join(', ')}',
          ),
        AdminLine(
          '확인 문자',
          m['smsVerifiedAt'] == null ? '아직' : '${adminDate(m['smsVerifiedAt'])} · ${m['phoneMasked'] ?? ''}',
        ),
        if (m['reconsentKind'] != null)
          AdminLine(
            '다시 받을 동의',
            '만 ${m['reconsentKind']}세 · ${adminDate(m['reconsentFrom'])}부터 · '
                '기한 ${adminDate(m['reconsentDueAt'] ?? m['dueAt'])}'
                '${m['reconsentDoneAt'] != null ? ' · 받음 ${adminDate(m['reconsentDoneAt'])}' : ''}',
          ),
        if (m['notGuardian'] == true) const Notice('부모가 아닌 분이 예전에 만든 아이의 대화 상대예요. 처리 방법은 대표가 정해요.'),
      ],
    );
  }

  Widget _billing(Map<String, dynamic>? billing) {
    final payments = _list(billing?['payments']);
    final trial = switch (billing?['trialUsed']) {
      true => '썼어요',
      false => '안 썼어요',
      _ => '—',
    };
    // 회원 잘못 없이 멈추면(이의 접수 등) 돌려드릴 남은 날 일할 금액(약관 2번). 결정은 대표가 한다
    final prorated = _obj(billing?['proratedRefund']);
    return AdminSection(
      '결제·체험',
      trailing: TextButton(onPressed: () => context.go('/admin/payments'), child: const Text('결제·환불 전체')),
      children: [
        AdminLine('무료 체험', trial),
        if (billing?['trialEndsAt'] != null) AdminLine('체험 끝', adminDate(billing!['trialEndsAt'])),
        if (prorated != null && (prorated['refund'] as num? ?? 0) > 0)
          AdminLine(
            '남은 날 일할',
            '${adminWon(prorated['refund'])} (남은 ${prorated['remainingDays'] ?? '?'}일 / ${prorated['periodDays'] ?? '?'}일)',
          ),
        if (payments.isEmpty)
          const Text('결제가 없어요.', style: AppText.small)
        else
          for (final p in payments)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: PaymentFacts(row: p, api: widget.api, onChanged: _load),
            ),
      ],
    );
  }
}

/// 시스템이 건 멈춤(이의를 받으면 서버가 까닭을 이의로 바꾼다 — holds.SYSTEM_REASONS).
const systemHoldReasons = {'subject_refused', 'minor_consent_missing'};

const systemHoldObjectionText = '동의 녹음 결과나 기한 넘김으로 멈춘 대화 상대예요. 까닭을 이의로 바꾸고, 지우는 날은 7일 뒤보다 늦으면 '
    '앞당겨요. 만든 분께 다시 알려요.';

/// 이의 접수 뒤 알림 글(서버 응답 already · replaced 로). 응답에 없으면 화면이 본 상태(held)로.
String objectionDoneText(Map<String, dynamic> result, {required bool held}) {
  if (result['replaced'] == true) return '까닭을 이의로 바꿨어요. 지우는 날을 다시 정하고 만든 분께 다시 알렸어요.';
  final already = result['already'] is bool ? result['already'] as bool : held;
  return already ? '이의 기록을 더했어요. 지우는 날은 그대로예요.' : '멈췄어요. 만든 분께 알림을 남겼어요.';
}
