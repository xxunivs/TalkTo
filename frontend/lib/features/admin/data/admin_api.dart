import '../../../core/network/api_client.dart';

class AdminApi {
  Future<AdminMetrics> getMetrics() async {
    final response = await ApiClient.dio.get('/admin/metrics/overview');
    final data = response.data['data'];

    if (data == null) {
      throw Exception('admin metrics data가 없습니다.');
    }

    return AdminMetrics.fromJson(Map<String, dynamic>.from(data));
  }

  Future<List<NegativeFeedbackSummary>> getNegativeSummary() async {
    final response =
        await ApiClient.dio.get('/admin/feedback/negative-summary');
    final data = response.data['data'];

    if (data == null) {
      return [];
    }

    return (data as List)
        .map((e) => NegativeFeedbackSummary.fromJson(
              Map<String, dynamic>.from(e),
            ))
        .toList();
  }

  Future<List<FeedbackReview>> getFeedbackReviews() async {
    final response = await ApiClient.dio.get('/admin/feedback/reviews');
    final data = response.data['data'];

    if (data == null) {
      return [];
    }

    return (data as List)
        .map((e) => FeedbackReview.fromJson(
              Map<String, dynamic>.from(e),
            ))
        .toList();
  }

  Future<DailyMetricsResponse> getDailyMetrics() async {
    final response = await ApiClient.dio.get('/admin/metrics/daily');
    final data = response.data['data'];

    if (data == null) {
      throw Exception('daily metrics data가 없습니다.');
    }

    return DailyMetricsResponse.fromJson(
      Map<String, dynamic>.from(data),
    );
  }
}

class AdminMetrics {
  final int usersTotal;
  final int conversationsTotal;
  final int messagesTotal;
  final int voiceMessagesTotal;
  final double feedbackPositiveRatio;
  final double feedbackNegativeRatio;

  AdminMetrics({
    required this.usersTotal,
    required this.conversationsTotal,
    required this.messagesTotal,
    required this.voiceMessagesTotal,
    required this.feedbackPositiveRatio,
    required this.feedbackNegativeRatio,
  });

  factory AdminMetrics.fromJson(Map<String, dynamic> json) {
    return AdminMetrics(
      usersTotal: json['usersTotal'] ?? 0,
      conversationsTotal: json['conversationsTotal'] ?? 0,
      messagesTotal: json['messagesTotal'] ?? 0,
      voiceMessagesTotal: json['voiceMessagesTotal'] ?? 0,
      feedbackPositiveRatio:
          ((json['feedbackPositiveRatio'] ?? 0) as num).toDouble(),
      feedbackNegativeRatio:
          ((json['feedbackNegativeRatio'] ?? 0) as num).toDouble(),
    );
  }
}

class NegativeFeedbackSummary {
  final String tag;
  final int count;

  NegativeFeedbackSummary({
    required this.tag,
    required this.count,
  });

  factory NegativeFeedbackSummary.fromJson(Map<String, dynamic> json) {
    return NegativeFeedbackSummary(
      tag: json['tag'] ?? '',
      count: json['count'] ?? 0,
    );
  }
}

class FeedbackReview {
  final String time;
  final String sessionId;
  final String rating;
  final List<String> tags;
  final String comment;

  FeedbackReview({
    required this.time,
    required this.sessionId,
    required this.rating,
    required this.tags,
    required this.comment,
  });

  factory FeedbackReview.fromJson(Map<String, dynamic> json) {
    return FeedbackReview(
      time: json['time'] ?? json['createdAt'] ?? '',
      sessionId: json['sessionId'] ?? '',
      rating: json['rating'] ?? '',
      tags: List<String>.from(json['tags'] ?? []),
      comment: json['comment'] ?? '-',
    );
  }
}

class DailyMetricsResponse {
  final String timeZone;
  final int days;
  final List<DailyMetricItem> items;

  DailyMetricsResponse({
    required this.timeZone,
    required this.days,
    required this.items,
  });

  factory DailyMetricsResponse.fromJson(Map<String, dynamic> json) {
    return DailyMetricsResponse(
      timeZone: json['timeZone'] ?? 'Asia/Seoul',
      days: json['days'] ?? 14,
      items: (json['items'] as List<dynamic>? ?? [])
          .map(
            (e) => DailyMetricItem.fromJson(
              Map<String, dynamic>.from(e),
            ),
          )
          .toList(),
    );
  }
}

class DailyMetricItem {
  final String date;
  final int newUsers;
  final int conversationSessions;
  final int messages;
  final int voiceMessages;
  final int assistantMessages;
  final int feedbackPositive;
  final int feedbackNeutral;
  final int feedbackNegative;
  final int feedbackTotal;
  final double feedbackResponseRate;

  DailyMetricItem({
    required this.date,
    required this.newUsers,
    required this.conversationSessions,
    required this.messages,
    required this.voiceMessages,
    required this.assistantMessages,
    required this.feedbackPositive,
    required this.feedbackNeutral,
    required this.feedbackNegative,
    required this.feedbackTotal,
    required this.feedbackResponseRate,
  });

  factory DailyMetricItem.fromJson(Map<String, dynamic> json) {
    return DailyMetricItem(
      date: json['date'] ?? '',
      newUsers: json['newUsers'] ?? 0,
      conversationSessions: json['conversationSessions'] ?? 0,
      messages: json['messages'] ?? 0,
      voiceMessages: json['voiceMessages'] ?? 0,
      assistantMessages: json['assistantMessages'] ?? 0,
      feedbackPositive: json['feedbackPositive'] ?? 0,
      feedbackNeutral: json['feedbackNeutral'] ?? 0,
      feedbackNegative: json['feedbackNegative'] ?? 0,
      feedbackTotal: json['feedbackTotal'] ?? 0,
      feedbackResponseRate:
          ((json['feedbackResponseRate'] ?? 0) as num).toDouble(),
    );
  }
}
