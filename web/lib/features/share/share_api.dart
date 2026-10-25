import 'package:flutter/services.dart';

import '../../core/network/api_client.dart';
import '../../core/platform/browser.dart' as browser;
import '../../shared/korean.dart';

/// 가족 공유 API (talkto_api_server /api/v1 의 /personas/{id}/shares, /invites).
///
/// 초대 열쇠(token)는 주소창이 아니라 요청 몸통으로만 보낸다(서버 기록에 남지 않게).
/// 열쇠는 링크를 만들거나 다시 보낼 때 한 번만 받고, 목록에는 없다.
class ShareApi {
  ShareApi._();

  // ── 만든 사람(주인) ──

  /// 가족과 함께 쓰기 화면: {allowed, reason, prelaunch, subjectKind, need, consentItems, cap, used,
  /// familyRows, addressSuggestions, voiceShareable, items}.
  static Future<Map<String, dynamic>> list(String personaId) async =>
      Map<String, dynamic>.from(await ApiClient.get('/personas/$personaId/shares'));

  /// 초대 링크 하나 만들기 → {share, url}. [agreements] 는 처음 한 번 체크한 주인 확인,
  /// [familyRow] 는 가족 표에서 고른 줄 이름, [prefill] 은 받는 분에 대해 적어 둔 것(모두 선택).
  static Future<Map<String, dynamic>> create(
    String personaId, {
    List<String> agreements = const [],
    String? familyRow,
    Map<String, dynamic> prefill = const {},
  }) async =>
      Map<String, dynamic>.from(await ApiClient.post('/personas/$personaId/shares', {
        if (agreements.isNotEmpty) 'agreements': agreements,
        if (familyRow != null) 'familyRow': familyRow,
        'prefill': prefill,
      }));

  /// 주인 확인만 하기(링크는 만들지 않는다) → 아직 남은 확인 종류(서버의 {need}).
  /// 그분 상태를 바꿔 확인이 거둬졌으면 여기서 다시 확인해야 함께 쓰는 가족이 이어서 이야기한다.
  /// 자리가 다 찼거나 요금제·잠시 멈춤으로 링크를 만들 수 없을 때도 받는다.
  static Future<List<String>> confirm(String personaId, List<String> agreements) async {
    final data = await ApiClient.post('/personas/$personaId/shares/confirm', {'agreements': agreements});
    final need = data is Map ? data['need'] : null;
    return [for (final t in need is List ? need : const []) '$t'];
  }

  /// 같은 자리에 새 링크(전 링크는 더 쓰이지 않는다) → {share, url}.
  static Future<Map<String, dynamic>> renew(String personaId, String shareId) async =>
      Map<String, dynamic>.from(await ApiClient.post('/personas/$personaId/shares/$shareId/renew'));

  /// 적어 둔 것 고치기. 몸통은 ShareProfile 그대로(비우는 칸은 null). 주인이면 ShareItem,
  /// 함께하는 가족이 자기 것을 고치면 MemberMe 를 돌려준다.
  static Future<Map<String, dynamic>> update(String personaId, String shareId, Map<String, dynamic> profile) async =>
      Map<String, dynamic>.from(await ApiClient.patch('/personas/$personaId/shares/$shareId', profile));

  /// 링크 취소 · 가족 내보내기 · (함께하는 가족이) 그만두기 → 그 가족이 나눈 대화를 지웠으면 서버가 정한
  /// 안내 글(서버 백업에 남는 날까지 — 응답 deletion.message), 지운 것이 없으면 null.
  static Future<String?> end(String personaId, String shareId) async =>
      deletionMessageOf(await ApiClient.delete('/personas/$personaId/shares/$shareId'));

  /// 가족과 함께 쓰기 그만두기(모든 가족) → (빠진 수, 가족이 나눈 대화를 지웠으면 그 안내 글).
  static Future<({int removed, String? deletion})> stop(String personaId) async {
    final data = await ApiClient.post('/personas/$personaId/shares/stop');
    return (
      removed: (data is Map ? (data['removed'] as num?)?.toInt() : null) ?? 0,
      deletion: deletionMessageOf(data),
    );
  }

  // ── 초대 받는 사람 ──

  /// 로그인 전에도 볼 수 있는 짧은 안내: {state, ownerName, addressTerm, ownerTitle, isSelf, expiresAt}.
  static Future<Map<String, dynamic>> preview(String token) async =>
      Map<String, dynamic>.from(await ApiClient.post('/invites/preview', {'token': token}));

  /// 로그인한 뒤: {state(join|member|owner|used|expired|unavailable|invalid), personaId, ownerName, …}.
  static Future<Map<String, dynamic>> resolve(String token) async =>
      Map<String, dynamic>.from(await ApiClient.post('/invites/resolve', {'token': token}));

  /// 함께하기 → {personaId, shareId, already}.
  static Future<Map<String, dynamic>> accept(String token, Map<String, dynamic> profile) async =>
      Map<String, dynamic>.from(
        await ApiClient.post('/invites/accept', {'token': token, 'agree': true, 'profile': profile}),
      );
}

/// 휴대폰 공유 창·클립보드. 단추를 누른 그 자리에서 부른다. 시험에서 바꿔 끼운다.
class LinkSharing {
  LinkSharing._();

  static bool Function() canShare = browser.canShare;
  static Future<bool?> Function({required String url, String? text}) share = browser.shareLink;
  static Future<bool> Function(String text) copy = _copy;

  static Future<bool> _copy(String text) async {
    try {
      await Clipboard.setData(ClipboardData(text: text));
      return true;
    } catch (_) {
      return false;
    }
  }
}

/// 링크에 받는 분의 이름·관계·부르는 말(함께하려면 꼭 있어야 하는 칸, member_profile_sheet 의
/// requiredProfileKeys)을 모두 적어 두었는가. 그러면 받는 분은 확인 카드에서 동의에 체크하고
/// '네, 맞아요. 함께하기'만 누르면 되고, 아니면 빈 칸을 직접 적은 뒤 '함께하기'를 누른다.
bool invitePrefilled(Map? prefill) =>
    const ['myName', 'myRelation', 'addressTerm'].every((key) => '${prefill?[key] ?? ''}'.trim().isNotEmpty);

/// 링크와 함께 보내는 글(받는 분이 어르신이어도 따라 할 수 있게 세 단계).
/// [addressTerm] 은 받는 분이 그분을 부르는 말(모르면 '가족이 만든 대화 상대'),
/// [prefilled] 는 [invitePrefilled]. 어느 쪽이든 동의 한 줄에 체크해야 단추가 눌리고,
/// 처음 가입하는 분은 로그인한 뒤 계정 동의 화면을 한 번 거친다.
String inviteShareText({required String ownerName, String? addressTerm, bool prefilled = false}) {
  final term = addressTerm?.trim() ?? '';
  final who = term.isEmpty ? '가족이 만든 대화 상대와' : josa(term, '과', '와');
  final last = prefilled
      ? "③ 동의에 체크하고 '네, 맞아요. 함께하기'를 누르면"
      : "③ 내 정보를 적고 동의에 체크한 뒤 '함께하기'를 누르면";
  return '$ownerName님이 TalkTo에서 $who 이야기할 수 있게 초대했어요. '
      "① 링크를 누르고 ② '카카오로 시작하기'를 누른 뒤 $last 바로 이야기할 수 있어요. "
      '처음 들어오면 ②와 ③ 사이에 동의 화면이 나와요.';
}

/// '어르신께 보내실 때' 전화로 알려 드릴 말([inviteShareText] 의 세 단계와 같다).
String inviteTipText({bool prefilled = false}) {
  final last = prefilled ? '동의에 체크한 다음 네 맞아요를' : '내 정보를 적고 동의에 체크한 다음 함께하기를';
  return "링크를 보낸 뒤 전화로 '링크를 누르고, 카카오로 시작하기를 누르고, $last 누르시면 돼요'라고 "
      '알려 드리면 쉬워요. 처음 들어오시면 중간에 동의 화면이 나와요. 만 14세 미만은 함께할 수 없어요.';
}

/// 서버가 부르는 말 추천을 주지 않을 때 쓰는 표(서버 sharing.address_suggestions 와 같은 표,
/// 그분이 여자분인지 남자분인지 모를 때처럼 두 줄을 합친다).
const _fallbackSuggestions = {
  '아들': ['엄마', '어머니', '아빠', '아버지'],
  '딸': ['엄마', '어머니', '아빠', '아버지'],
  '손자': ['할머니', '할아버지'],
  '손녀': ['할머니', '할아버지'],
  '외손자': ['외할머니', '할머니', '외할아버지', '할아버지'],
  '외손녀': ['외할머니', '할머니', '외할아버지', '할아버지'],
  '며느리': ['어머님', '아버님'],
  '사위': ['장모님', '어머님', '장인어른', '아버님'],
  '배우자': ['여보', '당신'],
  '조카': ['이모', '고모', '큰엄마', '삼촌', '큰아버지', '작은아버지'],
  '형제자매': ['언니', '누나', '오빠', '형'],
};

/// '내가 그분을 부르는 말' 추천. 서버 추천([server], 관계 → 부르는 말)이 있으면 그것을 쓴다.
/// 말 높이(반말·존댓말)는 추측하지 않는다.
List<String> addressSuggestionsFor(String? relation, [Map? server]) {
  final key = relation?.trim() ?? '';
  if (key.isEmpty) return const [];
  final fromServer = server?[key];
  if (fromServer is List) return [for (final s in fromServer) '$s'];
  if (server != null && server.isNotEmpty) return const [];
  return _fallbackSuggestions[key] ?? const [];
}

/// 서버 시각 → '10월 24일'.
String monthDay(DateTime? t) => t == null ? '' : '${t.month}월 ${t.day}일';

/// 지우기 응답의 안내 글(deletion.message — 서버 백업에 남는 날까지). 없으면 null.
String? deletionMessageOf(Object? data) {
  final deletion = data is Map ? data['deletion'] : null;
  final text = deletion is Map ? '${deletion['message'] ?? ''}'.trim() : '';
  return text.isEmpty ? null : text;
}
