import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/session.dart';
import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/page_events.dart';
import '../../shared/widgets/ui.dart';
import '../billing/data/billing_api.dart';
import '../notices/notice_api.dart';
import '../notices/notices_page.dart';
import '../personas/data/persona_store.dart';
import '../personas/owner_actions.dart';
import '../share/roles.dart';

/// 로그인한 뒤의 바깥 틀.
/// PC(넓은 화면)는 메신저처럼 왼쪽에 페르소나 목록, 오른쪽에 고른 페르소나.
/// 모바일은 목록 → 페르소나로 한 화면씩 넘긴다(틀 없이 [child] 그대로).
class AppShell extends StatefulWidget {
  final String location;
  final Widget child;

  const AppShell({super.key, required this.location, required this.child});

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  void Function()? _stopListening;

  @override
  void initState() {
    super.initState();
    personaStore.load();
    billingStore.load();
    // 안 읽은 알림 수(종). 시간마다 묻지 않고 화면이 다시 보이거나 다른 화면으로 옮길 때만 읽는다
    noticeStore.load();
    _stopListening = PageEvents.listen((visible) {
      if (visible) noticeStore.load();
    });
  }

  @override
  void didUpdateWidget(covariant AppShell oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.location != widget.location) noticeStore.load();
  }

  @override
  void dispose() {
    _stopListening?.call();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!Breakpoints.isWide(context)) return widget.child;
    final selected = RegExp(r'^/p/([^/]+)').firstMatch(widget.location)?.group(1);
    return Scaffold(
      body: Row(
        children: [
          SizedBox(
            width: 300,
            child: Sidebar(selectedId: selected, creating: widget.location == '/new'),
          ),
          const VerticalDivider(width: 1, color: AppColors.line),
          Expanded(child: widget.child),
        ],
      ),
    );
  }
}

class Sidebar extends StatelessWidget {
  final String? selectedId;
  final bool creating;

  const Sidebar({super.key, this.selectedId, this.creating = false});

  @override
  Widget build(BuildContext context) {
    return ColoredBox(
      color: AppColors.card,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(22, 18, 10, 14),
            child: Row(
              children: [
                InkWell(onTap: () => context.go('/'), child: const BrandLogo(height: 19)),
                const Spacer(),
                const NoticeBell(),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 14),
            child: OutlinedButton.icon(
              onPressed: () => context.go('/new'),
              style: OutlinedButton.styleFrom(
                minimumSize: const Size(0, 46),
                backgroundColor: creating ? AppColors.primarySoft : AppColors.card,
              ),
              icon: const Icon(Icons.add_rounded, size: 20),
              label: const Text('새 대화 상대 만들기'),
            ),
          ),
          const SizedBox(height: 18),
          const Padding(padding: EdgeInsets.only(left: 12), child: SectionLabel('대화 상대')),
          Expanded(
            child: ListenableBuilder(
              listenable: personaStore,
              builder: (context, _) {
                final items = personaStore.items;
                if (items == null) {
                  return personaStore.error != null
                      ? Padding(padding: const EdgeInsets.all(14), child: ErrorBox(personaStore.error.toString()))
                      : const Loading();
                }
                if (items.isEmpty) {
                  return const Padding(
                    padding: EdgeInsets.symmetric(horizontal: 24, vertical: 8),
                    child: Text('아직 없어요. 위 버튼을 눌러 첫 대화 상대를 만들어 보세요.', style: AppText.small),
                  );
                }
                return ListView(
                  padding: const EdgeInsets.fromLTRB(10, 0, 10, 12),
                  children: [
                    for (final p in items)
                      PersonaTile(persona: p, selected: p['id'] == selectedId, dense: true),
                  ],
                );
              },
            ),
          ),
          const PlanBadge(),
          const Divider(height: 1),
          const AccountRow(),
        ],
      ),
    );
  }
}

/// 목록 한 줄: 이름, 마지막 말(또는 만들기 상태), 시각.
class PersonaTile extends StatelessWidget {
  final Map<String, dynamic> persona;
  final bool selected;
  final bool dense;

  const PersonaTile({super.key, required this.persona, this.selected = false, this.dense = false});

  @override
  Widget build(BuildContext context) {
    final p = persona;
    final ready = p['step'] == 'READY';
    final last = p['lastMessage'] as String?;
    final title = personaTitle(p);
    // 호칭과 관계가 다를 때만 관계를 곁들인다(엄마 · 어머니). 성함은 보이지 않는다.
    final relation = (p['relation'] as String?)?.trim() ?? '';
    final shownRelation = relation.isNotEmpty && relation != title ? relation : '';
    // 가족이 나눠 준 대화 상대는 '함께', 내가 만들어 가족과 함께 쓰면 '가족 n명'
    final member = isMember(p);
    final members = member ? 0 : membersOf(p);
    // 만든 분이 할 일이 있거나(그분 동의 녹음 등) 멈춘 대화 상대
    final chip = member ? null : ownerChipOf(p);
    final subtitle = !ready
        ? null
        : last != null
            ? '${p['lastMessageRole'] == 'user' ? '나: ' : ''}$last'
            : '첫 인사를 건네 보세요';
    return Padding(
      padding: const EdgeInsets.only(bottom: 2),
      child: Material(
        color: selected ? AppColors.primarySoft : Colors.transparent,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: () => context.go('/p/${p['id']}'),
          child: Padding(
            padding: EdgeInsets.symmetric(horizontal: 12, vertical: dense ? 11 : 14),
            child: Row(
              children: [
                Avatar(title, size: dense ? 42 : 48),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: Text(
                              title,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(fontSize: dense ? 15.5 : 17, fontWeight: FontWeight.w700),
                            ),
                          ),
                          if (member) ...[
                            const SizedBox(width: 6),
                            const StatusChip('함께'),
                          ] else if (shownRelation.isNotEmpty) ...[
                            const SizedBox(width: 6),
                            Text(shownRelation, style: AppText.small.copyWith(fontSize: 12.5)),
                          ],
                          if (members > 0) ...[
                            const SizedBox(width: 6),
                            const Icon(Icons.group_outlined, size: 14, color: AppColors.muted),
                            const SizedBox(width: 2),
                            Text('가족 $members명', style: AppText.small.copyWith(fontSize: 12)),
                          ],
                          if (chip != null) ...[
                            const SizedBox(width: 6),
                            Flexible(child: StatusChip(chip)),
                          ],
                        ],
                      ),
                      const SizedBox(height: 3),
                      if (subtitle != null)
                        Text(
                          subtitle,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(fontSize: 13.5, color: AppColors.ink2),
                        )
                      else
                        StatusChip(stepLabel[p['step']] ?? '', busy: p['step'] == 'BUILDING'),
                    ],
                  ),
                ),
                if (ready && p['lastMessageAt'] != null) ...[
                  const SizedBox(width: 8),
                  Text(shortTime(p['lastMessageAt'] as String?), style: AppText.small.copyWith(fontSize: 12)),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 왼쪽 아래 내 계정 줄.
class AccountRow extends StatelessWidget {
  const AccountRow({super.key});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 8, 14),
      child: Row(
        children: [
          Avatar(session.name, size: 36),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(session.name, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w700)),
                Text(accountLabel(), overflow: TextOverflow.ellipsis, style: AppText.small.copyWith(fontSize: 12)),
              ],
            ),
          ),
          const AccountMenuButton(),
        ],
      ),
    );
  }
}

String accountLabel() => switch (session.provider) {
      'kakao' => '카카오 계정',
      'google' => session.email ?? 'Google 계정',
      _ => session.email ?? '',
    };

class AccountMenuButton extends StatelessWidget {
  final Widget? icon;

  const AccountMenuButton({super.key, this.icon});

  @override
  Widget build(BuildContext context) {
    return PopupMenuButton<String>(
      tooltip: '내 계정',
      icon: icon ?? const Icon(Icons.more_horiz_rounded, color: AppColors.ink2),
      onSelected: (v) async {
        if (v == 'admin') context.go('/admin/stats');
        if (v == 'plans') context.go('/plans');
        if (v == 'account') context.go('/account');
        if (v == 'support') context.go('/support');
        if (v == 'logout' &&
            await confirmAction(
              context,
              '로그아웃할까요?',
              // 서버가 이 계정의 로그인을 모두 끝낸다(휴대폰·컴퓨터 모두 다시 로그인해야 한다)
              '이 계정으로 로그인한 모든 기기(휴대폰·컴퓨터)에서 로그아웃돼요.',
              action: '로그아웃',
              danger: false,
            )) {
          await session.logout();
        }
      },
      itemBuilder: (_) => [
        PopupMenuItem(
          enabled: false,
          child: Text('${session.name} · ${accountLabel()}', style: AppText.small),
        ),
        const PopupMenuItem(value: 'plans', child: Text('요금제·결제')),
        const PopupMenuItem(value: 'account', child: Text('내 계정·동의')),
        const PopupMenuItem(value: 'support', child: Text('고객센터')),
        if (session.isAdmin) const PopupMenuItem(value: 'admin', child: Text('개발자 화면')),
        const PopupMenuItem(value: 'logout', child: Text('로그아웃')),
      ],
    );
  }
}


/// 페르소나마다의 요금제 가운데 알릴 것 하나(결제 실패 → 요금제가 필요함 → 체험 끝나 감 → 체험 중)를
/// 작게 보인다. 누르면 그 페르소나의 요금제로.
class PlanBadge extends StatelessWidget {
  final bool compact;

  const PlanBadge({super.key, this.compact = false});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([billingStore, personaStore]),
      builder: (context, _) {
        final me = billingStore.me;
        if (me == null || me['admin'] == true) return const SizedBox.shrink();
        // 서버도 내가 만든 대화 상대만 주지만, 가족이 나눠 준 대화 상대는 한 번 더 뺀다(요금제는 만든 분 몫)
        final views = [
          for (final p in billingStore.personas)
            if (p['step'] == 'READY' && p['role'] != 'member' && !isMember(personaStore.byId('${p['personaId']}'))) p,
        ];
        Map<String, dynamic>? pick(bool Function(Map<String, dynamic>) test) =>
            views.where(test).firstOrNull;
        final pastDue = pick((v) => v['status'] == 'PAST_DUE');
        final needsPlan = pick((v) => v['active'] == false);
        final trials = views.where((v) => v['status'] == 'TRIAL').toList()
          ..sort((a, b) => (BillingStore.trialDaysLeft(a) ?? 99).compareTo(BillingStore.trialDaysLeft(b) ?? 99));
        final trial = trials.firstOrNull;
        String who(Map<String, dynamic> v) => personaLabel(v['title'], v['relation']);
        // 결제가 열리기 전: 무료로 쓰는 중(구독 신청해 둔 대화 상대가 있으면 그 수)
        final requests = billingStore.requestCount;
        final (String text, bool warn, String? personaId) = pastDue != null
            ? ('${who(pastDue)} 결제가 되지 않았어요 · 카드를 확인해 주세요', true, pastDue['personaId'] as String)
            : billingStore.prelaunch
                ? (requests > 0 ? '구독 신청 $requests명' : '결제 전 무료로 쓰는 중', false, null)
            : needsPlan != null
                ? ('${who(needsPlan)} 요금제를 골라 주세요', true, needsPlan['personaId'] as String)
                : trial != null
                    ? (
                        '${who(trial)} 무료 체험 ${BillingStore.trialDaysLeft(trial) ?? ''}일 남음',
                        (BillingStore.trialDaysLeft(trial) ?? 9) <= 2,
                        trial['personaId'] as String,
                      )
                    : ('', false, null);
        if (text.isEmpty) return const SizedBox.shrink();
        final color = warn ? AppColors.clay : AppColors.primary;
        return Padding(
          padding: EdgeInsets.fromLTRB(compact ? 0 : 14, 0, compact ? 0 : 14, compact ? 0 : 12),
          child: Material(
            color: warn ? AppColors.claySoft : AppColors.primarySoft,
            borderRadius: BorderRadius.circular(12),
            child: InkWell(
              borderRadius: BorderRadius.circular(12),
              onTap: () => context.go(personaId == null ? '/plans' : '/plans?persona=$personaId'),
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: Row(
                  children: [
                    Icon(warn ? Icons.error_outline_rounded : Icons.card_giftcard_rounded, size: 18, color: color),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(text, style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700, color: color)),
                    ),
                    Icon(Icons.chevron_right_rounded, size: 18, color: color),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}

