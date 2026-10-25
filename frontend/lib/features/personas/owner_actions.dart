import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';

/// 만든 분이 할 일(2026-10-22 법률 자문): 그분 동의 녹음 · 법정대리인 확인 · 유족 확인 · 멈춤 안내.
///
/// 무엇을 막고 무엇을 할지는 서버가 정한다(상세의 gates·actions·subjectConsent·hold). 화면은 그대로
/// 그리기만 한다. 새 칸이 없는 예전 응답·시험 가짜 응답이면 모두 됨·할 일 없음으로 본다(SPEC §17.4).

/// 상세의 gates: 지금 할 수 있는 일(true = 됨). 막혔으면 [code]·[message] 가 까닭이다.
class PersonaGates {
  final bool audio;
  final bool voice;
  final bool build;
  final bool chat;
  final bool invite;
  final String? code;
  final String? message;

  const PersonaGates({
    this.audio = true,
    this.voice = true,
    this.build = true,
    this.chat = true,
    this.invite = true,
    this.code,
    this.message,
  });

  static PersonaGates of(Map? detail) {
    final g = detail?['gates'];
    if (g is! Map) return const PersonaGates();
    bool on(String key) => g[key] != false;
    return PersonaGates(
      audio: on('audio'),
      voice: on('voice'),
      build: on('build'),
      chat: on('chat'),
      invite: on('invite'),
      code: g['code'] as String?,
      message: g['message'] as String?,
    );
  }
}

/// 만든 분 카드(상세: {kind, blocking, title, body, route, dueAt?, deleteAt?} · 목록: {kind, blocking}).
List<Map<String, dynamic>> ownerActionsOf(Map? persona) => [
      for (final a in persona?['actions'] as List? ?? const [])
        if (a is Map) Map<String, dynamic>.from(a),
    ];

/// 멈춘 대화 상대({reason, holdAt, deleteAfter}). 아니면 null.
Map<String, dynamic>? holdOf(Map? persona) {
  final hold = persona?['hold'];
  return hold is Map ? Map<String, dynamic>.from(hold) : null;
}

/// 그분 동의 녹음 상태(서버 GET …/subject-consent 와 같은 모양). 없으면 null.
Map<String, dynamic>? subjectConsentOf(Map? detail) {
  final s = detail?['subjectConsent'];
  return s is Map ? Map<String, dynamic>.from(s) : null;
}

/// 만들기 단계(녹음 올리기) 맨 위 카드. 서버가 카드(actions)를 주면 그대로, 주지 않았는데 그분 동의 녹음이
/// 필요하면 그 상태에 맞는 카드 하나(SPEC §11.2 글).
List<Map<String, dynamic>> setupActionsOf(Map<String, dynamic> detail) {
  final actions = ownerActionsOf(detail);
  if (actions.isNotEmpty) return actions;
  final consent = subjectConsentOf(detail);
  if (consent?['required'] != true) return const [];
  final route = '/p/${detail['id']}/consent';
  return switch (consent?['status']) {
    'passed' => const [],
    'submitted' => const [
        {
          'kind': 'subject_consent_review',
          'title': '그분 동의 녹음을 확인하고 있어요',
          'body': '보통 1~2일 걸려요. 확인되면 알림으로 알려 드릴게요.',
        },
      ],
    'hold' => const [
        {'kind': 'subject_consent_hold', 'title': '확인할 것이 있어요', 'body': 'TalkTo가 따로 연락드릴게요.'},
      ],
    _ => [
        {
          'kind': 'subject_consent',
          'title': '먼저 그분 동의 녹음을 해 주세요',
          'body': '앱이 보여 주는 대본대로 그분이 직접 말씀하시는 모습을 한 번에 녹음해요. '
              'TalkTo가 확인하기 전에는 그분 녹음을 글로 옮기거나 해외 회사로 보내지 않아요. 녹음은 미리 올려 두셔도 돼요.',
          'route': route,
        },
      ],
  };
}

/// 목록·첫 화면의 작은 꼬리표: 멈춘 대화 상대는 '멈춤', 할 일이 있으면 '확인이 필요해요'.
String? ownerChipOf(Map? persona) {
  if (holdOf(persona) != null) return '멈춤';
  return ownerActionsOf(persona).isNotEmpty ? '확인이 필요해요' : null;
}

/// 막는 카드(입력 칸 자리에 그린다): 멈춤·유족 확인. 없으면 null.
Map<String, dynamic>? blockingActionOf(Map? persona) {
  for (final a in ownerActionsOf(persona)) {
    if (a['blocking'] == true) return a;
  }
  return null;
}

/// 카드 단추 글. 갈 곳(route)이 없으면 단추가 없다. [consentStatus] 는 그분 동의 녹음 상태
/// (다시 부탁받았으면 '다시 녹음하기').
String ownerActionLabel(Map action, {String? consentStatus}) {
  // 확인 중·보류 카드도 동의 녹음 화면으로 가지만, 거기서는 상태만 보여 준다(녹음을 시작할 수 없다)
  if (action['kind'] == 'subject_consent_review' || action['kind'] == 'subject_consent_hold') return '자세히 보기';
  final route = '${action['route'] ?? ''}';
  if (route.endsWith('/bereaved')) return '신청 자격과 유족 명단 적기';
  if (route.endsWith('/guardian')) return '법정대리인 확인하기';
  if (route.endsWith('/consent')) return consentStatus == 'rerequest' ? '다시 녹음하기' : '동의 녹음하기';
  return '자세히 보기';
}

/// 만든 분 카드 하나. 막는 카드(멈춤·유족 확인)는 진한 흙색, 그 밖은 차분한 초록.
/// [onDelete] 가 있으면 멈춘 카드에 '지금 지우기'(기존 지우기 흐름)를 단다.
class OwnerActionCard extends StatelessWidget {
  final Map<String, dynamic> action;
  final VoidCallback? onDelete;
  final bool compact;

  /// 그분 동의 녹음 상태(상세의 subjectConsent.status). 단추 글을 고른다.
  final String? consentStatus;

  const OwnerActionCard({
    super.key,
    required this.action,
    this.onDelete,
    this.compact = false,
    this.consentStatus,
  });

  @override
  Widget build(BuildContext context) {
    final blocking = action['blocking'] == true;
    final stopped = action['kind'] == 'stopped';
    final route = '${action['route'] ?? ''}'.trim();
    final title = '${action['title'] ?? ''}'.trim();
    final body = '${action['body'] ?? ''}'.trim();
    final color = blocking ? AppColors.clay : AppColors.primary;
    return AppCard(
      padding: EdgeInsets.fromLTRB(18, compact ? 12 : 16, 14, compact ? 10 : 14),
      color: blocking ? AppColors.claySoft : AppColors.primarySoft,
      borderColor: Colors.transparent,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Icon(
              stopped ? Icons.pause_circle_outline_rounded : Icons.fact_check_outlined,
              size: 20,
              color: color,
            ),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (title.isNotEmpty)
                  Text(keepAll(title), style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15, height: 1.45)),
                if (body.isNotEmpty) ...[
                  const SizedBox(height: 3),
                  Text(keepAll(body), style: AppText.small.copyWith(color: AppColors.ink2, fontSize: 13.5, height: 1.55)),
                ],
                if (route.isNotEmpty || (stopped && onDelete != null))
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Wrap(
                      spacing: 8,
                      runSpacing: 4,
                      children: [
                        if (route.isNotEmpty)
                          FilledButton(
                            onPressed: () => context.go(route),
                            style: FilledButton.styleFrom(minimumSize: const Size(0, 42)),
                            child: Text(ownerActionLabel(action, consentStatus: consentStatus)),
                          ),
                        if (stopped && onDelete != null)
                          OutlinedButton(
                            onPressed: onDelete,
                            style: OutlinedButton.styleFrom(
                              minimumSize: const Size(0, 42),
                              foregroundColor: AppColors.danger,
                            ),
                            child: const Text('지금 지우기'),
                          ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
