import '../../core/network/api_client.dart';

/// 페르소나 가져오기(/admin/import) 서버 호출. 관리자 계정만 된다.
/// 시험에서는 이 클래스를 이어받은 가짜를 넣는다.
class PersonaImportApi {
  const PersonaImportApi();

  /// GET /admin/users?q= — 가져올 계정 후보. 이름·이메일·이은 소셜 이메일로 찾는다.
  /// 한 줄: id·name·role·status·createdAt·provider·providers·emailMasked·personaCount·
  /// importedPersonas[{id, name, createdAt}](되돌리기 id 찾기). 전체 이메일은 싣지 않는다.
  /// q 가 비어 있으면 최근 가입 순.
  Future<List<Map<String, dynamic>>> searchUsers(String q) async {
    final query = q.trim();
    final data = await ApiClient.get('/admin/users', query: {if (query.isNotEmpty) 'q': query, 'limit': 50});
    if (data is! List) return const [];
    return [for (final row in data) if (row is Map) Map<String, dynamic>.from(row)];
  }

  /// POST /admin/personas/import — dryRun 이 true 면 쓰지 않고 미리보기(건수·검사)만 돌려준다.
  Future<Map<String, dynamic>> importPersona(Map<String, dynamic> body) async {
    final data = await ApiClient.post('/admin/personas/import', body);
    return data is Map ? Map<String, dynamic>.from(data) : const {};
  }

  /// DELETE /admin/personas/{id}/import — 가져온 대화 상대(origin=import)를 기억·대화와 함께 지운다.
  Future<void> removeImport(String personaId) =>
      ApiClient.delete('/admin/personas/${Uri.encodeComponent(personaId)}/import');
}
