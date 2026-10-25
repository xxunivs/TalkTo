import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../admin/data/admin_api.dart';
import 'feedback_admin_page.dart' show FeedbackAdminLink;
import 'interview_admin_page.dart' show InterviewAdminLink;
import 'legal_admin_links.dart';
import 'support_lookup.dart';

class AdminDashboardPage extends StatefulWidget {
  const AdminDashboardPage({super.key});

  @override
  State<AdminDashboardPage> createState() => _AdminDashboardPageState();
}

class _AdminDashboardPageState extends State<AdminDashboardPage> {
  final AdminApi _adminApi = AdminApi();
  late final Future<AdminMetrics> _metricsFuture;

  late final Future<List<NegativeFeedbackSummary>> _negativeSummaryFuture;
  late final Future<List<FeedbackReview>> _feedbackReviewsFuture;
  late final Future<DailyMetricsResponse> _dailyMetricsFuture;

  @override
  void initState() {
    super.initState();
    _metricsFuture = _adminApi.getMetrics();
    _negativeSummaryFuture = _adminApi.getNegativeSummary();
    _feedbackReviewsFuture = _adminApi.getFeedbackReviews();
    _dailyMetricsFuture = _adminApi.getDailyMetrics();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFFFAFAFA),
      body: Center(
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width < 600 ? 420 : 640,
          ),
          child: Column(
            children: [
              _AdminHeader(
                onBack: () => context.go('/'),
                onStats: () => context.go('/admin/stats'),
              ),
              Expanded(
                child: SingleChildScrollView(
                  padding: EdgeInsets.fromLTRB(
                    MediaQuery.of(context).size.width < 600 ? 16 : 28,
                    MediaQuery.of(context).size.width < 600 ? 16 : 24,
                    MediaQuery.of(context).size.width < 600 ? 16 : 28,
                    MediaQuery.of(context).size.width < 600 ? 28 : 48,
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      _ToolLinks(
                        onImport: () => context.go('/admin/import'),
                      ),
                      const SizedBox(height: 12),
                      // 기억 인터뷰 신청 목록(연락처·상태·들은 내용 넣기)
                      const InterviewAdminLink(),
                      const SizedBox(height: 12),
                      // 대화 평가(말풍선 ⋯ 고른 것·고친 말, 대화 한마디) 목록 · 확인함 표시
                      const FeedbackAdminLink(),
                      const SizedBox(height: 12),
                      // 고객센터 문의(카카오톡·이메일)의 문의 코드로 회원 찾기
                      const SupportLookupCard(),
                      const SizedBox(height: 12),
                      // 법률 자문 반영: 동의 녹음 검수·멈춘 대화 상대·대화가 어려우신 분·결제·환불·확인 문자
                      const LegalAdminLinks(),
                      const SizedBox(height: 32),
                      const _SectionTitle('주요 메트릭'),
                      const SizedBox(height: 20),
                      FutureBuilder<AdminMetrics>(
                        future: _metricsFuture,
                        builder: (context, snapshot) {
                          if (snapshot.connectionState ==
                              ConnectionState.waiting) {
                            return const _MetricLoadingBox();
                          }

                          if (snapshot.hasError) {
                            return _MetricErrorBox(
                              message: snapshot.error.toString(),
                            );
                          }

                          return _MetricGrid(
                            metrics: snapshot.data!,
                          );
                        },
                      ),
                      const SizedBox(height: 48),
                      const _SectionTitle('최근 14일 일별 통계'),
                      const SizedBox(height: 20),
                      FutureBuilder<DailyMetricsResponse>(
                        future: _dailyMetricsFuture,
                        builder: (context, snapshot) {
                          if (snapshot.connectionState ==
                              ConnectionState.waiting) {
                            return const _MetricLoadingBox();
                          }

                          if (snapshot.hasError) {
                            return _MetricErrorBox(
                                message: snapshot.error.toString());
                          }

                          return _DailyMetricsSection(data: snapshot.data!);
                        },
                      ),
                      const SizedBox(height: 48),
                      const _SectionTitle('부정 피드백 큐'),
                      const SizedBox(height: 20),
                      FutureBuilder<List<NegativeFeedbackSummary>>(
                        future: _negativeSummaryFuture,
                        builder: (context, snapshot) {
                          if (snapshot.connectionState ==
                              ConnectionState.waiting) {
                            return const _MetricLoadingBox();
                          }

                          if (snapshot.hasError) {
                            return _MetricErrorBox(
                                message: snapshot.error.toString());
                          }

                          return _NegativeFeedbackGrid(items: snapshot.data!);
                        },
                      ),
                      const SizedBox(height: 48),
                      const _SectionTitle('피드백 검토'),
                      const SizedBox(height: 20),
                      FutureBuilder<List<FeedbackReview>>(
                        future: _feedbackReviewsFuture,
                        builder: (context, snapshot) {
                          if (snapshot.connectionState ==
                              ConnectionState.waiting) {
                            return const _MetricLoadingBox();
                          }

                          if (snapshot.hasError) {
                            return _MetricErrorBox(
                                message: snapshot.error.toString());
                          }

                          return _ReviewList(reviews: snapshot.data!);
                        },
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _AdminHeader extends StatelessWidget {
  final VoidCallback onBack;
  final VoidCallback onStats;

  const _AdminHeader({
    required this.onBack,
    required this.onStats,
  });

  @override
  Widget build(BuildContext context) {
    final isMobile = MediaQuery.of(context).size.width < 600;

    return Container(
      height: isMobile ? 72 : 88,
      padding: EdgeInsets.symmetric(horizontal: isMobile ? 16 : 28),
      decoration: const BoxDecoration(
        color: Color(0xFFFFFBF0),
        border: Border(
          bottom: BorderSide(
            color: Color(0xFFFFD666),
          ),
        ),
      ),
      child: Row(
        children: [
          Expanded(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '개발자 통계/피드백 페이지',
                  style: TextStyle(
                    fontSize: isMobile ? 16 : 22,
                    fontWeight: FontWeight.w600,
                    color: const Color(0xFF222222),
                  ),
                ),
                SizedBox(height: isMobile ? 4 : 8),
                Text(
                  '⚠ 내부 개발자 확인용',
                  style: TextStyle(
                    fontSize: isMobile ? 11 : 15,
                    color: const Color(0xFFCC5A00),
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ],
            ),
          ),
          SizedBox(
            height: isMobile ? 42 : 56,
            child: FilledButton.icon(
              onPressed: onStats,
              icon: Icon(
                Icons.bar_chart_rounded,
                size: isMobile ? 16 : 20,
              ),
              label: Text(
                '사용 통계',
                style: TextStyle(
                  fontSize: isMobile ? 12 : 17,
                  fontWeight: FontWeight.w600,
                ),
              ),
              style: FilledButton.styleFrom(
                minimumSize: Size.zero,
                padding: EdgeInsets.symmetric(
                  horizontal: isMobile ? 12 : 24,
                ),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(
                    isMobile ? 12 : 16,
                  ),
                ),
              ),
            ),
          ),
          SizedBox(width: isMobile ? 6 : 10),
          SizedBox(
            height: isMobile ? 42 : 56,
            child: OutlinedButton.icon(
              onPressed: onBack,
              icon: Icon(
                Icons.arrow_back,
                size: isMobile ? 16 : 20,
              ),
              label: Text(
                '대화 화면',
                style: TextStyle(
                  fontSize: isMobile ? 12 : 17,
                  fontWeight: FontWeight.w600,
                ),
              ),
              style: OutlinedButton.styleFrom(
                foregroundColor: const Color(0xFF222222),
                side: const BorderSide(color: Color(0xFFD8D8D8)),
                backgroundColor: Colors.white,
                padding: EdgeInsets.symmetric(
                  horizontal: isMobile ? 12 : 24,
                ),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(
                    isMobile ? 12 : 16,
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// 관리 도구로 가는 링크(지금은 페르소나 가져오기 하나).
class _ToolLinks extends StatelessWidget {
  final VoidCallback onImport;

  const _ToolLinks({
    required this.onImport,
  });

  @override
  Widget build(BuildContext context) {
    final isMobile = MediaQuery.of(context).size.width < 600;

    return Material(
      color: Colors.white,
      shape: RoundedRectangleBorder(
        side: const BorderSide(color: Color(0xFFE1E1E1)),
        borderRadius: BorderRadius.circular(18),
      ),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onImport,
        child: Padding(
          padding: EdgeInsets.symmetric(
            horizontal: isMobile ? 14 : 18,
            vertical: isMobile ? 12 : 16,
          ),
          child: Row(
            children: [
              Icon(
                Icons.move_to_inbox_outlined,
                size: isMobile ? 20 : 24,
                color: const Color(0xFF222222),
              ),
              SizedBox(width: isMobile ? 10 : 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '페르소나 가져오기',
                      style: TextStyle(
                        fontSize: isMobile ? 14 : 17,
                        fontWeight: FontWeight.w700,
                        color: const Color(0xFF222222),
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      '예전 자료 묶음(.json)으로 고른 계정의 대화 상대를 만들어요',
                      style: TextStyle(
                        fontSize: isMobile ? 11 : 13,
                        color: const Color(0xFF777777),
                      ),
                    ),
                  ],
                ),
              ),
              const Icon(
                Icons.chevron_right_rounded,
                color: Color(0xFF999999),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SectionTitle extends StatelessWidget {
  final String title;

  const _SectionTitle(this.title);

  @override
  Widget build(BuildContext context) {
    final isMobile = MediaQuery.of(context).size.width < 600;

    return Text(
      title,
      style: TextStyle(
        fontSize: isMobile ? 18 : 24,
        fontWeight: FontWeight.w700,
        color: const Color(0xFF222222),
      ),
    );
  }
}

class _MetricGrid extends StatelessWidget {
  final AdminMetrics metrics;

  const _MetricGrid({
    required this.metrics,
  });

  String _toPercent(double value) {
    return '${(value * 100).round()}%';
  }

  @override
  Widget build(BuildContext context) {
    final metricItems = [
      (metrics.usersTotal.toString(), '총 사용자 수'),
      (metrics.conversationsTotal.toString(), '총 대화 세션'),
      (metrics.messagesTotal.toString(), '총 메시지'),
      (metrics.voiceMessagesTotal.toString(), '음성 메시지'),
      (_toPercent(metrics.feedbackPositiveRatio), '긍정 피드백 비율'),
      (_toPercent(metrics.feedbackNegativeRatio), '부정 피드백 비율'),
    ];

    return LayoutBuilder(
      builder: (context, constraints) {
        final isMobile = constraints.maxWidth < 480;

        return GridView.builder(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          itemCount: metricItems.length,
          gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 2,
            mainAxisSpacing: isMobile ? 10 : 16,
            crossAxisSpacing: isMobile ? 10 : 20,
            mainAxisExtent: isMobile ? 64 : 76,
          ),
          itemBuilder: (context, index) {
            final item = metricItems[index];

            return _MetricCard(
              value: item.$1,
              label: item.$2,
            );
          },
        );
      },
    );
  }
}

class _MetricCard extends StatelessWidget {
  final String value;
  final String label;

  const _MetricCard({
    required this.value,
    required this.label,
  });

  @override
  Widget build(BuildContext context) {
    final isMobile = MediaQuery.of(context).size.width < 600;

    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: isMobile ? 12 : 16,
        vertical: isMobile ? 8 : 10,
      ),
      decoration: BoxDecoration(
        color: Colors.white,
        border: Border.all(color: const Color(0xFFE1E1E1)),
        borderRadius: BorderRadius.circular(
          isMobile ? 14 : 18,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            value,
            style: TextStyle(
              fontSize: isMobile ? 16 : 22,
              fontWeight: FontWeight.w600,
              color: const Color(0xFF222222),
            ),
          ),
          SizedBox(height: isMobile ? 2 : 4),
          Text(
            label,
            style: TextStyle(
              fontSize: isMobile ? 9 : 10,
              color: const Color(0xFF777777),
            ),
          ),
        ],
      ),
    );
  }
}

class _MetricLoadingBox extends StatelessWidget {
  const _MetricLoadingBox();

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 120,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: Colors.white,
        border: Border.all(color: const Color(0xFFE1E1E1)),
        borderRadius: BorderRadius.circular(18),
      ),
      child: const Text(
        '메트릭을 불러오는 중입니다...',
        style: TextStyle(
          fontSize: 13,
          color: Color(0xFF777777),
        ),
      ),
    );
  }
}

class _MetricErrorBox extends StatelessWidget {
  final String message;

  const _MetricErrorBox({
    required this.message,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF2F2),
        border: Border.all(color: const Color(0xFFFFB8B8)),
        borderRadius: BorderRadius.circular(18),
      ),
      child: Text(
        '메트릭을 불러오지 못했습니다.\n$message',
        style: const TextStyle(
          fontSize: 13,
          height: 1.5,
          color: Color(0xFFD40000),
        ),
      ),
    );
  }
}

class _NegativeFeedbackGrid extends StatelessWidget {
  final List<NegativeFeedbackSummary> items;

  const _NegativeFeedbackGrid({
    required this.items,
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final isMobile = constraints.maxWidth < 480;

        return GridView.builder(
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          itemCount: items.length,
          gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: 2,
            mainAxisSpacing: isMobile ? 10 : 16,
            crossAxisSpacing: isMobile ? 10 : 20,
            mainAxisExtent: isMobile ? 64 : 76,
          ),
          itemBuilder: (context, index) {
            final item = items[index];
            return _NegativeFeedbackCard(
              count: '${item.count}건',
              label: item.tag,
            );
          },
        );
      },
    );
  }
}

class _NegativeFeedbackCard extends StatelessWidget {
  final String count;
  final String label;

  const _NegativeFeedbackCard({
    required this.count,
    required this.label,
  });

  @override
  Widget build(BuildContext context) {
    final isMobile = MediaQuery.of(context).size.width < 600;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF2F2),
        border: Border.all(color: const Color(0xFFFFB8B8)),
        borderRadius: BorderRadius.circular(18),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Text(
            count,
            style: TextStyle(
              fontSize: isMobile ? 14 : 18,
              fontWeight: FontWeight.w600,
              color: const Color(0xFFD40000),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            label,
            style: TextStyle(
              fontSize: isMobile ? 9 : 10,
              color: const Color(0xFFD40000),
            ),
          ),
        ],
      ),
    );
  }
}

class _ReviewList extends StatelessWidget {
  final List<FeedbackReview> reviews;

  const _ReviewList({
    required this.reviews,
  });

  @override
  Widget build(BuildContext context) {
    final screenWidth = MediaQuery.of(context).size.width;
    final isMobile = screenWidth < 600;

    if (reviews.isEmpty) {
      return Container(
        width: double.infinity,
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
          color: Colors.white,
          border: Border.all(color: const Color(0xFFE1E1E1)),
          borderRadius: BorderRadius.circular(18),
        ),
        child: const Text(
          '아직 등록된 피드백이 없습니다.',
          style: TextStyle(
            fontSize: 13,
            color: Color(0xFF777777),
          ),
        ),
      );
    }

    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: SizedBox(
        width: isMobile ? 720 : 640,
        child: Container(
          decoration: BoxDecoration(
            color: Colors.white,
            border: Border.all(color: const Color(0xFFE1E1E1)),
            borderRadius: BorderRadius.circular(18),
          ),
          clipBehavior: Clip.hardEdge,
          child: Table(
            columnWidths: const {
              0: FixedColumnWidth(88),
              1: FixedColumnWidth(105),
              2: FixedColumnWidth(70),
              3: FixedColumnWidth(120),
              4: FlexColumnWidth(),
            },
            defaultVerticalAlignment: TableCellVerticalAlignment.middle,
            children: [
              const TableRow(
                decoration: BoxDecoration(color: Color(0xFFFAFAFA)),
                children: [
                  _TableHeaderCell('일시'),
                  _TableHeaderCell('Session ID'),
                  _TableHeaderCell('평가'),
                  _TableHeaderCell('태그'),
                  _TableHeaderCell('코멘트'),
                ],
              ),
              ...reviews.map(
                (review) => TableRow(
                  decoration: const BoxDecoration(
                    border: Border(
                      top: BorderSide(color: Color(0xFFEFEFEF)),
                    ),
                  ),
                  children: [
                    _TableBodyCell(review.time),
                    _TableBodyCell(review.sessionId),
                    _RatingCell(review.rating),
                    _TagCell(review.tags),
                    _CommentCell(review.comment),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _CommentCell extends StatelessWidget {
  final String text;

  const _CommentCell(this.text);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: 10,
        vertical: 14,
      ),
      child: Text(
        text,
        softWrap: true,
        maxLines: null,
        overflow: TextOverflow.visible,
        style: const TextStyle(
          fontSize: 12,
          height: 1.45,
          color: Color(0xFF555555),
        ),
      ),
    );
  }
}

class _TableHeaderCell extends StatelessWidget {
  final String text;

  const _TableHeaderCell(this.text);

  @override
  Widget build(BuildContext context) {
    final isMobile = MediaQuery.of(context).size.width < 600;

    return Padding(
      padding: EdgeInsets.symmetric(
        horizontal: isMobile ? 8 : 14,
        vertical: isMobile ? 12 : 16,
      ),
      child: Text(
        text,
        style: TextStyle(
          fontSize: isMobile ? 11 : 13,
          fontWeight: FontWeight.w700,
          color: const Color(0xFF555555),
        ),
      ),
    );
  }
}

class _TableBodyCell extends StatelessWidget {
  final String text;

  const _TableBodyCell(this.text);

  @override
  Widget build(BuildContext context) {
    final isMobile = MediaQuery.of(context).size.width < 600;

    return Padding(
      padding: EdgeInsets.symmetric(
        horizontal: isMobile ? 8 : 14,
        vertical: isMobile ? 12 : 18,
      ),
      child: SizedBox(
        width: double.infinity,
        child: Text(
          text,
          softWrap: true,
          maxLines: null,
          overflow: TextOverflow.visible,
          style: TextStyle(
            fontSize: isMobile ? 11 : 13,
            height: 1.5,
            color: const Color(0xFF555555),
          ),
        ),
      ),
    );
  }
}

class _RatingCell extends StatelessWidget {
  final String rating;

  const _RatingCell(this.rating);

  @override
  Widget build(BuildContext context) {
    // 서버는 UP/DOWN 을 보낸다(예전 줄은 👍/👎)
    final isPositive = rating == 'UP' || rating == '👍';
    final label = switch (rating) { 'UP' => '좋아요', 'DOWN' => '아쉬워요', _ => rating };

    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: 14,
        vertical: 18,
      ),
      child: Align(
        alignment: Alignment.centerLeft,
        child: Container(
          padding: const EdgeInsets.symmetric(
            horizontal: 10,
            vertical: 6,
          ),
          decoration: BoxDecoration(
            color:
                isPositive ? const Color(0xFFE8FFF0) : const Color(0xFFFFE8E8),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Text(
            label,
            style: const TextStyle(fontSize: 14),
          ),
        ),
      ),
    );
  }
}

class _TagCell extends StatelessWidget {
  final List<String> tags;

  const _TagCell(this.tags);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: 14,
        vertical: 12,
      ),
      child: Wrap(
        spacing: 6,
        runSpacing: 6,
        children: tags.map((tag) {
          return Container(
            padding: const EdgeInsets.symmetric(
              horizontal: 8,
              vertical: 5,
            ),
            decoration: BoxDecoration(
              color: const Color(0xFFF4F4F4),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text(
              tag,
              style: const TextStyle(
                fontSize: 11,
                color: Color(0xFF444444),
              ),
            ),
          );
        }).toList(),
      ),
    );
  }
}

class _DailyMetricsSection extends StatelessWidget {
  final DailyMetricsResponse data;

  const _DailyMetricsSection({
    required this.data,
  });

  @override
  Widget build(BuildContext context) {
    final items = data.items;
    final isMobile = MediaQuery.of(context).size.width < 600;

    if (items.isEmpty) {
      return const _MetricErrorBox(message: '일별 통계 데이터가 없습니다.');
    }

    return Column(
      children: [
        _DailyBarChartCard(
          title: '사용자 / 대화 세션',
          items: items,
          firstLabel: '신규 사용자',
          secondLabel: '대화 세션',
          firstValue: (item) => item.newUsers,
          secondValue: (item) => item.conversationSessions,
        ),
        SizedBox(height: isMobile ? 14 : 20),
        _DailyBarChartCard(
          title: '메시지 / 음성 메시지',
          items: items,
          firstLabel: '전체 메시지',
          secondLabel: '음성 메시지',
          firstValue: (item) => item.messages,
          secondValue: (item) => item.voiceMessages,
        ),
        SizedBox(height: isMobile ? 14 : 20),
        _FeedbackBarChartCard(items: items),
      ],
    );
  }
}

class _DailyBarChartCard extends StatelessWidget {
  final String title;
  final List<DailyMetricItem> items;
  final String firstLabel;
  final String secondLabel;
  final int Function(DailyMetricItem item) firstValue;
  final int Function(DailyMetricItem item) secondValue;

  const _DailyBarChartCard({
    required this.title,
    required this.items,
    required this.firstLabel,
    required this.secondLabel,
    required this.firstValue,
    required this.secondValue,
  });

  @override
  Widget build(BuildContext context) {
    final isMobile = MediaQuery.of(context).size.width < 600;
    final maxY = items
        .map((e) => [firstValue(e), secondValue(e)])
        .expand((e) => e)
        .fold<int>(0, (prev, value) => value > prev ? value : prev)
        .toDouble();

    return Container(
      padding: EdgeInsets.all(isMobile ? 14 : 18),
      decoration: BoxDecoration(
        color: Colors.white,
        border: Border.all(color: const Color(0xFFE1E1E1)),
        borderRadius: BorderRadius.circular(18),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _ChartHeader(
            title: title,
            labels: [firstLabel, secondLabel],
          ),
          SizedBox(height: isMobile ? 16 : 22),
          SizedBox(
            height: isMobile ? 180 : 220,
            child: BarChart(
              BarChartData(
                maxY: maxY == 0 ? 5 : maxY + 2,
                barGroups: List.generate(items.length, (index) {
                  final item = items[index];

                  return BarChartGroupData(
                    x: index,
                    barsSpace: 3,
                    barRods: [
                      BarChartRodData(
                        toY: firstValue(item).toDouble(),
                        width: isMobile ? 5 : 7,
                        borderRadius: BorderRadius.circular(3),
                      ),
                      BarChartRodData(
                        toY: secondValue(item).toDouble(),
                        width: isMobile ? 5 : 7,
                        borderRadius: BorderRadius.circular(3),
                      ),
                    ],
                  );
                }),
                titlesData: FlTitlesData(
                  topTitles: const AxisTitles(
                    sideTitles: SideTitles(showTitles: false),
                  ),
                  rightTitles: const AxisTitles(
                    sideTitles: SideTitles(showTitles: false),
                  ),
                  leftTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      reservedSize: 28,
                      getTitlesWidget: (value, meta) {
                        return Text(
                          value.toInt().toString(),
                          style: TextStyle(
                            fontSize: isMobile ? 9 : 10,
                            color: const Color(0xFF888888),
                          ),
                        );
                      },
                    ),
                  ),
                  bottomTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      reservedSize: 28,
                      getTitlesWidget: (value, meta) {
                        final index = value.toInt();
                        if (index < 0 || index >= items.length) {
                          return const SizedBox.shrink();
                        }

                        final date = items[index].date;
                        final label = date.length >= 10
                            ? date.substring(5).replaceAll('-', '/')
                            : date;

                        return Padding(
                          padding: const EdgeInsets.only(top: 8),
                          child: Text(
                            label,
                            style: TextStyle(
                              fontSize: isMobile ? 8 : 9,
                              color: const Color(0xFF777777),
                            ),
                          ),
                        );
                      },
                    ),
                  ),
                ),
                gridData: const FlGridData(show: true),
                borderData: FlBorderData(show: false),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _FeedbackBarChartCard extends StatelessWidget {
  final List<DailyMetricItem> items;

  const _FeedbackBarChartCard({
    required this.items,
  });

  @override
  Widget build(BuildContext context) {
    final isMobile = MediaQuery.of(context).size.width < 600;

    final maxY = items
        .map((e) => e.feedbackTotal)
        .fold<int>(0, (prev, value) => value > prev ? value : prev)
        .toDouble();

    return Container(
      padding: EdgeInsets.all(isMobile ? 14 : 18),
      decoration: BoxDecoration(
        color: Colors.white,
        border: Border.all(color: const Color(0xFFE1E1E1)),
        borderRadius: BorderRadius.circular(18),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const _ChartHeader(
            title: '피드백 반응',
            labels: ['긍정', '중립', '부정'],
          ),
          SizedBox(height: isMobile ? 16 : 22),
          SizedBox(
            height: isMobile ? 180 : 220,
            child: BarChart(
              BarChartData(
                maxY: maxY == 0 ? 5 : maxY + 2,
                barGroups: List.generate(items.length, (index) {
                  final item = items[index];

                  return BarChartGroupData(
                    x: index,
                    barsSpace: 2,
                    barRods: [
                      BarChartRodData(
                        toY: item.feedbackPositive.toDouble(),
                        width: isMobile ? 4 : 6,
                        borderRadius: BorderRadius.circular(3),
                      ),
                      BarChartRodData(
                        toY: item.feedbackNeutral.toDouble(),
                        width: isMobile ? 4 : 6,
                        borderRadius: BorderRadius.circular(3),
                      ),
                      BarChartRodData(
                        toY: item.feedbackNegative.toDouble(),
                        width: isMobile ? 4 : 6,
                        borderRadius: BorderRadius.circular(3),
                      ),
                    ],
                  );
                }),
                titlesData: FlTitlesData(
                  topTitles: const AxisTitles(
                    sideTitles: SideTitles(showTitles: false),
                  ),
                  rightTitles: const AxisTitles(
                    sideTitles: SideTitles(showTitles: false),
                  ),
                  leftTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      reservedSize: 28,
                      getTitlesWidget: (value, meta) {
                        return Text(
                          value.toInt().toString(),
                          style: TextStyle(
                            fontSize: isMobile ? 9 : 10,
                            color: const Color(0xFF888888),
                          ),
                        );
                      },
                    ),
                  ),
                  bottomTitles: AxisTitles(
                    sideTitles: SideTitles(
                      showTitles: true,
                      reservedSize: 28,
                      getTitlesWidget: (value, meta) {
                        final index = value.toInt();
                        if (index < 0 || index >= items.length) {
                          return const SizedBox.shrink();
                        }

                        final date = items[index].date;
                        final label = date.length >= 10
                            ? date.substring(5).replaceAll('-', '/')
                            : date;

                        return Padding(
                          padding: const EdgeInsets.only(top: 8),
                          child: Text(
                            label,
                            style: TextStyle(
                              fontSize: isMobile ? 8 : 9,
                              color: const Color(0xFF777777),
                            ),
                          ),
                        );
                      },
                    ),
                  ),
                ),
                gridData: const FlGridData(show: true),
                borderData: FlBorderData(show: false),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _ChartHeader extends StatelessWidget {
  final String title;
  final List<String> labels;

  const _ChartHeader({
    required this.title,
    required this.labels,
  });

  @override
  Widget build(BuildContext context) {
    final isMobile = MediaQuery.of(context).size.width < 600;

    return Row(
      children: [
        Expanded(
          child: Text(
            title,
            style: TextStyle(
              fontSize: isMobile ? 14 : 17,
              fontWeight: FontWeight.w700,
              color: const Color(0xFF222222),
            ),
          ),
        ),
        Wrap(
          spacing: isMobile ? 6 : 10,
          children: labels.map((label) {
            return Text(
              label,
              style: TextStyle(
                fontSize: isMobile ? 9 : 11,
                color: const Color(0xFF777777),
              ),
            );
          }).toList(),
        ),
      ],
    );
  }
}
