import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import '../personas/data/persona_store.dart' show shortTime;
import 'notice_api.dart';

/// 알림(/notices). 그분 동의 녹음 확인 결과·멈춤·나이가 바뀌는 달·결제 내용 같은 TalkTo 의 안내를 모아 본다.
/// 누르면 읽음으로 하고, 할 일이 있으면 그 화면으로 간다. 푸시는 보내지 않는다(앱을 열면 여기서 본다).
class NoticesPage extends StatefulWidget {
  const NoticesPage({super.key});

  @override
  State<NoticesPage> createState() => _NoticesPageState();
}

class _NoticesPageState extends State<NoticesPage> {
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    noticeStore.load();
  }

  Future<void> _open(Map<String, dynamic> notice) async {
    await noticeStore.markRead(notice);
    final action = notice['action'];
    final route = action is Map ? '${action['route'] ?? ''}'.trim() : '';
    // 앱 안 주소만 연다(알림 글에 다른 사이트 주소가 와도 따라가지 않는다)
    if (route.startsWith('/') && !route.startsWith('//') && mounted) context.go(route);
  }

  Future<void> _readAll() async {
    setState(() => _busy = true);
    try {
      await noticeStore.markAllRead();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final wide = Breakpoints.isWide(context);
    return Scaffold(
      appBar: AppBar(
        automaticallyImplyLeading: !wide,
        title: const Text('알림'),
        actions: [
          ListenableBuilder(
            listenable: noticeStore,
            builder: (context, _) => noticeStore.unread == 0
                ? const SizedBox.shrink()
                : TextButton(onPressed: _busy ? null : _readAll, child: const Text('모두 읽음')),
          ),
          SizedBox(width: wide ? 12 : 4),
        ],
      ),
      body: ListenableBuilder(
        listenable: noticeStore,
        builder: (context, _) {
          final items = noticeStore.items;
          if (items == null) {
            return noticeStore.error != null
                ? LoadFailed(noticeStore.error!, onRetry: noticeStore.load)
                : const Loading();
          }
          return RefreshIndicator(
            color: AppColors.primary,
            onRefresh: noticeStore.load,
            child: ListView(
              padding: EdgeInsets.fromLTRB(20, wide ? 24 : 8, 20, 40),
              children: [
                PageBody(
                  child: items.isEmpty
                      ? const Padding(
                          padding: EdgeInsets.symmetric(vertical: 40),
                          child: Center(child: Text('아직 알림이 없어요.', style: AppText.small)),
                        )
                      : Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            for (final n in items)
                              Padding(padding: const EdgeInsets.only(bottom: 10), child: _row(n)),
                          ],
                        ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _row(Map<String, dynamic> n) {
    final unread = n['readAt'] == null;
    final action = n['action'];
    final label = action is Map ? '${action['label'] ?? ''}'.trim() : '';
    final route = action is Map ? '${action['route'] ?? ''}'.trim() : '';
    return AppCard(
      padding: const EdgeInsets.fromLTRB(18, 14, 14, 14),
      color: unread ? AppColors.card : AppColors.paper,
      borderColor: unread ? AppColors.primary.withValues(alpha: 0.35) : null,
      onTap: () => _open(n),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 6, right: 10),
            child: Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(
                color: unread ? AppColors.primary : Colors.transparent,
                shape: BoxShape.circle,
              ),
            ),
          ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Text(
                        keepAll('${n['title'] ?? ''}'),
                        style: TextStyle(fontSize: 15.5, fontWeight: unread ? FontWeight.w700 : FontWeight.w600),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Text(shortTime(n['createdAt'] as String?), style: AppText.small.copyWith(fontSize: 12)),
                  ],
                ),
                const SizedBox(height: 4),
                Text(keepAll('${n['body'] ?? ''}'), style: AppText.small.copyWith(color: AppColors.ink2, height: 1.55)),
                if (route.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  Text(
                    label.isEmpty ? '자세히 보기' : label,
                    style: const TextStyle(color: AppColors.primary, fontWeight: FontWeight.w700, fontSize: 14),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 앱 셸 위의 종. 안 읽은 알림 수를 작게 보이고, 누르면 알림 화면으로.
class NoticeBell extends StatelessWidget {
  const NoticeBell({super.key});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: noticeStore,
      builder: (context, _) {
        final unread = noticeStore.unread;
        return IconButton(
          tooltip: unread > 0 ? '알림 $unread개' : '알림',
          onPressed: () => context.go('/notices'),
          icon: Badge(
            isLabelVisible: unread > 0,
            label: Text(unread > 99 ? '99+' : '$unread'),
            backgroundColor: AppColors.clay,
            child: const Icon(Icons.notifications_none_rounded, color: AppColors.ink2),
          ),
        );
      },
    );
  }
}
