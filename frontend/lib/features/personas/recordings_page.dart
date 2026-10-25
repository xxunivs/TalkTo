import 'dart:async';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/file_drop.dart';
import '../../shared/widgets/ui.dart';
import '../legal/consent_checks.dart';
import 'data/persona_api.dart';
import 'data/persona_store.dart';
import 'imported.dart';
import 'owner_actions.dart';
import 'steps/questions_step.dart' show pledgeDocOf;

/// 올릴 수 있는 녹음·영상(영상은 서버가 소리만 뽑는다).
const recordingTypes = {
  'wav', 'm4a', 'mp3', 'webm', 'ogg', 'flac', 'aac', 'opus', 'amr', '3gp', //
  'mp4', 'mov', 'm4v', 'mkv', 'avi', 'wmv', 'mpeg', 'mpg',
};

/// 대화 상대 하나에 올릴 수 있는 녹음 수(서버 MAX_RECORDINGS 와 같다).
const maxRecordings = 20;

/// 녹음 파일 하나 크기 상한. 서버가 /intake/meta 의 maxUploadMb 로 알려 주고, 못 읽으면 이 값(서버 기본 300MB).
const defaultRecordingMaxBytes = 300 * 1024 * 1024;

/// 서버가 정한 녹음 파일 하나 크기 상한(바이트).
Future<int> recordingMaxBytes() async {
  try {
    final mb = (await PersonaApi.meta())['maxUploadMb'];
    if (mb is num && mb > 0) return (mb * 1024 * 1024).toInt();
  } catch (_) {
    // 못 읽으면 기본값(서버도 같은 값으로 막는다)
  }
  return defaultRecordingMaxBytes;
}

/// 너무 커서 올리지 않은 녹음 파일을 알리는 글.
String recordingTooBigText(List<String> names, int maxBytes) =>
    '파일 하나는 ${maxBytes ~/ (1024 * 1024)}MB까지예요: ${names.join(', ')}';

/// 올리기 전에 고른 녹음을 본다. 서버는 다 받은 뒤에야 거절해서, 큰 영상이면 긴 업로드가 헛수고가 되고
/// 한 파일 때문에 함께 고른 파일이 모두 실패한다. 파일 하나가 [maxBytes] 를 넘으면 빼고, 이미 올린
/// [existing] 개와 합쳐 [maxRecordings] 개를 넘으면 아무것도 올리지 않는다. message 는 알릴 까닭(없으면 null).
({List<T> keep, String? message}) screenRecordings<T>(
  List<T> files, {
  required String Function(T file) name,
  required int Function(T file) size,
  required int existing,
  required int maxBytes,
}) {
  final keep = [for (final f in files) if (size(f) <= maxBytes) f];
  final tooBig = [for (final f in files) if (size(f) > maxBytes) name(f)];
  final left = maxRecordings - existing;
  if (keep.length > left) {
    return (
      keep: <T>[],
      message: left <= 0
          ? '녹음은 $maxRecordings개까지 올릴 수 있어요. 필요 없는 녹음을 빼고 올려 주세요.'
          : '녹음은 $maxRecordings개까지 올릴 수 있어요. 지금은 $left개 더 올릴 수 있어요.',
    );
  }
  return (keep: keep, message: tooBig.isEmpty ? null : recordingTooBigText(tooBig, maxBytes));
}

/// 녹음·영상 파일 고르기. 고를 때는 내용을 읽지 않는다(크기를 본 뒤 올릴 파일만 [readPicked] 로 읽어
/// 큰 영상 때문에 휴대폰 브라우저가 멈추지 않게).
Future<List<PlatformFile>> pickRecordingFiles() async {
  final result = await FilePicker.pickFiles(
    allowMultiple: true,
    type: FileType.custom,
    // 음성과 영상 모두. 영상은 서버가 소리만 뽑는다.
    allowedExtensions: recordingTypes.toList(),
    withData: false,
    withReadStream: true,
  );
  return result?.files ?? const [];
}

/// 고른 파일의 내용을 읽는다(읽지 못한 파일은 뺀다).
Future<List<PickedFile>> readPicked(List<PlatformFile> files) async {
  final out = <PickedFile>[];
  for (final f in files) {
    var bytes = f.bytes;
    final stream = f.readStream;
    if (bytes == null && stream != null) {
      final buffer = BytesBuilder(copy: false);
      await for (final chunk in stream) {
        buffer.add(chunk);
      }
      bytes = buffer.takeBytes();
    }
    // 빈 파일도 보낸다(서버가 '빈 파일이에요'라고 알려 준다)
    if (bytes != null) out.add((name: f.name, bytes: bytes));
  }
  return out;
}

/// 녹음 올리기 전 확인(recording_pledge) 체크 글. 전문은 '보기'로 연다.
/// [pledgeDoc] 은 그분 상태에 맞는 확인 문서(pledge_self·pledge_deceased …). 나 자신은 호칭이
/// '나'라서 '나께서'가 되지 않게 따로 쓰고, 살아 계신 분이면 올리기 전 허락을 함께 확인한다.
String recordingPledgeLabel(String title, {required String pledgeDoc}) {
  if (pledgeDoc == 'pledge_self') {
    return '올리는 녹음은 제가 정당하게 가진 것이고, 제가 직접 참여한 대화예요. '
        '남들끼리 나눈 대화를 몰래 녹음한 파일은 올리지 않아요.';
  }
  final living = pledgeDoc != 'pledge_deceased'
      ? '$title께 녹음이 미국 회사로 보내져 처리된다는 것까지 말씀드리고, 올리기 전에 직접 허락을 받았어요. '
          '그분 동의 녹음이 확인되기 전에는 저장만 해요. '
      : '';
  return '올리는 녹음은 제가 정당하게 가진 것이고, 저나 $title께서 직접 참여한 대화예요. $living'
      '남들끼리 나눈 대화를 몰래 녹음한 파일은 올리지 않아요.';
}

/// 다 만든 페르소나에 녹음을 더한다(기억 더하기).
class RecordingsPage extends StatefulWidget {
  final String personaId;

  const RecordingsPage({super.key, required this.personaId});

  @override
  State<RecordingsPage> createState() => _RecordingsPageState();
}

class _RecordingsPageState extends State<RecordingsPage> {
  Map<String, dynamic>? _detail;
  Object? _error;
  double? _progress;
  bool _busy = false;
  Timer? _timer;

  /// 녹음 올리기 전 확인(recording_pledge). 이 대화 상대에 이미 확인 기록이 있으면 묻지 않는다.
  bool _pledged = false;

  String get _id => widget.personaId;

  bool get _needsPledge => (_detail?['consents'] as Map?)?['recordingPledge'] != true;

  bool get _canUpload => !_needsPledge || _pledged;

  /// 확인 전에는 올리지 않는다(고르기·끌어다 놓기 모두).
  bool _checkPledge() {
    if (_canUpload) return true;
    showMessage(context, '녹음을 올리기 전에 위 확인에 체크해 주세요.');
    return false;
  }

  /// 지우는 중인 녹음(같은 줄의 빼기를 막는다).
  final Set<String> _removing = {};

  /// 녹음 파일 하나 크기 상한(서버 /intake/meta 의 maxUploadMb).
  int _maxBytes = defaultRecordingMaxBytes;

  @override
  void initState() {
    super.initState();
    _load();
    recordingMaxBytes().then((value) => _maxBytes = value);
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Map<String, dynamic>? get _job => ((_detail?['jobs'] as Map?)?['EXTEND'] as Map?)?.cast<String, dynamic>();

  Future<void> _load() async {
    _timer?.cancel();
    try {
      final detail = await PersonaApi.detail(_id);
      if (!mounted) return;
      final wasRunning = _job?['status'] == 'RUNNING';
      setState(() {
        _detail = detail;
        _error = null;
      });
      if (_job?['status'] == 'RUNNING') {
        _timer = Timer(const Duration(seconds: 3), _load);
      } else if (wasRunning) {
        personaStore.load();
        notifyPersonaEdited(_id);
        if (_job?['status'] == 'DONE') {
          final detail = (_job?['detail'] ?? '') as String;
          showMessage(context, detail.startsWith('완료 ·') ? detail.substring(5) : '새 녹음을 다 익혔어요.');
        }
      }
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = error);
      if (_job?['status'] == 'RUNNING') _timer = Timer(const Duration(seconds: 6), _load);
    }
  }

  /// 지금 올라가 있는 녹음 수(서버가 대화 상대마다 [maxRecordings] 개까지 받는다).
  int get _existing => (_detail?['recordings'] as List? ?? const []).length;

  Future<void> _pick() async {
    if (!_checkPledge()) return;
    final picked = await pickRecordingFiles();
    if (picked.isEmpty || !mounted) return;
    // 내용을 읽기 전에 크기·개수부터 본다(큰 영상을 통째로 읽거나 다 올린 뒤 거절당하지 않게)
    final screened = screenRecordings(
      picked,
      name: (f) => f.name,
      size: (f) => f.size,
      existing: _existing,
      maxBytes: _maxBytes,
    );
    if (screened.message != null) showMessage(context, screened.message!);
    if (screened.keep.isEmpty) return;
    await _upload(await readPicked(screened.keep));
  }

  Future<void> _upload(List<PickedFile> files) async {
    if (files.isEmpty || _progress != null || !mounted) return;
    if (!_checkPledge()) return;
    // 끌어다 놓은 파일도 같은 기준으로 본다
    final screened = screenRecordings(
      files,
      name: (f) => f.name,
      size: (f) => f.bytes.length,
      existing: _existing,
      maxBytes: _maxBytes,
    );
    if (screened.message != null) showMessage(context, screened.message!);
    files = screened.keep;
    if (files.isEmpty) return;
    setState(() => _progress = 0);
    try {
      await PersonaApi.upload(
        _id,
        files,
        pledge: _needsPledge && _pledged,
        onProgress: (p) => mounted ? setState(() => _progress = p) : null,
      );
      await _load();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _progress = null);
    }
  }

  Future<void> _remove(String recordingId) async {
    // 두 번 눌러도 한 번만 지운다(두 번째는 '녹음을 찾지 못했어요'가 된다)
    if (_removing.contains(recordingId)) return;
    setState(() => _removing.add(recordingId));
    try {
      await PersonaApi.deleteRecording(_id, recordingId);
      await _load();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _removing.remove(recordingId));
    }
  }

  Future<void> _apply() async {
    setState(() => _busy = true);
    try {
      await PersonaApi.applyRecordings(_id);
      await _load();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    if (detail == null) {
      return Scaffold(
        appBar: AppBar(),
        body: _error != null
            ? LoadFailed(_error!, onRetry: () {
                setState(() => _error = null);
                _load();
              })
            : const Loading(),
      );
    }
    // 가져온 대화 상대는 녹음을 더하면 예전 지침을 덮어써서 쓰지 않는다(서버도 막는다).
    // 함께하는 가족은 녹음을 더하지 않는다(만든 사람만)
    final blocked = PersonaBlocked.of(detail, personaId: _id);
    if (blocked != null) return blocked;
    final title = personaTitle(detail);
    final recordings = detail['recordings'] as List;
    final pending = recordings.where((r) => r['status'] == 'UPLOADED').toList();
    final failedRecs = recordings.where((r) => r['status'] == 'FAILED').toList();
    final done = recordings.where((r) => r['status'] == 'TRANSCRIBED').toList();
    final running = _job?['status'] == 'RUNNING';
    // 앞선 반영이 실패했으면 새 녹음이 없어도 다시 반영할 수 있다
    final retry = _job?['status'] == 'FAILED';
    // 그분 동의 녹음 전·멈춤 등으로 새 녹음을 익힐 수 없으면 올려 두기만 한다(서버가 정한다)
    final gates = PersonaGates.of(detail);
    return FileDropArea(
      allowed: recordingTypes,
      enabled: _progress == null && !running,
      hint: '여기에 놓으면 녹음이 올라가요',
      onFiles: _upload,
      onRejected: (names) => showMessage(context, '녹음·영상 파일만 올릴 수 있어요: ${names.join(', ')}'),
      maxBytes: _maxBytes,
      onTooBig: (names) => showMessage(context, recordingTooBigText(names, _maxBytes)),
      child: WizardScaffold(
      title: '녹음 더하기',
      headline: '$title 녹음 더하기',
      lead: '녹음을 더 올리면 말투와 이야기를 더 익혀요. 익히는 동안에도 대화할 수 있어요.',
      secondary: TextButton(
        onPressed: () => context.canPop() ? context.pop() : context.go('/p/$_id'),
        child: const Text('돌아가기'),
      ),
      primary: FilledButton(
        onPressed: (pending.isEmpty && !retry) || running || _busy || _progress != null || !gates.audio
            ? null
            : _apply,
        child: Text(running
            ? '새 녹음을 익히는 중…'
            : pending.isNotEmpty
                ? '새 녹음 ${pending.length}개 익히기'
                : retry
                    ? '다시 익히기'
                    : '새 녹음을 올려 주세요'),
      ),
      children: [
        if (!gates.audio) ...[
          for (final action in ownerActionsOf(detail)) ...[
            OwnerActionCard(action: action, consentStatus: subjectConsentOf(detail)?['status'] as String?),
            const SizedBox(height: 12),
          ],
          if (ownerActionsOf(detail).isEmpty)
            Notice(gates.message ?? '지금은 새 녹음을 익힐 수 없어요. 올려 두기만 할 수 있어요.'),
          const SizedBox(height: 16),
        ],
        if (running) ...[
          ProgressCard(
            title: _job?['detail'] ?? '준비하는 중',
            percent: _job?['percent'] as num?,
            note: '몇 분 걸려요. 이 화면을 닫아도 계속 진행돼요.',
          ),
          const SizedBox(height: 18),
        ],
        if (_job?['status'] == 'FAILED') ...[
          ErrorBox(_job?['error'] ?? "새 녹음을 익히지 못했어요. '다시 익히기'를 눌러 주세요."),
          const SizedBox(height: 16),
        ],
        if (_needsPledge) ...[
          AppCard(
            padding: const EdgeInsets.fromLTRB(6, 6, 6, 6),
            borderColor: _pledged ? AppColors.primary : null,
            child: ConsentCheck(
              label: recordingPledgeLabel(title, pledgeDoc: pledgeDocOf(detail)),
              value: _pledged,
              onChanged: (v) => setState(() => _pledged = v),
              docKey: 'recording_pledge',
              callName: title,
            ),
          ),
          const SizedBox(height: 12),
        ],
        OutlinedButton.icon(
          onPressed: _progress != null || running || !_canUpload ? null : _pick,
          style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(56)),
          icon: const Icon(Icons.upload_rounded),
          label: Text(
            _progress == null
                ? '녹음·영상 파일 고르기 (여러 개)'
                : _progress! >= 0.999
                    ? '올린 녹음을 저장하는 중…'
                    : '올리는 중… ${(_progress! * 100).round()}%',
          ),
        ),
        if (_progress != null) ...[
          const SizedBox(height: 10),
          Center(child: PercentBar(percent: _progress! * 100)),
        ],
        const MultiPickHint(),
        if (pending.isNotEmpty) ...[
          const SizedBox(height: 22),
          SectionLabel('새로 올린 녹음 ${pending.length}개'),
          AppCard(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
            child: Column(children: [for (final r in pending) _row(r, removable: !running)]),
          ),
        ],
        if (failedRecs.isNotEmpty) ...[
          const SizedBox(height: 22),
          SectionLabel('쓸 수 없는 녹음 ${failedRecs.length}개'),
          AppCard(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
            child: Column(children: [for (final r in failedRecs) _row(r, removable: !running)]),
          ),
        ],
        if (done.isNotEmpty) ...[
          const SizedBox(height: 22),
          SectionLabel('익힌 녹음 ${done.length}개'),
          AppCard(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
            child: Column(children: [for (final r in done) _row(r, removable: false)]),
          ),
        ],
      ],
      ),
    );
  }

  Widget _row(dynamic r, {required bool removable}) {
    final failed = r['status'] == 'FAILED';
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 10, 0, 10),
      child: Row(
        children: [
          Icon(r['phoneBand'] == true ? Icons.phone_in_talk_outlined : Icons.graphic_eq_rounded,
              size: 20, color: AppColors.ink2),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(r['originalName'], overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
                if (failed)
                  Text(r['error'] ?? '이 녹음은 쓸 수 없었어요', style: AppText.small.copyWith(color: AppColors.danger)),
              ],
            ),
          ),
          if (removable)
            IconButton(
              tooltip: '빼기',
              icon: const Icon(Icons.close_rounded, size: 20, color: AppColors.muted),
              onPressed: _removing.contains(r['id']) ? null : () => _remove(r['id']),
            )
          else
            const SizedBox(width: 12),
        ],
      ),
    );
  }
}
