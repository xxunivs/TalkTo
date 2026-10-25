import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_client.dart';
import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/ui.dart';
import '../personas/data/persona_api.dart';
import '../personas/data/persona_store.dart';
import '../personas/persona_flow_page.dart' show leaveRemovedShare;
import '../share/roles.dart';
import 'gap_api.dart';
import 'gap_widgets.dart';

/// 더 알려 주실 기억(/p/:id/gaps): 대화하다 아직 모르던 이야기를 가족에게 여쭤보는 질문 목록.
///
/// 만든 분: 열린 질문(답하기 글·🎤, 잘 모르겠어요, 나중에, 함께 쓰는 가족이 있으면 가족에게 물어보기)과
/// 가족이 보낸 답·가족이 고쳐 준 대답(넣기/넣지 않기). 답은 바로 기억이 되어 다음 대화부터 쓴다(다시 만들지 않는다).
/// 함께 쓰는 가족: 만든 분이 물어봐 달라고 한 질문과 확인을 기다리는 내 답. 가족의 답은 만든 분이 확인한 뒤 넣는다.
class GapQuestionsPage extends StatefulWidget {
  final String personaId;

  const GapQuestionsPage({super.key, required this.personaId});

  @override
  State<GapQuestionsPage> createState() => _GapQuestionsPageState();
}

class _GapQuestionsPageState extends State<GapQuestionsPage> {
  Map<String, dynamic>? _detail;
  GapList? _list;
  Object? _error;

  String get _id => widget.personaId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait<Object>([PersonaApi.detail(_id), GapApi.list(_id)]);
      if (!mounted) return;
      setState(() {
        _detail = results[0] as Map<String, dynamic>;
        _list = results[1] as GapList;
        _error = null;
      });
    } catch (error) {
      if (!mounted) return;
      // 함께 쓰던 대화 상대에서 그사이 빠졌다
      if (error is ApiException && error.statusCode == 404 && isMember(personaStore.byId(_id))) {
        leaveRemovedShare(context);
        return;
      }
      setState(() => _error = error);
    }
  }

  /// 목록에서 한 줄을 빼거나 바꾼 뒤. 열려 있는 대화 화면도 배지(gapCount)를 다시 읽는다.
  void _changed(GapList next) {
    setState(() => _list = next);
    notifyPersonaEdited(_id);
  }

  void _removeQuestion(Map<String, dynamic> updated) {
    final list = _list!;
    _changed(GapList(
      member: list.member,
      questions: [for (final q in list.questions) if (q['id'] != updated['id']) q],
      // 가족이 답했으면 내 답이 확인을 기다리는 목록에 들어간다
      pendingAnswers: [
        ...list.pendingAnswers,
        if (list.member && updated['answerState'] == 'pending_owner') updated,
      ],
      pendingCorrections: list.pendingCorrections,
      canAskFamily: list.canAskFamily,
    ));
  }

  void _replaceQuestion(Map<String, dynamic> updated) {
    final list = _list!;
    _changed(GapList(
      member: list.member,
      questions: [for (final q in list.questions) q['id'] == updated['id'] ? {...q, ...updated} : q],
      pendingAnswers: list.pendingAnswers,
      pendingCorrections: list.pendingCorrections,
      canAskFamily: list.canAskFamily,
    ));
  }

  void _decidedAnswer(Map<String, dynamic> updated) {
    final list = _list!;
    _changed(GapList(
      member: list.member,
      // 넣지 않은 답의 질문은 다시 열린다
      questions: [
        if (updated['status'] == 'open') updated,
        for (final q in list.questions) if (q['id'] != updated['id']) q,
      ],
      pendingAnswers: [for (final q in list.pendingAnswers) if (q['id'] != updated['id']) q],
      pendingCorrections: list.pendingCorrections,
      canAskFamily: list.canAskFamily,
    ));
  }

  void _decidedCorrection(Map<String, dynamic> updated) {
    final list = _list!;
    _changed(GapList(
      member: list.member,
      questions: list.questions,
      pendingAnswers: list.pendingAnswers,
      pendingCorrections: [for (final c in list.pendingCorrections) if (c['id'] != updated['id']) c],
      canAskFamily: list.canAskFamily,
    ));
  }

  void _back() => context.canPop() ? context.pop() : context.go('/p/$_id');

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    final list = _list;
    if (detail == null || list == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('더 알려 주실 기억')),
        body: _error != null
            ? LoadFailed(_error!, onRetry: () {
                setState(() => _error = null);
                _load();
              })
            : const Loading(),
      );
    }
    final title = personaTitle(detail);
    final member = list.member || isMember(detail);
    final owner = ownerNameOf(detail);
    return WizardScaffold(
      title: '더 알려 주실 기억',
      headline: member ? '$owner님이 여쭤봐 달라고 했어요' : '$title 이야기를 더 알려 주세요',
      lead: member
          ? '$title에 대해 아시는 만큼만 알려 주세요. 보내 주신 답은 $owner님이 확인하면 기억에 넣어요. 모르면 「잘 모르겠어요」를 눌러 주세요.'
          : '대화하다 보니 아직 모르는 이야기가 있었어요. 아시는 만큼만 알려 주시면 바로 기억에 넣어 다음 대화부터 써요. '
              '모르면 「잘 모르겠어요」를 눌러 주세요.',
      secondary: TextButton(onPressed: _back, child: const Text('돌아가기')),
      children: [
        if (!member && (list.pendingAnswers.isNotEmpty || list.pendingCorrections.isNotEmpty)) ...[
          SectionLabel('가족이 보낸 것 · 확인해 주세요 ${list.waiting}'),
          for (final q in list.pendingAnswers)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: GapPendingAnswerCard(
                key: ValueKey('answer-${q['id']}'),
                personaId: _id,
                question: q,
                onDone: _decidedAnswer,
                onStale: _load,
              ),
            ),
          for (final c in list.pendingCorrections)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: GapCorrectionCard(
                key: ValueKey('correction-${c['id']}'),
                personaId: _id,
                correction: c,
                onDone: _decidedCorrection,
                onStale: _load,
              ),
            ),
          const SizedBox(height: 14),
        ],
        SectionLabel('여쭤볼 것 ${list.questions.length}'),
        if (list.questions.isEmpty)
          Notice(
            member
                ? '지금은 여쭤볼 것이 없어요. $owner님이 물어봐 달라고 하면 여기에 보여요.'
                : '지금은 여쭤볼 것이 없어요. 대화하다 아직 모르는 이야기가 나오면 여기에 모아 둘게요.',
            icon: Icons.check_circle_outline_rounded,
            color: AppColors.primary,
            background: AppColors.primarySoft,
          )
        else
          for (final q in list.questions)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: GapQuestionCard(
                key: ValueKey('question-${q['id']}'),
                personaId: _id,
                question: q,
                member: member,
                canAskFamily: list.canAskFamily,
                ownerName: owner,
                onDone: _removeQuestion,
                onChanged: _replaceQuestion,
              ),
            ),
        if (member && list.pendingAnswers.isNotEmpty) ...[
          const SizedBox(height: 14),
          SectionLabel('보낸 답 ${list.pendingAnswers.length}'),
          for (final q in list.pendingAnswers)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: GapPendingAnswerCard(
                key: ValueKey('mine-${q['id']}'),
                personaId: _id,
                question: q,
                member: true,
                ownerName: owner,
              ),
            ),
        ],
        const SizedBox(height: 8),
        Text(
          keepAll(member
              ? '짐작한 것보다 직접 보고 들은 것만 알려 주세요.'
              : '짐작한 것보다 직접 보고 들은 것만 알려 주세요. 함께 쓰는 가족이 있으면 「가족에게 물어보기」로 가족에게도 여쭤볼 수 있어요.'),
          style: AppText.small,
        ),
      ],
    );
  }
}
