import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../features/legal/legal_api.dart';
import '../features/share/pending_invite.dart';
import 'network/api_client.dart';
import 'platform/browser.dart';

/// 로그인 상태. 토큰은 브라우저(앱)에 저장해 새로고침해도 로그인이 유지된다.
class Session extends ChangeNotifier {
  static const _accessKey = 'talkto.access';
  static const _refreshKey = 'talkto.refresh';

  Map<String, dynamic>? user;
  bool ready = false;

  /// 앱을 열 때 내 정보(/auth/me)를 잠깐 못 읽었으면 이만큼 뒤 한 번 더 읽는다(시험에서 줄인다).
  @visibleForTesting
  static Duration retryDelay = const Duration(seconds: 2);

  bool get loggedIn => user != null;
  bool get isAdmin => user?['role'] == 'ADMIN';
  String get name => (user?['name'] ?? '') as String;

  /// 어떤 계정으로 들어왔는지: kakao, google, 또는 null(이메일).
  String? get provider => user?['provider'] as String?;
  String? get email => user?['email'] as String?;

  /// 아직 받지 않은 계정 필수 동의(서버 /auth/me 의 consentsNeeded). 모든 필수 동의는 가입할 때 받는다
  /// (초대받은 가족도 같다).
  List<String> get consentsNeeded =>
      [for (final t in user?['consentsNeeded'] as List? ?? const []) t.toString()];

  /// 로그인했지만 필수 동의가 빠져 있어 동의 화면부터 거쳐야 하는지. 관리자는 서버에서도 면제다.
  bool get needsConsent => loggedIn && !isAdmin && consentsNeeded.isNotEmpty;

  Future<void> init() async {
    ApiClient.onUnauthorized = () => _clear();
    ApiClient.onTokens = _saveTokens;
    ApiClient.onConsentRequired = _onConsentRequired;
    // 로그인 전에 연 가족 초대(카카오·구글 로그인에서 돌아오면 라우터가 그 화면으로 이어 준다)
    await PendingInvite.load();
    final prefs = await SharedPreferences.getInstance();
    final access = prefs.getString(_accessKey);
    if (access != null) {
      ApiClient.setTokens(access, prefs.getString(_refreshKey));
      // 배포 중 서버를 바꾸는 몇 초 동안 열면 한 번 실패한다. 조금 뒤 한 번 더 확인해 로그인 화면으로
      // 잘못 보내지 않는다.
      for (var attempt = 0; attempt < 2 && user == null; attempt++) {
        if (attempt > 0) await Future<void>.delayed(retryDelay);
        try {
          user = Map<String, dynamic>.from(await ApiClient.get('/auth/me'));
        } on ApiException catch (error) {
          // 로그인이 정말 풀렸을 때(401)만 지운다. 연결이 잠깐 안 되거나 서버를 바꾸는 중(5xx)이면
          // 저장한 로그인을 남겨 두고, 다음에 열 때 다시 확인한다.
          if (error.statusCode == 401) {
            await _clear(notify: false);
            break;
          }
          if (!ApiException.isTransient(error)) break;
        } catch (_) {
          // 위와 같다(알 수 없는 실패로 로그인을 지우지 않는다)
        }
      }
    }
    ready = true;
    notifyListeners();
  }

  Future<void> login(String email, String password) async {
    final tokens = await ApiClient.post('/auth/login', {'email': email, 'password': password});
    await _start(tokens);
  }

  /// 이메일 가입. [agreements] 는 가입 화면에서 체크한 계정 필수 동의 종류(같이 기록된다).
  Future<void> signup(String email, String password, String name, {List<String> agreements = const []}) async {
    final tokens = await ApiClient.post(
      '/auth/signup',
      {'email': email, 'password': password, 'name': name, 'agreements': agreements},
    );
    await _start(tokens);
  }

  /// 동의 화면에서 체크한 계정 필수 동의를 기록한다. 남은 것이 없으면 needsConsent 가 꺼진다.
  Future<void> acceptConsents(List<String> types) async {
    final left = await LegalApi.accept(types);
    if (user != null) user = {...user!, 'consentsNeeded': left};
    notifyListeners();
  }

  /// 내 정보(/auth/me)를 다시 읽는다. 동의 상태가 바뀌었을 때 쓴다.
  Future<void> reloadMe() async {
    if (!loggedIn) return;
    user = Map<String, dynamic>.from(await ApiClient.get('/auth/me'));
    notifyListeners();
  }

  bool _consentCheck = false;

  /// 서버가 403 consent_required 를 주면 /auth/me 를 다시 읽는다(여러 요청이 한꺼번에 막혀도 한 번만).
  Future<void> _onConsentRequired() async {
    if (_consentCheck || !loggedIn) return;
    _consentCheck = true;
    try {
      await reloadMe();
    } catch (_) {
      // 읽지 못하면 그대로 둔다(다음 요청에서 다시 막히면 또 해 본다)
    } finally {
      _consentCheck = false;
    }
  }

  /// 서버가 켜 둔 로그인 방법. {kakao: bool, google: bool, signup: bool}
  static Future<Map<String, dynamic>> providers() async {
    try {
      return Map<String, dynamic>.from(await ApiClient.get('/auth/providers'));
    } catch (_) {
      return const {};
    }
  }

  /// 카카오·구글 로그인 화면으로 넘어간다. 끝나면 서버가 이 페이지의 #/auth?ticket=… 로 돌려보낸다.
  void startSocialLogin(String provider) {
    final returnTo = Uri.encodeQueryComponent(currentPageUrl());
    goToUrl('${ApiConfig.baseUrl}/auth/oauth/$provider/start?return_to=$returnTo');
  }

  /// 소셜 로그인에서 받은 한 번 쓰는 표를 토큰으로 바꾼다.
  Future<void> exchangeTicket(String ticket) async {
    final tokens = await ApiClient.post('/auth/oauth/exchange', {'ticket': ticket});
    await _start(tokens);
  }

  /// 이 브라우저(앱)에서만 로그인을 지운다. 서버에 알리지 않아 다른 기기(휴대폰·컴퓨터)의 로그인은 그대로다.
  /// 다른 계정으로 들어가기·가입 동의를 하지 않고 나가기처럼, 이 기기에서 계정만 바꾸는 데 쓴다
  /// (할머니 휴대폰에서 다른 계정으로 바꿔도 내 휴대폰은 로그아웃되지 않게).
  /// 가족 초대([PendingInvite])는 지우지 않는다.
  Future<void> signOutHere() => _clear();

  /// 로그아웃. 서버가 이 계정의 모든 로그인을 끝낸다(모든 기기에서 로그아웃).
  /// 가족 초대([PendingInvite])는 지우지 않는다('다른 계정으로 들어가기'로 이어 간다).
  Future<void> logout() async {
    try {
      await ApiClient.post('/auth/logout');
    } catch (_) {
      // 서버가 꺼져 있어도 이 기기에서는 로그아웃한다
    }
    await _clear();
  }

  Future<void> _start(dynamic tokens) async {
    ApiClient.setTokens(tokens['bearerToken'], tokens['refreshToken']);
    await _saveTokens(tokens['bearerToken'], tokens['refreshToken']);
    user = Map<String, dynamic>.from(await ApiClient.get('/auth/me'));
    notifyListeners();
  }

  Future<void> _saveTokens(String access, String refresh) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_accessKey, access);
    await prefs.setString(_refreshKey, refresh);
  }

  Future<void> _clear({bool notify = true}) async {
    ApiClient.clearToken();
    user = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_accessKey);
    await prefs.remove(_refreshKey);
    if (notify) notifyListeners();
  }
}

final session = Session();
