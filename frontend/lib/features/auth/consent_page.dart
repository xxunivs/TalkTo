import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_client.dart';
import '../../core/session.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import '../legal/account_page.dart';
import '../legal/consent_checks.dart';
import '../legal/legal_api.dart';
import '../legal/legal_footer.dart';
import '../legal/legal_text.dart';
import '../legal/support_page.dart';
import '../share/pending_invite.dart';

/// 첫 로그인(가입) 동의 화면. 계정 필수 동의가 빠져 있으면 라우터가 어디서든 여기로 보낸다(/consent?from=).
/// 모든 필수 동의(녹음 파일 처리·그분 목소리 만들기·해외 회사로 보내기까지)를 여기서 한 번에 받는다.
/// 초대받은 가족도 같다. 서버가 빠졌다고 한 항목만 보인다.
/// 이미 동의했던 회원이 약관이 바뀌어 다시 오면(만 14세 확인은 그대로라 빠진 항목에 없다)
/// 바뀐 항목만 보여 주고, 동의하지 않으면 여기서 바로 회원 탈퇴할 수 있게 한다.
/// 바깥 틀(AppShell) 밖에 있다 — 틀이 읽는 목록·요금제는 동의 전에는 서버가 막기 때문이다.
class ConsentPage extends StatefulWidget {
  /// 동의를 마치면 돌아갈 화면(없으면 첫 화면).
  final String? from;

  /// 동의 항목을 읽는다. 시험에서는 서버 없이 바꿔 끼운다.
  final Future<List<Map<String, dynamic>>> Function() loadItems;

  /// 맨 아래 사업자 정보를 보일지(시험에서는 끈다).
  final bool showBusiness;

  /// 빠진 필수 동의(없으면 지금 로그인한 회원의 것). 시험에서 바꿔 끼운다.
  final List<String>? needed;

  const ConsentPage({
    super.key,
    this.from,
    this.loadItems = LegalApi.accountItems,
    this.showBusiness = true,
    this.needed,
  });

  /// 돌아갈 주소는 이 앱 안의 화면만 받는다.
  static String safeFrom(String? from) {
    if (from == null || !from.startsWith('/') || from.startsWith('//')) return '/';
    if (from.startsWith('/consent') || from.startsWith('/login') || from.startsWith('/auth')) return '/';
    return from;
  }

  @override
  State<ConsentPage> createState() => _ConsentPageState();
}

class _ConsentPageState extends State<ConsentPage> {
  List<Map<String, dynamic>>? _items;
  Object? _loadError;
  Set<String> _checked = {};
  bool _busy = false;
  String? _error;

  /// 이 화면에서 받을 빠진 항목.
  List<String> get _needed => widget.needed ?? session.consentsNeeded;

  /// 예전에 동의했고 약관이 바뀌어 다시 동의하는 회원인지(처음 열 때 한 번 정한다)
  late final bool _returning = _isReturning(_needed);

  static bool _isReturning(List<String> needed) => needed.isNotEmpty && !needed.contains('age14');

  // 초대받은 가족도 읽는다. 만드는 분에게만 맞는 말은 그렇다고 밝힌다
  static const _summary = '- 가족이 남긴 **녹음과 이야기를 바탕으로**, 그분 말투와 목소리로 대답하는 AI 대화 상대를 만드는 '
      '서비스예요.\n'
      '- 대화 상대를 만든 가족은 **가족 공유**로 다른 가족을 초대해, 같은 대화 상대와 함께 이야기할 수 있어요.\n'
      '- 대화 상대의 대답과 목소리는 모두 **AI가 만든 것**이에요. 그분을 되살리거나 대신하는 것이 아니에요.\n'
      '- AI라서 사실과 다른 말을 할 수 있어요. 틀린 점은 대화 상대를 만든 분이 무료로 쓰시는 동안이나 구독 중에 '
      '바로잡을 수 있어요.\n'
      '- 치료·상담 서비스가 아니에요. 마음이 많이 힘드시면 가까운 분이나 전문가에게 꼭 도움을 청해 주세요.';

  @override
  void initState() {
    super.initState();
    LegalApi.forget();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loadError = null);
    try {
      // 서버가 빠졌다고 한 것만(다시 동의할 때는 바뀐 것만)
      final needed = _needed;
      final items = [for (final item in await widget.loadItems()) if (needed.contains(item['type'])) item];
      if (mounted) setState(() => _items = items);
    } catch (error) {
      if (mounted) setState(() => _loadError = error);
    }
  }

  bool get _allChecked => ConsentGroup.allChecked(_items ?? const [], _checked);

  /// 초대한 가족: '지은님'. 이름을 모르면 '받은 가족'.
  static String _inviter() {
    final name = PendingInvite.ownerName?.trim() ?? '';
    return name.isEmpty ? '받은 가족' : '$name님';
  }

  Future<void> _accept() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await session.acceptConsents(_checked.toList());
      if (!mounted) return;
      if (session.needsConsent) {
        // 그사이 문서가 바뀌었으면 남은 항목을 다시 보여 준다(기억해 둔 글도 버리고 새로 받는다)
        LegalApi.forget();
        setState(() {
          _checked = {};
          _error = '확인할 내용이 새로 생겼어요. 다시 한번 살펴봐 주세요.';
        });
        await _load();
      } else {
        // 카카오·구글로 처음 가입하면 /auth → / → /consent 로 와서 from 이 없다. 적어 둔 가족 초대로 이어 간다
        context.go(ConsentPage.safeFrom(widget.from ?? PendingInvite.path));
      }
    } catch (error) {
      final message = error is ApiException ? error.message : '잠시 문제가 생겼어요. 잠시 뒤 다시 해 주세요.';
      if (mounted) setState(() => _error = message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _leave() async {
    // 이 기기에서만 나간다(다른 기기의 로그인은 그대로 둔다)
    setState(() => _busy = true);
    await session.signOutHere();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _withdraw() async {
    final confirmed = await showDialog<bool>(context: context, builder: (_) => const WithdrawDialog());
    if (confirmed != true || !mounted) return;
    setState(() => _busy = true);
    final Map<String, dynamic> result;
    try {
      result = await LegalApi.deleteAccount();
    } catch (error) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = error is ApiException ? error.message : '탈퇴하지 못했어요. 잠시 뒤 다시 해 주세요.';
        });
      }
      return;
    }
    // 지운 곳과 서버 백업이 남는 날(서버 글)을 로그아웃 전에 보여 준다
    if (mounted) await showWithdrawDone(context, result);
    await session.logout();
  }

  @override
  Widget build(BuildContext context) {
    final wide = Breakpoints.isWide(context);
    final items = _items;
    return Scaffold(
      body: SafeArea(
        child: SingleChildScrollView(
          padding: EdgeInsets.fromLTRB(20, wide ? 40 : 20, 20, 40),
          child: PageBody(
            maxWidth: 580,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Align(alignment: Alignment.centerLeft, child: BrandLogo(height: 18)),
                const SizedBox(height: 28),
                if (PendingInvite.path != null) ...[
                  Notice(
                    "${_inviter()}의 초대를 이어 가려면 먼저 아래에 동의해 주세요. "
                    "'모두 동의해요'를 누르면 한 번에 체크돼요.",
                    icon: Icons.mark_email_unread_outlined,
                    color: AppColors.primary,
                    background: AppColors.primarySoft,
                  ),
                  const SizedBox(height: 20),
                ],
                if (_returning)
                  const Headline(
                    '약관과 안내가 바뀌었어요',
                    lead: "바뀐 내용을 확인하고 다시 동의해 주세요. 항목마다 '보기'를 누르면 바뀐 글을 볼 수 있어요.",
                  )
                else
                  const Headline(
                    'TalkTo는 이런 서비스예요',
                    lead: '시작하기 전에 꼭 알아 두실 것과 동의할 내용을 모았어요.',
                  ),
                const SizedBox(height: 18),
                AppCard(
                  padding: const EdgeInsets.fromLTRB(18, 18, 18, 8),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      if (_returning)
                        Text(keepAll('무엇이 왜 바뀌었는지 한곳에 모아 두었어요.'), style: AppText.lead)
                      else
                        const LegalText(_summary),
                      Align(
                        alignment: Alignment.centerRight,
                        child: TextButton(
                          onPressed: () => showLegalDoc(context, _returning ? 'changes' : 'service'),
                          child: Text(_returning ? '바뀐 점 보기' : '자세히 보기'),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 28),
                SectionLabel(_returning ? '계속 쓰시려면 바뀐 내용에 다시 동의해 주세요' : '시작하려면 아래 내용에 동의해 주세요'),
                if (items != null)
                  ConsentGroup(
                    items: items,
                    checked: _checked,
                    enabled: !_busy,
                    onChanged: (next) => setState(() => _checked = next),
                  )
                else if (_loadError != null) ...[
                  ErrorBox(_loadError is ApiException
                      ? _loadError.toString()
                      : '동의 내용을 불러오지 못했어요. 잠시 뒤 다시 해 주세요.'),
                  const SizedBox(height: 8),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: TextButton(onPressed: _load, child: const Text('다시 불러오기')),
                  ),
                ] else
                  const Padding(padding: EdgeInsets.all(24), child: Loading()),
                if (_error != null) ...[const SizedBox(height: 12), ErrorBox(_error!)],
                const SizedBox(height: 22),
                FilledButton(
                  onPressed: _allChecked && !_busy ? _accept : null,
                  child: Text(_busy ? '잠시만요…' : (_returning ? '동의하고 계속하기' : '동의하고 시작하기')),
                ),
                const SizedBox(height: 6),
                TextButton(
                  onPressed: _busy ? null : _leave,
                  child: const Text('동의하지 않고 나가기'),
                ),
                Text(
                  keepAll('나가면 로그아웃돼요. 언제든 다시 들어와 동의할 수 있어요.'),
                  textAlign: TextAlign.center,
                  style: AppText.small,
                ),
                const SizedBox(height: 6),
                // 궁금한 점은 동의하기 전에도 물어볼 수 있다(카카오톡 채널·이메일·문의 코드)
                Center(
                  child: TextButton.icon(
                    onPressed: () => context.push(SupportPage.beforeConsentPath),
                    icon: const Icon(Icons.support_agent_outlined, size: 18),
                    label: const Text('고객센터'),
                  ),
                ),
                if (_returning) ...[
                  const SizedBox(height: 22),
                  Text(
                    keepAll('바뀐 내용에 동의하지 않으시면 회원 탈퇴를 할 수 있어요. 탈퇴하면 만든 대화 상대와 '
                        '올린 녹음·남긴 글·대화 기록을 TalkTo에서 모두 지우고, 법으로 보관해야 하는 결제 기록과 '
                        '이름·이메일을 지운 동의·이용 기록만 남겨요. 지운 자료도 서버 백업에는 최대 7일 남았다가 '
                        '저절로 지워져요. '
                        "녹음을 보낸 해외 회사에 남는 자료와 지우는 때는 '해외 회사로 보내는 정보'에 적어 두었어요. "
                        '$withdrawSharingNote'),
                    textAlign: TextAlign.center,
                    style: AppText.small,
                  ),
                  TextButton(
                    onPressed: _busy ? null : _withdraw,
                    style: TextButton.styleFrom(foregroundColor: AppColors.danger),
                    child: const Text('회원 탈퇴'),
                  ),
                ],
                // 정보를 받는 화면이라 아래에 개인정보 처리방침 링크를 둔다(WO 6)
                const SizedBox(height: 28),
                const LegalFooter(compact: true),
                if (widget.showBusiness) ...[
                  const SizedBox(height: 12),
                  const Divider(height: 1),
                  const SizedBox(height: 16),
                  const BusinessInfo(),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
