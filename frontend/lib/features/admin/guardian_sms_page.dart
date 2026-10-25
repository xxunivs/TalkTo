import 'package:flutter/material.dart';

import '../../core/network/api_client.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import 'legal_admin_api.dart';
import 'legal_admin_common.dart';

/// 관리자 화면 · 만 14세 미만 확인 문자(/admin/guardian-sms, 관리자만).
///
/// 서버 설정이 SMS_PROVIDER=manual 일 때만 켜진다: 법정대리인이 '확인 문자 받기'를 누르면 번호·확인 번호·문자
/// 글이 여기 쌓이고, 대표가 자기 휴대전화(사업자 정보의 대표 전화)로 그 글을 그대로 보낸 뒤 '보냈어요'를 누른다.
/// TalkTo는 문자를 자동으로 보내지 않는다. '보냈어요' 뒤에는 서버가 전체 번호와 확인 번호를 지우고 가린 번호만
/// 남긴다. 설정이 off 면(기본) 만 14세 미만 아이의 대화 상대를 만들 수 없어 이 대기열도 꺼져 있다.
class GuardianSmsPage extends StatefulWidget {
  final LegalAdminApi api;

  const GuardianSmsPage({super.key, this.api = const LegalAdminApi()});

  /// 대기열이 켜져 있나: 서버가 meta.provider 를 주면 manual 일 때만, 없으면 under14Open(off 면 false).
  /// 둘 다 모르면 켜진 것으로 본다. 'sms_unavailable' 이면 꺼짐([unavailable]).
  static bool active(Map<String, dynamic> meta) {
    if (meta.containsKey('provider')) return meta['provider'] == 'manual';
    if (meta.containsKey('under14Open')) return meta['under14Open'] == true;
    return true;
  }

  /// 꺼져 있다는 서버 답인가(설정이 manual 이 아니면 서버가 막는다).
  static bool unavailable(Object error) =>
      error is ApiException && (error.code == 'sms_unavailable' || error.statusCode == 404);

  @override
  State<GuardianSmsPage> createState() => _GuardianSmsPageState();
}

class _GuardianSmsPageState extends State<GuardianSmsPage> {
  List<Map<String, dynamic>>? _rows;
  Object? _error;
  bool _off = false;
  String? _sending;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _rows = null;
      _error = null;
    });
    try {
      final result = await widget.api.guardianSms();
      if (!mounted) return;
      setState(() {
        _off = !GuardianSmsPage.active(result.meta);
        _rows = result.rows;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        if (GuardianSmsPage.unavailable(error)) {
          _off = true;
          _rows = const [];
        } else {
          _error = error;
        }
      });
    }
  }

  Future<void> _sent(Map<String, dynamic> row) async {
    final ok = await confirmAction(
      context,
      '문자를 보냈나요?',
      '대표 휴대전화로 이 번호에 문자를 보냈을 때만 눌러 주세요. 누르면 전체 번호와 확인 번호를 지우고 가린 번호만 남겨요. '
          '만든 분께는 확인 번호를 넣어 달라는 알림이 가요.',
      action: '보냈어요',
      danger: false,
    );
    if (!ok || !mounted) return;
    final id = '${row['personaId']}';
    setState(() => _sending = id);
    try {
      await widget.api.guardianSmsSent(id);
      if (!mounted) return;
      showMessage(context, '보냈다고 적었어요.');
      await _load();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _sending = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    // 설정을 off 로 바꾸기 전에 쌓인 요청이 있으면 꺼져 있어도 보인다
    final pending = rows != null && rows.isNotEmpty;
    return AdminFrame(
      title: '확인 문자 보내기',
      actions: [IconButton(tooltip: '새로고침', onPressed: _load, icon: const Icon(Icons.refresh_rounded))],
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_off) ...[
            const Notice(
              '지금은 확인 문자를 받지 않아요(서버 설정 SMS_PROVIDER=off). 그래서 만 14세 미만 아이의 대화 상대는 만들 수 '
              '없어요. 대표가 직접 보내기로 하면 서버 설정을 manual 로 바꿔요.',
            ),
            if (pending) const SizedBox(height: 14),
          ],
          if (!_off || pending) ...[
            const Notice(
              "만 14세 미만 아이의 법정대리인께 확인 문자를 보내 주세요. 대표 휴대전화로 아래 글을 그대로 보내고 '보냈어요'를 "
              '눌러요. TalkTo는 자동으로 보내지 않아요. 번호는 보낸 뒤 가린 것만 남아요.',
            ),
            const SizedBox(height: 14),
            if (_error != null || rows == null || rows.isEmpty)
              adminListState(
                error: _error,
                loading: rows == null,
                empty: rows?.isEmpty ?? false,
                emptyText: '보낼 확인 문자가 없어요.',
                onRetry: _load,
              )
            else
              for (final row in rows) _row(row),
          ],
        ],
      ),
    );
  }

  Widget _row(Map<String, dynamic> row) {
    final sending = _sending == '${row['personaId']}';
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: AppCard(
        padding: const EdgeInsets.fromLTRB(18, 14, 14, 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('${row['title'] ?? ''}', style: AppText.label),
            Text('요청한 때 ${adminDate(row['requestedAt'])}', style: AppText.small),
            if (row['expired'] == true) ...[
              const SizedBox(height: 6),
              const ErrorBox('확인 번호의 유효 시간이 지났어요. 보내도 쓸 수 없으니, 만든 분이 확인 문자를 다시 받으면 새로 보내 주세요.'),
            ],
            const SizedBox(height: 8),
            AdminLine('받는 번호', '${row['phone'] ?? ''}', copy: true),
            AdminLine('확인 번호', '${row['code'] ?? ''}'),
            const SizedBox(height: 6),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: AppColors.sunk, borderRadius: BorderRadius.circular(12)),
              child: SelectableText('${row['text'] ?? ''}', style: AppText.body),
            ),
            const SizedBox(height: 6),
            Wrap(
              alignment: WrapAlignment.end,
              spacing: 8,
              runSpacing: 8,
              children: [
                AdminCopyButton('${row['text'] ?? ''}', label: '문자 글 복사'),
                FilledButton(
                  onPressed: _sending != null ? null : () => _sent(row),
                  style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                  child: Text(sending ? '적는 중…' : '보냈어요'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
