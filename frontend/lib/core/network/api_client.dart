import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

class ApiConfig {
  /// 로컬은 기본값 그대로. 운영은 웹과 API 가 같은 주소라 `--dart-define=API_BASE_URL=/api/v1`(상대 경로)로
  /// 빌드한다. 그러면 어느 주소(app.talkto.co.kr·cloudfront.net)로 열어도 그 주소의 API 를 부른다.
  static const String baseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000/api/v1',
  );
}

/// 화면에 그대로 보여줄 수 있는 오류.
class ApiException implements Exception {
  final int? statusCode;
  final String message;

  /// 서버가 함께 준 오류 이름(share_paused·invite_used 등). 없으면 null.
  final String? code;

  /// 오류 몸통의 나머지 값(reason·need·cap·field 등). detail·code 는 빠진다.
  final Map<String, dynamic> extra;

  const ApiException(this.message, {this.statusCode, this.code, this.extra = const {}});

  @override
  String toString() => message;

  /// 잠깐의 실패(연결이 끊김·서버를 바꾸는 중 5xx·요청이 몰림 429)인가. 조금 뒤 다시 하면 될 수 있다.
  /// ApiException 이 아닌 오류(알 수 없는 실패)도 그렇게 본다.
  static bool isTransient(Object error) {
    if (error is! ApiException) return true;
    final status = error.statusCode;
    return status == null || status >= 500 || status == 429;
  }
}

class ApiClient {
  static String? _refreshToken;

  /// 다시 로그인해야 할 때 (refresh 토큰도 만료) 부른다.
  static VoidCallback? onUnauthorized;

  /// 토큰이 새로 발급될 때 부른다. 세션이 저장해 둔다.
  static void Function(String access, String refresh)? onTokens;

  /// 서버가 '서비스 이용 동의가 필요해요'(403, code: consent_required)라고 할 때 부른다.
  /// 세션이 /auth/me 를 다시 읽어 needsConsent 를 켜고, 라우터가 동의 화면으로 보낸다.
  static VoidCallback? onConsentRequired;

  /// 403 응답 몸통이 동의가 빠져서 막힌 것인지.
  static bool isConsentRequired(Object? body) => body is Map && body['code'] == 'consent_required';

  static final BaseOptions _options = BaseOptions(
    baseUrl: ApiConfig.baseUrl,
    connectTimeout: const Duration(seconds: 15),
    receiveTimeout: const Duration(seconds: 180),
    headers: {'Content-Type': 'application/json'},
  );

  /// 토큰을 새로 받은 뒤 한 번 다시 보낼 때 쓴다. 가로채기(interceptor)가 없어서, 다시 보낸 요청이
  /// 실패해도 아래 대기열로 돌아가 서로를 기다리며 멈추지 않는다.
  static final Dio _retryDio = Dio(_options);
  static final Dio _refreshDio = Dio(BaseOptions(baseUrl: ApiConfig.baseUrl));

  /// 테스트에서 서버 대신 가짜 응답을 넣는다.
  @visibleForTesting
  static void useAdapterForTests(HttpClientAdapter adapter) {
    dio.httpClientAdapter = adapter;
    _retryDio.httpClientAdapter = adapter;
    _refreshDio.httpClientAdapter = adapter;
  }

  /// 401 이어도 토큰을 새로 받지 않는 요청(로그인·가입·토큰 갱신·소셜 로그인).
  /// /auth/me 처럼 로그인한 뒤 쓰는 요청은 만료되면 새로 받아 다시 보낸다.
  static const _noRefreshPaths = ['/auth/login', '/auth/signup', '/auth/refresh', '/auth/oauth'];

  static final Dio dio = Dio(_options)
    ..interceptors.add(
      QueuedInterceptorsWrapper(
        onError: (error, handler) async {
          final request = error.requestOptions;
          final retried = request.extra['retried'] == true;
          final noRefresh = _noRefreshPaths.any(request.path.startsWith);
          if (error.response?.statusCode == 401 && !retried && !noRefresh) {
            // access 토큰은 30분이라, 만료되면 refresh 토큰으로 한 번 다시 받아 재시도한다
            final refreshed = await _refresh();
            if (refreshed == true) {
              request.extra['retried'] = true;
              request.headers['Authorization'] = dio.options.headers['Authorization'];
              // 파일 묶음(FormData)은 한 번 보내면 다시 못 쓴다. 복사해서 보낸다.
              final body = request.data;
              if (body is FormData) request.data = body.clone();
              try {
                return handler.resolve(await _retryDio.fetch(request));
              } on DioException catch (retryError) {
                if (retryError.response?.statusCode == 403 &&
                    isConsentRequired(retryError.response?.data)) {
                  onConsentRequired?.call();
                }
                return handler.next(retryError);
              }
            }
            // refresh 토큰이 정말 만료됐을 때만 로그아웃한다. 연결이 잠깐 끊기거나 서버가 바쁜 것(5xx·429)은
            // 로그인을 지우지 않는다.
            if (refreshed == false) onUnauthorized?.call();
            if (refreshed == null) {
              // 처음 받은 401 을 그대로 넘기면 부른 쪽이 '로그인이 풀렸다'(401)로 알고 저장한 로그인을
              // 지운다(앱을 열 때 Session.init). 연결 문제로 바꿔 넘겨 로그인은 그대로 두고 '잠시 뒤 다시'로 보인다.
              return handler.next(DioException(
                requestOptions: request,
                type: DioExceptionType.connectionError,
                error: error.error,
                message: 'token refresh failed for a moment',
              ));
            }
          }
          if (error.response?.statusCode == 403 && isConsentRequired(error.response?.data)) {
            onConsentRequired?.call();
          }
          handler.next(error);
        },
      ),
    );

  static void setTokens(String access, String? refresh) {
    dio.options.headers['Authorization'] = 'Bearer $access';
    _refreshToken = refresh;
  }

  static void clearToken() {
    dio.options.headers.remove('Authorization');
    _refreshToken = null;
  }

  /// 새 토큰을 받는다. 됐으면 true, refresh 토큰이 만료·무효면 false(다시 로그인),
  /// 연결 문제·서버 오류처럼 잠깐의 실패면 null(로그인은 그대로 둔다).
  static Future<bool?> _refresh() async {
    final token = _refreshToken;
    if (token == null) return false;
    try {
      final response = await _refreshDio.post('/auth/refresh', data: {'refreshToken': token});
      final data = response.data['data'];
      setTokens(data['bearerToken'], data['refreshToken']);
      onTokens?.call(data['bearerToken'], data['refreshToken']);
      return true;
    } on DioException catch (error) {
      final status = error.response?.statusCode;
      return status == 401 || status == 403 ? false : null;
    } catch (_) {
      return null;
    }
  }

  /// 응답의 `data` 를 꺼내고, 실패는 [ApiException] 으로 바꾼다.
  static Future<dynamic> call(
    String method,
    String path, {
    Object? data,
    Map<String, dynamic>? query,
    void Function(int sent, int total)? onSendProgress,
  }) async {
    try {
      final response = await dio.request(
        path,
        data: data,
        queryParameters: query,
        // 파일 올리기는 오래 걸릴 수 있다. 웹은 보내는 시간까지 이 한도에 들어가서(연결 15초 + 받기),
        // 기본 180초면 느린 회선에서 큰 녹음이 늘 실패한다.
        options: Options(
          method: method,
          receiveTimeout: data is FormData ? const Duration(hours: 1) : null,
        ),
        onSendProgress: onSendProgress,
      );
      final body = response.data;
      return body is Map ? body['data'] : body;
    } on DioException catch (error) {
      throw toApiException(error);
    }
  }

  /// 파일(PDF 등)을 그대로 받는다. 실패하면 서버가 보낸 JSON 오류를 읽어 [ApiException] 으로 바꾼다.
  static Future<List<int>> bytes(String method, String path, {Object? data}) async {
    try {
      final response = await dio.request<List<int>>(
        path,
        data: data,
        options: Options(method: method, responseType: ResponseType.bytes),
      );
      return response.data ?? const [];
    } on DioException catch (error) {
      final raw = error.response?.data;
      if (raw is List<int>) {
        try {
          error.response!.data = jsonDecode(utf8.decode(raw));
        } catch (_) {
          // JSON 이 아니면 그대로 둔다
        }
      }
      throw toApiException(error);
    }
  }

  static Future<dynamic> get(String path, {Map<String, dynamic>? query}) =>
      call('GET', path, query: query);
  static Future<dynamic> post(String path, [Object? data]) => call('POST', path, data: data);
  static Future<dynamic> put(String path, [Object? data]) => call('PUT', path, data: data);
  static Future<dynamic> patch(String path, [Object? data]) => call('PATCH', path, data: data);
  static Future<dynamic> delete(String path) => call('DELETE', path);

  static final _hangul = RegExp(r'[가-힣]');

  /// 서버가 준 문장이 한국어 안내면 그대로 쓰고, 영어 원문(Not found 등)이면 쓰지 않는다.
  static String? _readable(Object? text) {
    if (text is! String) return null;
    final trimmed = text.trim();
    return trimmed.isNotEmpty && _hangul.hasMatch(trimmed) ? trimmed : null;
  }

  static ApiException toApiException(DioException error) {
    final status = error.response?.statusCode;
    final body = error.response?.data;
    if (error.type == DioExceptionType.connectionError ||
        error.type == DioExceptionType.connectionTimeout) {
      return const ApiException('연결이 잘 안 돼요. 인터넷이 연결되어 있는지 확인하고 잠시 뒤 다시 해 주세요.');
    }
    if (error.type == DioExceptionType.receiveTimeout) {
      return const ApiException('응답이 너무 오래 걸려요. 잠시 뒤 다시 해 주세요.');
    }
    String? message;
    String? code;
    var extra = const <String, dynamic>{};
    if (body is Map) {
      // 가족 공유 오류처럼 서버가 이름(code)과 덧붙인 값(reason·need·cap…)을 함께 주면 화면이 갈래를 고른다
      if (body['code'] is String) code = body['code'] as String;
      extra = {
        for (final e in body.entries)
          if (e.key is String && e.key != 'detail' && e.key != 'code') e.key as String: e.value,
      };
      final detail = body['detail'];
      if (detail is String) {
        message = _readable(detail);
      } else if (detail is List) {
        message = '적은 내용을 다시 확인해 주세요.';
      } else if (body['error'] is Map) {
        message = _readable(body['error']['message']);
      }
    }
    // 상태 번호와 서버 원문은 고객에게 보이지 않고, 개발할 때만 로그로 남긴다
    if (message == null && kDebugMode) debugPrint('API 오류 $status ${error.requestOptions.path}: $body');
    message ??= switch (status) {
      401 => '로그인이 풀렸어요. 다시 로그인해 주세요.',
      403 => '이 화면은 열 수 없어요.',
      404 => '찾지 못했어요. 화면을 새로고침해 주세요.',
      413 => '파일이 너무 커요. 더 작은 파일로 올려 주세요.',
      _ => '잠시 문제가 생겼어요. 잠시 뒤 다시 해 주세요.',
    };
    return ApiException(message, statusCode: status, code: code, extra: extra);
  }
}
