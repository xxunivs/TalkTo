import 'dart:convert';

import 'package:dio/dio.dart';

import '../../../core/network/api_client.dart';

/// 대화 상대를 만들고 쓰기 전에 받는 확인(2026-10-22 법률 자문). 모두 만든 분 본인만 부른다.
///
///   그분 동의 녹음   GET /personas/{id}/subject-consent · POST …/start · POST …/{rid}/audio · POST …/{rid}/abandon
///   만 19세 미만     PUT /personas/{id}/minor (예전에 만든 살아 계신 분 대화 상대가 처음 답할 때 한 번)
///   법정대리인       GET /personas/{id}/guardian · POST …/guardian/consent · …/guardian/sms · …/guardian/sms/verify
///   유족 확인        GET · PUT /personas/{id}/bereaved
///
/// 막히면 서버가 {detail, code}(409)로 답한다. 화면은 code 로 갈래를 고르고 글은 그대로 보인다.
class SubjectConsentApi {
  SubjectConsentApi._();

  /// {required, mode, status, attempts, submittedCount, maxAttempts, latest, scriptDoc, canStart,
  /// blockCode, blockMessage, guardian}.
  static Future<Map<String, dynamic>> status(String personaId) async =>
      Map<String, dynamic>.from(await ApiClient.get('/personas/$personaId/subject-consent'));

  /// 녹음 한 번을 시작한다(확인 단어 발급) → {id, attempt, words, issuedAt, expiresAt, mode, scriptDoc,
  /// scriptVersion, maxSeconds, maxMb}.
  static Future<Map<String, dynamic>> start(String personaId) async =>
      Map<String, dynamic>.from(await ApiClient.post('/personas/$personaId/subject-consent/start'));

  /// 녹음한 파일을 받은 그대로 올린다(고치거나 바꾸지 않는다). [steps] 는 대본 단계가 바뀐 때
  /// ({step, atMs}), [interrupted] 는 중간에 멈춘 녹음, [refused] 는 '그분이 원하지 않으세요'를 누른 녹음.
  /// → {id, status}.
  static Future<Map<String, dynamic>> upload(
    String personaId,
    String recordingId, {
    required List<int> bytes,
    required String filename,
    required List<Map<String, int>> steps,
    bool interrupted = false,
    bool refused = false,
    String reason = '',
    void Function(double progress)? onProgress,
  }) async {
    final dot = filename.lastIndexOf('.');
    final form = FormData.fromMap({
      'audio': MultipartFile.fromBytes(bytes, filename: filename),
      'steps': jsonEncode(steps),
      'interrupted': interrupted ? 'true' : 'false',
      'refused': refused ? 'true' : 'false',
      'reason': reason,
      'format': dot < 0 ? '' : filename.substring(dot + 1).toLowerCase(),
    });
    final data = await ApiClient.call(
      'POST',
      '/personas/$personaId/subject-consent/$recordingId/audio',
      data: form,
      onSendProgress: (sent, total) {
        if (total > 0) onProgress?.call(sent / total);
      },
    );
    return data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};
  }

  /// 녹음을 올리지 못하고 멈췄다(소리 없이 기록만). [reason]: hidden · mic · network · user · error.
  static Future<void> abandon(String personaId, String recordingId, String reason) =>
      ApiClient.post('/personas/$personaId/subject-consent/$recordingId/abandon', {'reason': reason});

  /// 예전에 만든 살아 계신 분 대화 상대: 그분이 만 19세 미만인지 처음 답한다(한 번만).
  /// {minor, birthYm?, guardianBasis?, guardianAttest?}.
  static Future<void> setMinor(String personaId, Map<String, dynamic> body) =>
      ApiClient.put('/personas/$personaId/minor', body);
}

/// 만 19세 미만 대화 상대의 법정대리인 확인(만 14세 미만은 동의 두 가지와 확인 문자까지).
class GuardianApi {
  GuardianApi._();

  /// {needed, mode, missing, phoneMasked, smsVerifiedAt, under14Open, labels}.
  static Future<Map<String, dynamic>> status(String personaId) async =>
      Map<String, dynamic>.from(await ApiClient.get('/personas/$personaId/guardian'));

  /// 법정대리인 동의 두 가지(아이 정보 모으기·해외 회사로 보내기). [attest] 는 법정대리인 확인이 빠진
  /// 대화 상대에서만 함께 보낸다.
  static Future<void> consent(String personaId, {bool attest = false}) =>
      ApiClient.post('/personas/$personaId/guardian/consent', {
        'consent': true,
        'overseas': true,
        if (attest) 'attest': true,
      });

  /// 확인 문자 받기 → {queued: true}(TalkTo 가 손으로 보낸다) 또는 보낸 결과.
  static Future<Map<String, dynamic>> sendSms(String personaId, String phone) async {
    final data = await ApiClient.post('/personas/$personaId/guardian/sms', {'phone': phone});
    return data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};
  }

  static Future<void> verify(String personaId, String code) =>
      ApiClient.post('/personas/$personaId/guardian/sms/verify', {'code': code});
}

/// 돌아가신 분 대화 상대의 신청 자격과 유족 명단.
class BereavedApi {
  BereavedApi._();

  /// 적어 둔 것. 아직 없으면 null.
  static Future<Map<String, dynamic>?> load(String personaId) async {
    try {
      final data = await ApiClient.get('/personas/$personaId/bereaved');
      return data is Map ? Map<String, dynamic>.from(data) : null;
    } on ApiException catch (error) {
      if (error.statusCode == 404) return null;
      rethrow;
    }
  }

  /// {applicantRelation, consentingName?, consentingRelation?, members, noneLeft, pledge: true}.
  static Future<void> save(String personaId, Map<String, dynamic> body) =>
      ApiClient.put('/personas/$personaId/bereaved', body);
}
