import 'package:flutter/material.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_client.dart';
import '../../core/session.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import '../legal/consent_checks.dart';
import '../legal/legal_api.dart';
import '../legal/legal_footer.dart';
import '../legal/legal_text.dart';

/// 소셜 로그인에서 실패해 돌아왔을 때 주소에 붙은 ?error= 를 보일 글로 바꾼다.
/// 서버·로그인 마치는 화면이 붙이는 이름(code)과 지금 서버가 보내는 정해진 문장만 알아보고, 그 밖의 값은
/// 한 가지 글로 보인다. 남이 만든 주소(#/login?error=계정이 정지…)로 TalkTo 안내처럼 아무 글이나
/// 띄우지 못하게 주소의 글을 그대로 보이지 않는다. 값이 없으면 null.
String? loginErrorText(String? raw) {
  if (raw == null || raw.trim().isEmpty) return null;
  const codes = {
    'cancelled': '로그인을 취소했어요.',
    'failed': '로그인에 실패했어요. 잠시 후 다시 시도해 주세요.',
    'not_open': '이 로그인 방법은 아직 쓸 수 없어요. 다른 방법으로 로그인해 주세요.',
    'state': _loginRestart,
    'bad_state': _loginRestart,
    'expired': '로그인 시간이 지났어요. 다시 시도해 주세요.',
    'unavailable': '사용할 수 없는 계정이에요.',
    'blocked': '사용할 수 없는 계정이에요.',
    'refused': '사용할 수 없는 계정이에요.',
    'signup_closed': '지금은 가입을 받지 않아요.',
    'retry': '잠시 후 다시 시도해 주세요.',
    'busy': '잠시 후 다시 시도해 주세요.',
    'network': '연결이 잘 안 돼요. 인터넷이 연결되어 있는지 확인하고 다시 시도해 주세요.',
  };
  final text = raw.trim();
  final known = codes[text];
  if (known != null) return known;
  // 지금 서버(auth.py _back_with_error)가 보내는 정해진 문장은 그대로 보인다
  final legacy = <String>{
    _loginRestart,
    '로그인 시간이 지났어요. 다시 시도해 주세요.',
    '사용할 수 없는 계정이에요.',
    '로그인을 취소했어요.',
    '지금은 가입을 받지 않아요.',
    '잠시 후 다시 시도해 주세요.',
    for (final label in ['카카오', '구글']) ...[
      '$label 로그인에 실패했어요. 잠시 후 다시 시도해 주세요.',
      '$label 로그인은 아직 쓸 수 없어요. 다른 방법으로 로그인해 주세요.',
    ],
  };
  if (legacy.contains(text)) return text;
  return '로그인하지 못했어요. 다시 시도해 주세요.';
}

const _loginRestart = '로그인을 마치지 못했어요. 처음부터 다시 로그인해 주세요.';

/// 로그인. 카카오·구글이 먼저, 이메일은 한 번 더 눌러야 나온다.
/// PC 는 왼쪽에 어두운 소개 판, 모바일은 한 칸.
class LoginPage extends StatefulWidget {
  /// 소셜 로그인에서 실패해 돌아왔을 때 주소에 붙은 값(?error=). 그대로 보이지 않고 [loginErrorText] 로 바꾼다.
  final String? error;

  /// 서버가 켜 둔 로그인 방법을 읽는다. 시험에서는 서버 없이 바꿔 끼운다.
  final Future<Map<String, dynamic>> Function() loadProviders;

  /// 이메일 가입 때 받는 필수 동의 항목을 읽는다. 시험에서는 서버 없이 바꿔 끼운다.
  final Future<List<Map<String, dynamic>>> Function() loadConsentItems;

  /// 아래 사업자 정보를 읽는다. 시험에서는 서버 없이 바꿔 끼운다.
  final Future<Map<String, dynamic>> Function() loadBusiness;

  const LoginPage({
    super.key,
    this.error,
    this.loadProviders = Session.providers,
    this.loadConsentItems = LegalApi.accountItems,
    this.loadBusiness = LegalApi.business,
  });

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  Map<String, dynamic> _providers = const {};
  bool _email = false;

  @override
  void initState() {
    super.initState();
    widget.loadProviders().then((p) {
      if (mounted) setState(() => _providers = p);
    });
  }

  void _social(String provider, String label) {
    if (_providers[provider] != true) {
      showMessage(context, '$label 로그인은 아직 쓸 수 없어요. 지금은 이메일로 들어와 주세요.');
      return;
    }
    session.startSocialLogin(provider);
  }

  @override
  Widget build(BuildContext context) {
    final wide = Breakpoints.isWide(context);
    final panel = AnimatedSwitcher(
      duration: const Duration(milliseconds: 220),
      child: _email
          ? _EmailForm(
              key: const ValueKey('email'),
              loadConsentItems: widget.loadConsentItems,
              onBack: () => setState(() => _email = false),
            )
          : _SocialButtons(
              key: const ValueKey('social'),
              error: loginErrorText(widget.error),
              onKakao: () => _social('kakao', '카카오'),
              onGoogle: () => _social('google', 'Google'),
              onEmail: () => setState(() => _email = true),
            ),
    );

    if (wide) {
      return Scaffold(
        body: Row(
          children: [
            const Expanded(flex: 11, child: _BrandPanel()),
            Expanded(
              flex: 9,
              child: Center(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.all(32),
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 380),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text('시작하기', style: AppText.display.copyWith(fontSize: 26)),
                        const SizedBox(height: 8),
                        const Text('처음이면 자동으로 가입돼요.', style: AppText.lead),
                        const SizedBox(height: 28),
                        panel,
                        const SizedBox(height: 24),
                        // 앱의 첫 화면이라 사업자 정보를 글로 그대로 보인다(전자상거래법 제10조 제1항)
                        LegalFooter(loadBusiness: widget.loadBusiness),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      );
    }

    return Scaffold(
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, constraints) => SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(24, 20, 24, 24),
            child: ConstrainedBox(
              constraints: BoxConstraints(minHeight: constraints.maxHeight - 44),
              child: IntrinsicHeight(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const Align(alignment: Alignment.centerLeft, child: BrandLogo(height: 18)),
                    const SizedBox(height: 40),
                    Text('가장 듣고 싶은 목소리,\n그 사람다움 그대로', style: AppText.display.copyWith(fontSize: 27)),
                    const SizedBox(height: 12),
                    const Text('목소리도, 말투도, 기억도.\n가족과 다시 이야기 나눠 보세요.', style: AppText.lead),
                    if (!_email) ...[
                      const SizedBox(height: 28),
                      const _SampleChat(dark: false),
                    ],
                    const Spacer(),
                    const SizedBox(height: 28),
                    panel,
                    const SizedBox(height: 18),
                    LegalFooter(loadBusiness: widget.loadBusiness),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _SocialButtons extends StatelessWidget {
  final String? error;
  final VoidCallback onKakao;
  final VoidCallback onGoogle;
  final VoidCallback onEmail;

  const _SocialButtons({
    super.key,
    this.error,
    required this.onKakao,
    required this.onGoogle,
    required this.onEmail,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (error != null) ...[ErrorBox(error!), const SizedBox(height: 16)],
        SocialLoginButton.kakao(onPressed: onKakao),
        const SizedBox(height: 10),
        SocialLoginButton.google(onPressed: onGoogle),
        const SizedBox(height: 14),
        TextButton(onPressed: onEmail, child: const Text('이메일로 계속하기')),
      ],
    );
  }
}

/// 카카오·구글 로그인 단추(로그인 화면과 가족 초대 화면이 같이 쓴다).
class SocialLoginButton extends StatelessWidget {
  final VoidCallback onPressed;
  final Color background;
  final Color foreground;
  final Color? border;
  final Widget icon;
  final String label;
  final double height;

  const SocialLoginButton({
    super.key,
    required this.onPressed,
    required this.background,
    required this.foreground,
    this.border,
    required this.icon,
    required this.label,
    this.height = 54,
  });

  SocialLoginButton.kakao({super.key, required this.onPressed, this.height = 54})
      : background = AppColors.kakao,
        foreground = const Color(0xD9000000),
        border = null,
        icon = SvgPicture.asset('assets/brand/kakao_symbol.svg', width: 20, height: 20),
        label = '카카오로 시작하기';

  SocialLoginButton.google({super.key, required this.onPressed, this.height = 54})
      : background = Colors.white,
        foreground = const Color(0xFF1F1F1F),
        border = const Color(0xFFC9C6BE),
        icon = SvgPicture.asset('assets/brand/google_g.svg', width: 18, height: 18),
        label = 'Google로 계속하기';

  @override
  Widget build(BuildContext context) {
    return Material(
      color: background,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: border == null ? BorderSide.none : BorderSide(color: border!),
      ),
      child: InkWell(
        onTap: onPressed,
        borderRadius: BorderRadius.circular(14),
        child: SizedBox(
          height: height,
          child: Stack(
            alignment: Alignment.center,
            children: [
              Positioned(left: 20, child: icon),
              Text(label, style: TextStyle(color: foreground, fontSize: 16, fontWeight: FontWeight.w700)),
            ],
          ),
        ),
      ),
    );
  }
}

/// 작은 글 링크: 이용약관 · 환불 정책 · 개인정보 처리방침 · 사업자 정보(가족 초대 화면). 로그인 화면은 [LegalFooter].
class LegalLinks extends StatelessWidget {
  const LegalLinks({super.key});

  @override
  Widget build(BuildContext context) {
    Widget link(String label, VoidCallback onTap, {bool strong = false}) => InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(6),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 6),
            child: Text(
              label,
              style: AppText.small.copyWith(
                fontSize: 12.5,
                color: strong ? AppColors.ink : AppColors.ink2,
                fontWeight: strong ? FontWeight.w700 : null,
              ),
            ),
          ),
        );
    // 좁은 휴대폰에서는 줄을 넘긴다. 구분점 대신 간격을 둬 줄 끝에 '·' 가 홀로 남지 않게 한다
    return Wrap(
      alignment: WrapAlignment.center,
      crossAxisAlignment: WrapCrossAlignment.center,
      spacing: 10,
      children: [
        link('이용약관', () => context.push('/legal/terms')),
        link('환불 정책', () => context.push('/legal/refund')),
        // 개인정보 처리방침은 다른 안내와 구별되게 굵게 보인다(표준 개인정보 보호지침 제20조)
        link('개인정보 처리방침', () => context.push('/legal/privacy_policy'), strong: true),
        link('사업자 정보', () => showBusinessInfo(context)),
      ],
    );
  }
}

class _EmailForm extends StatefulWidget {
  final VoidCallback onBack;
  final Future<List<Map<String, dynamic>>> Function() loadConsentItems;

  const _EmailForm({super.key, required this.onBack, required this.loadConsentItems});

  @override
  State<_EmailForm> createState() => _EmailFormState();
}

class _EmailFormState extends State<_EmailForm> {
  bool _signup = false;
  bool _busy = false;
  String? _error;
  final _name = TextEditingController();
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _password2 = TextEditingController();

  /// 가입 때 받는 필수 동의(GET /legal 의 account, 모두). 못 읽으면 가입은 막지 않고,
  /// 가입한 뒤 동의 화면에서 받는다(서버 게이트가 그리로 보낸다).
  List<Map<String, dynamic>>? _consentItems;
  bool _consentLoadFailed = false;
  Set<String> _agreed = {};

  @override
  void initState() {
    super.initState();
    widget.loadConsentItems().then((items) {
      if (mounted) setState(() => _consentItems = items);
    }, onError: (Object _) {
      if (mounted) setState(() => _consentLoadFailed = true);
    });
  }

  /// 가입 버튼을 켤 수 있는지: 동의 항목을 다 체크했거나, 항목을 읽지 못한 경우.
  bool get _consentsReady =>
      _consentLoadFailed || ConsentGroup.allChecked(_consentItems ?? const [], _agreed);

  @override
  void dispose() {
    for (final c in [_name, _email, _password, _password2]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _submit() async {
    // 보내는 중에 Enter 를 또 누르면(비밀번호 칸) 두 번 보내지 않는다
    if (_busy) return;
    setState(() => _error = null);
    final email = _email.text.trim();
    final password = _password.text;
    if (_signup) {
      if (_name.text.trim().isEmpty) return setState(() => _error = '이름을 적어 주세요.');
      if (password.length < 8) return setState(() => _error = '비밀번호는 8자 이상이어야 해요.');
      if (password != _password2.text) return setState(() => _error = '비밀번호 두 칸이 서로 달라요.');
      if (!_consentsReady) return setState(() => _error = '필수 항목에 모두 동의해 주세요.');
    }
    if (!email.contains('@')) return setState(() => _error = '이메일 주소를 다시 확인해 주세요.');
    setState(() => _busy = true);
    try {
      if (_signup) {
        await session.signup(email, password, _name.text.trim(), agreements: _agreed.toList());
      } else {
        await session.login(email, password);
      }
    } catch (error) {
      // 서버가 준 안내 문장만 보여 준다(그 밖의 오류 원문은 고객에게 보이지 않는다)
      final message = error is ApiException ? error.message : '로그인하지 못했어요. 잠시 뒤 다시 해 주세요.';
      if (mounted) setState(() => _error = message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _tab(String label, bool signup) {
    final selected = _signup == signup;
    return Expanded(
      child: GestureDetector(
        onTap: () => setState(() {
          _signup = signup;
          _error = null;
        }),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 160),
          height: 40,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: selected ? Colors.white : Colors.transparent,
            borderRadius: BorderRadius.circular(999),
            boxShadow: selected ? const [BoxShadow(color: Color(0x14000000), blurRadius: 6, offset: Offset(0, 1))] : null,
          ),
          child: Text(
            label,
            style: TextStyle(
              fontWeight: FontWeight.w700,
              color: selected ? AppColors.ink : AppColors.muted,
            ),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AutofillGroup(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            padding: const EdgeInsets.all(4),
            decoration: BoxDecoration(color: AppColors.sunk, borderRadius: BorderRadius.circular(999)),
            child: Row(children: [_tab('로그인', false), _tab('회원가입', true)]),
          ),
          const SizedBox(height: 20),
          if (_signup) ...[
            const FieldLabel('이름'),
            TextField(
              controller: _name,
              autofillHints: const [AutofillHints.name],
              decoration: const InputDecoration(hintText: '예: 김하늘'),
            ),
            const SizedBox(height: 16),
          ],
          const FieldLabel('이메일'),
          TextField(
            controller: _email,
            keyboardType: TextInputType.emailAddress,
            autofillHints: const [AutofillHints.email],
          ),
          const SizedBox(height: 16),
          const FieldLabel('비밀번호'),
          TextField(
            controller: _password,
            obscureText: true,
            autofillHints: [_signup ? AutofillHints.newPassword : AutofillHints.password],
            decoration: InputDecoration(hintText: _signup ? '8자 이상' : null),
            onSubmitted: _signup ? null : (_) => _submit(),
          ),
          if (_signup) ...[
            const SizedBox(height: 16),
            const FieldLabel('비밀번호 확인'),
            TextField(
              controller: _password2,
              obscureText: true,
              onSubmitted: (_) => _consentsReady ? _submit() : null,
            ),
            const SizedBox(height: 22),
            const FieldLabel('약관 동의'),
            if (_consentItems != null)
              ConsentGroup(
                items: _consentItems!,
                checked: _agreed,
                enabled: !_busy,
                onChanged: (next) => setState(() => _agreed = next),
              )
            else if (_consentLoadFailed)
              const Notice('동의 내용을 불러오지 못했어요. 가입한 뒤 다음 화면에서 확인해 주세요.')
            else
              const Padding(padding: EdgeInsets.all(12), child: Loading()),
          ],
          if (_error != null) ...[const SizedBox(height: 16), ErrorBox(_error!)],
          const SizedBox(height: 22),
          FilledButton(
            onPressed: _busy || (_signup && !_consentsReady) ? null : _submit,
            child: Text(_busy ? '잠시만요…' : (_signup ? '가입하고 시작하기' : '로그인')),
          ),
          const SizedBox(height: 6),
          TextButton.icon(
            onPressed: widget.onBack,
            icon: const Icon(Icons.arrow_back_rounded, size: 18),
            label: const Text('카카오·구글로 들어가기'),
          ),
        ],
      ),
    );
  }
}

/// PC 왼쪽의 어두운 소개 판.
class _BrandPanel extends StatelessWidget {
  const _BrandPanel();

  @override
  Widget build(BuildContext context) {
    return Container(
      color: AppColors.night,
      padding: const EdgeInsets.fromLTRB(56, 48, 56, 40),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const BrandLogo(height: 20, color: AppColors.nightInk),
          const Spacer(),
          Text(
            '가장 듣고 싶은 목소리,\n그 사람다움 그대로',
            style: AppText.display.copyWith(color: AppColors.nightInk, fontSize: 38, height: 1.35),
          ),
          const SizedBox(height: 16),
          Text(
            '목소리도, 말투도, 기억도.\n가족의 목소리와 기억으로 다시 이야기 나눠 보세요.',
            style: AppText.lead.copyWith(color: AppColors.nightInk.withValues(alpha: 0.72), fontSize: 16),
          ),
          const SizedBox(height: 40),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 440),
            child: const _SampleChat(dark: true),
          ),
          const Spacer(),
          Text(
            '© TalkTo',
            style: TextStyle(color: AppColors.nightInk.withValues(alpha: 0.4), fontSize: 12),
          ),
        ],
      ),
    );
  }
}

/// 소개용 대화 세 줄.
class _SampleChat extends StatelessWidget {
  final bool dark;

  const _SampleChat({required this.dark});

  @override
  Widget build(BuildContext context) {
    final otherBg = dark ? const Color(0xFF2A3730) : AppColors.card;
    final otherFg = dark ? AppColors.nightInk : AppColors.ink;
    final mineBg = dark ? const Color(0xFFD9CFBC) : AppColors.primary;
    final mineFg = dark ? AppColors.night : Colors.white;

    Widget bubble(String text, {required bool mine}) {
      return Align(
        alignment: mine ? Alignment.centerRight : Alignment.centerLeft,
        child: Container(
          margin: EdgeInsets.only(bottom: 8, left: mine ? 48 : 0, right: mine ? 0 : 48),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: mine ? mineBg : otherBg,
            border: mine || dark ? null : Border.all(color: AppColors.line),
            borderRadius: BorderRadius.only(
              topLeft: const Radius.circular(18),
              topRight: const Radius.circular(18),
              bottomLeft: Radius.circular(mine ? 18 : 5),
              bottomRight: Radius.circular(mine ? 5 : 18),
            ),
          ),
          child: Text(text, style: TextStyle(color: mine ? mineFg : otherFg, fontSize: 14.5, height: 1.5)),
        ),
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        bubble('할머니 정말 많이 보고 싶어요', mine: true),
        bubble('할머니도 정말 많이 보고 싶어. 건강하게 잘 지내지?', mine: false),
        bubble('네 저는 요즘 밥도 잘 챙겨먹고 건강하게 잘 지내고 있어요', mine: true),
        bubble('다행이다. 정말 많이 사랑한다', mine: false),
      ],
    );
  }
}
