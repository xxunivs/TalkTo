import 'dart:async';
import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// 로그인 전에 연 가족 초대 링크(#/join/<token>). 로그인·가입·필수 동의를 마치면 이 초대로 돌아온다.
///
/// 카카오·구글 로그인은 주소의 # 뒤를 버리고 돌아오므로, 떠나기 전에 브라우저에 잠시 적어 둔다
/// ({token, savedAt, ownerName}). 개인정보 처리방침의 약속('로그인과 가입을 마칠 때까지 … 잠시 저장해요 …
/// 하루가 지난 것은 쓰지 않고, 다음에 TalkTo를 열 때 지워요')대로:
///   로그인(가입·필수 동의까지)을 마치고 초대 화면이 서버에 물어 답을 받으면 바로 지운다(그 뒤로는 주소의
///   열쇠로 충분하다). 링크가 끝난 것(기간 지남·없음·함께 쓸 수 없음)을 알거나 거절하면('제가 아니에요'·
///   '다음에 할게요') 지운다. 하루가 지난 것은 읽을 때([load]·[path]) 지운다.
/// 로그아웃할 때는 지우지 않는다('다른 계정으로 들어가기'로 로그아웃하면 초대 화면이 로그인 전 모습으로
/// 다시 열리며 다시 적어 둔다).
class PendingInvite {
  PendingInvite._();

  static const key = 'talkto.pendingInvite';
  static const maxAge = Duration(hours: 24);

  /// 서버가 만드는 열쇠 모양(token_urlsafe). 다른 글은 적어 두지 않는다.
  static final _shape = RegExp(r'^[A-Za-z0-9_-]{8,128}$');

  /// 라우터가 동기로 본다(Session.init 에서 [load] 로 읽어 둔다). 시험에서는 바로 넣는다.
  static String? token;
  static String? ownerName;
  static DateTime? savedAt;

  static bool isToken(String? value) => value != null && _shape.hasMatch(value);

  /// 이어 갈 초대 화면 주소. 없거나 하루가 지났으면 null(하루가 지났으면 브라우저에서도 지운다).
  static String? get path {
    final t = token;
    if (t == null) return null;
    final at = savedAt;
    if (at != null && DateTime.now().difference(at) > maxAge) {
      unawaited(clear());
      return null;
    }
    return '/join/$t';
  }

  static Future<void> load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(key);
      if (raw == null) return;
      final data = jsonDecode(raw);
      final at = data is Map ? DateTime.tryParse('${data['savedAt']}') : null;
      final t = data is Map ? data['token'] as String? : null;
      if (at == null || !isToken(t) || DateTime.now().difference(at) > maxAge) {
        await prefs.remove(key);
        return;
      }
      token = t;
      savedAt = at;
      ownerName = data['ownerName'] as String?;
    } catch (_) {
      // 읽지 못하면 초대 없이 그대로 시작한다
    }
  }

  /// 초대 화면을 열었을 때와 카카오·구글 로그인으로 떠나기 직전에 적어 둔다.
  static Future<void> save(String value, {String? owner}) async {
    if (!isToken(value)) return;
    final keepOwner = value == token ? ownerName : null;
    token = value;
    ownerName = owner ?? keepOwner;
    savedAt = DateTime.now();
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(
        key,
        jsonEncode({'token': value, 'savedAt': savedAt!.toIso8601String(), 'ownerName': ownerName}),
      );
    } catch (_) {
      // 적어 두지 못해도 이 탭에서는 이어 간다
    }
  }

  static Future<void> clear() async {
    token = null;
    ownerName = null;
    savedAt = null;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(key);
    } catch (_) {
      // 지우지 못해도 하루 뒤에 버린다
    }
  }
}
