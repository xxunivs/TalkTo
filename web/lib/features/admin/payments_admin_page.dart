import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import 'legal_admin_api.dart';
import 'legal_admin_common.dart';

/// 관리자 화면 · 결제·환불(/admin/payments, 관리자만).
///
/// 결제마다 환불 판단에 필요한 사실을 보인다: 결제 후 첫 대화(만든 분·초대한 가족), 그 대화 상대가 무료 체험을
/// 썼는지, 그 대화 상대의 첫 결제일, 이용 기간, 서버가 짚어 준 환불 판단(refundHint). 환불은 대표가 토스
/// 상점관리자에서 하고, 여기서는 '환불했어요'로 기록만 남긴다(토스를 부르지 않는다).
class PaymentsAdminPage extends StatefulWidget {
  final LegalAdminApi api;

  /// 대화 상대 하나의 결제만(대화 상대 관리 화면에서 열 때).
  final String? personaId;

  const PaymentsAdminPage({super.key, this.api = const LegalAdminApi(), this.personaId});

  @override
  State<PaymentsAdminPage> createState() => _PaymentsAdminPageState();
}

class _PaymentsAdminPageState extends State<PaymentsAdminPage> {
  static const _size = 50;

  final _q = TextEditingController();
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

  @override
  void dispose() {
    _q.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final request = ++_request;
    setState(() {
      _rows = null;
      _error = null;
      _page = 1;
    });
    try {
      final result = await widget.api.payments(q: _q.text, personaId: widget.personaId, size: _size);
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
      final result =
          await widget.api.payments(q: _q.text, personaId: widget.personaId, page: _page + 1, size: _size);
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
      title: '결제·환불',
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Notice('환불은 토스 상점관리자에서 먼저 하고, 여기서는 기록만 남겨요. 이 화면은 돈을 보내거나 결제를 취소하지 않아요.'),
          const SizedBox(height: 14),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _q,
                  decoration: const InputDecoration(hintText: '주문 번호로 찾기', isDense: true),
                  onSubmitted: (_) => _load(),
                ),
              ),
              const SizedBox(width: 8),
              FilledButton(
                onPressed: _load,
                style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                child: const Text('찾기'),
              ),
            ],
          ),
          const SizedBox(height: 16),
          if (_error != null || rows == null || rows.isEmpty)
            adminListState(
              error: _error,
              loading: rows == null,
              empty: rows?.isEmpty ?? false,
              emptyText: '결제가 없어요.',
              onRetry: _load,
            )
          else ...[
            Text('${rows.length} / $_total건', style: AppText.small),
            const SizedBox(height: 8),
            for (final row in rows)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: PaymentFacts(row: row, api: widget.api, onChanged: _load),
              ),
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
}

/// 결제 한 줄(§3.4): 결제·이용 기간·결제 후 첫 대화·체험·환불 판단·환불 기록과 '환불했어요'.
class PaymentFacts extends StatelessWidget {
  final Map<String, dynamic> row;
  final LegalAdminApi api;

  /// 환불을 적은 뒤 다시 읽는다.
  final VoidCallback onChanged;

  const PaymentFacts({super.key, required this.row, required this.api, required this.onChanged});

  /// 전액 환불 대상으로 짚어 준 판단인가.
  static bool fullRefund(Object? code) => code == 'no_chat_7d' || code == 'no_trial_7d';

  @override
  Widget build(BuildContext context) {
    final hint = row['refundHint'] is Map ? row['refundHint'] as Map : const {};
    final refunded = row['refundedAt'] != null;
    final trial = switch (row['trialUsed']) {
      true => '썼어요',
      false => '안 썼어요',
      _ => '—',
    };
    final firstChat = row['firstChatAt'] == null
        ? '없음'
        : '${adminDate(row['firstChatAt'])}${row['firstChatBy'] != null ? ' · ${row['firstChatBy']}' : ''}';
    // 체험: 쓴 것으로 보는 것은 체험 기간에 실제로 대화했을 때만(서버 스냅숏). 체험 끝·결제 전 대화 수는 참고
    final trialFacts = [
      trial,
      if (row['trialEndsAt'] != null) '체험 끝 ${adminDate(row['trialEndsAt'])}',
      if (row['chatsBeforePayment'] != null) '결제 전 대화 ${row['chatsBeforePayment']}번',
    ].join(' · ');
    return AppCard(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  [
                    '${row['personaLabel'] ?? ''}',
                    '${row['planName'] ?? row['plan'] ?? ''}',
                    adminWon(row['amount']),
                  ].where((s) => s.isNotEmpty).join(' · '),
                  style: AppText.label,
                ),
              ),
              StatusChip(paymentKindLabels[row['kind']] ?? '${row['kindLabel'] ?? row['kind'] ?? ''}'),
              const SizedBox(width: 6),
              StatusChip('${row['status'] ?? ''}', positive: row['status'] == 'DONE'),
            ],
          ),
          const SizedBox(height: 6),
          AdminLine('주문 번호', '${row['orderId'] ?? ''}', copy: true),
          AdminLine('결제한 때', adminDate(row['approvedAt'])),
          AdminLine('만든 분', '${row['ownerMasked'] ?? ''}'),
          AdminLine('이용 기간', '${adminDate(row['periodStart'])} ~ ${adminDate(row['periodEnd'])}'),
          AdminLine('결제 후 첫 대화', firstChat),
          AdminLine('무료 체험', trialFacts),
          AdminLine('대화 상대 첫 결제', adminDate(row['personaFirstPaidAt'])),
          if (row['contractSentAt'] != null) AdminLine('계약 내용 알림', adminDate(row['contractSentAt'])),
          const SizedBox(height: 6),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (fullRefund(hint['code'])) ...[
                const StatusChip('전액 환불 대상', positive: true),
                const SizedBox(width: 8),
              ],
              Expanded(
                child: Text(
                  '${hint['text'] ?? ''}${hint['until'] != null ? ' (${adminDate(hint['until'])}까지)' : ''}',
                  style: AppText.small.copyWith(color: AppColors.ink2),
                ),
              ),
            ],
          ),
          if (refunded) ...[
            const SizedBox(height: 6),
            Text(
              '환불했어요: ${adminDate(row['refundedAt'])} · ${adminWon(row['refundAmount'])}'
              '${'${row['refundNote'] ?? ''}'.isNotEmpty ? ' · ${refundNoteText(row['refundNote'])}' : ''}',
              style: AppText.small.copyWith(color: AppColors.primary, fontWeight: FontWeight.w700),
            ),
          ] else if (row['status'] == 'DONE')
            Align(
              alignment: Alignment.centerRight,
              child: OutlinedButton(
                onPressed: () async {
                  final done = await showRefundDialog(
                    context,
                    api,
                    paymentId: '${row['id']}',
                    amount: (row['amount'] as num?)?.toInt(),
                    reason: fullRefund(hint['code']) ? '${hint['code']}' : null,
                  );
                  if (done) onChanged();
                },
                child: const Text('환불했어요'),
              ),
            ),
        ],
      ),
    );
  }
}

/// '환불했어요' 기록 창. 토스 상점관리자에서 취소한 뒤 금액·까닭·메모를 적는다. 적었으면 true.
Future<bool> showRefundDialog(
  BuildContext context,
  LegalAdminApi api, {
  required String paymentId,
  int? amount,
  String? reason,
}) async {
  final result = await showDialog<bool>(
    context: context,
    builder: (_) => _RefundDialog(api: api, paymentId: paymentId, amount: amount, reason: reason),
  );
  return result ?? false;
}

class _RefundDialog extends StatefulWidget {
  final LegalAdminApi api;
  final String paymentId;
  final int? amount;
  final String? reason;

  const _RefundDialog({required this.api, required this.paymentId, this.amount, this.reason});

  @override
  State<_RefundDialog> createState() => _RefundDialogState();
}

class _RefundDialogState extends State<_RefundDialog> {
  late final _amount = TextEditingController(text: widget.amount == null ? '' : '${widget.amount}');
  final _note = TextEditingController();
  late String? _reason = widget.reason;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _amount.dispose();
    _note.dispose();
    super.dispose();
  }

  int get _won => int.tryParse(_amount.text.trim()) ?? 0;

  Future<void> _save() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.refund(widget.paymentId, amount: _won, reason: _reason!, note: _note.text);
      if (mounted) Navigator.pop(context, true);
    } catch (error) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = error.toString();
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('환불했어요'),
      content: SizedBox(
        width: 440,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                keepAll('토스 상점관리자에서 먼저 결제를 취소한 뒤 적어 주세요. 여기서는 돈이 오가지 않고 기록만 남아요.'),
                style: AppText.lead,
              ),
              const SizedBox(height: 14),
              const FieldLabel('돌려준 금액(원)'),
              TextField(
                controller: _amount,
                keyboardType: TextInputType.number,
                inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: 14),
              const FieldLabel('까닭'),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final e in refundReasonLabels.entries)
                    Pill(label: e.value, selected: _reason == e.key, onTap: () => setState(() => _reason = e.key)),
                ],
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _note,
                maxLength: 200,
                decoration: const InputDecoration(hintText: '메모(선택)'),
              ),
              if (_error != null) ErrorBox(_error!),
            ],
          ),
        ),
      ),
      actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      actions: [
        TextButton(onPressed: _busy ? null : () => Navigator.pop(context, false), child: const Text('취소')),
        FilledButton(
          onPressed: _busy || _won <= 0 || _reason == null ? null : _save,
          style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
          child: Text(_busy ? '적는 중…' : '적기'),
        ),
      ],
    );
  }
}
