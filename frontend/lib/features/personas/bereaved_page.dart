import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/ui.dart';
import '../share/roles.dart';
import 'data/persona_api.dart';
import 'data/persona_rules_api.dart';
import 'data/persona_store.dart';
import 'imported.dart';
import 'widgets/bereaved_form.dart';

/// 신청 자격과 유족 명단(/p/:id/bereaved, 만든 분만). 이미 만든 돌아가신 분 대화 상대가 다음에 들어올 때 적는다
/// (새 대화 상대는 만들기 전 확인에서 받는다). 적은 것은 고칠 수 있고, 고치기 전 것도 서버가 남긴다.
class BereavedPage extends StatefulWidget {
  final String personaId;

  const BereavedPage({super.key, required this.personaId});

  @override
  State<BereavedPage> createState() => _BereavedPageState();
}

class _BereavedPageState extends State<BereavedPage> {
  Map<String, dynamic>? _detail;
  Map<String, dynamic> _meta = const {};
  BereavedDraft? _draft;
  Object? _loadError;
  String? _error;
  bool _busy = false;

  String get _id => widget.personaId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _draft?.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final detail = await PersonaApi.detail(_id);
      final saved = isMember(detail) ? null : await BereavedApi.load(_id);
      final meta = await PersonaApi.meta().catchError((Object _) => <String, dynamic>{});
      if (!mounted) return;
      setState(() {
        _detail = detail;
        _meta = meta;
        _draft?.dispose();
        _draft = BereavedDraft(saved);
        _loadError = null;
      });
    } catch (error) {
      if (mounted) setState(() => _loadError = error);
    }
  }

  Future<void> _save() async {
    final draft = _draft!;
    final problem = draft.check();
    if (problem != null) {
      setState(() => _error = problem);
      showMessage(context, problem);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await BereavedApi.save(_id, draft.toJson());
      personaStore.load();
      notifyPersonaEdited(_id);
      if (!mounted) return;
      showMessage(context, '저장했어요.');
      context.go('/p/$_id');
    } catch (error) {
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
    final draft = _draft;
    if (detail == null || draft == null) {
      return Scaffold(
        appBar: AppBar(automaticallyImplyLeading: !Breakpoints.isWide(context)),
        body: _loadError != null
            ? LoadFailed(_loadError!, onRetry: () {
                setState(() => _loadError = null);
                _load();
              })
            : const Loading(),
      );
    }
    final blocked = PersonaBlocked.of(detail, personaId: _id, imported: false);
    if (blocked != null) return blocked;
    final title = personaTitle(detail);
    final deceased = (detail['basics'] as Map?)?['status'] == 'deceased' || detail['lifeStatus'] == 'DECEASED';
    return WizardScaffold(
      title: '유족 확인',
      headline: '신청 자격과 유족 명단',
      lead: '돌아가신 분의 대화 상대는 고인과의 관계와 고인의 배우자·자녀(안 계시면 부모) 명단을 적고 확인하셔야 해요. '
          '사망진단서 같은 서류는 받지 않아요.',
      secondary: TextButton(
        onPressed: () => context.canPop() ? context.pop() : context.go('/p/$_id'),
        child: const Text('돌아가기'),
      ),
      primary: deceased
          ? FilledButton(onPressed: _busy ? null : _save, child: Text(_busy ? '잠시만요…' : '저장하기'))
          : null,
      children: [
        if (!deceased)
          const Notice('돌아가신 분의 대화 상대에서만 적어요.')
        else
          BereavedForm(
            draft: draft,
            meta: _meta,
            title: title,
            onChanged: () {
              if (_error != null) setState(() => _error = null);
            },
          ),
        if (_error != null) ...[const SizedBox(height: 16), ErrorBox(_error!)],
      ],
    );
  }
}
