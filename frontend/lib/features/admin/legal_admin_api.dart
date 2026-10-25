import 'package:dio/dio.dart';

import '../../core/network/api_client.dart';

/// 관리자 화면 · 법률 자문 반영(2026-10-22) API. 관리자만 열린다.
///
///   동의 녹음 검수   GET /admin/subject-consents?status=&page=&size= · GET /admin/subject-consents/{id}
///                    POST /admin/subject-consents/{id}/review
///   대화 상대 관리   GET /admin/personas/{id}/legal · POST /admin/personas/{id}/objection
///                    POST /admin/personas/{id}/hold/clear · POST /admin/personas/{id}/hold/extend
///   목록            GET /admin/holds · GET /admin/personas/unable
///   결제·환불       GET /admin/payments?userId=&personaId=&q=&page=&size= · POST /admin/payments/{id}/refund
///   확인 문자       GET /admin/guardian-sms · POST /admin/guardian-sms/{personaId}/sent (SMS_PROVIDER=manual 일 때만)
///
/// 아무것도 자동으로 보내지 않는다(문자·메일 문안은 복사만). 환불은 토스 상점관리자에서 한 뒤 기록만 남긴다.
/// 시험에서는 이 클래스를 이어받은 가짜를 넣는다.
class LegalAdminApi {
  const LegalAdminApi();

  /// 한 쪽 읽기: 줄과 거른 뒤 전체 수(meta.total), 그리고 meta 그대로.
  Future<({List<Map<String, dynamic>> rows, int total, Map<String, dynamic> meta})> _page(
    String path, [
    Map<String, dynamic>? query,
  ]) async {
    try {
      final response = await ApiClient.dio.get(path, queryParameters: query);
      final body = response.data;
      final data = body is Map ? body['data'] : null;
      final meta = _map(body is Map ? body['meta'] : null);
      final rows = [
        for (final row in data is List ? data : const [])
          if (row is Map) Map<String, dynamic>.from(row),
      ];
      final total = (meta['total'] as num?)?.toInt();
      return (rows: rows, total: total ?? rows.length, meta: meta);
    } on DioException catch (error) {
      throw ApiClient.toApiException(error);
    }
  }

  static Map<String, dynamic> _map(Object? data) => data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};

  // ── 그분 동의 녹음 검수 ──

  /// 검수 목록. [status]: submitted(기본)·hold·passed·rerequest·refused·interrupted·all.
  Future<({List<Map<String, dynamic>> rows, int total, Map<String, dynamic> meta})> consents({
    String status = 'submitted',
    int page = 1,
    int size = 50,
  }) =>
      _page('/admin/subject-consents', {'status': status, 'page': page, 'size': size});

  /// 녹음 하나: 확인 단어·시각·길이·단계·앞선 녹음·이야기 녹음·법정대리인·10항목·지난 판정.
  Future<Map<String, dynamic>> consent(String id) async => _map(await ApiClient.get('/admin/subject-consents/$id'));

  /// 판정. [checklist] {"1":bool,…,"10":bool}, [verdict] pass·rerequest·hold·refused.
  Future<Map<String, dynamic>> review(
    String id, {
    required Map<String, bool> checklist,
    required String verdict,
    String? noteToOwner,
    String? adminNote,
    bool allowMore = false,
  }) async =>
      _map(await ApiClient.post('/admin/subject-consents/$id/review', {
        'checklist': checklist,
        'verdict': verdict,
        if (noteToOwner != null && noteToOwner.trim().isNotEmpty) 'noteToOwner': noteToOwner.trim(),
        if (adminNote != null && adminNote.trim().isNotEmpty) 'adminNote': adminNote.trim(),
        if (verdict == 'rerequest' && allowMore) 'allowMore': true,
      }));

  // ── 대화 상대 관리(유족 명단·이의·멈춤·결제) ──

  /// {persona, owner, bereaved, minor, hold, objections, subjectConsent, templates, billing}.
  Future<Map<String, dynamic>> persona(String personaId) async =>
      _map(await ApiClient.get('/admin/personas/$personaId/legal'));

  /// 이의 접수: 바로 멈추고 7일 뒤 지운다. {holdAt, deleteAfter, refund}.
  Future<Map<String, dynamic>> objection(
    String personaId, {
    required String reason,
    String? fromName,
    String? fromRelation,
    String? note,
  }) async =>
      _map(await ApiClient.post('/admin/personas/$personaId/objection', {
        'reason': reason,
        if (fromName != null && fromName.trim().isNotEmpty) 'fromName': fromName.trim(),
        if (fromRelation != null && fromRelation.trim().isNotEmpty) 'fromRelation': fromRelation.trim(),
        if (note != null && note.trim().isNotEmpty) 'note': note.trim(),
      }));

  /// 멈춤 해제(이의 철회·착오).
  Future<Map<String, dynamic>> clearHold(String personaId, {String? note}) async =>
      _map(await ApiClient.post('/admin/personas/$personaId/hold/clear', {'note': note?.trim() ?? ''}));

  /// 지우는 날 미루기(1~30일).
  Future<Map<String, dynamic>> extendHold(String personaId, {required int days, String? note}) async =>
      _map(await ApiClient.post('/admin/personas/$personaId/hold/extend', {'days': days, 'note': note?.trim() ?? ''}));

  /// 멈춘 대화 상대: [{personaId, title, ownerMasked, reason, holdAt, deleteAfter}].
  Future<List<Map<String, dynamic>>> holds() async => (await _page('/admin/holds')).rows;

  /// 처리를 멈춘 예전 대화 상대(대표 보고용, 단추 없음): [{personaId, kind, title, ownerMasked, step, hasVoice,
  /// voiceProvider, createdAt, readyAt, lastChatAt, members}]. kind: unable · minor_not_guardian ·
  /// status_changed(살아 계신 분을 돌아가신 분으로 바꿨는데 유족 명단이 아직 없음).
  Future<List<Map<String, dynamic>>> unable() async => (await _page('/admin/personas/unable')).rows;

  // ── 결제·환불 ──

  /// 결제 줄(§3.4): 결제 후 첫 대화·체험 사용·환불 판단 도움(refundHint)·환불 기록.
  Future<({List<Map<String, dynamic>> rows, int total, Map<String, dynamic> meta})> payments({
    String? q,
    String? personaId,
    String? userId,
    int page = 1,
    int size = 50,
  }) =>
      _page('/admin/payments', {
        if (q != null && q.trim().isNotEmpty) 'q': q.trim(),
        if (personaId != null) 'personaId': personaId,
        if (userId != null) 'userId': userId,
        'page': page,
        'size': size,
      });

  /// 환불했다고 적는다(토스는 부르지 않는다). [reason] no_chat_7d·no_trial_7d·mismatch·prorated·other.
  Future<Map<String, dynamic>> refund(String paymentId, {required int amount, required String reason, String? note}) async =>
      _map(await ApiClient.post('/admin/payments/$paymentId/refund', {
        'amount': amount,
        'reason': reason,
        if (note != null && note.trim().isNotEmpty) 'note': note.trim(),
      }));

  // ── 만 14세 미만 확인 문자(대표가 손으로 보냄) ──

  /// 보낼 문자: [{personaId, title, phone, code, text, requestedAt, expiresAt, expired}] + meta.
  /// 대기열이 켜져 있는지(설정 SMS_PROVIDER)는 서버가 meta.provider 로 주면 그것, 아니면 /intake/meta 의
  /// under14Open(만 14세 미만을 받는가 = 확인 문자를 보낼 수 있는가)을 meta 에 붙여 [GuardianSmsPage.active] 가 본다.
  Future<({List<Map<String, dynamic>> rows, int total, Map<String, dynamic> meta})> guardianSms() async {
    final result = await _page('/admin/guardian-sms');
    if (result.meta.containsKey('provider')) return result;
    try {
      final open = _map(await ApiClient.get('/intake/meta'))['under14Open'];
      if (open is bool) return (rows: result.rows, total: result.total, meta: {...result.meta, 'under14Open': open});
    } catch (_) {
      // 못 읽으면 켜진 것으로 본다(쌓인 문자가 있으면 보여야 하므로)
    }
    return result;
  }

  /// 보냈어요: 전체 번호·확인 번호를 지우고 가린 번호만 남긴다.
  Future<Map<String, dynamic>> guardianSmsSent(String personaId) async =>
      _map(await ApiClient.post('/admin/guardian-sms/$personaId/sent'));
}
