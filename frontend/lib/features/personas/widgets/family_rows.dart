import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../../../shared/widgets/ui.dart';
import '../../../shared/widgets/voice_input.dart';
import 'basics_form.dart' show selfRelation;

/// 가족 표(M2-01): 그분이 알아야 할 가족을 한 사람씩 더한다.
/// 한 줄 = {name, relation, toMe, calledBy, status(alive|deceased), note}. 이름만 꼭 받는다.
/// 맨 위의 '나' 줄은 기본 정보에서 오고 여기서는 고치지 않는다.
class FamilyRows extends StatelessWidget {
  final Map<String, dynamic> question;
  final List<Map<String, dynamic>> rows;

  /// 대화 상대 기본 정보(나 이름·관계). 없으면 '나' 줄을 그리지 않는다.
  final Map<String, dynamic>? basics;
  final ValueChanged<List<Map<String, dynamic>>> onChanged;

  /// 말로 적기(🎤). 있으면 한 사람 적기의 글 칸마다 🎤 단추를 단다.
  final Future<String> Function(VoiceClip clip)? transcribe;

  const FamilyRows({
    super.key,
    required this.question,
    required this.rows,
    required this.onChanged,
    this.basics,
    this.transcribe,
  });

  bool get _self => basics?['relation'] == selfRelation;
  int get _max => (question['max'] as int?) ?? 10;

  Future<void> _edit(BuildContext context, [int? index]) async {
    final row = await showFamilyRowEditor(
      context,
      question: question,
      self: _self,
      initial: index == null ? null : rows[index],
      taken: {
        for (var i = 0; i < rows.length; i++)
          if (i != index) '${rows[i]['name']}',
      },
      transcribe: transcribe,
    );
    if (row == null) return;
    final next = [...rows];
    if (index == null) {
      next.add(row);
    } else {
      next[index] = row;
    }
    onChanged(next);
  }

  @override
  Widget build(BuildContext context) {
    final myName = '${basics?['myName'] ?? ''}'.trim();
    final myRelation = '${basics?['myRelation'] ?? ''}'.trim();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (basics != null && !_self && myName.isNotEmpty)
          _tile(
            title: '나 · $myName',
            subtitle: '${myRelation.isEmpty ? '그분의 가족' : '그분의 $myRelation'} · 이 대화 상대를 만든 사람(나) · 기본 정보에서 고쳐요',
            color: AppColors.sunk,
          ),
        for (var i = 0; i < rows.length; i++)
          _tile(
            title: [rows[i]['name'], rows[i]['relation']].where((v) => '${v ?? ''}'.isNotEmpty).join(' · '),
            subtitle: describeFamilyRow(rows[i], self: _self),
            onTap: () => _edit(context, i),
            onRemove: () => onChanged([...rows]..removeAt(i)),
          ),
        const SizedBox(height: 4),
        Align(
          alignment: Alignment.centerLeft,
          child: OutlinedButton.icon(
            onPressed: rows.length >= _max ? null : () => _edit(context),
            icon: const Icon(Icons.person_add_alt_1_outlined, size: 19),
            label: Text(rows.length >= _max ? '최대 $_max명까지 적을 수 있어요' : '가족 더하기'),
          ),
        ),
        if (question['privacy'] != null)
          Padding(
            padding: const EdgeInsets.only(top: 10),
            child: Text(keepAll(question['privacy'] as String), style: AppText.small),
          ),
      ],
    );
  }

  Widget _tile({
    required String title,
    String subtitle = '',
    Color? color,
    VoidCallback? onTap,
    VoidCallback? onRemove,
  }) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Material(
        color: color ?? AppColors.paper,
        borderRadius: BorderRadius.circular(14),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: onTap,
          child: Padding(
            padding: EdgeInsets.fromLTRB(14, 10, onRemove == null ? 14 : 4, 10),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(keepAll(title), style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
                      if (subtitle.isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 2),
                          child: Text(keepAll(subtitle), style: AppText.small),
                        ),
                    ],
                  ),
                ),
                if (onTap != null) const Icon(Icons.edit_outlined, size: 18, color: AppColors.muted),
                if (onRemove != null)
                  IconButton(
                    tooltip: '빼기',
                    visualDensity: VisualDensity.compact,
                    onPressed: onRemove,
                    icon: const Icon(Icons.close_rounded, size: 18, color: AppColors.muted),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 가족 한 줄을 한 문장으로('나에게는 아빠 · 그분은 "철수야"라고 부르심 · 돌아가심').
String describeFamilyRow(Map<String, dynamic> row, {bool self = false}) {
  String get(String key) => '${row[key] ?? ''}'.trim();
  return [
    if (!self && get('toMe').isNotEmpty) '나에게는 ${get('toMe')}',
    if (get('calledBy').isNotEmpty) '그분은 "${get('calledBy')}"라고 부르심',
    if (get('status') == 'deceased') '돌아가심',
    if (get('note').isNotEmpty) get('note'),
  ].join(' · ');
}

/// 가족 한 사람 적기. PC 는 가운데 창, 휴대폰은 아래에서 올라오는 시트. 취소하면 null.
Future<Map<String, dynamic>?> showFamilyRowEditor(
  BuildContext context, {
  required Map<String, dynamic> question,
  required bool self,
  Map<String, dynamic>? initial,
  Set<String> taken = const {},
  Future<String> Function(VoiceClip clip)? transcribe,
}) {
  final editor = _FamilyRowEditor(
    question: question,
    self: self,
    initial: initial,
    taken: taken,
    transcribe: transcribe,
  );
  if (Breakpoints.isWide(context)) {
    return showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => Dialog(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 480),
          child: Padding(padding: const EdgeInsets.fromLTRB(24, 24, 24, 16), child: editor),
        ),
      ),
    );
  }
  return showModalBottomSheet<Map<String, dynamic>>(
    context: context,
    isScrollControlled: true,
    builder: (sheet) => Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(sheet).viewInsets.bottom),
      child: SafeArea(
        child: Padding(padding: const EdgeInsets.fromLTRB(20, 4, 20, 12), child: editor),
      ),
    ),
  );
}

class _FamilyRowEditor extends StatefulWidget {
  final Map<String, dynamic> question;
  final bool self;
  final Map<String, dynamic>? initial;
  final Set<String> taken;
  final Future<String> Function(VoiceClip clip)? transcribe;

  const _FamilyRowEditor({
    required this.question,
    required this.self,
    this.initial,
    this.taken = const {},
    this.transcribe,
  });

  @override
  State<_FamilyRowEditor> createState() => _FamilyRowEditorState();
}

class _FamilyRowEditorState extends State<_FamilyRowEditor> {
  late final Map<String, TextEditingController> _c = {
    for (final key in const ['name', 'relation', 'toMe', 'calledBy', 'note'])
      key: TextEditingController(text: '${widget.initial?[key] ?? ''}'),
  };
  late bool _deceased = widget.initial?['status'] == 'deceased';
  String? _error;

  @override
  void dispose() {
    for (final c in _c.values) {
      c.dispose();
    }
    super.dispose();
  }

  List<String> _options(String key) => [for (final o in (widget.question[key] as List? ?? const [])) '$o'];

  void _save() {
    final name = _c['name']!.text.trim();
    if (name.isEmpty) {
      setState(() => _error = '이름을 적어 주세요.');
      return;
    }
    if (widget.taken.contains(name)) {
      setState(() => _error = '이미 적은 분이에요.');
      return;
    }
    final row = <String, dynamic>{
      for (final e in _c.entries)
        if (e.value.text.trim().isNotEmpty && !(widget.self && e.key == 'toMe')) e.key: e.value.text.trim(),
      'status': _deceased ? 'deceased' : 'alive',
    };
    Navigator.pop(context, row);
  }

  Widget _field(String key, String label, String hint, int max, {List<String> chips = const [], String? help}) {
    final controller = _c[key]!;
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          FieldLabel(label, hint: help),
          if (chips.isNotEmpty) ...[
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final o in chips)
                  Pill(
                    label: o,
                    selected: controller.text.trim() == o,
                    onTap: () => setState(() => controller.text = controller.text.trim() == o ? '' : o),
                  ),
              ],
            ),
            const SizedBox(height: 8),
          ],
          VoiceField(
            controller: controller,
            transcribe: widget.transcribe,
            maxLength: max,
            onChanged: (_) => setState(() => _error = null),
            child: TextField(
              controller: controller,
              maxLength: max,
              decoration: InputDecoration(hintText: hint, counterText: ''),
              onChanged: (_) => setState(() => _error = null),
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final editing = widget.initial != null;
    return SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(editing ? '가족 고치기' : '가족 더하기', style: AppText.title),
          const SizedBox(height: 18),
          _field('name', '이름 *', '예: 홍철수', 15),
          _field(
            'relation',
            widget.self ? '나와의 관계' : '그분과의 관계',
            widget.self ? '이 사람은 나의 ○○ (예: 아들)' : '이 사람은 그분의 ○○ (예: 큰아들)',
            20,
            chips: _options('relationOptions'),
          ),
          if (!widget.self)
            _field(
              'toMe',
              '나에게는 (내가 부르는 말)',
              '예: 아빠 / 고모 / 삼촌',
              15,
              chips: _options('toMeOptions'),
              help: "대화에서 '아빠'라고 하면 이 분인 줄 알아들어요.",
            ),
          _field('calledBy', '그분이 이 사람을 부르던 말', '예: 철수야 / 애비야 / 큰애', 15, help: '모르면 비워 두셔도 돼요.'),
          const FieldLabel('살아 계신가요?'),
          ChoiceGroup(
            options: const ['살아 계심', '돌아가심'],
            value: _deceased ? '돌아가심' : '살아 계심',
            onChanged: (v) => setState(() => _deceased = v == '돌아가심'),
          ),
          const SizedBox(height: 16),
          _field('note', '한 줄 메모 (선택)', '예: 막내라 늘 걱정하셨어요', 60),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(_error!, style: const TextStyle(color: AppColors.danger, fontSize: 13.5)),
            ),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              TextButton(onPressed: () => Navigator.pop(context), child: const Text('취소')),
              const SizedBox(width: 8),
              FilledButton(onPressed: _save, child: Text(editing ? '고치기' : '더하기')),
            ],
          ),
        ],
      ),
    );
  }
}
