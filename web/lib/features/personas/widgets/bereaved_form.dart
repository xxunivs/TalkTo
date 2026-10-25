import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../../../shared/widgets/ui.dart';
import '../../legal/consent_checks.dart';
import '../../legal/legal_api.dart';
import '../../legal/legal_text.dart';

/// 돌아가신 분 대화 상대의 신청 자격과 유족 명단(2026-10-22 법률 자문). 서버 /intake/meta 가 주지 않을 때
/// 쓰는 보기(서버 intake 와 같다). 사망진단서 같은 서류는 받지 않는다.
const _applicantRelations = {
  'spouse': '배우자',
  'child': '자녀',
  'grandchild': '손주',
  'parent': '부모',
  'other': '그 밖의 친족·지인',
};
const _consentingRelations = {'spouse': '배우자', 'child': '자녀', 'grandchild': '손주', 'parent': '부모'};
const _bereavedRelations = {'spouse': '배우자', 'son': '아들', 'daughter': '딸', 'mother': '어머니', 'father': '아버지'};

/// 명단은 12분까지.
const bereavedMaxMembers = 12;

Map<String, String> _choices(Map<String, dynamic> meta, String key, Map<String, String> fallback) {
  final value = meta[key];
  if (value is! Map || value.isEmpty) return fallback;
  return {for (final e in value.entries) '${e.key}': '${e.value}'};
}

/// 적는 중인 신청 자격·유족 명단·확인 체크.
class BereavedDraft {
  String? applicantRelation;
  final consentingName = TextEditingController();
  String? consentingRelation;

  /// [{name, relation, contact}] — relation 은 spouse·son·daughter·mother·father.
  List<Map<String, dynamic>> members;
  bool noneLeft;

  /// 확인 내용(유족 확인 문서)에 체크했다.
  bool pledge = false;

  BereavedDraft([Map? saved])
      : applicantRelation = saved?['applicantRelation'] as String?,
        consentingRelation = saved?['consentingRelation'] as String?,
        members = [
          for (final m in saved?['members'] as List? ?? const [])
            if (m is Map) Map<String, dynamic>.from(m),
        ],
        noneLeft = saved?['noneLeft'] == true {
    consentingName.text = '${saved?['consentingName'] ?? ''}';
  }

  bool get other => applicantRelation == 'other';

  /// 서버로 보낼 것(PUT /personas/{id}/bereaved).
  Map<String, dynamic> toJson() => {
        'applicantRelation': applicantRelation,
        if (other) 'consentingName': consentingName.text.trim(),
        if (other) 'consentingRelation': consentingRelation,
        'members': noneLeft
            ? const <Map<String, dynamic>>[]
            : [
                for (final m in members)
                  {
                    'name': '${m['name'] ?? ''}'.trim(),
                    'relation': m['relation'],
                    'contact': '${m['contact'] ?? ''}'.trim().isEmpty ? null : '${m['contact']}'.trim(),
                  },
              ],
        'noneLeft': noneLeft,
        'pledge': pledge,
      };

  /// 보내기 전에 화면에서 먼저 본다(서버도 같은 글로 다시 본다). 괜찮으면 null.
  /// [needPledge] 가 false 면 확인 체크는 부르는 쪽이 따로 본다(만들기 전 확인 카드).
  String? check({bool needPledge = true}) {
    if (applicantRelation == null) return '고인과 어떤 관계인지 골라 주세요.';
    if (other && (consentingName.text.trim().isEmpty || consentingRelation == null)) {
      return '동의해 준 유족(배우자·자녀·손주·부모 가운데 한 분)의 이름과 관계를 적어 주세요.';
    }
    if (!noneLeft && members.isEmpty) return "고인의 배우자·자녀(안 계시면 부모)를 적거나 '적을 분이 없어요'를 골라 주세요.";
    if (members.length > bereavedMaxMembers && !noneLeft) return '$bereavedMaxMembers분까지 적을 수 있어요.';
    if (needPledge && !pledge) return '확인할 내용에 체크해 주세요.';
    return null;
  }

  void dispose() => consentingName.dispose();
}

/// 신청 자격(5가지) · '그 밖'이면 동의해 준 유족 · 유족 명단(이름·관계·연락처 선택) · '적을 분이 없어요' ·
/// [showPledge] 면 유족 확인 문서(pledge_deceased)와 체크. 만들기 전 확인에서는 문서와 체크를 옆 카드가 하므로 끈다.
class BereavedForm extends StatefulWidget {
  final BereavedDraft draft;

  /// 서버 /intake/meta (applicantRelations, consentingRelations, bereavedRelations).
  final Map<String, dynamic> meta;

  /// 고인을 부르는 말(할머니).
  final String title;
  final bool showPledge;
  final VoidCallback? onChanged;

  const BereavedForm({
    super.key,
    required this.draft,
    required this.meta,
    required this.title,
    this.showPledge = true,
    this.onChanged,
  });

  @override
  State<BereavedForm> createState() => _BereavedFormState();
}

class _BereavedFormState extends State<BereavedForm> {
  BereavedDraft get _d => widget.draft;
  late Future<Map<String, dynamic>> _doc = LegalApi.doc('pledge_deceased');

  void _changed(VoidCallback change) {
    setState(change);
    widget.onChanged?.call();
  }

  Future<void> _edit([int? index]) async {
    final row = await showBereavedRowEditor(
      context,
      relations: _choices(widget.meta, 'bereavedRelations', _bereavedRelations),
      initial: index == null ? null : _d.members[index],
    );
    if (row == null || !mounted) return;
    _changed(() {
      if (index == null) {
        _d.members = [..._d.members, row];
      } else {
        _d.members = [..._d.members]..[index] = row;
      }
      _d.noneLeft = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final applicants = _choices(widget.meta, 'applicantRelations', _applicantRelations);
    final consenting = _choices(widget.meta, 'consentingRelations', _consentingRelations);
    final relations = _choices(widget.meta, 'bereavedRelations', _bereavedRelations);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        AppCard(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 22),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              FieldLabel('나는 ${widget.title}의…', hint: '고인의 배우자·자녀·손주·부모, 또는 이분들 가운데 한 분의 동의를 받은 분이 신청할 수 있어요.'),
              ChoiceGroup(
                options: applicants.keys.toList(),
                value: _d.applicantRelation,
                label: (key) => applicants[key] ?? key,
                onChanged: (v) => _changed(() => _d.applicantRelation = v),
              ),
              if (_d.other) ...[
                const SizedBox(height: 20),
                const FieldLabel('동의해 준 유족', hint: '배우자·자녀·손주·부모 가운데 한 분의 이름과 관계를 적어 주세요.'),
                TextField(
                  controller: _d.consentingName,
                  maxLength: 40,
                  decoration: const InputDecoration(hintText: '이름', counterText: ''),
                  onChanged: (_) => widget.onChanged?.call(),
                ),
                const SizedBox(height: 10),
                ChoiceGroup(
                  options: consenting.keys.toList(),
                  value: _d.consentingRelation,
                  label: (key) => consenting[key] ?? key,
                  onChanged: (v) => _changed(() => _d.consentingRelation = v),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: 16),
        AppCard(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              FieldLabel(
                '${widget.title}의 배우자·자녀 (안 계시면 부모)',
                hint: '만드시는 분도 고인의 배우자나 자녀라면 명단에 함께 적어 주세요.',
              ),
              if (!_d.noneLeft)
                for (var i = 0; i < _d.members.length; i++)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Material(
                      color: AppColors.paper,
                      borderRadius: BorderRadius.circular(14),
                      child: InkWell(
                        borderRadius: BorderRadius.circular(14),
                        onTap: () => _edit(i),
                        child: Padding(
                          padding: const EdgeInsets.fromLTRB(14, 10, 4, 10),
                          child: Row(
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      keepAll([
                                        '${_d.members[i]['name'] ?? ''}',
                                        relations['${_d.members[i]['relation']}'] ?? '',
                                      ].where((v) => v.trim().isNotEmpty).join(' · ')),
                                      style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15),
                                    ),
                                    if ('${_d.members[i]['contact'] ?? ''}'.trim().isNotEmpty)
                                      Text('${_d.members[i]['contact']}', style: AppText.small),
                                  ],
                                ),
                              ),
                              const Icon(Icons.edit_outlined, size: 18, color: AppColors.muted),
                              IconButton(
                                tooltip: '빼기',
                                visualDensity: VisualDensity.compact,
                                onPressed: () => _changed(() => _d.members = [..._d.members]..removeAt(i)),
                                icon: const Icon(Icons.close_rounded, size: 18, color: AppColors.muted),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                  ),
              if (!_d.noneLeft)
                Align(
                  alignment: Alignment.centerLeft,
                  child: OutlinedButton.icon(
                    onPressed: _d.members.length >= bereavedMaxMembers ? null : () => _edit(),
                    icon: const Icon(Icons.person_add_alt_1_outlined, size: 19),
                    label: Text(_d.members.length >= bereavedMaxMembers ? '$bereavedMaxMembers분까지 적을 수 있어요' : '유족 더하기'),
                  ),
                ),
              const SizedBox(height: 6),
              ConsentCheck(
                label: '적을 분이 없어요(모두 돌아가셨어요)',
                value: _d.noneLeft,
                onChanged: (v) => _changed(() => _d.noneLeft = v),
              ),
            ],
          ),
        ),
        if (widget.showPledge) ...[
          const SizedBox(height: 16),
          FutureBuilder<Map<String, dynamic>>(
            future: _doc,
            builder: (context, snap) {
              final doc = snap.data;
              return AppCard(
                padding: const EdgeInsets.fromLTRB(20, 18, 16, 10),
                borderColor: _d.pledge ? AppColors.primary : null,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (snap.hasError) ...[
                      const ErrorBox('확인 내용을 불러오지 못했어요.'),
                      TextButton.icon(
                        onPressed: () => setState(() {
                          _doc = LegalApi.doc('pledge_deceased');
                        }),
                        icon: const Icon(Icons.refresh_rounded, size: 18),
                        label: const Text('다시 불러오기'),
                      ),
                    ] else if (doc == null)
                      const Padding(padding: EdgeInsets.all(20), child: Loading())
                    else ...[
                      Text('${doc['title'] ?? ''}', style: AppText.heading),
                      const SizedBox(height: 12),
                      LegalText(fillCallName('${doc['body'] ?? ''}', widget.title)),
                    ],
                    const Divider(height: 18),
                    ConsentCheck(
                      label: '위 내용을 모두 확인했어요.',
                      value: _d.pledge,
                      enabled: doc != null,
                      onChanged: (v) => _changed(() => _d.pledge = v),
                    ),
                  ],
                ),
              );
            },
          ),
        ],
      ],
    );
  }
}

/// 유족 한 분 적기(이름 · 관계 · 연락처 선택). PC 는 가운데 창, 휴대폰은 아래에서 올라오는 시트. 취소하면 null.
Future<Map<String, dynamic>?> showBereavedRowEditor(
  BuildContext context, {
  required Map<String, String> relations,
  Map<String, dynamic>? initial,
}) {
  final editor = _BereavedRowEditor(relations: relations, initial: initial);
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
      child: SafeArea(child: Padding(padding: const EdgeInsets.fromLTRB(20, 4, 20, 12), child: editor)),
    ),
  );
}

class _BereavedRowEditor extends StatefulWidget {
  final Map<String, String> relations;
  final Map<String, dynamic>? initial;

  const _BereavedRowEditor({required this.relations, this.initial});

  @override
  State<_BereavedRowEditor> createState() => _BereavedRowEditorState();
}

class _BereavedRowEditorState extends State<_BereavedRowEditor> {
  late final _name = TextEditingController(text: '${widget.initial?['name'] ?? ''}');
  late final _contact = TextEditingController(text: '${widget.initial?['contact'] ?? ''}');
  late String? _relation = widget.initial?['relation'] as String?;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    _contact.dispose();
    super.dispose();
  }

  void _save() {
    final name = _name.text.trim();
    if (name.isEmpty || _relation == null) {
      setState(() => _error = '이름과 관계를 적어 주세요.');
      return;
    }
    Navigator.pop(context, {
      'name': name,
      'relation': _relation,
      'contact': _contact.text.trim().isEmpty ? null : _contact.text.trim(),
    });
  }

  @override
  Widget build(BuildContext context) {
    final editing = widget.initial != null;
    return SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(editing ? '유족 고치기' : '유족 더하기', style: AppText.title),
          const SizedBox(height: 18),
          const FieldLabel('이름'),
          TextField(
            controller: _name,
            maxLength: 20,
            decoration: const InputDecoration(hintText: '예: 홍철수', counterText: ''),
            onChanged: (_) => setState(() => _error = null),
          ),
          const SizedBox(height: 16),
          const FieldLabel('고인과의 관계'),
          ChoiceGroup(
            options: widget.relations.keys.toList(),
            value: _relation,
            label: (key) => widget.relations[key] ?? key,
            onChanged: (v) => setState(() {
              _relation = v;
              _error = null;
            }),
          ),
          const SizedBox(height: 16),
          const FieldLabel(
            '연락처 (선택)',
            hint: '휴대전화나 이메일. 적으시면 TalkTo가 이 대화 상대를 만든 사실을 직접 알려 드릴 수 있어요.',
          ),
          TextField(
            controller: _contact,
            maxLength: 60,
            decoration: const InputDecoration(counterText: ''),
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(top: 8, bottom: 8),
              child: Text(_error!, style: const TextStyle(color: AppColors.danger, fontSize: 13.5)),
            ),
          const SizedBox(height: 8),
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
