import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_client.dart';
import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/ui.dart';
import '../../shared/widgets/voice_input.dart';
import '../personas/data/persona_api.dart';
import '../share/roles.dart';
import 'gap_api.dart';

// '더 알려 주실 기억' 화면 조각: 질문 카드(답 칸·🎤·잘 모르겠어요·나중에·가족에게 물어보기), 가족 답·가족 고침
// 확인 카드, 기억 더 채우기 화면의 들어가는 카드, 대화를 나올 때 하나만 묻는 시트.

/// 답 칸 글자 수(서버도 2,000자까지 받는다).
const gapAnswerMax = 2000;

/// 가족 고침 종류 → 화면 이름.
const correctionKindLabels = {'STYLE': '말투', 'CONTENT': '내용'};

/// 답한(고친) 가족을 한 줄로: '철수(아들)님'. 모르면 '함께 쓰는 가족'.
String familyWho(Object? by) {
  final map = by is Map ? by : const {};
  final name = '${map['name'] ?? ''}'.trim();
  final relation = '${map['relation'] ?? ''}'.trim();
  if (name.isNotEmpty) return relation.isEmpty ? '$name님' : '$name($relation)님';
  if (relation.isNotEmpty) return relation;
  return '함께 쓰는 가족';
}

/// 열린 질문 하나. 접혀 있으면 질문과 [답하기]·[잘 모르겠어요], 펼치면 답 칸(🎤)과
/// [알려 주기]·[잘 모르겠어요]·[나중에]·(만든 분, 함께 쓰는 가족이 있으면) [가족에게 물어보기].
class GapQuestionCard extends StatefulWidget {
  final String personaId;
  final Map<String, dynamic> question;

  /// 함께 쓰는 가족이 보는가(가족에게 물어보기가 없고, 답은 만든 분이 확인한 뒤 넣는다).
  final bool member;

  /// '가족에게 물어보기'를 보일지(만든 분이고 함께 쓰는 가족이 있을 때).
  final bool canAskFamily;

  /// 함께하는 가족 화면에 보일 만든 분 이름(지은).
  final String ownerName;

  /// 처음부터 답 칸을 펼친다(대화를 나올 때의 시트).
  final bool open;

  /// 답했거나 '잘 모르겠어요'를 눌러 목록에서 빠진다(서버가 돌려준 새 줄).
  final void Function(Map<String, dynamic> updated) onDone;

  /// '가족에게 물어보기'를 켜거나 끈 뒤(새 줄).
  final void Function(Map<String, dynamic> updated)? onChanged;

  /// '나중에'. 없으면 답 칸만 접는다(질문은 목록에 그대로).
  final VoidCallback? onLater;

  const GapQuestionCard({
    super.key,
    required this.personaId,
    required this.question,
    required this.onDone,
    this.member = false,
    this.canAskFamily = false,
    this.ownerName = '가족',
    this.open = false,
    this.onChanged,
    this.onLater,
  });

  @override
  State<GapQuestionCard> createState() => _GapQuestionCardState();
}

class _GapQuestionCardState extends State<GapQuestionCard> {
  final _answer = TextEditingController();
  late bool _open = widget.open;

  /// 지금 하는 일('answer'·'dismiss'·'ask'). 그동안 다른 단추는 잠근다.
  String? _busy;

  String get _qid => '${widget.question['id']}';
  bool get _askingFamily => widget.question['askFamily'] == true;

  @override
  void dispose() {
    _answer.dispose();
    super.dispose();
  }

  Future<void> _run(String kind, Future<void> Function() work) async {
    if (_busy != null) return;
    setState(() => _busy = kind);
    try {
      await work();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  Future<void> _send() async {
    final text = _answer.text.trim();
    if (text.isEmpty) {
      showMessage(context, '답을 적거나 🎤를 눌러 말씀해 주세요.');
      return;
    }
    await _run('answer', () async {
      final updated = await GapApi.answer(widget.personaId, _qid, text);
      if (!mounted) return;
      showMessage(
        context,
        widget.member
            ? '고마워요. ${widget.ownerName}님이 확인하면 기억에 넣어요.'
            : '알려 주셔서 고마워요. 다음 대화부터 기억해요.',
      );
      widget.onDone(updated);
    });
  }

  Future<void> _dismiss() => _run('dismiss', () async {
        final updated = await GapApi.dismiss(widget.personaId, _qid);
        if (!mounted) return;
        showMessage(context, widget.member ? '알겠어요. 이 질문은 다시 보이지 않아요.' : '알겠어요. 이 질문은 더 여쭤보지 않을게요.');
        widget.onDone(updated);
      });

  Future<void> _ask(bool ask) => _run('ask', () async {
        final updated = await GapApi.askFamily(widget.personaId, _qid, ask: ask);
        if (!mounted) return;
        showMessage(
          context,
          ask ? '함께 쓰는 가족에게도 물어볼게요. 가족이 답하면 여기서 확인하고 넣어요.' : '가족에게는 더 묻지 않을게요.',
        );
        widget.onChanged?.call(updated);
      });

  void _later() {
    if (widget.onLater != null) {
      widget.onLater!();
      return;
    }
    setState(() => _open = false);
  }

  @override
  Widget build(BuildContext context) {
    final busy = _busy != null;
    final askButton = widget.canAskFamily && !widget.member && !_askingFamily
        ? OutlinedButton.icon(
            onPressed: busy ? null : () => _ask(true),
            style: OutlinedButton.styleFrom(minimumSize: const Size(0, 44)),
            icon: const Icon(Icons.group_outlined, size: 18),
            label: const Text('가족에게 물어보기'),
          )
        : null;
    final dismiss = TextButton(
      onPressed: busy ? null : _dismiss,
      style: TextButton.styleFrom(minimumSize: const Size(0, 44)),
      child: Text(_busy == 'dismiss' ? '알리는 중…' : '잘 모르겠어요'),
    );
    return AppCard(
      padding: const EdgeInsets.fromLTRB(18, 16, 14, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (_askingFamily && !widget.member) ...[
            Row(
              children: [
                const StatusChip('가족에게도 물어보는 중', positive: true),
                const Spacer(),
                TextButton(
                  onPressed: busy ? null : () => _ask(false),
                  style: TextButton.styleFrom(foregroundColor: AppColors.muted, visualDensity: VisualDensity.compact),
                  child: const Text('그만 묻기'),
                ),
              ],
            ),
            const SizedBox(height: 8),
          ],
          Text(
            keepAll('${widget.question['question'] ?? ''}'),
            style: const TextStyle(fontSize: 16.5, fontWeight: FontWeight.w700, height: 1.5, color: AppColors.ink),
          ),
          const SizedBox(height: 10),
          if (!_open)
            Wrap(
              spacing: 8,
              runSpacing: 4,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                FilledButton(
                  onPressed: busy ? null : () => setState(() => _open = true),
                  style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                  child: const Text('답하기'),
                ),
                dismiss,
                if (askButton != null) askButton,
              ],
            )
          else ...[
            VoiceField(
              controller: _answer,
              transcribe: (clip) => PersonaApi.transcribeAnswer(widget.personaId, clip),
              maxLength: gapAnswerMax,
              enabled: !busy,
              child: TextField(
                controller: _answer,
                enabled: !busy,
                minLines: 2,
                maxLines: 6,
                maxLength: gapAnswerMax,
                textInputAction: TextInputAction.newline,
                decoration: const InputDecoration(
                  hintText: '아시는 만큼 적거나 🎤를 눌러 말씀해 주세요',
                  counterText: '',
                ),
              ),
            ),
            // 함께하는 가족: 보내기 전에, 답은 만든 분이 확인한 뒤 반영되고 만든 분께 보인다고 알린다
            if (widget.member) ...[
              const SizedBox(height: 10),
              const Notice(
                memberSendNotice,
                icon: Icons.visibility_outlined,
                color: AppColors.primary,
                background: AppColors.primarySoft,
              ),
            ],
            const SizedBox(height: 12),
            FilledButton(
              onPressed: busy ? null : _send,
              style: FilledButton.styleFrom(minimumSize: const Size(0, 50)),
              child: Text(_busy == 'answer' ? '넣는 중…' : '알려 주기'),
            ),
            const SizedBox(height: 6),
            Wrap(
              spacing: 8,
              runSpacing: 4,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                dismiss,
                TextButton(
                  onPressed: busy ? null : _later,
                  style: TextButton.styleFrom(minimumSize: const Size(0, 44)),
                  child: const Text('나중에'),
                ),
                if (askButton != null) askButton,
              ],
            ),
          ],
        ],
      ),
    );
  }
}

/// 가족이 보낸 답 하나. 만든 분: [기억에 넣기]·[넣지 않기]. 가족: 확인을 기다리는 내 답(단추 없이).
class GapPendingAnswerCard extends StatefulWidget {
  final String personaId;
  final Map<String, dynamic> question;
  final bool member;
  final String ownerName;

  /// 넣었거나 넣지 않아 목록에서 빠진다(새 줄).
  final void Function(Map<String, dynamic> updated)? onDone;

  /// 그 사이에 답이 바뀌었거나 이미 확인한 것(409). 목록을 다시 읽는다.
  final VoidCallback? onStale;

  const GapPendingAnswerCard({
    super.key,
    required this.personaId,
    required this.question,
    this.member = false,
    this.ownerName = '가족',
    this.onDone,
    this.onStale,
  });

  @override
  State<GapPendingAnswerCard> createState() => _GapPendingAnswerCardState();
}

class _GapPendingAnswerCardState extends State<GapPendingAnswerCard> {
  bool _busy = false;

  Future<void> _decide(bool approve) async {
    // 누른 때 보인 답을 잡아 둔다(묻는 창이 열린 사이에 목록이 새로 와도 본 답만 보낸다)
    final seen = widget.question;
    if (!approve) {
      final ok = await confirmAction(
        context,
        '가족이 보낸 답을 넣지 않을까요?',
        '보낸 답은 지워지고, 질문은 다시 여쭤볼 목록으로 돌아가요.',
        action: '넣지 않기',
      );
      if (!ok || !mounted) return;
    }
    setState(() => _busy = true);
    try {
      // 이 카드에 보인 답(글·답한 때)을 함께 보낸다. 그 사이에 다른 가족이 다시 답했으면 서버가 409
      final updated = approve
          ? await GapApi.approve(widget.personaId, seen)
          : await GapApi.reject(widget.personaId, seen);
      if (!mounted) return;
      showMessage(context, approve ? '기억에 넣었어요. 다음 대화부터 기억해요.' : '넣지 않았어요.');
      widget.onDone?.call(updated);
    } catch (error) {
      if (!mounted) return;
      showMessage(context, error);
      // 바뀐 답·이미 확인한 답: 목록을 새로 읽어 지금 것을 보인다
      if (error is ApiException && error.statusCode == 409) widget.onStale?.call();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final q = widget.question;
    return AppCard(
      padding: const EdgeInsets.fromLTRB(18, 16, 14, 12),
      color: widget.member ? AppColors.card : AppColors.primarySoft,
      borderColor: widget.member ? null : Colors.transparent,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            widget.member ? '확인을 기다리는 내 답' : '${familyWho(q['answeredBy'])}이 알려 줬어요',
            style: AppText.small.copyWith(fontWeight: FontWeight.w700, color: AppColors.primary),
          ),
          const SizedBox(height: 6),
          Text(
            keepAll('${q['question'] ?? ''}'),
            style: const TextStyle(fontSize: 15.5, fontWeight: FontWeight.w700, height: 1.5),
          ),
          const SizedBox(height: 8),
          _Quote('${q['answerText'] ?? ''}'),
          const SizedBox(height: 8),
          if (widget.member)
            Text(keepAll('${widget.ownerName}님이 확인하면 기억에 넣어요.'), style: AppText.small)
          else
            Wrap(
              spacing: 8,
              runSpacing: 4,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                FilledButton(
                  onPressed: _busy ? null : () => _decide(true),
                  style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                  child: const Text('기억에 넣기'),
                ),
                TextButton(
                  onPressed: _busy ? null : () => _decide(false),
                  style: TextButton.styleFrom(minimumSize: const Size(0, 44)),
                  child: const Text('넣지 않기'),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

/// 함께 쓰는 가족이 고쳐 준 대답 하나(만든 분이 확인). 그분 대답과 고친 말, [반영하기]·[반영하지 않기].
/// 고친 말은 글 예시로만 쓴다(그분 목소리로 읽지 않는다).
class GapCorrectionCard extends StatefulWidget {
  final String personaId;
  final Map<String, dynamic> correction;

  /// 반영했거나 반영하지 않아 목록에서 빠진다(서버가 돌려준 값).
  final void Function(Map<String, dynamic> updated)? onDone;

  /// 가족이 그 사이에 고침을 바꿨거나 이미 확인한 것(409). 목록을 다시 읽는다.
  final VoidCallback? onStale;

  const GapCorrectionCard({
    super.key,
    required this.personaId,
    required this.correction,
    this.onDone,
    this.onStale,
  });

  @override
  State<GapCorrectionCard> createState() => _GapCorrectionCardState();
}

class _GapCorrectionCardState extends State<GapCorrectionCard> {
  bool _busy = false;

  List<String> get _kinds => [for (final k in widget.correction['kinds'] as List? ?? const []) '$k'];

  Future<void> _decide(bool apply) async {
    setState(() => _busy = true);
    try {
      // 이 카드에 보인 고침(고친 말·고른 것)을 함께 보낸다. 가족이 그 사이에 바꿨으면 서버가 409
      final updated = apply
          ? await GapApi.approveCorrection(widget.personaId, widget.correction)
          : await GapApi.rejectCorrection(widget.personaId, widget.correction);
      if (!mounted) return;
      showMessage(context, apply ? '반영했어요. 다음 대답부터 참고해요.' : '반영하지 않았어요.');
      widget.onDone?.call(updated);
    } catch (error) {
      if (!mounted) return;
      showMessage(context, error);
      // 바뀐 고침·이미 확인한 고침: 목록을 새로 읽어 지금 것을 보인다
      if (error is ApiException && error.statusCode == 409) widget.onStale?.call();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.correction;
    final kinds = [for (final k in _kinds) correctionKindLabels[k] ?? k].join('·');
    final note = _kinds.contains('CONTENT')
        ? (_kinds.contains('STYLE') ? '반영하면 고친 내용을 기억에 넣고, 다음 대답부터 이 말투를 참고해요.' : '반영하면 고친 내용을 기억에 넣어요.')
        : '반영하면 다음 대답부터 이 말투를 참고해요.';
    return AppCard(
      padding: const EdgeInsets.fromLTRB(18, 16, 14, 12),
      color: AppColors.primarySoft,
      borderColor: Colors.transparent,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            '${familyWho(c['answeredBy'])}이 대답을 고쳐 줬어요${kinds.isEmpty ? '' : ' · $kinds'}',
            style: AppText.small.copyWith(fontWeight: FontWeight.w700, color: AppColors.primary),
          ),
          const SizedBox(height: 8),
          const Text('그분 대답', style: AppText.small),
          const SizedBox(height: 4),
          _Quote('${c['original'] ?? ''}', maxLines: 5, muted: true),
          const SizedBox(height: 8),
          const Text('그분이라면 이렇게 말하셨을 거예요', style: AppText.small),
          const SizedBox(height: 4),
          _Quote('${c['correction'] ?? ''}'),
          const SizedBox(height: 8),
          Text(keepAll(note), style: AppText.small),
          const SizedBox(height: 6),
          Wrap(
            spacing: 8,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              FilledButton(
                onPressed: _busy ? null : () => _decide(true),
                style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                child: const Text('반영하기'),
              ),
              TextButton(
                onPressed: _busy ? null : () => _decide(false),
                style: TextButton.styleFrom(minimumSize: const Size(0, 44)),
                child: const Text('반영하지 않기'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// 따옴표 상자(가족 답·그분 대답·고친 말).
class _Quote extends StatelessWidget {
  final String text;
  final int? maxLines;
  final bool muted;

  const _Quote(this.text, {this.maxLines, this.muted = false});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
      decoration: BoxDecoration(color: AppColors.card, borderRadius: BorderRadius.circular(12)),
      child: Text(
        keepAll(text),
        maxLines: maxLines,
        overflow: maxLines == null ? null : TextOverflow.ellipsis,
        style: TextStyle(fontSize: 15, height: 1.55, color: muted ? AppColors.ink2 : AppColors.ink),
      ),
    );
  }
}

/// 기억 더 채우기 화면 위의 들어가는 카드. [count] 는 상세의 gapCount(여쭤볼 것 수).
class GapEntryCard extends StatelessWidget {
  final String personaId;
  final int count;

  /// 상세의 gapWaiting(확인을 기다리는 가족 답·가족 고침 수, 만든 분만). 배지에 더한다.
  final int waiting;

  /// 더 알려 주실 기억에서 돌아온 뒤(숫자를 다시 읽는다).
  final VoidCallback? onReturn;

  const GapEntryCard({super.key, required this.personaId, this.count = 0, this.waiting = 0, this.onReturn});

  @override
  Widget build(BuildContext context) {
    final total = count + waiting;
    return AppCard(
      padding: const EdgeInsets.fromLTRB(18, 16, 14, 16),
      color: total > 0 ? AppColors.claySoft : AppColors.card,
      borderColor: total > 0 ? Colors.transparent : null,
      onTap: () async {
        await context.push('/p/$personaId/gaps');
        onReturn?.call();
      },
      child: Row(
        children: [
          Badge(
            isLabelVisible: total > 0,
            label: Text('$total'),
            backgroundColor: AppColors.clay,
            child: Icon(Icons.lightbulb_outline_rounded, color: total > 0 ? AppColors.clay : AppColors.primary),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('더 알려 주실 기억', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5)),
                const SizedBox(height: 2),
                Text(
                  keepAll(waiting > 0
                      ? '가족이 보낸 것 $waiting개 확인해 주세요.${count > 0 ? ' 아직 모르는 이야기도 $count개 있어요.' : ''}'
                      : count > 0
                          ? '대화하다 아직 모르는 이야기가 $count개 있어요. 아시는 것만 알려 주세요.'
                          : '대화하다 아직 모르는 이야기가 나오면 여기에 모아 둘게요.'),
                  style: AppText.small,
                ),
              ],
            ),
          ),
          const Icon(Icons.chevron_right_rounded, color: AppColors.muted),
        ],
      ),
    );
  }
}

/// 대화 화면을 나올 때 '더 알려 주실 기억' 하나를 묻는다. 대화하는 동안에는 묻지 않는다.
///
/// 대화 화면이 닫힌 뒤(뒤로 가기·브라우저 뒤로 가기·PC 왼쪽 목록) 목록을 한 번 읽고, 대화 상대 목록(첫 화면)에
/// 왔을 때만 시트를 띄운다. 다른 대화 상대·요금제 화면 등으로 갔으면 묻지 않는다(질문은 '더 알려 주실 기억'에
/// 그대로 있다). 지운 대화 상대·그만둔 함께 쓰기는 서버가 404 를 주어 묻지 않는다.
class GapLeave {
  GapLeave._();

  /// 이번에 '나중에'를 누르거나 시트를 닫은 질문(앱을 다시 열 때까지). 다음에 나올 때는 다른 질문을 묻는다.
  static final later = <String>{};

  static bool _showing = false;

  /// 대화 화면을 나온 뒤 부른다. [router] 는 대화 화면이 있던 앱의 라우터.
  static Future<void> afterChat(GoRouter router, Map<String, dynamic> persona) async {
    final id = persona['id'];
    if (id is! String || _showing) return;
    _showing = true;
    try {
      final GapList list;
      try {
        list = await GapApi.list(id);
      } catch (_) {
        return; // 묻지 않는다(연결 문제·지운 대화 상대)
      }
      final question = list.questions.where((q) => !later.contains('${q['id']}')).firstOrNull;
      if (question == null) return;
      final context = router.routerDelegate.navigatorKey.currentContext;
      if (context == null || !context.mounted) return;
      if (router.routerDelegate.currentConfiguration.uri.path != '/') return;
      await showGapSheet(context, persona: persona, question: question, list: list);
    } finally {
      _showing = false;
    }
  }
}

/// 질문 하나를 아래 시트(PC 는 가운데 창)로 묻는다. '나중에'·닫기는 [GapLeave.later] 에 남긴다.
Future<void> showGapSheet(
  BuildContext context, {
  required Map<String, dynamic> persona,
  required Map<String, dynamic> question,
  required GapList list,
}) async {
  final id = '${persona['id']}';
  final qid = '${question['id']}';
  final title = personaTitle(persona);
  final member = list.member || isMember(persona);
  final more = list.questions.length - 1;
  var done = false;

  Widget body(BuildContext sheet) {
    void close() => Navigator.pop(sheet);
    return SingleChildScrollView(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(sheet).bottom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // 질문은 TalkTo 가 여쭌다(대화 상대가 직접 묻는 것처럼 보이지 않게 그분 얼굴 대신 TalkTo 표시)
          const Align(alignment: Alignment.centerLeft, child: BrandLogo(height: 16)),
          const SizedBox(height: 10),
          Text(
            keepAll('$title 이야기, TalkTo가 하나만 여쭤볼게요'),
            style: AppText.title.copyWith(fontSize: 19),
          ),
          const SizedBox(height: 8),
          Text(
            keepAll(member
                ? '${ownerNameOf(persona)}님이 가족에게 물어봐 달라고 한 질문이에요. 아시는 만큼만 알려 주세요.'
                : '대화하다 보니 아직 모르는 이야기가 있었어요. 아시는 만큼만 알려 주시면 다음 대화부터 기억해요.'),
            style: AppText.small.copyWith(fontSize: 14, color: AppColors.ink2),
          ),
          const SizedBox(height: 14),
          GapQuestionCard(
            personaId: id,
            question: question,
            member: member,
            canAskFamily: list.canAskFamily,
            ownerName: ownerNameOf(persona),
            open: true,
            onDone: (_) {
              done = true;
              close();
            },
            onChanged: (_) {
              done = true; // 가족에게 물어봤으면 이번에는 그만 묻는다
              close();
            },
            onLater: close,
          ),
          const SizedBox(height: 4),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: () {
                final router = GoRouter.maybeOf(sheet);
                done = true;
                close();
                router?.go('/p/$id/gaps');
              },
              icon: const Icon(Icons.list_alt_rounded, size: 18),
              label: Text(more > 0 ? '모아 둔 질문 모두 보기 (${more + 1})' : '더 알려 주실 기억 보기'),
            ),
          ),
        ],
      ),
    );
  }

  if (Breakpoints.isWide(context)) {
    await showDialog<void>(
      context: context,
      builder: (sheet) => Dialog(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 480),
          child: Padding(padding: const EdgeInsets.fromLTRB(24, 24, 24, 12), child: body(sheet)),
        ),
      ),
    );
  } else {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      builder: (sheet) => SafeArea(
        child: Padding(padding: const EdgeInsets.fromLTRB(20, 4, 20, 12), child: body(sheet)),
      ),
    );
  }
  // 답하지 않고 닫았으면(나중에·바깥 누르기) 이번에는 다시 묻지 않는다. 질문은 목록에 그대로 있다
  if (!done) GapLeave.later.add(qid);
}
