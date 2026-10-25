import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/file_drop.dart';
import '../../shared/widgets/ui.dart';
import '../legal/consent_checks.dart';
import '../share/roles.dart';
import 'data/persona_api.dart';
import 'imported.dart';
import 'material_guides.dart';

/// 올릴 수 있는 글 파일(서버 materials.FILE_TYPES 와 같다).
const materialTypes = {'txt', 'csv', 'eml', 'md', 'html', 'htm', 'xml', 'mbox', 'json'};

/// 파일 하나 최대 크기(서버 materials.MAX_FILE_MB).
const maxMaterialBytes = 30 * 1024 * 1024;

/// 글 자료 모음. 넣을지 말지는 사용자 선택이고, 지금은 모아 두기만 한다(기억 체계가 바뀌면 쓴다).
class MaterialsPage extends StatefulWidget {
  final String personaId;

  const MaterialsPage({super.key, required this.personaId});

  @override
  State<MaterialsPage> createState() => _MaterialsPageState();
}

class _MaterialsPageState extends State<MaterialsPage> {
  Map<String, dynamic>? _persona;
  Map<String, dynamic>? _data;
  Object? _error;

  String get _id => widget.personaId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final persona = await PersonaApi.detail(_id);
      // 함께하는 가족은 남긴 글을 보거나 더하지 않는다(서버도 막는다). 목록은 읽지 않는다
      final data = isMember(persona) ? const <String, dynamic>{'items': []} : await PersonaApi.materials(_id);
      if (!mounted) return;
      setState(() {
        _persona = persona;
        _data = data;
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  Future<void> _toggle(Map item, bool include) async {
    try {
      final data = await PersonaApi.updateMaterial(_id, item['id'], include: include);
      if (mounted) setState(() => _data = data);
    } catch (error) {
      if (mounted) showMessage(context, error);
    }
  }

  Future<void> _delete(Map item) async {
    final ok = await confirmAction(context, '이 글을 지울까요?', '"${item['title']}" 글을 지워요. 지우면 되돌릴 수 없어요.');
    if (!ok) return;
    try {
      final data = await PersonaApi.deleteMaterial(_id, item['id']);
      if (mounted) setState(() => _data = data);
    } catch (error) {
      if (mounted) showMessage(context, error);
    }
  }

  Future<void> _add() async {
    // '다 했어요'·뒤로 가기·브라우저 뒤로 가기 어느 쪽으로 돌아와도 새로 읽는다
    await context.push<bool>('/p/$_id/materials/add');
    if (mounted) _load();
  }

  @override
  Widget build(BuildContext context) {
    final persona = _persona;
    final data = _data;
    if (persona == null || data == null) {
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
    final blocked = PersonaBlocked.of(persona, personaId: _id, imported: false);
    if (blocked != null) return blocked;
    final title = personaTitle(persona);
    final items = [for (final i in data['items'] as List) Map<String, dynamic>.from(i)];
    return WizardScaffold(
      title: '남긴 글 더하기',
      headline: '${josa(title, '이', '가')} 남긴 글',
      lead: '카카오톡 대화, 문자, 메일, 블로그, 일기처럼 그분이 남긴 글을 더할 수 있어요. 꼭 올리지 않아도 괜찮아요.',
      secondary: TextButton(
        onPressed: () => context.canPop() ? context.pop() : context.go('/p/$_id'),
        child: const Text('돌아가기'),
      ),
      primary: FilledButton.icon(
        onPressed: _add,
        icon: const Icon(Icons.add_rounded),
        label: const Text('남긴 글 더하기'),
      ),
      children: [
        const Notice(
          '지금은 올린 글을 안전하게 보관만 하고 대화에는 쓰지 않아요. 대화에 쓰게 되면 미리 알려 드리고, 다시 확인을 받은 뒤에 써요.',
          icon: Icons.schedule_rounded,
          color: AppColors.primary,
          background: AppColors.primarySoft,
        ),
        const SizedBox(height: 20),
        if (items.isEmpty)
          AppCard(
            padding: const EdgeInsets.fromLTRB(20, 26, 20, 26),
            onTap: _add,
            child: Column(
              children: [
                const Icon(Icons.note_add_outlined, size: 34, color: AppColors.primary),
                const SizedBox(height: 10),
                const Text('아직 더한 글이 없어요', style: AppText.heading),
                const SizedBox(height: 4),
                Text(
                  keepAll("'남긴 글 더하기'를 누르면 카카오톡·문자 같은 곳에서 글을 가져오는 방법을 차례로 알려 드려요."),
                  textAlign: TextAlign.center,
                  style: AppText.small,
                ),
              ],
            ),
          )
        else ...[
          SectionLabel('더한 글 ${items.length}개 · 나중에 대화에 써도 되는 글 ${data['includedCount']}개'),
          AppCard(
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
            child: Column(
              children: [
                for (var i = 0; i < items.length; i++) ...[
                  if (i > 0) const Divider(height: 1, indent: 60),
                  _row(items[i]),
                ],
              ],
            ),
          ),
        ],
        const SizedBox(height: 20),
        const Notice(
          '올린 글은 다른 곳에는 쓰지 않아요. 대화 상대를 지우면 글도 함께 지워져요.',
          icon: Icons.lock_outline_rounded,
        ),
      ],
    );
  }

  Widget _row(Map<String, dynamic> item) {
    final guide = guideFor(item['kind'] as String);
    final count = (item['charCount'] as num).toInt();
    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 12, 4, 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 36,
            height: 36,
            decoration: const BoxDecoration(color: AppColors.primarySoft, shape: BoxShape.circle),
            child: Icon(guide.icon, size: 19, color: AppColors.primary),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(item['title'], overflow: TextOverflow.ellipsis, style: const TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(height: 2),
                Text(
                  '${item['kindLabel']} · ${count >= 10000 ? '${(count / 10000).toStringAsFixed(1)}만' : count}자'
                  '${item['source'] == 'text' ? ' · 붙여넣기' : ''}',
                  style: AppText.small,
                ),
                if ((item['preview'] ?? '').toString().isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Text(
                    item['preview'],
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: AppText.small.copyWith(color: AppColors.ink2),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(width: 8),
          Column(
            children: [
              Switch(value: item['include'] == true, onChanged: (v) => _toggle(item, v)),
              Text(item['include'] == true ? '나중에 써도 돼요' : '쓰지 않아요', style: AppText.small),
            ],
          ),
          IconButton(
            tooltip: '지우기',
            icon: const Icon(Icons.delete_outline_rounded, size: 20, color: AppColors.muted),
            onPressed: () => _delete(item),
          ),
        ],
      ),
    );
  }
}

/// 남긴 글 더하기: 종류 고르기 → 글 가져오는 방법 보기 → 파일 올리기 또는 붙여넣기.
class MaterialAddPage extends StatefulWidget {
  final String personaId;

  const MaterialAddPage({super.key, required this.personaId});

  @override
  State<MaterialAddPage> createState() => _MaterialAddPageState();
}

class _MaterialAddPageState extends State<MaterialAddPage> {
  MaterialGuide? _guide;
  final List<PickedFile> _files = [];
  final _text = TextEditingController();
  double? _progress;
  bool _added = false;

  /// 이 대화 상대 정보(호칭·확인 기록). 못 읽어도 올릴 수 있게, 그때는 확인을 묻는다.
  Map<String, dynamic>? _persona;

  /// 남긴 글 올리기 전 확인(material_pledge). 이미 확인 기록이 있으면 묻지 않는다.
  bool _pledged = false;

  String get _id => widget.personaId;

  String get _title => _persona == null ? '그분' : personaTitle(_persona!);

  bool get _needsPledge => (_persona?['consents'] as Map?)?['materialPledge'] != true;

  bool get _canSubmit => !_needsPledge || _pledged;

  @override
  void initState() {
    super.initState();
    PersonaApi.detail(_id).then((detail) {
      if (mounted) setState(() => _persona = detail);
    }).catchError((Object _) {
      // 못 읽어도 확인 체크를 보여 주고 올릴 수 있다
    });
  }

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  void _close() {
    if (context.canPop()) {
      context.pop(_added);
    } else {
      context.go('/p/$_id/materials');
    }
  }

  Future<void> _pick() async {
    final result = await FilePicker.pickFiles(
      allowMultiple: true,
      type: FileType.custom,
      allowedExtensions: materialTypes.toList(),
      withData: true,
    );
    if (result == null) return;
    final tooBig = [for (final f in result.files) if (f.size > maxMaterialBytes) f.name];
    if (tooBig.isNotEmpty && mounted) {
      showMessage(context, '파일 하나는 30MB까지예요: ${tooBig.join(', ')}');
    }
    _addFiles([
      for (final f in result.files)
        if (f.bytes != null && f.size <= maxMaterialBytes) (name: f.name, bytes: f.bytes!),
    ]);
  }

  void _addFiles(List<PickedFile> files) {
    final tooBig = [for (final f in files) if (f.bytes.length > maxMaterialBytes) f.name];
    if (tooBig.isNotEmpty) showMessage(context, '파일 하나는 30MB까지예요: ${tooBig.join(', ')}');
    files = [for (final f in files) if (f.bytes.length <= maxMaterialBytes) f];
    setState(() {
      for (final f in files) {
        if (_files.length < 20 && !_files.any((x) => x.name == f.name)) _files.add(f);
      }
    });
  }

  Future<void> _submit() async {
    final guide = _guide!;
    if (_files.isEmpty && _text.text.trim().isEmpty) {
      showMessage(context, '파일을 고르거나 글을 붙여넣어 주세요.');
      return;
    }
    if (!_canSubmit) {
      showMessage(context, '남긴 글을 올리기 전에 아래 확인에 체크해 주세요.');
      return;
    }
    setState(() => _progress = 0);
    try {
      await PersonaApi.addMaterials(
        _id,
        kind: guide.kind,
        files: _files,
        text: _text.text,
        pledge: _needsPledge && _pledged,
        onProgress: (p) => mounted ? setState(() => _progress = p) : null,
      );
      if (!mounted) return;
      _added = true;
      // 한 번 확인하면 서버에 남는다. 다음 글부터는 묻지 않는다.
      if (_needsPledge && _persona != null) {
        _persona = {
          ..._persona!,
          'consents': {...?(_persona!['consents'] as Map?), 'materialPledge': true},
        };
      }
      showMessage(context, '${josa(guide.title, '을', '를')} 더했어요.');
      setState(() {
        _files.clear();
        _text.clear();
        _guide = null; // 다른 종류도 더할 수 있게 처음으로
      });
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _progress = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    // 함께하는 가족은 남긴 글을 더하지 않는다(만든 사람만, 서버도 막는다)
    final blocked = _persona == null ? null : PersonaBlocked.of(_persona!, personaId: _id, imported: false);
    if (blocked != null) return blocked;
    final guide = _guide;
    if (guide == null) return _chooser();
    final busy = _progress != null;
    return FileDropArea(
      allowed: materialTypes,
      enabled: !busy,
      hint: '여기에 놓으면 파일이 담겨요',
      onFiles: _addFiles,
      onRejected: (names) => showMessage(context, '글 파일만 올릴 수 있어요: ${names.join(', ')}'),
      // 너무 큰 파일은 읽지도 않는다(아래 _addFiles 와 같은 기준)
      maxBytes: maxMaterialBytes,
      onTooBig: (names) => showMessage(context, '파일 하나는 30MB까지예요: ${names.join(', ')}'),
      child: WizardScaffold(
        title: '남긴 글 더하기',
        headline: guide.title,
        lead: guide.summary,
        secondary: TextButton.icon(
          onPressed: busy ? null : () => setState(() => _guide = null),
          icon: const Icon(Icons.arrow_back_rounded, size: 18),
          label: const Text('다른 종류'),
        ),
        primary: FilledButton(
          onPressed: busy || !_canSubmit || (_files.isEmpty && _text.text.trim().isEmpty) ? null : _submit,
          child: Text(busy ? '올리는 중… ${(_progress! * 100).round()}%' : '올리기'),
        ),
        children: [
          const SectionLabel('글 가져오는 방법'),
          AppCard(
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 4),
            child: Column(
              children: [
                for (var i = 0; i < guide.sections.length; i++) ...[
                  if (i > 0) const Divider(height: 1),
                  _section(guide.sections[i], initiallyExpanded: i == 0),
                ],
              ],
            ),
          ),
          if (guide.notes.isNotEmpty) ...[
            const SizedBox(height: 12),
            for (final n in guide.notes)
              Padding(
                padding: const EdgeInsets.only(bottom: 6, left: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('· ', style: AppText.small),
                    Expanded(child: Text(keepAll(n), style: AppText.small)),
                  ],
                ),
              ),
          ],
          const SizedBox(height: 22),
          const SectionLabel('파일 올리기'),
          OutlinedButton.icon(
            onPressed: busy ? null : _pick,
            style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(52)),
            icon: const Icon(Icons.upload_file_rounded),
            label: const Text('글 파일 고르기 (여러 개)'),
          ),
          const MultiPickHint(
            text: '위 방법대로 받은 글 파일(.txt 등)을 올려 주세요. 파일 하나는 30MB까지예요. '
                '여러 개를 한 번에 골라도 되고, 컴퓨터에서는 이 화면에 끌어다 놓아도 돼요. 압축 파일(zip)은 먼저 풀어 주세요.',
          ),
          for (var i = 0; i < _files.length; i++)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Row(
                children: [
                  const Icon(Icons.description_outlined, size: 20, color: AppColors.ink2),
                  const SizedBox(width: 8),
                  Expanded(child: Text(_files[i].name, overflow: TextOverflow.ellipsis)),
                  IconButton(
                    tooltip: '빼기',
                    icon: const Icon(Icons.close_rounded, size: 18, color: AppColors.muted),
                    onPressed: busy ? null : () => setState(() => _files.removeAt(i)),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 22),
          const SectionLabel('붙여넣기'),
          TextField(
            controller: _text,
            enabled: !busy,
            minLines: 5,
            maxLines: 12,
            onChanged: (_) => setState(() {}),
            decoration: const InputDecoration(
              hintText: '복사한 대화·문자·글을 여기에 붙여넣어 주세요.',
            ),
          ),
          if (_needsPledge) ...[
            const SizedBox(height: 22),
            const SectionLabel('올리기 전 확인'),
            AppCard(
              padding: const EdgeInsets.fromLTRB(6, 6, 6, 6),
              borderColor: _pledged ? AppColors.primary : null,
              child: ConsentCheck(
                label: '올리는 글은 제가 정당하게 가진 것이에요. 글을 쓴 분이 살아 계시면 올리기 전에 그분께 직접 허락을 받았어요. '
                    '글에 다른 분의 이름·연락처·이야기가 들어 있을 수 있다는 것도 알아요.',
                value: _pledged,
                onChanged: (v) => setState(() => _pledged = v),
                docKey: 'material_pledge',
                callName: _title,
                enabled: !busy,
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _section(GuideSection section, {required bool initiallyExpanded}) {
    return Theme(
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        initiallyExpanded: initiallyExpanded,
        tilePadding: const EdgeInsets.symmetric(horizontal: 16),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 14),
        title: Text(section.title, style: AppText.label.copyWith(fontSize: 15)),
        children: [
          for (var i = 0; i < section.steps.length; i++)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 22,
                    height: 22,
                    alignment: Alignment.center,
                    decoration: const BoxDecoration(color: AppColors.primarySoft, shape: BoxShape.circle),
                    child: Text(
                      '${i + 1}',
                      style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: AppColors.primaryDeep),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(keepAll(section.steps[i]), style: const TextStyle(fontSize: 14.5, height: 1.55)),
                  ),
                ],
              ),
            ),
          if (section.tip != null)
            Container(
              width: double.infinity,
              margin: const EdgeInsets.only(top: 2),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(color: AppColors.sunk, borderRadius: BorderRadius.circular(12)),
              child: Text(keepAll(section.tip!), style: AppText.small.copyWith(color: AppColors.ink2)),
            ),
        ],
      ),
    );
  }

  Widget _chooser() {
    final wide = Breakpoints.isWide(context);
    return WizardScaffold(
      title: '남긴 글 더하기',
      headline: '어떤 글을 더할까요?',
      lead: '고르면 글을 가져오는 방법을 차례로 알려 드려요. 한 종류씩 더하면 돼요.',
      secondary: TextButton(onPressed: _close, child: const Text('다 했어요')),
      children: [
        GridView.count(
          crossAxisCount: wide ? 3 : 2,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 12,
          crossAxisSpacing: 12,
          childAspectRatio: wide ? 1.5 : 1.15,
          children: [
            for (final g in materialGuides)
              AppCard(
                padding: const EdgeInsets.all(16),
                onTap: () => setState(() => _guide = g),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(g.icon, color: AppColors.primary),
                    const Spacer(),
                    Text(g.title, style: AppText.heading),
                  ],
                ),
              ),
          ],
        ),
      ],
    );
  }
}
