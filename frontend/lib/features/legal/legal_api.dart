import '../../core/network/api_client.dart';

/// 동의문·약관과 동의 기록 API (talkto_api_server /api/v1/legal, /consents).
/// 원문은 서버의 app/static/legal/*.md 하나뿐이고, 화면은 그대로 받아 그린다.
class LegalApi {
  static Map<String, dynamic>? _index;
  static final Map<String, Map<String, dynamic>> _docs = {};

  /// 문서 목록 · 첫 로그인(가입) 필수 동의 항목(account) · 사업자 정보(business). 한 번 받으면 기억해 둔다.
  static Future<Map<String, dynamic>> index({bool refresh = false}) async {
    if (_index != null && !refresh) return _index!;
    _index = Map<String, dynamic>.from(await ApiClient.get('/legal'));
    return _index!;
  }

  /// 기억해 둔 목록과 문서를 버린다. 동의 화면을 열 때 불러, 열어 둔 탭에서도 지금 서버 글을 보이게 한다.
  static void forget() {
    _index = null;
    _docs.clear();
  }

  /// 첫 로그인(가입) 때 받는 필수 동의 항목: [{type, label, doc, kind}]. 서버 순서 그대로.
  static Future<List<Map<String, dynamic>>> accountItems() async =>
      [for (final item in (await index())['account'] as List? ?? const []) Map<String, dynamic>.from(item)];

  static Future<Map<String, dynamic>> business() async =>
      Map<String, dynamic>.from((await index())['business'] as Map? ?? const {});

  /// 결제 직전 안내와 체크 글(서버 legal.PAYMENT_NOTICE·PAYMENT_LABELS): {notice, items: [{type, label}]}.
  /// 서버가 아직 주지 않으면 빈 값 — 부르는 쪽이 결제 단추를 막는다.
  static Future<Map<String, dynamic>> payment() async =>
      Map<String, dynamic>.from((await index())['payment'] as Map? ?? const {});

  /// 법정대리인 확인 체크 글(서버 legal.GUARDIAN_LABELS): {attest, consent, overseas}. 없으면 빈 값.
  static Future<Map<String, dynamic>> guardianLabels() async =>
      Map<String, dynamic>.from((await index())['guardian'] as Map? ?? const {});

  /// 문서 하나: {key, title, version, kind, body}. 본문의 {{호칭}} 은 화면이 바꾼다([callName]).
  static Future<Map<String, dynamic>> doc(String key) async {
    final cached = _docs[key];
    if (cached != null) return cached;
    final data = Map<String, dynamic>.from(await ApiClient.get('/legal/$key'));
    _docs[key] = data;
    return data;
  }

  /// 내 동의 내역(지금 상태): [{type, title, agreed, version, agreedAt, personaId, personaTitle}]
  static Future<List<Map<String, dynamic>>> myConsents() async =>
      [for (final row in await ApiClient.get('/consents/me') as List) Map<String, dynamic>.from(row)];

  /// 계정 필수 동의를 기록한다. 남은 필수 항목(consentsNeeded)을 돌려준다.
  static Future<List<String>> accept(List<String> types) async {
    final data = await ApiClient.post('/consents/accept', {'types': types});
    return [for (final t in (data as Map)['consentsNeeded'] as List? ?? const []) t.toString()];
  }

  /// 회원 탈퇴. 내가 만든 대화 상대와 기록을 모두 지우고(함께 쓰던 가족도 더는 이야기할 수 없다),
  /// 가족이 나눠 준 대화 상대에서는 빠지며 거기서 나눈 내 대화도 지운 뒤 계정을 닫는다.
  /// 돌려주는 값: {deleted, doneAt, backupUntil, vendorOk, message}(message = 서버가 정한 지운 뒤 안내).
  static Future<Map<String, dynamic>> deleteAccount() async {
    final data = await ApiClient.call('DELETE', '/auth/me', data: {'confirm': '탈퇴'});
    return data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};
  }
}

/// 본문의 {{호칭}} 을 그 대화 상대를 부르는 말로 바꾼다(없으면 '그분').
/// 동의 녹음 대본의 {{확인 단어}}·{{보호자}} 는 녹음 화면이 채운다. 문서 목록에서 열면 뜻만 보이게 바꾼다.
String fillCallName(String body, String? callName) {
  final name = (callName == null || callName.trim().isEmpty) ? '그분' : callName.trim();
  return body
      .replaceAll('{{호칭}}', name)
      .replaceAll('{{확인 단어}}', '녹음할 때 화면에 보이는 낱말 두 개')
      .replaceAll('{{보호자}}', '엄마(아빠)');
}
