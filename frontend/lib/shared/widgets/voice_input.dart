import 'dart:async';

import 'package:cross_file/cross_file.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:record/record.dart';

import '../../core/network/api_client.dart';
import '../../core/platform/browser.dart';
import '../../core/theme.dart';
import 'ui.dart';

// 마이크로 말하기: 대화 화면의 말로 보내기와 답 칸의 말로 적기(질문 답·더 알려 주실 기억·대화 고쳐 주기)가
// 같은 녹음기·형식 고르기를 쓴다.

/// 녹음 형식과 서버에 보낼 파일 이름(확장자로 서버가 형식을 안다).
typedef VoiceFormat = ({AudioEncoder encoder, String filename});

/// 녹음 한 번: 소리와 서버에 보낼 파일 이름.
typedef VoiceClip = ({List<int> bytes, String filename});

/// 말로 보내기 녹음이 이만큼(초) 길어지면 저절로 멈추고 보낸다(잊고 켜 둔 녹음이 다 올라간 뒤 거절되지 않게).
const maxVoiceSeconds = 120;

/// 답 칸의 말로 적기는 3분까지(서버도 3분이 넘으면 받지 않는다). 3분이 되면 저절로 멈추고 글로 옮긴다.
const maxAnswerVoiceSeconds = 180;

/// 이 기기에서 녹음할 형식. 웹은 브라우저가 녹음할 수 있는 것부터 고른다:
/// webm(opus · 크롬·안드로이드) → m4a(aac · 아이폰 사파리 18.4 전·그 위의 카카오톡 안 브라우저) → wav(어디서나).
/// 앱(웹이 아님)은 m4a. [supported] 는 녹음기의 isEncoderSupported.
Future<VoiceFormat> pickVoiceFormat(
  Future<bool> Function(AudioEncoder encoder) supported, {
  bool web = kIsWeb,
}) async {
  if (!web) return (encoder: AudioEncoder.aacLc, filename: 'voice.m4a');
  const candidates = <VoiceFormat>[
    (encoder: AudioEncoder.opus, filename: 'voice.webm'),
    (encoder: AudioEncoder.aacLc, filename: 'voice.m4a'),
  ];
  for (final format in candidates) {
    try {
      if (await supported(format.encoder)) return format;
    } catch (_) {
      // 알아보지 못하면 다음 형식으로
    }
  }
  return (encoder: AudioEncoder.wav, filename: 'voice.wav');
}

/// 녹음 설정. 울림·잡음을 줄이고 소리 크기를 맞춘다(휴대폰 스피커로 나오던 대답이 녹음에
/// 섞여 받아쓰기에 들어가지 않게). 말소리만 담으면 되니 작게 담아 느린 회선에서도 빨리 올라가게 한다.
RecordConfig voiceRecordConfig(AudioEncoder encoder) => RecordConfig(
      encoder: encoder,
      numChannels: 1,
      bitRate: encoder == AudioEncoder.opus ? 32000 : 64000,
      sampleRate: encoder == AudioEncoder.wav ? 16000 : 44100,
      echoCancel: true,
      noiseSuppress: true,
      autoGain: true,
    );

/// 녹음기. 시험에서는 [VoiceRecorder.create] 를 가짜로 바꾼다(마이크 없이).
abstract class VoiceRecorder {
  /// 새 녹음기를 만든다. 기본은 기기 마이크([AudioRecorder]).
  @visibleForTesting
  static VoiceRecorder Function() create = _AudioVoiceRecorder.new;

  Future<bool> hasPermission();
  Future<bool> isEncoderSupported(AudioEncoder encoder);
  Future<void> start(RecordConfig config, {required String path});
  Future<bool> isRecording();

  /// 멈추고 녹음이 있는 곳(웹은 blob 주소, 앱은 파일 경로)을 준다. 받은 소리가 없으면 null.
  Future<String?> stop();

  /// [stop] 이 준 곳의 소리를 읽고, 읽었으면 그 자리를 푼다(브라우저 메모리를 돌려준다).
  Future<List<int>> read(String path);
  Future<void> cancel();
  Future<void> dispose();
}

class _AudioVoiceRecorder implements VoiceRecorder {
  final _recorder = AudioRecorder();

  @override
  Future<bool> hasPermission() => _recorder.hasPermission();

  @override
  Future<bool> isEncoderSupported(AudioEncoder encoder) => _recorder.isEncoderSupported(encoder);

  @override
  Future<void> start(RecordConfig config, {required String path}) => _recorder.start(config, path: path);

  @override
  Future<bool> isRecording() => _recorder.isRecording();

  @override
  Future<String?> stop() => _recorder.stop();

  @override
  Future<List<int>> read(String path) async {
    try {
      // 웹은 blob 주소, 앱은 파일 경로. XFile 이 둘 다 읽는다.
      return await XFile(path).readAsBytes();
    } finally {
      revokeBlobUrl(path);
    }
  }

  @override
  Future<void> cancel() => _recorder.cancel();

  @override
  Future<void> dispose() => _recorder.dispose();
}

/// 녹음을 시작하지 못한 까닭.
enum VoiceStartFailure {
  /// 마이크를 허락하지 않았다.
  denied,

  /// 마이크를 켜지 못했다(통화 중·다른 앱이 쓰는 중).
  busy,

  /// 이 브라우저는 녹음할 수 없다.
  unsupported,
}

/// 녹음 한 번을 다룬다: 허락 묻기 → 형식 고르기 → 녹음 → 시간 세기 → [maxSeconds] 에서 [onLimit] → 멈추고 소리 받기.
/// 화면(대화의 말로 보내기, [VoiceField])은 단추와 안내만 그린다.
class VoiceCapture {
  VoiceCapture({this.maxSeconds = maxVoiceSeconds, this.onLimit, this.onLost, VoiceRecorder? recorder})
      : _recorder = recorder ?? VoiceRecorder.create();

  /// 녹음하는 동안 바꿀 수 있다(그분 동의 녹음: 마이크를 먼저 켠 뒤 서버가 정한 길이로).
  int maxSeconds;

  /// 녹음이 [maxSeconds] 에 닿으면 1초마다 부른다. 부르는 쪽이 [stop] 하면 그친다
  /// (대답을 기다리는 중처럼 지금 멈출 수 없으면 다음 번에 다시 불린다).
  VoidCallback? onLimit;

  /// 녹음하는 동안 마이크가 끊기면(장치가 빠짐·다른 앱이 마이크를 가져감) 부른다. 5초마다 보고 두 번
  /// 잇달아 멈춰 있을 때만(잠깐 흔들린 것은 넘긴다). 없으면 보지 않는다(그분 동의 녹음만 쓴다).
  VoidCallback? onLost;

  final VoiceRecorder _recorder;

  /// 녹음한 시간(초).
  final seconds = ValueNotifier<int>(0);

  /// 이 브라우저에 맞는 형식(처음 녹음할 때 정한다). [pickVoiceFormat]
  VoiceFormat? _format;
  Timer? _timer;
  bool _recording = false;

  bool get recording => _recording;

  /// 녹음을 시작한다. 시작했으면 null, 못 했으면 그 까닭. [beforeStart] 는 마이크를 켜기 바로 전에 부른다
  /// (대화 화면: 들려주던 대답을 멈춘다 — 녹음하는 동안 나오면 그분 목소리가 받아쓰기에 섞인다).
  Future<VoiceStartFailure?> start({Future<void> Function()? beforeStart}) async {
    if (_recording) return null;
    try {
      if (!await _recorder.hasPermission()) return VoiceStartFailure.denied;
      // 이 브라우저가 녹음할 수 있는 형식(아이폰 사파리 18.4 전은 webm 을 녹음하지 못한다)
      final format = _format ??= await pickVoiceFormat(_recorder.isEncoderSupported);
      await beforeStart?.call();
      await _recorder.start(
        voiceRecordConfig(format.encoder),
        path: kIsWeb ? format.filename : '${DateTime.now().millisecondsSinceEpoch}.m4a',
      );
      // 마이크를 켜지 못하면(통화 중·다른 앱이 쓰는 중) 녹음기는 오류 없이 멈춘 채로 있다
      if (!await _recorder.isRecording()) return VoiceStartFailure.busy;
    } catch (_) {
      return VoiceStartFailure.unsupported;
    }
    _recording = true;
    _startTimer();
    return null;
  }

  void _startTimer() {
    _timer?.cancel();
    seconds.value = 0;
    var missed = 0;
    _timer = Timer.periodic(const Duration(seconds: 1), (timer) async {
      if (!_recording) {
        timer.cancel();
        return;
      }
      if (seconds.value < maxSeconds) seconds.value++;
      if (seconds.value >= maxSeconds) onLimit?.call();
      if (onLost == null || seconds.value % 5 != 0) return;
      final still = await _recorder.isRecording().catchError((Object _) => false);
      if (!_recording) return;
      missed = still ? 0 : missed + 1;
      if (missed >= 2) {
        missed = 0;
        onLost?.call();
      }
    });
  }

  /// 멈추고 녹음한 소리를 준다. 마이크가 소리를 받지 못했으면 null(통화 중이거나 다른 앱이 마이크를 쓰는 중).
  /// 소리가 비었으면 [ApiException], 읽지 못하면 그 오류를 던진다.
  Future<VoiceClip?> stop() async {
    _timer?.cancel(); // 먼저(기다리기 전에) 그친다: 그사이 [onLimit] 이 다시 불리지 않게
    _recording = false;
    String? path;
    try {
      path = await _recorder.stop();
    } catch (_) {
      path = null;
    }
    if (path == null) return null;
    final bytes = await _recorder.read(path);
    if (bytes.isEmpty) throw const ApiException('녹음된 소리가 없어요. 다시 말씀해 주세요.');
    return (bytes: bytes, filename: _format?.filename ?? (kIsWeb ? 'voice.webm' : 'voice.m4a'));
  }

  /// 녹음을 버린다.
  Future<void> cancel() async {
    _timer?.cancel();
    _recording = false;
    try {
      await _recorder.cancel();
    } catch (_) {
      // 이미 멈췄다
    }
  }

  void dispose() {
    _timer?.cancel();
    _recording = false;
    seconds.dispose();
    unawaited(_recorder.dispose().catchError((Object _) {}));
  }
}

/// 말로 적기 설정. 시험에서는 카카오톡 안 브라우저인지를 바꿔 본다.
class VoiceInput {
  VoiceInput._();

  /// 카카오톡 앱 안의 브라우저인가([isKakaoInApp]).
  @visibleForTesting
  static bool Function() kakaoInApp = isKakaoInApp;

  /// 다른 브라우저로 연다([openExternal]). 시험에서는 연 주소를 받아 둔다.
  @visibleForTesting
  static void Function(String url) openOutside = openExternal;
}

/// 0:12 처럼 분:초로.
String voiceClock(int seconds) => '${seconds ~/ 60}:${(seconds % 60).toString().padLeft(2, '0')}';

/// 카카오톡 안 브라우저에서 녹음을 시작하지 못했을 때: 다른 브라우저(크롬·사파리)로 열지 묻고, 고르면
/// 지금 주소를 그대로 바깥에서 연다. 물었으면 true(카카오톡 밖이거나 마이크가 바쁜 것이면 묻지 않고 false).
/// 말로 적기와 그분 동의 녹음이 같이 쓴다.
Future<bool> askOpenOutside(
  BuildContext context,
  VoiceStartFailure failure, {
  required String title,
  required String body,
  required String stay,
}) async {
  if (failure == VoiceStartFailure.busy || !VoiceInput.kakaoInApp()) return false;
  final open = await showDialog<bool>(
    context: context,
    builder: (dialog) => AlertDialog(
      title: Text(title),
      content: Text(keepAll(body), style: AppText.lead),
      actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      actions: [
        TextButton(onPressed: () => Navigator.pop(dialog, false), child: Text(stay)),
        FilledButton.icon(
          onPressed: () => Navigator.pop(dialog, true),
          style: FilledButton.styleFrom(minimumSize: const Size(0, 48)),
          icon: const Icon(Icons.open_in_browser_rounded, size: 20),
          label: const Text('다른 브라우저로 열기'),
        ),
      ],
    ),
  );
  if (open == true) VoiceInput.openOutside(currentFullUrl());
  return true;
}

/// 글 칸 옆의 큰 🎤 단추. 누르면 녹음하고, 다시 누르거나 [maxSeconds](3분)가 되면 멈춰 받아쓴 글을
/// 칸([controller])에 넣는다. 넣은 글은 고친 뒤 저장한다(저장은 부르는 쪽이 한다, [onChanged]).
/// [transcribe] 가 없으면 칸만 그린다.
class VoiceField extends StatefulWidget {
  final TextEditingController controller;

  /// [controller] 를 쓰는 글 칸.
  final Widget child;

  /// 소리를 글로 옮긴다(서버). 예: `(clip) => PersonaApi.transcribeAnswer(id, clip)`.
  final Future<String> Function(VoiceClip clip)? transcribe;

  /// 받아쓴 글을 칸에 넣은 뒤 부른다(칸의 새 글).
  final ValueChanged<String>? onChanged;

  /// false 면 단추를 누를 수 없다('기억이 안 나요'를 골라 칸이 잠긴 때 등).
  final bool enabled;

  /// 칸이 받는 글자 수. 넘으면 앞부분만 넣는다.
  final int? maxLength;
  final int maxSeconds;

  const VoiceField({
    super.key,
    required this.controller,
    required this.child,
    this.transcribe,
    this.onChanged,
    this.enabled = true,
    this.maxLength,
    this.maxSeconds = maxAnswerVoiceSeconds,
  });

  @override
  State<VoiceField> createState() => _VoiceFieldState();
}

class _VoiceFieldState extends State<VoiceField> {
  /// 지금 녹음하는 칸(한 번에 하나만). 다른 칸을 누르면 앞 칸 녹음을 마치고 글로 옮긴다.
  static _VoiceFieldState? _active;

  VoiceCapture? _capture;
  bool _starting = false;
  bool _working = false;

  bool get _recording => _capture?.recording == true;

  @override
  void dispose() {
    if (_active == this) _active = null;
    _capture?.dispose();
    super.dispose();
  }

  Future<void> _toggle() async {
    if (_working || _starting) return;
    if (_recording) return _finish();
    final other = _active;
    if (other != null && other != this && other.mounted) await other._finish();
    if (!mounted) return;
    setState(() => _starting = true);
    final capture = _capture ??= VoiceCapture(maxSeconds: widget.maxSeconds, onLimit: _finish);
    final failure = await capture.start();
    if (!mounted) return;
    setState(() => _starting = false);
    if (failure != null) return _cannotRecord(failure);
    _active = this;
    setState(() {});
  }

  /// 녹음을 멈추고 글로 옮겨 칸에 넣는다.
  Future<void> _finish() async {
    final capture = _capture;
    if (capture == null || !capture.recording || _working) return;
    if (_active == this) _active = null;
    setState(() => _working = true);
    try {
      final clip = await capture.stop();
      if (!mounted) return;
      if (clip == null) {
        showMessage(context, '녹음된 소리가 없어요. 통화 중이거나 다른 앱이 마이크를 쓰고 있는지 확인하고 다시 말씀해 주세요.');
        return;
      }
      final text = await widget.transcribe!(clip);
      if (mounted) _insert(text);
    } catch (error) {
      // 서버가 준 안내(못 알아들었어요·너무 길어요·잠시 뒤에)는 그대로, 녹음기의 영어 원문은 보이지 않는다
      if (mounted) showMessage(context, error is ApiException ? error : '글로 옮기지 못했어요. 다시 말씀하시거나 글로 적어 주세요.');
    } finally {
      if (mounted) setState(() => _working = false);
    }
  }

  Future<void> _cancel() async {
    if (_active == this) _active = null;
    await _capture?.cancel();
    if (mounted) setState(() {});
  }

  /// 받아쓴 글을 칸에 넣는다. 적어 둔 글이 있으면 뒤에 잇는다.
  void _insert(String text) {
    final said = text.trim();
    if (said.isEmpty) {
      showMessage(context, '말씀을 알아듣지 못했어요. 다시 한번 말씀해 주세요.');
      return;
    }
    final before = widget.controller.text.trimRight();
    var next = before.isEmpty ? said : '$before $said';
    final max = widget.maxLength;
    if (max != null && next.characters.length > max) {
      next = next.characters.take(max).toString().trimRight();
      showMessage(context, '$max자까지 적을 수 있어서 앞부분만 넣었어요. 고쳐서 줄여 주세요.');
    }
    widget.controller.value = TextEditingValue(text: next, selection: TextSelection.collapsed(offset: next.length));
    widget.onChanged?.call(next);
  }

  /// 녹음을 시작하지 못했다. 마이크를 막았으면 글로 적게 하고, 카카오톡 안이면 다른 브라우저로 열 수 있게 한다.
  Future<void> _cannotRecord(VoiceStartFailure failure) async {
    final asked = await askOpenOutside(
      context,
      failure,
      title: '카카오톡 안에서는 말로 적기가 안 될 수 있어요',
      body: '다른 브라우저(크롬·사파리 등)로 열면 말로 답할 수 있어요. 지금은 글로 적어 주셔도 돼요.',
      stay: '글로 적을게요',
    );
    if (asked) return;
    if (!mounted) return;
    showMessage(context, switch (failure) {
      VoiceStartFailure.denied => '마이크를 쓸 수 없어요. 글로 적어 주세요.',
      VoiceStartFailure.busy => '마이크를 켜지 못했어요. 통화 중이거나 다른 앱이 마이크를 쓰고 있는지 확인해 주세요.',
      VoiceStartFailure.unsupported => '이 브라우저에서는 말로 적을 수 없어요. 글로 적어 주세요.',
    });
  }

  @override
  Widget build(BuildContext context) {
    if (widget.transcribe == null) return widget.child;
    final recording = _recording;
    final busy = _working || _starting;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: widget.child),
            const SizedBox(width: 8),
            Tooltip(
              message: recording ? '다 말했어요' : '말로 적기',
              child: SizedBox(
                width: 52,
                height: 52,
                child: IconButton(
                  style: IconButton.styleFrom(
                    backgroundColor: recording ? AppColors.danger : AppColors.primarySoft,
                    foregroundColor: recording ? Colors.white : AppColors.primaryDeep,
                    disabledBackgroundColor: AppColors.sunk,
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
                  ),
                  onPressed: busy || (!widget.enabled && !recording) ? null : _toggle,
                  icon: _working
                      ? const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.4))
                      : Icon(recording ? Icons.stop_rounded : Icons.mic_none_rounded, size: 28),
                ),
              ),
            ),
          ],
        ),
        if (recording || _working) ...[
          const SizedBox(height: 8),
          _status(recording),
        ],
      ],
    );
  }

  /// 녹음하는 동안: 흐른 시간과 '말씀하신 내용을 글로 옮겨 드려요'. 옮기는 동안: 기다려 달라는 말.
  Widget _status(bool recording) {
    final capture = _capture!;
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 8, 6, 8),
      decoration: BoxDecoration(
        color: recording ? AppColors.dangerSoft : AppColors.primarySoft,
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: [
          if (recording) ...[
            const RecordingDot(),
            const SizedBox(width: 8),
            ValueListenableBuilder<int>(
              valueListenable: capture.seconds,
              builder: (context, seconds, _) => Text(
                '${voiceClock(seconds)} / ${voiceClock(widget.maxSeconds)}',
                style: const TextStyle(
                  fontWeight: FontWeight.w700,
                  fontSize: 15,
                  color: AppColors.danger,
                  fontFeatures: [FontFeature.tabularFigures()],
                ),
              ),
            ),
            const SizedBox(width: 10),
          ],
          Expanded(
            child: Text(
              keepAll(recording ? '말씀하신 내용을 글로 옮겨 드려요' : '글로 옮기고 있어요…'),
              style: const TextStyle(color: AppColors.ink2, fontSize: 14.5, height: 1.4),
            ),
          ),
          if (recording)
            TextButton(
              onPressed: _cancel,
              style: TextButton.styleFrom(minimumSize: const Size(56, 44)),
              child: const Text('취소'),
            )
          else
            const SizedBox(height: 44),
        ],
      ),
    );
  }
}

/// 녹음 중이라는 빨간 점(천천히 깜빡인다).
class RecordingDot extends StatefulWidget {
  const RecordingDot({super.key});

  @override
  State<RecordingDot> createState() => _RecordingDotState();
}

class _RecordingDotState extends State<RecordingDot> with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 900))
    ..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: Tween(begin: 0.3, end: 1.0).animate(_controller),
      child: Container(
        width: 9,
        height: 9,
        decoration: const BoxDecoration(color: AppColors.danger, shape: BoxShape.circle),
      ),
    );
  }
}
