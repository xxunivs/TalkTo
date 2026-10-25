import 'network/api_client.dart';
import 'session.dart';

/// 사용 통계(개발자 화면 /admin/stats 에서 본다). 화면을 연 것과 몇몇 단추 누름만 서버에 남긴다.
///
/// - 로그인했을 때만 보낸다.
/// - 보내고 기다리지 않는다. 실패해도 화면은 아무 일 없이 그대로다.
/// - 대화 내용·녹음·이름·이메일 같은 것은 절대 넣지 않는다(서버도 허용한 이름·작은 값만 받는다).
class Analytics {
  Analytics._();

  /// 시험에서 바꿔 끼운다. 기본은 POST /analytics/events.
  static Future<void> Function(String name, Map<String, Object?> props) sender = _post;

  static String? _lastScreen;

  /// 화면을 열었다(screen_view). 같은 사람이 같은 화면을 연달아 열면 한 번만 보낸다.
  static void screen(String name) {
    if (!session.loggedIn) return;
    final key = '${session.user?['id']}|$name';
    if (key == _lastScreen) return;
    _lastScreen = key;
    _send('screen_view', {'screen': name});
  }

  /// 단추 누름 같은 화면 이벤트. 서버가 허용한 이름만 남긴다
  /// (more_tile_clicked, plans_viewed, legal_doc_opened, share_link_sent, join_declined).
  static void event(String name, [Map<String, Object?> props = const {}]) {
    if (!session.loggedIn) return;
    _send(name, props);
  }

  static void _send(String name, Map<String, Object?> props) {
    try {
      sender(name, props).catchError((Object _) {});
    } catch (_) {
      // 통계는 화면을 깨지 않는다
    }
  }

  static Future<void> _post(String name, Map<String, Object?> props) async {
    await ApiClient.post('/analytics/events', {'name': name, 'props': props});
  }

  /// 화면 주소 → 통계에 남길 화면 이름(id 는 뺀다).
  ///   /            home            /p/:id            persona_chat
  ///   /new         persona_create  /p/:id/more       persona_more
  ///   /plans       plans           /p/:id/recordings persona_recordings
  ///   /account     account         /p/:id/materials  persona_materials
  ///   /consent     consent         /p/:id/materials/add  persona_material_add
  ///   /support     support         /p/:id/support    persona_support
  ///   /legal/:key  legal           /p/:id/edit       persona_edit
  ///   /login       login           /p/:id/family     persona_family
  ///   /join/:token join            /admin/stats      admin_stats
  ///   /admin       admin
  /// 초대 열쇠(/join/ 뒤)는 절대 보내지 않는다.
  static String screenName(String path) {
    final parts = path.split('/').where((s) => s.isNotEmpty).toList();
    if (parts.isEmpty) return 'home';
    switch (parts.first) {
      case 'p':
        final rest = parts.skip(2).toList(); // 'p' 와 id 를 뺀다
        if (rest.isEmpty) return 'persona_chat';
        if (rest.length >= 2 && rest[0] == 'materials' && rest[1] == 'add') return 'persona_material_add';
        return 'persona_${rest.first}';
      case 'new':
        return 'persona_create';
      case 'join':
        return 'join';
      case 'legal':
        return 'legal';
      case 'auth':
        return 'auth_callback';
      case 'admin':
        // 관리자 화면은 둘째 칸까지만(/admin/interviews/:id 의 신청·대화 상대 id 를 남기지 않게)
        return parts.length >= 2 ? 'admin_${parts[1]}' : 'admin';
      default:
        return parts.join('_');
    }
  }

  static String? _lastPath;

  /// 라우터가 화면을 바꿀 때마다 부른다(router.dart). 화면 이름을 남기고,
  /// 약관 문서·요금제 화면은 그 이벤트도 함께 남긴다.
  static void trackLocation(Uri uri) {
    final path = uri.path;
    if (path == _lastPath) return;
    _lastPath = path;
    final name = screenName(path);
    final before = _lastScreen;
    screen(name);
    final changed = _lastScreen != before;
    if (name == 'legal') {
      final parts = path.split('/').where((s) => s.isNotEmpty).toList();
      if (parts.length >= 2) event('legal_doc_opened', {'doc': parts[1]});
    } else if (name == 'plans' && changed) {
      event('plans_viewed', {'withPersona': uri.queryParameters.containsKey('persona')});
    }
  }
}
