import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/analytics.dart';
import '../../core/network/api_client.dart';
import '../../core/platform/browser.dart';
import '../../core/session.dart';
import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/ui.dart';
import '../auth/login_page.dart' show LegalLinks, SocialLoginButton;
import '../legal/consent_checks.dart';
import '../legal/legal_api.dart';
import '../personas/data/persona_store.dart';
import 'member_profile_sheet.dart';
import 'pending_invite.dart';
import 'share_api.dart';

/// 가족 초대 받기(/join/:token). 바깥 틀 밖, 로그인 없이도 열린다.
///
/// 어르신도 따라 할 수 있게 글을 조금 크게(1.15배), 주 단추는 높게(52) 두고 카카오 로그인을 맨 앞에 둔다.
///   로그인 전: 누가 초대했는지 보이고 카카오·구글·이메일로 들어온다(초대는 [PendingInvite] 에 적어 둔다).
///   로그인 뒤: 만든 분이 적어 둔 내 정보가 있으면 '네, 맞아요' 카드 하나, 없으면 직접 적기.
///   이미 함께하는 사람이면(받은 링크를 다시 누름) 바로 대화로.
/// 초대 열쇠는 요청 몸통으로만 보내고, 통계에는 화면 이름 'join' 만 남는다.
class JoinPage extends StatefulWidget {
  final String token;

  /// 서버가 켜 둔 로그인 방법을 읽는다. 시험에서는 서버 없이 바꿔 끼운다.
  final Future<Map<String, dynamic>> Function() loadProviders;

  /// 메신저 앱 안의 브라우저인지(시험에서 바꿔 끼운다). 없으면 브라우저에 묻는다.
  final bool? inApp;

  const JoinPage({super.key, required this.token, this.loadProviders = Session.providers, this.inApp});

  @override
  State<JoinPage> createState() => _JoinPageState();
}

class _JoinPageState extends State<JoinPage> {
  /// 로그인 전이면 preview, 로그인 뒤면 resolve 응답.
  Map<String, dynamic>? _info;
  Object? _loadError;
  Map<String, dynamic> _providers = const {};
  late bool _loggedIn = session.loggedIn;

  ProfileDraft? _draft;
  bool _agreed = false;
  bool _busy = false;
  bool _notMe = false;
  String? _error;

  /// 받아들이다 링크가 끝난 것을 알았을 때 서버가 준 문장(없으면 상태마다 정해 둔 문장).
  String? _endedText;

  String get _token => widget.token;
  String get _state => '${_info?['state'] ?? ''}';
  String get _ownerName {
    final name = (_info?['ownerName'] as String?)?.trim() ?? '';
    return name.isEmpty ? '가족' : name;
  }

  bool get _isSelf => _info?['isSelf'] == true;
  Map<String, dynamic> get _prefill =>
      _info?['prefill'] is Map ? Map<String, dynamic>.from(_info!['prefill'] as Map) : <String, dynamic>{};

  @override
  void initState() {
    super.initState();
    session.addListener(_sessionChanged);
    widget.loadProviders().then((p) {
      if (mounted) setState(() => _providers = p);
    });
    _load();
  }

  @override
  void dispose() {
    session.removeListener(_sessionChanged);
    _draft?.dispose();
    super.dispose();
  }

  /// '다른 계정으로 들어가기'(로그아웃)처럼 이 화면에 있는 채로 로그인 상태가 바뀌면 다시 본다.
  void _sessionChanged() {
    if (session.loggedIn == _loggedIn || !mounted) return;
    _loggedIn = session.loggedIn;
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _info = null;
      _loadError = null;
      _error = null;
      _endedText = null;
      _notMe = false;
      _agreed = false;
    });
    if (!PendingInvite.isToken(_token)) {
      await PendingInvite.clear();
      if (mounted) setState(() => _info = {'state': 'invalid'});
      return;
    }
    try {
      if (!_loggedIn) {
        // 열자마자 적어 둔다(카카오·구글 로그인에서 돌아오면 여기로 이어진다)
        await PendingInvite.save(_token);
        final info = await ShareApi.preview(_token);
        if (const {'open', 'used'}.contains(info['state'])) {
          await PendingInvite.save(_token, owner: info['ownerName'] as String?);
        } else {
          await PendingInvite.clear();
        }
        if (mounted) setState(() => _info = info);
        return;
      }
      final info = await ShareApi.resolve(_token);
      // 로그인·가입·필수 동의를 마쳤다: 브라우저에 적어 둔 초대는 여기서 지운다(이 화면은 주소의 열쇠로
      // 이어 가고, '다른 계정으로 들어가기'로 로그아웃하면 로그인 전 모습으로 다시 열리며 다시 적어 둔다)
      await PendingInvite.clear();
      final state = info['state'];
      if (state == 'member') {
        // 받은 링크를 다시 누름: 바로 대화로
        await personaStore.load();
        if (mounted) context.go('/p/${info['personaId']}');
        return;
      }
      if (state == 'join') {
        // 앞서 그리던 칸이 있으면 다음 화면을 그린 뒤에 놓아 준다
        final old = _draft;
        if (old != null) WidgetsBinding.instance.addPostFrameCallback((_) => old.dispose());
        final prefill = info['prefill'] is Map ? Map<String, dynamic>.from(info['prefill'] as Map) : {};
        _draft = ProfileDraft({
          ...prefill,
          // 내 이름은 비어 있으면 계정 이름으로 채워 둔다(고칠 수 있다)
          if ('${prefill['myName'] ?? ''}'.trim().isEmpty && session.name.trim().isNotEmpty) 'myName': session.name.trim(),
        });
      }
      if (mounted) setState(() => _info = info);
    } catch (error) {
      if (mounted) setState(() => _loadError = error);
    }
  }

  Future<void> _social(String provider, String label) async {
    if (_providers[provider] != true) {
      showMessage(context, '$label 로그인은 아직 쓸 수 없어요. 지금은 이메일로 들어와 주세요.');
      return;
    }
    await PendingInvite.save(_token, owner: _info?['ownerName'] as String?);
    session.startSocialLogin(provider);
  }

  Future<void> _email() async {
    await PendingInvite.save(_token, owner: _info?['ownerName'] as String?);
    if (mounted) context.go('/login');
  }

  Future<void> _home() async {
    await PendingInvite.clear();
    if (mounted) context.go('/');
  }

  Future<void> _otherAccount() async {
    // 로그아웃하면 이 화면이 로그인 전 모습으로 다시 열리며 초대를 다시 적어 둔다
    // (다른 계정으로 로그인하면 이 화면으로 돌아온다)
    // 이 기기에서만 로그인을 지운다(다른 기기의 내 로그인은 그대로 둔다)
    setState(() => _busy = true);
    await session.signOutHere();
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _later() async {
    Analytics.event('join_declined', {'reason': 'later'});
    await _home();
  }

  Future<void> _notMine() async {
    Analytics.event('join_declined', {'reason': 'not_me'});
    await PendingInvite.clear();
    if (mounted) setState(() => _notMe = true);
  }

  bool get _ready => _draft != null && _draft!.missing().isEmpty && _agreed && !_busy;

  Future<void> _accept() async {
    final draft = _draft!;
    final problem = draft.check(voice: ProfileVoice.member);
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await ShareApi.accept(_token, draft.toJson());
      await PendingInvite.clear();
      await personaStore.load();
      if (!mounted) return;
      final messenger = ScaffoldMessenger.of(context);
      context.go('/p/${result['personaId']}');
      messenger
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text('${josa(draft.value('addressTerm'), '과', '와')} 이야기할 수 있어요.')));
    } on ApiException catch (error) {
      if (!mounted) return;
      // 그사이 링크가 끝났거나 다른 분이 먼저 썼다: 그 상태 화면으로
      final ended = switch (error.code) {
        'invite_used' => 'used',
        'invite_expired' => 'expired',
        'invite_invalid' => 'invalid',
        'invite_unavailable' || 'share_closed' => 'unavailable',
        'invite_own' => 'owner',
        _ => null,
      };
      setState(() {
        _busy = false;
        if (ended == null) {
          _error = error.message;
        } else {
          _info = {...?_info, 'state': ended};
          _endedText = error.message;
        }
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = '잠시 문제가 생겼어요. 잠시 뒤 다시 해 주세요.';
        });
      }
    }
  }

  /// 카드의 한 칸을 고친다(그 칸만 띄운다).
  Future<void> _editOne(String key) async {
    final draft = _draft!;
    final result = await showProfileSheet(
      context,
      // 고치는 창도 이 화면처럼 글을 키운다(창은 이 화면 밖에 그려진다)
      textScaler: _textScaler(context),
      heading: profileLabel(key, ProfileVoice.member, ownerName: _ownerName),
      initial: draft.toJson(),
      voice: ProfileVoice.member,
      ownerName: _ownerName,
      self: _isSelf,
      relations: _relations,
      suggestions: _info?['addressSuggestions'] as Map?,
      keys: [key],
      action: '고치기',
      minorScope: _minorScope,
    );
    if (result == null || !mounted) return;
    setState(() {
      if (key == 'speechLevel') {
        draft.speechLevel = result[key] as String?;
      } else {
        draft.text[key]!.text = '${result[key] ?? ''}';
      }
      _error = null;
    });
  }

  List<String> get _relations => [for (final r in _info?['relations'] as List? ?? const []) '$r'];

  /// 아이(만 19세 미만)의 대화 상대: 법정대리인과 직계가족만 들어온다. 관계는 목록에서만 고른다.
  bool get _minorScope => _info?['minorScope'] == true;

  // ────────────────────────── 그리기 ──────────────────────────

  /// 이 화면의 글 크기: 앱 글 크기의 1.15배(어르신도 읽게). [context] 는 이 화면 바깥(State 의 context).
  static TextScaler _textScaler(BuildContext context) =>
      TextScaler.linear(MediaQuery.textScalerOf(context).scale(16) / 16 * 1.15);

  @override
  Widget build(BuildContext context) {
    return MediaQuery(
      data: MediaQuery.of(context).copyWith(textScaler: _textScaler(context)),
      child: Scaffold(
        body: SafeArea(
          child: SingleChildScrollView(
            padding: EdgeInsets.fromLTRB(20, Breakpoints.isWide(context) ? 40 : 20, 20, 40),
            child: PageBody(
              maxWidth: 560,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Align(alignment: Alignment.centerLeft, child: BrandLogo(height: 18)),
                  const SizedBox(height: 28),
                  ..._body(),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  List<Widget> _body() {
    if (_loadError != null) {
      final error = _loadError;
      return [
        const Headline('초대를 확인하지 못했어요'),
        const SizedBox(height: 18),
        ErrorBox(error is ApiException ? error.message : '잠시 문제가 생겼어요. 잠시 뒤 다시 해 주세요.'),
        const SizedBox(height: 16),
        _primary('다시 해 보기', _load),
      ];
    }
    if (_info == null || (_loggedIn && _state == 'member')) {
      return const [
        SizedBox(height: 40),
        Center(child: Breathing()),
        SizedBox(height: 18),
        Center(child: Text('초대를 확인하는 중이에요', style: AppText.lead)),
      ];
    }
    if (_notMe) return _notMeView();
    if (!_loggedIn) {
      return switch (_state) {
        'open' => _welcome(again: false),
        'used' => _welcome(again: true),
        _ => _endedView(),
      };
    }
    return switch (_state) {
      'join' => _info?['fromOwner'] == true ? _confirmView() : _formView(),
      'owner' => _ownerView(),
      'used' => _usedView(),
      _ => _endedView(),
    };
  }

  Widget _primary(String label, VoidCallback? onPressed) => FilledButton(
        onPressed: onPressed,
        style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(52)),
        child: Text(label),
      );

  /// 그분을 가리키는 말: 받는 분이 부르는 말(엄마) → 만든 분 쪽 호칭(할머니).
  String? get _subjectWord {
    for (final key in ['addressTerm', 'ownerTitle']) {
      final value = (_info?[key] as String?)?.trim() ?? '';
      if (value.isNotEmpty) return value;
    }
    return null;
  }

  String get _welcomeLead {
    final word = _subjectWord;
    final what = _isSelf
        ? '$_ownerName님이 자기 목소리와 이야기로 만든 대화 상대와 이야기할 수 있어요.'
        : word != null
            ? '$_ownerName님이 $word의 녹음과 이야기로 만든 대화 상대와 이야기할 수 있어요.'
            : '$_ownerName님이 녹음과 이야기로 만든 대화 상대와 이야기할 수 있어요.';
    return '$what 로그인하면 바로 이어져요.';
  }

  List<Widget> _welcome({required bool again}) {
    final inApp = widget.inApp ?? isInAppBrowser();
    return [
      if (again)
        const Headline('다시 오셨네요', lead: '로그인하면 바로 이어져요.')
      else
        Headline('$_ownerName님이 초대했어요', lead: _welcomeLead),
      const SizedBox(height: 26),
      if (inApp) ...[
        Notice(
          isKakaoInApp()
              ? "카카오톡 안에서는 Google 로그인이 안 될 수 있어요. '카카오로 시작하기'를 눌러 주세요."
              : "이 앱 안에서는 Google 로그인이 안 될 수 있어요. '카카오로 시작하기'를 누르거나 다른 브라우저로 열어 주세요.",
        ),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            onPressed: () => openExternal(currentFullUrl()),
            icon: const Icon(Icons.open_in_browser_rounded, size: 20),
            label: const Text('다른 브라우저로 열기'),
          ),
        ),
        const SizedBox(height: 10),
      ],
      SocialLoginButton.kakao(onPressed: () => _social('kakao', '카카오'), height: 58),
      const SizedBox(height: 10),
      SocialLoginButton.google(onPressed: () => _social('google', 'Google')),
      const SizedBox(height: 10),
      TextButton(onPressed: _email, child: const Text('이메일로 계속하기')),
      const SizedBox(height: 4),
      const Text('처음이면 자동으로 가입돼요.', textAlign: TextAlign.center, style: AppText.small),
      const SizedBox(height: 24),
      const LegalLinks(),
    ];
  }

  static const _endedTexts = {
    'expired': '기간이 지난 초대 링크예요. 보내 준 가족에게 새 링크를 부탁해 주세요.',
    'invalid': '초대 링크를 찾지 못했어요. 받은 링크를 다시 확인하거나 보내 준 가족에게 새 링크를 부탁해 주세요.',
    'unavailable': '지금은 이 대화 상대를 함께 쓸 수 없어요. 보내 준 가족에게 알려 주세요.',
    'used': '이미 다른 분이 쓴 초대 링크예요. 보내 준 가족에게 내 링크를 따로 부탁해 주세요.',
  };

  List<Widget> _endedView() {
    return [
      Headline(
        '초대 링크를 열 수 없어요',
        lead: _endedText ?? _endedTexts[_state] ?? _endedTexts['invalid'],
      ),
      const SizedBox(height: 26),
      _primary('처음 화면으로', _home),
    ];
  }

  List<Widget> _usedView() {
    return [
      Headline('이미 쓴 초대 링크예요', lead: _endedText ?? _endedTexts['used']),
      const SizedBox(height: 18),
      Notice("지금 '${session.name}' 계정이에요. 이 링크로 들어왔던 계정이 따로 있으면 그 계정으로 들어와 주세요."),
      const SizedBox(height: 22),
      _primary('다른 계정으로 들어가기', _busy ? null : _otherAccount),
      const SizedBox(height: 8),
      TextButton(onPressed: _home, child: const Text('처음 화면으로')),
    ];
  }

  List<Widget> _ownerView() {
    final id = _info?['personaId'];
    return [
      const Headline('내가 만든 대화 상대예요', lead: '초대 링크는 함께 쓸 가족에게 보내 주세요.'),
      const SizedBox(height: 26),
      _primary('대화로 가기', () => context.go(id == null ? '/' : '/p/$id')),
      const SizedBox(height: 8),
      TextButton(onPressed: _busy ? null : _otherAccount, child: const Text('다른 계정으로 들어가기')),
    ];
  }

  List<Widget> _notMeView() {
    final name = '${_prefill['myName'] ?? ''}'.trim();
    return [
      Headline(
        '다른 분께 보낸 링크예요',
        lead: '이 링크는 ${name.isEmpty ? '다른 가족' : '$name님'}께 보낸 거예요. $_ownerName님께 내 링크를 따로 부탁해 주세요.',
      ),
      const SizedBox(height: 26),
      _primary('처음 화면으로', () => context.go('/')),
    ];
  }

  /// 함께하기 아래 공통: 동의 한 줄 · 대화는 나만 본다는 안내 · 단추.
  List<Widget> _agreeAndJoin({required String action}) {
    final consent = _info?['consent'] is Map ? _info!['consent'] as Map : const {};
    final label = '${consent['label'] ?? ''}'.trim();
    return [
      const SizedBox(height: 8),
      ConsentCheck(
        tag: '필수',
        // 받는 분 화면에서는 {{호칭}} 을 '그분'으로 읽는다
        label: fillCallName(label.isEmpty ? '가족 공유 안내를 읽고 동의해요.' : label, null),
        docKey: (consent['doc'] as String?) ?? 'family_share',
        value: _agreed,
        enabled: !_busy,
        onChanged: (v) => setState(() => _agreed = v),
      ),
      const SizedBox(height: 10),
      Notice(
        '내가 나눈 대화는 나만 볼 수 있어요. $_ownerName님도 볼 수 없어요. '
        '다만 내가 고쳐 보낸 대답과 보낸 답은 $_ownerName님이 확인할 수 있게 보여요.',
        icon: Icons.lock_outline_rounded,
        color: AppColors.primary,
        background: AppColors.primarySoft,
      ),
      if (_error != null) ...[const SizedBox(height: 12), ErrorBox(_error!)],
      const SizedBox(height: 20),
      _primary(_busy ? '잠시만요…' : action, _ready ? _accept : null),
      if (!_ready && !_busy && _draft != null)
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text(
            keepAll(_draft!.missing().isNotEmpty ? "'적어 주세요' 칸을 채워 주세요." : '위 안내에 동의하면 눌러져요.'),
            textAlign: TextAlign.center,
            style: AppText.small,
          ),
        ),
    ];
  }

  List<Widget> _confirmView() {
    final draft = _draft!;
    final name = '${_prefill['myName'] ?? ''}'.trim();
    final keys = [
      'myName',
      'myRelation',
      'addressTerm',
      'calledMe',
      'speechLevel',
      for (final k in extraProfileKeys)
        if (draft.value(k).isNotEmpty && !(k == 'callsOwner' && _isSelf)) k,
    ];
    return [
      Headline(
        '$_ownerName님이 이렇게 적어 두셨어요',
        lead: "맞으면 '네, 맞아요'를 눌러 주세요. 이대로 나를 알아보고 대답해요.",
      ),
      const SizedBox(height: 22),
      // 가족 공유 안내(family_share)가 이 카드를 '맞는지 확인'이라고 부른다
      const SectionLabel('맞는지 확인'),
      AppCard(
        padding: const EdgeInsets.fromLTRB(6, 6, 6, 6),
        child: Column(
          children: [
            for (var i = 0; i < keys.length; i++) ...[
              if (i > 0) const Divider(height: 1, indent: 14, endIndent: 14),
              _cardRow(keys[i]),
            ],
          ],
        ),
      ),
      const SizedBox(height: 8),
      Text(
        keepAll('$_ownerName님이 초대하면서 적어 둔 내용이에요. 틀린 곳은 눌러서 고칠 수 있어요.'),
        style: AppText.small,
      ),
      ..._agreeAndJoin(action: '네, 맞아요. 함께하기'),
      const SizedBox(height: 10),
      if (name.isNotEmpty)
        OutlinedButton(
          onPressed: _busy ? null : _notMine,
          style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(48)),
          child: Text('제가 $name님이 아니에요'),
        ),
      TextButton(onPressed: _busy ? null : _later, child: const Text('다음에 할게요')),
    ];
  }

  Widget _cardRow(String key) {
    final draft = _draft!;
    final value = draft.value(key);
    final required = requiredProfileKeys.contains(key);
    final shown = key == 'speechLevel' ? profileValueText(draft.toJson(), key) : value;
    return InkWell(
      borderRadius: BorderRadius.circular(12),
      onTap: _busy ? null : () => _editOne(key),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 12, 8, 12),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    keepAll(profileLabel(key, ProfileVoice.member, ownerName: _ownerName, self: _isSelf)),
                    style: AppText.small,
                  ),
                  const SizedBox(height: 2),
                  if (shown.isNotEmpty)
                    Text(keepAll(shown), style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700))
                  else
                    Text(
                      required ? '적어 주세요' : '비워 두셔도 돼요',
                      style: TextStyle(
                        fontSize: 15,
                        fontWeight: required ? FontWeight.w700 : FontWeight.w400,
                        color: required ? AppColors.danger : AppColors.muted,
                      ),
                    ),
                ],
              ),
            ),
            IconButton(
              tooltip: '고치기',
              onPressed: _busy ? null : () => _editOne(key),
              icon: const Icon(Icons.edit_outlined, size: 20, color: AppColors.ink2),
            ),
          ],
        ),
      ),
    );
  }

  List<Widget> _formView() {
    final word = (_info?['ownerTitle'] as String?)?.trim() ?? '';
    final who = _isSelf ? '$_ownerName님' : (word.isEmpty ? '그분' : '$_ownerName님의 $word');
    return [
      Headline('$who께 나는 어떤 사람인가요?', lead: '적어 주신 대로 나를 알아보고, 나를 부르던 말로 대답해요.'),
      const SizedBox(height: 24),
      if (_minorScope) ...[
        const Notice(minorScopeText, icon: Icons.child_care_rounded),
        const SizedBox(height: 16),
      ],
      ProfileForm(
        draft: _draft!,
        voice: ProfileVoice.member,
        ownerName: _ownerName,
        self: _isSelf,
        relations: _relations,
        suggestions: _info?['addressSuggestions'] as Map?,
        minorScope: _minorScope,
        keys: const ['myRelation', 'myName', 'addressTerm', 'calledMe', 'speechLevel', 'selfReference', 'callsOwner'],
        onChanged: () => setState(() => _error = null),
      ),
      ..._agreeAndJoin(action: '함께하기'),
      const SizedBox(height: 10),
      TextButton(onPressed: _busy ? null : _later, child: const Text('다음에 할게요')),
    ];
  }
}
