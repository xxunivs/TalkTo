import 'package:flutter/material.dart';

import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import '../../shared/widgets/voice_input.dart';
import '../personas/data/persona_api.dart';
import '../share/roles.dart' show memberSendNotice;

// 대화 평가: 그분 대답 말풍선 아래 작은 ⋯(다섯 가지 중 고르기, 말투·내용이면 '그분이라면 어떻게 말하셨을까요?'
// 한 줄)과 대화 화면 위 '한마디'(대화 전체에 한 줄). 고친 한 줄은 글 예시로만 쓴다(그분 목소리로 읽지 않는다).

/// 말풍선 ⋯ 고르기(서버 tags) → 화면 이름. 순서대로 보인다.
const feedbackChoices = {
  'LIKE': '그분 같았어요',
  'STYLE': '말투가 달라요',
  'CONTENT': '내용이 틀려요',
  'TOO_LONG': '너무 길어요',
  'VOICE': '목소리가 달라요',
};

/// 고친 한 줄을 받는 고르기(말투·내용).
const correctable = {'STYLE', 'CONTENT'};

/// 고친 한 줄 글자 수(서버도 300자까지).
const correctionMax = 300;

/// 고르기 → 평가(서버 rating). 그분 같았어요만 UP, 나머지는 DOWN.
String ratingOf(Iterable<String> tags) => tags.contains('LIKE') ? 'UP' : 'DOWN';

/// 말풍선 평가 한 번에 보낸 것: 고른 것·고친 한 줄·서버가 정한 state(applied·pending_owner·none·rejected).
typedef SentFeedback = ({List<String> tags, String? correction, String? state});

/// 보낸 뒤 알릴 말. [ownerName] 은 함께하는 가족 화면의 만든 분 이름.
String feedbackThanks(SentFeedback sent, {String ownerName = '가족'}) {
  final tags = sent.tags;
  switch (sent.state) {
    case 'pending_owner':
      return '고마워요. 고쳐 주신 말은 $ownerName님이 확인하면 반영돼요.';
    case 'applied':
      final style = tags.contains('STYLE');
      final content = tags.contains('CONTENT');
      if (style && content) return '고쳐 주신 말투와 내용을 다음 대답부터 반영해요.';
      if (content) return '고쳐 주신 내용을 기억에 넣었어요. 다음 대답부터 반영해요.';
      return '고쳐 주신 말투를 다음 대답부터 참고해요.';
  }
  if (tags.length == 1 && tags.first == 'LIKE') return '알려 주셔서 고마워요.';
  return '알려 주셔서 고마워요. 더 그분답게 다듬을게요.';
}

/// 말풍선 ⋯ 를 누르면 여는 창(휴대폰은 아래 시트, PC 는 가운데 창). 보냈으면 보낸 것, 닫았으면 null.
/// [voice] 가 false 면(목소리가 없는 대화 상대) '목소리가 달라요'를 보이지 않는다.
/// [member](함께하는 가족)면 고친 한 줄 칸 아래에 '만든 분이 확인한 뒤에 반영돼요…'를 보인다([memberSendNotice]).
Future<SentFeedback?> showMessageFeedback(
  BuildContext context, {
  required String personaId,
  required String messageId,
  bool voice = true,
  bool member = false,
  SentFeedback? previous,
}) {
  final form = _MessageFeedbackForm(
    personaId: personaId,
    messageId: messageId,
    voice: voice,
    member: member,
    previous: previous,
  );
  if (Breakpoints.isWide(context)) {
    return showDialog<SentFeedback>(
      context: context,
      builder: (_) => Dialog(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 460),
          child: Padding(padding: const EdgeInsets.fromLTRB(24, 24, 24, 16), child: form),
        ),
      ),
    );
  }
  return showModalBottomSheet<SentFeedback>(
    context: context,
    isScrollControlled: true,
    builder: (_) => SafeArea(child: Padding(padding: const EdgeInsets.fromLTRB(20, 0, 20, 12), child: form)),
  );
}

class _MessageFeedbackForm extends StatefulWidget {
  final String personaId;
  final String messageId;
  final bool voice;
  final bool member;
  final SentFeedback? previous;

  const _MessageFeedbackForm({
    required this.personaId,
    required this.messageId,
    required this.voice,
    required this.member,
    this.previous,
  });

  @override
  State<_MessageFeedbackForm> createState() => _MessageFeedbackFormState();
}

class _MessageFeedbackFormState extends State<_MessageFeedbackForm> {
  late final Set<String> _tags = {...?widget.previous?.tags};
  late final _correction = TextEditingController(text: widget.previous?.correction ?? '');
  bool _sending = false;

  bool get _asksLine => _tags.any(correctable.contains);

  @override
  void dispose() {
    _correction.dispose();
    super.dispose();
  }

  void _toggle(String tag) {
    setState(() {
      if (_tags.remove(tag)) return;
      // '그분 같았어요'는 다른 것과 함께 고르지 않는다
      if (tag == 'LIKE') {
        _tags.clear();
      } else {
        _tags.remove('LIKE');
      }
      _tags.add(tag);
    });
  }

  Future<void> _send() async {
    if (_tags.isEmpty || _sending) return;
    final tags = [for (final t in feedbackChoices.keys) if (_tags.contains(t)) t];
    // 고친 한 줄은 말투·내용을 골랐을 때만 보낸다(한 줄로 모은다)
    final line = _asksLine ? _correction.text.split(RegExp(r'\s+')).where((w) => w.isNotEmpty).join(' ') : '';
    setState(() => _sending = true);
    try {
      final saved = await PersonaApi.feedback(
        widget.messageId,
        ratingOf(tags),
        tags: tags,
        correction: line.isEmpty ? null : line,
      );
      if (!mounted) return;
      Navigator.pop<SentFeedback>(context, (
        tags: tags,
        correction: line.isEmpty ? null : line,
        state: saved['state'] as String?,
      ));
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final choices = [
      for (final e in feedbackChoices.entries)
        if (e.key != 'VOICE' || widget.voice || _tags.contains('VOICE')) e,
    ];
    return SingleChildScrollView(
      padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text('이 대답은 어땠나요?', style: AppText.title),
          const SizedBox(height: 6),
          Text(
            keepAll('골라 주시면 더 그분답게 다듬어요. 여러 개 골라도 돼요.'),
            style: AppText.small.copyWith(fontSize: 14, color: AppColors.ink2),
          ),
          const SizedBox(height: 16),
          for (final e in choices)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Pill(label: e.value, selected: _tags.contains(e.key), wide: true, onTap: () => _toggle(e.key)),
            ),
          if (_asksLine) ...[
            const SizedBox(height: 8),
            FieldLabel(
              '그분이라면 어떻게 말하셨을까요? (선택)',
              // 함께하는 가족은 아래 안내(만든 분이 확인한 뒤에 반영)로 알린다
              hint: widget.member ? '한 줄이면 돼요.' : '한 줄이면 돼요. 다음 대답부터 참고해요.',
            ),
            VoiceField(
              controller: _correction,
              transcribe: (clip) => PersonaApi.transcribeAnswer(widget.personaId, clip),
              maxLength: correctionMax,
              enabled: !_sending,
              child: TextField(
                controller: _correction,
                enabled: !_sending,
                minLines: 1,
                maxLines: 3,
                maxLength: correctionMax,
                textInputAction: TextInputAction.done,
                decoration: const InputDecoration(hintText: '예: 밥은 묵었나', counterText: ''),
              ),
            ),
            // 함께하는 가족: 보내기 전에, 고친 말은 만든 분이 확인한 뒤 반영되고 만든 분께 보인다고 알린다
            if (widget.member) ...[
              const SizedBox(height: 10),
              const Notice(
                memberSendNotice,
                icon: Icons.visibility_outlined,
                color: AppColors.primary,
                background: AppColors.primarySoft,
              ),
            ],
          ],
          const SizedBox(height: 16),
          Row(
            children: [
              TextButton(
                onPressed: _sending ? null : () => Navigator.pop(context),
                style: TextButton.styleFrom(minimumSize: const Size(0, 48)),
                child: const Text('닫기'),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: FilledButton(
                  onPressed: _tags.isEmpty || _sending ? null : _send,
                  style: FilledButton.styleFrom(minimumSize: const Size(0, 50)),
                  child: Text(_sending ? '보내는 중…' : '보내기'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// 대화 화면 위 '한마디': 대화 전체에 한 줄을 남긴다(관리자만 본다). 남겼으면 true.
Future<bool> showConversationNote(
  BuildContext context, {
  required String personaId,
  required String conversationId,
}) async {
  final sent = await showDialog<bool>(
    context: context,
    builder: (_) => _ConversationNoteDialog(personaId: personaId, conversationId: conversationId),
  );
  return sent == true;
}

class _ConversationNoteDialog extends StatefulWidget {
  final String personaId;
  final String conversationId;

  const _ConversationNoteDialog({required this.personaId, required this.conversationId});

  @override
  State<_ConversationNoteDialog> createState() => _ConversationNoteDialogState();
}

class _ConversationNoteDialogState extends State<_ConversationNoteDialog> {
  final _note = TextEditingController();
  bool _sending = false;
  bool _hasText = false;

  @override
  void initState() {
    super.initState();
    _note.addListener(() {
      final has = _note.text.trim().isNotEmpty;
      if (has != _hasText) setState(() => _hasText = has);
    });
  }

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final text = _note.text.trim();
    if (text.isEmpty || _sending) return;
    setState(() => _sending = true);
    try {
      await PersonaApi.conversationFeedback(widget.conversationId, text);
      if (mounted) Navigator.pop(context, true);
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('이 대화 어땠나요?'),
      content: SizedBox(
        width: 440,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                keepAll('대화 전체에 대해 한 줄 남겨 주세요. 더 그분답게 다듬는 데 써요.'),
                style: AppText.lead,
              ),
              const SizedBox(height: 14),
              VoiceField(
                controller: _note,
                transcribe: (clip) => PersonaApi.transcribeAnswer(widget.personaId, clip),
                maxLength: correctionMax,
                enabled: !_sending,
                child: TextField(
                  controller: _note,
                  enabled: !_sending,
                  autofocus: true,
                  minLines: 1,
                  maxLines: 3,
                  maxLength: correctionMax,
                  decoration: const InputDecoration(hintText: '예: 오늘은 정말 그분과 이야기하는 것 같았어요', counterText: ''),
                ),
              ),
            ],
          ),
        ),
      ),
      actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      actions: [
        TextButton(onPressed: _sending ? null : () => Navigator.pop(context, false), child: const Text('닫기')),
        FilledButton(
          onPressed: _hasText && !_sending ? _send : null,
          style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
          child: Text(_sending ? '남기는 중…' : '남기기'),
        ),
      ],
    );
  }
}
