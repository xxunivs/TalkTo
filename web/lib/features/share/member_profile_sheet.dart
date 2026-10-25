import 'package:flutter/material.dart';

import '../../core/network/api_client.dart';
import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/ui.dart';
import '../legal/legal_footer.dart';
import 'share_api.dart';

/// 함께하는 가족 한 사람에 대해 적는 것(서버 ShareProfile). 초대 받는 화면(맞는지 확인·직접 적기),
/// 함께하는 가족의 '나에 대한 정보 고치기', 만든 사람의 초대 적어 두기·정보 고치기가 같이 쓴다.
const profileKeys = ['myName', 'myRelation', 'addressTerm', 'calledMe', 'speechLevel', 'selfReference', 'callsOwner'];

/// 함께하려면 꼭 있어야 하는 칸.
const requiredProfileKeys = ['myName', 'myRelation', 'addressTerm'];

/// '더 적기(선택)' 아래에 접어 두는 칸.
const extraProfileKeys = ['selfReference', 'callsOwner'];

/// 칸마다 글자 수 상한(서버 sharing.validate_profile 과 같다).
const profileMaxLength = {
  'myName': 20,
  'myRelation': 20,
  'addressTerm': 15,
  'calledMe': 15,
  'selfReference': 15,
  'callsOwner': 15,
};

/// 그분이 그 가족에게 쓰던 말. 고르지 않으면(잘 모르겠어요) null 로 보낸다. 미리 고르지 않는다.
const speechLevels = ['반말', '존댓말'];
const _unknownLevel = '잘 모르겠어요';

/// 나·저처럼 대화하는 사람 자신을 가리키는 말은 그분을 부르는 말이 될 수 없다(서버 intake._SELF_WORDS).
const _selfWords = {'나', '저', '본인', '나 자신'};

/// 누가 읽는 글인가: 함께하는 가족 자신(member) · 만든 사람이 그 가족에 대해 적을 때(owner).
enum ProfileVoice { member, owner }

/// 아이(만 19세 미만)의 대화 상대를 함께 쓸 수 있는 관계 안내(법정대리인과 직계가족만).
const minorScopeText = '아이의 대화 상대는 법정대리인과 직계가족(부모·조부모)만 함께 쓸 수 있어요.';

/// 칸 이름. [title] = 만든 사람 쪽 호칭(할머니), [ownerName] = 만든 사람 이름(지은),
/// [self] = 만든 사람 자신의 대화 상대(그분 = 만든 사람).
String profileLabel(
  String key,
  ProfileVoice voice, {
  String title = '그분',
  String ownerName = '가족',
  bool self = false,
}) {
  if (voice == ProfileVoice.member) {
    return switch (key) {
      'myName' => '내 이름 (그분이 아는 이름)',
      'myRelation' => '나는 그분의…',
      'addressTerm' => '내가 그분을 부르는 말',
      'calledMe' => '그분이 나를 부르던 말',
      'speechLevel' => '그분이 나에게 쓰시던 말',
      'selfReference' => '내 앞에서 그분이 자기를 가리키던 말',
      'callsOwner' => '내가 $ownerName님을 부르는 말',
      _ => key,
    };
  }
  if (self) {
    return switch (key) {
      'myName' => '이름',
      'myRelation' => '나와의 관계',
      'addressTerm' => '이분이 나를 부르는 말',
      'calledMe' => '내가 이분을 부르는 말',
      'speechLevel' => '내가 이분께 쓰는 말',
      'selfReference' => '이분 앞에서 내가 나를 가리키는 말',
      _ => key,
    };
  }
  return switch (key) {
    'myName' => '이름',
    'myRelation' => '${josa(title, '과', '와')}의 관계',
    'addressTerm' => '이분이 ${josa(title, '을', '를')} 부르는 말',
    'calledMe' => '$title께서 이분을 부르던 말',
    'speechLevel' => '$title께서 이분께 쓰시던 말',
    'selfReference' => '이분 앞에서 $title께서 자기를 가리키던 말',
    'callsOwner' => '이분이 나를 부르는 말',
    _ => key,
  };
}

/// 화면에 보일 값(말 높이를 고르지 않았으면 '잘 모르겠어요').
String profileValueText(Map? profile, String key) {
  final value = '${profile?[key] ?? ''}'.trim();
  if (key == 'speechLevel' && value.isEmpty) return _unknownLevel;
  return value;
}

/// 적는 중인 값. 칸마다 글 상자 하나와 말 높이 하나.
class ProfileDraft {
  final Map<String, TextEditingController> text;
  String? speechLevel;

  ProfileDraft([Map? initial])
      : text = {
          for (final key in profileKeys)
            if (key != 'speechLevel') key: TextEditingController(text: '${initial?[key] ?? ''}'.trim()),
        },
        speechLevel = speechLevels.contains(initial?['speechLevel']) ? initial!['speechLevel'] as String : null;

  String value(String key) => key == 'speechLevel' ? (speechLevel ?? '') : text[key]!.text.trim();

  /// 서버로 보낼 값(빈 칸은 null). [keys] 를 주면 그 칸만.
  Map<String, dynamic> toJson([List<String> keys = profileKeys]) => {
        for (final key in keys) key: value(key).isEmpty ? null : value(key),
      };

  /// 적은 것이 하나라도 있는가.
  bool get isEmpty => profileKeys.every((k) => value(k).isEmpty);

  /// 꼭 있어야 하는 칸 가운데 빈 것.
  List<String> missing([List<String> keys = profileKeys]) =>
      [for (final key in requiredProfileKeys) if (keys.contains(key) && value(key).isEmpty) key];

  /// 서버로 보내기 전 확인(서버도 같은 규칙으로 다시 본다). 괜찮으면 null.
  String? check({required ProfileVoice voice, List<String> keys = profileKeys, bool requireAll = true}) {
    if (requireAll) {
      for (final key in missing(keys)) {
        return switch (key) {
          'myName' => voice == ProfileVoice.member ? '내 이름을 적어 주세요.' : '이름을 적어 주세요.',
          'myRelation' => '관계를 골라 주세요.',
          _ => '그분을 부르는 말을 적어 주세요.',
        };
      }
    }
    if (keys.contains('addressTerm') && _selfWords.contains(value('addressTerm'))) {
      return "'${value('addressTerm')}'는 부르는 말로 쓸 수 없어요. 그분을 부르는 말을 적어 주세요.";
    }
    return null;
  }

  void dispose() {
    for (final c in text.values) {
      c.dispose();
    }
  }
}

/// 칸들. [keys] 가운데 [extraProfileKeys] 는 '더 적기(선택)' 아래에 접어 둔다(한 칸만 고칠 때는 펼친다).
class ProfileForm extends StatefulWidget {
  final ProfileDraft draft;
  final ProfileVoice voice;
  final String title;
  final String ownerName;
  final bool self;
  final bool imported;
  final List<String> relations;

  /// 서버가 준 관계 → 부르는 말 추천(없으면 앱 안의 표).
  final Map? suggestions;
  final List<String> keys;
  final VoidCallback? onChanged;

  /// 아이(만 19세 미만)의 대화 상대: 관계는 서버가 준 목록([relations])에서만 고른다(직접 적지 않는다).
  final bool minorScope;

  const ProfileForm({
    super.key,
    required this.draft,
    required this.voice,
    this.title = '그분',
    this.ownerName = '가족',
    this.self = false,
    this.imported = false,
    this.relations = const [],
    this.suggestions,
    this.keys = profileKeys,
    this.onChanged,
    this.minorScope = false,
  });

  @override
  State<ProfileForm> createState() => _ProfileFormState();
}

class _ProfileFormState extends State<ProfileForm> {
  late bool _more = extraProfileKeys.any((k) => widget.draft.value(k).isNotEmpty);

  ProfileDraft get _d => widget.draft;

  /// 나 자신 대화 상대는 그분이 곧 만든 사람이라 '만든 분을 부르는 말'이 없다.
  List<String> get _keys => [
        for (final k in widget.keys)
          if (!(k == 'callsOwner' && widget.self)) k,
      ];

  void _changed() {
    setState(() {});
    widget.onChanged?.call();
  }

  String _label(String key) {
    final label = profileLabel(key, widget.voice, title: widget.title, ownerName: widget.ownerName, self: widget.self);
    return requiredProfileKeys.contains(key) ? label : '$label (선택)';
  }

  String? _hint(String key) => switch (key) {
        'myName' => widget.voice == ProfileVoice.owner
            ? (widget.imported ? '예전 가족 목록 속 이름과 같게 적으면 더 잘 알아봐요.' : null)
            : '그분이 대화에서 나를 알아보는 데 써요.',
        'addressTerm' => '대화에서 그대로 써요.',
        'calledMe' => '예: 철수야, 우리 강아지 · 모르면 비워 두세요.',
        _ => null,
      };

  List<String> _chips(String key) {
    switch (key) {
      case 'myRelation':
        return widget.relations;
      case 'addressTerm':
        return addressSuggestionsFor(_d.value('myRelation'), widget.suggestions);
      case 'selfReference':
        final term = _d.value('addressTerm');
        return term.isEmpty ? const ['내가'] : [josa(term, '이', '가'), '내가'];
      default:
        return const [];
    }
  }

  Widget _text(String key) {
    final controller = _d.text[key]!;
    final chips = _chips(key);
    // 아이의 대화 상대: 관계는 목록에서만(직접 적는 칸을 숨긴다)
    final chipsOnly = key == 'myRelation' && widget.minorScope;
    return Padding(
      padding: const EdgeInsets.only(bottom: 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          FieldLabel(_label(key), hint: _hint(key)),
          if (chips.isNotEmpty) ...[
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final o in chips)
                  Pill(
                    label: o,
                    selected: controller.text.trim() == o,
                    onTap: () {
                      controller.text = controller.text.trim() == o ? '' : o;
                      _changed();
                    },
                  ),
              ],
            ),
            const SizedBox(height: 8),
          ],
          if (chipsOnly)
            Text(keepAll(minorScopeText), style: AppText.small)
          else
            TextField(
              controller: controller,
              maxLength: profileMaxLength[key],
              decoration: InputDecoration(
                hintText: key == 'myRelation' ? '목록에 없으면 직접 적어 주세요 (예: 큰아들)' : null,
                counterText: '',
              ),
              onChanged: (_) => _changed(),
            ),
        ],
      ),
    );
  }

  Widget _level() {
    return Padding(
      padding: const EdgeInsets.only(bottom: 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          FieldLabel(_label('speechLevel')),
          ChoiceGroup(
            options: const [...speechLevels, _unknownLevel],
            value: _d.speechLevel ?? _unknownLevel,
            onChanged: (v) {
              _d.speechLevel = speechLevels.contains(v) ? v : null;
              _changed();
            },
          ),
        ],
      ),
    );
  }

  Widget _field(String key) => key == 'speechLevel' ? _level() : _text(key);

  @override
  Widget build(BuildContext context) {
    final keys = _keys;
    final main = [for (final k in keys) if (keys.length == 1 || !extraProfileKeys.contains(k)) k];
    final extra = [for (final k in keys) if (keys.length > 1 && extraProfileKeys.contains(k)) k];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final k in main) _field(k),
        if (extra.isNotEmpty) ...[
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: () => setState(() => _more = !_more),
              icon: Icon(_more ? Icons.expand_less_rounded : Icons.expand_more_rounded, size: 20),
              label: const Text('더 적기 (선택)'),
            ),
          ),
          if (_more) ...[const SizedBox(height: 8), for (final k in extra) _field(k)],
        ],
      ],
    );
  }
}

/// 적어 두기·고치기 창. PC 는 가운데 창, 휴대폰은 아래에서 올라오는 시트.
/// [onSave] 가 있으면 저장 단추에서 부르고(서버), 실패하면 창 안에 이유를 보인다.
/// 저장하면 적은 값(ShareProfile, 빈 칸은 null)을, 닫으면 null 을 돌려준다.
/// [textScaler] 는 창의 글 크기(초대 받기 화면처럼 글을 키운 화면에서 넘긴다).
Future<Map<String, dynamic>?> showProfileSheet(
  BuildContext context, {
  TextScaler? textScaler,
  required String heading,
  String? lead,
  Map? initial,
  required ProfileVoice voice,
  String title = '그분',
  String ownerName = '가족',
  bool self = false,
  bool imported = false,
  List<String> relations = const [],
  Map? suggestions,
  List<String> keys = profileKeys,
  bool requireAll = true,
  String action = '저장하기',
  String? note,
  bool minorScope = false,
  Future<void> Function(Map<String, dynamic> profile)? onSave,
}) {
  final sheet = _ProfileSheet(
    heading: heading,
    lead: lead,
    initial: initial,
    voice: voice,
    title: title,
    ownerName: ownerName,
    self: self,
    imported: imported,
    relations: relations,
    suggestions: suggestions,
    keys: keys,
    requireAll: requireAll,
    action: action,
    note: note,
    minorScope: minorScope,
    onSave: onSave,
  );
  // 창은 앱 맨 위 Navigator 에 따로 그려져서, 부른 화면만 키운 글 크기(초대 받기 화면은 어르신을 위해
  // 1.15배)를 물려받지 않는다. 그래서 [textScaler](없으면 [context] 의 글 크기)를 창에도 씌운다.
  final scaler = textScaler ?? MediaQuery.textScalerOf(context);
  final scaled = Builder(
    builder: (ctx) => MediaQuery(data: MediaQuery.of(ctx).copyWith(textScaler: scaler), child: sheet),
  );
  if (Breakpoints.isWide(context)) {
    return showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => Dialog(
        child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: 520, maxHeight: MediaQuery.sizeOf(context).height * 0.9),
          child: Padding(padding: const EdgeInsets.fromLTRB(24, 24, 24, 16), child: scaled),
        ),
      ),
    );
  }
  return showModalBottomSheet<Map<String, dynamic>>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    backgroundColor: AppColors.card,
    constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.92),
    builder: (ctx) => Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).viewInsets.bottom),
      child: SafeArea(child: Padding(padding: const EdgeInsets.fromLTRB(20, 0, 20, 12), child: scaled)),
    ),
  );
}

class _ProfileSheet extends StatefulWidget {
  final String heading;
  final String? lead;
  final Map? initial;
  final ProfileVoice voice;
  final String title;
  final String ownerName;
  final bool self;
  final bool imported;
  final List<String> relations;
  final Map? suggestions;
  final List<String> keys;
  final bool requireAll;
  final String action;
  final String? note;
  final bool minorScope;
  final Future<void> Function(Map<String, dynamic> profile)? onSave;

  const _ProfileSheet({
    required this.heading,
    this.lead,
    this.initial,
    required this.voice,
    required this.title,
    required this.ownerName,
    required this.self,
    required this.imported,
    required this.relations,
    this.suggestions,
    required this.keys,
    required this.requireAll,
    required this.action,
    this.note,
    this.minorScope = false,
    this.onSave,
  });

  @override
  State<_ProfileSheet> createState() => _ProfileSheetState();
}

class _ProfileSheetState extends State<_ProfileSheet> {
  late final _draft = ProfileDraft(widget.initial);
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _draft.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final problem = _draft.check(voice: widget.voice, keys: widget.keys, requireAll: widget.requireAll);
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    final profile = _draft.toJson(widget.keys);
    if (widget.onSave == null) {
      Navigator.pop(context, profile);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.onSave!(profile);
      if (mounted) Navigator.pop(context, profile);
    } catch (error) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = error is ApiException ? error.message : '저장하지 못했어요. 잠시 뒤 다시 해 주세요.';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(keepAll(widget.heading), style: AppText.title),
          if (widget.lead != null) ...[
            const SizedBox(height: 6),
            Text(keepAll(widget.lead!), style: AppText.small.copyWith(fontSize: 14, color: AppColors.ink2)),
          ],
          const SizedBox(height: 20),
          ProfileForm(
            draft: _draft,
            voice: widget.voice,
            title: widget.title,
            ownerName: widget.ownerName,
            self: widget.self,
            imported: widget.imported,
            relations: widget.relations,
            suggestions: widget.suggestions,
            keys: widget.keys,
            minorScope: widget.minorScope,
            onChanged: () {
              if (_error != null) setState(() => _error = null);
            },
          ),
          if (widget.note != null) ...[
            Notice(widget.note!, icon: Icons.lightbulb_outline_rounded),
            const SizedBox(height: 12),
          ],
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(keepAll(_error!), style: const TextStyle(color: AppColors.danger, fontSize: 13.5)),
            ),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              TextButton(onPressed: _busy ? null : () => Navigator.pop(context), child: const Text('취소')),
              const SizedBox(width: 8),
              Flexible(
                child: FilledButton(
                  onPressed: _busy ? null : _save,
                  style: FilledButton.styleFrom(minimumSize: const Size(0, 48)),
                  child: Text(_busy ? '잠시만요…' : widget.action),
                ),
              ),
            ],
          ),
          // 가족에 대해 적는 창도 정보를 받는 화면이다(개인정보 처리방침 링크)
          const SizedBox(height: 12),
          const Center(child: LegalFooter(compact: true)),
        ],
      ),
    );
  }
}
