import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_client.dart';
import '../../core/theme.dart';
import '../../shared/widgets/file_drop.dart';
import '../../shared/widgets/page_events.dart';
import '../../shared/widgets/ui.dart';
import '../billing/data/billing_api.dart';
import '../chat/persona_chat_page.dart';
import '../legal/consent_checks.dart';
import '../share/roles.dart';
import 'data/persona_api.dart';
import 'data/persona_store.dart';
import 'owner_actions.dart';
import 'recordings_page.dart'
    show
        defaultRecordingMaxBytes,
        pickRecordingFiles,
        readPicked,
        recordingMaxBytes,
        recordingPledgeLabel,
        recordingTooBigText,
        recordingTypes,
        screenRecordings;
import 'steps/questions_step.dart';
import 'steps/speakers_step.dart';
import '../../shared/korean.dart';

/// 페르소나 하나의 만들기 흐름. 서버의 단계(step)에 맞는 화면을 보여준다.
///   UPLOAD → SPEAKERS → QUESTIONS → BUILDING → READY(대화)
class PersonaFlowPage extends StatefulWidget {
  final String personaId;

  const PersonaFlowPage({super.key, required this.personaId});

  @override
  State<PersonaFlowPage> createState() => _PersonaFlowPageState();
}

class _PersonaFlowPageState extends State<PersonaFlowPage> {
  Map<String, dynamic>? _detail;
  Object? _error;
  Timer? _timer;

  /// 다른 앱·탭에 다녀오면 새로 읽는다(그분 동의 녹음 확인·알림을 다른 기기에서 보고 왔을 때).
  void Function()? _stopListening;

  @override
  void initState() {
    super.initState();
    _load();
    personaEdited.addListener(_onEdited);
    _stopListening = PageEvents.listen((visible) {
      if (visible && mounted && _detail != null) _load();
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    _stopListening?.call();
    personaEdited.removeListener(_onEdited);
    super.dispose();
  }

  /// 정보 고치기에서 저장하면 이름·호칭과 반영 진행을 다시 읽는다.
  void _onEdited() {
    if (personaEdited.value.$1 == widget.personaId) _load();
  }

  Map<String, dynamic>? _job(String kind) {
    final jobs = _detail?['jobs'] as Map?;
    return jobs?[kind] == null ? null : Map<String, dynamic>.from(jobs![kind]);
  }

  Future<void> _load() async {
    _timer?.cancel();
    try {
      final detail = await PersonaApi.detail(widget.personaId);
      if (!mounted) return;
      final stepChanged = _detail != null && _detail!['step'] != detail['step'];
      setState(() {
        _detail = detail;
        _error = null;
      });
      // 왼쪽 목록의 상태도 같이 바꾼다
      if (stepChanged) personaStore.load();
      // 뒤에서 도는 일이 있으면 2초마다 다시 읽는다
      if (_waiting) _timer = Timer(const Duration(seconds: 2), _load);
    } catch (error) {
      if (!mounted) return;
      // 함께 쓰던 가족이 그사이 빠졌다(그만두기·내보내기·그만 함께 쓰기): 목록으로 돌아간다
      if (error is ApiException && error.statusCode == 404 && isMember(personaStore.byId(widget.personaId))) {
        leaveRemovedShare(context);
        return;
      }
      setState(() => _error = error);
      // 잠깐 못 읽었어도 진행 중이던 일이 있으면 조금 뒤 다시 읽는다
      if (_waiting) {
        _timer = Timer(const Duration(seconds: 8), _load);
      } else if (_detail == null && !_retried && ApiException.isTransient(error)) {
        // 처음 열 때 서버를 바꾸는 중(배포)이거나 연결이 잠깐 끊겼으면 한 번 더 읽어 본다.
        // 그래도 안 되면 '다시 불러오기' 단추가 보인다
        _retried = true;
        _timer = Timer(const Duration(seconds: 3), _load);
      }
    }
  }

  /// 처음 읽기를 저절로 한 번 더 해 봤는지.
  bool _retried = false;

  void _retry() {
    setState(() => _error = null);
    _load();
  }

  bool get _waiting {
    final step = _detail?['step'];
    return (step == 'UPLOAD' && _job('TRANSCRIBE')?['status'] == 'RUNNING') ||
        (step == 'BUILDING' && _job('BUILD')?['status'] != 'FAILED') ||
        // 목소리를 뒤에서 새로 만드는 동안(VOICE)도 다 될 때까지 다시 읽어, 다시 만들기 단추를 풀어 준다
        (step == 'READY' &&
            (_job('UPDATE')?['status'] == 'RUNNING' ||
                _job('EXTEND')?['status'] == 'RUNNING' ||
                _job('VOICE')?['status'] == 'RUNNING'));
  }

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    if (detail == null) {
      return Scaffold(
        appBar: AppBar(automaticallyImplyLeading: !Breakpoints.isWide(context)),
        body: _error != null ? LoadFailed(_error!, onRetry: _retry) : const Loading(),
      );
    }
    // 함께하는 가족은 만들기 단계를 보지 않는다(서버는 다 만든 대화 상대만 나눠 준다)
    if (isMember(detail) && detail['step'] != 'READY') return _MemberWaiting(detail: detail);
    return switch (detail['step'] as String) {
      'READY' => PersonaChatPage(persona: detail),
      'UPLOAD' => _UploadStep(detail: detail, job: _job('TRANSCRIBE'), onChanged: _load),
      'SPEAKERS' => SpeakersStep(detail: detail, onChanged: _load),
      'QUESTIONS' => QuestionsStep(detail: detail, onChanged: _load),
      _ => _BuildingStep(detail: detail, job: _job('BUILD'), onChanged: _load),
    };
  }
}

/// 만들기 단계 화면 오른쪽 위 메뉴: 이 대화 상대 지우기.
class PersonaMenu extends StatelessWidget {
  final Map<String, dynamic> detail;

  const PersonaMenu({super.key, required this.detail});

  @override
  Widget build(BuildContext context) {
    return PopupMenuButton<String>(
      tooltip: '더 보기',
      icon: const Icon(Icons.more_horiz_rounded),
      onSelected: (v) => v == 'edit' ? context.go('/p/${detail['id']}/edit') : deletePersona(context, detail),
      itemBuilder: (_) => [
        // 만드는 중에는 고칠 수 없다(서버도 막는다)
        if (detail['step'] != 'BUILDING') const PopupMenuItem(value: 'edit', child: Text('정보 고치기')),
        const PopupMenuItem(value: 'delete', child: Text('이 대화 상대 지우기', style: TextStyle(color: AppColors.danger))),
      ],
    );
  }
}

Future<void> deletePersona(BuildContext context, Map<String, dynamic> detail) async {
  final id = detail['id'] as String;
  // 결제 중인 요금제가 있으면 지우는 순간 구독도 끝난다는 걸 미리 알린다
  final billing = billingStore.persona(id) ?? (detail['step'] == 'READY' ? await billingStore.loadPersona(id) : null);
  if (!context.mounted) return;
  final paying = const {'ACTIVE', 'CANCELING', 'PAST_DUE'}.contains(billing?['status']);
  final ok = await confirmAction(
    context,
    '${personaTitle(detail)} 대화 상대를 지울까요?',
    '올린 녹음과 나눈 대화도 모두 지워지고, 되돌릴 수 없어요.'
    '${paying ? ' 이 대화 상대의 요금제 구독도 바로 끝나요. 이미 결제한 날이 남아 있어도 쓸 수 없어요.' : ''}'
    ' 함께 쓰는 가족이 있으면 그 가족도 더는 이야기할 수 없고, 가족이 나눈 대화도 지워져요.',
  );
  if (!ok) return;
  try {
    final result = await PersonaApi.delete(id);
    personaStore.load();
    // 서버 백업에 남는 날까지 사실대로 알린다(서버가 정한 글 그대로)
    final message = '${result['message'] ?? ''}'.trim();
    if (message.isNotEmpty && context.mounted) {
      await showDialog<void>(
        context: context,
        builder: (dialog) => AlertDialog(
          title: const Text('대화 상대를 지웠어요'),
          content: Text(keepAll(message), style: AppText.lead),
          actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          actions: [
            FilledButton(
              onPressed: () => Navigator.pop(dialog),
              style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
              child: const Text('확인'),
            ),
          ],
        ),
      );
    }
    if (context.mounted) context.go('/');
  } catch (error) {
    if (context.mounted) showMessage(context, error);
  }
}

/// 함께 쓰던 대화 상대에서 빠졌을 때(그만두기·내보내기·만든 분이 그만 함께 쓰기): 목록을 새로 읽고 첫 화면으로.
void leaveRemovedShare(BuildContext context) {
  personaStore.load();
  final messenger = ScaffoldMessenger.of(context);
  context.go('/');
  messenger
    ..hideCurrentSnackBar()
    ..showSnackBar(const SnackBar(content: Text('더는 함께 쓰지 않는 대화 상대예요.')));
}

/// 함께하는 가족이 다 만들기 전의 대화 상대를 열었을 때(만든 분이 정보를 고치는 중 등).
class _MemberWaiting extends StatelessWidget {
  final Map<String, dynamic> detail;

  const _MemberWaiting({required this.detail});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(automaticallyImplyLeading: !Breakpoints.isWide(context), title: Text(personaTitle(detail))),
      body: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(20, 24, 20, 32),
        child: PageBody(
          child: ProgressCard(
            title: '준비 중이에요',
            note: '${ownerNameOf(detail)}님이 대화 상대를 다듬고 있어요. 조금 뒤 다시 열어 주세요.',
          ),
        ),
      ),
    );
  }
}

// ────────────────────────── 2. 녹음 올리기 ──────────────────────────

class _UploadStep extends StatefulWidget {
  final Map<String, dynamic> detail;
  final Map<String, dynamic>? job;
  final VoidCallback onChanged;

  const _UploadStep({required this.detail, required this.job, required this.onChanged});

  @override
  State<_UploadStep> createState() => _UploadStepState();
}

class _UploadStepState extends State<_UploadStep> {
  late List<dynamic> _recordings = List.of(widget.detail['recordings'] as List);
  double? _progress;
  bool _starting = false;

  /// 녹음 올리기 전 확인(recording_pledge). 이 대화 상대에 이미 확인 기록이 있으면 묻지 않는다.
  bool _pledged = false;

  String get _id => widget.detail['id'];
  String get _title => personaTitle(widget.detail);

  bool get _needsPledge => (widget.detail['consents'] as Map?)?['recordingPledge'] != true;

  bool get _canUpload => !_needsPledge || _pledged;

  /// 확인 전에는 올리지 않는다(고르기·끌어다 놓기 모두).
  bool _checkPledge() {
    if (_canUpload) return true;
    showMessage(context, '녹음을 올리기 전에 아래 확인에 체크해 주세요.');
    return false;
  }

  /// 지우는 중인 녹음(같은 줄의 빼기를 막는다).
  final Set<String> _removing = {};

  /// 녹음 파일 하나 크기 상한(서버 /intake/meta 의 maxUploadMb).
  int _maxBytes = defaultRecordingMaxBytes;

  @override
  void initState() {
    super.initState();
    recordingMaxBytes().then((value) => _maxBytes = value);
  }

  @override
  void didUpdateWidget(covariant _UploadStep old) {
    super.didUpdateWidget(old);
    // 위 화면이 새로 읽은 정보를 줄 때만 목록을 바꾼다. 창 크기만 바뀌어 다시 그려질 때 옛 정보로
    // 덮으면 방금 올린 녹음이 사라지거나 지운 녹음이 다시 보인다
    if (!identical(old.detail, widget.detail)) _recordings = List.of(widget.detail['recordings'] as List);
    // 정보 고치기에서 그분 상태가 바뀌면 확인 글도 바뀐다. 앞서 한 체크는 쓰지 않는다
    if (pledgeDocOf(old.detail) != pledgeDocOf(widget.detail)) _pledged = false;
  }

  Future<void> _pick() async {
    if (!_checkPledge()) return;
    final picked = await pickRecordingFiles();
    if (picked.isEmpty || !mounted) return;
    // 내용을 읽기 전에 크기·개수부터 본다(큰 영상을 통째로 읽거나 다 올린 뒤 거절당하지 않게)
    final screened = screenRecordings(
      picked,
      name: (f) => f.name,
      size: (f) => f.size,
      existing: _recordings.length,
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
      existing: _recordings.length,
      maxBytes: _maxBytes,
    );
    if (screened.message != null) showMessage(context, screened.message!);
    files = screened.keep;
    if (files.isEmpty) return;
    setState(() => _progress = 0);
    try {
      final recordings = await PersonaApi.upload(
        _id,
        files,
        pledge: _needsPledge && _pledged,
        onProgress: (p) => mounted ? setState(() => _progress = p) : null,
      );
      if (!mounted) return;
      setState(() => _recordings = recordings);
      // 위 화면도 새로 읽는다(확인 기록·목록이 바뀌었다)
      widget.onChanged();
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
      final recordings = await PersonaApi.deleteRecording(_id, recordingId);
      if (!mounted) return;
      setState(() => _recordings = recordings);
      widget.onChanged();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _removing.remove(recordingId));
    }
  }

  Future<void> _start() async {
    setState(() => _starting = true);
    try {
      await PersonaApi.transcribe(_id);
      widget.onChanged();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _starting = false);
    }
  }

  static const _video = {'mp4', 'mov', 'm4v', 'mkv', 'avi', 'wmv', 'mpeg', 'mpg'};

  @override
  Widget build(BuildContext context) {
    final job = widget.job;
    if (job?['status'] == 'RUNNING') {
      return WizardScaffold(
        title: _title,
        step: 1,
        actions: [PersonaMenu(detail: widget.detail)],
        headline: '녹음을 살펴보고 있어요',
        lead: '말소리를 글자로 옮기고, 누가 말했는지 사람별로 나누고 있어요.',
        children: [ProgressCard(title: job?['detail'] ?? '준비하는 중', percent: job?['percent'] as num?)],
      );
    }
    final failed = job?['status'] == 'FAILED';
    final uploading = _progress != null;
    // 살아 계신 분: 그분 동의 녹음이 확인되기 전에는 녹음을 올려 두기만 하고 살펴보지 않는다(서버가 정한다)
    final gates = PersonaGates.of(widget.detail);
    final held = !gates.audio;
    final next = FilledButton(
      onPressed: _recordings.isEmpty || _starting || uploading || held ? null : _start,
      child: Text(failed ? '다시 시도' : '다음: 그분 목소리 찾기'),
    );
    return FileDropArea(
      allowed: recordingTypes,
      enabled: !uploading,
      hint: '여기에 놓으면 녹음이 올라가요',
      onFiles: _upload,
      onRejected: (names) => showMessage(context, '녹음·영상 파일만 올릴 수 있어요: ${names.join(', ')}'),
      maxBytes: _maxBytes,
      onTooBig: (names) => showMessage(context, recordingTooBigText(names, _maxBytes)),
      child: WizardScaffold(
      title: _title,
      step: 1,
      actions: [PersonaMenu(detail: widget.detail)],
      headline: '$_title 목소리가 담긴\n녹음을 올려주세요',
      lead: '통화 녹음, 인터뷰, 가족 영상 모두 괜찮아요. 여러 개일수록 말투와 기억을 더 잘 익혀요.',
      primary: !held
          ? next
          : Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                next,
                const SizedBox(height: 4),
                Text(
                  keepAll(gates.code == null || gates.code == 'subject_consent'
                      ? '그분 동의 녹음이 확인되면 이어서 할 수 있어요.'
                      : gates.message ?? '지금은 이어서 할 수 없어요.'),
                  textAlign: TextAlign.center,
                  style: AppText.small.copyWith(fontSize: 12.5),
                ),
              ],
            ),
      children: [
        for (final action in setupActionsOf(widget.detail)) ...[
          OwnerActionCard(
            action: action,
            consentStatus: subjectConsentOf(widget.detail)?['status'] as String?,
            onDelete: () => deletePersona(context, widget.detail),
          ),
          const SizedBox(height: 16),
        ],
        if (failed) ...[ErrorBox(job?['error'] ?? '녹음을 살펴보지 못했어요.'), const SizedBox(height: 16)],
        // 그분 목소리 찾기에서 녹음을 더 올리러 돌아온 경우
        if (_recordings.any((r) => r['status'] == 'TRANSCRIBED')) ...[
          const Notice(
            '이미 살펴본 녹음은 그대로 두고, 새로 올린 녹음만 더 살펴볼게요. 그다음 목소리를 사람별로 다시 나눠요.',
            icon: Icons.add_circle_outline_rounded,
            color: AppColors.primary,
            background: AppColors.primarySoft,
          ),
          const SizedBox(height: 16),
        ],
        if (_needsPledge) ...[
          AppCard(
            padding: const EdgeInsets.fromLTRB(6, 6, 6, 6),
            borderColor: _pledged ? AppColors.primary : null,
            child: ConsentCheck(
              label: recordingPledgeLabel(_title, pledgeDoc: pledgeDocOf(widget.detail)),
              value: _pledged,
              onChanged: (v) => setState(() => _pledged = v),
              docKey: 'recording_pledge',
              callName: _title,
            ),
          ),
          const SizedBox(height: 12),
        ],
        Material(
          color: _canUpload ? AppColors.card : AppColors.sunk,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(18),
            side: const BorderSide(color: AppColors.line2, width: 1.4),
          ),
          child: InkWell(
            onTap: uploading ? null : _pick,
            borderRadius: BorderRadius.circular(18),
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(vertical: 30, horizontal: 16),
              child: Column(
                children: [
                  Container(
                    width: 52,
                    height: 52,
                    decoration: const BoxDecoration(color: AppColors.primarySoft, shape: BoxShape.circle),
                    child: const Icon(Icons.upload_rounded, color: AppColors.primary, size: 26),
                  ),
                  const SizedBox(height: 14),
                  Text(
                    uploading
                        ? (_progress! >= 0.999 ? '올린 녹음을 저장하는 중…' : '올리는 중… ${(_progress! * 100).round()}%')
                        : '녹음·영상 파일 고르기',
                    style: AppText.heading,
                  ),
                  const SizedBox(height: 4),
                  Text(
                    _canUpload ? '통화 녹음·음성 메모·영상 모두 돼요 (mp3·m4a·wav·mp4·mov 등)' : '위 확인에 체크하면 올릴 수 있어요',
                    style: AppText.small,
                  ),
                  if (uploading) ...[
                    const SizedBox(height: 16),
                    ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 260),
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(4),
                        child: LinearProgressIndicator(value: _progress, minHeight: 5),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
        const MultiPickHint(),
        if (_recordings.isNotEmpty) ...[
          const SizedBox(height: 22),
          SectionLabel('올린 파일 ${_recordings.length}개'),
          AppCard(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
            child: Column(
              children: [
                for (var i = 0; i < _recordings.length; i++) ...[
                  if (i > 0) const Divider(height: 1, indent: 64),
                  _fileRow(_recordings[i]),
                ],
              ],
            ),
          ),
        ],
        const SizedBox(height: 22),
        const Notice(
          '그분 목소리가 또렷하게 1분 이상 담긴 녹음이 좋아요. 녹음을 다 합쳐 그분 목소리가 10초 이상이면 만들 수 있어요. '
          '영상은 소리만 뽑아 쓰고 원본 영상은 지워요.',
          icon: Icons.lightbulb_outline_rounded,
        ),
      ],
      ),
    );
  }

  Widget _fileRow(dynamic r) {
    final name = r['originalName'] as String;
    final ext = name.contains('.') ? name.split('.').last.toLowerCase() : '';
    final phone = r['phoneBand'] == true;
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 10, 0, 10),
      child: Row(
        children: [
          Container(
            width: 40,
            height: 40,
            decoration: BoxDecoration(color: AppColors.sunk, borderRadius: BorderRadius.circular(12)),
            child: Icon(
              phone
                  ? Icons.phone_in_talk_outlined
                  : _video.contains(ext)
                      ? Icons.movie_outlined
                      : Icons.graphic_eq_rounded,
              size: 20,
              color: AppColors.ink2,
            ),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(name, overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Text('${_size(r['sizeBytes'] as int)}${phone ? ' · 통화 녹음' : ''}', style: AppText.small),
              ],
            ),
          ),
          IconButton(
            tooltip: '빼기',
            icon: const Icon(Icons.close_rounded, size: 20, color: AppColors.muted),
            onPressed: _removing.contains(r['id']) ? null : () => _remove(r['id']),
          ),
        ],
      ),
    );
  }

  static String _size(int bytes) =>
      bytes >= 1048576 ? '${(bytes / 1048576).toStringAsFixed(1)}MB' : '${(bytes / 1024).ceil()}KB';
}

// ────────────────────────── 만드는 중 ──────────────────────────

class _BuildingStep extends StatefulWidget {
  final Map<String, dynamic> detail;
  final Map<String, dynamic>? job;
  final VoidCallback onChanged;

  const _BuildingStep({required this.detail, required this.job, required this.onChanged});

  @override
  State<_BuildingStep> createState() => _BuildingStepState();
}

class _BuildingStepState extends State<_BuildingStep> {
  bool _busy = false;

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
      widget.onChanged();
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final id = widget.detail['id'] as String;
    final title = personaTitle(widget.detail);
    final job = widget.job;
    final failed = job?['status'] == 'FAILED';
    return WizardScaffold(
      title: title,
      actions: [PersonaMenu(detail: widget.detail)],
      headline: failed ? '만들다가 멈췄어요' : '${josa(title, '과', '와')} 대화할\n준비를 하고 있어요',
      lead: failed ? '다시 시도하거나, 답을 고친 뒤 다시 만들 수 있어요.' : '녹음 속 말투와 이야기, 적어 주신 답을 하나로 모으고 있어요.',
      secondary: failed
          ? TextButton(
              onPressed: _busy ? null : () => _run(() => PersonaApi.backToQuestions(id)),
              child: const Text('답 고치기'),
            )
          : null,
      primary: failed
          ? FilledButton(
              onPressed: _busy
                  ? null
                  : () => _run(() async {
                        try {
                          await PersonaApi.build(id, Map<String, dynamic>.from(widget.detail['answers'] ?? {}));
                        } on ApiException catch (error) {
                          // 예전에 만들다 멈춘 페르소나는 목소리 사용 동의가 없다. 동의 칸이 있는 질문 단계로.
                          // 돌아가신 분의 신청 자격·유족 명단이 없을 때(409 bereaved)도 그 칸이 있는 질문 단계로.
                          final askAgain =
                              error.code == 'bereaved' || (error.statusCode == 400 && error.message.contains('동의'));
                          if (!askAgain) rethrow;
                          await PersonaApi.backToQuestions(id);
                        }
                      }),
              child: const Text('다시 시도'),
            )
          : null,
      children: [
        // 만들다 멈춘 까닭이 그분 동의 녹음·법정대리인 확인이면 그 카드(누르면 그리로)
        if (failed)
          for (final action in ownerActionsOf(widget.detail)) ...[
            OwnerActionCard(action: action, consentStatus: subjectConsentOf(widget.detail)?['status'] as String?),
            const SizedBox(height: 14),
          ],
        if (failed)
          ErrorBox(job?['error'] ?? '만들지 못했어요.')
        else
          ProgressCard(
            title: job?['detail'] ?? '준비하는 중',
            percent: job?['percent'] as num?,
            note: '몇 분 걸려요. 다 되면 바로 대화할 수 있어요.\n이 화면을 닫아도 계속 진행돼요.',
          ),
      ],
    );
  }
}
