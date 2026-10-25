import 'dart:async';

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../shared/widgets/ui.dart';
import '../share/roles.dart';
import 'data/persona_api.dart';
import 'data/persona_store.dart';
import 'imported.dart';
import 'widgets/answers_form.dart';
import 'widgets/basics_form.dart';
import '../../shared/korean.dart';

/// 정보 고치기: 처음에 적은 기본 정보와 질문 답을 고친다. 녹음·목소리는 그대로.
/// 다 만든 페르소나면 저장할 때 서버가 지침을 뒤에서 다시 만든다(그동안에도 대화는 된다).
///
/// [section] 이 [familySection] 이면 '가족 알려 주기': 가족 표(M2-01)만 보이고, 저장·취소하면
/// 기억 더 채우기로 돌아간다(그 화면의 '가족을 알려 주세요' 카드가 연다).
class PersonaEditPage extends StatefulWidget {
  final String personaId;

  /// 한 부분만 연다(주소의 ?section=). 없거나 이 대화 상대에 그 부분이 없으면 전체 정보 고치기.
  final String? section;

  const PersonaEditPage({super.key, required this.personaId, this.section});

  static const familySection = 'family';

  /// 가족 알려 주기 주소.
  static String familyPath(String personaId) => '/p/$personaId/edit?section=$familySection';

  @override
  State<PersonaEditPage> createState() => _PersonaEditPageState();
}

class _PersonaEditPageState extends State<PersonaEditPage> {
  Map<String, dynamic>? _detail;
  Map<String, dynamic>? _meta;
  BasicsDraft? _draft;
  Map<String, dynamic> _answers = {};
  List<Map<String, dynamic>> _questions = [];
  Map<String, String> _leads = {};
  List<String> _suggestions = [];
  final Map<String, GlobalKey> _keys = {};
  final _form = GlobalKey<AnswersFormState>();
  Object? _loadError;
  String? _error;
  bool _busy = false;
  Timer? _jobTimer;

  String get _id => widget.personaId;
  bool get _ready => _detail?['step'] == 'READY';

  /// 가족 표 문항. 묶음 이름이 대화 상대마다 달라(가족·고인은 '나와 그분', 나 자신은 '가족') 문항으로 찾는다.
  static const _familyId = 'M2-01';

  /// 기억 더 채우기의 가족 카드에서 왔다(저장·취소하면 그리로 돌아간다).
  bool get _fromMore => widget.section == PersonaEditPage.familySection;

  /// 가족 알려 주기: 다 만든 대화 상대이고 질문에 가족 표가 있을 때만. 아니면 전체 화면.
  bool get _familyOnly => _fromMore && _ready && _questions.any((q) => q['id'] == _familyId);

  /// 이 화면에 보이는(저장할) 문항. 가족 알려 주기는 가족 표 하나(묶음 이름·안내 없이).
  List<Map<String, dynamic>> get _shown => _familyOnly
      ? [
          for (final q in _questions)
            if (q['id'] == _familyId) {...q, 'section': ''},
        ]
      : _questions;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _jobTimer?.cancel();
    _draft?.dispose();
    super.dispose();
  }

  Map? _job(String kind) => (_detail?['jobs'] as Map?)?[kind] as Map?;
  /// 지침을 다시 만드는 일(UPDATE·EXTEND)의 진행률 글(' · 40%'). 모르면 빈칸.
  String get _percentText {
    for (final kind in const ['UPDATE', 'EXTEND']) {
      final job = _job(kind);
      if (job?['status'] == 'RUNNING' && job?['percent'] != null) return ' · ${job!['percent']}%';
    }
    return '';
  }

  /// 만드는 중이거나 앞서 고친 내용·더한 녹음을 반영하는 중(그동안 서버가 저장을 받지 않는다)
  bool get _waiting =>
      _detail?['step'] == 'BUILDING' ||
      _job('UPDATE')?['status'] == 'RUNNING' ||
      _job('EXTEND')?['status'] == 'RUNNING';

  /// 기다리는 동안 몇 초마다 작업 상태만 다시 읽는다(고치던 내용은 건드리지 않는다).
  void _watchJobs() {
    _jobTimer?.cancel();
    if (!_waiting) return;
    _jobTimer = Timer(const Duration(seconds: 4), _refreshJobs);
  }

  Future<void> _refreshJobs() async {
    try {
      final fresh = await PersonaApi.detail(_id);
      if (!mounted || _detail == null) return;
      setState(() => _detail = {..._detail!, 'jobs': fresh['jobs'], 'step': fresh['step']});
    } catch (_) {
      // 잠깐 못 읽어도 다음 차례에 다시 읽는다
    }
    if (mounted) _watchJobs();
  }

  Future<void> _openMore() async {
    await context.push('/p/$_id/more');
    // 거기서 저장했으면 반영하는 중이다 — 저장 단추와 안내를 맞춘다
    if (mounted) await _refreshJobs();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([PersonaApi.detail(_id), PersonaApi.meta()]);
      final detail = results[0];
      // 이 대화 상대의 질문(필수 묶음이 대화 상대마다 다르다 — 개편 뒤 만든 가족·예전 것·나 자신)
      final meta = PersonaApi.forPersona(results[1], detail);
      if (!mounted) return;
      setState(() {
        _detail = detail;
        _meta = meta;
        _draft = BasicsDraft(
          basics: Map<String, dynamic>.from(detail['basics'] ?? {}),
          relations: (meta['relations'] as List).cast<String>(),
        );
        _answers = Map<String, dynamic>.from(detail['answers'] ?? {});
        // 만들 때 화면처럼 민감한 범주(종교·건강) 질문은 동의했을 때만 보인다. 빠진 질문은 저장할 때
        // ids 에도 없어 서버 값을 그대로 둔다. 돌아가신 분만 묻는 질문은 AnswersForm 이 basics 로 거른다.
        final consents = detail['consents'] as Map?;
        _questions = [
          for (final q in meta['questions'] as List)
            if (AnswersForm.consented(q as Map, consents)) Map<String, dynamic>.from(q),
        ];
        _leads = PersonaApi.sectionLeads(meta);
        for (final q in _questions) {
          _keys[q['id']] = GlobalKey();
        }
      });
      _watchJobs();
      // 함께하는 가족은 이 화면을 쓰지 않는다(막힌 안내만 보인다)
      if (detail['step'] == 'READY' && !isMember(detail)) {
        final phrases = await PersonaApi.phraseSuggestions(_id).catchError((_) => <String>[]);
        if (mounted) setState(() => _suggestions = phrases);
      }
    } catch (error) {
      if (mounted) setState(() => _loadError = error);
    }
  }

  void _close() {
    if (_fromMore) {
      // 기억 더 채우기를 새로 열어 가족 카드가 지금 답대로 보이게 한다
      context.go('/p/$_id/more');
    } else if (context.canPop()) {
      context.pop();
    } else {
      context.go('/p/$_id');
    }
  }

  Future<void> _save() async {
    _form.currentState?.commitPending(); // 적어 두고 '더하기'를 누르지 않은 말도 넣는다
    final shown = _shown;
    if (_ready && !AnswersForm.checkRequired(context, shown, _answers, _keys, basics: _draft!.toJson())) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      // 이 화면의 문항만 바꾼다('기억 더 채우기'에서 저장한 답은 서버 값을 둔다).
      // 가족 알려 주기는 기본 정보 칸이 없어 서버 값을 그대로 둔다
      final saved = _ready
          ? await PersonaApi.updateAnswers(
              _id,
              basics: _familyOnly ? null : _draft!.toJson(),
              answers: _answers,
              ids: [for (final q in shown) q['id'] as String],
            )
          : await PersonaApi.update(_id, _draft!.toJson());
      personaStore.load();
      notifyPersonaEdited(_id);
      if (!mounted) return;
      showMessage(
        context,
        saved['updating'] == true ? '저장했어요. 고친 내용을 익히는 중이에요. 그동안에도 대화할 수 있어요.' : '저장했어요.',
      );
      _close();
    } catch (error) {
      // 안내는 긴 화면 맨 아래에 그려진다. 아래에 붙은 '저장하기'를 누른 자리에서도 보이게 스낵바로도 알린다
      if (mounted) {
        setState(() => _error = error.toString());
        showMessage(context, error);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    if (detail == null || _draft == null) {
      return Scaffold(
        appBar: AppBar(),
        body: _loadError != null
            ? LoadFailed(_loadError!, onRetry: () {
                setState(() => _loadError = null);
                _load();
              })
            : const Loading(),
      );
    }
    // 가져온 대화 상대는 고치면 예전 지침을 덮어써서 쓰지 않는다(서버도 막는다).
    // 함께하는 가족은 그분 정보를 고치지 않는다(만든 사람만)
    final blocked = PersonaBlocked.of(detail, personaId: _id);
    if (blocked != null) return blocked;
    final building = detail['step'] == 'BUILDING';
    final updating = _waiting;
    final failed = _ready && !updating && _job('UPDATE')?['status'] == 'FAILED';
    final family = _familyOnly;
    final page = WizardScaffold(
      title: family ? '기억 더 채우기' : '정보 고치기',
      headline: family ? '가족 알려 주기' : '${personaTitle(detail)} 정보 고치기',
      lead: family
          ? '아는 가족만 적어 주세요. 저장하면 1~2분 동안 익혀요. 그동안에도 대화할 수 있어요.'
          : _ready
              ? '처음에 적어 주신 내용을 고칠 수 있어요. 저장하면 1~2분 동안 익혀요. 그동안에도 대화할 수 있어요.'
              : '처음에 적어 주신 내용을 고칠 수 있어요. 올린 녹음과 고른 목소리는 그대로예요.',
      secondary: TextButton(onPressed: _close, child: const Text('취소')),
      primary: FilledButton(
        onPressed: _busy || building || updating ? null : _save,
        child: Text(_busy ? '잠시만요…' : '저장하기'),
      ),
      children: [
        if (building || updating) ...[
          Notice(
            building
                ? '아직 만드는 중이에요. 다 만든 뒤에 고칠 수 있어요.'
                : '새 내용을 익히는 중이에요$_percentText. 끝나면 다시 고칠 수 있어요.',
            icon: Icons.hourglass_top_rounded,
          ),
          const SizedBox(height: 18),
        ] else if (failed) ...[
          ErrorBox("앞서 고친 내용을 익히지 못했어요. '저장하기'를 누르면 다시 익혀요."
              "${_job('UPDATE')?['error'] is String ? ' (${_job('UPDATE')!['error']})' : ''}"),
          const SizedBox(height: 18),
        ],
        // 가족 알려 주기는 가족 표만(기본 정보·다른 화면으로 가는 단추 없이)
        if (!family) ...[
          BasicsForm(
            draft: _draft!,
            meta: _meta!,
            onChanged: () => setState(() {}),
            // '그분이 나를 부르던 말'(P1-07A)도 말로 적을 수 있게
            transcribe: (clip) => PersonaApi.transcribeAnswer(_id, clip),
          ),
          if (_ready) const SizedBox(height: 28),
        ],
        if (_ready)
          AnswersForm(
            key: _form,
            questions: _shown,
            answers: _answers,
            keys: _keys,
            suggestions: _suggestions,
            onChanged: () {},
            basics: _draft!.toJson(),
            sectionLeads: _leads,
            personaId: _id,
          ),
        const SizedBox(height: 8),
        if (!family) ...[
          if (_ready) ...[
            OutlinedButton.icon(
              onPressed: _openMore,
              style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(50)),
              icon: const Icon(Icons.quiz_outlined, size: 20),
              label: const Text('기억 더 채우기 — 인생 이야기·일상 속 기억 (선택)'),
            ),
            const SizedBox(height: 10),
          ],
          OutlinedButton.icon(
            onPressed: () => context.push('/p/${widget.personaId}/materials'),
            style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(50)),
            icon: const Icon(Icons.note_add_outlined, size: 20),
            label: const Text('남긴 글 더하기 (카카오톡·문자·일기)'),
          ),
        ],
        if (_error != null) ...[const SizedBox(height: 4), ErrorBox(_error!)],
      ],
    );
    if (!_fromMore) return page;
    // 위 뒤로 가기·휴대폰 뒤로 가기도 취소처럼 기억 더 채우기로 돌아간다
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && !_busy) _close();
      },
      child: page,
    );
  }
}
