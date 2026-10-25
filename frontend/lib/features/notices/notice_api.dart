import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

import '../../core/network/api_client.dart';
import '../../core/session.dart';

/// 앱 안 알림 API (talkto_api_server /api/v1/notices). 푸시·문자는 보내지 않는다 — 알림은 앱을 열었을 때만 본다.
///
///   GET  /notices?unread=0|1   최신 50개 + meta.unread(안 읽은 수)
///   POST /notices/{id}/read    하나 읽음
///   POST /notices/read-all     모두 읽음
///
/// 알림 한 줄: {id, kind, title, body, action: {label, route}?, personaId, createdAt, readAt}.
/// 글은 TalkTo 가 하는 말이다(대화 상대가 보낸 것처럼 쓰지 않는다 — 서버가 정한 글 그대로 보인다).
class NoticeApi {
  NoticeApi._();

  static Future<({List<Map<String, dynamic>> rows, int unread})> list({bool unreadOnly = false}) async {
    try {
      final response = await ApiClient.dio.get('/notices', queryParameters: {'unread': unreadOnly ? 1 : 0});
      final body = response.data;
      final data = body is Map ? body['data'] : null;
      final meta = body is Map ? body['meta'] : null;
      final rows = [
        for (final row in data is List ? data : const [])
          if (row is Map) Map<String, dynamic>.from(row),
      ];
      final unread = meta is Map ? (meta['unread'] as num?)?.toInt() : null;
      return (rows: rows, unread: unread ?? rows.where((r) => r['readAt'] == null).length);
    } on DioException catch (error) {
      throw ApiClient.toApiException(error);
    }
  }

  static Future<void> read(String id) => ApiClient.post('/notices/$id/read');

  static Future<void> readAll() => ApiClient.post('/notices/read-all');
}

/// 내 알림과 안 읽은 수. 앱 셸의 종(안 읽은 수)과 알림 화면이 같이 본다.
/// 시간마다 묻지 않고, 화면이 다시 보이거나 다른 화면으로 옮길 때 [load] 한다.
class NoticeStore extends ChangeNotifier {
  List<Map<String, dynamic>>? items;
  int unread = 0;
  Object? error;
  Future<void>? _loading;

  NoticeStore() {
    // 로그아웃하면 다른 사람 알림이 남지 않게 비운다
    session.addListener(() {
      if (!session.loggedIn && (items != null || unread != 0)) {
        items = null;
        unread = 0;
        error = null;
        notifyListeners();
      }
    });
  }

  Future<void> load() => _loading ??= _fetch().whenComplete(() => _loading = null);

  Future<void> _fetch() async {
    try {
      final result = await NoticeApi.list();
      items = result.rows;
      unread = result.unread;
      error = null;
    } catch (e) {
      // 종 숫자는 못 읽어도 화면을 막지 않는다(알림 화면에서만 알린다)
      error = e;
    }
    notifyListeners();
  }

  /// 하나 읽음(바로 숫자를 줄이고 서버에 남긴다).
  Future<void> markRead(Map<String, dynamic> notice) async {
    if (notice['readAt'] != null) return;
    notice['readAt'] = DateTime.now().toUtc().toIso8601String();
    if (unread > 0) unread--;
    notifyListeners();
    try {
      await NoticeApi.read('${notice['id']}');
    } catch (_) {
      // 다음에 다시 읽으면 맞춰진다
    }
  }

  Future<void> markAllRead() async {
    await NoticeApi.readAll();
    final now = DateTime.now().toUtc().toIso8601String();
    for (final n in items ?? const <Map<String, dynamic>>[]) {
      n['readAt'] ??= now;
    }
    unread = 0;
    notifyListeners();
  }
}

final noticeStore = NoticeStore();
