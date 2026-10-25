import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_client.dart';
import '../../core/platform/browser.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import '../legal/consent_checks.dart';
import '../legal/legal_api.dart';
import '../legal/legal_footer.dart';
import '../legal/legal_text.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/usage_ring.dart';
import '../personas/data/persona_store.dart';
import '../share/roles.dart';
import 'data/billing_api.dart';

/// '추억으로' · '가족으로'. 한글 이름이면 받침을 보고(ㄹ 받침은 '로'), 영어 이름은 받침을 글자로 알 수 없어
/// '로'를 붙인다('Memories로' · 'Family로').
String toPlan(Map plan) {
  final name = plan['name'] as String;
  final last = name.isEmpty ? 0 : name.runes.last;
  final korean = last >= 0xAC00 && last <= 0xD7A3;
  final batchim = korean && hasBatchim(name) && (last - 0xAC00) % 28 != 8;
  return '$name${batchim ? '으로' : '로'}';
}

/// 문장 속 요금제 이름: '추억 요금제' · '가족 요금제'.
String planWord(Object? name) => '$name 요금제';

/// '추억 요금제로'.
String toPlanWord(Map plan) => toPlan({'name': planWord(plan['name'])});

/// 올리기·내리기 순서. 고를 수 있는 요금제도 이것뿐이다(서버 목록에 다른 게 있어도 보이지 않는다).
const planOrder = ['memories', 'family'];

/// 요금제를 내리거나 해지할 때 덧붙이는 가족 공유 한 줄.
const _shareStops = '함께 쓰는 가족이 있으면 그날부터 가족은 이야기할 수 없어요(가족 요금제로 돌아오면 다시 함께 써요).';

/// 1,000 처럼 세 자리마다 쉼표.
String commas(num value) {
  final digits = value.round().toString();
  final out = StringBuffer();
  for (var i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 == 0) out.write(',');
    out.write(digits[i]);
  }
  return out.toString();
}

String won(num value) => '${commas(value)}원';

/// 결제 창에서 돌아왔을 때 주소에 붙은 ?error= 를 보일 글로 바꾼다. 서버가 붙이는 이름(code)과 지금 서버가
/// 보내는 정해진 문장만 알아보고, 그 밖의 값은 한 가지 글로 보인다(남이 만든 주소로 TalkTo 안내처럼
/// 아무 글이나 띄우지 못하게). 값이 없으면 null.
String? paymentErrorText(String? raw) {
  if (raw == null || raw.trim().isEmpty) return null;
  const notOpen = '지금은 결제할 수 없어요. 잠시 뒤 다시 해 주세요.';
  const expired = '결제 시간이 지났어요. 요금제 화면에서 다시 시도해 주세요.';
  const unverified = '결제를 확인하지 못했어요. 요금제 화면에서 다시 해 주세요.';
  const noPersona = '대화 상대를 찾지 못해 결제하지 않았어요.';
  const checking = '결제 결과를 확인하고 있어요. 몇 분 뒤 요금제 화면을 다시 봐 주세요.';
  const working = '결제를 처리하고 있어요. 잠시 뒤 요금제 화면을 다시 봐 주세요.';
  const codes = {
    'cancelled': '결제를 취소했어요.',
    'failed': _paymentFailed,
    'not_open': notOpen,
    'expired': expired,
    'payment_unverified': unverified,
    'unverified': unverified,
    'no_persona': noPersona,
    'pending': checking,
    'checking': checking,
    'retry': working,
    'busy': working,
  };
  final text = raw.trim();
  final known = codes[text];
  if (known != null) return known;
  // 지금 서버(billing.py _back)가 보내는 정해진 문장은 그대로 보인다
  const legacy = {
    notOpen,
    expired,
    unverified,
    noPersona,
    checking,
    working,
    '결제를 취소했어요.',
    '결제하지 못했어요.',
    '카드 등록에 실패했어요.',
    '결제 회사와 연결하지 못했어요.',
    '방금 하신 결제가 잘 됐는지 확인하고 있어요. 몇 분 뒤 요금제 화면을 다시 봐 주세요.',
  };
  if (legacy.contains(text)) return text;
  return _paymentFailed;
}

const _paymentFailed = '결제하지 못했어요. 요금제 화면에서 다시 해 주세요.';

/// 요금제·구독 관리. 결제는 페르소나마다 받는다 — 위에서 페르소나를 고르면 그 페르소나의 요금제를
/// 고른다. 결제는 서버가 띄우는 결제 창(토스페이먼츠)에서 하고 이 화면으로 돌아온다.
class PlansPage extends StatefulWidget {
  /// 요금제를 볼 페르소나(없으면 첫 번째 다 만든 페르소나)
  final String? personaId;

  /// 결제 창에서 돌아왔을 때: result=success 또는 error=…(그대로 보이지 않고 [paymentErrorText] 로 바꾼다).
  /// success 는 새로 읽은 구독이 결제된 상태일 때만 알린다.
  final String? result;
  final String? error;

  /// 시험에서 서버 대신 쓸 값(없으면 BillingApi)
  final Future<Map<String, dynamic>> Function()? loadMe;
  final Future<List<Map<String, dynamic>>> Function()? loadPayments;

  /// 결제 직전 안내와 체크 글(없으면 서버 GET /legal 의 payment — [LegalApi.payment]).
  final Future<Map<String, dynamic>> Function()? loadPayment;

  const PlansPage({
    super.key,
    this.personaId,
    this.result,
    this.error,
    this.loadMe,
    this.loadPayments,
    this.loadPayment,
  });

  @override
  State<PlansPage> createState() => _PlansPageState();
}

class _PlansPageState extends State<PlansPage> {
  Map<String, dynamic>? _me;
  List<Map<String, dynamic>> _payments = [];
  Object? _loadError;
  bool _busy = false;
  late String? _selected = widget.personaId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final results = await Future.wait([
        (widget.loadMe ?? BillingApi.me)(),
        (widget.loadPayments ?? BillingApi.payments)(),
      ]);
      if (!mounted) return;
      final me = results[0] as Map<String, dynamic>;
      setState(() {
        _me = me;
        _payments = results[1] as List<Map<String, dynamic>>;
        _loadError = null;
        // 고른 페르소나가 없거나 지워졌으면 첫 번째 다 만든 페르소나
        if (_selected == null || _view(_selected!) == null) {
          _selected = _personas.where((p) => p['step'] == 'READY').firstOrNull?['personaId'] ??
              _personas.firstOrNull?['personaId'];
        }
      });
      billingStore.setMe(me);
      if (widget.loadMe == null) personaStore.load();
    } catch (error) {
      if (mounted) setState(() => _loadError = error);
    }
  }

  List<Map<String, dynamic>> get _personas => [
        for (final p in (_me?['personas'] as List? ?? const [])) Map<String, dynamic>.from(p),
      ];

  Map<String, dynamic>? _view(String id) => _personas.where((p) => p['personaId'] == id).firstOrNull;

  Map<String, dynamic>? get _current => _selected == null ? null : _view(_selected!);

  Map<String, dynamic>? _plan(String id) {
    for (final p in (_me?['plans'] as List? ?? const [])) {
      if (p['id'] == id) return Map<String, dynamic>.from(p);
    }
    return null;
  }

  static String _won(num value) => won(value);

  /// 결제가 열리기 전(구독 신청만 받는다). 예전 서버는 paymentsOpen 을 주지 않으므로 없으면 열린 것으로 본다.
  bool get _prelaunch => _me?['paymentsOpen'] == false;

  /// 결제 전 한 달 대화 상한(운영에서 정한 값). 없으면 제한 없음.
  int? get _prelaunchLimit => (_me?['prelaunchConversations'] as num?)?.toInt();

  /// 결제한 기록이 있는가. 결제 전 모드에서 결제 내역·해지·원래 가격 동의는 이럴 때만 보인다.
  bool get _hasPaid =>
      _payments.isNotEmpty ||
      _personas.any((p) =>
          p['hadPaidPlan'] == true ||
          p['subscription'] != null ||
          const {'ACTIVE', 'CANCELING', 'PAST_DUE'}.contains(p['status']));

  /// 결제 전에 쓰는 말: '무료로, 횟수 제한 없이' · '무료로(다 만든 날부터 30일마다 대화 200번까지)'.
  String get _freeWay => _prelaunchLimit == null
      ? '무료로, 횟수 제한 없이'
      : '무료로(다 만든 날부터 30일마다 대화 ${commas(_prelaunchLimit!)}번까지)';

  /// 신청한 요금제의 화면 이름(서버 planName, 없으면 요금제 목록에서).
  String? _requestName(Map<String, dynamic>? request) {
    if (request == null) return null;
    return (request['planName'] ?? _plan(request['plan'] as String)?['name']) as String?;
  }

  /// 구독 신청(결제 전). 이미 다른 요금제를 신청했으면 신청을 바꾼다.
  Future<void> _requestPlan(String planId) async {
    final view = _current!;
    final plan = _plan(planId)!;
    final previous = BillingStore.requestOf(view);
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => _RequestDialog(
        plan: plan,
        personaTitle: _who(view),
        betaName: _betaName,
        change: previous != null && previous['plan'] != planId,
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      await BillingApi.request(_selected!, planId);
      await _load();
      if (mounted) {
        showMessage(
          context,
          previous != null
              ? '${toPlanWord(plan)} 신청을 바꿨어요. 결제가 열리면 알려 드릴게요.'
              : '${planWord(plan['name'])} 구독 신청을 받았어요. 결제가 열리면 알려 드릴게요.',
        );
      }
    } catch (error) {
      if (mounted) showMessage(context, error);
      // 그사이 결제가 열렸으면 새로 보여 준다(그때부터는 바로 시작할 수 있다)
      if (error is ApiException && error.statusCode == 409) await _load();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _cancelRequest() async {
    final view = _current!;
    final name = _requestName(BillingStore.requestOf(view));
    final ok = await confirmAction(
      context,
      name == null ? '구독 신청을 취소할까요?' : '${planWord(name)} 신청을 취소할까요?',
      '취소해도 결제가 열리기 전까지는 지금처럼 $_freeWay 쓰실 수 있어요. 언제든 다시 신청할 수 있어요.',
      action: '신청 취소',
      danger: false,
    );
    if (!ok || !mounted) return;
    setState(() => _busy = true);
    try {
      await BillingApi.cancelRequest(_selected!);
      await _load();
      if (mounted) showMessage(context, '구독 신청을 취소했어요.');
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 요금제 화면에서 대화 상대를 가리키는 말: '할머니 (외할머니)'.
  static String _who(Map v) => personaLabel(v['title'], v['relation']);

  /// 무료 체험: 계정 하나에 대화 상대 몇 명까지 · 며칠 동안.
  int get _trialPersonas => (_me?['trialPersonasPerAccount'] as num?)?.toInt() ?? 1;
  int get _trialDays => (_me?['trialDays'] as num?)?.toInt() ?? 7;

  /// '첫 100가족 할인'(가족 요금제와 헷갈리지 않게 할인이라는 말을 붙인다).
  String get _betaName => '첫 ${_me?['betaFamilies'] ?? 100}가족 할인';

  /// 이 가족이 지금 고르면 내는 값(초기 가족 요금이면 그 값).
  static num _charge(Map plan) => (plan['chargePrice'] ?? plan['price']) as num;

  static String _date(dynamic iso) {
    final t = parseServerTime(iso as String?);
    return t == null ? '' : '${t.month}월 ${t.day}일';
  }

  /// 이용 기간이 끝나는 날(…까지): 기간 끝(다음 결제일)의 전날. 한 번 결제한 이용 기간은 결제일부터 다음 달
  /// 같은 날의 전날까지다(약관 4번). 다음 결제일은 [_date] 그대로.
  static String _lastDay(dynamic iso) {
    final t = parseServerTime(iso as String?);
    if (t == null) return '';
    final day = DateTime(t.year, t.month, t.day).subtract(const Duration(days: 1));
    return '${day.month}월 ${day.day}일';
  }

  void _select(String id) {
    setState(() => _selected = id);
    context.go('/plans?persona=$id');
  }

  Future<void> _choose(String planId) async {
    final view = _current!;
    final plan = _plan(planId)!;
    final current = view['plan'] as String;
    final sub = view['subscription'] as Map?;
    // 등록된 카드로 바로 바꾸는가는 서버가 정한다(결제 때와 같은 규칙)
    final hasCard = view['canChange'] == true;
    final downgrade = hasCard && planOrder.indexOf(planId) < planOrder.indexOf(current);

    if (view['paymentPending'] == true) {
      showMessage(context, '방금 하신 결제가 잘 됐는지 확인하고 있어요. 몇 분 뒤에 다시 해 주세요.');
      return;
    }
    if (downgrade) {
      // 다음 결제부터 바뀌는 것도 결제 조건이 바뀌는 일이라 같은 두 가지를 확인받는다
      final ok = await showDialog<bool>(
        context: context,
        builder: (_) => _ConsentDialog(
          plan: plan,
          personaTitle: _who(view),
          upgrade: true,
          savedCard: null,
          periodEnd: _lastDay(view['periodEnd']),
          loadPayment: widget.loadPayment ?? LegalApi.payment,
          testMode: _me?['testMode'] == true,
          betaName: _betaName,
          betaMonths: (_me?['betaMonths'] as num?)?.toInt() ?? 12,
          betaUntil: _me?['betaUntil'] == null ? null : _date(_me?['betaUntil']),
          downgradeText: '${_lastDay(view['periodEnd'])}까지는 지금 요금제를 그대로 쓰고, 다음 결제부터 '
              '${toPlanWord(plan)} 바뀌어요(월 ${_won(_charge(plan))}). $_shareStops',
        ),
      );
      if (ok == true) await _run(planId, mode: 'change', agreements: _agreements);
      return;
    }
    // 이 페르소나 구독은 없지만 다른 페르소나에 등록한 카드가 있으면 그 카드로 바로 결제한다
    final savedCard = hasCard || sub?['status'] == 'PAST_DUE' ? null : _me?['cardLabel'] as String?;
    final upgradeCharge = (view['upgradeQuotes'] as Map?)?[planId] as num?;
    final agreed = await showDialog<bool>(
      context: context,
      builder: (_) => _ConsentDialog(
        plan: plan,
        personaTitle: _who(view),
        upgrade: hasCard,
        upgradeCharge: upgradeCharge,
        savedCard: savedCard,
        periodEnd: _lastDay(view['periodEnd']),
        loadPayment: widget.loadPayment ?? LegalApi.payment,
        testMode: _me?['testMode'] == true,
        betaName: _betaName,
        betaMonths: (_me?['betaMonths'] as num?)?.toInt() ?? 12,
        betaUntil: _me?['betaUntil'] == null ? null : _date(_me?['betaUntil']),
      ),
    );
    if (agreed == true) {
      await _run(
        planId,
        mode: hasCard ? 'change' : (savedCard != null ? 'saved_card' : 'window'),
        expectedAmount: (hasCard ? upgradeCharge : _charge(plan))?.toInt(),
        agreements: _agreements,
      );
    }
  }

  /// 결제 확인 창에서 체크한 두 가지(정기결제 · 결제 취소 제한). 서버가 결제마다 기록한다.
  static const _agreements = ['recurring', 'withdrawal_limit'];

  /// [agreements] 는 결제 확인 창을 거쳤을 때만 보낸다. 지금 요금제를 다시 골라 예약한 변경을
  /// 취소할 때는 결제가 아니라서 없어도 된다.
  Future<void> _run(String planId, {String? mode, int? expectedAmount, List<String>? agreements}) async {
    final personaId = _selected!;
    setState(() => _busy = true);
    try {
      final result = await BillingApi.checkout(
        personaId,
        planId,
        currentPageUrl(),
        mode: mode,
        expectedAmount: expectedAmount,
        agreements: agreements,
      );
      final url = result['checkoutUrl'] as String?;
      if (url != null) {
        goToUrl(url); // 결제 창으로. 끝나면 #/plans?persona= 로 돌아온다
        return;
      }
      if (mounted) {
        showMessage(
          context,
          result['started'] == true
              ? '등록된 카드로 ${_won(result['charged'] as num)}을 결제하고 구독을 시작했어요.'
              : result['scheduled'] != null
                  ? '다음 결제부터 바뀌어요.'
                  : result['charged'] != null
                      ? '바꿨어요. 남은 날만큼 더 내는 금액 ${_won(result['charged'])}을 결제했어요.'
                      : result['changed'] == false
                          ? '지금 요금제를 그대로 써요.'
                          : '바꿨어요.',
        );
      }
      await _load();
    } catch (error) {
      if (mounted) showMessage(context, error);
      // 요금·결제 방법이 그사이 바뀌었거나 결과를 기다리는 중이면 새로 보여 준다
      if (error is ApiException && (error.statusCode == 409 || error.statusCode == 402)) await _load();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _cancel() async {
    final view = _current!;
    final ok = await confirmAction(
      context,
      '${_who(view)} 구독을 해지할까요?',
      '${_lastDay(view['periodEnd'])}까지는 그대로 쓰고, 그 뒤로는 결제하지 않아요. '
          '만들어 둔 대화 상대와 나눈 대화는 그대로 남아요. $_shareStops',
      action: '해지하기',
    );
    if (!ok) return;
    try {
      await BillingApi.cancel(_selected!);
      await _load();
    } catch (error) {
      if (mounted) showMessage(context, error);
    }
  }

  Future<void> _resume() async {
    try {
      await BillingApi.resume(_selected!);
      await _load();
      if (mounted) showMessage(context, '해지 신청을 취소했어요. 매달 이어서 결제돼요.');
    } catch (error) {
      if (mounted) showMessage(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final wide = Breakpoints.isWide(context);
    final me = _me;
    final view = _current;
    return Scaffold(
      appBar: AppBar(automaticallyImplyLeading: !wide, title: const Text('요금제')),
      body: me == null
          ? (_loadError != null
              ? LoadFailed(_loadError!, onRetry: () {
                  setState(() => _loadError = null);
                  _load();
                })
              : const Loading())
          : ListView(
              padding: EdgeInsets.fromLTRB(20, wide ? 24 : 4, 20, 48),
              children: [
                PageBody(
                  maxWidth: 1000,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      // 주소의 result=success 만 믿지 않는다. 새로 읽은 이 대화 상대의 구독이 결제된 상태일 때만
                      if (widget.result == 'success' && const {'ACTIVE', 'CANCELING'}.contains(view?['status'])) ...[
                        const Notice(
                          '결제했어요. 고마워요.',
                          icon: Icons.check_circle_outline_rounded,
                          color: AppColors.primary,
                          background: AppColors.primarySoft,
                        ),
                        const SizedBox(height: 16),
                      ],
                      if (paymentErrorText(widget.error) case final error?) ...[
                        ErrorBox(error),
                        const SizedBox(height: 16),
                      ],
                      // 결제 전에는 결제한 기록이 있을 때만(원래 가격 동의는 결제한 구독에만 해당한다)
                      if (me['regularPriceConsentNeeded'] == true && (!_prelaunch || _hasPaid)) ...[
                        _regularPriceCard(me),
                        const SizedBox(height: 16),
                      ],
                      if (_prelaunch) ...[
                        _prelaunchNotice(),
                        const SizedBox(height: 18),
                      ],
                      Headline(
                        '요금제',
                        lead: _prelaunch
                            ? '요금제는 대화 상대마다 따로 골라요. 만드는 건 무료예요.'
                            : '요금제는 대화 상대마다 따로 골라요. 만드는 건 무료이고, '
                                '계정 하나에 대화 상대 $_trialPersonas명은 $_trialDays일 동안 무료로 써 볼 수 있어요.',
                      ),
                      const SizedBox(height: 22),
                      if (_personas.isEmpty)
                        _noPersona()
                      else ...[
                        const SectionLabel('어느 분의 요금제인가요?'),
                        _personaPicker(),
                        const SizedBox(height: 22),
                        if (view != null) ...[
                          if (_startRequested(view) != null) ...[
                            _startRequested(view)!,
                            const SizedBox(height: 16),
                          ],
                          _status(view),
                          const SizedBox(height: 26),
                          SectionLabel('${_who(view)} 요금제 고르기'),
                          if (view['step'] != 'READY')
                            Notice(
                              _prelaunch
                                  ? '다 만든 뒤에 구독 신청할 수 있어요. 결제가 열리기 전까지는 $_freeWay 쓸 수 있어요.'
                                  : view['trialWillStart'] == false
                                      ? '다 만든 뒤에 요금제를 고를 수 있어요. 무료 체험은 이미 쓰셔서, 다 만든 뒤 요금제를 고르면 대화할 수 있어요.'
                                      : '다 만든 뒤에 요금제를 고를 수 있어요. 다 만들면 먼저 $_trialDays일 동안 무료로 써 볼 수 있어요.',
                            )
                          else ...[
                            if (view['paymentPending'] == true) ...[
                              const Notice(
                                '방금 하신 결제가 잘 됐는지 확인하고 있어요. 확인되면 바로 바뀌어요(몇 분 걸릴 수 있어요).',
                                icon: Icons.hourglass_top_rounded,
                              ),
                              const SizedBox(height: 12),
                            ],
                            _planGrid(view, wide),
                          ],
                        ],
                      ],
                      const SizedBox(height: 14),
                      _accountNotes(me),
                      if (_payments.isNotEmpty) ...[
                        const SizedBox(height: 28),
                        const SectionLabel('결제 내역'),
                        AppCard(
                          padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 6),
                          child: Column(
                            children: [
                              for (var i = 0; i < _payments.length; i++) ...[
                                if (i > 0) const Divider(height: 1),
                                _paymentRow(_payments[i]),
                              ],
                            ],
                          ),
                        ),
                      ],
                      const SizedBox(height: 26),
                      Text(
                        _prelaunch && !_hasPaid
                            ? '구독 신청만으로는 돈이 나가지 않아요. 결제가 열리면 알려 드리고, 그때 직접 결제하셔야 구독이 시작돼요. '
                                '결제가 열린 뒤 요금은 대화 상대마다 처음 결제한 날짜에 매달 자동으로 결제되고, 해지는 언제든 할 수 있어요. '
                                '결제 취소(청약철회)와 환불은 이용약관과 환불 정책을 따라요. '
                                '대화 상대를 지우면 함께 쓰던 가족도 더는 대화할 수 없어요.'
                            : '요금은 대화 상대마다 처음 결제한 날짜에 매달 자동으로 결제돼요. 해지는 이 화면에서 언제든 할 수 있고, '
                                '해지해도 이미 결제한 기간이 끝날 때까지는 쓸 수 있어요. 대화 상대를 지우면 그 구독도 함께 끝나요. '
                                '결제 취소(청약철회)와 환불은 이용약관과 환불 정책을 따라요. '
                                '대화 상대를 지우면 함께 쓰던 가족도 더는 대화할 수 없어요.',
                        style: AppText.small,
                      ),
                      // 결제 화면 아래: 개인정보 처리방침과 사업자 정보(글 그대로, 전자상거래법 제10조)
                      const SizedBox(height: 22),
                      const LegalFooter(),
                    ],
                  ),
                ),
              ],
            ),
    );
  }

  Future<void> _consentRegularPrice() async {
    try {
      await BillingApi.consentRegularPrice();
      await _load();
      if (mounted) showMessage(context, '동의했어요. 할인이 끝나도 원래 가격으로 계속 쓰실 수 있어요.');
    } catch (error) {
      if (mounted) showMessage(context, error);
    }
  }

  /// 초기 가족 요금 → 정식 요금은 정기결제 가격 인상이라 미리 알리고 동의를 받는다(마스터 리포트 0.0.5).
  Widget _regularPriceCard(Map<String, dynamic> me) {
    final ended = parseServerTime(me['betaUntil'] as String?)?.isBefore(DateTime.now()) ?? false;
    final prices = [
      for (final id in ['memories', 'family'])
        if (_plan(id) != null) '${planWord(_plan(id)!['name'])} 월 ${_won(_plan(id)!['price'] as num)}',
    ].join(' · ');
    return AppCard(
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 16),
      color: AppColors.claySoft,
      borderColor: Colors.transparent,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            ended ? '$_betaName이 끝났어요' : '$_betaName이 ${_date(me['betaUntil'])}에 끝나요',
            style: AppText.heading,
          ),
          const SizedBox(height: 4),
          Text(
            keepAll('그 뒤에는 원래 가격($prices)으로 결제돼요. 동의해 주셔야 계속 쓰실 수 있고, '
                '동의하지 않으면 그때 구독이 끝나요(만들어 둔 대화 상대와 나눈 대화는 남아요).'),
            style: AppText.small.copyWith(color: AppColors.ink2, fontSize: 14),
          ),
          const SizedBox(height: 12),
          FilledButton(onPressed: _consentRegularPrice, child: const Text('원래 가격에 동의하고 계속 쓰기')),
        ],
      ),
    );
  }

  /// 결제가 열리기 전 맨 위 안내. '구독 신청'만 굵게.
  Widget _prelaunchNotice() {
    const style = TextStyle(color: AppColors.primaryDeep, fontSize: 14.5, height: 1.6);
    return AppCard(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 16),
      color: AppColors.primarySoft,
      borderColor: Colors.transparent,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Padding(
            padding: EdgeInsets.only(top: 2),
            child: Icon(Icons.card_giftcard_rounded, size: 20, color: AppColors.primary),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text.rich(
              TextSpan(
                style: style,
                children: [
                  TextSpan(
                    text: keepAll('지금은 결제가 열리기 전이에요. 대화 상대를 $_freeWay 쓰실 수 있어요. '
                        '마음에 드는 요금제를 '),
                  ),
                  TextSpan(text: keepAll('구독 신청'), style: const TextStyle(fontWeight: FontWeight.w800)),
                  TextSpan(text: keepAll('해 두시면 결제가 열릴 때 알려 드려요(신청만으로는 돈이 나가지 않아요).')),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// 결제가 열린 뒤, 결제 전에 구독 신청해 둔 대화 상대: 그 요금제를 바로 시작하게(지금의 결제 흐름).
  Widget? _startRequested(Map<String, dynamic> view) {
    final request = BillingStore.requestOf(view);
    if (_prelaunch || request == null || view['step'] != 'READY') return null;
    if (const {'ACTIVE', 'CANCELING', 'PAST_DUE', 'ADMIN'}.contains(view['status'])) return null;
    final planId = request['plan'] as String;
    // 지금 고를 수 없는 요금제를 예전에 신청했으면 시작하게 하지 않는다
    if (!planOrder.contains(planId) || _plan(planId) == null) return null;
    final name = _requestName(request) ?? _plan(planId)!['name'];
    return AppCard(
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 16),
      color: AppColors.primarySoft,
      borderColor: AppColors.primary,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('신청하신 ${planWord(name)}를 시작할 수 있어요', style: AppText.heading),
          const SizedBox(height: 4),
          Text(
            keepAll('결제가 열렸어요. 기다려 주셔서 고마워요. 시작하기를 누르면 결제 내용을 한 번 더 확인하고 결제해요.'),
            style: AppText.small.copyWith(color: AppColors.ink2, fontSize: 14),
          ),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: _busy || view['paymentPending'] == true ? null : () => _choose(planId),
            child: const Text('시작하기'),
          ),
        ],
      ),
    );
  }

  Widget _noPersona() {
    return AppCard(
      padding: const EdgeInsets.fromLTRB(20, 22, 20, 20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 가족이 나눠 준 대화 상대만 있는 사람: 따로 결제하지 않아도 된다고 먼저 알린다
          ListenableBuilder(
            listenable: personaStore,
            builder: (context, _) {
              if (!(personaStore.items ?? const <Map<String, dynamic>>[]).any(isMember)) {
                return const SizedBox.shrink();
              }
              return const Padding(
                padding: EdgeInsets.only(bottom: 16),
                child: Notice(
                  '가족이 나눠 준 대화 상대는 나눠 준 가족의 요금제로 이야기해요. 따로 결제하지 않아도 돼요.',
                  icon: Icons.group_outlined,
                  color: AppColors.primary,
                  background: AppColors.primarySoft,
                ),
              );
            },
          ),
          const Text('아직 만든 대화 상대가 없어요', style: AppText.heading),
          const SizedBox(height: 4),
          Text(
            keepAll(_prelaunch
                ? '대화 상대를 다 만들면 결제가 열리기 전까지 $_freeWay 쓸 수 있어요. 마음에 드는 요금제는 그때 구독 신청해 두시면 돼요.'
                : ((_me?['trialsLeft'] as num?) ?? 1) > 0
                    ? '대화 상대를 다 만들면 $_trialDays일 동안 무료로 써 볼 수 있고, 그 뒤에 요금제를 고르면 돼요.'
                    : '대화 상대를 다 만든 뒤 요금제를 고르면 대화할 수 있어요.'),
            style: AppText.small,
          ),
          const SizedBox(height: 14),
          FilledButton.icon(
            onPressed: () => context.go('/new'),
            icon: const Icon(Icons.add_rounded),
            label: const Text('새 대화 상대 만들기'),
          ),
        ],
      ),
    );
  }

  /// 페르소나마다 한 줄: 호칭 · 지금 상태. 누르면 아래에 그 페르소나의 요금제.
  Widget _personaPicker() {
    return Wrap(
      spacing: 10,
      runSpacing: 10,
      children: [
        for (final p in _personas)
          Material(
            color: p['personaId'] == _selected ? AppColors.primarySoft : Colors.white,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
              side: BorderSide(
                color: p['personaId'] == _selected ? AppColors.primary : AppColors.line2,
                width: p['personaId'] == _selected ? 1.6 : 1,
              ),
            ),
            child: InkWell(
              borderRadius: BorderRadius.circular(16),
              onTap: () => _select(p['personaId'] as String),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(10, 10, 16, 10),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Avatar(p['title'] as String, size: 34),
                    const SizedBox(width: 10),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(_who(p), style: const TextStyle(fontWeight: FontWeight.w700)),
                        Text(_shortStatus(p), style: AppText.small),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ),
      ],
    );
  }

  String _shortStatus(Map<String, dynamic> v) {
    if (v['step'] != 'READY') return '만드는 중';
    return switch (v['status']) {
      'TRIAL' => '무료 체험 ${BillingStore.trialDaysLeft(v) ?? ''}일 남음',
      'NOT_STARTED' => '만드는 중',
      'PRELAUNCH' when BillingStore.requestOf(v) != null => '${planWord(_requestName(BillingStore.requestOf(v)))} 신청함',
      'PRELAUNCH' => '결제 전 무료',
      // 결제했던 구독이 끝났으면 체험 이야기가 아니라 구독이 끝난 것이다
      'EXPIRED' || 'NO_TRIAL' when v['hadPaidPlan'] == true => '구독 끝남',
      'EXPIRED' => '무료 체험 끝남',
      'NO_TRIAL' => '요금제가 필요해요',
      'PAST_DUE' => '결제가 안 됐어요',
      'CANCELING' => '${planWord(v['planName'])} · 해지 신청함',
      'ADMIN' => '관리자',
      _ => '${planWord(v['planName'])} 구독 중',
    };
  }

  Widget _status(Map<String, dynamic> view) {
    final status = view['status'] as String;
    final sub = view['subscription'] as Map?;
    final limit = view['conversationsLimit'];
    final used = view['conversationsUsed'] ?? 0;
    final left = view['conversationsLeft'];
    final reset = view['conversationsResetAt'];
    final title = _who(view);
    const ended = '요금제를 고르면 이어서 대화할 수 있어요. 지금까지 나눈 대화는 그대로 있어요.';
    final (head, body) = switch (status) {
      'TRIAL' => (
          '$title · 무료 체험 중 · ${BillingStore.trialDaysLeft(view) ?? ''}일 남음',
          '${_date(view['trialEndsAt'])}까지 무료예요. 무료 체험이 끝나도 지금까지 나눈 대화는 그대로 남아요.',
        ),
      'PRELAUNCH' => (
          '$title · 결제 전 무료로 쓰는 중',
          BillingStore.requestOf(view) != null
              ? '${planWord(_requestName(BillingStore.requestOf(view)))} 신청함 · 결제가 열리면 알려 드려요. '
                  '그때 직접 결제하셔야 구독이 시작되고, 그전까지는 지금처럼 $_freeWay 쓰실 수 있어요.'
              : '결제가 열리기 전까지는 $_freeWay 쓰실 수 있어요. 마음에 드는 요금제를 아래에서 구독 신청해 두세요.',
        ),
      'NOT_STARTED' => (
          '$title · 만드는 중',
          _prelaunch
              ? '다 만들면 결제가 열리기 전까지 $_freeWay 쓸 수 있어요.'
              : view['trialWillStart'] == false
                  ? '무료 체험은 계정 하나에 대화 상대 $_trialPersonas명까지인데 이미 쓰셨어요. '
                      '다 만든 뒤 요금제를 고르면 대화할 수 있어요. $noTrialRefundText'
                  : '다 만들면 $_trialDays일 동안 무료로 써 볼 수 있어요.',
        ),
      // 결제했던 구독이 끝났으면 '체험 없음'이 아니라 '구독이 끝났어요'
      'EXPIRED' || 'NO_TRIAL' when view['hadPaidPlan'] == true => ('$title · 구독이 끝났어요', ended),
      'EXPIRED' => ('$title · 무료 체험이 끝났어요', ended),
      'NO_TRIAL' => (
          '$title · 요금제를 골라 주세요',
          '무료 체험은 계정 하나에 대화 상대 $_trialPersonas명까지예요. 요금제를 고르면 바로 대화할 수 있어요. '
              '$noTrialRefundText',
        ),
      'CANCELING' => (
          '$title · ${planWord(view['planName'])} 해지 신청됨',
          '${_lastDay(view['periodEnd'])}까지 쓸 수 있고, 그 뒤로는 결제하지 않아요.',
        ),
      'PAST_DUE' => (
          '$title · 결제가 되지 않았어요',
          '카드를 확인해 주세요. 며칠 동안 자동으로 다시 결제해 보고, 그동안에도 계속 쓰실 수 있어요.',
        ),
      'ADMIN' => (
          '관리자 계정',
          view['madeAt'] == null
              ? '모든 기능을 제한 없이 쓸 수 있어요.'
              : '모든 기능을 제한 없이 쓸 수 있어요. 대화 상대를 만든 날(${_date(view['madeAt'])})부터 30일씩 세고, '
                  '이번 30일 동안 대화 $used번 했어요${reset != null ? ' · 다음 30일은 ${_date(reset)}부터' : ''}.',
        ),
      _ => (
          '$title · ${planWord(view['planName'])} 구독 중',
          '다음 결제일 ${_date(view['periodEnd'])}${sub?['cardLabel'] != null ? ' · ${sub!['cardLabel']}' : ''}'
              '${view['nextPlan'] != null && _plan(view['nextPlan']) != null ? '\n${_date(view['periodEnd'])}부터 ${toPlanWord(_plan(view['nextPlan'])!)} 바뀌어요.' : ''}',
        ),
    };
    return AppCard(
      padding: const EdgeInsets.fromLTRB(20, 18, 16, 18),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(head, style: AppText.heading),
                const SizedBox(height: 4),
                Text(keepAll(body), style: AppText.small.copyWith(color: AppColors.ink2, fontSize: 14)),
                if (limit != null && view['active'] == true && status != 'NOT_STARTED') ...[
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      UsageRing(left: (left as num?)?.toInt() ?? 0, limit: (limit as num).toInt(), size: 76),
                      const SizedBox(width: 16),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              status == 'TRIAL' || status == 'PRELAUNCH' ? '무료 대화 $left번 남음' : '이번 달 남은 대화 $left번',
                              style: AppText.label.copyWith(color: AppColors.primaryDeep),
                            ),
                            const SizedBox(height: 2),
                            Text('$used / $limit번 썼어요', style: AppText.small),
                          ],
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Text(
                    keepAll(status == 'TRIAL'
                        ? '한 번 말하고 대답을 들으면 1번이에요. 다 쓰면 무료 체험이 끝날 때까지 목소리 대신 글로 대답해요.'
                        : status == 'PRELAUNCH'
                            ? '한 번 말하고 대답을 들으면 1번이에요. '
                                '${view['madeAt'] != null ? '대화 상대를 만든 날(${_date(view['madeAt'])})부터' : '대화 상대를 만든 날부터'} '
                                '30일마다 다시 채워지고, 다 쓰면 목소리 대신 글로 대답해요.'
                                '${reset != null ? ' 다음은 ${_date(reset)}에 다시 채워져요.' : ''}'
                            : '한 번 말하고 대답을 들으면 1번이에요. 다 쓰면 목소리 대신 글로 대답해요.'
                                '${reset != null ? ' ${_date(reset)}에 다시 채워져요.' : ''}'),
                    style: AppText.small,
                  ),
                ],
              ],
            ),
          ),
          if (status == 'CANCELING')
            TextButton(onPressed: _resume, child: const Text('해지 신청 취소'))
          else if (status == 'ACTIVE' || status == 'PAST_DUE')
            TextButton(
              onPressed: _cancel,
              style: TextButton.styleFrom(foregroundColor: AppColors.muted),
              child: const Text('해지'),
            ),
        ],
      ),
    );
  }

  Widget _accountNotes(Map<String, dynamic> me) {
    final lines = <String>[
      if (_prelaunch)
        '결제가 열리기 전까지는 기간${_prelaunchLimit == null ? '도, 대화 횟수도' : ''} 제한 없이 무료예요'
            '${_prelaunchLimit == null ? '' : '(대화 상대마다, 다 만든 날부터 30일마다 대화 ${commas(_prelaunchLimit!)}번까지)'}. '
            '결제가 열리면 그때부터 원래 무료 체험(계정 하나에 대화 상대 $_trialPersonas명, $_trialDays일 동안 '
            '대화 ${me['trialConversations'] ?? 30}번)이 시작돼요.'
      else
        '무료 체험은 계정 하나에 대화 상대 $_trialPersonas명까지예요. 다 만든 날부터 $_trialDays일 동안 '
            '대화 ${me['trialConversations'] ?? 30}번까지 쓸 수 있어요(남은 무료 체험 ${me['trialsLeft'] ?? 0}명). '
            '$noTrialRefundText',
      '$_betaName: 처음 ${me['betaFamilies'] ?? 100}가족은 할인 자리를 받은 날(추억·가족 요금제를 처음 할인가로 '
          '결제하거나, 할인가 요금제로 내리기를 예약한 날)부터 ${me['betaMonths'] ?? 12}개월 동안 우리 가족의 '
          '추억·가족 요금제를 할인가로 내요'
          '${me['betaUntil'] != null ? ' (우리 가족은 ${_date(me['betaUntil'])}까지)' : ''}. '
          '할인이 끝나기 30일 전에 이 화면에서 알려 드려요. 동의하시면 원래 가격으로 계속 쓰실 수 있고, '
          '동의하지 않으시면 할인이 끝난 뒤 처음 돌아오는 결제일에 그 대화 상대의 구독이 끝나요. '
          '지금 가격은 출시 때 가격이라 나중에 바뀔 수 있어요.',
      if (me['cardLabel'] != null) '등록된 카드(${me['cardLabel']})로 다른 대화 상대의 구독도 바로 시작할 수 있어요.',
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final l in lines)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('· ', style: AppText.small),
                Expanded(child: Text(keepAll(l), style: AppText.small)),
              ],
            ),
          ),
        if (me['testMode'] == true && !_prelaunch) ...[
          const SizedBox(height: 8),
          const Notice(
            '지금은 연습용 결제라 실제로 돈이 나가지 않아요.',
            icon: Icons.info_outline_rounded,
          ),
        ],
      ],
    );
  }

  Widget _planGrid(Map<String, dynamic> view, bool wide) {
    final cards = [for (final id in planOrder) if (_plan(id) != null) _planCard(view, _plan(id)!)];
    return LayoutBuilder(
      builder: (context, constraints) {
        // 카드가 모두 나란히 들어갈 만큼 넓을 때만 가로로(카드 하나에 230px 이상, 사이 14px)
        if (constraints.maxWidth < cards.length * 230 + (cards.length - 1) * 14) {
          return Column(
            children: [for (final c in cards) Padding(padding: const EdgeInsets.only(bottom: 12), child: c)],
          );
        }
        return IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              for (var i = 0; i < cards.length; i++) ...[
                if (i > 0) const SizedBox(width: 14),
                Expanded(child: cards[i]),
              ],
            ],
          ),
        );
      },
    );
  }

  List<String> _features(Map<String, dynamic> plan) {
    // 횟수는 서버 값(요금제마다 다르다)
    final count = plan['conversations'] == null
        ? '대화 횟수 제한 없음'
        : '한 달 대화 ${commas(plan['conversations'] as num)}번 (다 쓰면 목소리 대신 글로 대답해요)';
    String everything(String id, String fallback) => '${planWord(_plan(id)?['name'] ?? fallback)}의 모든 것';
    return switch (plan['id']) {
      'memories' => [count, '그분 목소리로 대답해요', '녹음·질문 답·남긴 글을 더할수록 더 그분다워져요'],
      'family' => [count, everything('memories', '추억'), '가족을 초대해 함께 대화하기 (대화 횟수는 함께 써요)'],
      _ => [count],
    };
  }

  Widget _planCard(Map<String, dynamic> view, Map<String, dynamic> plan) {
    final id = plan['id'] as String;
    final status = view['status'] as String;
    final paying = status == 'ACTIVE' || status == 'CANCELING' || status == 'PAST_DUE';
    final current = paying && view['plan'] == id;
    final scheduled = view['nextPlan'] == id;
    final pendingChange = view['nextPlan'] != null;
    // 결제 전: 결제 대신 구독 신청. 연습 결제로 켜 둔 구독이 있어도 결제가 열리기 전에는 서버가
    // 요금제 바꾸기를 막으므로(409) 똑같이 신청으로 받는다.
    final requestMode = _prelaunch && status != 'ADMIN';
    final requested = requestMode && BillingStore.requestOf(view)?['plan'] == id;
    final label = requestMode
        ? (current ? '지금 쓰는 요금제' : requested ? '신청했어요 ✓' : '구독 신청하기')
        : current
        ? (status == 'PAST_DUE'
            ? '카드 다시 등록하기'
            : status == 'CANCELING'
                ? '해지 신청 취소하기'
                : pendingChange
                    ? '이 요금제 계속 쓰기'
                    : '지금 쓰는 요금제')
        : scheduled
            ? '다음 결제부터 바뀌어요'
            : paying
                ? '이 요금제로 바꾸기'
                : '이 요금제로 시작하기';
    final enabled = requestMode
        ? !_busy && !requested && !current
        : !_busy &&
            status != 'ADMIN' &&
            !scheduled &&
            view['paymentPending'] != true &&
            (!current || status == 'CANCELING' || status == 'PAST_DUE' || pendingChange);
    final beta = plan['beta'] == true && _charge(plan) != plan['price'];
    final VoidCallback? onPressed = !enabled
        ? null
        : requestMode
            ? () => _requestPlan(id)
            : () => _onPlan(id, current, status);
    final Widget button = requested
        // 신청한 카드: 눌리지 않지만 흐리게 보이지 않게(초록 글씨·테두리)
        ? OutlinedButton(
            onPressed: null,
            style: OutlinedButton.styleFrom(
              disabledForegroundColor: AppColors.primary,
              side: const BorderSide(color: AppColors.primary, width: 1.4),
            ),
            child: Text(label),
          )
        : OutlinedButton(onPressed: onPressed, child: Text(label));
    final card = AppCard(
      padding: const EdgeInsets.fromLTRB(22, 22, 22, 20),
      borderColor: requested ? AppColors.primary : null,
      color: requested ? AppColors.primarySoft : null,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 8,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(plan['name'], style: AppText.title.copyWith(fontSize: 21)),
              if (current) const StatusChip('사용 중'),
              if (requested) const StatusChip('신청함', positive: true),
            ],
          ),
          const SizedBox(height: 10),
          if (beta) ...[
            StatusChip('$_betaName가', positive: true),
            const SizedBox(height: 8),
          ],
          Text.rich(
            TextSpan(
              text: _won(_charge(plan)),
              style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800, color: AppColors.ink),
              children: const [TextSpan(text: ' / 월', style: TextStyle(fontSize: 14, color: AppColors.muted))],
            ),
          ),
          if (beta)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text('원래 가격 월 ${_won(plan['price'] as num)}', style: AppText.small),
            ),
          const SizedBox(height: 4),
          const Text('대화 상대 1명 기준', style: AppText.small),
          const SizedBox(height: 14),
          for (final f in _features(plan))
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Padding(
                    padding: EdgeInsets.only(top: 2),
                    child: Icon(Icons.check_rounded, size: 17, color: AppColors.primary),
                  ),
                  const SizedBox(width: 8),
                  Expanded(child: Text(keepAll(f), style: const TextStyle(fontSize: 14.5, height: 1.5))),
                ],
              ),
            ),
          const SizedBox(height: 10),
          SizedBox(width: double.infinity, child: button),
          if (requested)
            Center(
              child: TextButton(
                onPressed: _busy ? null : _cancelRequest,
                style: TextButton.styleFrom(foregroundColor: AppColors.muted),
                child: const Text('신청 취소'),
              ),
            ),
        ],
      ),
    );
    if (!requested) return card;
    // 신청한 카드는 테두리를 한 겹 더 둘러 눈에 띄게
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: AppColors.primary, width: 1.6),
      ),
      child: Padding(padding: const EdgeInsets.all(1.5), child: card),
    );
  }

  void _onPlan(String id, bool current, String status) {
    if (!current) {
      _choose(id);
      return;
    }
    if (status == 'PAST_DUE') {
      _choose(id); // 새 카드를 결제 창에서 받는다
    } else if (status == 'CANCELING') {
      _resume();
    } else if (_current?['nextPlan'] != null) {
      _run(id, mode: 'change'); // 같은 요금제를 고르면 예약한 변경이 취소된다
    } else {
      _resume();
    }
  }

  Widget _paymentRow(Map<String, dynamic> p) {
    final ok = p['status'] == 'DONE';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${p['personaTitle'] != null ? '${personaLabel(p['personaTitle'], p['personaRelation'])} · ' : ''}'
                  '${planWord(p['planName'])} · ${_won(p['amount'] as num)}',
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
                Text(
                  '${_date(p['at'])}${ok ? '' : p['status'] == 'UNKNOWN' ? ' · 결제가 됐는지 확인하는 중' : ' · 결제되지 않았어요${p['failure'] != null ? ': ${p['failure']}' : ''}'}',
                  style: AppText.small.copyWith(color: ok ? AppColors.muted : AppColors.danger),
                ),
              ],
            ),
          ),
          if (p['receiptUrl'] != null)
            TextButton(onPressed: () => goToUrl(p['receiptUrl']), child: const Text('영수증')),
        ],
      ),
    );
  }
}

/// 결제 직전 확인(payment 문서: 정기결제 · 결제 취소(청약철회) 제한 두 가지를 따로 체크).
/// [downgradeText] 가 있으면 다음 결제부터 내리는 경우(지금 결제하지 않는다).
class _ConsentDialog extends StatefulWidget {
  final Map<String, dynamic> plan;
  final String personaTitle;
  final bool upgrade;

  /// 올리기면 지금 바로 결제할 차액(서버가 같은 셈으로 미리 알려 준 값)
  final num? upgradeCharge;

  /// 다른 페르소나에 등록해 둔 카드로 바로 결제할 때 그 카드 이름(없으면 결제 창에서 카드를 받는다)
  final String? savedCard;
  final String periodEnd;
  final bool testMode;

  /// 할인 이름('첫 100가족 할인')
  final String betaName;
  final int betaMonths;

  /// 이 가족이 이미 초기 가족 요금을 받았으면 끝나는 날('10월 13일')
  final String? betaUntil;

  /// 요금제를 내릴 때 안내 글(다음 결제부터 바뀐다)
  final String? downgradeText;

  /// 결제 직전 안내와 체크 글(서버 GET /legal 의 payment: {notice, items: [{type, label}]}).
  final Future<Map<String, dynamic>> Function() loadPayment;

  const _ConsentDialog({
    required this.plan,
    required this.personaTitle,
    required this.upgrade,
    this.upgradeCharge,
    required this.savedCard,
    required this.periodEnd,
    required this.testMode,
    required this.betaName,
    required this.betaMonths,
    this.betaUntil,
    this.downgradeText,
    required this.loadPayment,
  });

  @override
  State<_ConsentDialog> createState() => _ConsentDialogState();
}

class _ConsentDialogState extends State<_ConsentDialog> {
  bool _recurring = false;
  bool _withdrawal = false;

  /// 결제 직전 안내와 체크 글은 서버 것 그대로(결제 기록의 증빙 글과 같게). 못 읽으면 결제 단추를 막는다.
  late Future<Map<String, dynamic>> _payment = widget.loadPayment();

  static String? _label(Map<String, dynamic>? payment, String type) {
    for (final item in payment?['items'] as List? ?? const []) {
      if (item is Map && item['type'] == type) {
        final label = '${item['label'] ?? ''}'.trim();
        return label.isEmpty ? null : label;
      }
    }
    return null;
  }

  /// 안내와 체크 글 두 가지를 모두 읽었는가(아니면 결제하지 않는다).
  static bool _complete(Map<String, dynamic>? payment) =>
      '${payment?['notice'] ?? ''}'.trim().isNotEmpty &&
      _label(payment, 'recurring') != null &&
      _label(payment, 'withdrawal_limit') != null;

  @override
  Widget build(BuildContext context) {
    final plan = widget.plan;
    final today = DateTime.now();
    final price = (plan['chargePrice'] ?? plan['price']) as num;
    final beta = plan['beta'] == true && price != plan['price'];
    final who = widget.personaTitle;
    return AlertDialog(
      title: Text(widget.upgrade ? '$who · ${toPlanWord(plan)} 바꾸기' : '$who · ${planWord(plan['name'])} 구독하기'),
      content: SizedBox(
        width: 440,
        child: SingleChildScrollView(
          child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              keepAll(widget.downgradeText ??
                  (widget.upgrade
                  ? '$who 요금제가 지금 바로 ${toPlanWord(plan)} 바뀌고, ${widget.periodEnd}까지 남은 날만큼 더 내는 금액'
                      '${widget.upgradeCharge != null ? ' ${won(widget.upgradeCharge!)}' : ''}을 지금 결제해요. '
                      '그다음부터는 매달 ${won(price)}이 결제돼요.'
                  : widget.savedCard != null
                      ? '$who 요금으로 오늘 등록된 카드(${widget.savedCard})에서 ${won(price)}을 결제하고, '
                          '매달 ${today.day}일에 같은 금액이 자동으로 결제돼요.'
                      : '$who 요금으로 오늘 ${won(price)}을 결제하고, 매달 ${today.day}일에 같은 금액이 '
                          '자동으로 결제돼요. 카드는 다음 결제 화면(토스페이먼츠)에서 등록해요.')),
              style: AppText.lead,
            ),
            if (beta && widget.downgradeText == null)
              Padding(
                padding: const EdgeInsets.only(top: 10),
                child: Text(
                  keepAll('${widget.betaName}가예요. ${widget.betaUntil != null ? '${widget.betaUntil}까지' : '${widget.betaMonths}개월 동안'} '
                      '월 ${won(price)}이고, 그 뒤에는 동의하시면 원래 가격 월 ${won(plan['price'] as num)}으로 이어져요.'),
                  style: AppText.small,
                ),
              ),
            const SizedBox(height: 12),
            FutureBuilder<Map<String, dynamic>>(
              future: _payment,
              builder: (context, snap) {
                if (snap.connectionState != ConnectionState.done) {
                  return const Padding(padding: EdgeInsets.all(16), child: Loading());
                }
                final payment = snap.data;
                if (snap.hasError || !_complete(payment)) {
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      const ErrorBox('결제 전 안내를 불러오지 못했어요. 잠시 뒤 다시 해 주세요.'),
                      Align(
                        alignment: Alignment.centerLeft,
                        child: TextButton.icon(
                          onPressed: () => setState(() {
                            _payment = widget.loadPayment();
                          }),
                          icon: const Icon(Icons.refresh_rounded, size: 18),
                          label: const Text('다시 불러오기'),
                        ),
                      ),
                    ],
                  );
                }
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    // 결제 직전에 결제 취소(청약철회) 안내를 한 번 더(전자상거래법 제13조 · 약관 5번)
                    Notice('${payment!['notice']}', icon: Icons.receipt_long_outlined),
                    const SizedBox(height: 8),
                    ConsentCheck(
                      label: _label(payment, 'recurring')!,
                      value: _recurring,
                      onChanged: (v) => setState(() => _recurring = v),
                    ),
                    ConsentCheck(
                      label: _label(payment, 'withdrawal_limit')!,
                      value: _withdrawal,
                      onChanged: (v) => setState(() => _withdrawal = v),
                    ),
                  ],
                );
              },
            ),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton(
                onPressed: () => showLegalDoc(context, 'payment', callName: who),
                child: const Text('자세히 보기'),
              ),
            ),
            if (widget.testMode)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text('연습용 결제라 실제로 돈이 나가지 않아요.', style: AppText.small),
              ),
          ],
          ),
        ),
      ),
      actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('취소')),
        FutureBuilder<Map<String, dynamic>>(
          future: _payment,
          builder: (context, snap) => FilledButton(
            // 안내와 체크 글을 읽지 못했으면 결제하지 않는다
            onPressed: _complete(snap.data) && _recurring && _withdrawal ? () => Navigator.pop(context, true) : null,
            style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
            child: Text(widget.downgradeText != null ? '바꾸기' : '결제하기'),
          ),
        ),
      ],
    );
  }
}

/// 결제가 열리기 전 구독 신청 확인(결제 동의 창 대신). 돈이 나가지 않아 체크할 것이 없다.
/// [change] 면 이미 다른 요금제를 신청해 두었고 이 요금제로 바꾼다. 신청하면 true, 취소하면 null.
class _RequestDialog extends StatelessWidget {
  final Map<String, dynamic> plan;
  final String personaTitle;

  /// 할인 이름('첫 100가족 할인')
  final String betaName;
  final bool change;

  const _RequestDialog({
    required this.plan,
    required this.personaTitle,
    required this.betaName,
    this.change = false,
  });

  @override
  Widget build(BuildContext context) {
    final price = (plan['chargePrice'] ?? plan['price']) as num;
    final beta = plan['beta'] == true && price != plan['price'];
    Widget row(String label, Widget value) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 7),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(width: 84, child: Text(label, style: AppText.small)),
              Expanded(child: value),
            ],
          ),
        );
    const strong = TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5);
    return AlertDialog(
      title: Text(change ? '${toPlanWord(plan)} 신청을 바꿀까요?' : '${planWord(plan['name'])} 구독 신청'),
      content: SizedBox(
        width: 420,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Container(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                decoration: BoxDecoration(color: AppColors.sunk, borderRadius: BorderRadius.circular(14)),
                child: Column(
                  children: [
                    row('대화 상대', Text(personaTitle, style: strong)),
                    row('요금제', Text(planWord(plan['name']), style: strong)),
                    row(
                      '월 가격',
                      Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text('월 ${won(price)}', style: strong),
                          if (beta) ...[
                            const SizedBox(height: 2),
                            // 좁은 화면에서도 줄을 바꿀 수 있게 꼬리표 대신 글로
                            Text(
                              keepAll('$betaName가'),
                              style: AppText.small.copyWith(color: AppColors.primary, fontWeight: FontWeight.w700),
                            ),
                            Text(keepAll('원래 가격 월 ${won(plan['price'] as num)}'), style: AppText.small),
                          ],
                        ],
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 14),
              Text(
                keepAll('결제가 열리면 알려 드려요. 그때 직접 결제하셔야 구독이 시작돼요. '
                    '신청만으로는 돈이 나가지 않고, 그전까지는 지금처럼 무료로 쓰실 수 있어요.'),
                style: AppText.lead,
              ),
            ],
          ),
        ),
      ),
      actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('취소')),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
          child: Text(change ? '바꾸기' : '신청하기'),
        ),
      ],
    );
  }
}

/// 다른 화면에서 "요금제 보기"로 보낼 때. 페르소나를 알면 그 페르소나의 요금제로.
void openPlans(BuildContext context, [String? personaId]) =>
    context.go(personaId == null ? '/plans' : '/plans?persona=$personaId');
