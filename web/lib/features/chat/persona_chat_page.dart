import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:just_audio/just_audio.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../core/analytics.dart';
import '../../core/network/api_client.dart';
import '../billing/data/billing_api.dart';
import '../gaps/gap_api.dart' show gapCountOf, gapOwnerLine, gapWaitingOf;
import '../gaps/gap_widgets.dart' show GapLeave;
import '../interview/interview_api.dart' show canAskInterview, interviewSubtitle, interviewTitle;
import 'chat_feedback.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import '../../shared/widgets/voice_input.dart';
import '../personas/data/persona_api.dart';
import '../personas/data/persona_store.dart';
import '../personas/imported.dart';
import '../personas/owner_actions.dart';
import '../legal/consent_checks.dart';
import '../legal/legal_api.dart';
import '../legal/legal_text.dart';
import '../legal/support_page.dart' show SupportPage;
import '../personas/persona_flow_page.dart' show deletePersona, leaveRemovedShare;
import '../personas/steps/questions_step.dart' show pledgeDocOf;
import '../share/member_profile_sheet.dart';
import '../share/roles.dart';
import '../share/share_api.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/usage_ring.dart';

/// 다 만든 페르소나와 대화. 글로 쓰거나 마이크로 말하고, 그분 목소리로 듣는다.
class PersonaChatPage extends StatefulWidget {
  final Map<String, dynamic> persona;

  const PersonaChatPage({super.key, required this.persona});

  @override
  State<PersonaChatPage> createState() => _PersonaChatPageState();
}

class _PersonaChatPageState extends State<PersonaChatPage> {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  final _player = AudioPlayer();

  /// 말로 보내기 녹음(형식 고르기·시간 세기는 답 칸의 말로 적기와 같이 쓴다). [maxVoiceSeconds] 가 되면
  /// 저절로 멈추고 보낸다(대답을 기다리는 중이면 끝난 뒤에).
  late final _capture = VoiceCapture(
    maxSeconds: maxVoiceSeconds,
    onLimit: () {
      if (mounted && !_sending) _toggleMic();
    },
  );
  final List<Map<String, dynamic>> _messages = [];

  /// 이번에 평가한 대답(말풍선 ⋯): 메시지 id → 고른 것·고친 한 줄·서버가 정한 state. 다시 열면 이어서 고친다.
  final Map<String, SentFeedback> _rated = {};
  String? _conversationId;

  /// 이번에 대화를 나눴는지(보내고 대답을 받았다). 나눴으면 화면을 나올 때 '더 알려 주실 기억' 하나를 묻는다.
  bool _talked = false;

  /// 이 화면이 있는 앱의 라우터(나온 뒤 시트를 띄울 곳). 라우터 없이 그린 시험 화면에서는 null.
  GoRouter? _router;
  String? _loadError;
  String? _pendingUser;
  String? _speakingId;
  String? _loadingSpeechId;
  bool _sending = false;
  bool get _recording => _capture.recording;

  /// 듣기 요청 차례. 나중에 누른 듣기·녹음 시작이 앞선 듣기를 이긴다(늦게 온 소리가 덮어 나오거나,
  /// 녹음하는 동안 대답이 스피커로 나와 녹음에 섞이지 않게).
  int _speechSeq = 0;

  /// 대화를 처음 읽기를 저절로 한 번 더 해 봤는지.
  bool _openRetried = false;

  /// 대답을 바로 그분 목소리로 들려준다(기본으로 켬). 머리글 스피커 단추로 끌 수 있다.
  bool _autoSpeak = true;
  bool _hasText = false;
  late bool _hasVoice = widget.persona['hasVoice'] == true;
  String? _voiceProgress;

  String get _id => widget.persona['id'];

  /// 화면에서 부르는 말(할머니). 성함은 보이지 않는다.
  String get _title => personaTitle(widget.persona);
  String get _call => _title;
  bool get _canSpeak => _hasVoice && _voiceProgress == null;

  /// 예전 자료로 가져온 대화 상대(지침을 다시 만드는 기능은 숨긴다). [isImportedPersona]
  bool get _imported => isImportedPersona(widget.persona);

  /// 초대로 함께하는 가족(대화·듣기·평가·내 정보 고치기·그만두기만, 요금제 안내는 보지 않는다). [isMember]
  bool get _member => isMember(widget.persona);

  /// 함께하는 가족에게 보일 만든 분 이름(지은).
  String get _ownerName => ownerNameOf(widget.persona);

  /// 더 알려 주실 기억 수(상세의 gapCount). 머리글 ⋯ 의 작은 숫자로만 보인다(대화 중에 묻지 않는다).
  int get _gapCount => gapCountOf(widget.persona);

  /// 만든 분이 확인할 가족 답·가족 고침 수(상세의 gapWaiting, 가족은 0). 열린 질문이 없어도 배지에 더한다.
  int get _gapWaiting => gapWaitingOf(widget.persona);

  /// 머리글 ⋯ 와 '더 알려 주실 기억' 줄의 작은 숫자.
  int get _gapBadge => _gapCount + _gapWaiting;

  /// 무료 체험이 끝나 대화를 멈췄을 때 보여줄 말
  String? _paywall;

  /// 함께하는 가족: 만든 분 쪽 사정으로 지금은 이야기할 수 없을 때 서버가 준 말(share_paused).
  String? _paused;

  /// 처리 게이트가 막았다는 오류(만든 분 409). 받으면 상세를 다시 읽어 카드를 맞춘다.
  static const _gateCodes = {'stopped', 'unable', 'bereaved', 'guardian', 'subject_consent'};

  /// 함께하는 가족의 첫 안내 카드를 이미 봤는지(이 기기에 남긴다).
  bool _tipSeen = true;
  static const _tipKey = 'talkto.memberTipSeen';

  /// 정보 고치기·녹음 더하기 뒤 지침을 다시 만드는 일(페르소나 화면이 다 될 때까지 다시 읽어 준다).
  String? get _bgKind {
    final jobs = widget.persona['jobs'] as Map?;
    for (final kind in const ['UPDATE', 'EXTEND']) {
      if ((jobs?[kind] as Map?)?['status'] == 'RUNNING') return kind;
    }
    return null;
  }

  Map? get _bgJob => _bgKind == null ? null : (widget.persona['jobs'] as Map)[_bgKind] as Map?;
  bool get _updating => _bgKind != null;

  /// 뒤에서 도는 목소리 (다시) 만들기(새 목소리로 옮기기, 다른 기기에서 누른 다시 만들기).
  /// 그동안은 다시 만들기를 막는다(서버도 막는다). 다 될 때까지 지금 목소리로 대답한다.
  Map? get _voiceJob {
    final job = (widget.persona['jobs'] as Map?)?['VOICE'] as Map?;
    return job?['status'] == 'RUNNING' ? job : null;
  }

  @override
  void initState() {
    super.initState();
    _open();
    // 체험이 끝났는지 알면 입력 칸 대신 요금제 안내를 보인다
    billingStore.addListener(_billingChanged);
    billingStore.loadPersona(_id); // 이 페르소나의 남은 대화 횟수를 새로 본다
    _input.addListener(() {
      final has = _input.text.trim().isNotEmpty;
      if (has != _hasText) setState(() => _hasText = has);
    });
    SharedPreferences.getInstance().then((prefs) {
      if (!mounted) return;
      setState(() {
        _autoSpeak = prefs.getBool('talkto.autoSpeak') ?? true;
        _tipSeen = prefs.getBool(_tipKey) ?? false;
      });
    });
    _player.playerStateStream.listen((state) {
      final done = !state.playing || state.processingState == ProcessingState.completed;
      if (done && mounted && _speakingId != null) setState(() => _speakingId = null);
    });
  }

  void _billingChanged() {
    if (!mounted) return;
    setState(() {
      // 함께하는 가족: 새로 읽은 요금제 보기에 멈춤이 없으면(만든 분이 다시 열었다) 입력 칸을 돌려준다
      final view = _billing;
      if (_paused != null && view != null && view['paused'] == null && view['active'] == true) _paused = null;
    });
  }

  /// 대화 화면 위에 늘 보이는 단추: 기억 더 채우기(만든 분, 가져온 대화 상대 빼고), 기억 인터뷰 받기(만든 분,
  /// 나 자신 대화 상대 빼고 — 2026-10-25 머리글 ⋯ 안에서 옮김)와 작은 '한마디'(대화 전체 평가).
  /// 나머지 관리(정보 고치기·녹음 더하기 등)는 머리글 ⋯ 안에 둔다.
  Widget _featureStrip() {
    final more = !_imported && !_member;
    final interview = canAskInterview(widget.persona);
    return Container(
      width: double.infinity,
      color: AppColors.paper,
      padding: EdgeInsets.fromLTRB(16, more || interview ? 12 : 8, 16, 2),
      child: PageBody(
        maxWidth: 720,
        child: LayoutBuilder(
          builder: (context, box) {
            final narrow = box.maxWidth < 560;
            final tiles = [
              if (more)
                _FeatureTile(
                  icon: Icons.quiz_outlined,
                  compact: narrow,
                  title: '기억 더 채우기',
                  subtitle: narrow ? '더 그분답게' : '인생 이야기·즐겨 해 주시던 음식을 알려 주면 더 그분답게 대답해요',
                  onTap: () {
                    Analytics.event('more_tile_clicked');
                    context.go('/p/$_id/more');
                  },
                ),
              if (interview)
                _FeatureTile(
                  icon: Icons.record_voice_over_outlined,
                  compact: narrow,
                  title: narrow ? '기억 인터뷰' : interviewTitle,
                  subtitle: narrow ? '대표가 직접 들어요' : interviewSubtitle,
                  onTap: () => context.go('/p/$_id/interview'),
                ),
            ];
            // 단추가 둘이면 휴대폰에서는 한마디를 아이콘만 둔다(두 제목이 잘리지 않게)
            final note = _noteButton(narrow, iconOnly: narrow && tiles.length > 1);
            if (tiles.isEmpty) return Align(alignment: Alignment.centerRight, child: note);
            return Row(
              children: [
                for (final tile in tiles) ...[Expanded(child: tile), const SizedBox(width: 8)],
                note,
              ],
            );
          },
        ),
      ),
    );
  }

  /// 대화 전체에 한 줄 남기기(작게, 대화 화면 위). iconOnly 면 글 없이 아이콘만(이름은 읽어 주기·말풍선 도움말로).
  Widget _noteButton(bool narrow, {bool iconOnly = false}) {
    final onPressed = _conversationId == null ? null : _leaveNote;
    final style = OutlinedButton.styleFrom(
      minimumSize: const Size(44, 44),
      padding: EdgeInsets.symmetric(horizontal: iconOnly ? 0 : 12),
      foregroundColor: AppColors.ink2,
      backgroundColor: AppColors.card,
    );
    const icon = Icon(Icons.rate_review_outlined, size: 18);
    return Tooltip(
      message: '이 대화에 한마디 남기기',
      child: iconOnly
          ? Semantics(
              label: '대화 한마디',
              button: true,
              child: OutlinedButton(onPressed: onPressed, style: style, child: icon),
            )
          : OutlinedButton.icon(
              onPressed: onPressed,
              style: style,
              icon: icon,
              label: Text(narrow ? '한마디' : '대화 한마디'),
            ),
    );
  }

  Future<void> _leaveNote() async {
    final conversationId = _conversationId;
    if (conversationId == null) return;
    final sent = await showConversationNote(context, personaId: _id, conversationId: conversationId);
    if (sent && mounted) showMessage(context, '남겨 주셔서 고마워요. 더 그분답게 다듬을게요.');
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _router = GoRouter.maybeOf(context);
  }

  @override
  void didUpdateWidget(covariant PersonaChatPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    // 위 화면이 새로 읽은 정보(다른 기기·뒤에서 새로 만든 목소리)를 준다. 목소리가 생기거나 없어진 것을 맞춘다
    final hasVoice = widget.persona['hasVoice'];
    if (hasVoice is bool && hasVoice != _hasVoice) _hasVoice = hasVoice;
  }

  @override
  void dispose() {
    // 대화를 나누고 나왔으면, 나온 뒤(목록 화면에서) '더 알려 주실 기억' 하나를 묻는다. 대화 중에는 묻지 않는다
    final router = _router;
    if (_talked && router != null) unawaited(GapLeave.afterChat(router, widget.persona));
    billingStore.removeListener(_billingChanged);
    _capture.dispose();
    _input.dispose();
    _scroll.dispose();
    _player.dispose();
    super.dispose();
  }

  Future<void> _open() async {
    try {
      final conversationId = await PersonaApi.conversation(_id);
      final messages = await PersonaApi.messages(conversationId);
      if (!mounted) return;
      setState(() {
        _conversationId = conversationId;
        _messages
          ..clear()
          ..addAll(messages);
      });
      _toBottom(jump: true);
    } catch (error) {
      if (!mounted) return;
      // 함께 쓰던 대화 상대에서 그사이 빠졌다
      if (_member && error is ApiException && error.statusCode == 404) {
        leaveRemovedShare(context);
        return;
      }
      setState(() => _loadError = error.toString());
      // 서버를 바꾸는 중(배포)이거나 연결이 잠깐 끊겼으면 한 번 더 읽어 본다. 그래도 안 되면 '다시 불러오기'
      if (!_openRetried && ApiException.isTransient(error)) {
        _openRetried = true;
        Timer(const Duration(seconds: 3), () {
          if (mounted && _conversationId == null && _loadError != null) _retryOpen();
        });
      }
    }
  }

  void _retryOpen() {
    setState(() => _loadError = null);
    _open();
  }

  void _toBottom({bool jump = false}) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      final end = _scroll.position.maxScrollExtent;
      if (jump) {
        _scroll.jumpTo(end);
      } else {
        _scroll.animateTo(end, duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
      }
    });
  }

  Future<void> _deliver(Future<Exchange> Function() send, String shown) async {
    setState(() {
      _sending = true;
      _pendingUser = shown;
    });
    _toBottom();
    try {
      final result = await send();
      final pair = result.messages;
      if (!mounted) return;
      setState(() => _messages.addAll(pair));
      _talked = true;
      // 머리글의 '남은 대화'를 바로 줄인다
      billingStore.setConversationsLeft(_id, result.left);
      // 함께하는 가족의 첫 안내는 첫 대화를 하면 다시 보이지 않는다
      if (_member && !_tipSeen) unawaited(_dismissTip());
      _toBottom();
      personaStore.load();
      // 대화 횟수를 다 쓴 뒤의 대답은 글로만 온다(목소리로 들려주지 않는다)
      if (_autoSpeak && _canSpeak && !result.textOnly) _speak(pair.last['id']);
    } catch (error) {
      if (mounted) {
        if (shown.isNotEmpty && _input.text.isEmpty && !shown.startsWith('🎤')) _input.text = shown;
        if (_member && error is ApiException && error.statusCode == 404) {
          // 함께 쓰던 대화 상대에서 그사이 빠졌다(내보내기·그만 함께 쓰기)
          leaveRemovedShare(context);
        } else if (_member && error is ApiException && error.code == 'share_paused') {
          // 만든 분 쪽 사정으로 멈췄다: 입력 칸 대신 그 까닭을 차분히 보인다(나눈 대화는 그대로)
          billingStore.loadPersona(_id);
          setState(() => _paused = error.message);
        } else if (error is ApiException && error.statusCode == 402 && !_prelaunch) {
          // 체험이 끝났거나 이번 달 대화를 다 썼다: 입력 칸 대신 요금제 안내를 보인다
          billingStore.loadPersona(_id);
          setState(() => _paywall = error.message);
        } else {
          // 결제 전에는 체험이 끝나지 않으니 요금제 안내로 막지 않는다(상태만 다시 읽는다)
          if (error is ApiException && error.statusCode == 402) billingStore.loadPersona(_id);
          // 409: 다른 창에서 횟수를 다 써 지금은 글로만 대답하는 등 요금제 상태가 바뀌었다.
          // 다시 읽어 마이크·안내를 맞춘다.
          if (error is ApiException && error.statusCode == 409) billingStore.loadPersona(_id);
          // 멈춤·유족 확인처럼 처리 게이트가 막았으면 상세를 다시 읽어 입력 칸 자리의 카드를 맞춘다
          if (error is ApiException && _gateCodes.contains(error.code)) notifyPersonaEdited(_id);
          showMessage(context, error);
        }
      }
    } finally {
      if (mounted) {
        setState(() {
          _sending = false;
          _pendingUser = null;
        });
      }
    }
  }

  Future<void> _sendText([String? preset]) async {
    final text = (preset ?? _input.text).trim();
    if (text.isEmpty || _sending || _conversationId == null) return;
    _input.clear();
    await _deliver(() => PersonaApi.sendText(_conversationId!, text), text);
  }

  /// 누르면 녹음, 다시 누르면 서버로 보내 받아쓰고 바로 대답을 받는다.
  Future<void> _toggleMic() async {
    if (_conversationId == null || _sending) return;
    if (_recording) {
      VoiceClip? clip;
      try {
        clip = await _capture.stop();
      } catch (error) {
        // 녹음기·파일 읽기의 영어 원문은 보이지 않는다
        if (mounted) {
          setState(() {});
          showMessage(context, error is ApiException ? error : '녹음을 보내지 못했어요. 다시 말씀해 주세요.');
        }
        return;
      }
      if (!mounted) return;
      setState(() {});
      if (clip == null) {
        // 마이크가 소리를 받지 못했다(통화 중이거나 다른 앱이 마이크를 쓰는 중). 말없이 넘어가지 않는다
        showMessage(context, '녹음된 소리가 없어요. 통화 중이거나 다른 앱이 마이크를 쓰고 있는지 확인하고 다시 말씀해 주세요.');
        return;
      }
      final sound = clip;
      await _deliver(() => PersonaApi.sendVoice(_conversationId!, sound.bytes, sound.filename), '🎤 …');
      return;
    }
    final failure = await _capture.start(
      beforeStart: () async {
        // 들려주던(들려주려고 읽던) 대답을 멈춘다. 녹음하는 동안 나오면 그분 목소리가 받아쓰기에 섞인다
        _speechSeq++;
        if (mounted && _loadingSpeechId != null) setState(() => _loadingSpeechId = null);
        await _player.pause();
      },
    );
    if (!mounted) return;
    if (failure != null) {
      showMessage(context, switch (failure) {
        VoiceStartFailure.denied => '마이크를 쓸 수 없어요. 브라우저에서 마이크를 허용해 주세요.',
        VoiceStartFailure.busy => '마이크를 켜지 못했어요. 통화 중이거나 다른 앱이 마이크를 쓰고 있는지 확인해 주세요.',
        VoiceStartFailure.unsupported => '이 브라우저에서는 말로 보낼 수 없어요. 글로 보내 주세요.',
      });
      return;
    }
    setState(() {});
  }

  Future<void> _cancelRecording() async {
    await _capture.cancel();
    if (mounted) setState(() {});
  }

  Future<void> _speak(String messageId) async {
    if (_speakingId == messageId) {
      await _player.pause();
      return;
    }
    // 나중에 누른 듣기가 이긴다. 그사이 녹음을 시작했으면 들려주지 않는다(녹음에 섞이지 않게)
    final seq = ++_speechSeq;
    bool current() => mounted && seq == _speechSeq && !_recording;
    setState(() => _loadingSpeechId = messageId);
    try {
      final url = await PersonaApi.speech(messageId);
      if (!current()) return;
      await _player.setUrl(url);
      if (!current()) return;
      setState(() => _speakingId = messageId);
      // 대답이 오고 나서 저절로 들려줄 때는 브라우저(특히 아이폰 사파리)가 막을 수 있다.
      // 막히면 멈춘 상태로 되돌리고 '듣기'를 누르게 알려 준다(멈춘 채 '재생 중'으로 남지 않게).
      unawaited(_player.play().catchError((Object _) async {
        await _player.pause();
        if (mounted) {
          setState(() => _speakingId = null);
          showMessage(context, "소리가 저절로 나오지 않았어요. 대답 아래 '듣기'를 눌러 주세요.");
        }
      }));
    } on PlayerInterruptedException {
      // 다른 대답을 듣기로 해 앞서 읽던 소리를 멈췄다(알릴 일이 아니다)
    } catch (error) {
      // 앞서 누른(뒤로 밀린) 듣기의 실패는 알리지 않는다
      if (!mounted || !current()) return;
      if (_member && error is ApiException && error.statusCode == 404) {
        leaveRemovedShare(context);
      } else if (error is ApiException && error.statusCode == 402) {
        // 함께하는 가족은 요금제를 고르지 않는다(만든 분의 요금제로 이야기한다)
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(
            content: Text(error.message),
            action: _member
                ? null
                : SnackBarAction(label: '요금제 보기', onPressed: () => context.go('/plans?persona=$_id')),
          ));
      } else {
        if (_member && error is ApiException && error.code == 'share_paused') setState(() => _paused = error.message);
        // 멈춤·유족 확인으로 막혔으면 상세를 다시 읽어 카드를 맞춘다
        if (!_member && error is ApiException && _gateCodes.contains(error.code)) notifyPersonaEdited(_id);
        // 소리 재생기의 영어 원문('(4) …')은 보이지 않는다
        showMessage(context, error is ApiException ? error : '지금은 들려 드리지 못했어요. 잠시 뒤 다시 눌러 주세요.');
      }
    } finally {
      if (mounted && seq == _speechSeq) setState(() => _loadingSpeechId = null);
    }
  }

  /// 말풍선 ⋯: 다섯 가지 중 고르고, 말투·내용이면 '그분이라면 어떻게 말하셨을까요?' 한 줄(글·🎤).
  Future<void> _rate(String messageId) async {
    final sent = await showMessageFeedback(
      context,
      personaId: _id,
      messageId: messageId,
      voice: _hasVoice,
      member: _member,
      previous: _rated[messageId],
    );
    if (sent == null || !mounted) return;
    setState(() => _rated[messageId] = sent);
    showMessage(context, feedbackThanks(sent, ownerName: _ownerName));
  }

  /// 평가한 대답 아래 작은 글: 고른 것(첫째)과 몇 개 더.
  String _ratedText(SentFeedback sent) {
    final first = feedbackChoices[sent.tags.firstOrNull] ?? '';
    final more = sent.tags.length - 1;
    return more > 0 ? '$first 외 $more' : first;
  }

  Future<void> _remakeVoice() async {
    // 가져온 대화 상대는 새로 만들지 않고, 준비해 둔 목소리를 같은 확인을 받은 뒤 붙인다(서버 같은 주소).
    final attach = !_hasVoice && hasPendingVoice(widget.persona);
    final working = attach ? '목소리 붙이는 중' : '목소리 만드는 중';
    // 목소리를 (다시) 만들기 직전마다 그분 상태에 맞는 확인(pledgeDoc)을 받는다.
    final ok = await showDialog<bool>(
          context: context,
          builder: (_) => _VoiceConsentDialog(
            title: _title,
            pledgeDoc: pledgeDocOf(widget.persona),
            remake: _hasVoice,
            attach: attach,
            bereavedDeclared: widget.persona['bereavedDeclared'] == true,
            onBereaved: () => context.go('/p/$_id/bereaved'),
          ),
        ) ??
        false;
    if (!ok || !mounted) return;
    setState(() => _voiceProgress = working);
    try {
      await PersonaApi.remakeVoice(_id, voiceConsent: true);
      widget.persona['voiceConsented'] = true;
      final started = DateTime.now();
      while (mounted) {
        await Future.delayed(const Duration(seconds: 2));
        if (!mounted) break;
        Map<String, dynamic> detail;
        try {
          detail = await PersonaApi.detail(_id);
        } catch (error) {
          // 잠깐 못 읽어도(배포 중·연결 끊김) 서버는 계속 만든다. 다음 차례에 다시 읽는다
          if (!ApiException.isTransient(error)) rethrow;
          if (DateTime.now().difference(started) > const Duration(minutes: 10)) {
            throw const ApiException('목소리 만드는 상황을 확인하지 못했어요. 조금 뒤 다시 확인해 주세요.');
          }
          continue;
        }
        final job = (detail['jobs'] as Map?)?['VOICE'] as Map?;
        // 붙이기는 서버가 바로 끝낸다: 목소리가 생겼으면 작업 기록이 없어도 끝
        final attached = attach && detail['hasVoice'] == true && job?['status'] != 'RUNNING';
        if (!attached && (job == null || job['status'] == 'RUNNING')) {
          final percent = job?['percent'];
          if (mounted) setState(() => _voiceProgress = percent == null ? working : '$working · $percent%');
          continue;
        }
        if (!attached && job!['status'] == 'FAILED') {
          throw ApiException(job['error'] ?? (attach ? '목소리를 붙이지 못했어요.' : '목소리를 만들지 못했어요.'));
        }
        _hasVoice = true;
        widget.persona['hasVoice'] = true;
        if (attach) widget.persona['voicePending'] = false;
        if (mounted) showMessage(context, attach ? '목소리를 붙였어요. 이제 대답을 그분 목소리로 들려 드려요.' : '새 목소리로 바꿨어요.');
        break;
      }
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _voiceProgress = null);
    }
  }

  Future<void> _toggleAutoSpeak() async {
    setState(() => _autoSpeak = !_autoSpeak);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool('talkto.autoSpeak', _autoSpeak);
    if (mounted) showMessage(context, _autoSpeak ? '대답을 바로 목소리로 들려드려요.' : '대답을 글로만 보여드려요.');
  }

  /// 함께하는 가족: 나에 대한 정보(이름·관계·부르는 말·말 높이)를 고친다. 다음 대답부터 이대로 부른다.
  Future<void> _editMe() async {
    final me = memberMeOf(widget.persona);
    final shareId = (widget.persona['shareId'] ?? me['shareId']) as String?;
    if (shareId == null) return;
    var relations = const <String>[];
    // 아이(만 19세 미만)의 대화 상대는 법정대리인과 직계가족만: 서버가 준 관계 목록에서만 고른다
    final minorScope = widget.persona['minorScope'] == true && widget.persona['relations'] is List;
    try {
      relations = minorScope
          ? [for (final r in widget.persona['relations'] as List) '$r']
          : [for (final r in (await PersonaApi.meta())['myRelations'] as List? ?? const []) '$r'];
    } catch (_) {
      // 관계 목록이 없어도 직접 적을 수 있다
    }
    if (!mounted) return;
    final saved = await showProfileSheet(
      context,
      heading: '나에 대한 정보 고치기',
      lead: '고치면 다음 대답부터 이대로 나를 알아보고 불러요.',
      initial: me,
      voice: ProfileVoice.member,
      ownerName: _ownerName,
      // 나 자신 대화 상대면 '내가 ○○님을 부르는 말'은 묻지 않고, 부르는 말 추천은 서버 것(그분이
      // 여자분인지 남자분인지 맞춘 표)을 쓴다. 예전 서버는 둘 다 주지 않는다(그때는 묻고, 앱 안의 표).
      self: widget.persona['isSelf'] == true,
      suggestions: widget.persona['addressSuggestions'] is Map ? widget.persona['addressSuggestions'] as Map : null,
      relations: relations,
      minorScope: minorScope,
      onSave: (profile) async {
        final updated = await ShareApi.update(_id, shareId, profile);
        widget.persona['me'] = {...me, ...updated};
      },
    );
    if (saved == null || !mounted) return;
    setState(() => widget.persona['addressTerm'] = saved['addressTerm']);
    personaStore.load();
    notifyPersonaEdited(_id);
    showMessage(context, '고쳤어요. 다음 대답부터 이대로 불러요.');
  }

  /// 함께하는 가족: 함께 쓰기 그만두기(내가 나눈 대화는 지워진다).
  Future<void> _leave() async {
    final me = memberMeOf(widget.persona);
    final shareId = (widget.persona['shareId'] ?? me['shareId']) as String?;
    if (shareId == null) return;
    final ok = await confirmAction(
      context,
      '${josa(_title, '과', '와')} 함께 쓰기를 그만둘까요?',
      '내가 나눈 대화는 지워지고, 다시 함께하려면 새 링크가 필요해요.',
      action: '그만두기',
    );
    if (!ok || !mounted) return;
    try {
      final told = await ShareApi.end(_id, shareId);
      await personaStore.load();
      if (!mounted) return;
      // 내가 나눈 대화를 지웠으면 서버 백업에 남는 날까지 알린다(첫 화면으로 가도 보인다)
      if (told != null) showDeletionNote(context, told);
      context.go('/');
    } catch (error) {
      if (mounted) showMessage(context, error);
    }
  }

  /// 대화 상대 정보와 관리(정보 고치기, 녹음·남긴 글 더하기, 목소리 다시 만들기, 지우기).
  /// 함께하는 가족은 나에 대한 정보 고치기와 함께 쓰기 그만두기만, 만든 사람은 맨 위에 가족과 함께 쓰기.
  Future<void> _showInfo() async {
    final cycle = _cycleText;
    // 목소리를 만드는 회사·녹음 종류(통화/일반)는 내부 구분이라 보이지 않는다
    final voiceJob = _voiceJob;
    final voicePercent = voiceJob?['percent'] == null ? '' : ' · ${voiceJob!['percent']}%';
    final actions = personaActionsOf(widget.persona, hasVoice: _hasVoice);
    final voiceText = _voiceProgress ??
        (voiceJob != null
            ? (_hasVoice ? '새로 만드는 중$voicePercent · 그동안은 지금 목소리로 들려 드려요' : '만드는 중$voicePercent')
            : !_hasVoice
                ? (_member
                    ? '지금은 글로만 대답해요'
                    : !_imported
                    ? '아직 없어요. 아래에서 목소리를 만들어 주세요.'
                    : (actions.voice != null ? '준비돼 있어요. 아래에서 확인하고 붙여 주세요.' : '아직 없어요'))
                : '있어요 · 그분 목소리');
    // 호칭(제목)과 다를 때만 관계를 곁들인다. 성함은 보이지 않는다. 함께하는 가족에게는 누가 만들었는지.
    final relation = _member
        ? '$_ownerName님과 함께'
        : [widget.persona['relation'], widget.persona['addressTerm']]
            .whereType<String>()
            .map((s) => s.trim())
            .where((s) => s.isNotEmpty && s != _title)
            .toSet()
            .join(' · ');
    final me = memberMeOf(widget.persona);
    final members = membersOf(widget.persona);

    Widget body(BuildContext sheet) {
      void run(VoidCallback action) {
        Navigator.pop(sheet);
        action();
      }

      return SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Center(child: Avatar(_title, size: 72)),
            const SizedBox(height: 12),
            Text(_title, textAlign: TextAlign.center, style: AppText.title),
            if (relation.isNotEmpty) Text(relation, textAlign: TextAlign.center, style: AppText.small),
            const SizedBox(height: 20),
            AppCard(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
              color: AppColors.paper,
              child: Row(
                children: [
                  const Icon(Icons.graphic_eq_rounded, size: 20, color: AppColors.primary),
                  const SizedBox(width: 10),
                  const Text('목소리', style: AppText.label),
                  const SizedBox(width: 12),
                  Expanded(child: Text(voiceText, textAlign: TextAlign.right, style: AppText.small)),
                ],
              ),
            ),
            if (cycle != null) ...[
              const SizedBox(height: 8),
              AppCard(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
                color: AppColors.paper,
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(Icons.chat_bubble_outline_rounded, size: 20, color: AppColors.primary),
                    const SizedBox(width: 10),
                    const Text('남은 대화', style: AppText.label),
                    const SizedBox(width: 12),
                    Expanded(child: Text(keepAll(cycle), textAlign: TextAlign.right, style: AppText.small)),
                  ],
                ),
              ),
            ],
            if (actions.profile && me.isNotEmpty) ...[
              const SizedBox(height: 8),
              AppCard(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
                color: AppColors.paper,
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(Icons.person_outline_rounded, size: 20, color: AppColors.primary),
                    const SizedBox(width: 10),
                    const Text('나', style: AppText.label),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Text(
                        keepAll(memberLine(me)),
                        textAlign: TextAlign.right,
                        style: AppText.small,
                      ),
                    ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 8),
            // 만든 사람(가져온 대화 상대도)은 맨 위에 가족과 함께 쓰기
            if (actions.share)
              _sheetItem(
                Icons.group_add_outlined,
                '가족과 함께 쓰기',
                () => run(() => context.go('/p/$_id/family')),
                subtitle: members > 0
                    ? '가족 $members명과 함께 쓰는 중'
                    : '링크를 보내면 가족도 ${josa(_title, '과', '와')} 이야기해요',
              ),
            if (actions.profile)
              _sheetItem(Icons.badge_outlined, '나에 대한 정보 고치기', () => run(_editMe)),
            // 가져온 대화 상대는 지침을 다시 만드는 단추(정보 고치기·기억 더 채우기·녹음 더하기·목소리 다시 만들기)를
            // 숨긴다. 기억 더 채우기 자리에는 '종교·건강 이야기 동의'가, 기다리는 목소리가 있으면 '목소리 붙이기'가
            // 보인다. [personaActionsOf]
            if (actions.edit)
              _sheetItem(Icons.edit_outlined, '정보 고치기', () => run(() => context.go('/p/$_id/edit'))),
            if (actions.more != null)
              _sheetItem(
                _imported ? Icons.lock_outline_rounded : Icons.quiz_outlined,
                actions.more!,
                () => run(() => context.go('/p/$_id/more')),
              ),
            // 더 알려 주실 기억: 만든 분은 늘, 함께하는 가족은 물어봐 달라고 한 질문이 있을 때만
            if (!_member || _gapCount > 0)
              _sheetItem(
                Icons.lightbulb_outline_rounded,
                '더 알려 주실 기억',
                () => run(() => context.go('/p/$_id/gaps')),
                subtitle: _member
                    ? '$_ownerName님이 여쭤봐 달라고 한 질문 $_gapCount개'
                    : gapOwnerLine(_gapCount, _gapWaiting) ?? '대화하다 아직 모르는 이야기를 모아 둬요',
                badge: _gapBadge,
              ),
            // 기억 인터뷰는 대화 화면 위 단추로 옮겼다(_featureStrip)
            if (actions.recordings)
              _sheetItem(Icons.library_add_outlined, '녹음 더하기', () => run(() => context.go('/p/$_id/recordings'))),
            // 남긴 글·목소리·지우기는 만든 사람만
            if (!_member)
              _sheetItem(
                Icons.note_add_outlined,
                '남긴 글 더하기 (카카오톡·문자·일기)',
                () => run(() => context.go('/p/$_id/materials')),
              ),
            if (actions.voice != null)
              _sheetItem(
                Icons.mic_none_rounded,
                actions.voice!,
                _voiceProgress == null && voiceJob == null ? () => run(_remakeVoice) : null,
              ),
            // 고객센터: 함께 쓰는 가족도. 문의 코드에 이 대화 상대 짧은 코드를 붙인다
            _sheetItem(
              Icons.support_agent_outlined,
              '고객센터',
              () => run(() => context.go(SupportPage.pathFor(_id))),
              subtitle: '카카오톡 채널이나 이메일로 물어보세요',
            ),
            if (actions.leave)
              _sheetItem(Icons.logout_rounded, '함께 쓰기 그만두기', () => run(_leave), danger: true)
            else
              _sheetItem(
                Icons.delete_outline_rounded,
                '이 대화 상대 지우기',
                () => run(() => deletePersona(context, widget.persona)),
                danger: true,
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
            constraints: const BoxConstraints(maxWidth: 420),
            child: Padding(padding: const EdgeInsets.fromLTRB(24, 28, 24, 16), child: body(sheet)),
          ),
        ),
      );
    } else {
      await showModalBottomSheet<void>(
        context: context,
        isScrollControlled: true,
        builder: (sheet) => SafeArea(
          child: Padding(padding: const EdgeInsets.fromLTRB(20, 0, 20, 12), child: body(sheet)),
        ),
      );
    }
  }

  Widget _sheetItem(
    IconData icon,
    String label,
    VoidCallback? onTap, {
    bool danger = false,
    String? subtitle,
    int badge = 0,
  }) {
    final color = onTap == null ? AppColors.muted : (danger ? AppColors.danger : AppColors.ink);
    final title = Text(label, style: TextStyle(fontSize: 15.5, color: color, fontWeight: FontWeight.w600));
    return InkWell(
      borderRadius: BorderRadius.circular(12),
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 14),
        child: Row(
          children: [
            Badge(
              isLabelVisible: badge > 0,
              label: Text('$badge'),
              backgroundColor: AppColors.clay,
              child: Icon(icon, size: 21, color: color),
            ),
            const SizedBox(width: 14),
            if (subtitle == null)
              Flexible(child: title)
            else
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    title,
                    const SizedBox(height: 3),
                    Text(keepAll(subtitle), style: AppText.small),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final wide = Breakpoints.isWide(context);
    // 만든 분 카드(서버 actions). 함께하는 가족에게는 없다(가족은 share_paused 글을 받는다)
    final actions = _member ? const <Map<String, dynamic>>[] : ownerActionsOf(widget.persona);
    final blocking = _member ? null : blockingActionOf(widget.persona);
    final notes = [for (final a in actions) if (a['blocking'] != true) a];
    return Scaffold(
      appBar: AppBar(
        automaticallyImplyLeading: !wide,
        titleSpacing: wide ? 24 : 0,
        toolbarHeight: 64,
        title: InkWell(
          onTap: _showInfo,
          borderRadius: BorderRadius.circular(12),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Row(
              children: [
                Avatar(_title, size: 40),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(_title, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 17)),
                      if (_subtitle.isNotEmpty)
                        Text(
                          _subtitle,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: AppText.small.copyWith(fontSize: 12.5),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
        actions: [
          if (_usage != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 4),
              child: UsageRing(
                left: _usage!.$1,
                limit: _usage!.$2,
                size: 42,
                tooltip: _member
                    ? '가족이 함께 쓰는 대화 ${_usage!.$1}번 / ${_usage!.$2}번'
                    : _cycleText ??
                        (_prelaunch
                            ? '남은 대화 ${_usage!.$1}번 / ${_usage!.$2}번 · 대화 상대를 만든 날부터 30일마다 다시 채워져요'
                            : '이번 달 남은 대화 ${_usage!.$1}번 / ${_usage!.$2}번'),
              ),
            ),
          if (_canSpeak)
            IconButton(
              tooltip: _autoSpeak ? '대답 소리 끄기' : '대답을 바로 목소리로 듣기',
              color: _autoSpeak ? AppColors.primary : AppColors.ink2,
              onPressed: _toggleAutoSpeak,
              icon: Icon(_autoSpeak ? Icons.volume_up_rounded : Icons.volume_off_outlined),
            ),
          IconButton(
            tooltip: '더 보기',
            onPressed: _showInfo,
            // 더 알려 주실 기억이 있으면 작은 숫자(알림은 보내지 않는다)
            icon: Badge(
              isLabelVisible: _gapBadge > 0,
              label: Text('$_gapBadge'),
              backgroundColor: AppColors.clay,
              child: const Icon(Icons.more_horiz_rounded),
            ),
          ),
          SizedBox(width: wide ? 12 : 4),
        ],
        bottom: const PreferredSize(preferredSize: Size.fromHeight(1), child: Divider(height: 1)),
      ),
      body: Column(
        children: [
          if (_updating)
            Container(
              width: double.infinity,
              color: AppColors.primarySoft,
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  const SizedBox(width: 12, height: 12, child: CircularProgressIndicator(strokeWidth: 1.8)),
                  const SizedBox(width: 10),
                  Flexible(
                    child: Text(
                      keepAll('${_bgKind == 'EXTEND' ? '새 녹음을' : '고친 정보를'} 익히고 있어요'
                          '${_bgJob?['percent'] != null ? ' · ${_bgJob!['percent']}%' : ''}. 그동안에도 대화할 수 있어요.'),
                      style: const TextStyle(fontSize: 13, color: AppColors.primaryDeep),
                    ),
                  ),
                ],
              ),
            ),
          if (MediaQuery.viewInsetsOf(context).bottom == 0) ...[
            // 가져온 대화 상대·함께하는 가족은 기억 더 채우기를 쓰지 않는다(한마디만)
            _featureStrip(),
            if (_cycleText != null) _cycleLine(),
          ],
          Expanded(child: _log()),
          // 만든 분이 할 일(그분 동의 녹음·법정대리인 확인 등): 막지 않는 카드는 입력 칸 위에
          if (blocking == null && notes.isNotEmpty && MediaQuery.viewInsetsOf(context).bottom == 0) _ownerCards(notes),
          // 함께하는 가족: 만든 분 쪽 사정으로 멈췄으면 입력 칸 대신 그 까닭(나눈 대화는 그대로 본다)
          if (_member && _pausedText != null)
            _pausedCard(_pausedText!)
          // 만든 분: 멈춘 대화 상대·유족 확인 전이면 입력 칸 자리에 그 카드(나눈 대화는 그대로 본다)
          else if (blocking != null)
            _blockedCard(blocking)
          // 결제 전에는 무료 체험에 한도가 없어 요금제 안내로 막지 않는다
          else if (!_prelaunch && (_paywall != null || _billing?['active'] == false))
            _paywallCard()
          else ...[
            if (_textOnly) _textOnlyBanner(),
            _composer(),
          ],
        ],
      ),
    );
  }

  Widget _log() {
    if (_loadError != null) return LoadFailed(_loadError!, onRetry: _retryOpen);
    if (_conversationId == null) return const Loading();
    if (_messages.isEmpty && _pendingUser == null) return _empty();
    final children = <Widget>[];
    String? lastDay;
    for (final m in _messages) {
      final day = _day(m['createdAt'] as String?);
      if (day != null && day != lastDay) {
        children.add(_daySeparator(day));
        lastDay = day;
      }
      children.add(_bubble(m));
    }
    if (_pendingUser != null) {
      children
        ..add(_bubble({'role': 'user', 'content': _pendingUser}))
        ..add(_typing());
    }
    return ListView(
      controller: _scroll,
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
      children: [PageBody(maxWidth: 720, child: Column(children: children))],
    );
  }

  static const _weekdays = ['월', '화', '수', '목', '금', '토', '일'];

  static String? _day(String? iso) {
    final t = parseServerTime(iso);
    if (t == null) return null;
    return '${t.year}년 ${t.month}월 ${t.day}일 ${_weekdays[t.weekday - 1]}요일';
  }

  Widget _daySeparator(String day) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 14),
      child: Center(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          decoration: BoxDecoration(color: AppColors.sunk, borderRadius: BorderRadius.circular(999)),
          child: Text(day, style: AppText.small.copyWith(fontSize: 12)),
        ),
      ),
    );
  }

  Widget _empty() {
    final starters = ['$_call, 저 왔어요', '보고 싶었어요', '오늘 하루 어떻게 보내셨어요?'];
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(28),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 400),
          child: Column(
            children: [
              Avatar(_title, size: 84),
              const SizedBox(height: 20),
              Text('$_title께\n먼저 말을 걸어보세요', textAlign: TextAlign.center, style: AppText.title),
              const SizedBox(height: 10),
              Text(
                _member
                    ? '$_ownerName님이 녹음과 이야기로 만든 대화 상대예요. 실제 그분은 아니지만, 그분이 하셨을 법한 말로 대답해요.'
                    : _imported
                    ? '예전에 모아 둔 녹음과 이야기로 만든 대화 상대예요. 실제 그분은 아니지만, 그분이 하셨을 법한 말로 대답해요.'
                    : '올려 주신 녹음과 적어 주신 이야기로 만든 대화 상대예요. 실제 그분은 아니지만, 그분이 하셨을 법한 말로 대답해요.',
                textAlign: TextAlign.center,
                style: AppText.small,
              ),
              if (_member && !_tipSeen) ...[const SizedBox(height: 18), _memberTip()],
              const SizedBox(height: 24),
              Wrap(
                alignment: WrapAlignment.center,
                spacing: 8,
                runSpacing: 8,
                children: [for (final s in starters) Pill(label: s, onTap: () => _sendText(s))],
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _bubble(Map<String, dynamic> m) {
    final mine = m['role'] == 'user';
    final id = m['id'] as String?;
    final bubble = Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 11),
      decoration: BoxDecoration(
        color: mine ? AppColors.primary : AppColors.card,
        border: mine ? null : Border.all(color: AppColors.line),
        borderRadius: BorderRadius.only(
          topLeft: const Radius.circular(20),
          topRight: const Radius.circular(20),
          bottomLeft: Radius.circular(mine ? 20 : 6),
          bottomRight: Radius.circular(mine ? 6 : 20),
        ),
      ),
      child: Text(
        keepAll(m['content'] ?? ''),
        style: TextStyle(color: mine ? Colors.white : AppColors.ink, fontSize: 15.5, height: 1.55),
      ),
    );
    if (mine) {
      return Align(
        alignment: Alignment.centerRight,
        child: Padding(padding: const EdgeInsets.only(bottom: 12, left: 56), child: bubble),
      );
    }
    final memoryCount = (m['retrievedMemoryIds'] as List?)?.length ?? 0;
    final speaking = _speakingId == id;
    // 평가는 그분 대답마다 작은 ⋯ 하나(말풍선 글을 가리지 않게 아래 줄에). 떠올린 이야기 수는 마지막 대답(또는
    // 평가한 것)에만 — 모든 말풍선에 붙으면 어수선하다.
    final latest = id != null && _messages.isNotEmpty && _messages.last['id'] == id;
    final rated = id == null ? null : _rated[id];
    final showRate = id != null && (latest || rated != null);
    return Align(
      alignment: Alignment.centerLeft,
      child: Padding(
        padding: const EdgeInsets.only(bottom: 14, right: 40),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Avatar(_title, size: 34),
            const SizedBox(width: 8),
            Flexible(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  bubble,
                  if (id != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Wrap(
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          if (_canSpeak && m['textOnly'] != true)
                            TextButton.icon(
                              onPressed: () => _speak(id),
                              style: TextButton.styleFrom(
                                foregroundColor: speaking ? AppColors.primary : AppColors.ink2,
                                visualDensity: VisualDensity.compact,
                                padding: const EdgeInsets.symmetric(horizontal: 8),
                                textStyle: const TextStyle(
                                    fontSize: 13, fontWeight: FontWeight.w700, fontFamilyFallback: koreanFallback),
                              ),
                              icon: _loadingSpeechId == id
                                  ? const SizedBox(
                                      width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2))
                                  : Icon(speaking ? Icons.pause_rounded : Icons.play_arrow_rounded, size: 18),
                              label: Text(speaking ? '멈추기' : '듣기'),
                            ),
                          _tool(
                            tooltip: '이 대답 어땠나요?',
                            onPressed: () => _rate(id),
                            icon: Icons.more_horiz_rounded,
                            active: rated != null,
                          ),
                          if (rated != null && rated.tags.isNotEmpty)
                            Padding(
                              padding: const EdgeInsets.only(right: 6),
                              child: Text(
                                _ratedText(rated),
                                style: AppText.small.copyWith(fontSize: 12, color: AppColors.primary),
                              ),
                            ),
                          if (memoryCount > 0 && showRate)
                            Padding(
                              padding: const EdgeInsets.only(left: 4),
                              child: Text('이야기 $memoryCount개를 떠올려 대답했어요', style: AppText.small.copyWith(fontSize: 12)),
                            ),
                        ],
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _tool({
    required String tooltip,
    required VoidCallback onPressed,
    required IconData icon,
    bool active = false,
  }) {
    return IconButton(
      tooltip: tooltip,
      visualDensity: VisualDensity.compact,
      iconSize: 16,
      color: active ? AppColors.primary : AppColors.muted,
      onPressed: onPressed,
      icon: Icon(icon),
    );
  }

  Widget _typing() {
    return Align(
      alignment: Alignment.centerLeft,
      child: Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Avatar(_title, size: 34),
            const SizedBox(width: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 15),
              decoration: BoxDecoration(
                color: AppColors.card,
                border: Border.all(color: AppColors.line),
                borderRadius: BorderRadius.circular(20),
              ),
              child: const _TypingDots(),
            ),
          ],
        ),
      ),
    );
  }

  /// 이 페르소나의 요금제 상태(결제·체험·대화 횟수는 페르소나마다).
  Map<String, dynamic>? get _billing => billingStore.persona(_id);

  /// 대화 횟수를 다 써서 지금은 글로만 대답한다(끊지 않는다).
  bool get _textOnly => _billing?['textOnly'] == true;

  /// 결제가 열리기 전(무료 체험에 한도가 없고, 구독 신청만 받는다).
  /// 예전 서버는 paymentsOpen 을 주지 않으므로 이 대화 상대의 상태(PRELAUNCH)도 함께 본다.
  /// 함께하는 가족은 내 계정(billing/me)이 아니라 만든 분의 요금제를 따르므로 이 대화 상대의 값만 본다.
  bool get _prelaunch {
    final status = _billing?['status'];
    if (_member) return _billing?['paymentsOpen'] == false || status == 'PRELAUNCH';
    return billingStore.prelaunch || status == 'PRELAUNCH';
  }

  /// 함께하는 가족이 지금 이야기할 수 없는 까닭(보낼 때 받은 말 → 요금제 보기의 paused.text →
  /// 만든 분 쪽에서 멈춘 대화 상대면 상세의 stopped. 까닭·지우는 날은 만든 분만 본다).
  String? get _pausedText {
    final paused = _billing?['paused'];
    final text = _paused ??
        (paused is Map ? paused['text'] as String? : null) ??
        (widget.persona['stopped'] == true ? '지금은 이 대화 상대와 이야기할 수 없어요.' : null);
    return text == null || text.trim().isEmpty ? null : text;
  }

  /// 결제 전 모드: 대화 상대를 다 만든 날. 대화 횟수는 이날부터 30일마다 다시 채워진다.
  DateTime? get _madeAt => parseServerTime(_billing?['madeAt'] as String?);
  DateTime? get _resetAt => parseServerTime(_billing?['conversationsResetAt'] as String?);

  /// 결제 전 모드의 남은 대화 안내(대화 상대를 만든 날부터 30일마다). 알 수 없으면 null.
  String? get _cycleText {
    if (_member) {
      // 남은 횟수는 가족이 함께 쓴다(만든 날은 보이지 않는다)
      if (!_prelaunch || _usage == null) return null;
      final reset = _resetAt;
      return '함께 쓰는 남은 대화 ${_usage!.$1}번${reset == null ? '' : ' · ${reset.month}월 ${reset.day}일에 다시 채워져요'}';
    }
    final made = _madeAt;
    if (!_prelaunch || made == null) return null;
    final since = '대화 상대를 만든 날(${made.month}월 ${made.day}일)부터 30일마다';
    final reset = _resetAt;
    final next = reset == null ? '' : ' · 다음은 ${reset.month}월 ${reset.day}일';
    if (_usage != null) return '남은 대화 ${_usage!.$1}번 / ${_usage!.$2}번 · $since 다시 채워져요$next';
    if (_billing?['status'] == 'ADMIN') return '$since 세요 · 관리자는 횟수 제한이 없어요';
    return null;
  }

  Widget _cycleLine() {
    return Container(
      width: double.infinity,
      color: AppColors.paper,
      padding: const EdgeInsets.fromLTRB(16, 6, 16, 4),
      child: PageBody(
        maxWidth: 720,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Padding(
              padding: EdgeInsets.only(top: 1),
              child: Icon(Icons.chat_bubble_outline_rounded, size: 14, color: AppColors.muted),
            ),
            const SizedBox(width: 6),
            Expanded(child: Text(keepAll(_cycleText!), style: AppText.small.copyWith(fontSize: 12.5))),
          ],
        ),
      ),
    );
  }

  Widget _textOnlyBanner() {
    final reset = parseServerTime(_billing?['conversationsResetAt'] as String?);
    final made = _madeAt;
    return Container(
      width: double.infinity,
      color: AppColors.paper,
      padding: const EdgeInsets.fromLTRB(16, 6, 16, 0),
      child: PageBody(
        maxWidth: 720,
        child: Row(
          children: [
            const Icon(Icons.chat_bubble_outline_rounded, size: 16, color: AppColors.muted),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                keepAll(_member
                    ? '가족이 함께 쓰는 대화를 다 써서 지금은 목소리 대신 글로 대답해요.'
                        '${reset == null ? '' : ' ${reset.month}월 ${reset.day}일부터 다시 목소리로 들려 드려요.'}'
                    : _billing?['status'] == 'PRELAUNCH'
                    ? '${made == null ? '대화 상대를 만든 날부터 30일마다' : '대화 상대를 만든 날(${made.month}월 ${made.day}일)부터 30일마다'} '
                        '채워지는 무료 대화를 다 써서 지금은 목소리 대신 글로 대답해요. '
                        '${reset == null ? '조금 지나면' : '${reset.month}월 ${reset.day}일부터'} 다시 목소리로 들려 드려요.'
                    : _billing?['status'] == 'TRIAL'
                    ? '무료 대화를 다 써서 지금은 목소리 대신 글로 대답해요. 요금제를 고르면 다시 목소리로 들려 드려요.'
                    : '이번 달 대화 횟수를 다 써서 지금은 목소리 대신 글로 대답해요.'
                        '${reset == null ? '' : ' ${reset.month}월 ${reset.day}일부터 다시 목소리로 들려 드려요.'}'),
                style: AppText.small,
              ),
            ),
            // 함께하는 가족은 요금제를 고르지 않는다
            if (!_member)
              TextButton(onPressed: () => context.go('/plans?persona=$_id'), child: const Text('요금제 보기')),
          ],
        ),
      ),
    );
  }

  /// 대화를 멈춘 까닭(서버가 402 로 알려 준 말이 없을 때).
  String get _paywallText {
    final view = _billing;
    final status = view?['status'];
    // 함께하는 가족: 서버가 준 까닭 그대로(요금제 이야기 없이)
    if (_member) return _pausedText ?? '지금은 이 대화 상대와 이야기할 수 없어요. 나눈 대화는 그대로 볼 수 있어요.';
    // 결제한 적이 있으면 '체험 없음'이 아니라 '요금제가 끝남'이다(먼저 본다)
    if (view?['active'] == false && view?['hadPaidPlan'] == true) {
      return '${josa(_title, '과', '와')} 대화하던 요금제가 끝났어요. 요금제를 다시 고르면 이어서 대화할 수 있어요.';
    }
    if (status == 'NO_TRIAL') {
      return '무료 체험은 계정 하나에 대화 상대 ${billingStore.me?['trialPersonasPerAccount'] ?? 1}명까지예요. '
          '${josa(_title, '과', '와')}는 요금제를 고르면 대화할 수 있어요. $noTrialRefundText';
    }
    if (view?['active'] == false) {
      return '${josa(_title, '과', '와')}의 무료 체험이 끝났어요. 요금제를 고르면 이어서 대화할 수 있어요.';
    }
    final limit = (view?['conversationsLimit'] as num?)?.toInt() ?? 0;
    if (status == 'TRIAL') return '무료 대화 $limit번을 다 썼어요. 요금제를 고르면 이어서 대화할 수 있어요.';
    final reset = parseServerTime(view?['conversationsResetAt'] as String?);
    return '이번 달 대화 $limit번을 다 썼어요.'
        '${reset == null ? '' : ' ${reset.month}월 ${reset.day}일에 다시 채워져요.'}';
  }

  /// 머리글 아래 한 줄: 호칭과 다를 때의 관계 · 남은 대화 횟수. 목소리를 만드는 중이면 그 진행.
  String get _subtitle {
    if (_voiceProgress != null) return _voiceProgress!;
    final relation = (widget.persona['relation'] ?? '').toString().trim();
    return [
      if (_member)
        '$_ownerName님과 함께'
      else if (relation.isNotEmpty && relation != _title)
        relation,
      // 횟수가 있으면 오른쪽 원형 막대로 보여 주고, 글은 횟수가 없을 때(제한 없음 등)만
      if (_usage == null && BillingStore.conversationsText(_billing) != null) BillingStore.conversationsText(_billing)!,
    ].join(' · ');
  }

  /// (남은 대화, 한 달 전체). 제한이 없거나 아직 모르면 null.
  (int, int)? get _usage {
    final b = _billing;
    final left = (b?['conversationsLeft'] as num?)?.toInt();
    final limit = (b?['conversationsLimit'] as num?)?.toInt();
    if (b == null || b['active'] != true || left == null || limit == null || limit <= 0) return null;
    return (left, limit);
  }

  Widget _paywallCard() {
    return Container(
      color: AppColors.paper,
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
      child: SafeArea(
        top: false,
        child: PageBody(
          maxWidth: 720,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [_paywallBox(), _aiCaption()],
          ),
        ),
      ),
    );
  }

  Widget _paywallBox() {
    return AppCard(
      padding: const EdgeInsets.fromLTRB(20, 16, 16, 16),
      color: AppColors.claySoft,
      borderColor: Colors.transparent,
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  _paywall ?? _paywallText,
                  style: const TextStyle(fontWeight: FontWeight.w700, height: 1.5),
                ),
                const SizedBox(height: 2),
                Text('${josa(_title, '과', '와')} 나눈 대화는 그대로 남아 있어요.', style: AppText.small),
              ],
            ),
          ),
          // 함께하는 가족은 요금제를 고르지 않는다(만든 분의 요금제로 이야기한다)
          if (!_member) ...[
            const SizedBox(width: 12),
            FilledButton(
              onPressed: () => context.go('/plans?persona=$_id'),
              style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
              child: const Text('요금제 보기'),
            ),
          ],
        ],
      ),
    );
  }

  /// 만든 분이 할 일(막지 않는 카드): 입력 칸 바로 위에 작게.
  Widget _ownerCards(List<Map<String, dynamic>> notes) {
    final consentStatus = subjectConsentOf(widget.persona)?['status'] as String?;
    return Container(
      color: AppColors.paper,
      padding: const EdgeInsets.fromLTRB(16, 6, 16, 0),
      child: PageBody(
        maxWidth: 720,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (final action in notes)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: OwnerActionCard(action: action, compact: true, consentStatus: consentStatus),
              ),
          ],
        ),
      ),
    );
  }

  /// 만든 분: 멈춘 대화 상대(지금 지우기) · 유족 확인 전(적으러 가기). 입력 칸 자리에 그린다.
  Widget _blockedCard(Map<String, dynamic> action) {
    return Container(
      color: AppColors.paper,
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
      child: SafeArea(
        top: false,
        child: PageBody(
          maxWidth: 720,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              OwnerActionCard(action: action, onDelete: () => deletePersona(context, widget.persona)),
              _aiCaption(),
            ],
          ),
        ),
      ),
    );
  }

  /// 함께하는 가족: 지금은 이야기할 수 없는 까닭을 입력 칸 자리에 차분히 보인다(단추 없이).
  Widget _pausedCard(String text) {
    return Container(
      color: AppColors.paper,
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
      child: SafeArea(
        top: false,
        child: PageBody(
          maxWidth: 720,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              AppCard(
                padding: const EdgeInsets.fromLTRB(18, 16, 18, 16),
                color: AppColors.primarySoft,
                borderColor: Colors.transparent,
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Padding(
                      padding: EdgeInsets.only(top: 2),
                      child: Icon(Icons.pause_circle_outline_rounded, size: 20, color: AppColors.primary),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        keepAll(text),
                        style: const TextStyle(height: 1.55, color: AppColors.primaryDeep, fontWeight: FontWeight.w600),
                      ),
                    ),
                  ],
                ),
              ),
              _aiCaption(),
            ],
          ),
        ),
      ),
    );
  }

  /// 함께하는 가족이 처음 열었을 때 한 번만 보이는 안내.
  Widget _memberTip() {
    return AppCard(
      padding: const EdgeInsets.fromLTRB(16, 14, 8, 8),
      color: AppColors.primarySoft,
      borderColor: Colors.transparent,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            keepAll('아래 칸에 하고 싶은 말을 쓰거나 마이크를 눌러 말해 보세요. '
                '${_hasVoice ? '대답은 목소리로도 들려 드려요. ' : ''}'
                '다음에는 받은 링크를 다시 누르면 바로 이어져요.'),
            style: const TextStyle(fontSize: 14, height: 1.55, color: AppColors.primaryDeep),
          ),
          Align(
            alignment: Alignment.centerRight,
            child: TextButton(onPressed: _dismissTip, child: const Text('알겠어요')),
          ),
        ],
      ),
    );
  }

  Future<void> _dismissTip() async {
    setState(() => _tipSeen = true);
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(_tipKey, true);
    } catch (_) {
      // 남기지 못하면 다음에 한 번 더 보일 뿐이다
    }
  }

  Widget _composer() {
    // 대화를 아직 읽지 못했으면 보낼 곳이 없다(누르면 아무 일도 없어 보이지 않게 끈다)
    final canSend = _hasText && !_sending && _conversationId != null;
    return Container(
      decoration: const BoxDecoration(color: AppColors.paper),
      padding: const EdgeInsets.fromLTRB(12, 6, 12, 8),
      child: SafeArea(
        top: false,
        child: PageBody(
          maxWidth: 720,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _composerBox(canSend),
              _aiCaption(),
            ],
          ),
        ),
      ),
    );
  }

  /// AI가 만든 대답이라는 표시. 입력 칸 아래에 늘 보이게 둔다(휴대폰 포함).
  static Widget _aiCaption() => Padding(
        padding: const EdgeInsets.only(top: 6),
        child: Text(
          keepAll('대답과 목소리는 AI가 만들어요 · 사실과 다를 수 있어요'),
          textAlign: TextAlign.center,
          style: AppText.small.copyWith(fontSize: 11.5, color: AppColors.muted),
        ),
      );

  Widget _composerBox(bool canSend) {
    return Container(
      padding: const EdgeInsets.fromLTRB(6, 6, 6, 6),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(28),
        border: Border.all(color: _recording ? AppColors.danger : AppColors.line2),
        boxShadow: const [BoxShadow(color: Color(0x0A000000), blurRadius: 12, offset: Offset(0, 2))],
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          IconButton(
            tooltip: _recording ? '다 말했어요' : '말로 하기',
            style: IconButton.styleFrom(
              backgroundColor: _recording ? AppColors.danger : Colors.transparent,
              foregroundColor: _recording ? Colors.white : AppColors.ink2,
              minimumSize: const Size(44, 44),
            ),
            // 횟수를 다 쓴 뒤에는 글로만 대화한다(말을 글로 옮기는 데도 돈이 든다)
            onPressed: _sending || _conversationId == null || (_textOnly && !_recording) ? null : _toggleMic,
            icon: Icon(_recording ? Icons.stop_rounded : Icons.mic_none_rounded),
          ),
          const SizedBox(width: 4),
          Expanded(
            child: _recording
                ? Padding(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    child: Row(
                      children: [
                        const RecordingDot(),
                        const SizedBox(width: 8),
                        Expanded(
                          child: ValueListenableBuilder<int>(
                            valueListenable: _capture.seconds,
                            builder: (context, seconds, _) {
                              final left = maxVoiceSeconds - seconds;
                              return Text(
                                left <= 30
                                    ? '듣고 있어요 · $left초 뒤에 저절로 보내요'
                                    : '듣고 있어요 · 다 말씀하시면 빨간 단추를 눌러 주세요',
                                style: const TextStyle(color: AppColors.ink2, fontSize: 14.5),
                              );
                            },
                          ),
                        ),
                        TextButton(
                          onPressed: _cancelRecording,
                          style: TextButton.styleFrom(visualDensity: VisualDensity.compact),
                          child: const Text('취소'),
                        ),
                      ],
                    ),
                  )
                : TextField(
                    controller: _input,
                    minLines: 1,
                    maxLines: 5,
                    maxLength: 2000,
                    textInputAction: TextInputAction.send,
                    onSubmitted: (_) => _sendText(),
                    decoration: InputDecoration(
                      hintText: '$_call께 하고 싶은 말',
                      counterText: '',
                      filled: false,
                      isDense: true,
                      contentPadding: const EdgeInsets.symmetric(horizontal: 4, vertical: 12),
                      border: InputBorder.none,
                      enabledBorder: InputBorder.none,
                      focusedBorder: InputBorder.none,
                    ),
                  ),
          ),
          if (!_recording) ...[
            const SizedBox(width: 4),
            IconButton(
              tooltip: '보내기',
              style: IconButton.styleFrom(
                backgroundColor: canSend ? AppColors.primary : AppColors.sunk,
                foregroundColor: canSend ? Colors.white : AppColors.muted,
                minimumSize: const Size(44, 44),
              ),
              onPressed: canSend ? () => _sendText() : null,
              icon: const Icon(Icons.arrow_upward_rounded),
            ),
          ],
        ],
      ),
    );
  }
}

/// 대답을 기다리는 동안 점 세 개가 차례로 떠오른다.
class _TypingDots extends StatefulWidget {
  const _TypingDots();

  @override
  State<_TypingDots> createState() => _TypingDotsState();
}

class _TypingDotsState extends State<_TypingDots> with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 1100))..repeat();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (var i = 0; i < 3; i++)
            Builder(builder: (context) {
              final t = ((_controller.value - i * 0.18) % 1.0);
              final lift = t < 0.4 ? Curves.easeInOut.transform(t < 0.2 ? t / 0.2 : (0.4 - t) / 0.2) : 0.0;
              return Container(
                margin: EdgeInsets.only(left: i == 0 ? 0 : 5),
                transform: Matrix4.translationValues(0, -4 * lift, 0),
                width: 7,
                height: 7,
                decoration: BoxDecoration(
                  color: Color.lerp(AppColors.line2, AppColors.ink2, lift),
                  shape: BoxShape.circle,
                ),
              );
            }),
        ],
      ),
    );
  }
}

/// 목소리를 (다시) 만들기 전 확인. 그분 상태에 맞는 확인 문서(pledgeDoc)를 보여 주고, 체크해야 만들기가 눌린다.
/// [attach]: 가져온 대화 상대에 준비해 둔 목소리를 붙일 때(새로 만들지 않는다). 확인 문서는 같다.
/// 돌아가신 분의 확인 문서는 '위에서 고르고 적은 것'(신청 자격과 유족 명단)을 가리키므로, 이 창에서는
/// 그 화면으로 가는 길을 함께 보인다(막지는 않는다 — 대표 답 2026-10-23 #3).
class _VoiceConsentDialog extends StatefulWidget {
  final String title;
  final String pledgeDoc;
  final bool remake;
  final bool attach;
  final bool bereavedDeclared;
  final VoidCallback? onBereaved;

  const _VoiceConsentDialog({
    required this.title,
    required this.pledgeDoc,
    required this.remake,
    this.attach = false,
    this.bereavedDeclared = false,
    this.onBereaved,
  });

  @override
  State<_VoiceConsentDialog> createState() => _VoiceConsentDialogState();
}

class _VoiceConsentDialogState extends State<_VoiceConsentDialog> {
  late Future<Map<String, dynamic>> _doc = LegalApi.doc(widget.pledgeDoc);
  bool _agreed = false;

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.attach
          ? '목소리를 붙일까요?'
          : (widget.remake ? '목소리를 다시 만들까요?' : '목소리를 만들까요?')),
      content: SizedBox(
        width: 480,
        child: FutureBuilder<Map<String, dynamic>>(
          future: _doc,
          builder: (context, snap) {
            final doc = snap.data;
            return SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    keepAll(widget.attach
                        ? '예전 녹음으로 준비해 둔 ${widget.title} 목소리를 붙여, 대답을 그 목소리로 들려 드려요. '
                            '붙이기 전에 아래 내용을 한 번 더 확인해 주세요.'
                        : '올려 주신 녹음으로 ${widget.title} 목소리를 새로 만들어, 대답을 그 목소리로 들려 드려요. '
                            '1분쯤 걸려요. 만들기 전에 아래 내용을 한 번 더 확인해 주세요.'),
                    style: AppText.lead,
                  ),
                  const SizedBox(height: 14),
                  if (widget.pledgeDoc == 'pledge_deceased') ...[
                    Notice(
                      widget.bereavedDeclared
                          ? "아래 '위에서 고르고 적은 것'은 이 대화 상대의 신청 자격과 유족 명단에 적어 주신 내용이에요."
                          : "아래 '위에서 고르고 적은 것'은 신청 자격과 유족 명단에 적는 내용이에요. 아직 적지 않으셨으면 먼저 적어 주세요.",
                    ),
                    if (widget.onBereaved != null)
                      Align(
                        alignment: Alignment.centerLeft,
                        child: TextButton(
                          onPressed: () {
                            Navigator.pop(context, false);
                            widget.onBereaved!();
                          },
                          child: Text(widget.bereavedDeclared ? '신청 자격과 유족 명단 보기' : '신청 자격과 유족 명단 적기'),
                        ),
                      ),
                    const SizedBox(height: 8),
                  ],
                  if (snap.hasError) ...[
                    const ErrorBox('확인 내용을 불러오지 못했어요.'),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton.icon(
                        onPressed: () => setState(() {
                          _doc = LegalApi.doc(widget.pledgeDoc);
                        }),
                        icon: const Icon(Icons.refresh_rounded, size: 18),
                        label: const Text('다시 불러오기'),
                      ),
                    ),
                  ] else if (doc == null)
                    const Padding(padding: EdgeInsets.all(20), child: Loading())
                  else
                    Container(
                      padding: const EdgeInsets.fromLTRB(16, 14, 16, 6),
                      decoration: BoxDecoration(color: AppColors.sunk, borderRadius: BorderRadius.circular(14)),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(doc['title'] as String? ?? '', style: AppText.label),
                          const SizedBox(height: 10),
                          LegalText(fillCallName(doc['body'] as String? ?? '', widget.title)),
                        ],
                      ),
                    ),
                  const SizedBox(height: 6),
                  ConsentCheck(
                    label: '위 내용을 모두 확인했어요',
                    value: _agreed,
                    enabled: doc != null,
                    onChanged: (v) => setState(() => _agreed = v),
                  ),
                ],
              ),
            );
          },
        ),
      ),
      actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('취소')),
        FilledButton(
          onPressed: _agreed ? () => Navigator.pop(context, true) : null,
          style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
          child: Text(widget.attach ? '붙이기' : '만들기'),
        ),
      ],
    );
  }
}

/// 대화 화면 위 큰 단추 하나.
class _FeatureTile extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback onTap;

  /// 좁은 화면(휴대폰): 아이콘을 줄이고 화살표를 빼서 제목이 잘리지 않게 한다.
  final bool compact;

  const _FeatureTile({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
    this.compact = false,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: Ink(
        decoration: BoxDecoration(
          color: AppColors.card,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppColors.line),
        ),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(16),
          child: Padding(
            padding: compact ? const EdgeInsets.fromLTRB(10, 10, 8, 10) : const EdgeInsets.fromLTRB(12, 11, 8, 11),
            child: Row(
              children: [
                Container(
                  width: compact ? 28 : 36,
                  height: compact ? 28 : 36,
                  decoration: const BoxDecoration(color: AppColors.primarySoft, shape: BoxShape.circle),
                  child: Icon(icon, size: compact ? 15 : 19, color: AppColors.primary),
                ),
                SizedBox(width: compact ? 8 : 10),
                Expanded(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(fontSize: compact ? 14 : 15, fontWeight: FontWeight.w700, color: AppColors.ink),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        keepAll(subtitle),
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 12.5, height: 1.35, color: AppColors.ink2),
                      ),
                    ],
                  ),
                ),
                if (!compact)
                  const Icon(Icons.chevron_right_rounded, size: 20, color: AppColors.muted),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
