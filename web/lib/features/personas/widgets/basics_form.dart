import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../../../shared/widgets/ui.dart';
import '../../../shared/widgets/voice_input.dart';
import '../../legal/consent_checks.dart';
import '../../legal/legal_api.dart';

/// 관계 '나 자신': 내 목소리와 이야기로 나를 닮은 대화 상대를 만든다(살아 있는 본인).
const selfRelation = '나 자신';

/// 만 19세 미만 대화 상대(2026-10-22 법률 자문): 서버 /intake/meta 가 주지 않을 때 쓰는 값(서버 intake 와 같다).
/// 그분이 나에게(relation) · 나는 그분의(myRelation) — 아이의 대화 상대는 그 아이의 부모만 만든다.
const _minorRelations = ['아들', '딸'];
const _minorMyRelations = ['어머니', '아버지'];
const _guardianBases = {'both_agreed': '다른 쪽 부모님의 동의를 받았어요', 'sole_custody': '제가 혼자 친권을 가지고 있어요'};

/// 법정대리인 확인 체크 글(서버 legal.GUARDIAN_LABELS.attest). 서버 글을 읽지 못했을 때만 쓴다.
const attestFallback = '저는 이 아이의 법정대리인(부모)이에요. 다른 쪽 부모님의 동의를 받았거나 제가 혼자 친권을 가지고 있어요.';

/// 예전에 고를 수 있었던 '대화가 어려우신 분'(unable). 새로 고를 수 없고, 이미 만든 대화 상대는 바꿀 수 없다.
const _unableLine = '대화가 어려우신 분(예전에 고른 상태) — 지금은 새 녹음 처리와 목소리 다시 만들기를 멈췄어요.';

/// 만 14세 미만을 아직 받지 않을 때(확인 문자를 보낼 수 없을 때) 만들기 전 안내.
const under14ClosedText = '만 14세 미만 아이의 대화 상대는 아직 만들 수 없어요(법정대리인 확인 문자를 준비하고 있어요).';

/// 'YYYY-MM' 을 [today] 의 만 나이로. 태어난 달의 다음 달 1일부터 센다(서버 ages.age_on 과 같이 보수적으로
/// 늦게 본다). 형식이 틀리면 null.
int? minorAge(String? ym, [DateTime? today]) {
  final match = RegExp(r'^(\d{4})-(0[1-9]|1[0-2])$').firstMatch(ym ?? '');
  if (match == null) return null;
  final year = int.parse(match.group(1)!);
  final month = int.parse(match.group(2)!);
  final born = month == 12 ? DateTime(year + 1, 1) : DateTime(year, month + 1);
  final now = today ?? DateTime.now();
  return now.year - born.year - (now.month < born.month ? 1 : 0);
}

/// 서버 /intake/meta 의 만 19세 미만 관계(없으면 앞의 기본값).
({List<String> relation, List<String> myRelation}) minorRelationsOf(Map<String, dynamic> meta) {
  final m = meta['minorRelations'];
  List<String> read(String key, List<String> fallback) {
    final list = m is Map ? m[key] : null;
    return list is List && list.isNotEmpty ? [for (final v in list) '$v'] : fallback;
  }

  return (relation: read('relation', _minorRelations), myRelation: read('myRelation', _minorMyRelations));
}

/// 서버 /intake/meta 의 다른 쪽 부모님 보기(없으면 앞의 기본값).
Map<String, String> guardianBasesOf(Map<String, dynamic> meta) {
  final bases = meta['guardianBases'];
  if (bases is! Map || bases.isEmpty) return _guardianBases;
  return {for (final e in bases.entries) '${e.key}': '${e.value}'};
}

/// 기본 정보 칸의 값. 새로 만들 때는 비어 있고, 고칠 때는 저장된 값으로 채운다.
class BasicsDraft {
  final name = TextEditingController();
  final relationOther = TextEditingController();
  final addressTerm = TextEditingController();
  final myName = TextEditingController();
  final calledMe = TextEditingController();
  String? relation;
  String? status;
  String? myRelation;

  /// 그분이 만 19세 미만인가(살아 계신 분·나 자신 아님만 묻는다). 아직 고르지 않았으면 null.
  bool? minor;

  /// 태어난 해와 달 'YYYY-MM'(만 19세 미만일 때만, 나이를 세는 데만 쓴다).
  String? birthYm;

  /// 다른 쪽 부모님: both_agreed(동의를 받았어요) · sole_custody(혼자 친권).
  String? guardianBasis;

  /// 아이의 법정대리인(부모)인지 확인했다. 만들 때만 보내고 기본 정보에 남기지 않는다.
  bool guardianAttest = false;

  /// 이미 만든 대화 상대라 만 19세 미만 여부·태어난 해와 달을 고칠 수 없다(저장된 답이 있을 때).
  final bool minorLocked;

  /// 부모가 아닌 분이 예전에 만든 아이의 대화 상대(관계를 묶지 않고, 법정대리인 칸을 묻지 않는다).
  final bool notGuardian;

  /// 이미 만든 대화 상대를 고친다(저장된 기본 정보가 있다).
  final bool editing;

  BasicsDraft({Map<String, dynamic>? basics, List<String> relations = const [], String? myNameDefault})
      : minorLocked = basics?.containsKey('minor') == true && basics?['minor'] is bool,
        notGuardian = basics?['notGuardian'] == true,
        editing = basics != null {
    final b = basics ?? const {};
    name.text = b['name'] ?? '';
    final saved = b['relation'] as String?;
    if (saved != null && saved.isNotEmpty) {
      if (saved == selfRelation || relations.contains(saved)) {
        relation = saved;
      } else {
        relation = '직접 입력';
        relationOther.text = saved;
      }
    }
    addressTerm.text = b['addressTerm'] ?? '';
    status = b['status'] as String?;
    myName.text = b['myName'] ?? myNameDefault ?? '';
    myRelation = b['myRelation'] as String?;
    calledMe.text = b['calledMe'] ?? '';
    minor = b['minor'] is bool ? b['minor'] as bool : null;
    birthYm = b['birthYm'] as String?;
    guardianBasis = b['guardianBasis'] as String?;
  }

  bool get isSelf => relation == selfRelation;

  /// 만 19세 미만인지 묻는 대화 상대(살아 계신 분, 나 자신 아님). 예전에 만들어 아직 답하지 않은
  /// 대화 상대는 여기서 묻지 않는다(서버가 받지 않는다 — 그분 동의 녹음을 시작할 때 따로 한 번 묻는다).
  bool get asksMinor => !isSelf && status == 'alive' && (!editing || minorLocked);

  /// 만 19세 미만이라고 답한 대화 상대(관계를 부모·자녀로 묶는다).
  bool get isMinor => asksMinor && minor == true;

  /// 새로 만들 수 없는 까닭(만 14세 미만인데 아직 받지 않음). 만들 수 있으면 null.
  String? createBlock(Map<String, dynamic> meta) {
    if (!isMinor || minorLocked || meta['under14Open'] == true) return null;
    final age = minorAge(birthYm);
    return age != null && age < 14 ? under14ClosedText : null;
  }

  Map<String, dynamic> toJson() => isSelf
      ? {
          // 나 자신: 내 이름 하나만 받고, 나머지는 서버와 같은 규칙으로 채운다
          'name': name.text,
          'relation': selfRelation,
          'addressTerm': '나',
          'status': 'alive',
          'myName': name.text,
          'myRelation': null,
          'calledMe': '',
        }
      : {
          'name': name.text,
          'relation': (relation == '직접 입력' ? relationOther.text.trim() : relation) ?? '',
          'addressTerm': addressTerm.text,
          'status': status ?? '',
          'myName': myName.text,
          'myRelation': myRelation,
          'calledMe': calledMe.text,
          // 살아 계신 분만. 고르지 않았으면 보내지 않는다(새로 만들 때는 서버가 골라 달라고 한다,
          // 고칠 때는 서버가 저장된 값을 그대로 둔다)
          if (asksMinor && minor != null) 'minor': minor,
          if (isMinor) 'birthYm': birthYm,
          if (isMinor && !notGuardian) 'guardianBasis': guardianBasis,
          if (isMinor && !notGuardian && !minorLocked) 'guardianAttest': guardianAttest,
        };

  void dispose() {
    for (final c in [name, relationOther, addressTerm, myName, calledMe]) {
      c.dispose();
    }
  }
}

/// "그분에 대해" · "나에 대해" 두 묶음. 새 대화 상대 만들기와 정보 고치기가 같이 쓴다.
/// '나 자신'을 고르면 내 이름 하나만 받는다.
class BasicsForm extends StatefulWidget {
  final BasicsDraft draft;

  /// 서버 /intake/meta (relations, statuses, myRelations, minorRelations, guardianBases, under14Open).
  final Map<String, dynamic> meta;

  /// 관계·상태·내 이름 등이 바뀔 때(같은 화면의 다른 칸이 따라 바뀌어야 할 때).
  final VoidCallback? onChanged;

  /// 말로 적기(🎤). 있으면 '그분이 나를 부르던 말' 칸에 🎤 단추를 단다(P1-07A 를 여기서 답하므로).
  /// 정보 고치기만 넘긴다. 새로 만들 때는 아직 대화 상대가 없어 받아쓸 곳이 없다.
  final Future<String> Function(VoiceClip clip)? transcribe;

  const BasicsForm({super.key, required this.draft, required this.meta, this.onChanged, this.transcribe});

  @override
  State<BasicsForm> createState() => _BasicsFormState();
}

class _BasicsFormState extends State<BasicsForm> {
  BasicsDraft get _d => widget.draft;

  /// 법정대리인 확인 체크 글(서버). 만 19세 미만을 고를 때 처음 읽는다.
  Future<Map<String, dynamic>>? _labels;

  void _notify() => widget.onChanged?.call();

  @override
  void initState() {
    super.initState();
    _d.myName.addListener(_notify);
    // '그분이 나를 부르던 말'을 비우면 정보 고치기에 그 질문(P1-07A)이 다시 보인다
    _d.calledMe.addListener(_notify);
  }

  @override
  void dispose() {
    _d.myName.removeListener(_notify);
    _d.calledMe.removeListener(_notify);
    super.dispose();
  }

  /// 관계를 부모·자녀로 묶는가(만 19세 미만이라고 답했고, 부모가 아닌 분이 예전에 만든 것이 아님).
  bool get _minorLock => _d.isMinor && !_d.notGuardian;

  /// 관계를 고르면 호칭 칸이 비었거나 앞서 저절로 채운 값일 때 같은 말로 채운다(할머니 → 할머니).
  void _pickRelation(String value) {
    final auto = _d.addressTerm.text.isEmpty || _d.addressTerm.text == _d.relation;
    // 나 자신을 고르면 이름 칸은 내 이름이다(비어 있으면 '나에 대해'에 적은 이름을 옮긴다)
    if (value == selfRelation && _d.name.text.trim().isEmpty) _d.name.text = _d.myName.text;
    setState(() => _d.relation = value);
    if (auto) _d.addressTerm.text = value == '직접 입력' || value == selfRelation ? '' : value;
    _notify();
  }

  /// 만 19세 미만이라고 답하면 관계를 부모·자녀만 남긴다(앞서 고른 다른 관계는 비운다).
  void _pickMinor(bool value) {
    setState(() {
      _d.minor = value;
      if (value) {
        final allowed = minorRelationsOf(widget.meta);
        if (_d.relation != null && !allowed.relation.contains(_d.relation)) {
          if (_d.addressTerm.text == _d.relation) _d.addressTerm.text = '';
          _d.relation = null;
        }
        if (_d.myRelation != null && !allowed.myRelation.contains(_d.myRelation)) _d.myRelation = null;
      }
    });
    _notify();
  }

  @override
  Widget build(BuildContext context) {
    final meta = widget.meta;
    final statuses = Map<String, dynamic>.from(meta['statuses']);
    final wide = Breakpoints.isWide(context);
    final self = _d.isSelf;
    final minorRelations = minorRelationsOf(meta);
    final relations = _minorLock
        ? minorRelations.relation
        : [
            selfRelation,
            for (final r in (meta['relations'] as List).cast<String>())
              if (r != selfRelation) r,
            '직접 입력',
          ];
    final myRelations =
        _minorLock ? minorRelations.myRelation : (meta['myRelations'] as List).cast<String>();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SectionLabel(self ? '나에 대해' : '그분에 대해'),
        AppCard(
          padding: const EdgeInsets.fromLTRB(20, 22, 20, 24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              FieldLabel(
                '나에게 어떤 분인가요?',
                hint: _minorLock
                    ? '아이의 대화 상대는 그 아이의 부모님만 만들 수 있어요.'
                    : '나를 닮은 대화 상대를 만들려면 \'나 자신\'을 골라 주세요.',
              ),
              ChoiceGroup(options: relations, value: _d.relation, onChanged: _pickRelation),
              if (_d.relation == '직접 입력') ...[
                const SizedBox(height: 10),
                TextField(
                  controller: _d.relationOther,
                  autofocus: _d.relationOther.text.isEmpty,
                  decoration: const InputDecoration(hintText: '예: 큰이모'),
                ),
              ],
              const SizedBox(height: 26),
              FieldLabel(
                self ? '내 이름' : '성함',
                hint: self
                    ? '화면에는 보이지 않아요. 녹음에서 내 목소리를 알아보는 데만 써요.'
                    : '화면에는 보이지 않아요. 녹음에서 그분을 알아보고 대화를 만드는 데만 써요.',
              ),
              TextField(
                controller: _d.name,
                maxLength: 20,
                decoration: const InputDecoration(hintText: '예: 김영자', counterText: ''),
              ),
              if (self) ...[
                const SizedBox(height: 12),
                const Notice(
                  '내 목소리가 담긴 녹음(통화·음성 메모·영상)과 내 이야기로, 나처럼 대답하는 대화 상대를 만들어요. '
                  "다 만든 뒤 '가족과 함께 쓰기'에서 가족을 초대할 수 있어요.",
                  icon: Icons.person_outline_rounded,
                ),
              ] else ...[
                const SizedBox(height: 26),
                const FieldLabel('내가 그분을 부르는 말', hint: '대화에서 그대로 써요.'),
                TextField(
                  controller: _d.addressTerm,
                  decoration: const InputDecoration(hintText: '예: 할머니, 엄마, 여보'),
                ),
                const SizedBox(height: 26),
                const FieldLabel('그분은 지금 살아 계신가요?'),
                // 예전에 고른 '대화가 어려우신 분'은 새로 고를 수 없고 바꿀 수도 없다(읽기만)
                if (_d.status == 'unable')
                  const Notice(_unableLine, icon: Icons.pause_circle_outline_rounded)
                else
                  for (final entry in statuses.entries)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Pill(
                        label: entry.value as String,
                        wide: true,
                        selected: _d.status == entry.key,
                        // 정보 고치기의 질문도 그분 상태를 따라 바뀐다(돌아가신 분만 묻는 질문 등)
                        onTap: () {
                          setState(() => _d.status = entry.key);
                          _notify();
                        },
                      ),
                    ),
                if (_d.status == 'alive') ...[
                  const SizedBox(height: 4),
                  const Notice(
                    '살아 계신 분의 대화 상대는 그분 본인이 직접 허락하셔야 해요. 녹음을 올린 뒤, 앱이 보여 주는 대본대로 '
                    "그분이 직접 말씀하시는 '그분 동의 녹음'을 해 주세요. TalkTo가 확인하기 전에는 그분 녹음을 글로 옮기거나 "
                    '해외 회사로 보내지 않아요.',
                    icon: Icons.front_hand_outlined,
                  ),
                  ..._minor(meta),
                ],
              ],
            ],
          ),
        ),
        if (!self) ...[
          const SizedBox(height: 28),
          const SectionLabel('나에 대해'),
          AppCard(
            padding: const EdgeInsets.fromLTRB(20, 22, 20, 24),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (wide)
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [Expanded(child: _myName()), const SizedBox(width: 14), Expanded(child: _calledMe())],
                  )
                else ...[
                  _myName(),
                  const SizedBox(height: 22),
                  _calledMe(),
                ],
                const SizedBox(height: 26),
                const FieldLabel('나는 그분의…'),
                ChoiceGroup(
                  options: myRelations,
                  value: _d.myRelation,
                  onChanged: (v) {
                    setState(() => _d.myRelation = v);
                    _notify();
                  },
                ),
              ],
            ),
          ),
        ],
      ],
    );
  }

  /// 살아 계신 분: 만 19세 미만인가 · 태어난 해와 달 · 법정대리인 확인 · 다른 쪽 부모님.
  /// 이미 답한 대화 상대는 고칠 수 없어 읽기만(다른 쪽 부모님은 고칠 수 있다).
  List<Widget> _minor(Map<String, dynamic> meta) {
    if (!_d.asksMinor) return const [];
    final bases = guardianBasesOf(meta);
    final basis = [
      const SizedBox(height: 18),
      ChoiceGroup(
        options: bases.keys.toList(),
        value: _d.guardianBasis,
        label: (key) => bases[key] ?? key,
        onChanged: (v) {
          setState(() => _d.guardianBasis = v);
          _notify();
        },
      ),
    ];
    if (_d.minorLocked) {
      if (_d.minor != true) return const [];
      final born = RegExp(r'^(\d{4})-(\d{2})$').firstMatch(_d.birthYm ?? '');
      return [
        const SizedBox(height: 18),
        Notice(
          '만 19세 미만'
          '${born == null ? '' : ' · ${born.group(1)}년 ${int.parse(born.group(2)!)}월생'}. '
          '아이의 태어난 해·달과 만 19세 미만 여부는 고칠 수 없어요. 잘못 적었으면 고객센터로 알려 주세요.',
          icon: Icons.child_care_rounded,
        ),
        if (!_d.notGuardian) ...basis,
      ];
    }
    final under14 = _d.createBlock(meta);
    return [
      const SizedBox(height: 22),
      const FieldLabel('그분은 만 19세 미만인가요?'),
      ChoiceGroup(
        options: const ['아니요', '네'],
        value: _d.minor == null ? null : (_d.minor! ? '네' : '아니요'),
        onChanged: (v) => _pickMinor(v == '네'),
      ),
      if (_d.minor == true) ...[
        const SizedBox(height: 22),
        const FieldLabel('태어난 해와 달', hint: '나이를 세는 데만 써요.'),
        BirthPicker(
          value: _d.birthYm,
          onChanged: (v) {
            setState(() => _d.birthYm = v);
            _notify();
          },
        ),
        const SizedBox(height: 16),
        const Notice('아이의 대화 상대는 그 아이의 부모(법정대리인)만 만들 수 있어요.', icon: Icons.child_care_rounded),
        if (under14 != null) ...[
          const SizedBox(height: 10),
          ErrorBox(under14),
        ],
        const SizedBox(height: 10),
        FutureBuilder<Map<String, dynamic>>(
          future: _labels ??= LegalApi.guardianLabels().catchError((Object _) => <String, dynamic>{}),
          builder: (context, snap) {
            final label = '${snap.data?['attest'] ?? ''}'.trim();
            return ConsentCheck(
              label: label.isEmpty ? attestFallback : label,
              value: _d.guardianAttest,
              docKey: 'guardian_consent',
              onChanged: (v) {
                setState(() => _d.guardianAttest = v);
                _notify();
              },
            );
          },
        ),
        ...basis,
      ],
    ];
  }

  Widget _myName() => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [const FieldLabel('내 이름'), TextField(controller: _d.myName)],
      );

  Widget _calledMe() => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const FieldLabel('그분이 나를 부르던 말'),
          VoiceField(
            controller: _d.calledMe,
            transcribe: widget.transcribe,
            child: TextField(controller: _d.calledMe, decoration: const InputDecoration(hintText: '예: 하늘아, 우리 강아지')),
          ),
        ],
      );
}

/// 태어난 해와 달 고르기(만 19세 미만이라 올해부터 19년 전까지). 'YYYY-MM' 으로 알린다.
class BirthPicker extends StatelessWidget {
  final String? value;
  final ValueChanged<String?> onChanged;

  const BirthPicker({super.key, required this.value, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    final now = DateTime.now();
    final match = RegExp(r'^(\d{4})-(\d{2})$').firstMatch(value ?? '');
    final year = match == null ? null : int.parse(match.group(1)!);
    final month = match == null ? null : int.parse(match.group(2)!);
    final lastMonth = year == now.year ? now.month : 12;
    String ym(int y, int m) => '$y-${m.toString().padLeft(2, '0')}';
    return Row(
      children: [
        Expanded(
          child: DropdownButtonFormField<int>(
            key: const ValueKey('birth-year'),
            value: year,
            isExpanded: true,
            hint: const Text('태어난 해'),
            items: [
              for (var y = now.year; y >= now.year - 19; y--) DropdownMenuItem(value: y, child: Text('$y년')),
            ],
            onChanged: (y) {
              if (y == null) return;
              final m = month ?? (y == now.year ? now.month : 1);
              onChanged(ym(y, y == now.year && m > now.month ? now.month : m));
            },
          ),
        ),
        const SizedBox(width: 10),
        Expanded(
          child: DropdownButtonFormField<int>(
            key: const ValueKey('birth-month'),
            value: month != null && month <= lastMonth ? month : null,
            isExpanded: true,
            hint: const Text('달'),
            items: [for (var m = 1; m <= lastMonth; m++) DropdownMenuItem(value: m, child: Text('$m월'))],
            onChanged: year == null
                ? null
                : (m) {
                    if (m != null) onChanged(ym(year, m));
                  },
          ),
        ),
      ],
    );
  }
}
