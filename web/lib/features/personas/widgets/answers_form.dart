import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../../core/theme.dart';
import '../../../shared/widgets/ui.dart';
import '../../../shared/widgets/voice_input.dart';
import '../data/persona_api.dart';
import 'basics_form.dart' show selfRelation;
import 'family_rows.dart';

/// 핵심 질문 답 칸들. 문항은 서버(대화 상대 상세의 questions, 없으면 /intake/meta)가 정의한 그대로 그린다.
/// 상세의 questions 는 서버가 이 대화 상대에 맞춘 것이다: required(개편 뒤 가족·예전 대화 상대·나 자신마다
/// 다르다)·title(그분 상태에 맞는 문구)·default(미리 채울 답). 화면은 다시 고르지 않고 그대로 쓴다.
/// 답이 바뀌면 [answers] 를 바로 고치고 [onChanged] 를 부른다. 저장은 부르는 쪽이 한다
/// (질문 단계는 저절로 저장, 정보 고치기는 저장 단추). 저장 직전에는
/// `GlobalKey<AnswersFormState>` 로 [AnswersFormState.commitPending] 을 불러, 적어 두고
/// '더하기'를 누르지 않은 말도 답에 넣는다.
///
/// 글로 적는 칸마다 큰 🎤 단추가 붙는다([personaId] 가 있을 때). 말하면 서버가 글로 옮겨 칸에 넣고,
/// 고친 뒤 저장한다. 문항에 skipOption({value, label})이 있으면 '기억이 안 나요'·'따로 없었어요'·'모름'
/// 칩을 보인다. 고르면 답은 그 값(__unknown__·__none__)이 되고 칸은 비고 잠긴다(답한 것으로 센다).
class AnswersForm extends StatefulWidget {
  final List<Map<String, dynamic>> questions;
  final Map<String, dynamic> answers;
  final List<String> suggestions;
  final VoidCallback onChanged;

  /// 대화 상대 기본 정보(가족 표의 '나' 줄, 나 자신일 때 숨길 문항). 없어도 된다.
  final Map<String, dynamic>? basics;

  /// 문항 id → 위치. 빠진 필수 답으로 스크롤할 때 쓴다.
  final Map<String, GlobalKey> keys;

  /// 묶음(section) → 묶음 제목 아래 한 줄 안내(서버 meta.sections, `PersonaApi.sectionLeads`).
  final Map<String, String> sectionLeads;

  /// 말로 적기(🎤)가 글로 옮겨 달라고 보낼 대화 상대. 없으면 🎤 단추를 달지 않는다.
  final String? personaId;

  const AnswersForm({
    super.key,
    required this.questions,
    required this.answers,
    required this.keys,
    required this.onChanged,
    this.suggestions = const [],
    this.basics,
    this.sectionLeads = const {},
    this.personaId,
  });

  /// 기본 정보로 이미 답한 문항 → 그 기본 정보 칸. 기본 정보에 있으면 다시 묻지 않는다
  /// (P1-07A '그분은 나를 뭐라고 부르셨어요?' = 만들 때 적은 '그분이 나를 부르던 말').
  static const answeredByBasics = {'P1-07A': 'calledMe'};

  /// 이 대화 상대에게 묻는 문항인가. 나 자신이면 '나와 이야기하실 때' 같은 문항(when=notSelf)을,
  /// 돌아가신 분이 아니면 돌아가신 분에게만 묻는 문항(when=deceased)을 묻지 않는다.
  /// 기본 정보로 이미 답한 문항([answeredByBasics])도 묻지 않는다.
  /// [basics] 가 없으면 다 묻는다(기억 더 채우기는 그분 상태로 미리 거른다).
  static bool asks(Map q, Map? basics) {
    final field = answeredByBasics[q['id']];
    if (field != null && '${basics?[field] ?? ''}'.trim().isNotEmpty) return false;
    return switch (q['when']) {
      'notSelf' => basics?['relation'] != selfRelation,
      'deceased' => basics == null || basics['status'] == 'deceased',
      _ => true,
    };
  }

  /// '기억이 안 나요'·'따로 없었어요'·'모름' 고르기(서버 문항의 skipOption). 없으면 null.
  static ({String value, String label})? skipOf(Map q) {
    final option = q['skipOption'];
    if (option is! Map || option['value'] is! String) return null;
    return (value: option['value'] as String, label: option['label'] as String? ?? '모름');
  }

  /// 민감한 범주(종교·건강) 문항은 그 범주에 동의했을 때만 묻는다(동의는 '기억 더 채우기'에서).
  /// [consents] 는 대화 상대 상세의 consents.
  static bool consented(Map q, Map? consents) => switch (q['sensitive']) {
        'religion' => consents?['sensitiveReligion'] == true,
        'health' => consents?['sensitiveHealth'] == true,
        _ => true,
      };

  static bool answered(dynamic v) =>
      v != null && (v is! String || v.trim().isNotEmpty) && (v is! List || v.isNotEmpty);

  /// 진행 표시용: 필수 질문 수·그중 답한 수, 답한 질문 수 / 보이는 질문 수([asks] 로 거른 것만 센다).
  static ({int required, int requiredDone, int done, int total}) progress(
    List<Map<String, dynamic>> questions,
    Map<String, dynamic> answers,
    Map? basics,
  ) {
    final shown = questions.where((q) => asks(q, basics));
    final required = shown.where((q) => q['required'] == true);
    return (
      required: required.length,
      requiredDone: required.where((q) => answered(answers[q['id']])).length,
      done: shown.where((q) => answered(answers[q['id']])).length,
      total: shown.length,
    );
  }

  /// 꼭 답해야 하는데 빈 첫 문항 id. 다 답했으면 null. [basics] 를 주면 이 대화 상대에게 묻지 않는
  /// 문항([asks])은 빼고 본다. '기억이 안 나요'(skipOption)를 고른 것도 답한 것이다.
  static String? firstMissing(List<Map<String, dynamic>> questions, Map<String, dynamic> answers, [Map? basics]) {
    for (final q in questions) {
      if (basics != null && !asks(q, basics)) continue;
      if (q['required'] == true && !answered(answers[q['id']])) return q['id'] as String;
    }
    return null;
  }

  /// 빠진 필수 답이 있으면 그 문항으로 스크롤하고 알려준다. 다 답했으면 true.
  static bool checkRequired(
    BuildContext context,
    List<Map<String, dynamic>> questions,
    Map<String, dynamic> answers,
    Map<String, GlobalKey> keys, {
    Map? basics,
  }) {
    final missing = firstMissing(questions, answers, basics);
    if (missing == null) return true;
    final target = keys[missing]?.currentContext;
    if (target != null) {
      Scrollable.ensureVisible(target, duration: const Duration(milliseconds: 300), alignment: 0.3);
    }
    showMessage(context, '아직 답하지 않은 필수 질문이 있어요.');
    return false;
  }

  @override
  State<AnswersForm> createState() => AnswersFormState();
}

class AnswersFormState extends State<AnswersForm> {
  final Map<String, TextEditingController> _text = {};

  /// 문항 id → '더하기' 칸의 더하기 동작
  final Map<String, VoidCallback> _adders = {};

  /// 적어 두고 '더하기'를 누르지 않은 말을 답에 넣는다(저장 직전에 부른다).
  void commitPending() {
    for (final add in _adders.values.toList()) {
      add();
    }
  }

  Map<String, dynamic> get _answers => widget.answers;
  List<String> get _suggestions => widget.suggestions;

  /// 미리 채워 본 문항 id(한 문항에 한 번만 채운다. 지운 선택 답을 다시 채우지 않게).
  final Set<String> _defaulted = {};

  /// 서버가 미리 채워 두라고 한 답(문항의 default, 예: 개편 뒤 대화 상대의 '그분의 사망·장례·임종' =
  /// 물어볼 때만)을 이 대화 상대에게 묻는 빈 문항에 넣는다. 가족은 확인만 하면 되고 답한 것으로 센다.
  /// 정보 고치기에서 그분 상태를 '돌아가셨어요'로 바꿔 새로 묻게 된 문항도 그때 채운다.
  void _fillDefaults() {
    for (final q in widget.questions) {
      final id = q['id'] as String;
      final value = q['default'];
      if (value == null || _defaulted.contains(id) || !AnswersForm.asks(q, widget.basics)) continue;
      _defaulted.add(id);
      if (!AnswersForm.answered(_answers[id])) _answers[id] = value;
    }
  }

  @override
  void dispose() {
    for (final c in _text.values) {
      c.dispose();
    }
    super.dispose();
  }

  void _set(String key, dynamic value) {
    setState(() {
      final empty = value == null || (value is String && value.trim().isEmpty) || (value is List && value.isEmpty);
      if (empty) {
        _answers.remove(key);
      } else {
        _answers[key] = value;
      }
    });
    widget.onChanged();
  }

  /// 여럿 고르기·말 목록의 답. '기억이 안 나요'(글 하나)를 골랐으면 빈 목록.
  List<String> _list(String id) {
    final value = _answers[id];
    return value is List ? List<String>.from(value) : <String>[];
  }

  /// 🎤 로 말한 것을 글로 옮긴다. 대화 상대를 모르면(시험·미리 보기) 🎤 를 달지 않는다.
  Future<String> Function(VoiceClip clip)? get _transcribe {
    final id = widget.personaId;
    return id == null ? null : (clip) => PersonaApi.transcribeAnswer(id, clip);
  }

  /// '기억이 안 나요' 등을 고르거나 거둔다. 고르면 적은 글·고른 답을 비우고 칸을 잠근다.
  void _toggleSkip(String id, String value) {
    if (_answers[id] == value) {
      _set(id, null);
      return;
    }
    for (final key in [id, '$id:add']) {
      _text[key]?.clear();
    }
    _text.remove('$id:other')?.dispose();
    _text.remove('$id:detail')?.dispose();
    _answers.remove('$id:detail');
    _set(id, value);
  }

  /// 글 칸. [personaId] 가 있으면 옆에 큰 🎤 단추를 단다(받아쓴 글은 칸에 들어가고 [onChanged] 로 저장).
  Widget _voice(TextEditingController controller, TextField field, ValueChanged<String> onChanged, {bool enabled = true}) {
    return VoiceField(
      controller: controller,
      transcribe: _transcribe,
      onChanged: onChanged,
      enabled: enabled,
      maxLength: field.maxLength,
      child: field,
    );
  }

  /// 필수와 선택이 섞인 목록(만들기·정보 고치기)인가. 모두 선택인 '기억 더 채우기'에는 표시를 달지 않는다.
  bool get _mixed => widget.questions.any((q) => q['required'] == true);

  /// 묶음 이름. 필수 질문이 든 묶음은 몇 개인지 붙인다(예: 말투 · 필수 2개).
  String _sectionTitle(String section, List<Map<String, dynamic>> questions) {
    final need = questions.where((q) => q['required'] == true).length;
    return need == 0 ? section : '$section · 필수 $need개';
  }

  /// 질문 제목 옆 '필수'·'선택' 표시
  Widget _badge(bool required) {
    return Container(
      margin: const EdgeInsets.only(left: 6),
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 1),
      decoration: BoxDecoration(
        color: required ? AppColors.clay : null,
        border: required ? null : Border.all(color: AppColors.line2),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        required ? '필수' : '선택',
        style: TextStyle(
          fontSize: 11.5,
          fontWeight: FontWeight.w700,
          height: 1.4,
          color: required ? Colors.white : AppColors.muted,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    _adders.clear(); // 지금 보이는 문항의 더하기 칸만 남긴다(_addRow 가 다시 채운다)
    _fillDefaults();
    // 묶음(section)마다 카드 하나. 카드 안 질문은 줄로 나눈다.
    final groups = <String, List<Map<String, dynamic>>>{};
    for (final q in widget.questions) {
      // 나 자신이면 '나와 이야기하실 때' 같은 문항을, 살아 계신 분이면 임종 이야기 같은 문항을 묻지 않는다
      if (!AnswersForm.asks(q, widget.basics)) continue;
      groups.putIfAbsent(q['section'] as String? ?? '', () => []).add(q);
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final entry in groups.entries) ...[
          if (entry.key.isNotEmpty) SectionLabel(_sectionTitle(entry.key, entry.value)),
          if (widget.sectionLeads[entry.key] != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(4, 0, 4, 12),
              child: Text(keepAll(widget.sectionLeads[entry.key]!), style: AppText.small.copyWith(color: AppColors.ink2)),
            ),
          AppCard(
            padding: const EdgeInsets.fromLTRB(20, 4, 20, 4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                for (var i = 0; i < entry.value.length; i++)
                  Container(
                    key: widget.keys[entry.value[i]['id']],
                    // 촘촘한 줄(compact, 예: 주제마다 등급 고르기)은 위아래를 좁게. 덧붙임 질문(M6-08 → M6-08-why)은
                    // 앞 질문과 한 덩어리로(사이 줄 없이 바짝) 붙인다.
                    padding: EdgeInsets.only(
                      top: _followsPrevious(entry.value, i) ? 0 : (entry.value[i]['compact'] == true ? 12 : 20),
                      bottom: _followsPrevious(entry.value, i + 1) ? 12 : (entry.value[i]['compact'] == true ? 12 : 20),
                    ),
                    decoration: i == entry.value.length - 1 || _followsPrevious(entry.value, i + 1)
                        ? null
                        : const BoxDecoration(border: Border(bottom: BorderSide(color: AppColors.line))),
                    child: _question(entry.value[i], follow: _followsPrevious(entry.value, i)),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 26),
        ],
      ],
    );
  }

  /// [i] 번째 질문이 앞 질문에 덧붙는 질문인가(id 가 '앞 질문 id-why', 예: M6-08 → M6-08-why '왜 떠오르나요?').
  static bool _followsPrevious(List<Map<String, dynamic>> questions, int i) =>
      i > 0 && i < questions.length && questions[i]['id'] == '${questions[i - 1]['id']}-why';

  Widget _question(Map<String, dynamic> q, {bool follow = false}) {
    final id = q['id'] as String;
    final type = q['type'] as String;
    final options = [for (final o in (q['options'] as List? ?? const [])) o as String];
    // 선택지 보이는 글(labels: 저장 값 → 보이는 글). 저장·비교는 늘 원래 값으로 한다.
    final labels = {
      for (final e in ((q['labels'] as Map?) ?? const {}).entries) e.key as String: e.value as String,
    };
    String label(String option) => labels[option] ?? option;
    final allowOther = q['other'] == true;
    final skip = AnswersForm.skipOf(q);
    // '기억이 안 나요' 등을 골랐다: 답은 그 값이고, 칸은 비우고 잠근다
    final skipped = skip != null && _answers[id] == skip.value;
    final value = skipped ? null : _answers[id];

    Widget body;
    switch (type) {
      case 'single':
        final custom = value is String && !options.contains(value);
        final shown = custom || (value == null && !skipped && _text['$id:other'] != null)
            ? (allowOther ? '직접 입력' : null)
            : value;
        body = Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            ChoiceGroup(
              options: [...options, if (allowOther) '직접 입력'],
              label: label,
              value: shown,
              onChanged: (v) {
                if (v == shown && q['required'] != true) {
                  // 선택 질문은 고른 답을 다시 누르면 지운다(답하지 않은 것으로)
                  _text.remove('$id:other')?.dispose();
                  _text.remove('$id:detail')?.dispose();
                  _answers.remove('$id:detail');
                  _set(id, null);
                } else if (v == '직접 입력') {
                  _text.putIfAbsent('$id:other', () => TextEditingController(text: custom ? value : ''));
                  _set(id, _text['$id:other']!.text);
                } else {
                  _text.remove('$id:other')?.dispose();
                  _set(id, v);
                }
              },
            ),
            if (allowOther && (custom || _text['$id:other'] != null)) ...[
              const SizedBox(height: 10),
              Builder(builder: (context) {
                final other = _text.putIfAbsent('$id:other', () => TextEditingController(text: custom ? value : ''));
                return _voice(
                  other,
                  TextField(
                    controller: other,
                    decoration: const InputDecoration(hintText: '직접 적어주세요'),
                    onChanged: (v) => _set(id, v),
                  ),
                  (v) => _set(id, v),
                );
              }),
            ],
            if (q['detail'] != null && value != null && value != options.first) ...[
              const SizedBox(height: 10),
              Builder(builder: (context) {
                final detail =
                    _text.putIfAbsent('$id:detail', () => TextEditingController(text: _answers['$id:detail'] ?? ''));
                return _voice(
                  detail,
                  TextField(
                    controller: detail,
                    decoration: InputDecoration(hintText: q['detail']['placeholder']),
                    onChanged: (v) => _set('$id:detail', v),
                  ),
                  (v) => _set('$id:detail', v),
                );
              }),
            ],
          ],
        );
      case 'multi':
        final list = _list(id);
        final max = q['max'] as int?;
        final customs = list.where((x) => !options.contains(x));
        body = Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final o in options)
                  Pill(
                    label: label(o),
                    selected: list.contains(o),
                    onTap: () {
                      if (list.contains(o)) {
                        _set(id, list..remove(o));
                      } else if (max != null && list.length >= max) {
                        showMessage(context, '최대 $max개까지 고를 수 있어요.');
                      } else {
                        _set(id, list..add(o));
                      }
                    },
                  ),
                for (final c in customs) _tag(c, () => _set(id, list..remove(c))),
              ],
            ),
            if (allowOther)
              _addRow(id, '목록에 없으면 적어서 더하기', enabled: !skipped, (v) {
                if (max != null && list.length >= max) {
                  showMessage(context, '최대 $max개까지 고를 수 있어요.');
                } else if (!list.contains(v)) {
                  _set(id, list..add(v));
                }
              }),
          ],
        );
      case 'phrases':
        final list = _list(id);
        // 서버가 받는 개수(max)를 넘지 않게 한다. 정하지 않았으면 10개.
        final cap = math.min((q['max'] as int?) ?? 10, 10);
        final full = '최대 $cap개까지 넣을 수 있어요.';
        // 녹음에서 찾은 말 후보는 서버가 suggest 를 붙인 문항(입버릇·말끝)에만 보인다
        final unused = q['suggest'] == true ? _suggestions.where((s) => !list.contains(s)).toList() : const <String>[];
        body = Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: list.isEmpty
                  ? [const Text('아직 없어요', style: TextStyle(color: AppColors.muted))]
                  : [for (final p in list) _tag('“$p”', () => _set(id, list..remove(p)))],
            ),
            _addRow(id, q['placeholder'] ?? '', enabled: !skipped, (v) {
              if (list.length >= cap) {
                showMessage(context, full);
              } else if (!list.contains(v)) {
                _set(id, list..add(v));
              }
            }),
            if (unused.isNotEmpty) ...[
              const Padding(
                padding: EdgeInsets.only(top: 14, bottom: 8),
                child: Text('녹음에서 찾은 말 · 누르면 더해져요', style: TextStyle(color: AppColors.muted, fontSize: 13)),
              ),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final s in unused)
                    Pill(
                      label: '+ $s',
                      tint: AppColors.claySoft,
                      onTap: () => list.length >= cap ? showMessage(context, full) : _set(id, list..add(s)),
                    ),
                ],
              ),
            ],
          ],
        );
      case 'family':
        body = FamilyRows(
          question: q,
          basics: widget.basics,
          rows: [
            for (final row in (value is List ? value : const []))
              if (row is Map) Map<String, dynamic>.from(row),
          ],
          onChanged: (rows) => _set(id, rows),
          transcribe: _transcribe,
        );
      default: // text, textarea
        final controller = _text.putIfAbsent(id, () => TextEditingController(text: value is String ? value : ''));
        body = _voice(
          controller,
          TextField(
            controller: controller,
            enabled: !skipped,
            maxLength: q['maxLength'] as int?,
            minLines: type == 'textarea' ? 4 : 1,
            maxLines: type == 'textarea' ? 8 : 1,
            decoration: InputDecoration(hintText: skipped ? skip.label : q['placeholder'], counterText: ''),
            onChanged: (v) => _set(id, v),
          ),
          (v) => _set(id, v),
          enabled: !skipped,
        );
    }

    if (skip != null) {
      body = Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          body,
          const SizedBox(height: 10),
          Align(
            alignment: Alignment.centerLeft,
            child: Pill(
              label: skip.label,
              selected: skipped,
              icon: skipped ? Icons.check_rounded : Icons.remove_circle_outline_rounded,
              onTap: () => _toggleSkip(id, skip.value),
            ),
          ),
        ],
      );
    }

    // 촘촘한 줄(compact)·덧붙임 질문(follow)은 제목을 작게 하고 칸을 바로 붙인다
    final compact = q['compact'] == true || follow;
    final title = (q['title'] ?? q['label'] ?? '') as String;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text.rich(
          TextSpan(
            text: keepAll(title),
            style: TextStyle(
              fontSize: compact ? 15 : 16,
              fontWeight: compact ? FontWeight.w600 : FontWeight.w700,
              height: 1.45,
              color: follow ? AppColors.ink2 : null,
            ),
            children: [
              // 덧붙임 질문은 제목에 '(선택)'이 들어 있어 표시를 따로 달지 않는다
              if (_mixed && !follow)
                WidgetSpan(alignment: PlaceholderAlignment.middle, child: _badge(q['required'] == true)),
            ],
          ),
        ),
        if (q['hint'] != null)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(keepAll(q['hint'] as String), style: const TextStyle(color: AppColors.muted, fontSize: 13)),
          ),
        SizedBox(height: compact ? 8 : 12),
        body,
      ],
    );
  }

  Widget _tag(String label, VoidCallback onRemove) {
    return Container(
      padding: const EdgeInsets.only(left: 13, right: 4),
      decoration: BoxDecoration(color: AppColors.primarySoft, borderRadius: BorderRadius.circular(999)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(label, style: const TextStyle(color: AppColors.primaryDeep, fontWeight: FontWeight.w600)),
          IconButton(
            visualDensity: VisualDensity.compact,
            iconSize: 16,
            tooltip: '빼기',
            onPressed: onRemove,
            icon: const Icon(Icons.close_rounded, color: AppColors.primaryDeep),
          ),
        ],
      ),
    );
  }

  /// 적어서 더하는 칸(말 목록·목록에 없는 답). 🎤 로 말하면 칸에 넣고, '더하기'를 누르거나 저장할 때 답에 넣는다.
  /// [enabled] 가 false 면('기억이 안 나요'를 골랐다) 칸과 단추를 잠근다.
  Widget _addRow(String id, String hint, ValueChanged<String> onAdd, {bool enabled = true}) {
    final controller = _text.putIfAbsent('$id:add', TextEditingController.new);
    void add() {
      final v = controller.text.trim();
      if (v.isEmpty) return;
      onAdd(v);
      controller.clear();
    }

    _adders[id] = add;

    return Padding(
      padding: const EdgeInsets.only(top: 10),
      child: Row(
        // 🎤 가 있으면 녹음 중 안내가 칸 아래로 늘어나니 위에 맞춘다
        crossAxisAlignment: _transcribe == null ? CrossAxisAlignment.center : CrossAxisAlignment.start,
        children: [
          Expanded(
            child: _voice(
              controller,
              TextField(
                controller: controller,
                enabled: enabled,
                decoration: InputDecoration(hintText: hint, isDense: true),
                onSubmitted: (_) => add(),
                // 적기만 해도 바뀐 것으로 친다(저장할 때 commitPending 으로 답에 넣는다)
                onChanged: (_) => widget.onChanged(),
              ),
              (_) => widget.onChanged(),
              enabled: enabled,
            ),
          ),
          const SizedBox(width: 8),
          Padding(
            // 🎤 단추(52)와 높이를 맞춘다
            padding: EdgeInsets.only(top: _transcribe == null ? 0 : 4),
            child: OutlinedButton(onPressed: enabled ? add : null, child: const Text('더하기')),
          ),
        ],
      ),
    );
  }
}
