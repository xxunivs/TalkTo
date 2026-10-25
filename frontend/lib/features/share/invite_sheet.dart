import 'package:flutter/material.dart';

import '../../core/analytics.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import 'member_profile_sheet.dart';
import 'share_api.dart';

/// 초대할 가족에 대해 적어 두고 링크를 만든다(가족과 함께 화면에서 띄운다).
/// 이름·관계·부르는 말까지 적어 두면 받는 분은 동의에 체크하고 '네, 맞아요'만 누르면 된다.
/// 모두 비워 두어도 된다(받는 분이 직접 적는다).
/// 만들었으면 서버 응답({share, url})을, 닫으면 null 을 돌려준다.
Future<Map<String, dynamic>?> showInviteSheet(
  BuildContext context, {
  required String personaId,
  required String title,

  /// 가족 표에서 고른 줄({familyRow, myName, myRelation, calledMe}). '다른 가족'이면 null.
  Map<String, dynamic>? row,
  List<String> agreements = const [],
  List<String> relations = const [],
  Map? suggestions,
  bool self = false,
  bool imported = false,

  /// 아이(만 19세 미만)의 대화 상대: 관계는 [relations](법정대리인과 직계가족)에서만 고른다.
  bool minorScope = false,
}) async {
  Map<String, dynamic>? created;
  final name = '${row?['myName'] ?? ''}'.trim();
  final saved = await showProfileSheet(
    context,
    heading: name.isEmpty ? '초대할 가족을 알려 주세요' : '$name님은 어떤 분인가요?',
    initial: row,
    voice: ProfileVoice.owner,
    title: title,
    self: self,
    imported: imported,
    relations: relations,
    suggestions: suggestions,
    requireAll: false,
    minorScope: minorScope,
    action: '초대 링크 만들기',
    note: "이름·관계·부르는 말까지 적어 두면 받는 분은 동의에 체크하고 '네, 맞아요'만 누르면 돼요. 모르면 비워 두세요.",
    onSave: (profile) async {
      created = await ShareApi.create(
        personaId,
        agreements: agreements,
        familyRow: row?['familyRow'] as String?,
        prefill: {
          for (final e in profile.entries)
            if (e.value != null) e.key: e.value,
        },
      );
    },
  );
  return saved == null ? null : created;
}

/// 방금 만든(또는 다시 보낸) 링크. 공유 창과 클립보드는 누른 그 자리에서만 열려서
/// 만들기와 보내기를 두 번 누르게 한다. 링크는 이 화면에서 한 번만 보인다(목록에는 없다).
class InviteLinkCard extends StatefulWidget {
  final String url;

  /// 받는 분 이름(없으면 null).
  final String? name;

  /// 함께 보내는 글([inviteShareText]).
  final String shareText;

  /// 받는 분에 대해 다 적어 두었는지([invitePrefilled]). 어르신께 알려 드릴 말이 달라진다.
  final bool prefilled;
  final VoidCallback? onClose;

  const InviteLinkCard({
    super.key,
    required this.url,
    this.name,
    required this.shareText,
    this.prefilled = false,
    this.onClose,
  });

  @override
  State<InviteLinkCard> createState() => _InviteLinkCardState();
}

class _InviteLinkCardState extends State<InviteLinkCard> {
  bool _copyFailed = false;

  String get _message => '${widget.shareText}\n${widget.url}';

  Future<void> _share() async {
    final result = await LinkSharing.share(url: widget.url, text: widget.shareText);
    if (!mounted) return;
    if (result == true) {
      Analytics.event('share_link_sent', {'method': 'share'});
    } else if (result == false) {
      showMessage(context, "공유 창을 열지 못했어요. '링크 복사'를 눌러 보내 주세요.");
    }
  }

  Future<void> _copy() async {
    final ok = await LinkSharing.copy(_message);
    if (!mounted) return;
    setState(() => _copyFailed = !ok);
    if (ok) {
      Analytics.event('share_link_sent', {'method': 'copy'});
      showMessage(context, '링크를 복사했어요. 가족에게 보내 주세요.');
    }
  }

  @override
  Widget build(BuildContext context) {
    final name = widget.name?.trim() ?? '';
    final canShare = LinkSharing.canShare();
    return AppCard(
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 18),
      color: AppColors.primarySoft,
      borderColor: Colors.transparent,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Icon(Icons.link_rounded, color: AppColors.primary),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  keepAll(name.isEmpty ? '보낼 링크가 준비됐어요' : '$name님께 보낼 링크가 준비됐어요'),
                  style: AppText.heading,
                ),
              ),
              if (widget.onClose != null)
                IconButton(
                  tooltip: '닫기',
                  visualDensity: VisualDensity.compact,
                  onPressed: widget.onClose,
                  icon: const Icon(Icons.close_rounded, size: 20, color: AppColors.ink2),
                ),
            ],
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            decoration: BoxDecoration(color: AppColors.card, borderRadius: BorderRadius.circular(12)),
            child: SelectableText(widget.url, style: const TextStyle(fontSize: 13, color: AppColors.ink2)),
          ),
          if (_copyFailed) ...[
            const SizedBox(height: 6),
            const Text('링크를 길게 눌러 복사해 주세요.', style: TextStyle(fontSize: 13, color: AppColors.danger)),
          ],
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              if (canShare)
                FilledButton.icon(
                  onPressed: _share,
                  style: FilledButton.styleFrom(minimumSize: const Size(0, 48)),
                  icon: const Icon(Icons.ios_share_rounded, size: 18),
                  label: const Text('보내기'),
                ),
              OutlinedButton.icon(
                onPressed: _copy,
                style: OutlinedButton.styleFrom(minimumSize: const Size(0, 48), backgroundColor: AppColors.card),
                icon: const Icon(Icons.copy_rounded, size: 18),
                label: const Text('링크 복사'),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            keepAll('7일 안에 한 분만 들어올 수 있어요. 들어온 분은 이 링크로 언제든 다시 들어올 수 있어요.'),
            style: AppText.small.copyWith(color: AppColors.ink2),
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
            decoration: BoxDecoration(color: AppColors.card, borderRadius: BorderRadius.circular(12)),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('어르신께 보내실 때', style: AppText.label),
                const SizedBox(height: 4),
                Text(
                  keepAll(inviteTipText(prefilled: widget.prefilled)),
                  style: AppText.small.copyWith(color: AppColors.ink2, fontSize: 13.5),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
