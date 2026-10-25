import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_client.dart';
import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/page_events.dart';
import '../../shared/widgets/ui.dart';
import '../../shared/widgets/voice_input.dart';
import '../legal/consent_checks.dart';
import '../legal/legal_api.dart';
import 'data/persona_api.dart';
import 'data/persona_rules_api.dart';
import 'data/persona_store.dart';
import 'imported.dart';
import 'owner_actions.dart';
import 'persona_flow_page.dart' show deletePersona;
import 'widgets/basics_form.dart';

/// 예전에 만든 만 14세 미만 아이의 대화 상대(확인 문자를 아직 보낼 수 없을 때). 답은 받고 대화는 그대로 둔다.
const under14ExistingText = '만 14세 미만 아이는 지금은 확인 문자를 보낼 수 없어 동의 녹음을 아직 시작할 수 없어요. '
    '알려 주시면 아이에게 맞는 안전 규칙을 바로 켜고, 대화는 그대로 하실 수 있어요.';

/// 그분 동의 녹음(/p/:id/consent, 만든 분만). 살아 계신 분(나 자신 아님)의 대화 상대는 그분이 직접 듣고
/// 답하시는 모습을 앱에서 한 번에 녹음해야 그분 녹음을 글로 옮기고 목소리를 만든다(2026-10-22 법률 자문).
///
/// - 녹음은 멈추거나 고치거나 다시 들을 수 없다. 끝내면 바로 올린다(받은 그대로, 서울 서버에만).
/// - 녹음이 시작되면 서버가 준 오늘의 확인 단어 두 개가 크게 보이고, 대본(서버 문서)을 한 단계씩 넘긴다.
///   [다음]을 눌러도 녹음은 이어진다. 단계가 바뀐 때를 함께 보낸다(검수 보조).
/// - 다른 앱으로 넘어가 화면이 가려지면 그 녹음은 끊긴 것으로 올리고 처음부터 다시 한다.
/// - 이 녹음은 어떤 해외 회사로도 보내지 않는다(받아쓰기도 하지 않는다). 화면은 그 주소만 부른다.
class ConsentRecordingPage extends StatefulWidget {
  final String personaId;

  const ConsentRecordingPage({super.key, required this.personaId});

  @override
  State<ConsentRecordingPage> createState() => _ConsentRecordingPageState();
}

/// 녹음 화면이 지금 보이는 것.
enum _Phase { status, recording, sending, sent, sendFailed }

class _ConsentRecordingPageState extends State<ConsentRecordingPage> {
  Map<String, dynamic>? _detail;
  Map<String, dynamic>? _status;
  Map<String, dynamic>? _meta;
  Object? _loadError;
  Future<Map<String, dynamic>>? _doc;
  _Phase _phase = _Phase.status;
  bool _busy = false;

  /// 녹음이 중간에 멈춰 처음부터 다시 해야 할 때(시작 화면 위에 보인다).
  String? _stoppedText;

  // ── 녹음 한 번 ──
  Map<String, dynamic>? _session;
  ConsentScript? _script;
  VoiceCapture? _capture;
  final _clock = Stopwatch();
  final List<Map<String, int>> _marks = [];
  int _index = 0;

  /// 끝내기·끊김을 한 번만 처리한다(단추와 화면 가려짐이 겹쳐도).
  bool _ending = false;
  void Function()? _stopListening;
  void Function()? _releaseScreen;

  /// 올리지 못한 녹음(탭을 닫으면 사라진다). [다시 보내기]가 같은 파일을 그대로 다시 보낸다.
  ({VoiceClip clip, bool refused, bool interrupted, String reason})? _pending;
  double? _progress;
  Object? _sendError;

  // ── 만 19세 미만인지(예전에 만든 대화 상대가 처음 답할 때) ──
  bool? _minor;
  String? _birthYm;
  String? _basis;
  bool _attest = false;

  /// 그분이 아들·딸인데 '나는 그분의'가 어머니·아버지가 아닐 때 고르는 답(어머니 · 아버지 · 그 밖).
  /// 서버는 부모가 아니라고 답하면 법정대리인 확인 없이 받아 두므로 부모를 거기 넣지 않게 먼저 묻는다.
  String? _myRelation;

  String get _id => widget.personaId;
  String get _title => _detail == null ? '그분' : personaTitle(_detail!);
  Map<String, dynamic> get _basics => Map<String, dynamic>.from(_detail?['basics'] as Map? ?? const {});

  /// 만 19세 미만 대본(아이 눈높이)인가.
  bool get _child => '${_session?['mode'] ?? _status?['mode'] ?? ''}'.startsWith('minor');

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _stopListening?.call();
    _releaseScreen?.call();
    // 녹음하다 화면을 떠났다(다른 주소로 이동): 그 녹음은 쓸 수 없다고 남긴다
    final session = _session;
    if (_phase == _Phase.recording && session != null && !_ending) {
      unawaited(SubjectConsentApi.abandon(_id, '${session['id']}', 'user').catchError((Object _) {}));
    }
    _capture?.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([
        PersonaApi.detail(_id),
        SubjectConsentApi.status(_id),
        PersonaApi.meta().catchError((Object _) => <String, dynamic>{}),
      ]);
      if (!mounted) return;
      final status = results[1];
      setState(() {
        _detail = results[0];
        _status = status;
        _meta = results[2];
        _loadError = null;
        final doc = status['scriptDoc'] as String?;
        if (doc != null && doc.isNotEmpty) _doc = LegalApi.doc(doc);
      });
    } catch (error) {
      if (mounted) setState(() => _loadError = error);
    }
  }

  void _back() => context.canPop() ? context.pop() : context.go('/p/$_id');

  // ────────────────────────── 녹음 ──────────────────────────

  Future<void> _start() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _stoppedText = null;
    });
    // 마이크부터 켠다: 녹음할 수 없는 브라우저(카카오톡 안·마이크 허락 안 함)에서 확인 단어를 먼저 받으면 그 시도가
    // 끊긴 녹음으로 남아 횟수만 쓴다. 단어가 뜨기 전 1~2초 조용한 소리가 앞에 붙는다.
    // 15분(서버가 정한 길이)에 닿으면 끝내고 보낸다. 녹음하다 마이크가 끊기면 끊긴 녹음으로 남기고 처음부터
    final capture = VoiceCapture(maxSeconds: 900, onLimit: () => _finish(), onLost: () => _interrupt('mic'));
    final failure = await capture.start();
    if (!mounted) {
      capture.dispose();
      return;
    }
    if (failure != null) {
      capture.dispose();
      setState(() => _busy = false);
      return _cannotRecord(failure);
    }
    // 단계 시각은 녹음 파일 처음부터 센다(검수 화면이 그 자리로 넘겨 듣는다)
    _clock
      ..reset()
      ..start();
    Map<String, dynamic> session;
    try {
      session = await SubjectConsentApi.start(_id);
    } catch (error) {
      capture.dispose();
      _clock.stop();
      if (!mounted) return;
      setState(() => _busy = false);
      showMessage(context, error);
      // 그사이 상태가 바뀌었다(이미 확인됨·확인 중 등)
      if (error is ApiException && error.statusCode == 409) await _load();
      return;
    }
    final rid = '${session['id']}';
    ConsentScript script;
    try {
      final docKey = '${session['scriptDoc'] ?? _status?['scriptDoc'] ?? ''}';
      final doc = await LegalApi.doc(docKey);
      script = ConsentScript.parse(
        '${doc['body'] ?? ''}',
        mode: '${session['mode'] ?? _status?['mode'] ?? 'adult'}',
        words: [for (final w in session['words'] as List? ?? const []) '$w'],
        callName: _title,
        guardian: guardianWord(_basics['myRelation'] as String?),
      );
      if (script.steps.isEmpty) throw const ApiException('대본을 불러오지 못했어요. 잠시 뒤 다시 해 주세요.');
    } catch (error) {
      capture.dispose();
      _clock.stop();
      unawaited(SubjectConsentApi.abandon(_id, rid, 'error').catchError((Object _) {}));
      if (!mounted) return;
      setState(() => _busy = false);
      showMessage(context, error is ApiException ? error : '대본을 불러오지 못했어요. 잠시 뒤 다시 해 주세요.');
      return;
    }
    if (!mounted) {
      capture.dispose();
      unawaited(SubjectConsentApi.abandon(_id, rid, 'user').catchError((Object _) {}));
      return;
    }
    capture.maxSeconds = (session['maxSeconds'] as num?)?.toInt() ?? capture.maxSeconds;
    _capture?.dispose();
    _marks
      ..clear()
      ..add({'step': script.steps.first.number, 'atMs': _clock.elapsedMilliseconds});
    setState(() {
      _session = session;
      _script = script;
      _capture = capture;
      _index = 0;
      _ending = false;
      _pending = null;
      _sendError = null;
      _phase = _Phase.recording;
      _busy = false;
    });
    // 다른 앱으로 넘어가거나 화면이 꺼지면 그 녹음은 끊긴 것이다(멈췄다 이어 녹음하지 못하게)
    _stopListening = PageEvents.listen((visible) {
      if (!visible) _interrupt('hidden');
    });
    final release = await PageEvents.keepScreenOn();
    if (mounted && _phase == _Phase.recording) {
      _releaseScreen = release;
    } else {
      release();
    }
  }

  Future<void> _cannotRecord(VoiceStartFailure failure) async {
    final asked = await askOpenOutside(
      context,
      failure,
      title: '카카오톡 안에서는 녹음이 안 될 수 있어요',
      body: '다른 브라우저(크롬·사파리 등)로 열어 녹음해 주세요.',
      stay: '닫기',
    );
    if (asked || !mounted) return;
    showMessage(context, switch (failure) {
      VoiceStartFailure.denied => '마이크를 쓸 수 없어요. 브라우저에서 마이크를 허용해 주세요.',
      VoiceStartFailure.busy => '마이크를 켜지 못했어요. 통화 중이거나 다른 앱이 마이크를 쓰고 있는지 확인해 주세요.',
      VoiceStartFailure.unsupported => '이 브라우저에서는 녹음할 수 없어요. 다른 브라우저(크롬·사파리 등)로 열어 주세요.',
    });
  }

  /// 다음 단계로(녹음은 이어진다). 마지막 단계면 끝내고 보낸다.
  void _next() {
    final script = _script;
    if (script == null || _ending) return;
    if (_index >= script.steps.length - 1) {
      _finish();
      return;
    }
    setState(() => _index++);
    _marks.add({'step': script.steps[_index].number, 'atMs': _clock.elapsedMilliseconds});
  }

  /// 녹음을 멈추고 소리를 받는다. 이 녹음의 듣기·화면 꺼짐 막기도 놓는다.
  Future<VoiceClip?> _stopCapture() async {
    _clock.stop();
    _stopListening?.call();
    _stopListening = null;
    _releaseScreen?.call();
    _releaseScreen = null;
    try {
      return await _capture?.stop();
    } catch (_) {
      return null;
    }
  }

  /// 끝까지 녹음했다: 멈추고 바로 올린다(다시 듣기·버리기 없이).
  Future<void> _finish() async {
    if (_ending || _phase != _Phase.recording) return;
    _ending = true;
    final clip = await _stopCapture();
    if (clip == null) {
      // 소리를 받지 못했다(마이크가 끊김): 그 녹음은 쓸 수 없다고 남기고 처음부터
      await SubjectConsentApi.abandon(_id, '${_session?['id']}', 'mic').catchError((Object _) {});
      if (mounted) _stopped('mic');
      return;
    }
    if (!mounted) return;
    await _send((clip: clip, refused: false, interrupted: false, reason: ''));
  }

  /// 중간에 끊겼다(화면 가려짐·녹음기 오류·그만두기): 지금까지의 조각을 기록으로 올린다. 못 올리면 abandon.
  Future<void> _interrupt(String reason) async {
    if (_ending || _phase != _Phase.recording) return;
    _ending = true;
    final clip = await _stopCapture();
    final rid = '${_session?['id']}';
    var sent = false;
    if (mounted) {
      setState(() {
        _phase = _Phase.sending;
        _progress = 0;
      });
    }
    if (clip != null) {
      sent = await _upload(clip, interrupted: true, refused: false, reason: reason, retries: 1);
    }
    if (!sent) await SubjectConsentApi.abandon(_id, rid, reason).catchError((Object _) {});
    if (mounted) _stopped(reason);
  }

  /// 녹음이 중간에 멈췄다: 시작 화면으로 돌아가 처음부터(확인 단어도 새로).
  void _stopped(String reason) {
    setState(() {
      _phase = _Phase.status;
      _session = null;
      _script = null;
      _stoppedText = '녹음이 중간에 멈췄어요. 처음부터 다시 녹음해 주세요(확인 단어도 새로 받아요).';
    });
    _load();
  }

  Future<void> _refuse() async {
    if (_ending) return;
    final ok = await confirmAction(
      context,
      _child ? '아이가 싫다고 했어요' : '그분이 원하지 않으세요',
      '그분이 원하지 않으시면 만들지 않아요. 지금까지 녹음한 것을 TalkTo로 보내고 멈출까요?',
      action: '보내고 멈추기',
    );
    if (!ok || !mounted || _ending || _phase != _Phase.recording) return;
    _ending = true;
    final clip = await _stopCapture();
    if (!mounted) return;
    if (clip == null) {
      await SubjectConsentApi.abandon(_id, '${_session?['id']}', 'user').catchError((Object _) {});
      if (mounted) _stopped('user');
      return;
    }
    // 원하지 않으심을 남긴 녹음이다(끊긴 녹음이 아니다 — 검수 화면이 끊김으로 보이지 않게)
    await _send((clip: clip, refused: true, interrupted: false, reason: 'refused'));
  }

  Future<void> _quit() async {
    if (_ending) return;
    final ok = await confirmAction(
      context,
      '녹음을 그만둘까요?',
      '그만두면 이번 녹음은 쓸 수 없고, 처음부터 다시 녹음해야 해요(확인 단어도 새로 받아요). '
          '지금까지 녹음한 것은 기록으로 TalkTo에 보내요.',
      action: '그만두기',
    );
    if (ok && mounted) await _interrupt('user');
  }

  /// 녹음 하나를 올린다. 연결이 끊기면 같은 파일을 [retries] 번까지 다시 보낸다(편집이 아니다).
  Future<bool> _upload(
    VoiceClip clip, {
    required bool interrupted,
    required bool refused,
    required String reason,
    int retries = 3,
  }) async {
    final rid = '${_session?['id']}';
    for (var attempt = 0;; attempt++) {
      try {
        await SubjectConsentApi.upload(
          _id,
          rid,
          bytes: clip.bytes,
          filename: clip.filename,
          steps: List.of(_marks),
          interrupted: interrupted,
          refused: refused,
          reason: reason,
          onProgress: (p) {
            if (mounted && _phase == _Phase.sending) setState(() => _progress = p);
          },
        );
        return true;
      } catch (error) {
        if (attempt < retries && ApiException.isTransient(error)) {
          await Future.delayed(Duration(seconds: attempt + 1));
          continue;
        }
        _sendError = error;
        return false;
      }
    }
  }

  /// 끝까지(또는 '원하지 않으세요'로) 녹음한 것을 올린다. 못 올리면 그대로 두고 [다시 보내기].
  Future<void> _send(({VoiceClip clip, bool refused, bool interrupted, String reason}) take) async {
    setState(() {
      _pending = take;
      _phase = _Phase.sending;
      _progress = 0;
      _sendError = null;
    });
    final ok = await _upload(take.clip, interrupted: take.interrupted, refused: take.refused, reason: take.reason);
    if (!mounted) return;
    final error = _sendError;
    // 확인 단어가 바뀌었다(60분이 지남): 이 녹음은 쓸 수 없다. 처음부터
    if (!ok && error is ApiException && error.statusCode == 409) {
      showMessage(context, error);
      setState(() {
        _pending = null;
        _phase = _Phase.status;
        _session = null;
      });
      await _load();
      return;
    }
    setState(() {
      if (ok) _pending = null;
      _phase = ok ? _Phase.sent : _Phase.sendFailed;
    });
    if (ok) {
      personaStore.load();
      notifyPersonaEdited(_id);
    }
  }

  // ────────────────────────── 만 19세 미만인지 ──────────────────────────

  /// 아이의 대화 상대를 부모가 만들었는가(그렇지 않으면 서버가 법정대리인 확인 없이 받아 둔다).
  bool get _parent {
    final allowed = minorRelationsOf(_meta ?? const {});
    return allowed.relation.contains(_basics['relation']) &&
        allowed.myRelation.contains(_myRelation ?? _basics['myRelation']);
  }

  /// 그분이 아들·딸인데 '나는 그분의'가 어머니·아버지가 아니다(비었거나 직접 적은 말) → 먼저 고르게 한다.
  bool get _askMyRelation {
    final allowed = minorRelationsOf(_meta ?? const {});
    return allowed.relation.contains(_basics['relation']) && !allowed.myRelation.contains(_basics['myRelation']);
  }

  Future<void> _saveMinor() async {
    if (_minor == null) {
      showMessage(context, '그분이 만 19세 미만인지 골라 주세요.');
      return;
    }
    if (_minor == true && _askMyRelation && _myRelation == null) {
      showMessage(context, '나는 그분의 무엇인지 골라 주세요.');
      return;
    }
    setState(() => _busy = true);
    try {
      await SubjectConsentApi.setMinor(_id, {
        'minor': _minor,
        if (_minor == true) 'birthYm': _birthYm,
        if (_minor == true && _myRelation != null) 'myRelation': _myRelation,
        if (_minor == true && _parent) 'guardianBasis': _basis,
        if (_minor == true && _parent) 'guardianAttest': _attest,
      });
      personaStore.load();
      notifyPersonaEdited(_id);
      await _load();
    } catch (error) {
      if (!mounted) return;
      // 서버가 먼저 '나는 그분의'를 고르라고 했다(400 minor_my_relation) — 고르는 칸은 이미 보인다
      showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  // ────────────────────────── 그리기 ──────────────────────────

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    final status = _status;
    if (detail == null || status == null) {
      return Scaffold(
        appBar: AppBar(automaticallyImplyLeading: !Breakpoints.isWide(context)),
        body: _loadError != null
            ? LoadFailed(_loadError!, onRetry: () {
                setState(() => _loadError = null);
                _load();
              })
            : const Loading(),
      );
    }
    // 함께하는 가족은 이 화면을 쓰지 않는다(만든 분만)
    final blocked = PersonaBlocked.of(detail, personaId: _id, imported: false);
    if (blocked != null) return blocked;
    return switch (_phase) {
      _Phase.recording => _recording(),
      _Phase.sending => _sending(),
      _Phase.sent => _sent(),
      _Phase.sendFailed => _sendFailed(),
      _Phase.status => _statusView(status),
    };
  }

  WizardScaffold _page({
    required String headline,
    String? lead,
    required List<Widget> children,
    Widget? primary,
    Widget? secondary,
    Widget? footer,
    bool back = true,
  }) =>
      WizardScaffold(
        title: '그분 동의 녹음',
        headline: headline,
        lead: lead,
        primary: primary,
        secondary: secondary ?? (back ? TextButton(onPressed: _back, child: const Text('돌아가기')) : null),
        footer: footer,
        children: children,
      );

  Widget _statusView(Map<String, dynamic> s) {
    if (s['required'] != true) {
      return _page(headline: '그분 동의 녹음', children: const [Notice('이 대화 상대는 그분 동의 녹음이 필요 없어요.')]);
    }
    final code = s['blockCode'] as String?;
    if (code == 'minor_unknown') return _minorQuestion();
    final guardian = s['guardian'];
    final missing = guardian is Map ? guardian['missing'] as List? ?? const [] : const [];
    if (code == 'guardian' || (guardian is Map && guardian['needed'] == true && missing.isNotEmpty)) {
      return _page(
        headline: '법정대리인 확인을 마쳐 주세요',
        lead: '아이의 대화 상대는 동의 녹음을 하기 전에 법정대리인(부모) 확인이 필요해요.',
        primary: FilledButton(
          onPressed: () => context.go('/p/$_id/guardian'),
          child: const Text('법정대리인 확인하기'),
        ),
        children: [Notice('${s['blockMessage'] ?? '먼저 법정대리인 확인을 마쳐 주세요.'}')],
      );
    }
    final latest = s['latest'] is Map ? s['latest'] as Map : const {};
    switch (s['status']) {
      case 'submitted':
        return _page(
          headline: '그분 동의 녹음을 확인하고 있어요',
          lead: '보통 1~2일 걸려요. 확인되면 알림으로 알려 드릴게요.',
          primary: FilledButton(onPressed: () => context.go('/p/$_id'), child: const Text('대화 상대로 돌아가기')),
          children: const [Notice('그동안 녹음은 미리 올려 두셔도 돼요.', icon: Icons.hourglass_top_rounded)],
        );
      case 'hold':
        return _page(
          headline: '확인할 것이 있어요',
          lead: 'TalkTo가 따로 연락드릴게요.',
          primary: FilledButton(onPressed: () => context.go('/p/$_id'), child: const Text('대화 상대로 돌아가기')),
          children: const [],
        );
      case 'passed':
        return _page(
          headline: '그분 동의 녹음이 확인됐어요',
          lead: '이제 올리신 녹음을 살펴보고 대화 상대를 만들 수 있어요.',
          primary: FilledButton(onPressed: () => context.go('/p/$_id'), child: const Text('이어서 만들기')),
          children: const [],
        );
      case 'refused':
        final stopped = ownerActionsOf(_detail).where((a) => a['kind'] == 'stopped').firstOrNull;
        return _page(
          headline: '이 대화 상대는 만들 수 없어요',
          children: [
            if (stopped != null)
              OwnerActionCard(action: stopped, onDelete: () => deletePersona(context, _detail!))
            else
              const Notice('그분이 동의 녹음에서 원하지 않으셔서 이 대화 상대는 만들 수 없어요.'),
          ],
        );
    }
    return _startView(s, latest);
  }

  /// 녹음 전 안내(화면 글은 SPEC §5.5 그대로).
  Widget _startView(Map<String, dynamic> s, Map latest) {
    final rerequest = s['status'] == 'rerequest';
    final attempts = (s['attempts'] as num?)?.toInt() ?? 0;
    final canStart = s['canStart'] != false;
    // 다시 부탁한 까닭: 서버 카드(검수한 그 녹음의 글)를 먼저. latest 는 맨 끝 시도라, 그 뒤 끊긴 시도면 글이 없다
    final card = rerequest ? ownerActionsOf(_detail).where((a) => a['kind'] == 'subject_consent').firstOrNull : null;
    final note = [
      '${card?['body'] ?? ''}'.trim(),
      '${latest['noteToOwner'] ?? ''}'.trim(),
    ].firstWhere((t) => t.isNotEmpty, orElse: () => '');
    const guide = [
      '조용한 곳에서, 되도록 마주 앉아 녹음해 주세요.',
      '중간에 멈추거나 고칠 수 없어요. 처음부터 끝까지 이어서 녹음해요(15분까지).',
      '녹음하는 동안 화면을 켜 두고 다른 앱으로 넘어가지 마세요. 멈추면 처음부터 다시 해요.',
      '그분 대신 답하거나 따라 말하게 하지 마세요.',
      '그분이 망설이시거나 싫다고 하시면 멈춰 주세요. 싫으시면 만들지 않아요.',
      '녹음은 TalkTo 서버(서울)에만 저장하고 해외 회사로 보내지 않아요. 글로 옮기지 않고 목소리를 만드는 데도 쓰지 않아요. '
          'TalkTo 대표가 듣고 확인해요(보통 1~2일).',
    ];
    return _page(
      headline: '그분 동의 녹음',
      lead: '$_title께서 직접 듣고 답하시는 모습을 앱에서 한 번에 녹음해요. 녹음이 시작되면 화면에 오늘의 확인 단어가 나와요.',
      primary: FilledButton.icon(
        onPressed: _busy || !canStart ? null : _start,
        icon: const Icon(Icons.mic_rounded, size: 20),
        label: Text(_busy ? '잠시만요…' : '녹음 시작하기'),
      ),
      children: [
        if (_stoppedText != null) ...[ErrorBox(_stoppedText!), const SizedBox(height: 14)],
        if (rerequest) ...[
          Notice(
            note.isEmpty ? '녹음 잘 받았어요. 번거로우시겠지만 한 번 더 부탁드려요. 그분 목소리를 다루는 일이라 이 부분은 꼭 확인이 필요해요.' : note,
            icon: Icons.replay_rounded,
          ),
          const SizedBox(height: 14),
        ],
        // 대본 첫 문단(무엇을 하는 녹음인지)
        if (_doc != null)
          FutureBuilder<Map<String, dynamic>>(
            future: _doc,
            builder: (context, snap) {
              final intro = snap.data == null ? '' : ConsentScript.introOf('${snap.data!['body'] ?? ''}');
              if (intro.isEmpty) return const SizedBox.shrink();
              return Padding(
                padding: const EdgeInsets.only(bottom: 14),
                child: Text(keepAll(intro), style: AppText.body),
              );
            },
          ),
        AppCard(
          padding: const EdgeInsets.fromLTRB(18, 16, 18, 10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (final line in guide)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Padding(
                        padding: EdgeInsets.only(top: 3),
                        child: Icon(Icons.check_rounded, size: 17, color: AppColors.primary),
                      ),
                      const SizedBox(width: 8),
                      Expanded(child: Text(keepAll(line), style: const TextStyle(fontSize: 14.5, height: 1.55))),
                    ],
                  ),
                ),
            ],
          ),
        ),
        if (!canStart && '${s['blockMessage'] ?? ''}'.trim().isNotEmpty) ...[
          const SizedBox(height: 14),
          ErrorBox('${s['blockMessage']}'),
        ],
        if (attempts > 0) ...[
          const SizedBox(height: 14),
          Text(
            keepAll('${attempts + 1}번째 녹음이에요. 앞선 녹음도 지우지 않고 함께 확인해요.'),
            style: AppText.small.copyWith(color: AppColors.ink2, fontWeight: FontWeight.w700),
          ),
        ],
      ],
    );
  }

  /// 예전에 만든 대화 상대: 녹음 전에 그분이 만 19세 미만인지 먼저 묻는다(한 번만).
  Widget _minorQuestion() {
    final meta = _meta ?? const <String, dynamic>{};
    final bases = guardianBasesOf(meta);
    final age = minorAge(_birthYm);
    final closed = _minor == true && age != null && age < 14 && meta['under14Open'] != true;
    return _page(
      headline: '먼저 알려 주세요',
      lead: '그분 동의 녹음은 나이에 맞는 대본으로 해요. 그분이 만 19세 미만인지 알려 주세요.',
      primary: FilledButton(
        onPressed: _busy ? null : _saveMinor,
        child: Text(_busy ? '잠시만요…' : '알려 드리고 이어서 하기'),
      ),
      children: [
        AppCard(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 22),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const FieldLabel('그분은 만 19세 미만인가요?'),
              ChoiceGroup(
                options: const ['아니요', '네'],
                value: _minor == null ? null : (_minor! ? '네' : '아니요'),
                onChanged: (v) => setState(() => _minor = v == '네'),
              ),
              if (_minor == true) ...[
                const SizedBox(height: 22),
                const FieldLabel('태어난 해와 달', hint: '나이를 세는 데만 써요.'),
                BirthPicker(value: _birthYm, onChanged: (v) => setState(() => _birthYm = v)),
                const SizedBox(height: 16),
                const Notice('아이의 대화 상대는 그 아이의 부모(법정대리인)만 만들 수 있어요.', icon: Icons.child_care_rounded),
                if (_askMyRelation) ...[
                  const SizedBox(height: 18),
                  const FieldLabel('나는 그분의', hint: '아이의 부모가 아니면 "그 밖"을 골라 주세요.'),
                  ChoiceGroup(
                    options: myRelationChoices,
                    value: _myRelation,
                    onChanged: (v) => setState(() => _myRelation = v),
                  ),
                ],
                // 이미 있는 대화 상대라 답은 받는다(안전 규칙·공유 범위가 켜진다). 만들기 화면의 '만들 수 없어요' 글은 쓰지 않는다
                if (closed) ...[const SizedBox(height: 10), const Notice(under14ExistingText)],
                if (_parent) ...[
                  const SizedBox(height: 10),
                  FutureBuilder<Map<String, dynamic>>(
                    future: LegalApi.guardianLabels().catchError((Object _) => <String, dynamic>{}),
                    builder: (context, snap) {
                      final label = '${snap.data?['attest'] ?? ''}'.trim();
                      return ConsentCheck(
                        label: label.isEmpty ? attestFallback : label,
                        value: _attest,
                        docKey: 'guardian_consent',
                        onChanged: (v) => setState(() => _attest = v),
                      );
                    },
                  ),
                  const SizedBox(height: 14),
                  ChoiceGroup(
                    options: bases.keys.toList(),
                    value: _basis,
                    label: (key) => bases[key] ?? key,
                    onChanged: (v) => setState(() => _basis = v),
                  ),
                ],
              ],
            ],
          ),
        ),
      ],
    );
  }

  /// 녹음하는 동안: 확인 단어 · 흐른 시간 · 대본 한 단계 · [다음]. 바닥 글·뒤로 가기로 화면을 떠나지 않게 한다.
  Widget _recording() {
    final script = _script!;
    final step = script.steps[_index];
    final last = _index >= script.steps.length - 1;
    final words = [for (final w in _session?['words'] as List? ?? const []) '$w'].join(' · ');
    final max = _capture?.maxSeconds ?? 900;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _quit();
      },
      child: _page(
        headline: '녹음하고 있어요',
        footer: const SizedBox.shrink(),
        secondary: TextButton(
          onPressed: _ending ? null : _quit,
          style: TextButton.styleFrom(foregroundColor: AppColors.muted),
          child: const Text('그만두기'),
        ),
        primary: FilledButton(
          onPressed: _ending ? null : _next,
          child: Text(last ? '녹음 끝내고 보내기' : '다음'),
        ),
        children: [
          AppCard(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 18),
            color: AppColors.primarySoft,
            borderColor: Colors.transparent,
            child: Column(
              children: [
                const Text('오늘의 확인 단어', style: AppText.label),
                const SizedBox(height: 6),
                Text(
                  words,
                  key: const ValueKey('consent-words'),
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w800, color: AppColors.primaryDeep),
                ),
                const SizedBox(height: 10),
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    const RecordingDot(),
                    const SizedBox(width: 8),
                    ValueListenableBuilder<int>(
                      valueListenable: _capture!.seconds,
                      builder: (context, seconds, _) => Text(
                        '${voiceClock(seconds)} / ${voiceClock(max)}',
                        style: const TextStyle(
                          fontWeight: FontWeight.w700,
                          color: AppColors.danger,
                          fontFeatures: [FontFeature.tabularFigures()],
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 16),
          AppCard(
            padding: const EdgeInsets.fromLTRB(20, 18, 20, 18),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('${_index + 1} / ${script.steps.length}', style: AppText.small.copyWith(color: AppColors.primary)),
                const SizedBox(height: 4),
                Text(keepAll(step.title), style: AppText.heading),
                const SizedBox(height: 12),
                for (final line in step.lines) _line(line),
              ],
            ),
          ),
          const SizedBox(height: 14),
          Align(
            alignment: Alignment.centerLeft,
            child: OutlinedButton.icon(
              onPressed: _ending ? null : _refuse,
              style: OutlinedButton.styleFrom(foregroundColor: AppColors.danger, minimumSize: const Size(0, 46)),
              icon: const Icon(Icons.front_hand_outlined, size: 19),
              label: Text(_child ? '아이가 싫다고 했어요' : '그분이 원하지 않으세요'),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            keepAll("[다음]을 눌러도 녹음은 이어져요. 다른 앱으로 넘어가면 녹음이 멈춰요."),
            style: AppText.small,
          ),
        ],
      ),
    );
  }

  /// 대본 한 줄: 소리 내어 읽을 글은 크게, 도움말은 작게.
  Widget _line(ScriptLine line) {
    return switch (line.kind) {
      ScriptLineKind.say => Container(
          width: double.infinity,
          margin: const EdgeInsets.only(bottom: 8),
          padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
          decoration: BoxDecoration(color: AppColors.paper, borderRadius: BorderRadius.circular(12)),
          child: Text(
            keepAll(line.text),
            style: const TextStyle(fontSize: 19, height: 1.6, fontWeight: FontWeight.w600, color: AppColors.ink),
          ),
        ),
      ScriptLineKind.help => Padding(
          padding: const EdgeInsets.only(top: 2, bottom: 8),
          child: Text(keepAll(line.text), style: AppText.small.copyWith(color: AppColors.ink2)),
        ),
      ScriptLineKind.text => Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: Text(keepAll(line.text), style: AppText.body),
        ),
    };
  }

  Widget _sending() => _page(
        headline: '녹음을 보내고 있어요',
        footer: const SizedBox.shrink(),
        back: false,
        children: [
          ProgressCard(
            title: '보내는 중이에요',
            percent: _progress == null ? null : (_progress! * 100).round(),
            note: '이 화면을 닫지 말아 주세요.',
          ),
        ],
      );

  Widget _sent() => _page(
        headline: '보냈어요',
        primary: FilledButton(onPressed: () => context.go('/p/$_id'), child: const Text('대화 상대로 돌아가기')),
        back: false,
        children: const [
          Notice(
            '보냈어요. TalkTo가 듣고 확인하면 알림으로 알려 드릴게요. 그동안 녹음은 미리 올려 두셔도 돼요.',
            icon: Icons.check_circle_outline_rounded,
            color: AppColors.primary,
            background: AppColors.primarySoft,
          ),
        ],
      );

  /// 올리지 못했다(연결). 녹음은 이 화면에 그대로 있어 같은 파일을 다시 보낼 수 있다(탭을 닫으면 사라진다).
  Widget _sendFailed() => _page(
        headline: '녹음을 보내지 못했어요',
        lead: '녹음은 그대로 있어요. 인터넷 연결을 확인하고 다시 보내 주세요. 이 화면을 닫으면 녹음이 사라져요.',
        back: false,
        primary: FilledButton.icon(
          onPressed: _pending == null ? null : () => _send(_pending!),
          icon: const Icon(Icons.refresh_rounded, size: 20),
          label: const Text('다시 보내기'),
        ),
        children: [if (_sendError != null) ErrorBox(_sendError.toString())],
      );
}

/// 대본 한 줄의 종류: 소리 내어 읽을 글(> ) · 도움말(- ) · 그 밖의 글.
enum ScriptLineKind { say, help, text }

typedef ScriptLine = ({ScriptLineKind kind, String text});

/// 대본 한 단계. [number] 는 대본에 적힌 번호(없으면 차례), 검수 화면의 단계 시각에 쓴다.
typedef ScriptStep = ({int number, String title, List<ScriptLine> lines});

/// 그분 동의 녹음 대본(서버 문서 subject_consent_script · subject_consent_child).
/// `## ` 줄마다 한 단계. `> ` 는 소리 내어 읽을 글, `- ` 는 도움말. 제목에 `[만 14세 미만]` 이 붙은 단계는
/// 만 14세 미만(minor_child)일 때만 보인다(그 표시는 지운다). 자리표시 {{확인 단어}}·{{호칭}}·{{보호자}} 는
/// 화면이 채운다(서버는 채우지 않는다).
class ConsentScript {
  final String intro;
  final List<ScriptStep> steps;

  const ConsentScript({required this.intro, required this.steps});

  static const _under14 = '[만 14세 미만]';

  /// 첫 단계 앞 문단(녹음 전 안내 화면에 보인다).
  static String introOf(String body) {
    final lines = <String>[];
    for (final raw in body.split('\n')) {
      final line = raw.trim();
      if (line.startsWith('## ')) break;
      if (line.isNotEmpty) lines.add(line);
    }
    return lines.join(' ');
  }

  static ConsentScript parse(
    String body, {
    required String mode,
    required List<String> words,
    required String callName,
    required String guardian,
  }) {
    String fill(String text) => text
        .replaceAll('{{확인 단어}}', words.join(' · '))
        .replaceAll('{{호칭}}', callName.trim().isEmpty ? '그분' : callName.trim())
        .replaceAll('{{보호자}}', guardian);
    final steps = <ScriptStep>[];
    String? title;
    var lines = <ScriptLine>[];
    var skip = false;
    void close() {
      final t = title;
      if (t != null && !skip) {
        final number = int.tryParse(RegExp(r'^(\d+)\.').firstMatch(t)?.group(1) ?? '') ?? steps.length + 1;
        final shown = t.replaceFirst(RegExp(r'^\d+\.\s*'), '');
        steps.add((number: number, title: shown, lines: lines));
      }
      lines = <ScriptLine>[];
    }

    for (final raw in body.split('\n')) {
      final line = raw.trim();
      if (line.startsWith('## ')) {
        close();
        final heading = line.substring(3).trim();
        skip = heading.contains(_under14) && mode != 'minor_child';
        title = heading.replaceAll(_under14, '').trim();
        continue;
      }
      if (title == null || line.isEmpty) continue;
      if (line.startsWith('> ')) {
        lines.add((kind: ScriptLineKind.say, text: fill(line.substring(2).trim())));
      } else if (line.startsWith('- ')) {
        lines.add((kind: ScriptLineKind.help, text: fill(line.substring(2).trim())));
      } else {
        lines.add((kind: ScriptLineKind.text, text: fill(line)));
      }
    }
    close();
    return ConsentScript(intro: introOf(body), steps: steps);
  }
}

/// 아이 대본의 {{보호자}}: 만든 분이 아이의 어머니면 '엄마', 아버지면 '아빠'.
/// 예전 대화 상대의 '나는 그분의'(그분이 아들·딸일 때). 그 밖이면 부모가 아니라고 답한 것이다.
const myRelationChoices = ['어머니', '아버지', '그 밖'];

String guardianWord(String? myRelation) => switch (myRelation) {
      '어머니' => '엄마',
      '아버지' => '아빠',
      _ => '보호자',
    };
