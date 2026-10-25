import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_client.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import '../chat/chat_feedback.dart' show feedbackChoices;

/// 관리자 화면 · 대화 평가(/admin/feedback, 관리자만).
///
///   말풍선 평가   GET /admin/feedback?tag=&reviewed=&corrected=&page=&limit= · PATCH /admin/feedback/{id}
///   대화 한마디   GET /admin/conversation-feedback?reviewed=&page=&limit= · PATCH /admin/conversation-feedback/{id}
///
/// 대화 상대는 성함 대신 호칭으로만 보인다(서버가 personaTitle 로 준다). 고친 한 줄(correction)과 반영 상태도 본다.
class FeedbackAdminApi {
  const FeedbackAdminApi();

  /// 한 쪽 읽기: 줄과 거른 뒤 전체 수(meta.total).
  Future<({List<Map<String, dynamic>> rows, int total})> _page(String path, Map<String, dynamic> query) async {
    try {
      final response = await ApiClient.dio.get(path, queryParameters: query);
      final body = response.data;
      final data = body is Map ? body['data'] : null;
      final meta = body is Map ? body['meta'] : null;
      final rows = [
        for (final row in data is List ? data : const [])
          if (row is Map) Map<String, dynamic>.from(row),
      ];
      final total = meta is Map ? (meta['total'] as num?)?.toInt() : null;
      return (rows: rows, total: total ?? rows.length);
    } on DioException catch (error) {
      throw ApiClient.toApiException(error);
    }
  }

  /// 말풍선 평가 목록(최근 것부터). [tag]: LIKE·STYLE·CONTENT·TOO_LONG·VOICE. [corrected]: 고친 한 줄이 있는 것만.
  Future<({List<Map<String, dynamic>> rows, int total})> feedback({
    String? tag,
    bool? reviewed,
    bool? corrected,
    int page = 1,
    int limit = 50,
  }) =>
      _page('/admin/feedback', {
        if (tag != null) 'tag': tag,
        if (reviewed != null) 'reviewed': reviewed,
        if (corrected != null) 'corrected': corrected,
        'page': page,
        'limit': limit,
      });

  /// 확인함 켜고 끄기.
  Future<void> reviewFeedback(String id, bool reviewed) =>
      ApiClient.patch('/admin/feedback/$id', {'reviewed': reviewed});

  /// 대화 한마디 목록(최근 것부터).
  Future<({List<Map<String, dynamic>> rows, int total})> comments({bool? reviewed, int page = 1, int limit = 50}) =>
      _page('/admin/conversation-feedback', {
        if (reviewed != null) 'reviewed': reviewed,
        'page': page,
        'limit': limit,
      });

  Future<void> reviewComment(String id, bool reviewed) =>
      ApiClient.patch('/admin/conversation-feedback/$id', {'reviewed': reviewed});
}

/// 반영 상태 → 화면 이름.
const feedbackStateLabels = {
  'applied': '반영함',
  'pending_owner': '만든 분 확인 기다림',
  'rejected': '만든 분이 반영하지 않음',
};

String _date(Object? value) {
  final at = DateTime.tryParse('${value ?? ''}')?.toLocal();
  if (at == null) return '${value ?? ''}';
  String two(int n) => n.toString().padLeft(2, '0');
  return '${at.year}-${two(at.month)}-${two(at.day)} ${two(at.hour)}:${two(at.minute)}';
}

class FeedbackAdminPage extends StatefulWidget {
  final FeedbackAdminApi api;

  const FeedbackAdminPage({super.key, this.api = const FeedbackAdminApi()});

  @override
  State<FeedbackAdminPage> createState() => _FeedbackAdminPageState();
}

class _FeedbackAdminPageState extends State<FeedbackAdminPage> {
  static const _limit = 50;

  /// 'bubble'(말풍선 평가) | 'comment'(대화 한마디)
  String _tab = 'bubble';
  String? _tag;

  /// null = 전체, false = 확인 전, true = 확인함
  bool? _reviewed = false;
  bool _correctedOnly = false;

  List<Map<String, dynamic>>? _rows;
  int _total = 0;
  int _page = 1;
  bool _loadingMore = false;
  Object? _error;
  int _request = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<({List<Map<String, dynamic>> rows, int total})> _fetch(int page) => _tab == 'bubble'
      ? widget.api.feedback(
          tag: _tag,
          reviewed: _reviewed,
          corrected: _correctedOnly ? true : null,
          page: page,
          limit: _limit,
        )
      : widget.api.comments(reviewed: _reviewed, page: page, limit: _limit);

  Future<void> _load() async {
    final request = ++_request;
    setState(() {
      _rows = null;
      _error = null;
      _page = 1;
    });
    try {
      final result = await _fetch(1);
      if (!mounted || request != _request) return;
      setState(() {
        _rows = result.rows;
        _total = result.total;
      });
    } catch (error) {
      if (mounted && request == _request) setState(() => _error = error);
    }
  }

  Future<void> _more() async {
    if (_loadingMore) return;
    final request = _request;
    setState(() => _loadingMore = true);
    try {
      final result = await _fetch(_page + 1);
      if (!mounted || request != _request) return;
      setState(() {
        _page++;
        _rows = [...?_rows, ...result.rows];
        _total = result.total;
      });
    } catch (error) {
      if (mounted) showMessage(context, error);
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  Future<void> _toggleReviewed(Map<String, dynamic> row, bool reviewed) async {
    final before = row['reviewed'];
    setState(() => row['reviewed'] = reviewed);
    try {
      final id = '${row['id']}';
      if (_tab == 'bubble') {
        await widget.api.reviewFeedback(id, reviewed);
      } else {
        await widget.api.reviewComment(id, reviewed);
      }
    } catch (error) {
      if (!mounted) return;
      setState(() => row['reviewed'] = before);
      showMessage(context, error);
    }
  }

  void _set(VoidCallback change) {
    setState(change);
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final rows = _rows;
    return Scaffold(
      appBar: AppBar(
        title: const Text('대화 평가'),
        leading: IconButton(
          tooltip: '관리자 화면',
          icon: const Icon(Icons.arrow_back_rounded),
          onPressed: () => context.go('/admin'),
        ),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 40),
        child: PageBody(
          maxWidth: 820,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  Pill(label: '말풍선 평가', selected: _tab == 'bubble', onTap: () => _set(() => _tab = 'bubble')),
                  Pill(label: '대화 한마디', selected: _tab == 'comment', onTap: () => _set(() => _tab = 'comment')),
                ],
              ),
              const SizedBox(height: 14),
              if (_tab == 'bubble') ...[
                const SectionLabel('고른 것'),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    Pill(label: '전체', selected: _tag == null, onTap: () => _set(() => _tag = null)),
                    for (final e in feedbackChoices.entries)
                      Pill(label: e.value, selected: _tag == e.key, onTap: () => _set(() => _tag = e.key)),
                  ],
                ),
                const SizedBox(height: 12),
              ],
              const SectionLabel('확인'),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Pill(label: '확인 전', selected: _reviewed == false, onTap: () => _set(() => _reviewed = false)),
                  Pill(label: '확인함', selected: _reviewed == true, onTap: () => _set(() => _reviewed = true)),
                  Pill(label: '전체', selected: _reviewed == null, onTap: () => _set(() => _reviewed = null)),
                  if (_tab == 'bubble')
                    Pill(
                      label: '고친 말 있는 것만',
                      icon: Icons.edit_note_rounded,
                      selected: _correctedOnly,
                      onTap: () => _set(() => _correctedOnly = !_correctedOnly),
                    ),
                ],
              ),
              const SizedBox(height: 18),
              if (_error != null)
                LoadFailed(_error!, onRetry: _load)
              else if (rows == null)
                const Padding(padding: EdgeInsets.all(40), child: Loading())
              else if (rows.isEmpty)
                const Notice('이 조건의 평가가 없어요.')
              else ...[
                Text('${rows.length} / $_total건', style: AppText.small),
                const SizedBox(height: 8),
                for (final row in rows)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: _tab == 'bubble' ? _bubbleRow(row) : _commentRow(row),
                  ),
                if (rows.length < _total)
                  Center(
                    child: TextButton.icon(
                      onPressed: _loadingMore ? null : _more,
                      icon: const Icon(Icons.expand_more_rounded),
                      label: Text(_loadingMore ? '읽는 중…' : '더 보기'),
                    ),
                  ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _who(Map<String, dynamic> row) => Text(
        [
          '대화 상대: ${row['personaTitle'] ?? '(지움)'}',
          if (row['member'] == true) '함께 쓰는 가족',
          _date(row['createdAt']),
        ].join(' · '),
        style: AppText.small,
      );

  Widget _reviewedSwitch(Map<String, dynamic> row) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text('확인함', style: AppText.small),
          Switch(value: row['reviewed'] == true, onChanged: (v) => _toggleReviewed(row, v)),
        ],
      );

  Widget _bubbleRow(Map<String, dynamic> row) {
    final tags = [for (final t in row['tags'] as List? ?? const []) '$t'];
    final state = feedbackStateLabels[row['state']];
    final correction = '${row['correction'] ?? ''}'.trim();
    final comment = '${row['comment'] ?? ''}'.trim();
    return AppCard(
      padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: [
                    StatusChip(row['rating'] == 'UP' ? '좋아요' : (row['rating'] == 'DOWN' ? '아쉬워요' : '보통')),
                    for (final t in tags) StatusChip(feedbackChoices[t] ?? t, positive: t == 'LIKE'),
                    if (state != null) StatusChip(state, positive: row['state'] == 'applied'),
                  ],
                ),
              ),
              _reviewedSwitch(row),
            ],
          ),
          const SizedBox(height: 6),
          _who(row),
          if ('${row['reply'] ?? ''}'.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('대답: ${row['reply']}', maxLines: 4, overflow: TextOverflow.ellipsis, style: AppText.body),
          ],
          if (correction.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              '고친 말: $correction',
              style: AppText.body.copyWith(color: AppColors.primaryDeep, fontWeight: FontWeight.w600),
            ),
          ],
          if (comment.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text('남긴 말: $comment', style: AppText.body),
          ],
        ],
      ),
    );
  }

  Widget _commentRow(Map<String, dynamic> row) {
    return AppCard(
      padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('${row['comment'] ?? ''}', style: AppText.body),
                const SizedBox(height: 6),
                _who(row),
              ],
            ),
          ),
          _reviewedSwitch(row),
        ],
      ),
    );
  }
}

/// 관리자 화면의 들어가는 카드.
class FeedbackAdminLink extends StatelessWidget {
  const FeedbackAdminLink({super.key});

  @override
  Widget build(BuildContext context) {
    return AppCard(
      padding: const EdgeInsets.fromLTRB(16, 14, 12, 14),
      onTap: () => context.go('/admin/feedback'),
      child: const Row(
        children: [
          Icon(Icons.rate_review_outlined, color: AppColors.ink),
          SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('대화 평가', style: AppText.label),
                SizedBox(height: 2),
                Text('말풍선 평가(고른 것·고친 말)와 대화 한마디, 확인함 표시', style: AppText.small),
              ],
            ),
          ),
          Icon(Icons.chevron_right_rounded, color: AppColors.muted),
        ],
      ),
    );
  }
}
