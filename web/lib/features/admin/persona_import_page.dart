import 'dart:convert';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:just_audio/just_audio.dart';

import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import 'import_api.dart';

/// 고른 묶음 파일 하나(이름·내용). 시험에서는 파일 고르는 창 대신 가짜를 넣는다.
typedef BundleFile = ({String name, List<int> bytes});
typedef BundlePicker = Future<BundleFile?> Function();

/// 서버가 받는 요청 크기 상한(core/body_limit.py, 2 MiB). 묶음은 보통 150 KB 안팎이다.
const _maxBundleBytes = 2 * 1024 * 1024;

/// 가져오기 도구(AI 레포)가 만들고 서버(app/schemas/persona_import.py PersonaBundle)가 받는 묶음 format.
const _bundleFormat = 'talkto-persona-bundle';

/// 관리자 화면 · 페르소나 가져오기(/admin/import, 관리자만).
///
/// 예전 MVP 자료로 만든 묶음(.json)을 고른 계정의 대화 상대로 만든다(plan.md 단계 3).
/// ① 계정 고르기(후보 표) ② 묶음 고르기 ③ 정보·지침 방식·목소리 ④ 시험해 보기(dryRun, 쓰지 않음)
/// ⑤ 가져오기 ⑥ 되돌리기(DELETE). 묶음은 가족 자료라 화면에 내용(지침·기억 본문)을 그리지 않고
/// 건수·검사만 보여 준다.
class PersonaImportPage extends StatefulWidget {
  final PersonaImportApi api;
  final BundlePicker? pickBundle;

  const PersonaImportPage({super.key, this.api = const PersonaImportApi(), this.pickBundle});

  @override
  State<PersonaImportPage> createState() => _PersonaImportPageState();
}

class _PersonaImportPageState extends State<PersonaImportPage> {
  // ① 계정
  final _query = TextEditingController();
  List<Map<String, dynamic>>? _users;
  Object? _searchError;
  bool _searching = false;
  Map<String, dynamic>? _target;
  bool _requireKakao = true;

  // ② 묶음
  Map<String, dynamic>? _bundle;
  String? _bundleName;
  int _bundleSize = 0;
  int _bundleStamp = 0;
  String? _bundleError;

  // ③ 정보
  final _name = TextEditingController();
  final _relation = TextEditingController();
  final _addressTerm = TextEditingController();
  final _myName = TextEditingController();
  final _myRelation = TextEditingController();
  final _calledMe = TextEditingController();
  final _deathAnswer = TextEditingController();
  final _checkQuery = TextEditingController();
  String _mode = 'guarded';
  String _deathPolicy = 'ask_only';
  String _voiceAttach = 'none';
  bool _probe = true;

  // ④~⑥ 실행
  String? _running; // 'dry' | 'import' | 'undo'
  Map<String, dynamic>? _preview;
  String? _previewSignature;
  Object? _previewError;
  Map<String, dynamic>? _imported;
  Object? _importError;
  final _undoId = TextEditingController();
  AudioPlayer? _player;

  List<TextEditingController> get _fields =>
      [_name, _relation, _addressTerm, _myName, _myRelation, _calledMe, _deathAnswer, _checkQuery];

  @override
  void initState() {
    super.initState();
    for (final field in [..._fields, _undoId]) {
      field.addListener(_changed);
    }
    _search();
  }

  void _changed() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    for (final field in [..._fields, _query, _undoId]) {
      field.dispose();
    }
    _player?.dispose();
    super.dispose();
  }

  // ───────────────────────── 계정 ─────────────────────────

  Future<void> _search() async {
    setState(() {
      _searching = true;
      _searchError = null;
    });
    try {
      final rows = await widget.api.searchUsers(_query.text);
      if (!mounted) return;
      setState(() {
        _users = rows;
        // 고른 계정이 새 목록에 있으면 새 값으로 바꾼다(대화 상대 수 등)
        final id = _target?['id'];
        for (final row in rows) {
          if (row['id'] == id) _target = row;
        }
      });
    } catch (error) {
      if (mounted) setState(() => _searchError = error);
    } finally {
      if (mounted) setState(() => _searching = false);
    }
  }

  // ───────────────────────── 묶음 ─────────────────────────

  static Future<BundleFile?> _pickFromDevice() async {
    final result = await FilePicker.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['json'],
      withData: true,
    );
    final file = result == null || result.files.isEmpty ? null : result.files.first;
    if (file == null || file.bytes == null) return null;
    return (name: file.name, bytes: file.bytes!);
  }

  Future<void> _pickBundle() async {
    final BundleFile? picked;
    try {
      picked = await (widget.pickBundle ?? _pickFromDevice)();
    } catch (error) {
      if (mounted) setState(() => _bundleError = '파일을 고르지 못했어요. $error');
      return;
    }
    if (picked == null || !mounted) return;
    if (picked.bytes.length > _maxBundleBytes) {
      setState(() => _bundleError = '묶음 파일이 2MB 를 넘어요. 확인용 벡터를 줄여 다시 만들어 주세요.');
      return;
    }
    Map<String, dynamic>? bundle;
    try {
      var text = utf8.decode(picked.bytes);
      if (text.startsWith('\uFEFF')) text = text.substring(1);
      final decoded = jsonDecode(text);
      if (decoded is Map && decoded['memories'] is List) bundle = Map<String, dynamic>.from(decoded);
    } catch (_) {
      bundle = null;
    }
    if (bundle == null) {
      setState(() => _bundleError = '묶음 파일을 읽지 못했어요. 가져오기 도구가 만든 .json 파일인지 확인해 주세요.');
      return;
    }
    final file = picked;
    final persona = _asMap(bundle['persona']);
    final voice = _hasVoice(bundle);
    setState(() {
      _bundle = bundle;
      _bundleName = file.name;
      _bundleSize = file.bytes.length;
      _bundleStamp++;
      _bundleError = null;
      // 비어 있는 칸만 묶음 값으로 채운다(대표가 이미 적은 값은 두고)
      void fill(TextEditingController field, Object? value) {
        if (field.text.trim().isEmpty && value is String && value.trim().isNotEmpty) field.text = value.trim();
      }

      fill(_name, persona['name']);
      fill(_relation, persona['relation']);
      fill(_addressTerm, persona['addressTerm']);
      _voiceAttach = voice ? 'after_owner_pledge' : 'none';
      _probe = voice;
    });
  }

  static bool _hasVoice(Map<String, dynamic>? bundle) {
    final voiceId = _asMap(bundle?['voice'])['voiceId'];
    return voiceId is String && voiceId.trim().isNotEmpty;
  }

  // ───────────────────────── 실행 ─────────────────────────

  /// POST /admin/personas/import 본문(서버 PersonaImportRequest). 지침은 서버가 가져올 때
  /// prompt(mode·deathPolicy·deathAnswer)와 overrides(myName 등)로 조립한다(묶음을 다시 만들지 않는다).
  Map<String, dynamic> _body({required bool dryRun}) {
    String? text(TextEditingController field) {
      final value = field.text.trim();
      return value.isEmpty ? null : value;
    }

    final guarded = _mode == 'guarded';
    final voice = _hasVoice(_bundle);
    final overrides = <String, String?>{
      'name': text(_name),
      'relation': text(_relation),
      'addressTerm': text(_addressTerm),
      'myName': text(_myName),
      'myRelation': text(_myRelation),
      'calledMe': text(_calledMe),
    };
    return {
      'targetUserId': _target?['id'],
      // 서버 기본값이 kakao 라, 끌 때는 null 을 보낸다
      'requireProvider': _requireKakao ? 'kakao' : null,
      'dryRun': dryRun,
      'bundle': _bundle,
      'prompt': {
        'mode': _mode,
        if (guarded) 'deathPolicy': _deathPolicy,
        if (guarded && text(_deathAnswer) != null) 'deathAnswer': text(_deathAnswer),
      },
      'overrides': {
        for (final e in overrides.entries)
          if (e.value != null) e.key: e.value,
      },
      'voice': {'attach': voice ? _voiceAttach : 'none', 'probe': voice && _probe},
      if (text(_checkQuery) != null) 'checkQuery': text(_checkQuery),
    };
  }

  /// 미리보기와 같은 입력인지 견주는 값(묶음은 고른 차례 번호로 대신한다).
  String _signature() => jsonEncode({..._body(dryRun: true), 'bundle': _bundleStamp});

  bool get _ready =>
      _target != null && _bundle != null && _name.text.trim().isNotEmpty && _relation.text.trim().isNotEmpty;

  bool get _previewFresh => _preview != null && _previewSignature == _signature();

  bool get _canImport => _ready && _running == null && _imported == null && _previewFresh;

  Future<void> _run({required bool dryRun}) async {
    if (!_ready || _running != null) return;
    if (!dryRun) {
      final ok = await confirmAction(
        context,
        '이 계정에 가져올까요?',
        '${_target?['name'] ?? ''} 계정에 대화 상대를 만들어요. 방금 시험해 본 것과 같은 내용으로 가져와요. '
            '잘못 가져왔으면 아래 \'되돌리기\'로 지울 수 있어요.',
        action: '가져오기',
        danger: false,
      );
      if (!ok || !mounted) return;
    }
    final signature = _signature();
    setState(() {
      _running = dryRun ? 'dry' : 'import';
      if (dryRun) {
        _previewError = null;
      } else {
        _importError = null;
      }
    });
    try {
      final result = await widget.api.importPersona(_body(dryRun: dryRun));
      if (!mounted) return;
      setState(() {
        if (dryRun) {
          _preview = result;
          _previewSignature = signature;
        } else {
          _imported = result;
          final id = result['personaId'];
          if (id is String) _undoId.text = id;
        }
      });
      if (!dryRun) {
        showMessage(context, '가져왔어요. 결과를 확인해 주세요.');
        _search(); // 대화 상대 수·가져온 대화 상대 목록을 새로 본다
      }
    } catch (error) {
      if (!mounted) return;
      setState(() {
        if (dryRun) {
          _preview = null;
          _previewSignature = null;
          _previewError = error;
        } else {
          _importError = error;
        }
      });
    } finally {
      if (mounted) setState(() => _running = null);
    }
  }

  Future<void> _undo() async {
    final personaId = _undoId.text.trim();
    if (personaId.isEmpty || _running != null) return;
    final ok = await confirmAction(
      context,
      '가져온 대화 상대를 지울까요?',
      '이 대화 상대와 그 기억·대화를 모두 지워요. 되돌릴 수 없어요. '
          '가져오기로 만든 대화 상대만 지워지고, 목소리 모델(Fish)은 지우지 않아요.',
      action: '되돌리기',
    );
    if (!ok || !mounted) return;
    setState(() => _running = 'undo');
    try {
      await widget.api.removeImport(personaId);
      if (!mounted) return;
      setState(() {
        if (_imported?['personaId'] == personaId) _imported = null;
        _undoId.clear();
      });
      showMessage(context, '되돌렸어요. 가져온 대화 상대를 지웠어요.');
      _search(); // 대화 상대 수를 새로 본다
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _running = null);
    }
  }

  Future<void> _playProbe(String audio) async {
    final player = _player ??= AudioPlayer();
    try {
      await player.setUrl(audio.startsWith('data:') ? audio : 'data:audio/mpeg;base64,$audio');
      await player.play();
    } catch (error) {
      if (mounted) showMessage(context, '소리를 틀지 못했어요. $error');
    }
  }

  // ───────────────────────── 화면 ─────────────────────────

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.sizeOf(context).width;
    final side = width < 600 ? 16.0 : 28.0;
    return Scaffold(
      backgroundColor: AppColors.paper,
      body: SafeArea(
        child: Column(
          children: [
            if (_running != null) const LinearProgressIndicator(minHeight: 2) else const SizedBox(height: 2),
            Expanded(
              child: SingleChildScrollView(
                padding: EdgeInsets.fromLTRB(side, width < 600 ? 16 : 28, side, 48),
                child: Align(
                  alignment: Alignment.topCenter,
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 880),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        _header(),
                        const SizedBox(height: 20),
                        _accountStep(),
                        const SizedBox(height: 14),
                        _bundleStep(),
                        const SizedBox(height: 14),
                        _infoStep(),
                        const SizedBox(height: 14),
                        _promptStep(),
                        const SizedBox(height: 14),
                        _voiceStep(),
                        const SizedBox(height: 14),
                        _runStep(),
                        const SizedBox(height: 14),
                        _undoStep(),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _header() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('관리자 화면 · 페르소나 가져오기', style: AppText.title),
        const SizedBox(height: 4),
        const Text('관리자만 볼 수 있어요 · 예전 자료 묶음(.json)으로 고른 계정의 대화 상대를 만들어요', style: AppText.small),
        const SizedBox(height: 14),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            _SmallButton(icon: Icons.forum_outlined, label: '피드백 보기', onPressed: () => context.go('/admin')),
            _SmallButton(
              icon: Icons.bar_chart_rounded,
              label: '사용 통계',
              onPressed: () => context.go('/admin/stats'),
            ),
          ],
        ),
        const SizedBox(height: 14),
        const Notice(
          '묶음 파일은 가족 자료예요. 이 화면은 지침·기억 본문을 그리지 않고 건수와 검사만 보여 줘요. '
          '묶음 폴더(TalkTo\\페르소나 가져오기)는 OneDrive 로 동기화되니, 가져오기가 끝나면 묶음 파일과 '
          '다른 곳에 남은 사본을 지워 주세요.',
          icon: Icons.lock_outline_rounded,
        ),
      ],
    );
  }

  // ① 계정 고르기
  Widget _accountStep() {
    final users = _users;
    final target = _target;
    return _Step(
      number: 1,
      title: '계정 고르기',
      note: '카카오로 이은 계정을 고르세요. 이메일이 안 보이면 이름·가입일로 고릅니다.',
      children: [
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: _query,
                textInputAction: TextInputAction.search,
                onSubmitted: (_) => _search(),
                decoration: const InputDecoration(hintText: '이름 · 이메일 (비우면 최근 가입 순)', isDense: true),
              ),
            ),
            const SizedBox(width: 8),
            _SmallButton(
              icon: Icons.search_rounded,
              label: _searching ? '찾는 중…' : '찾기',
              onPressed: _searching ? null : _search,
            ),
          ],
        ),
        const SizedBox(height: 6),
        CheckboxListTile(
          value: _requireKakao,
          onChanged: (v) => setState(() => _requireKakao = v ?? true),
          contentPadding: EdgeInsets.zero,
          controlAffinity: ListTileControlAffinity.leading,
          dense: true,
          title: const Text('카카오로 이은 계정만 받기 (권장 · 관리자 계정을 잘못 고르는 것을 막아요)', style: TextStyle(fontSize: 13.5)),
        ),
        if (_searchError != null) ...[const SizedBox(height: 8), ErrorBox('$_searchError')],
        if (users != null) ...[
          const SizedBox(height: 8),
          if (users.isEmpty)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 8),
              child: Text('찾은 계정이 없어요.', style: AppText.small),
            )
          else
            for (final user in users)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _UserRow(
                  user: user,
                  selected: target?['id'] == user['id'],
                  onTap: () => setState(() => _target = user),
                ),
              ),
        ],
        if (target != null && _requireKakao && !_providers(target).contains('kakao')) ...[
          const SizedBox(height: 4),
          const Notice(
            '고른 계정에 카카오 로그인이 없어요. 서버가 받지 않아요(카카오로 이은 계정만 받기를 켠 경우).',
            icon: Icons.warning_amber_rounded,
            color: AppColors.danger,
            background: AppColors.dangerSoft,
          ),
        ],
      ],
    );
  }

  // ② 묶음 파일
  Widget _bundleStep() {
    final bundle = _bundle;
    return _Step(
      number: 2,
      title: '묶음 파일',
      note: '가져오기 도구가 만든 .json · 레포 밖(TalkTo\\페르소나 가져오기)에 두고, 가져온 뒤 지워 주세요',
      children: [
        Align(
          alignment: Alignment.centerLeft,
          child: _SmallButton(
            icon: Icons.upload_file_rounded,
            label: bundle == null ? '묶음 파일 고르기 (.json)' : '다른 묶음 파일 고르기',
            onPressed: _running == null ? _pickBundle : null,
          ),
        ),
        if (_bundleError != null) ...[const SizedBox(height: 10), ErrorBox(_bundleError!)],
        if (bundle != null) ...[
          const SizedBox(height: 12),
          BundleSummary(bundle: bundle, fileName: _bundleName ?? '', size: _bundleSize),
        ],
      ],
    );
  }

  // ③ 대화 상대 정보
  Widget _infoStep() {
    return _Step(
      number: 3,
      title: '대화 상대 정보',
      note: '묶음 값으로 채워져요 · 고쳐도 묶음을 다시 만들 필요 없어요',
      children: [
        _FieldGrid(children: [
          _field(_name, '그분 성함 (필수)', 40),
          _field(_relation, '관계 (필수) · 예: 할머니, 외할머니', 40),
          _field(_addressTerm, '부르던 호칭 · 예: 할머니', 30),
        ]),
        const SizedBox(height: 16),
        const Text('지금 말하는 사람 (주인)', style: AppText.label),
        const SizedBox(height: 4),
        const Text(
          '연결 문단에 "지금 대화하는 사람은 아래 가족 목록의 ○○" 한 줄로 들어가요. 비우면 예전 지침의 "누구냐" '
          '흐름을 그대로 둬요.',
          style: AppText.small,
        ),
        const SizedBox(height: 8),
        const Notice(
          '주인 이름은 예전 지침의 가족 목록에 적힌 짧은 이름과 글자까지 똑같이 적어 주세요. '
          '목록에 없는 이름이면 그분이 모르는 사람으로 대해요.',
          icon: Icons.warning_amber_rounded,
        ),
        const SizedBox(height: 10),
        _FieldGrid(children: [
          _field(_myName, '주인 이름 · 예전 가족 목록 속 짧은 이름', 40),
          _field(_myRelation, '그분과의 관계 · 예: 손자, 손녀', 40),
          _field(_calledMe, '그분이 부르던 말 · 예: 우리 강아지', 30),
        ]),
        const SizedBox(height: 8),
        const Text(
          '함께 쓰는 가족은 초대를 받을 때 이름을 적어요. 예전 가족 목록 속 이름과 같으면 더 잘 알아봐요.',
          style: AppText.small,
        ),
      ],
    );
  }

  /// [max]: 서버 ImportOverrides 의 글자 수 상한.
  Widget _field(TextEditingController controller, String label, int max) {
    return TextField(
      controller: controller,
      maxLength: max,
      decoration: InputDecoration(labelText: label, isDense: true, counterText: ''),
    );
  }

  // ④ 지침 방식
  Widget _promptStep() {
    final guarded = _mode == 'guarded';
    return _Step(
      number: 4,
      title: '지침 방식',
      note: '예전 지침 본문은 어느 쪽이든 한 글자도 바꾸지 않아요',
      children: [
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            _Choice(
              label: '안전 규칙 덧붙임 (guarded · 권장)',
              selected: guarded,
              onTap: () => setState(() => _mode = 'guarded'),
            ),
            _Choice(
              label: '예전 글 그대로 (verbatim)',
              selected: !guarded,
              onTap: () => setState(() => _mode = 'verbatim'),
            ),
          ],
        ),
        const SizedBox(height: 10),
        Text(
          guarded
              ? '공통 규칙(위기 시 109 안내·지침 비공개·마크다운 금지·사망 정책) + 짧은 연결 문단 + 예전 지침 본문.'
              : '예전 지침 본문만 넣어요. 109 안내·지침 비공개 같은 안전 규칙이 없어요.',
          style: AppText.small,
        ),
        if (guarded) ...[
          const SizedBox(height: 16),
          const Text('사망·임종 이야기 (사망 정책)', style: AppText.label),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final p in _deathPolicies)
                _Choice(label: p.$2, selected: _deathPolicy == p.$1, onTap: () => setState(() => _deathPolicy = p.$1)),
            ],
          ),
          const SizedBox(height: 8),
          Text(_deathPolicies.firstWhere((p) => p.$1 == _deathPolicy).$3, style: AppText.small),
          const SizedBox(height: 12),
          TextField(
            controller: _deathAnswer,
            maxLength: 80,
            decoration: const InputDecoration(
              labelText: '물으면 할 한마디 (선택)',
              hintText: '비우면 넣지 않아요(지어내지 않아요)',
              isDense: true,
            ),
          ),
        ],
      ],
    );
  }

  // ⑤ 목소리
  Widget _voiceStep() {
    final voice = _hasVoice(_bundle);
    return _Step(
      number: 5,
      title: '목소리',
      note: '관리자가 대신 확인하지 않아요 · 주인이 앱에서 확인하면 그때 붙어요',
      children: [
        if (_bundle != null && !voice)
          const Text('묶음에 목소리가 없어요. 글 대화만 해요.', style: AppText.small)
        else ...[
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _Choice(
                label: '주인 확인 뒤 붙이기',
                selected: _voiceAttach == 'after_owner_pledge',
                onTap: voice ? () => setState(() => _voiceAttach = 'after_owner_pledge') : null,
              ),
              _Choice(
                label: '붙이지 않기',
                selected: _voiceAttach == 'none',
                onTap: voice ? () => setState(() => _voiceAttach = 'none') : null,
              ),
            ],
          ),
          const SizedBox(height: 6),
          CheckboxListTile(
            value: voice && _probe,
            onChanged: voice ? (v) => setState(() => _probe = v ?? false) : null,
            contentPadding: EdgeInsets.zero,
            controlAffinity: ListTileControlAffinity.leading,
            dense: true,
            title: const Text(
              '시험해 볼 때 한 문장 미리 들어보기 (운영 Fish 계정에서 이 목소리를 쓸 수 있는지 확인)',
              style: TextStyle(fontSize: 13.5),
            ),
          ),
        ],
      ],
    );
  }

  // ⑥ 시험해 보기 → 가져오기
  Widget _runStep() {
    final preview = _preview;
    final stale = preview != null && !_previewFresh && _imported == null;
    return _Step(
      number: 6,
      title: '시험해 보기 → 가져오기',
      note: '시험해 보기는 아무것도 쓰지 않아요(임베딩·목소리 확인만)',
      children: [
        if (!_ready)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Text(
              '먼저 채워 주세요: ${[
                if (_target == null) '계정',
                if (_bundle == null) '묶음 파일',
                if (_name.text.trim().isEmpty || _relation.text.trim().isEmpty) '성함·관계',
              ].join(' · ')}',
              style: AppText.small,
            ),
          ),
        TextField(
          controller: _checkQuery,
          maxLength: 200,
          decoration: const InputDecoration(
            labelText: '시험 질문 (기억이 찾아지는지 봐요)',
            hintText: "비우면 '고향 이야기 해 주세요'",
            isDense: true,
            counterText: '',
          ),
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            _SmallButton(
              icon: Icons.science_outlined,
              label: _running == 'dry' ? '시험하는 중…' : '시험해 보기 (쓰지 않음)',
              onPressed: _ready && _running == null ? () => _run(dryRun: true) : null,
            ),
            FilledButton.icon(
              onPressed: _canImport ? () => _run(dryRun: false) : null,
              icon: const Icon(Icons.download_done_rounded, size: 18),
              label: Text(_running == 'import' ? '가져오는 중…' : '가져오기'),
              style: FilledButton.styleFrom(
                minimumSize: const Size(0, 38),
                padding: const EdgeInsets.symmetric(horizontal: 16),
                textStyle: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700, fontFamilyFallback: koreanFallback),
              ),
            ),
          ],
        ),
        if (stale) ...[
          const SizedBox(height: 10),
          const Notice('시험해 본 뒤 입력이 바뀌었어요. 다시 시험해 본 뒤에 가져올 수 있어요.', icon: Icons.refresh_rounded),
        ],
        if (_previewError != null) ...[const SizedBox(height: 10), ErrorBox('$_previewError')],
        if (preview != null) ...[
          const SizedBox(height: 14),
          const Text('미리보기', style: AppText.label),
          const SizedBox(height: 8),
          ImportResultView(result: preview, onPlay: _playProbe),
        ],
        if (_importError != null) ...[const SizedBox(height: 10), ErrorBox('$_importError')],
        if (_imported != null) ...[
          const SizedBox(height: 16),
          const Text('가져온 결과', style: AppText.label),
          const SizedBox(height: 8),
          ImportResultView(result: _imported!, onPlay: _playProbe),
        ],
      ],
    );
  }

  // ⑦ 되돌리기
  Widget _undoStep() {
    final id = _undoId.text.trim();
    final imported = _importedOf(_target);
    return _Step(
      number: 7,
      title: '되돌리기',
      note: '가져오기로 만든 대화 상대만 지울 수 있어요',
      children: [
        // 서버 GET /admin/users 의 importedPersonas: 나중에 이 화면을 다시 열어도 고른 계정에서 id 를 찾는다
        if (imported.isNotEmpty) ...[
          const Text('고른 계정에 가져온 대화 상대 · 누르면 아래 칸에 id 를 넣어요', style: AppText.small),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final p in imported)
                _Choice(
                  label: [p['name'] ?? '(이름 없음)', _date(p['createdAt'])].where((s) => '$s'.isNotEmpty).join(' · '),
                  selected: id == p['id'],
                  onTap: _running == null ? () => _undoId.text = p['id'] as String : null,
                ),
            ],
          ),
          const SizedBox(height: 12),
        ],
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: TextField(
                controller: _undoId,
                decoration: const InputDecoration(labelText: '대화 상대 id (가져온 뒤 저절로 채워져요)', isDense: true),
              ),
            ),
            const SizedBox(width: 8),
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: _SmallButton(
                icon: Icons.undo_rounded,
                label: _running == 'undo' ? '지우는 중…' : '되돌리기',
                danger: true,
                onPressed: id.isNotEmpty && _running == null ? _undo : null,
              ),
            ),
          ],
        ),
        if (id.isNotEmpty) ...[
          const SizedBox(height: 8),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: () {
                Clipboard.setData(ClipboardData(text: id));
                showMessage(context, '대화 상대 id 를 복사했어요.');
              },
              icon: const Icon(Icons.copy_rounded, size: 16),
              label: const Text('id 복사'),
            ),
          ),
        ],
      ],
    );
  }
}

const _deathPolicies = [
  ('deny', '말하지 않음', '직접 물어도 사인·임종을 말하지 않아요.'),
  ('brief', '짧게 넘김', '직접 물으면 한두 마디로 받고 넘어가요.'),
  ('ask_only', '물으면만 (기본)', '먼저 꺼내지 않고, 물으면 담담하게 짧게 말해요. 2026-09 설문에서 고른 값이에요.'),
  ('open', '자유', '먼저 꺼내도 돼요(의료 세부 금지는 공통 규칙대로).'),
];

// ───────────────────────── 계정 한 줄 ─────────────────────────

class _UserRow extends StatelessWidget {
  final Map<String, dynamic> user;
  final bool selected;
  final VoidCallback onTap;

  const _UserRow({required this.user, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final providers = _providers(user);
    final kakao = providers.contains('kakao');
    final email = _maskedEmail(user);
    final count = user['personaCount'];
    final imported = _importedOf(user).length;
    final role = '${user['role'] ?? ''}'.toUpperCase();
    final status = '${user['status'] ?? ''}'.toUpperCase();
    final details = [
      if (_date(user['createdAt']).isNotEmpty) '가입 ${_date(user['createdAt'])}',
      email,
      if (count is num) '대화 상대 ${count.toInt()}',
      if (imported > 0) '가져온 대화 상대 $imported',
    ].join(' · ');
    return Opacity(
      // 카카오로 잇지 않은 계정(관리자·이메일 가입)은 흐리게
      opacity: kakao ? 1 : 0.55,
      child: AppCard(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
        color: selected ? AppColors.primarySoft : AppColors.card,
        borderColor: selected ? AppColors.primary : AppColors.line,
        onTap: onTap,
        child: Row(
          children: [
            Icon(
              selected ? Icons.radio_button_checked_rounded : Icons.radio_button_unchecked_rounded,
              size: 20,
              color: selected ? AppColors.primary : AppColors.muted,
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Wrap(
                    spacing: 6,
                    runSpacing: 4,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      Text(
                        '${user['name'] ?? '(이름 없음)'}',
                        style: const TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: AppColors.ink),
                      ),
                      for (final p in providers) _Badge(_providerLabels[p] ?? p, strong: p == 'kakao'),
                      if (role == 'ADMIN') const _Badge('관리자', danger: true),
                      if (status.isNotEmpty && status != 'ACTIVE') _Badge(status, danger: true),
                    ],
                  ),
                  const SizedBox(height: 3),
                  Text(details, style: AppText.small.copyWith(fontSize: 12.5)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

const _providerLabels = {'kakao': '카카오', 'google': '구글', 'email': '이메일'};

List<String> _providers(Map<String, dynamic> user) {
  final list = user['providers'];
  final out = <String>[
    if (list is List)
      for (final p in list)
        if (p is String && p.isNotEmpty) p.toLowerCase(),
  ];
  final first = user['provider'];
  if (out.isEmpty && first is String && first.isNotEmpty) out.add(first.toLowerCase());
  return out.isEmpty ? const ['email'] : out;
}

/// 이 계정에 가져온 대화 상대(서버 importedPersonas [{id, name, createdAt}]). 예전 서버면 빈 목록.
List<Map<String, dynamic>> _importedOf(Map<String, dynamic>? user) {
  final list = user?['importedPersonas'];
  return [
    if (list is List)
      for (final p in list)
        if (p is Map && p['id'] is String && (p['id'] as String).isNotEmpty) Map<String, dynamic>.from(p),
  ];
}

/// 서버가 가린 이메일(emailMasked)을 쓰고, 없으면 여기서 가린다: alex@gmail.com → a***@gmail.com
String _maskedEmail(Map<String, dynamic> user) {
  final masked = user['emailMasked'];
  if (masked is String && masked.isNotEmpty) return masked;
  final email = user['email'];
  if (email is! String || !email.contains('@')) return '이메일 없음';
  final at = email.indexOf('@');
  return '${email.isEmpty ? '' : email[0]}***${email.substring(at)}';
}

// ───────────────────────── 묶음 요약 ─────────────────────────

/// 묶음 파일 요약: 건수·글자 수·sha 만(본문은 그리지 않는다).
class BundleSummary extends StatelessWidget {
  final Map<String, dynamic> bundle;
  final String fileName;
  final int size;

  const BundleSummary({super.key, required this.bundle, required this.fileName, required this.size});

  @override
  Widget build(BuildContext context) {
    final memories = bundle['memories'] is List ? bundle['memories'] as List : const [];
    final sensitive = memories.where((m) => m is Map && m['sensitive'] == true).length;
    // 서버 SENSITIVE_CATEGORIES(신앙·종교 → religion, 건강 → health)와 같게, 앞뒤 빈칸은 떼고 견준다
    final religionHealth = memories
        .where((m) =>
            m is Map && const {'신앙', '종교', '건강', 'religion', 'health'}.contains('${m['category'] ?? ''}'.trim()))
        .length;
    final prompt = _asMap(bundle['prompt']);
    final legacy = prompt['legacy'] ?? prompt['verbatim'] ?? bundle['verbatim'];
    final source = _asMap(bundle['source']);
    final sha = '${source['promptSha256'] ?? prompt['legacySha256'] ?? ''}';
    final persona = _asMap(bundle['persona']);
    final format = '${bundle['format'] ?? ''}';
    final status = '${persona['status'] ?? ''}';
    final rows = <(String, String)>[
      ('파일', '$fileName · ${_kb(size)}'),
      ('형식', '${format.isEmpty ? '(없음)' : format} · version ${bundle['version'] ?? '—'}'),
      ('기억', '${memories.length}건 · 민감(물으면만) $sensitive건 · 종교·건강 범주 $religionHealth건'),
      if (legacy is String) ('예전 지침', '${_fmt(legacy.length)}자${sha.isEmpty ? '' : ' · sha ${_short(sha)}'}'),
      if (prompt['rules'] is String) ('공통 규칙 · 연결 문단', '${_fmt((prompt['rules'] as String).length)}자 · 연결 문단 ${prompt['bridge'] is String ? '있음' : '없음'}'),
      if (prompt['deathRules'] is Map) ('사망 정책 문장', (prompt['deathRules'] as Map).keys.join(', ')),
      if (bundle['probeEmbeddings'] is List) ('확인용 옛 벡터', '${(bundle['probeEmbeddings'] as List).length}건'),
      ('그분 상태', status.isEmpty ? '(없음)' : status),
      ('목소리', _PersonaImportPageState._hasVoice(bundle) ? '있음 (주인 확인 뒤 붙임)' : '없음'),
      if (source['builtAt'] != null) ('만든 때', _dateTime(source['builtAt'])),
    ];
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(color: AppColors.paper, borderRadius: BorderRadius.circular(14)),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final row in rows) _KeyValue(row.$1, row.$2),
          if (format != _bundleFormat)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Notice('형식(format)이 이 화면이 아는 값이 아니에요. 서버가 받지 않을 수 있어요.', icon: Icons.warning_amber_rounded),
            ),
          if (status.isNotEmpty && status != 'deceased')
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Notice('그분 상태가 deceased 가 아니에요. 가져오기는 돌아가신 분만 받아요.', icon: Icons.warning_amber_rounded),
            ),
          if (religionHealth > 0)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Notice(
                '종교·건강 범주 기억 $religionHealth건이 켜진 채로 들어가요. 주인이 앱의 대화 화면 \'더 보기\' → '
                '\'종교·건강 이야기 동의\'에서 그 범주 동의를 거두면 꺼지고, 다시 동의하면 켜져요. '
                '예전 지침 기본 정보 속 문장은 그대로 남아요.',
              ),
            ),
        ],
      ),
    );
  }
}

// ───────────────────────── 미리보기·결과 ─────────────────────────

/// 서버 미리보기·가져오기 결과를 그대로 그린다. 서버가 칸 이름을 조금 바꿔도 그리도록,
/// 아는 칸은 한국어 이름표를 붙이고 모르는 칸은 이름 그대로 보인다.
/// 긴 글(160자 넘음)과 소리(base64)는 화면에 그리지 않는다(가족 자료가 새어 보이지 않게).
class ImportResultView extends StatelessWidget {
  final Map<String, dynamic> result;
  final void Function(String audio)? onPlay;

  const ImportResultView({super.key, required this.result, this.onPlay});

  @override
  Widget build(BuildContext context) {
    final warnings = result['warnings'];
    final audio = _findAudio(result);
    final rest = {
      for (final e in result.entries)
        if (e.key != 'warnings') e.key: e.value,
    };
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(
        color: AppColors.card,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: AppColors.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (warnings is List && warnings.isNotEmpty) ...[
            for (final w in warnings)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Notice(_warningText(w), icon: Icons.warning_amber_rounded),
              ),
            const SizedBox(height: 4),
          ],
          _KeyChecks(result),
          const SizedBox(height: 8),
          if (audio != null && onPlay != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Align(
                alignment: Alignment.centerLeft,
                child: _SmallButton(
                  icon: Icons.play_arrow_rounded,
                  label: '미리 들어보기',
                  onPressed: () => onPlay!(audio),
                ),
              ),
            ),
          ..._entries(rest, 0),
        ],
      ),
    );
  }

  static String _warningText(Object? w) {
    if (w is Map) return '${w['message'] ?? w['text'] ?? w['code'] ?? w}';
    return '$w';
  }

  List<Widget> _entries(Map<String, dynamic> map, int depth) {
    final out = <Widget>[];
    for (final e in map.entries) {
      final value = e.value;
      final label = _labels[e.key] ?? e.key;
      if (value is Map) {
        out.add(Padding(
          padding: EdgeInsets.only(top: 8, left: depth * 12.0, bottom: 2),
          child: Text(label, style: AppText.label.copyWith(fontSize: 13.5)),
        ));
        out.addAll(_entries(Map<String, dynamic>.from(value), depth + 1));
      } else if (value is List && value.any((v) => v is Map)) {
        out.add(Padding(
          padding: EdgeInsets.only(top: 8, left: depth * 12.0, bottom: 2),
          child: Text('$label (${value.length})', style: AppText.label.copyWith(fontSize: 13.5)),
        ));
        for (final item in value) {
          out.add(Padding(
            padding: EdgeInsets.only(left: (depth + 1) * 12.0, bottom: 2),
            child: Text(
              item is Map
                  ? item.entries.map((x) => '${_labels[x.key] ?? x.key}: ${_scalar('${x.key}', x.value)}').join(' · ')
                  : _scalar(e.key, item),
              style: const TextStyle(fontSize: 13, height: 1.45, color: AppColors.ink2),
            ),
          ));
        }
      } else {
        out.add(Padding(
          padding: EdgeInsets.only(left: depth * 12.0),
          child: _KeyValue(label, _scalar(e.key, value)),
        ));
      }
    }
    return out;
  }
}

/// 핵심 확인(plan.md §7): 서버 미리보기 숫자에서 바로 읽는다. 값이 없는 줄은 빼고 보인다.
/// 초록 = 기준에 맞음, 빨강 = 확인이 필요함, 회색 = 참고.
class _KeyChecks extends StatelessWidget {
  final Map<String, dynamic> result;

  const _KeyChecks(this.result);

  @override
  Widget build(BuildContext context) {
    final memories = _asMap(result['memories']);
    final embedding = _asMap(result['embedding']);
    final prompt = _asMap(result['prompt']);
    final check = _asMap(result['check']);
    final probe = _asMap(_asMap(result['voice'])['probe']);
    final total = memories['total'];
    final ready = memories['ready'];
    final dims = embedding['dimensions'];
    final match = embedding['mvpModelMatch'];
    final found = check['found'];
    final items = <(String, bool?)>[
      if (total is num && ready is num) ('기억 READY ${ready.toInt()} / ${total.toInt()}', total > 0 && ready == total),
      if (memories['sensitive'] is num) ('민감(물으면만) ${(memories['sensitive'] as num).toInt()}건', null),
      if (dims is num) ('임베딩 ${embedding['model'] ?? ''} · 차원 ${dims.toInt()}', true),
      if (match is num) ('MVP 모델 일치(최소 코사인) $match · 기준 0.99 (참고)', match >= 0.99 ? true : null),
      if (prompt['legacyUnchanged'] is bool) ('예전 지침 본문 그대로', prompt['legacyUnchanged'] as bool),
      if (found is num) ('시험 질문으로 찾은 기억 ${found.toInt()}건', found >= 1),
      if (probe['ok'] is bool) ('목소리 미리 듣기 ${probe['ok'] == true ? '성공 · 귀로 확인해 주세요' : '실패'}', probe['ok'] as bool),
    ];
    if (items.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final item in items)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 3),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  item.$2 == null
                      ? Icons.info_outline_rounded
                      : (item.$2! ? Icons.check_circle_rounded : Icons.cancel_rounded),
                  size: 18,
                  color: item.$2 == null ? AppColors.muted : (item.$2! ? AppColors.primary : AppColors.danger),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(item.$1, style: const TextStyle(fontSize: 13.5, height: 1.45, color: AppColors.ink)),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

/// 아는 칸 이름 → 화면 이름표(관리자 화면이라 회사 이름도 그대로 쓴다).
const _labels = {
  'ok': '성공',
  'dryRun': '시험 실행',
  'personaId': '대화 상대 id',
  'personId': '사람 id',
  'target': '대상 계정',
  'id': 'id',
  'name': '이름',
  'role': '권한',
  'status': '상태',
  'providers': '로그인',
  'provider': '로그인',
  'createdAt': '가입일',
  'emailMasked': '이메일(가림)',
  'personaCount': '대화 상대 수',
  'counts': '건수',
  'memories': '기억',
  'memoryCount': '기억 수',
  'total': '전체',
  'ready': 'READY',
  'readyCount': 'READY 수',
  'sensitive': '민감(물으면만)',
  'sensitiveCount': '민감(물으면만) 수',
  'religion': '종교 범주',
  'health': '건강 범주',
  'religionHealth': '종교·건강 범주',
  'instructions': '지침',
  'prompt': '지침',
  'mode': '방식',
  'chars': '글자 수',
  'length': '글자 수',
  'sha256': 'sha256',
  'sha': 'sha',
  'sha16': 'sha 앞 16자',
  'legacySha256': '예전 지침 sha256',
  'deathPolicy': '사망 정책',
  'deathAnswer': '물으면 할 한마디',
  'embedding': '임베딩',
  'model': '모델',
  'dimensions': '차원',
  'queryDimensions': '질문 벡터 차원',
  'mvpModelMatch': 'MVP 모델 일치(최소 코사인)',
  'voice': '목소리',
  'attach': '붙이기',
  'probe': '미리 들어보기 결과',
  'probeOk': '미리 듣기 성공',
  'voiceId': '목소리 id',
  'pending': '주인 확인 기다림',
  'testQuery': '시험 질문',
  'retrieved': '찾은 기억 수',
  'hits': '찾은 기억 수',
  'bundleSha256': '묶음 sha256',
  'basics': '기본 정보',
  'relation': '관계',
  'addressTerm': '호칭',
  'myName': '주인 이름',
  'myRelation': '그분과의 관계',
  'calledMe': '그분이 부르던 말',
  'deathAnswerSet': '한마디 넣음',
  'legacyChars': '예전 지침 글자 수',
  'legacyUnchanged': '예전 지침이 그대로 끝에 있음',
  'categories': '분류별 건수',
  'mvpDimensions': 'MVP 옛 벡터 차원',
  'hasVoiceId': '묶음에 목소리',
  'contentType': '소리 형식',
  'code': '오류 코드',
  'check': '시험 질문',
  'query': '질문',
  'found': '찾은 기억 수',
  'deleted': '지움',
};

const _audioKeys = ['probeAudio', 'probeMp3', 'audioBase64', 'probeAudioBase64', 'audio', 'mp3'];

/// 미리 듣기 소리(base64 mp3 또는 data: 주소)를 찾는다. 서버는 voice.probe.audio 에 싣는다.
String? _findAudio(Map<String, dynamic> result) {
  final voice = _asMap(result['voice']);
  for (final map in [_asMap(voice['probe']), voice, result]) {
    for (final key in _audioKeys) {
      final value = map[key];
      if (value is String && value.length > 32) return value;
    }
  }
  return null;
}

bool _looksLikeAudio(String key) {
  final k = key.toLowerCase();
  return k.contains('audio') || k.contains('mp3') || k.contains('base64');
}

/// 값 하나를 글로. 긴 글과 소리는 그리지 않는다.
String _scalar(String key, Object? value) {
  if (value == null) return '—';
  if (value is bool) return value ? '예' : '아니요';
  if (value is int) return _fmt(value);
  if (value is double) return value == value.roundToDouble() && value.abs() < 1e15 ? _fmt(value.toInt()) : value.toStringAsFixed(4);
  if (value is List) return value.isEmpty ? '없음' : value.map((v) => _scalar(key, v)).join(', ');
  final text = '$value';
  if (_looksLikeAudio(key)) return '(소리 · 화면에 그리지 않음)';
  if (text.length > 160) return '(긴 글 ${_fmt(text.length)}자 · 화면에 그리지 않음)';
  if (key == 'createdAt' || key.endsWith('At')) return _dateTime(text);
  if (key.toLowerCase().contains('sha') && text.length > 16) return _short(text);
  return text;
}

// ───────────────────────── 작은 조각 ─────────────────────────

class _Step extends StatelessWidget {
  final int number;
  final String title;
  final String? note;
  final List<Widget> children;

  const _Step({required this.number, required this.title, this.note, required this.children});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 18),
      decoration: BoxDecoration(
        color: AppColors.card,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 2,
            crossAxisAlignment: WrapCrossAlignment.end,
            children: [
              Text('$number. $title', style: AppText.heading),
              if (note != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 2),
                  child: Text(note!, style: AppText.small.copyWith(fontSize: 12)),
                ),
            ],
          ),
          const SizedBox(height: 12),
          ...children,
        ],
      ),
    );
  }
}

/// 칸 여러 개: 넓으면 두 줄로, 좁으면 한 줄씩.
class _FieldGrid extends StatelessWidget {
  final List<Widget> children;

  const _FieldGrid({required this.children});

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, constraints) {
      const gap = 12.0;
      final columns = constraints.maxWidth >= 620 ? 2 : 1;
      final width = (constraints.maxWidth - gap * (columns - 1)) / columns;
      return Wrap(
        spacing: gap,
        runSpacing: 12,
        children: [for (final child in children) SizedBox(width: width, child: child)],
      );
    });
  }
}

class _KeyValue extends StatelessWidget {
  final String label;
  final String value;

  const _KeyValue(this.label, this.value);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 150,
            child: Text(label, style: const TextStyle(fontSize: 13, height: 1.45, color: AppColors.muted)),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: SelectableText(value, style: const TextStyle(fontSize: 13.5, height: 1.45, color: AppColors.ink)),
          ),
        ],
      ),
    );
  }
}

class _Choice extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback? onTap;

  const _Choice({required this.label, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final radius = BorderRadius.circular(999);
    final enabled = onTap != null;
    return Material(
      color: selected ? (enabled ? AppColors.primary : AppColors.line2) : AppColors.card,
      shape: RoundedRectangleBorder(
        borderRadius: radius,
        side: BorderSide(color: selected ? (enabled ? AppColors.primary : AppColors.line2) : AppColors.line2),
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: radius,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 13.5,
              fontWeight: selected ? FontWeight.w700 : FontWeight.w400,
              color: selected ? Colors.white : (enabled ? AppColors.ink : AppColors.muted),
            ),
          ),
        ),
      ),
    );
  }
}

class _SmallButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback? onPressed;
  final bool danger;

  const _SmallButton({required this.icon, required this.label, required this.onPressed, this.danger = false});

  @override
  Widget build(BuildContext context) {
    return OutlinedButton.icon(
      onPressed: onPressed,
      icon: Icon(icon, size: 17),
      label: Text(label),
      style: OutlinedButton.styleFrom(
        foregroundColor: danger ? AppColors.danger : null,
        minimumSize: const Size(0, 38),
        padding: const EdgeInsets.symmetric(horizontal: 14),
        textStyle: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600, fontFamilyFallback: koreanFallback),
      ),
    );
  }
}

class _Badge extends StatelessWidget {
  final String text;
  final bool strong;
  final bool danger;

  const _Badge(this.text, {this.strong = false, this.danger = false});

  @override
  Widget build(BuildContext context) {
    final color = danger ? AppColors.danger : (strong ? AppColors.ink : AppColors.ink2);
    final background = danger ? AppColors.dangerSoft : (strong ? AppColors.kakao : AppColors.sunk);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(color: background, borderRadius: BorderRadius.circular(999)),
      child: Text(text, style: TextStyle(fontSize: 11.5, fontWeight: FontWeight.w700, color: color)),
    );
  }
}

Map<String, dynamic> _asMap(Object? value) => value is Map ? Map<String, dynamic>.from(value) : const {};

/// 1234567 → 1,234,567
String _fmt(int n) {
  final s = n.abs().toString();
  final out = StringBuffer(n < 0 ? '-' : '');
  for (var i = 0; i < s.length; i++) {
    if (i > 0 && (s.length - i) % 3 == 0) out.write(',');
    out.write(s[i]);
  }
  return out.toString();
}

String _kb(int bytes) => bytes < 1024 ? '$bytes B' : '${(bytes / 1024).toStringAsFixed(bytes < 10240 ? 1 : 0)} KB';

/// sha 는 앞 16자만(plan.md 확인 기준과 같은 길이).
String _short(String sha) => sha.length > 16 ? '${sha.substring(0, 16)}…' : sha;

String _two(int n) => n.toString().padLeft(2, '0');

/// 서버 시각 → 이 기기 시각 'YYYY-MM-DD HH:MM'. 시간대가 없으면(SQLite) UTC 로 읽는다.
String _dateTime(Object? iso) {
  final text = '${iso ?? ''}'.trim();
  if (text.isEmpty) return '';
  final hasZone = RegExp(r'(Z|[+-]\d\d:?\d\d)$').hasMatch(text);
  final parsed = DateTime.tryParse(hasZone || !text.contains('T') && !text.contains(' ') ? text : '${text}Z');
  if (parsed == null) return text;
  final t = parsed.toLocal();
  return '${t.year}-${_two(t.month)}-${_two(t.day)} ${_two(t.hour)}:${_two(t.minute)}';
}

/// 가입일처럼 날짜만.
String _date(Object? iso) {
  final full = _dateTime(iso);
  return full.length >= 10 ? full.substring(0, 10) : full;
}
