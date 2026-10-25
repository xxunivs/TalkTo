import 'package:flutter/material.dart';

import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import 'legal_text.dart';

/// 동의 한 줄: 체크 + 글 + '보기'(문서가 있으면). 미리 체크하지 않는다(사전 선택 금지).
/// [tag] 는 '필수'·'선택' 같은 앞 표시(없으면 생략).
class ConsentCheck extends StatelessWidget {
  final String label;
  final bool value;
  final ValueChanged<bool> onChanged;
  final String? docKey;
  final String? callName;
  final String? tag;
  final bool enabled;

  const ConsentCheck({
    super.key,
    required this.label,
    required this.value,
    required this.onChanged,
    this.docKey,
    this.callName,
    this.tag,
    this.enabled = true,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Checkbox(value: value, onChanged: enabled ? (v) => onChanged(v ?? false) : null),
          Expanded(
            child: InkWell(
              borderRadius: BorderRadius.circular(8),
              onTap: enabled ? () => onChanged(!value) : null,
              child: Padding(
                padding: const EdgeInsets.only(top: 12, bottom: 10, right: 4),
                child: Text.rich(
                  TextSpan(
                    children: [
                      if (tag != null)
                        TextSpan(
                          text: '[$tag] ',
                          style: TextStyle(
                            fontWeight: FontWeight.w700,
                            color: tag == '필수' ? AppColors.clay : AppColors.muted,
                          ),
                        ),
                      TextSpan(text: keepAll(label)),
                    ],
                  ),
                  style: AppText.body.copyWith(fontSize: 14.5, height: 1.45),
                ),
              ),
            ),
          ),
          if (docKey != null)
            TextButton(
              onPressed: () => showLegalDoc(context, docKey!, callName: callName),
              child: const Text('보기'),
            ),
        ],
      ),
    );
  }
}

/// 계정 필수 동의 묶음: 맨 위 '모두 동의해요' 한 줄 + 항목마다 [ConsentCheck].
/// [items] 는 서버 GET /legal 의 account([{type, label, doc, kind}]) 순서 그대로.
/// '모두 동의해요'는 한 번에 켜고 끄는 것일 뿐, 항목은 하나씩 따로 바꿀 수 있다. 미리 체크하지 않는다.
class ConsentGroup extends StatelessWidget {
  final List<Map<String, dynamic>> items;
  final Set<String> checked;
  final ValueChanged<Set<String>> onChanged;
  final bool enabled;

  const ConsentGroup({
    super.key,
    required this.items,
    required this.checked,
    required this.onChanged,
    this.enabled = true,
  });

  static String typeOf(Map<String, dynamic> item) => item['type'].toString();

  /// 항목이 있고 모두 체크했는지.
  static bool allChecked(List<Map<String, dynamic>> items, Set<String> checked) =>
      items.isNotEmpty && items.every((item) => checked.contains(typeOf(item)));

  @override
  Widget build(BuildContext context) {
    final all = allChecked(items, checked);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Material(
          color: all ? AppColors.primarySoft : AppColors.sunk,
          borderRadius: BorderRadius.circular(14),
          child: InkWell(
            borderRadius: BorderRadius.circular(14),
            onTap: enabled ? () => _setAll(!all) : null,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(4, 4, 12, 4),
              child: Row(
                children: [
                  Checkbox(value: all, onChanged: enabled ? (v) => _setAll(v ?? false) : null),
                  const Expanded(
                    child: Text('모두 동의해요', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                  ),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(height: 6),
        for (final item in items)
          ConsentCheck(
            label: (item['label'] ?? '').toString(),
            value: checked.contains(typeOf(item)),
            onChanged: (v) => _set(typeOf(item), v),
            docKey: item['doc'] as String?,
            tag: '필수',
            enabled: enabled,
          ),
      ],
    );
  }

  void _setAll(bool on) => onChanged(on ? {for (final item in items) typeOf(item)} : <String>{});

  void _set(String type, bool on) {
    final next = {...checked};
    if (on) {
      next.add(type);
    } else {
      next.remove(type);
    }
    onChanged(next);
  }
}
