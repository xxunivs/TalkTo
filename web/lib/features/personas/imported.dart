import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/ui.dart';
import '../share/roles.dart';
import 'owner_actions.dart';

/// 관리자가 예전 자료로 가져온 대화 상대인가(서버 상세의 origin == 'import').
///
/// 가져온 대화 상대는 예전 지침을 그대로 쓴다. 지침을 다시 만드는 기능(정보 고치기·기억 더 채우기의
/// 질문·녹음 더하기·목소리 다시 만들기)을 쓰면 그 지침을 덮어쓰게 되어 화면에서 숨기고, 서버도 막는다(409).
/// 종교·건강 이야기 동의는 그대로 둔다(질문 칸 없이 동의 단추만, [ImportedSensitivePanel]).
bool isImportedPersona(Map? persona) => persona?['origin'] == 'import';

/// 가져온 대화 상대에 주인 확인을 기다리는 목소리가 있다(대화 화면에서 확인하면 그때 붙는다).
bool hasPendingVoice(Map? persona) => isImportedPersona(persona) && persona?['voicePending'] == true;

/// 민감한 범주(종교·건강)별 동의 종류·상세의 consents 이름·화면 이름. 기억 더 채우기와 같이 쓴다.
const sensitiveCategories = {
  'religion': (type: 'sensitive_religion', key: 'sensitiveReligion', label: '종교'),
  'health': (type: 'sensitive_health', key: 'sensitiveHealth', label: '건강'),
};

/// 대화 화면 정보 시트에 보일 관리 단추. more 는 /p/:id/more 단추 글, voice 는 목소리 단추 글(null 이면 숨김).
/// share = 가족과 함께 쓰기(만든 사람), leave·profile = 함께 쓰기 그만두기·나에 대한 정보 고치기(함께하는 가족).
typedef PersonaActions = ({
  bool edit,
  String? more,
  bool recordings,
  String? voice,
  bool share,
  bool leave,
  bool profile,
});

PersonaActions personaActionsOf(Map persona, {required bool hasVoice}) {
  if (isMember(persona)) {
    // 초대로 함께하는 가족: 대화·듣기 말고는 내 정보 고치기와 그만두기만
    return (
      edit: false,
      more: null,
      recordings: false,
      voice: null,
      share: false,
      leave: true,
      profile: true,
    );
  }
  if (!isImportedPersona(persona)) {
    // 그분 동의 녹음 전·법정대리인 확인 전·멈춤·예전 '대화가 어려우신 분'이면 녹음 더하기와 목소리
    // (다시) 만들기를 숨긴다(서버의 gates, 서버도 막는다)
    final gates = PersonaGates.of(persona);
    final frozen = (persona['basics'] as Map?)?['status'] == 'unable';
    return (
      edit: true,
      more: '기억 더 채우기 (더 그분답게)',
      recordings: gates.audio && !frozen,
      voice: !gates.voice || frozen ? null : (hasVoice ? '목소리 다시 만들기' : '목소리 만들기'),
      share: true,
      leave: false,
      profile: false,
    );
  }
  // 가져온 대화 상대: 기다리는 목소리를 붙이는 것과 종교·건강 이야기 동의만 된다
  // (새로 만들거나 다시 만들지 않는다. 같은 주소가 질문 칸 없이 동의 단추만 보인다). 가족과 함께 쓰기는 된다
  return (
    edit: false,
    more: '종교·건강 이야기 동의',
    recordings: false,
    voice: !hasVoice && hasPendingVoice(persona) && PersonaGates.of(persona).voice ? '목소리 붙이기' : null,
    share: true,
    leave: false,
    profile: false,
  );
}

/// 가져온 대화 상대에서 동의 화면에 보일 범주. 서버가 sensitiveMemories 를 주면 그 분류 기억이 있는
/// 범주만, 주지 않으면(예전 서버) 둘 다.
List<String> importedSensitiveCategories(Map? detail) {
  final states = detail?['sensitiveMemories'];
  if (states is! Map) return sensitiveCategories.keys.toList();
  return [
    for (final c in sensitiveCategories.keys)
      if (states[c] != null && states[c] != 'none') c,
  ];
}

/// 가져온 기억 중 이 범주(religion·health)를 대화에 쓰는가: 'on' | 'off' | null(모름).
///
/// 가져온 종교·건강 기억은 동의 전에도 켜진 채로 들어온다(D6). 동의를 거두면 서버가 그 기억을 끄고
/// (INACTIVE), 다시 동의하면 켠다. 그래서 consents 만으로는 '아직 고르지 않음(켜짐)'과 '거둠(꺼짐)'을
/// 가를 수 없어, 서버 상세의 sensitiveMemories {religion: 'on'|'off', health: …} 를 먼저 본다.
/// 없으면 이 화면에서 방금 고른 값([known]), 그다음 동의했으면 켜짐(동의하면 서버가 켠다), 아니면 모름.
String? importedSensitiveState(Map? detail, String category, {String? known}) {
  final states = detail?['sensitiveMemories'];
  if (states is Map) {
    final value = states[category];
    if (value == 'on' || value == true) return 'on';
    if (value == 'off' || value == false) return 'off';
  }
  if (known != null) return known;
  final consents = detail?['consents'];
  if (consents is Map && consents[sensitiveCategories[category]!.key] == true) return 'on';
  return null;
}

/// 가져온 대화 상대의 종교·건강 이야기 동의(기억 더 채우기 자리, 질문 칸 없이 동의 단추만).
///
/// [onChoose] 는 (범주, 동의) 로 부른다: 동의하면 그 기억을 대화에 쓰고, 거두면 쓰지 않는다(지우지 않는다).
class ImportedSensitivePanel extends StatelessWidget {
  final Map detail;
  final bool busy;

  /// 이 화면에서 방금 고른 값(서버가 sensitiveMemories 를 주지 않을 때 쓴다). 범주 → 'on'|'off'.
  final Map<String, String> known;
  final void Function(String category, bool agreed) onChoose;
  final void Function(String category)? onDetails;

  const ImportedSensitivePanel({
    super.key,
    required this.detail,
    required this.onChoose,
    this.busy = false,
    this.known = const {},
    this.onDetails,
  });

  bool _consented(String category) =>
      (detail['consents'] as Map?)?[sensitiveCategories[category]!.key] == true;

  @override
  Widget build(BuildContext context) {
    final categories = importedSensitiveCategories(detail);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (categories.isEmpty)
          const Notice('예전에 모아 둔 이야기에 종교·건강에 관한 기억은 없어요.')
        else
          for (final c in categories)
            Padding(padding: const EdgeInsets.only(bottom: 12), child: _card(c)),
        const SizedBox(height: 4),
        const Notice(
          '동의를 거둬도 그 기억을 지우지 않아요. 다시 동의하면 그대로 돌아와요. '
          '처음부터 알고 있던 기본 정보 속 짧은 내용은 그대로 남아요.',
          icon: Icons.lightbulb_outline_rounded,
        ),
      ],
    );
  }

  Widget _card(String category) {
    final label = sensitiveCategories[category]!.label;
    final state = importedSensitiveState(detail, category, known: known[category]);
    final consented = _consented(category);
    final status = switch (state) {
      'off' => '대화에 쓰지 않아요',
      'on' => consented ? '대화에 쓰고 있어요 · 동의했어요' : '대화에 쓰고 있어요 · 아직 동의 전이에요',
      _ => '아직 동의 전이에요',
    };
    final lead = category == 'religion'
        ? '예전에 모아 둔 이야기 중 종교·신앙에 관한 기억이에요. 민감한 정보라서 대화에 쓸지 직접 정해 주세요.'
        : '예전에 모아 둔 이야기 중 건강에 관한 기억이에요. 민감한 정보라서 대화에 쓸지 직접 정해 주세요.';
    return AppCard(
      padding: const EdgeInsets.fromLTRB(20, 18, 16, 12),
      color: AppColors.primarySoft,
      borderColor: Colors.transparent,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.lock_outline_rounded, size: 20, color: AppColors.primary),
              const SizedBox(width: 8),
              Expanded(child: Text('$label 이야기', style: AppText.heading)),
            ],
          ),
          const SizedBox(height: 6),
          Text(status, style: AppText.small.copyWith(fontWeight: FontWeight.w700, color: AppColors.ink2)),
          const SizedBox(height: 8),
          Text(keepAll(lead), style: AppText.small.copyWith(color: AppColors.ink2, fontSize: 14, height: 1.55)),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              if (state == 'off')
                FilledButton(
                  onPressed: busy ? null : () => onChoose(category, true),
                  style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                  child: const Text('동의하고 다시 쓰기'),
                )
              else if (state == 'on' && consented)
                TextButton(
                  onPressed: busy ? null : () => onChoose(category, false),
                  style: TextButton.styleFrom(foregroundColor: AppColors.muted),
                  child: Text('$label 이야기 동의 거두기'),
                )
              else ...[
                FilledButton(
                  onPressed: busy ? null : () => onChoose(category, true),
                  style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                  child: const Text('동의하기'),
                ),
                TextButton(
                  onPressed: busy ? null : () => onChoose(category, false),
                  child: const Text('대화에 쓰지 않기'),
                ),
              ],
              if (onDetails != null)
                TextButton(
                  onPressed: () => onDetails!(category),
                  child: const Text('자세히 보기'),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

/// 이 화면을 쓰지 않는 까닭: 가져온 대화 상대(imported) · 초대로 함께하는 가족(member).
enum BlockReason { imported, member }

/// 쓰지 않는 화면(정보 고치기·녹음 더하기·남긴 글·기억 더 채우기·가족과 함께)에 주소로 바로 들어왔을 때.
/// 함께하는 가족에게는 누가 만든 대화 상대인지([ownerName])를 알려 준다.
class PersonaBlocked extends StatelessWidget {
  final String personaId;
  final String title;
  final BlockReason reason;
  final String ownerName;

  const PersonaBlocked({
    super.key,
    required this.personaId,
    required this.title,
    this.reason = BlockReason.imported,
    this.ownerName = '가족',
  });

  /// 상세(서버 응답)를 보고 막을지 정한다. 막지 않으면 null.
  static PersonaBlocked? of(Map persona, {required String personaId, bool imported = true}) {
    if (isMember(persona)) {
      return PersonaBlocked(
        personaId: personaId,
        title: personaTitle(Map<String, dynamic>.from(persona)),
        reason: BlockReason.member,
        ownerName: ownerNameOf(persona),
      );
    }
    if (imported && isImportedPersona(persona)) {
      return PersonaBlocked(personaId: personaId, title: personaTitle(Map<String, dynamic>.from(persona)));
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 24, 20, 32),
        child: PageBody(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Notice(
                reason == BlockReason.member
                    ? '이 대화 상대는 $ownerName님이 만들어 함께 쓰고 있어요. 정보와 녹음은 만든 분만 바꿀 수 있어요. '
                        '대화와 듣기는 그대로 할 수 있어요.'
                    : '이 대화 상대는 예전에 모아 둔 이야기를 옮겨 와 만들었어요. 그 이야기를 그대로 지키려고 '
                        '이 기능은 지금 쓸 수 없어요. 대화와 듣기는 그대로 할 수 있어요.',
              ),
              const SizedBox(height: 20),
              Align(
                alignment: Alignment.centerLeft,
                child: OutlinedButton.icon(
                  onPressed: () => context.canPop() ? context.pop() : context.go('/p/$personaId'),
                  icon: const Icon(Icons.arrow_back_rounded, size: 18),
                  label: const Text('대화로 돌아가기'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
