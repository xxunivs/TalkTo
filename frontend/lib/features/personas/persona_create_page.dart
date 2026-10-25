import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/session.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import '../billing/data/billing_api.dart';
import 'data/persona_api.dart';
import 'data/persona_store.dart';
import 'widgets/basics_form.dart';

/// 1단계: 누구와 이야기 나눌지(새 대화 상대). 녹음에서 그분 목소리를 찾고 말투를 맞추는 데 쓴다.
class PersonaCreatePage extends StatefulWidget {
  const PersonaCreatePage({super.key});

  @override
  State<PersonaCreatePage> createState() => _PersonaCreatePageState();
}

class _PersonaCreatePageState extends State<PersonaCreatePage> {
  final _draft = BasicsDraft(myNameDefault: session.name);

  @override
  void initState() {
    super.initState();
    // 체험 자리가 남았는지 새로 본다(만드는 중인 페르소나가 먼저 받는다)
    billingStore.load().then((_) {
      if (mounted) setState(() {});
    });
  }
  bool _busy = false;
  String? _error;
  late Future<Map<String, dynamic>> _meta = PersonaApi.meta();

  @override
  void dispose() {
    _draft.dispose();
    super.dispose();
  }

  /// 만들지 못한 까닭. 버튼은 화면 아래에 붙어 있고 안내는 긴 칸 맨 아래에 그려져서, 스낵바로도 보인다
  /// (누른 자리에서 아무 일도 없어 보이지 않게).
  void _fail(Object error) {
    if (!mounted) return;
    setState(() => _error = error.toString());
    showMessage(context, error);
  }

  Future<void> _submit() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final created = await PersonaApi.create(_draft.toJson());
      personaStore.load();
      if (mounted) context.go('/p/${created['id']}');
    } catch (error) {
      _fail(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return FutureBuilder(
      future: _meta,
      builder: (context, snapshot) {
        if (!snapshot.hasData) {
          return Scaffold(
            appBar: AppBar(),
            body: snapshot.hasError
                ? LoadFailed(snapshot.error!, onRetry: () => setState(() {
                    _meta = PersonaApi.meta();
                  }))
                : const Loading(),
          );
        }
        final blocked = _draft.createBlock(snapshot.data!);
        return WizardScaffold(
          title: '새 대화 상대',
          step: 0,
          headline: '누구와 이야기 나누고 싶으세요?',
          lead: '적어 주신 내용은 녹음에서 그분 목소리를 찾고, 말투와 부르는 말을 맞추는 데 써요. 나중에 언제든 고칠 수 있어요.',
          secondary: TextButton(onPressed: () => context.go('/'), child: const Text('취소')),
          primary: FilledButton(
            // 만 14세 미만인데 아직 받지 않으면 만들지 않는다(안내는 기본 정보 칸 안에)
            onPressed: _busy || blocked != null ? null : _submit,
            child: Text(_busy ? '잠시만요…' : '다음: 녹음 올리기'),
          ),
          children: [
            // 결제가 열리기 전에는 무료 체험에 한도가 없다
            if (billingStore.prelaunch && billingStore.me?['admin'] != true) ...[
              Notice(
                billingStore.me?['prelaunchConversations'] == null
                    ? '결제가 열리기 전까지는 무료로, 횟수 제한 없이 쓸 수 있어요.'
                    : '결제가 열리기 전까지는 무료로 쓸 수 있어요'
                        '(대화 상대마다, 다 만든 날부터 30일마다 대화 ${billingStore.me?['prelaunchConversations']}번까지).',
                icon: Icons.card_giftcard_rounded,
                color: AppColors.primary,
                background: AppColors.primarySoft,
              ),
              const SizedBox(height: 18),
            ]
            // 체험은 페르소나마다지만 계정에 상한이 있다. 다 썼으면 미리 알린다.
            else if (billingStore.me?['trialsAvailableForNew'] == 0 && billingStore.me?['admin'] != true) ...[
              Notice(
                '무료 체험은 계정 하나에 대화 상대 ${billingStore.me?['trialPersonasPerAccount'] ?? 1}명까지예요. '
                '이번 대화 상대는 다 만든 뒤 요금제를 따로 고르면 대화할 수 있어요. 만드는 건 무료예요. '
                '$noTrialRefundText',
                icon: Icons.info_outline_rounded,
              ),
              const SizedBox(height: 18),
            ],
            BasicsForm(draft: _draft, meta: snapshot.data!, onChanged: () => setState(() {})),
            if (_error != null) ...[const SizedBox(height: 18), ErrorBox(_error!)],
          ],
        );
      },
    );
  }
}
