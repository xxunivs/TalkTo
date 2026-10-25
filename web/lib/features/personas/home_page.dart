import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/session.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import '../legal/legal_footer.dart';
import '../notices/notices_page.dart';
import '../share/roles.dart';
import '../shell/app_shell.dart';
import 'data/persona_store.dart';
import 'owner_actions.dart';
import '../../shared/korean.dart';

/// 첫 화면. 모바일은 페르소나 목록, PC 는 왼쪽에 목록이 있으니 인사와 안내만.
class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    final wide = Breakpoints.isWide(context);
    return Scaffold(
      appBar: wide
          ? null
          : AppBar(
              titleSpacing: 20,
              title: const BrandLogo(height: 18),
              actions: [
                const NoticeBell(),
                AccountMenuButton(
                  icon: Avatar(session.name, size: 32),
                ),
                const SizedBox(width: 8),
              ],
            ),
      body: ListenableBuilder(
        listenable: personaStore,
        builder: (context, _) {
          final items = personaStore.items;
          if (items == null) {
            if (personaStore.error == null) return const Loading();
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    ErrorBox(personaStore.error.toString()),
                    const SizedBox(height: 12),
                    OutlinedButton(onPressed: personaStore.load, child: const Text('다시 불러오기')),
                  ],
                ),
              ),
            );
          }
          return RefreshIndicator(
            color: AppColors.primary,
            onRefresh: personaStore.load,
            child: ListView(
              padding: EdgeInsets.fromLTRB(20, wide ? 72 : 12, 20, 48),
              children: [
                PageBody(
                  maxWidth: 600,
                  child: items.isEmpty
                      ? const _Welcome()
                      : wide
                          ? _WideGreeting(items: items)
                          : _List(items: items),
                ),
                // 로그인 뒤 첫 화면 아래: 개인정보 처리방침과 사업자 정보(글 그대로)
                const SizedBox(height: 40),
                const PageBody(maxWidth: 600, child: LegalFooter()),
              ],
            ),
          );
        },
      ),
    );
  }
}

class _List extends StatelessWidget {
  final List<Map<String, dynamic>> items;

  const _List({required this.items});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('${session.name}님,\n오늘은 누구와 이야기할까요?', style: AppText.display.copyWith(fontSize: 24)),
        const SizedBox(height: 16),
        const PlanBadge(compact: true),
        const SizedBox(height: 16),
        AppCard(
          padding: const EdgeInsets.all(6),
          child: Column(children: [for (final p in items) PersonaTile(persona: p)]),
        ),
        const SizedBox(height: 16),
        OutlinedButton.icon(
          onPressed: () => context.go('/new'),
          style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(52)),
          icon: const Icon(Icons.add_rounded),
          label: const Text('새 대화 상대 만들기'),
        ),
      ],
    );
  }
}

class _WideGreeting extends StatelessWidget {
  final List<Map<String, dynamic>> items;

  const _WideGreeting({required this.items});

  @override
  Widget build(BuildContext context) {
    // 바로 이어 할 것: 확인이 필요한 분, 가장 최근에 대화한 분, 그리고 만들다 만 페르소나
    final waiting = items.where((p) => p['step'] == 'READY' && !isMember(p) && ownerChipOf(p) != null).take(2);
    final recent = items
        .where((p) => p['step'] == 'READY' && p['lastMessage'] != null && !waiting.contains(p))
        .take(1);
    // 가족이 나눠 준 대화 상대는 만들기를 이어 가지 않는다(만든 분 몫)
    final unfinished = items.where((p) => p['step'] != 'READY' && !isMember(p)).take(2);
    return Column(
      children: [
        const Breathing(size: 64),
        const SizedBox(height: 22),
        Text('${session.name}님, 반가워요', textAlign: TextAlign.center, style: AppText.display),
        const SizedBox(height: 10),
        const Text(
          '왼쪽에서 이야기할 분을 고르거나,\n새 대화 상대를 만들어 보세요.',
          textAlign: TextAlign.center,
          style: AppText.lead,
        ),
        if (waiting.isNotEmpty || recent.isNotEmpty || unfinished.isNotEmpty) ...[
          const SizedBox(height: 36),
          for (final p in waiting)
            _ContinueCard(
              persona: p,
              label: '확인하기',
              detail: holdOf(p) != null ? '이 대화 상대를 멈췄어요' : '먼저 확인할 것이 있어요',
            ),
          for (final p in recent)
            _ContinueCard(
              persona: p,
              label: '이어서 이야기하기',
              detail: '“${p['lastMessage']}”',
            ),
          for (final p in unfinished)
            _ContinueCard(
              persona: p,
              label: '이어서 만들기',
              detail: stepLabel[p['step']] ?? '',
            ),
        ],
      ],
    );
  }
}

class _ContinueCard extends StatelessWidget {
  final Map<String, dynamic> persona;
  final String label;
  final String detail;

  const _ContinueCard({required this.persona, required this.label, required this.detail});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: AppCard(
        padding: const EdgeInsets.fromLTRB(18, 16, 14, 16),
        onTap: () => context.go('/p/${persona['id']}'),
        child: Row(
          children: [
            Avatar(personaTitle(persona), size: 44),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Flexible(child: Text(personaTitle(persona), style: AppText.heading.copyWith(fontSize: 16))),
                      if (!isMember(persona) && ownerChipOf(persona) != null) ...[
                        const SizedBox(width: 6),
                        StatusChip(ownerChipOf(persona)!),
                      ],
                    ],
                  ),
                  const SizedBox(height: 2),
                  Text(detail, maxLines: 1, overflow: TextOverflow.ellipsis, style: AppText.small),
                ],
              ),
            ),
            const SizedBox(width: 12),
            Text(label, style: const TextStyle(color: AppColors.primary, fontWeight: FontWeight.w700)),
            const Icon(Icons.chevron_right_rounded, color: AppColors.primary),
          ],
        ),
      ),
    );
  }
}

/// 아직 페르소나가 없을 때: 무엇을 하게 되는지 세 단계로 보여주고 바로 시작.
class _Welcome extends StatelessWidget {
  const _Welcome();

  static const _steps = [
    (Icons.graphic_eq_rounded, '녹음 올리기', '그분 목소리가 담긴 통화 녹음이나 가족 영상을 올려요.'),
    (Icons.record_voice_over_outlined, '목소리 고르기', '녹음 속 여러 목소리 가운데 그분을 골라요.'),
    (Icons.edit_note_rounded, '질문 답하기', '그분 이야기를 아는 만큼 알려 주세요.'),
  ];

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '다시 듣고 싶은 목소리와\n이야기를 나눠 보세요',
          style: Breakpoints.isWide(context) ? AppText.display : AppText.display.copyWith(fontSize: 25),
        ),
        const SizedBox(height: 12),
        const Text(
          '녹음 몇 개와 질문 몇 가지면, 그분 말투와 목소리로 이야기하는 대화 상대가 만들어져요. 15분쯤 걸려요.',
          style: AppText.lead,
        ),
        const SizedBox(height: 26),
        AppCard(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 8),
          child: Column(
            children: [
              for (var i = 0; i < _steps.length; i++) ...[
                if (i > 0) const Divider(height: 1),
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 16),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Container(
                        width: 40,
                        height: 40,
                        decoration: const BoxDecoration(color: AppColors.primarySoft, shape: BoxShape.circle),
                        child: Icon(_steps[i].$1, color: AppColors.primary, size: 21),
                      ),
                      const SizedBox(width: 14),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text('${i + 1}. ${_steps[i].$2}', style: AppText.label.copyWith(fontSize: 15.5)),
                            const SizedBox(height: 3),
                            Text(_steps[i].$3, style: AppText.small.copyWith(color: AppColors.ink2, fontSize: 14)),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 22),
        SizedBox(
          width: double.infinity,
          child: FilledButton(onPressed: () => context.go('/new'), child: const Text('첫 대화 상대 만들기')),
        ),
        const SizedBox(height: 12),
        const Center(
          child: Text('올린 녹음은 대화 상대를 만드는 데만 써요.', style: AppText.small),
        ),
      ],
    );
  }
}
