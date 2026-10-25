import 'package:flutter/material.dart';

import 'guardian_sms_page.dart';
import 'legal_admin_api.dart';
import 'legal_admin_common.dart';

/// 관리자 첫 화면(/admin)의 법률 자문 반영 카드: 동의 녹음 검수 · 멈춘 대화 상대 · 대화가 어려우신 분 대화 상대
/// · 결제·환불 · (SMS_PROVIDER=manual 일 때만) 확인 문자 보내기. 수는 목록을 읽어 붙인다(못 읽으면 수 없이).
class LegalAdminLinks extends StatefulWidget {
  final LegalAdminApi api;

  const LegalAdminLinks({super.key, this.api = const LegalAdminApi()});

  @override
  State<LegalAdminLinks> createState() => _LegalAdminLinksState();
}

class _LegalAdminLinksState extends State<LegalAdminLinks> {
  late final Future<int?> _consents = _count(() async => (await widget.api.consents(size: 1)).total);
  late final Future<int?> _holds = _count(() async => (await widget.api.holds()).length);
  late final Future<int?> _unable = _count(() async => (await widget.api.unable()).length);

  /// 확인 문자 대기 수. 대기열이 꺼져 있고(off) 쌓인 것도 없으면 null — 카드를 보이지 않는다.
  late final Future<int?> _sms = _count(() async {
    final result = await widget.api.guardianSms();
    return GuardianSmsPage.active(result.meta) || result.rows.isNotEmpty ? result.rows.length : null;
  });

  static Future<int?> _count(Future<int?> Function() read) async {
    try {
      return await read();
    } catch (_) {
      return null;
    }
  }

  @override
  Widget build(BuildContext context) {
    const gap = SizedBox(height: 12);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AdminLinkCard(
          icon: Icons.mic_none_rounded,
          title: '동의 녹음 검수',
          unit: '건',
          subtitle: '살아 계신 분 본인의 동의 녹음을 듣고 10항목으로 판정해요',
          route: '/admin/consents',
          count: _consents,
        ),
        gap,
        AdminLinkCard(
          icon: Icons.pause_circle_outline_rounded,
          title: '멈춘 대화 상대',
          subtitle: '이의 접수 등으로 멈춘 것 · 지우는 날 · 해제와 미루기',
          route: '/admin/holds',
          count: _holds,
        ),
        gap,
        AdminLinkCard(
          icon: Icons.do_not_disturb_on_outlined,
          title: '대화가 어려우신 분 대화 상대',
          subtitle: '처리를 멈춘 예전 대화 상대(부모가 아닌 분이 만든 아이의 것 포함) · 대표 보고용',
          route: '/admin/unable',
          count: _unable,
        ),
        gap,
        const AdminLinkCard(
          icon: Icons.receipt_long_outlined,
          title: '결제·환불',
          subtitle: '결제 후 첫 대화 · 체험 사용 · 환불 판단 · 환불 기록',
          route: '/admin/payments',
        ),
        FutureBuilder<int?>(
          future: _sms,
          builder: (context, snap) => snap.data == null
              ? const SizedBox.shrink()
              : Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: AdminLinkCard(
                    icon: Icons.sms_outlined,
                    title: '확인 문자 보내기',
                    unit: '건',
                    subtitle: '만 14세 미만 법정대리인께 대표 휴대전화로 보낼 문자',
                    route: '/admin/guardian-sms',
                    count: _sms,
                  ),
                ),
        ),
      ],
    );
  }
}
