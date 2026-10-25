import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:just_audio/just_audio.dart';

import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';

/// 관리자 화면(법률 자문 반영) 여러 장이 같이 쓰는 것: 시각·길이 글, 이름표, 한 줄, 듣기, 화면 틀.

/// 서버 시각(UTC ISO) → 이 기기 시각 '2026-10-23 14:05'. 값이 없으면 ''.
String adminDate(Object? value) {
  final text = '${value ?? ''}';
  if (text.isEmpty || text == 'null') return '';
  final iso = text.length > 10 && !text.endsWith('Z') && !text.contains('+') ? '${text}Z' : text;
  final at = DateTime.tryParse(iso)?.toLocal();
  if (at == null) return text;
  String two(int n) => n.toString().padLeft(2, '0');
  final day = '${at.year}-${two(at.month)}-${two(at.day)}';
  return text.length <= 10 ? day : '$day ${two(at.hour)}:${two(at.minute)}';
}

/// 밀리초 → '3:05'(한 시간이 넘으면 '1:02:05').
String adminClock(Object? ms) {
  final total = ((ms as num?) ?? 0).toInt() ~/ 1000;
  String two(int n) => n.toString().padLeft(2, '0');
  final h = total ~/ 3600;
  final m = (total % 3600) ~/ 60;
  final s = total % 60;
  return h > 0 ? '$h:${two(m)}:${two(s)}' : '$m:${two(s)}';
}

/// 바이트 → '2.4MB'.
String adminSize(Object? bytes) {
  final n = ((bytes as num?) ?? 0).toDouble();
  if (n < 1024 * 1024) return '${(n / 1024).toStringAsFixed(0)}KB';
  return '${(n / 1024 / 1024).toStringAsFixed(1)}MB';
}

/// 확인 단어(서버는 ['사과', '바다'] 목록, 예전 꼴은 '사과 바다') → '사과 · 바다'.
String adminWords(Object? words) {
  final parts = words is List ? [for (final w in words) '$w'] : '${words ?? ''}'.split(RegExp(r'\s+'));
  return parts.map((w) => w.trim()).where((w) => w.isNotEmpty).join(' · ');
}

/// 금액 → '14,900원'.
String adminWon(Object? amount) {
  final n = ((amount as num?) ?? 0).toInt();
  final digits = n.abs().toString();
  final out = StringBuffer(n < 0 ? '-' : '');
  for (var i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 == 0) out.write(',');
    out.write(digits[i]);
  }
  return '$out원';
}

/// 동의 녹음 한 번의 상태 → 이름.
const consentStatusLabels = {
  'issued': '확인 단어 받음',
  'submitted': '검수 대기',
  'passed': '통과',
  'rerequest': '재요청',
  'hold': '보류',
  'refused': '원하지 않음',
  'interrupted': '중간에 멈춤',
  'superseded': '새 녹음에 밀림',
  'expired': '기한 지남',
  'none': '아직 없음',
};

/// 동의 녹음 mode → 나이 구간.
const consentModeLabels = {
  'adult': '만 19세 이상',
  'minor_teen': '만 14~18세',
  'minor_child': '만 14세 미만',
};

/// 멈춘 까닭(persona.hold_reason) → 이름.
const holdReasonLabels = {
  'objection': '유족 이의',
  'prior_wish': '고인이 생전에 원하지 않으심',
  'other_parent': '다른 쪽 부모님 반대',
  'subject_withdrew': '그분이 그만두길 원하심',
  'subject_refused': '동의 녹음에서 원하지 않으심',
  'minor_consent_missing': '아이 본인 동의를 기한까지 못 받음',
};

/// 관리자가 접수하는 이의의 까닭(이 넷만).
const objectionReasons = ['objection', 'prior_wish', 'other_parent', 'subject_withdrew'];

/// 신청 자격(고인과의 관계).
const applicantRelationLabels = {
  'spouse': '배우자',
  'child': '자녀',
  'grandchild': '손주',
  'parent': '부모',
  'other': '그 밖의 친족·지인',
};

/// 유족 명단의 관계.
const bereavedRelationLabels = {
  'spouse': '배우자',
  'son': '아들',
  'daughter': '딸',
  'mother': '어머니',
  'father': '아버지',
};

/// 부모 양쪽 확인.
const guardianBasisLabels = {
  'both_agreed': '다른 쪽 부모님의 동의를 받았어요',
  'sole_custody': '혼자 친권을 가지고 있어요',
};

/// 법정대리인 확인에서 아직 안 한 것(서버 minor.missing).
const guardianMissingLabels = {
  'attest': '법정대리인 확인',
  'consent': '아이 정보 동의',
  'overseas': '해외 회사 동의',
  'sms': '확인 문자',
};

/// 결제 종류.
const paymentKindLabels = {'first': '첫 결제', 'renew': '갱신', 'upgrade': '요금제 올리기'};

/// 환불 까닭(서버 refund_note 코드).
const refundReasonLabels = {
  'no_chat_7d': '결제 후 7일 안 대화 없음',
  'no_trial_7d': '체험 안 쓴 대화 상대의 첫 결제 7일 안',
  'mismatch': '안내와 다르게 드림',
  'prorated': '남은 날 일할',
  'other': '그 밖',
};

/// 서버 refund_note('까닭' 또는 '까닭: 메모') → '결제 후 7일 안 대화 없음: 메모'.
String refundNoteText(Object? note) {
  final text = '${note ?? ''}';
  final cut = text.indexOf(':');
  final code = cut < 0 ? text : text.substring(0, cut);
  final label = refundReasonLabels[code.trim()];
  return label == null ? text : '$label${cut < 0 ? '' : text.substring(cut)}';
}

/// 처리를 멈춘 예전 대화 상대의 종류.
const unableKindLabels = {
  'unable': '대화가 어려우신 분(예전 상태)',
  'minor_not_guardian': '부모가 아닌 분이 만든 아이의 대화 상대',
  'status_changed': '살아 계신 분을 돌아가신 분으로 바꾼 대화 상대(유족 명단 없음)',
};

/// 생존 상태(basics.status).
const lifeStatusLabels = {'alive': '살아 계심', 'deceased': '돌아가심', 'unable': '대화가 어려우신 분(예전 상태)'};

/// 관리자 화면 틀: 위에 제목과 돌아가기, 가운데 넓지 않은 본문.
class AdminFrame extends StatelessWidget {
  final String title;
  final Widget body;
  final String back;
  final String backLabel;
  final List<Widget>? actions;

  const AdminFrame({
    super.key,
    required this.title,
    required this.body,
    this.back = '/admin',
    this.backLabel = '관리자 화면',
    this.actions,
  });

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(title),
        leading: IconButton(
          tooltip: backLabel,
          icon: const Icon(Icons.arrow_back_rounded),
          onPressed: () => context.go(back),
        ),
        actions: actions,
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 48),
        child: PageBody(maxWidth: 820, child: body),
      ),
    );
  }
}

/// 카드 하나: 굵은 제목 + 내용.
class AdminSection extends StatelessWidget {
  final String title;
  final List<Widget> children;
  final Widget? trailing;

  const AdminSection(this.title, {super.key, required this.children, this.trailing});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: AppCard(
        padding: const EdgeInsets.fromLTRB(18, 16, 18, 16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(child: Text(title, style: AppText.heading)),
                if (trailing != null) trailing!,
              ],
            ),
            const SizedBox(height: 8),
            ...children,
          ],
        ),
      ),
    );
  }
}

/// 이름표 한 줄. [copy] 면 오른쪽에 복사 단추(자동으로 보내는 것은 없다).
class AdminLine extends StatelessWidget {
  final String label;
  final String value;
  final bool copy;

  const AdminLine(this.label, this.value, {super.key, this.copy = false});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(width: 108, child: Text(label, style: AppText.small)),
          Expanded(child: SelectableText(value.isEmpty ? '—' : value, style: AppText.body)),
          if (copy && value.isNotEmpty) AdminCopyButton(value),
        ],
      ),
    );
  }
}

/// 복사 단추. 누르면 글을 복사하고 '복사했어요.'.
class AdminCopyButton extends StatelessWidget {
  final String text;
  final String label;

  const AdminCopyButton(this.text, {super.key, this.label = '복사'});

  @override
  Widget build(BuildContext context) {
    return TextButton.icon(
      onPressed: () async {
        await Clipboard.setData(ClipboardData(text: text));
        if (context.mounted) showMessage(context, '복사했어요.');
      },
      icon: const Icon(Icons.copy_rounded, size: 16),
      label: Text(label),
    );
  }
}

/// 서버가 준 서명 주소(15분)로 듣기만 한다. 내려받기 단추는 두지 않는다.
/// 한 화면에 하나를 두고, 단추들이 같이 쓴다(한 번에 하나만 재생). 재생기는 처음 누를 때 만든다.
class AdminAudio extends ChangeNotifier {
  AudioPlayer? _player;
  StreamSubscription<PlayerState>? _sub;
  String? _url;
  bool _playing = false;

  bool isPlaying(String url) => _playing && _url == url;

  AudioPlayer _ensure() {
    final player = _player ??= AudioPlayer();
    _sub ??= player.playerStateStream.listen((state) {
      final stopped = !state.playing || state.processingState == ProcessingState.completed;
      if (stopped && _playing) {
        _playing = false;
        notifyListeners();
      }
    });
    return player;
  }

  /// 듣기·멈추기. [at] 이 있으면 그 자리부터.
  Future<void> toggle(String url, {Duration? at}) async {
    final player = _ensure();
    if (_url == url && _playing && at == null) {
      await player.pause();
      _playing = false;
      notifyListeners();
      return;
    }
    if (_url != url) {
      await player.setUrl(url);
      _url = url;
    }
    if (at != null) await player.seek(at);
    _playing = true;
    notifyListeners();
    unawaited(player.play());
  }

  Future<void> stop() async {
    await _player?.pause();
    _playing = false;
    notifyListeners();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _player?.dispose();
    super.dispose();
  }
}

/// 듣기 단추 하나.
class AdminAudioButton extends StatelessWidget {
  final AdminAudio audio;
  final String? url;
  final String label;

  const AdminAudioButton({super.key, required this.audio, required this.url, this.label = '듣기'});

  @override
  Widget build(BuildContext context) {
    final url = this.url;
    return ListenableBuilder(
      listenable: audio,
      builder: (context, _) {
        final playing = url != null && audio.isPlaying(url);
        return OutlinedButton.icon(
          onPressed: url == null || url.isEmpty
              ? null
              : () async {
                  try {
                    await audio.toggle(url);
                  } catch (_) {
                    if (context.mounted) showMessage(context, '재생하지 못했어요. 주소가 지났으면 새로고침해 주세요.');
                  }
                },
          icon: Icon(playing ? Icons.pause_rounded : Icons.play_arrow_rounded, size: 18),
          label: Text(playing ? '멈추기' : label),
        );
      },
    );
  }
}

/// 링크 카드(관리자 첫 화면). [count] 가 있으면 제목 뒤에 붙인다.
class AdminLinkCard extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final String route;
  final Future<int?>? count;
  final String unit;

  const AdminLinkCard({
    super.key,
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.route,
    this.count,
    this.unit = '',
  });

  @override
  Widget build(BuildContext context) {
    return AppCard(
      padding: const EdgeInsets.fromLTRB(16, 14, 12, 14),
      onTap: () => context.go(route),
      child: Row(
        children: [
          Icon(icon, color: AppColors.ink),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                FutureBuilder<int?>(
                  future: count,
                  builder: (context, snap) {
                    final n = snap.data;
                    return Text(n == null ? title : '$title $n$unit', style: AppText.label);
                  },
                ),
                const SizedBox(height: 2),
                Text(subtitle, style: AppText.small),
              ],
            ),
          ),
          const Icon(Icons.chevron_right_rounded, color: AppColors.muted),
        ],
      ),
    );
  }
}

/// 목록이 비었거나 못 읽었을 때·읽는 중을 한 자리에.
Widget adminListState({
  required Object? error,
  required bool loading,
  required bool empty,
  required String emptyText,
  required VoidCallback onRetry,
}) {
  if (error != null) return LoadFailed(error, onRetry: onRetry);
  if (loading) return const Padding(padding: EdgeInsets.all(40), child: Loading());
  if (empty) return Notice(emptyText);
  return const SizedBox.shrink();
}
