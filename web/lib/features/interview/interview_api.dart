import '../../core/network/api_client.dart';
import '../personas/widgets/basics_form.dart' show selfRelation;
import '../share/roles.dart';

/// 기억 인터뷰 신청(talkto_api_server app/api/v1/interviews.py).
///
/// 대화 상대를 만든 분(주인)이 신청하고, 대표가 그분께 전화나 영상 통화로 이야기를 들어 드린다.
/// 인터뷰 받는 분은 만든 분이다(그분 본인은 인터뷰하지 않는다). 그래서 '나 자신' 대화 상대와 함께 쓰는
/// 가족에게는 신청 단추를 보이지 않는다(서버도 막는다). 시험에서는 이 클래스를 이어받은 가짜를 넣는다.
class InterviewApi {
  const InterviewApi();

  /// GET /personas/{id}/interview — {available, reason, request|null, prefill{name, relation}, days}.
  Future<Map<String, dynamic>> state(String personaId) async =>
      Map<String, dynamic>.from(await ApiClient.get('/personas/$personaId/interview') as Map);

  /// POST /personas/{id}/interview — 신청. 돌려준 값은 [state] 와 같은 모양.
  Future<Map<String, dynamic>> apply(String personaId, Map<String, dynamic> body) async =>
      Map<String, dynamic>.from(await ApiClient.post('/personas/$personaId/interview', body) as Map);

  /// POST /personas/{id}/interview/cancel — 아직 끝나지 않은 신청 취소.
  Future<Map<String, dynamic>> cancel(String personaId) async =>
      Map<String, dynamic>.from(await ApiClient.post('/personas/$personaId/interview/cancel') as Map);

  // ── 관리자 ──

  /// GET /admin/interviews?status= — 신청 목록(전화번호 없이).
  Future<List<Map<String, dynamic>>> adminList({String? status}) async {
    final data = await ApiClient.get('/admin/interviews', query: {if (status != null) 'status': status});
    if (data is! List) return const [];
    return [for (final row in data) if (row is Map) Map<String, dynamic>.from(row)];
  }

  /// GET /admin/interviews/{id} — 신청 하나(전화번호·메모 포함).
  Future<Map<String, dynamic>> adminDetail(String id) async =>
      Map<String, dynamic>.from(await ApiClient.get('/admin/interviews/$id') as Map);

  /// PATCH /admin/interviews/{id} — 상태(requested·scheduled·done·cancelled)·메모(빈 글이면 지움).
  Future<Map<String, dynamic>> adminUpdate(String id, {String? status, String? note}) async =>
      Map<String, dynamic>.from(
        await ApiClient.patch('/admin/interviews/$id', {
          if (status != null) 'status': status,
          if (note != null) 'adminNote': note,
        }) as Map,
      );

  /// GET /admin/personas/{id}/interview — {persona, requests, entries, memories, answers, rebuild}.
  Future<Map<String, dynamic>> adminPersona(String personaId) async =>
      Map<String, dynamic>.from(await ApiClient.get('/admin/personas/$personaId/interview') as Map);

  /// POST /admin/personas/{id}/interview/answers — 들은 답을 질문 번호로 넣는다(지금 답에 합친다).
  Future<Map<String, dynamic>> addAnswer(
    String personaId, {
    required String questionId,
    required String text,
    bool replace = false,
    String? requestId,
  }) async =>
      Map<String, dynamic>.from(
        await ApiClient.post('/admin/personas/$personaId/interview/answers', {
          'questionId': questionId,
          'text': text,
          'replace': replace,
          if (requestId != null) 'requestId': requestId,
        }) as Map,
      );

  /// DELETE /admin/personas/{id}/interview/answers/{entryId} — 넣은 답 하나를 지우고 그 답이 더한 부분만
  /// 지금 답에서 뺀다. {deleted, questionId, changed, answer, requiredMissing}. 반영은 '다시 만들기'로.
  Future<Map<String, dynamic>> removeAnswer(String personaId, String entryId) async {
    final data = await ApiClient.delete('/admin/personas/$personaId/interview/answers/$entryId');
    return data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};
  }

  /// POST /admin/personas/{id}/interview/memories — 기억 카드(바로 대화에 쓰인다).
  Future<Map<String, dynamic>> addMemory(
    String personaId, {
    required String text,
    String? title,
    bool askedOnly = false,
  }) async =>
      Map<String, dynamic>.from(
        await ApiClient.post('/admin/personas/$personaId/interview/memories', {
          'text': text,
          if (title != null && title.trim().isNotEmpty) 'title': title.trim(),
          'askedOnly': askedOnly,
        }) as Map,
      );

  /// DELETE /admin/personas/{id}/interview/memories/{memoryId} — 잘못 넣은 기억 카드 지우기.
  Future<void> removeMemory(String personaId, String memoryId) =>
      ApiClient.delete('/admin/personas/$personaId/interview/memories/$memoryId');

  /// POST /admin/personas/{id}/rebuild — 다시 만들기(지금 답으로 지침·기억을 다시 만든다).
  Future<Map<String, dynamic>> rebuild(String personaId) async =>
      Map<String, dynamic>.from(await ApiClient.post('/admin/personas/$personaId/rebuild') as Map);
}

/// 대화 상대 화면에 기억 인터뷰 신청 단추를 보일까. 만든 분이고 '나 자신'이 아닐 때만.
bool canAskInterview(Map? persona) =>
    persona != null && !isMember(persona) && persona['relation'] != selfRelation;

/// 신청 단추 글(대화 화면 정보 시트·기억 더 채우기 화면이 같이 쓴다). 정해 둔 이 글 그대로 쓴다.
const interviewTitle = '기억 인터뷰 받기';
const interviewSubtitle = '대표가 직접 이야기를 들어 드려요(초기 가족 무료)';

/// 상태 → 화면 글.
const interviewStatusLabels = {
  'requested': '신청했어요',
  'scheduled': '일정을 잡았어요',
  'done': '인터뷰를 마쳤어요',
  'cancelled': '취소했어요',
};

/// 통화 방법 → 화면 글.
const interviewMethodLabels = {'phone': '전화', 'video': '영상 통화'};
