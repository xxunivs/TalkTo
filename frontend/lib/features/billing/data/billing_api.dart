import 'package:flutter/foundation.dart';

import '../../../core/network/api_client.dart';
import '../../../core/session.dart';

/// 무료 체험이 계정에 한 명뿐이라고 알리는 곳 끝에 붙이는 한 줄(약관 5번 · 먼저 써 볼 수 없었으니 대화했어도
/// 첫 결제 7일 안 전액 환불).
const noTrialRefundText = '무료 체험을 쓰지 않은 대화 상대는 처음 결제한 날부터 7일 안에 말씀하시면 대화하셨더라도 전액 돌려드려요.';

/// 요금제·구독·결제 API (talkto_api_server /api/v1/billing). 결제는 페르소나마다 받는다.
class BillingApi {
  /// 가족(계정) 정보 + 페르소나마다의 요금제 상태(personas).
  static Future<Map<String, dynamic>> me() async =>
      Map<String, dynamic>.from(await ApiClient.get('/billing/me'));

  /// 페르소나 하나의 요금제 상태(남은 대화 횟수 등).
  static Future<Map<String, dynamic>> persona(String personaId) async =>
      Map<String, dynamic>.from(await ApiClient.get('/billing/personas/$personaId'));

  /// 이 페르소나의 요금제 고르기. 카드가 없으면 {checkoutUrl}, 등록된 카드가 있으면 바로
  /// {started, charged} · 이미 구독 중이면 {changed, charged} 또는 {scheduled}.
  /// mode = 화면이 안내한 방법(change · saved_card · window), expectedAmount = 화면에 보여 준 값.
  /// 그사이 달라졌으면 서버가 결제하지 않고 409 로 돌려준다(다시 보여 주면 된다).
  /// [agreements] = 결제 확인 창에서 체크한 것(['recurring', 'withdrawal_limit']). 서버는 둘 다 있어야 결제한다
  /// (지금 요금제를 다시 골라 예약한 변경을 취소할 때만 없어도 된다).
  static Future<Map<String, dynamic>> checkout(
    String personaId,
    String plan,
    String returnTo, {
    String? mode,
    int? expectedAmount,
    List<String>? agreements,
  }) async =>
      Map<String, dynamic>.from(
        await ApiClient.post('/billing/checkout', {
          'personaId': personaId,
          'plan': plan,
          'returnTo': returnTo,
          if (mode != null) 'mode': mode,
          if (expectedAmount != null) 'expectedAmount': expectedAmount,
          if (agreements != null) 'agreements': agreements,
        }),
      );

  static Future<void> cancel(String personaId) => ApiClient.post('/billing/cancel', {'personaId': personaId});

  static Future<void> resume(String personaId) => ApiClient.post('/billing/resume', {'personaId': personaId});

  /// 초기 가족 요금이 끝난 뒤 정식 요금으로 이어 가겠다는 동의. 없으면 그때 구독이 끝난다.
  static Future<void> consentRegularPrice() => ApiClient.post('/billing/regular-price-consent');

  static Future<List<Map<String, dynamic>>> payments() async {
    final data = await ApiClient.get('/billing/payments') as List;
    return [for (final item in data) Map<String, dynamic>.from(item)];
  }

  /// 결제가 열리기 전: 이 대화 상대의 요금제를 구독 신청한다(돈은 나가지 않는다).
  /// 이미 신청했으면 요금제를 바꾼다. 응답 = 그 대화 상대의 요금제 보기.
  static Future<Map<String, dynamic>> request(String personaId, String plan) async =>
      Map<String, dynamic>.from(await ApiClient.post('/billing/requests', {
        'personaId': personaId,
        'plan': plan,
      }));

  /// 구독 신청 취소. 응답 = 그 대화 상대의 요금제 보기.
  static Future<Map<String, dynamic>> cancelRequest(String personaId) async =>
      Map<String, dynamic>.from(await ApiClient.delete('/billing/requests/$personaId'));
}

/// 요금제 상태. 왼쪽 목록·첫 화면의 알림과 대화 화면이 같이 본다.
/// me 는 가족(계정) 단위, personas 는 페르소나마다(대화 화면이 보는 남은 대화 횟수 등).
class BillingStore extends ChangeNotifier {
  Map<String, dynamic>? me;
  final Map<String, Map<String, dynamic>> _personas = {};

  BillingStore() {
    session.addListener(() {
      if (!session.loggedIn && (me != null || _personas.isNotEmpty)) {
        me = null;
        _personas.clear();
        notifyListeners();
      }
    });
  }

  Future<void> load() async {
    try {
      setMe(await BillingApi.me());
    } catch (_) {
      // 요금제 표시가 없어도 나머지는 쓸 수 있다
    }
  }

  void setMe(Map<String, dynamic> data) {
    me = data;
    for (final p in (data['personas'] as List? ?? const [])) {
      final view = Map<String, dynamic>.from(p);
      _personas[view['personaId'] as String] = view;
    }
    notifyListeners();
  }

  /// 페르소나 하나의 상태를 새로 읽는다.
  Future<Map<String, dynamic>?> loadPersona(String personaId) async {
    try {
      final view = await BillingApi.persona(personaId);
      _personas[personaId] = view;
      notifyListeners();
      return view;
    } catch (_) {
      return _personas[personaId];
    }
  }

  Map<String, dynamic>? persona(String personaId) => _personas[personaId];

  /// 페르소나마다의 상태(나중에 따로 새로 읽은 것이 있으면 그것으로). 왼쪽 알림이 본다.
  List<Map<String, dynamic>> get personas => [
        for (final p in (me?['personas'] as List? ?? const []))
          _personas[p['personaId']] ?? Map<String, dynamic>.from(p),
      ];

  /// 결제가 열렸는가. 예전 서버는 이 값을 주지 않으므로 없으면 열린 것으로 본다.
  bool get paymentsOpen => me?['paymentsOpen'] != false;

  /// 결제 전 모드(구독 신청만 받고, 무료 체험에 한도가 없다).
  bool get prelaunch => me != null && !paymentsOpen;

  /// 구독 신청해 둔 대화 상대 수.
  int get requestCount => personas.where((p) => requestOf(p) != null).length;

  /// 대화 상대의 구독 신청({plan, planName, status, createdAt}). 신청하지 않았으면 null.
  static Map<String, dynamic>? requestOf(Map<String, dynamic>? view) {
    final request = view?['request'];
    if (request is! Map || request['plan'] == null) return null;
    if (request['status'] != null && request['status'] != 'REQUESTED') return null;
    return Map<String, dynamic>.from(request);
  }

  /// 대화를 보낸 뒤 서버가 알려 준 남은 횟수로 바로 바꾼다(요금제 상태를 다시 읽지 않아도 되게).
  void setConversationsLeft(String personaId, int? left) {
    final current = _personas[personaId];
    if (current == null || left == null) return;
    _personas[personaId] = {
      ...current,
      'conversationsLeft': left,
      if (current['conversationsLimit'] is num && current['textOnly'] != true)
        'conversationsUsed': (current['conversationsLimit'] as num).toInt() - left,
      // 다 쓰면 끊지 않고 글로 대답한다
      'textOnly': current['active'] == true && left == 0,
    };
    notifyListeners();
  }

  /// '이번 달 남은 대화 12번' · '무료 대화 3번 남음'. 제한이 없거나 쓸 수 없으면 null.
  /// 결제 전(PRELAUNCH)이면 '결제 전 무료 · 횟수 제한 없음'(운영에서 한 달 상한을 두면 '무료 대화 N번 남음').
  /// 초대로 함께하는 가족(role: member)은 가족이 함께 쓰는 횟수라 앞에 '함께 쓰는 '을 붙인다.
  static String? conversationsText(Map<String, dynamic>? view) {
    final left = (view?['conversationsLeft'] as num?)?.toInt();
    if (view != null && view['status'] == 'PRELAUNCH' && view['active'] == true && view['conversationsLimit'] == null) {
      return '결제 전 무료 · 횟수 제한 없음';
    }
    if (view == null || left == null || view['active'] != true) return null;
    final shared = view['role'] == 'member' ? '함께 쓰는 ' : '';
    if (left == 0) return '$shared대화 횟수를 다 써서 지금은 목소리 대신 글로 대답해요';
    return switch (view['status']) {
      'TRIAL' || 'NOT_STARTED' || 'PRELAUNCH' => '$shared무료 대화 $left번 남음',
      _ => '$shared이번 달 남은 대화 $left번',
    };
  }

  /// 체험이 며칠 남았는지(오늘 끝나면 0).
  static int? trialDaysLeft(Map<String, dynamic>? view) {
    final end = DateTime.tryParse((view?['trialEndsAt'] ?? '') as String)?.toLocal();
    if (end == null) return null;
    final left = end.difference(DateTime.now());
    return left.isNegative ? 0 : (left.inHours / 24).ceil();
  }
}

final billingStore = BillingStore();

/// 요금제 화면에서 대화 상대를 가리키는 말. 서버가 호칭과 다른 관계를 함께 주면 곁들인다:
/// '할머니 (외할머니)'. 호칭이 같은 분이 둘이어도 어느 분인지 가를 수 있게.
String personaLabel(Object? title, Object? relation) {
  final shown = (title as String?)?.trim() ?? '';
  final extra = (relation as String?)?.trim() ?? '';
  final name = shown.isEmpty ? '그분' : shown;
  return extra.isEmpty || extra == name ? name : '$name ($extra)';
}
