import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_client.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';

/// 문의 코드로 회원 찾기(관리자, 서버 GET /admin/support/lookup).
/// 카카오톡 채널·이메일로 받은 문의의 첫 줄('문의 코드 K7M3-Q9XA · 대화 상대 3F2A9C · 앱 …')을
/// 그대로 붙여 넣으면 누구의 어느 대화 상대인지 보인다. 이메일은 가린 값, 대화 상대는 호칭만 보인다.
class SupportLookupCard extends StatefulWidget {
  /// 서버에 묻는다. 시험에서 바꿔 끼운다.
  final Future<Map<String, dynamic>> Function(String q) lookup;

  const SupportLookupCard({super.key, this.lookup = _lookup});

  static Future<Map<String, dynamic>> _lookup(String q) async =>
      Map<String, dynamic>.from(await ApiClient.get('/admin/support/lookup', query: {'q': q}));

  @override
  State<SupportLookupCard> createState() => _SupportLookupCardState();
}

class _SupportLookupCardState extends State<SupportLookupCard> {
  final _q = TextEditingController();
  Map<String, dynamic>? _result;
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _q.dispose();
    super.dispose();
  }

  Future<void> _find() async {
    final q = _q.text.trim();
    if (q.isEmpty || _busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await widget.lookup(q);
      if (mounted) setState(() => _result = result);
    } catch (error) {
      if (mounted) {
        setState(() {
          _result = null;
          _error = error is ApiException ? error.message : '찾지 못했어요. 잠시 뒤 다시 해 주세요.';
        });
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  static String _date(Object? iso) {
    final parsed = iso is String ? DateTime.tryParse(iso.endsWith('Z') || iso.contains('+') ? iso : '${iso}Z') : null;
    if (parsed == null) return '';
    final d = parsed.toLocal();
    return '${d.year}. ${d.month}. ${d.day}.';
  }

  @override
  Widget build(BuildContext context) {
    final result = _result;
    final users = [for (final u in result?['users'] as List? ?? const []) Map<String, dynamic>.from(u as Map)];
    return AppCard(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Row(
            children: [
              Icon(Icons.support_agent_outlined, color: AppColors.ink),
              SizedBox(width: 12),
              Expanded(child: Text('문의 코드로 회원 찾기', style: AppText.label)),
            ],
          ),
          const SizedBox(height: 4),
          const Text('카카오톡·이메일 문의의 첫 줄을 그대로 붙여 넣으세요', style: AppText.small),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _q,
                  decoration: const InputDecoration(hintText: '문의 코드 K7M3-Q9XA · 대화 상대 3F2A9C · 앱 …', isDense: true),
                  onSubmitted: (_) => _find(),
                ),
              ),
              const SizedBox(width: 8),
              FilledButton(
                onPressed: _busy ? null : _find,
                style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                child: Text(_busy ? '찾는 중…' : '찾기'),
              ),
            ],
          ),
          if (_error != null) ...[const SizedBox(height: 10), ErrorBox(_error!)],
          if (result != null) ...[
            const SizedBox(height: 12),
            Text(
              '코드 ${result['code']}${result['personaCode'] != null ? ' · 대화 상대 ${result['personaCode']}' : ''}',
              style: AppText.small,
            ),
            const SizedBox(height: 6),
            if (users.isEmpty)
              const Text('이 코드의 회원이 없어요. 코드를 다시 확인해 주세요.', style: AppText.lead)
            else
              for (final user in users) _user(user),
          ],
        ],
      ),
    );
  }

  Widget _user(Map<String, dynamic> user) {
    final personas = [for (final p in user['personas'] as List? ?? const []) Map<String, dynamic>.from(p as Map)];
    final providers = [for (final p in user['providers'] as List? ?? const []) '$p'].join('·');
    final facts = [
      if ((user['emailMasked'] ?? '').toString().isNotEmpty) '${user['emailMasked']}',
      providers.isEmpty ? '이메일 가입' : providers,
      '${user['status']}',
      '가입 ${_date(user['createdAt'])}',
    ].join(' · ');
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(color: AppColors.paper, borderRadius: BorderRadius.circular(12)),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('${user['name']}', style: AppText.label),
            const SizedBox(height: 2),
            Text(facts, style: AppText.small),
            SelectableText('회원 id ${user['id']}', style: AppText.small),
            const SizedBox(height: 6),
            if (personas.isEmpty)
              const Text('대화 상대 없음', style: AppText.small)
            else
              for (final p in personas)
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          '${p['matched'] == true ? '▶ ' : ''}${p['code']} · ${p['title']} · '
                          '${p['role'] == 'member' ? '함께 쓰는 가족' : '만든 분'} · ${p['step']}',
                          style: TextStyle(
                            fontSize: 13.5,
                            fontWeight: p['matched'] == true ? FontWeight.w800 : FontWeight.w500,
                            color: p['matched'] == true ? AppColors.primary : AppColors.ink2,
                          ),
                        ),
                      ),
                      // 유족 명단·이의 접수·멈춤·결제(대화 상대 관리)
                      if (p['id'] != null)
                        TextButton(
                          onPressed: () => context.go('/admin/personas/${p['id']}'),
                          child: const Text('관리'),
                        ),
                    ],
                  ),
                ),
          ],
        ),
      ),
    );
  }
}
