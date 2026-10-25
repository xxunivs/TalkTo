import 'package:dio/dio.dart';

import '../../core/network/api_client.dart';

/// 개발자 화면(사용 통계) 서버 호출. 관리자 계정만 된다.
class StatsApi {
  StatsApi._();

  /// GET /admin/stats?days= — 숫자 카드·퍼널·일별·분포·표에 쓰는 통계 한 묶음.
  static Future<Map<String, dynamic>> load(int days) async {
    try {
      final response = await ApiClient.dio.get('/admin/stats', queryParameters: {'days': days});
      final body = response.data;
      // 서버는 보통 {data: …} 로 감싸 준다. 감싸지 않고 바로 줘도 읽는다.
      final data = body is Map && body['data'] is Map ? body['data'] : body;
      if (data is! Map) throw const ApiException('통계를 읽지 못했어요. 잠시 뒤 다시 해 주세요.');
      return Map<String, dynamic>.from(data);
    } on DioException catch (error) {
      throw ApiClient.toApiException(error);
    }
  }

  /// GET /admin/stats/export?kind=requests|events&days= — CSV(UTF-8 BOM) 파일 그대로.
  static Future<List<int>> export(String kind, int days) =>
      ApiClient.bytes('GET', '/admin/stats/export?kind=$kind&days=$days');
}
