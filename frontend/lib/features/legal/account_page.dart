import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/app_update.dart';
import '../../core/network/api_client.dart';
import '../../core/session.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import 'legal_api.dart';
import 'legal_text.dart';

/// 회원 탈퇴 안내에 덧붙이는 가족 공유 한 줄(내 계정·탈퇴 확인 창·다시 동의 화면이 같이 쓴다).
const withdrawSharingNote = '내가 만들어 가족과 함께 쓰던 대화 상대도 지워져 가족이 더는 이야기할 수 없어요. '
    '가족이 나눠 준 대화 상대에서는 빠지고, 거기서 나눈 내 대화도 지워져요.';

/// 내 계정(/account): 이름·로그인 방식, 동의 내역, 약관과 안내, 고객센터, 회원 탈퇴.
class AccountPage extends StatefulWidget {
  const AccountPage({super.key});

  @override
  State<AccountPage> createState() => _AccountPageState();
}

class _AccountPageState extends State<AccountPage> {
  late Future<List<Map<String, dynamic>>> _consents = LegalApi.myConsents();
  late final Future<Map<String, dynamic>> _index = LegalApi.index();
  bool _deleting = false;

  String get _loginMethod => switch (session.provider) {
        'kakao' => '카카오로 로그인해요',
        'google' => 'Google로 로그인해요${session.email != null ? ' · ${session.email}' : ''}',
        _ => '이메일로 로그인해요${session.email != null ? ' · ${session.email}' : ''}',
      };

  /// 서버 시각(UTC ISO)을 이 기기 날짜로: 2026. 10. 13.
  static String _date(Object? iso) {
    final parsed = iso is String ? DateTime.tryParse(iso.endsWith('Z') || iso.contains('+') ? iso : '${iso}Z') : null;
    if (parsed == null) return '';
    final d = parsed.toLocal();
    return '${d.year}. ${d.month}. ${d.day}.';
  }

  Future<void> _withdraw() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialog) => const WithdrawDialog(),
    );
    if (confirmed != true || !mounted) return;
    setState(() => _deleting = true);
    final Map<String, dynamic> result;
    try {
      result = await LegalApi.deleteAccount();
    } catch (error) {
      if (mounted) {
        setState(() => _deleting = false);
        showMessage(context, error is ApiException ? error.message : '탈퇴하지 못했어요. 잠시 뒤 다시 해 주세요.');
      }
      return;
    }
    // 지운 곳과 서버 백업이 남는 날(서버 글)을 로그아웃 전에 보여 준다
    if (mounted) await showWithdrawDone(context, result);
    await session.logout();
  }

  @override
  Widget build(BuildContext context) {
    final wide = Breakpoints.isWide(context);
    return Scaffold(
      appBar: AppBar(automaticallyImplyLeading: !wide, title: const Text('내 계정·동의')),
      body: ListView(
        padding: EdgeInsets.fromLTRB(20, wide ? 24 : 4, 20, 48),
        children: [
          PageBody(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Headline('내 계정'),
                const SizedBox(height: 18),
                AppCard(
                  child: Row(
                    children: [
                      Avatar(session.name, size: 48),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(session.name, style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
                            const SizedBox(height: 2),
                            Text(_loginMethod, style: AppText.small),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 28),
                const SectionLabel('동의 내역'),
                AppCard(padding: const EdgeInsets.fromLTRB(18, 8, 18, 8), child: _consentList()),
                const SizedBox(height: 10),
                const Notice(
                  '필수 동의를 거두려면 아래에서 회원 탈퇴를 해 주세요. '
                  '종교·건강 이야기 동의는 그 대화 상대의 대화 화면 오른쪽 위 \'더 보기\'에서 언제든 거둘 수 있어요'
                  '(내가 만든 대화 상대만).',
                ),
                const SizedBox(height: 28),
                const SectionLabel('약관과 안내'),
                AppCard(padding: const EdgeInsets.symmetric(vertical: 6), child: _docList()),
                const SizedBox(height: 10),
                // 화면이 바뀌지 않아 보일 때 어느 버전인지 확인하는 데 쓴다
                Text('웹 버전 ${appBuild.isEmpty ? '로컬' : appBuild}', style: AppText.small),
                const SizedBox(height: 28),
                const SectionLabel('도움이 필요하세요?'),
                AppCard(
                  padding: const EdgeInsets.symmetric(vertical: 6),
                  child: ListTile(
                    leading: const Icon(Icons.support_agent_outlined, color: AppColors.primary),
                    title: const Text('고객센터', style: TextStyle(fontSize: 15.5, fontWeight: FontWeight.w700)),
                    subtitle: Text(keepAll('카카오톡 채널이나 이메일로 물어보세요'), style: AppText.small),
                    trailing: const Icon(Icons.chevron_right_rounded, color: AppColors.muted),
                    onTap: () => context.go('/support'),
                  ),
                ),
                const SizedBox(height: 28),
                const SectionLabel('회원 탈퇴'),
                AppCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        keepAll('탈퇴하면 모든 대화 상대와 녹음·남긴 글·대화 기록을 지우고, 요금제도 끝나서 더는 결제되지 않아요. '
                            '법으로 보관해야 하는 결제 기록과, 이름·이메일을 지운 동의·이용 기록만 따로 보관해요. '
                            '지운 자료도 서버 백업에는 최대 7일 남았다가 저절로 지워져요. '
                            "녹음을 보낸 해외 회사에 남는 자료와 지우는 때는 '해외 회사로 보내는 정보'에 적어 두었어요. "
                            '$withdrawSharingNote 되돌릴 수 없어요.'),
                        style: AppText.lead,
                      ),
                      const SizedBox(height: 14),
                      OutlinedButton(
                        onPressed: _deleting ? null : _withdraw,
                        style: OutlinedButton.styleFrom(
                          foregroundColor: AppColors.danger,
                          side: const BorderSide(color: AppColors.danger),
                        ),
                        child: Text(_deleting ? '탈퇴하는 중이에요…' : '회원 탈퇴'),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 32),
                const Divider(height: 1),
                const SizedBox(height: 16),
                const BusinessInfo(),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _consentList() {
    return FutureBuilder<List<Map<String, dynamic>>>(
      future: _consents,
      builder: (context, snap) {
        if (snap.hasError) {
          final error = snap.error;
          return Padding(
            padding: const EdgeInsets.symmetric(vertical: 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                ErrorBox(error is ApiException ? error.message : '동의 내역을 불러오지 못했어요.'),
                TextButton(
                  // setState 에 Future 를 돌려주면 안 된다(디버그에서 오류)
                  onPressed: () => setState(() {
                    _consents = LegalApi.myConsents();
                  }),
                  child: const Text('다시 불러오기'),
                ),
              ],
            ),
          );
        }
        final rows = snap.data;
        if (rows == null) return const Padding(padding: EdgeInsets.all(20), child: Loading());
        if (rows.isEmpty) {
          return const Padding(
            padding: EdgeInsets.symmetric(vertical: 12),
            child: Text('아직 동의한 내용이 없어요.', style: AppText.small),
          );
        }
        return Column(
          children: [
            for (var i = 0; i < rows.length; i++) ...[
              if (i > 0) const Divider(height: 1),
              _consentRow(rows[i]),
            ],
          ],
        );
      },
    );
  }

  Widget _consentRow(Map<String, dynamic> row) {
    final agreed = row['agreed'] == true;
    final who = (row['personaTitle'] as String?)?.trim() ?? '';
    final title = (row['title'] ?? row['type'] ?? '').toString();
    final date = _date(row['agreedAt']);
    final version = row['version'] != null ? '버전 ${row['version']}' : '';
    final detail = [
      agreed ? '동의함' : '동의하지 않음',
      if (version.isNotEmpty) version,
      if (date.isNotEmpty) date,
    ].join(' · ');
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 11),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 2, right: 10),
            child: Icon(
              agreed ? Icons.check_circle_rounded : Icons.remove_circle_outline_rounded,
              size: 18,
              color: agreed ? AppColors.primary : AppColors.muted,
            ),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text.rich(
                  TextSpan(children: [
                    if (who.isNotEmpty)
                      TextSpan(text: '$who · ', style: const TextStyle(color: AppColors.primary)),
                    TextSpan(text: keepAll(title)),
                  ]),
                  style: const TextStyle(fontSize: 14.5, fontWeight: FontWeight.w700, height: 1.45),
                ),
                const SizedBox(height: 2),
                Text(detail, style: AppText.small.copyWith(fontSize: 12.5)),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _docList() {
    return FutureBuilder<Map<String, dynamic>>(
      future: _index,
      builder: (context, snap) {
        if (snap.hasError) {
          return const Padding(
            padding: EdgeInsets.all(18),
            child: Text('문서 목록을 불러오지 못했어요. 잠시 뒤 다시 해 주세요.', style: AppText.small),
          );
        }
        final data = snap.data;
        if (data == null) return const Padding(padding: EdgeInsets.all(20), child: Loading());
        // 개인정보 처리방침은 맨 위에, 굵게 보인다(다른 안내와 구별되게)
        final all = [for (final d in data['docs'] as List? ?? const []) Map<String, dynamic>.from(d as Map)];
        final docs = [
          ...all.where((d) => d['key'] == 'privacy_policy'),
          ...all.where((d) => d['key'] != 'privacy_policy'),
        ];
        return Column(
          children: [
            for (final d in docs)
              ListTile(
                dense: true,
                title: Text(
                  keepAll((d['title'] ?? '').toString()),
                  style: TextStyle(
                    fontSize: 14.5,
                    fontWeight: d['key'] == 'privacy_policy' ? FontWeight.w700 : null,
                  ),
                ),
                trailing: const Icon(Icons.chevron_right_rounded, color: AppColors.muted),
                onTap: () => showLegalDoc(context, d['key'].toString()),
              ),
          ],
        );
      },
    );
  }
}

/// 탈퇴를 마친 뒤 창(제목 '탈퇴했어요'). 본문은 서버가 정한 지운 뒤 안내(DELETE /auth/me 의 message —
/// TalkTo 서버에서 지운 것·서버 백업이 남는 날·해외 회사에 남는 자료)와 인사. '확인'을 눌러야 닫힌다.
/// 부르는 쪽이 닫힌 뒤 로그아웃한다(내 계정·다시 동의 화면이 같이 쓴다).
Future<void> showWithdrawDone(BuildContext context, Map<String, dynamic> result) {
  final message = '${result['message'] ?? ''}'.trim();
  return showDialog<void>(
    context: context,
    barrierDismissible: false,
    builder: (dialog) => AlertDialog(
      title: const Text('탈퇴했어요'),
      content: SizedBox(
        width: 420,
        child: Text(
          keepAll([if (message.isNotEmpty) message, '그동안 TalkTo와 함께해 주셔서 고마워요.'].join('\n\n')),
          style: AppText.lead,
        ),
      ),
      actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      actions: [
        FilledButton(
          onPressed: () => Navigator.pop(dialog),
          style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
          child: const Text('확인'),
        ),
      ],
    ),
  );
}

/// 회원 탈퇴 확인 창. '탈퇴'를 그대로 적어야 버튼이 켜진다.
class WithdrawDialog extends StatefulWidget {
  const WithdrawDialog({super.key});

  @override
  State<WithdrawDialog> createState() => _WithdrawDialogState();
}

class _WithdrawDialogState extends State<WithdrawDialog> {
  final _text = TextEditingController();

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final ok = _text.text.trim() == '탈퇴';
    return AlertDialog(
      title: const Text('정말 탈퇴할까요?'),
      content: SizedBox(
        width: 420,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              keepAll('모든 대화 상대와 녹음·대화 기록이 지워지고 되돌릴 수 없어요. $withdrawSharingNote '
                  '계속하려면 아래 칸에 \'탈퇴\'라고 적어 주세요.'),
              style: AppText.lead,
            ),
            const SizedBox(height: 14),
            TextField(
              controller: _text,
              autofocus: true,
              decoration: const InputDecoration(hintText: '탈퇴'),
              onChanged: (_) => setState(() {}),
              onSubmitted: (_) {
                if (ok) Navigator.pop(context, true);
              },
            ),
          ],
        ),
      ),
      actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('취소')),
        FilledButton(
          onPressed: ok ? () => Navigator.pop(context, true) : null,
          style: FilledButton.styleFrom(backgroundColor: AppColors.danger, minimumSize: const Size(0, 44)),
          child: const Text('탈퇴하기'),
        ),
      ],
    );
  }
}
