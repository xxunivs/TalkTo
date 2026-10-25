import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import 'legal_admin_api.dart';
import 'legal_admin_common.dart';

/// 관리자 화면 · 대화 상대 목록 두 가지(관리자만). 줄을 누르면 대화 상대 관리(/admin/personas/:id).
///
///   /admin/holds    멈춘 대화 상대(이의·동의 녹음 거절·아이 동의 기한 지남 등). 지우는 날이 가까운 것부터
///   /admin/unable   처리를 멈춘 예전 대화 상대 — '대화가 어려우신 분'과 부모가 아닌 분이 만든 아이의 대화 상대.
///                   지우지 않았고 대화는 그대로다. 처리 방법은 대표가 정하므로 여기에는 단추가 없다.

class HoldsAdminPage extends StatefulWidget {
  final LegalAdminApi api;

  const HoldsAdminPage({super.key, this.api = const LegalAdminApi()});

  @override
  State<HoldsAdminPage> createState() => _HoldsAdminPageState();
}

class _HoldsAdminPageState extends State<HoldsAdminPage> {
  List<Map<String, dynamic>>? _rows;
  Object? _error;

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
      final rows = await widget.api.holds();
      rows.sort((a, b) => '${a['deleteAfter'] ?? ''}'.compareTo('${b['deleteAfter'] ?? ''}'));
      if (mounted) setState(() => _rows = rows);
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    return AdminFrame(
      title: '멈춘 대화 상대',
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Notice('지우는 때가 지나면 10분 안에 자동으로 지워요(한 번에 3개까지). 잘못 멈췄으면 줄을 눌러 해제해 주세요.'),
          const SizedBox(height: 14),
          if (_error != null || rows == null || rows.isEmpty)
            adminListState(
              error: _error,
              loading: rows == null,
              empty: rows?.isEmpty ?? false,
              emptyText: '멈춘 대화 상대가 없어요.',
              onRetry: _load,
            )
          else
            for (final row in rows)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: _PersonaRow(
                  personaId: '${row['personaId']}',
                  title: '${row['title'] ?? '(이름 없음)'}',
                  lines: [
                    [
                      holdReasonLabels[row['reason']] ?? '${row['reason'] ?? ''}',
                      if ('${row['ownerMasked'] ?? ''}'.isNotEmpty) '${row['ownerMasked']}',
                    ].join(' · '),
                    '멈춘 때 ${adminDate(row['holdAt'])} · 지우는 때 ${adminDate(row['deleteAfter'])}',
                  ],
                  chip: '멈춤',
                ),
              ),
        ],
      ),
    );
  }
}

class UnableListPage extends StatefulWidget {
  final LegalAdminApi api;

  const UnableListPage({super.key, this.api = const LegalAdminApi()});

  @override
  State<UnableListPage> createState() => _UnableListPageState();
}

class _UnableListPageState extends State<UnableListPage> {
  List<Map<String, dynamic>>? _rows;
  Object? _error;

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
      final rows = await widget.api.unable();
      if (mounted) setState(() => _rows = rows);
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    return AdminFrame(
      title: '처리를 멈춘 예전 대화 상대',
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Notice(
            "'대화가 어려우신 분'은 2026-10-22부터 새로 만들지 않아요. 이미 만든 것은 지우지 않고 대화도 그대로이며, "
            '새 녹음 처리와 목소리 다시 만들기만 멈췄어요. 부모가 아닌 분이 만든 아이의 대화 상대도 같아요. '
            '처리 방법은 대표가 정해요.',
          ),
          const SizedBox(height: 14),
          if (_error != null || rows == null || rows.isEmpty)
            adminListState(
              error: _error,
              loading: rows == null,
              empty: rows?.isEmpty ?? false,
              emptyText: '해당하는 대화 상대가 없어요.',
              onRetry: _load,
            )
          else ...[
            Text('${rows.length}개', style: AppText.small),
            const SizedBox(height: 8),
            for (final row in rows)
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: _PersonaRow(
                  personaId: '${row['personaId']}',
                  title: '${row['title'] ?? '(이름 없음)'}',
                  lines: [
                    [
                      unableKindLabels[row['kind'] ?? 'unable'] ?? '${row['kind']}',
                      if ('${row['ownerMasked'] ?? ''}'.isNotEmpty) '${row['ownerMasked']}',
                      '${row['step'] ?? ''}',
                    ].where((s) => s.isNotEmpty).join(' · '),
                    [
                      row['hasVoice'] == true
                          ? '목소리 있음${row['voiceProvider'] != null ? '(${row['voiceProvider']})' : ''}'
                          : '목소리 없음',
                      '함께 쓰는 가족 ${row['members'] ?? 0}명',
                    ].join(' · '),
                    [
                      '만든 날 ${adminDate(row['createdAt'])}',
                      if (row['readyAt'] != null) '다 만든 날 ${adminDate(row['readyAt'])}',
                      if (row['lastChatAt'] != null) '마지막 대화 ${adminDate(row['lastChatAt'])}',
                    ].join(' · '),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }
}

/// 대화 상대 한 줄(누르면 대화 상대 관리).
class _PersonaRow extends StatelessWidget {
  final String personaId;
  final String title;
  final List<String> lines;
  final String? chip;

  const _PersonaRow({required this.personaId, required this.title, required this.lines, this.chip});

  @override
  Widget build(BuildContext context) {
    return AppCard(
      padding: const EdgeInsets.fromLTRB(18, 14, 12, 14),
      onTap: () => context.go('/admin/personas/$personaId'),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: AppText.label),
                const SizedBox(height: 4),
                for (final line in lines)
                  if (line.isNotEmpty) Text(line, style: AppText.small),
              ],
            ),
          ),
          if (chip != null) StatusChip(chip!),
          const Icon(Icons.chevron_right_rounded, color: AppColors.muted),
        ],
      ),
    );
  }
}
