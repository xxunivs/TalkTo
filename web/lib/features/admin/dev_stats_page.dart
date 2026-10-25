import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import '../../core/platform/browser.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart' show Notice;
import 'stats_api.dart';

/// 통계 한 묶음을 읽는다(기간: 최근 [days]일). 시험에서는 가짜를 넣는다.
typedef StatsLoader = Future<Map<String, dynamic>> Function(int days);

/// CSV 파일을 받는다(kind: signups·requests = 처음부터 전부, events = 고른 기간).
typedef StatsExporter = Future<List<int>> Function(String kind, int days);

/// 개발자 화면 · 사용 통계(/admin/stats, 관리자만). 서버 GET /admin/stats 를 그대로 그린다.
/// PC 에서 보기 좋게 넓게 쓰지만, 휴대폰 폭에서도 넘치지 않는다(표는 옆으로 밀어 본다).
class DevStatsPage extends StatefulWidget {
  final StatsLoader? loader;
  final StatsExporter? exporter;

  const DevStatsPage({super.key, this.loader, this.exporter});

  @override
  State<DevStatsPage> createState() => _DevStatsPageState();
}

class _DevStatsPageState extends State<DevStatsPage> {
  static const _periods = [7, 30, 90];

  int _days = 30;
  Map<String, dynamic>? _stats;
  Object? _error;
  bool _loading = true;
  int _request = 0;
  String? _exporting;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final request = ++_request;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final stats = await (widget.loader ?? StatsApi.load)(_days);
      if (!mounted || request != _request) return;
      setState(() {
        _stats = stats;
        _loading = false;
      });
    } catch (error) {
      if (!mounted || request != _request) return;
      setState(() {
        _error = error;
        _loading = false;
      });
    }
  }

  void _pickDays(int days) {
    if (days == _days) return;
    _days = days;
    _load();
  }

  Future<void> _export(String kind) async {
    if (_exporting != null) return;
    setState(() => _exporting = kind);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final bytes = await (widget.exporter ?? StatsApi.export)(kind, _days);
      final now = DateTime.now();
      final stamp = '${now.year}${_two(now.month)}${_two(now.day)}';
      final period = kind == 'events' ? '${_days}d' : 'all';
      downloadBytes('talkto_${kind}_${period}_$stamp.csv', bytes, 'text/csv;charset=utf-8');
      final what = switch (kind) {
        'signups' => '가입자(전체 기간)',
        'requests' => '구독 신청(전체 기간)',
        _ => '이벤트(최근 $_days일)',
      };
      messenger.showSnackBar(SnackBar(content: Text('$what CSV 를 내려받았어요.')));
    } catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(error.toString())));
    } finally {
      if (mounted) setState(() => _exporting = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.sizeOf(context).width;
    final side = width < 600 ? 16.0 : 28.0;
    final stats = _stats;
    return Scaffold(
      backgroundColor: AppColors.paper,
      body: SafeArea(
        child: Column(
          children: [
            if (_loading && stats != null) const LinearProgressIndicator(minHeight: 2) else const SizedBox(height: 2),
            Expanded(
              child: SingleChildScrollView(
                padding: EdgeInsets.fromLTRB(side, width < 600 ? 16 : 28, side, 48),
                child: Align(
                  alignment: Alignment.topCenter,
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 1180),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _Header(
                          stats: stats,
                          days: _days,
                          periods: _periods,
                          loading: _loading,
                          exporting: _exporting,
                          onDays: _pickDays,
                          onRefresh: _load,
                          onFeedback: () => context.go('/admin'),
                          onImport: () => context.go('/admin/import'),
                          onExport: _export,
                        ),
                        const SizedBox(height: 24),
                        if (stats == null && _loading)
                          const _LoadingBox()
                        else if (stats == null)
                          _ErrorBox(message: '$_error', onRetry: _load)
                        else ...[
                          if (_error != null) ...[
                            _ErrorBox(message: '$_error', onRetry: _load),
                            const SizedBox(height: 16),
                          ],
                          StatsBody(stats: stats),
                        ],
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// ───────────────────────── 머리 ─────────────────────────

class _Header extends StatelessWidget {
  final Map<String, dynamic>? stats;
  final int days;
  final List<int> periods;
  final bool loading;
  final String? exporting;
  final ValueChanged<int> onDays;
  final VoidCallback onRefresh;
  final VoidCallback onFeedback;
  final VoidCallback onImport;
  final ValueChanged<String> onExport;

  const _Header({
    required this.stats,
    required this.days,
    required this.periods,
    required this.loading,
    required this.exporting,
    required this.onDays,
    required this.onRefresh,
    required this.onFeedback,
    required this.onImport,
    required this.onExport,
  });

  @override
  Widget build(BuildContext context) {
    final generated = _dateTime(stats?['generatedAt']);
    final paymentsOpen = stats?['paymentsOpen'];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 10,
          runSpacing: 6,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            const Text('개발자 화면 · 사용 통계', style: AppText.title),
            if (paymentsOpen is bool)
              _Badge(
                paymentsOpen ? '결제 열림' : '결제 전 모드 (구독 신청만)',
                color: paymentsOpen ? AppColors.primary : AppColors.clay,
                background: paymentsOpen ? AppColors.primarySoft : AppColors.claySoft,
              ),
          ],
        ),
        const SizedBox(height: 4),
        Text(
          '관리자만 볼 수 있어요${generated.isEmpty ? '' : ' · $generated 기준'}',
          style: AppText.small,
        ),
        if (stats?['aiProviderTrouble'] is Map) ...[
          const SizedBox(height: 14),
          _ProviderWarning(stats!['aiProviderTrouble'] as Map),
        ],
        // 목소리 회사 문제는 서버가 따로 보낸다(둘이 함께 뜰 수 있다)
        if (stats?['voiceProviderTrouble'] is Map) ...[
          const SizedBox(height: 14),
          _ProviderWarning(stats!['voiceProviderTrouble'] as Map),
        ],
        const SizedBox(height: 16),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            for (final d in periods) _Chip(label: '$d일', selected: d == days, onTap: () => onDays(d)),
            const SizedBox(width: 4),
            _SmallButton(
              icon: Icons.refresh_rounded,
              label: '새로고침',
              onPressed: loading ? null : onRefresh,
            ),
            _SmallButton(icon: Icons.forum_outlined, label: '피드백 보기', onPressed: onFeedback),
            _SmallButton(icon: Icons.move_to_inbox_outlined, label: '페르소나 가져오기', onPressed: onImport),
            _SmallButton(
              icon: Icons.download_rounded,
              label: exporting == 'signups' ? '내려받는 중…' : 'CSV · 가입자(전체 기간)',
              onPressed: exporting == null ? () => onExport('signups') : null,
            ),
            _SmallButton(
              icon: Icons.download_rounded,
              label: exporting == 'requests' ? '내려받는 중…' : 'CSV · 구독 신청(전체 기간)',
              onPressed: exporting == null ? () => onExport('requests') : null,
            ),
            _SmallButton(
              icon: Icons.download_rounded,
              label: exporting == 'events' ? '내려받는 중…' : 'CSV · 이벤트(최근 $days일)',
              onPressed: exporting == null ? () => onExport('events') : null,
            ),
          ],
        ),
      ],
    );
  }
}

// ───────────────────────── 본문 ─────────────────────────

/// 통계 한 묶음을 그린다(머리 아래 전부). 시험에서 바로 그려 본다.
class StatsBody extends StatelessWidget {
  final Map<String, dynamic> stats;

  const StatsBody({super.key, required this.stats});

  @override
  Widget build(BuildContext context) {
    final days = _int(stats['days']);
    return LayoutBuilder(
      builder: (context, constraints) {
        final wide = constraints.maxWidth >= 860;
        Widget pair(Widget a, Widget b) => wide
            ? Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [Expanded(child: a), const SizedBox(width: 16), Expanded(child: b)],
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [a, const SizedBox(height: 16), b],
              );

        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _MetricCards(stats: stats),
            const SizedBox(height: 28),
            _Section(
              title: '사람별 진행 단계',
              note: '가입자 전체 · 기간과 상관없이 지금 상태',
              child: _PeopleStages(stages: _list(stats['userStages']), users: _list(stats['users'])),
            ),
            const SizedBox(height: 16),
            pair(
              _Section(
                title: '퍼널',
                note: '사람 수 · 앞 단계 대비 %',
                child: _Funnel(steps: _list(stats['funnel'])),
              ),
              _Section(
                title: '대화 상대 만들기',
                note: days > 0 ? '최근 $days일' : null,
                child: _Builds(builds: _map(stats['builds'])),
              ),
            ),
            const SizedBox(height: 16),
            _Section(
              title: '일별',
              note: days > 0 ? '최근 $days일' : null,
              child: _Daily(days: _list(stats['daily'])),
            ),
            const SizedBox(height: 16),
            pair(
              _Section(
                title: '대화 상대 단계',
                child: _Distribution(values: _map(stats['personasByStep']), labels: _stepLabels),
              ),
              _Section(
                title: '돌아가신 분 · 살아 계신 분',
                child: _Distribution(values: _map(stats['lifeStatus']), labels: _lifeLabels),
              ),
            ),
            const SizedBox(height: 16),
            pair(
              _Section(
                title: '목소리 종류',
                child: _Distribution(values: _map(stats['voicesByProvider']), labels: _voiceLabels),
              ),
              _Section(
                title: '신청 요금제',
                child: _Distribution(values: _map(stats['requestsByPlan']), labels: _planLabels),
              ),
            ),
            const SizedBox(height: 16),
            _Section(
              title: '최근 구독 신청',
              note: '연락용 · 최근 50건',
              trailing: _CopyEmails(rows: _list(stats['recentRequests'])),
              child: _RequestsTable(rows: _list(stats['recentRequests'])),
            ),
            const SizedBox(height: 16),
            pair(
              _Section(
                title: '많이 본 화면',
                note: days > 0 ? '최근 $days일' : null,
                child: _ScreensTable(rows: _list(stats['topScreens'])),
              ),
              _Section(
                title: '최근 가입',
                note: '가장 최근 30명 · 전체는 가입자 CSV',
                child: _SignupsTable(rows: _list(stats['recentSignups'])),
              ),
            ),
          ],
        );
      },
    );
  }
}

// ───────────────────────── 숫자 카드 ─────────────────────────

class _MetricCards extends StatelessWidget {
  final Map<String, dynamic> stats;

  const _MetricCards({required this.stats});

  @override
  Widget build(BuildContext context) {
    final t = _map(stats['totals']);
    final a = _map(stats['active']);
    final errors = _int(t['errors24h']);
    final items = <_Metric>[
      _Metric('가입자', _int(t['users']), sub: '탈퇴 ${_fmt(_int(t['deletedUsers']))}명'),
      _Metric('활동 사용자 · 1일', _int(a['d1'])),
      _Metric('활동 사용자 · 7일', _int(a['d7'])),
      _Metric('활동 사용자 · 30일', _int(a['d30'])),
      _Metric('대화 상대', _int(t['personas']), sub: '다 만든 것 ${_fmt(_int(t['personasReady']))}'),
      _Metric('다 만든 대화 상대', _int(t['personasReady'])),
      _Metric('대화', _int(t['conversations'])),
      _Metric('메시지', _int(t['messages']), sub: '음성 ${_fmt(_int(t['voiceMessages']))}'),
      _Metric('대답 듣기', _int(t['replyPlays'])),
      _Metric('구독 신청', _int(t['subscriptionRequests']), highlight: true),
      _Metric('피드백', _int(t['feedbackUp']) + _int(t['feedbackDown']),
          sub: '좋아요 ${_fmt(_int(t['feedbackUp']))} · 아쉬워요 ${_fmt(_int(t['feedbackDown']))}'),
      _Metric('오류 · 24시간', errors, danger: errors > 0),
    ];
    return LayoutBuilder(
      builder: (context, constraints) {
        final w = constraints.maxWidth;
        final columns = w >= 1000 ? 6 : (w >= 700 ? 4 : (w >= 440 ? 3 : 2));
        const gap = 10.0;
        final cardWidth = (w - gap * (columns - 1)) / columns;
        return Wrap(
          spacing: gap,
          runSpacing: gap,
          children: [for (final item in items) SizedBox(width: cardWidth, child: _MetricCard(item))],
        );
      },
    );
  }
}

class _Metric {
  final String label;
  final int value;
  final String? sub;
  final bool highlight;
  final bool danger;

  const _Metric(this.label, this.value, {this.sub, this.highlight = false, this.danger = false});
}

class _MetricCard extends StatelessWidget {
  final _Metric metric;

  const _MetricCard(this.metric);

  @override
  Widget build(BuildContext context) {
    final color = metric.danger ? AppColors.danger : (metric.highlight ? AppColors.primary : AppColors.ink);
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 12, 12, 12),
      decoration: BoxDecoration(
        color: metric.danger ? AppColors.dangerSoft : AppColors.card,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: metric.highlight ? AppColors.primary.withValues(alpha: 0.45) : AppColors.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(metric.label, maxLines: 1, overflow: TextOverflow.ellipsis, style: AppText.small.copyWith(fontSize: 12)),
          const SizedBox(height: 4),
          Text(
            _fmt(metric.value),
            maxLines: 1,
            style: TextStyle(fontSize: 22, height: 1.2, fontWeight: FontWeight.w700, color: color),
          ),
          SizedBox(
            height: 16,
            child: metric.sub == null
                ? null
                : Text(metric.sub!,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 11, color: AppColors.muted)),
          ),
        ],
      ),
    );
  }
}

// ───────────────────────── 사람별 진행 단계 ─────────────────────────

/// 사람마다 지금 어느 단계인가(서버 users·userStages). 위는 단계별 사람 수, 아래는 사람 목록
/// (최근 가입 순). 단계 칩으로 거르고, 사람이 많으면 [_page]명씩 끊어 보여 준다.
class _PeopleStages extends StatefulWidget {
  final List<Map<String, dynamic>> stages;
  final List<Map<String, dynamic>> users;

  const _PeopleStages({required this.stages, required this.users});

  @override
  State<_PeopleStages> createState() => _PeopleStagesState();
}

class _PeopleStagesState extends State<_PeopleStages> {
  static const _page = 50;

  String? _stage; // null = 전체
  int _shown = _page;

  void _pick(String? stage) => setState(() {
        _stage = stage;
        _shown = _page;
      });

  @override
  Widget build(BuildContext context) {
    final users = widget.users;
    // 단계 차례와 이름표: 서버가 준 것, 없으면 이 화면의 이름표
    final labels = <String, String>{
      for (final s in widget.stages)
        if ('${s['key'] ?? ''}'.isNotEmpty) '${s['key']}': '${s['label'] ?? _stageNames['${s['key']}'] ?? s['key']}',
    };
    if (labels.isEmpty) labels.addAll(_stageNames);
    final counts = <String, int>{};
    for (final u in users) {
      final key = '${u['stage'] ?? ''}';
      counts[key] = (counts[key] ?? 0) + 1;
    }
    // 지금 사람이 있는 단계만 칩으로. 모르는 단계 값도 뒤에 붙여 고를 수 있게
    final keys = [
      ...labels.keys.where((k) => (counts[k] ?? 0) > 0 || k == _stage),
      ...counts.keys.where((k) => k.isNotEmpty && !labels.containsKey(k)),
    ];
    final filtered = _stage == null ? users : [for (final u in users) if ('${u['stage'] ?? ''}' == _stage) u];
    final shown = filtered.take(_shown).toList();
    String stageLabel(Map<String, dynamic> r) {
      final key = '${r['stage'] ?? ''}';
      return '${r['stageLabel'] ?? labels[key] ?? _stageNames[key] ?? key}';
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (widget.stages.isNotEmpty) ...[
          _StageFunnel(stages: widget.stages),
          const SizedBox(height: 18),
        ],
        if (users.isEmpty)
          const _Empty('아직 가입한 사람이 없어요.')
        else ...[
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _Chip(label: '전체 ${_fmt(users.length)}', selected: _stage == null, onTap: () => _pick(null)),
              for (final k in keys)
                _Chip(
                  label: '${labels[k] ?? _stageNames[k] ?? k} ${_fmt(counts[k] ?? 0)}',
                  selected: k == _stage,
                  onTap: () => _pick(k),
                ),
            ],
          ),
          const SizedBox(height: 14),
          if (shown.isEmpty)
            const _Empty('지금 이 단계에 있는 사람이 없어요.')
          else
            _Table(
              minWidth: 1120,
              columns: const [
                ('이메일', 2.6),
                ('단계', 1.7),
                ('가입 방법', 0.9),
                ('가입일', 1.5),
                ('대화 상대 완성/전체', 1.1),
                ('대화', 0.7),
                ('메시지', 0.8),
                ('마지막 활동', 1.1),
                ('구독', 1.3),
                ('가족', 1.5),
              ],
              numeric: const {4, 5, 6},
              custom: (row, col) => col == 1 ? _stageBadge('${shown[row]['stage'] ?? ''}', stageLabel(shown[row])) : null,
              rows: [
                for (final r in shown)
                  [
                    _who(r),
                    stageLabel(r),
                    _methodLabels['${r['method']}'] ?? '${r['method'] ?? ''}',
                    _dateTime(r['createdAt']),
                    _int(r['personaCount']) == 0 ? '' : '${_fmt(_int(r['readyCount']))} / ${_fmt(_int(r['personaCount']))}',
                    _fmt(_int(r['conversationCount'])),
                    _fmt(_int(r['messageCount'])),
                    _ago(r['lastActiveAt']),
                    _subscriptionText(r),
                    _familyText(r),
                  ],
              ],
            ),
          if (filtered.length > shown.length) ...[
            const SizedBox(height: 12),
            Center(
              child: _SmallButton(
                icon: Icons.expand_more_rounded,
                label: '더 보기 (남은 ${_fmt(filtered.length - shown.length)}명)',
                onPressed: () => setState(() => _shown += _page),
              ),
            ),
          ],
        ],
      ],
    );
  }
}

/// 단계별 사람 수. 막대·숫자 = 그 단계까지 온 사람(뒤 단계에 온 사람도 센다), 지금 = 지금 그 단계.
/// 가족(제 대화 상대 없이 초대받아 들어온 사람)은 갈래라 아래에 따로 둔다.
class _StageFunnel extends StatelessWidget {
  final List<Map<String, dynamic>> stages;

  const _StageFunnel({required this.stages});

  @override
  Widget build(BuildContext context) {
    final top = stages.map((s) => _int(s['reached'])).fold<int>(0, math.max);
    final main = [for (final s in stages) if (s['branch'] != true) s];
    final branches = [for (final s in stages) if (s['branch'] == true) s];
    return LayoutBuilder(builder: (context, constraints) {
      final narrow = constraints.maxWidth < 420;
      Widget row(Map<String, dynamic> s) => Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Row(
              children: [
                SizedBox(
                  width: narrow ? 92 : 140,
                  child: Text(
                    '${s['label'] ?? _stageNames['${s['key']}'] ?? s['key'] ?? ''}',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 13, height: 1.3, color: AppColors.ink2),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: _HBar(
                    fraction: top == 0 ? 0 : _int(s['reached']) / top,
                    color: s['branch'] == true ? AppColors.clay : AppColors.primary,
                    height: 14,
                  ),
                ),
                const SizedBox(width: 8),
                SizedBox(
                  width: narrow ? 104 : 128,
                  child: Text.rich(
                    TextSpan(children: [
                      TextSpan(
                        text: '${_fmt(_int(s['reached']))}명',
                        style: const TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink),
                      ),
                      TextSpan(
                        text: ' · 지금 ${_fmt(_int(s['current']))}',
                        style: const TextStyle(color: AppColors.muted),
                      ),
                    ]),
                    textAlign: TextAlign.right,
                    maxLines: 1,
                    style: const TextStyle(fontSize: 12.5),
                  ),
                ),
              ],
            ),
          );
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const Text('막대·숫자는 그 단계까지 온 사람, ‘지금’은 지금 그 단계에 있는 사람이에요.', style: AppText.small),
          const SizedBox(height: 8),
          for (final s in main) row(s),
          if (branches.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('갈래 · 제 대화 상대 없이 가족으로 들어온 사람', style: AppText.small.copyWith(fontSize: 12)),
            const SizedBox(height: 2),
            for (final s in branches) row(s),
          ],
        ],
      );
    });
  }
}

/// 단계 배지 색: 가입·동의는 회색, 만드는 중은 흙색, 완성은 연한 초록, 대화부터는 진한 초록
Widget _stageBadge(String stage, String label) {
  final (color, background) = switch (stage) {
    'signed_up' || 'consented' => (AppColors.ink2, AppColors.sunk),
    'ready' => (AppColors.primary, AppColors.primarySoft),
    'chatting' || 'requested' || 'subscribed' => (Colors.white, AppColors.primary),
    'family_member' => (AppColors.primary, AppColors.sunk),
    _ => (AppColors.clay, AppColors.claySoft),
  };
  return _Badge(label, color: color, background: background);
}

// ───────────────────────── 퍼널 ─────────────────────────

class _Funnel extends StatelessWidget {
  final List<Map<String, dynamic>> steps;

  const _Funnel({required this.steps});

  @override
  Widget build(BuildContext context) {
    if (steps.isEmpty) return const _Empty('아직 퍼널 자료가 없어요.');
    final top = steps.map((s) => _int(s['users'])).fold<int>(0, math.max);
    return LayoutBuilder(builder: (context, constraints) {
      final labelWidth = constraints.maxWidth < 420 ? 92.0 : 140.0;
      return Column(
        children: [
          for (var i = 0; i < steps.length; i++)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 5),
              child: Row(
                children: [
                  SizedBox(
                    width: labelWidth,
                    child: Text(
                      '${steps[i]['label'] ?? steps[i]['key'] ?? ''}',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontSize: 13, height: 1.3, color: AppColors.ink2),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: _HBar(
                      fraction: top == 0 ? 0 : _int(steps[i]['users']) / top,
                      color: AppColors.primary,
                      height: 18,
                    ),
                  ),
                  const SizedBox(width: 8),
                  SizedBox(
                    width: 92,
                    child: Text.rich(
                      TextSpan(children: [
                        TextSpan(
                          text: '${_fmt(_int(steps[i]['users']))}명',
                          style: const TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink),
                        ),
                        TextSpan(
                          text: i == 0 ? '' : '  ${_percent(_int(steps[i]['users']), _int(steps[i - 1]['users']))}',
                          style: const TextStyle(color: AppColors.muted),
                        ),
                      ]),
                      textAlign: TextAlign.right,
                      maxLines: 1,
                      style: const TextStyle(fontSize: 13),
                    ),
                  ),
                ],
              ),
            ),
        ],
      );
    });
  }
}

/// 가로 막대(라이브러리 없이). fraction 0~1.
class _HBar extends StatelessWidget {
  final double fraction;
  final Color color;
  final double height;

  const _HBar({required this.fraction, required this.color, this.height = 12});

  @override
  Widget build(BuildContext context) {
    final f = fraction.isNaN ? 0.0 : fraction.clamp(0.0, 1.0);
    return Container(
      height: height,
      decoration: BoxDecoration(color: AppColors.sunk, borderRadius: BorderRadius.circular(height / 2)),
      alignment: Alignment.centerLeft,
      child: FractionallySizedBox(
        widthFactor: f == 0 ? 0 : math.max(f, 0.015),
        heightFactor: 1,
        child: DecoratedBox(
          decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(height / 2)),
        ),
      ),
    );
  }
}

// ───────────────────────── 만들기 요약 ─────────────────────────

class _Builds extends StatelessWidget {
  final Map<String, dynamic> builds;

  const _Builds({required this.builds});

  @override
  Widget build(BuildContext context) {
    final started = _int(builds['started']);
    final done = _int(builds['done']);
    final failed = _int(builds['failed']);
    final avg = builds['avgSeconds'];
    final rows = [
      ('시작', _fmt(started), AppColors.ink),
      ('다 만듦', _fmt(done), AppColors.primary),
      ('실패', _fmt(failed), failed > 0 ? AppColors.danger : AppColors.ink),
      ('평균 걸린 시간', avg is num && avg > 0 ? _duration(avg) : '—', AppColors.ink),
      ('성공률', done + failed == 0 ? '—' : _percent(done, done + failed), AppColors.ink),
    ];
    return Column(
      children: [
        for (final row in rows)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 7),
            child: Row(
              children: [
                Expanded(child: Text(row.$1, style: const TextStyle(fontSize: 14, color: AppColors.ink2))),
                Text(row.$2, style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700, color: row.$3)),
              ],
            ),
          ),
      ],
    );
  }
}

// ───────────────────────── 일별 막대 ─────────────────────────

class _Daily extends StatefulWidget {
  final List<Map<String, dynamic>> days;

  const _Daily({required this.days});

  @override
  State<_Daily> createState() => _DailyState();
}

class _DailyState extends State<_Daily> {
  static const _series = [
    ('signups', '가입'),
    ('activeUsers', '활동'),
    ('messages', '메시지'),
    ('requests', '구독 신청'),
  ];

  String _key = 'signups';

  @override
  Widget build(BuildContext context) {
    final days = widget.days;
    final values = [for (final d in days) _int(d[_key])];
    final top = values.fold<int>(0, math.max);
    final total = values.fold<int>(0, (a, b) => a + b);
    final label = _series.firstWhere((s) => s.$1 == _key).$2;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final s in _series)
              _Chip(label: s.$2, selected: s.$1 == _key, onTap: () => setState(() => _key = s.$1)),
          ],
        ),
        const SizedBox(height: 14),
        if (days.isEmpty)
          const _Empty('이 기간의 일별 자료가 없어요.')
        else ...[
          Text('$label · 합계 ${_fmt(total)} · 하루 최대 ${_fmt(top)}', style: AppText.small),
          const SizedBox(height: 10),
          SizedBox(
            height: 180,
            child: LayoutBuilder(builder: (context, constraints) {
              final gap = constraints.maxWidth / days.length > 10 ? 3.0 : 1.0;
              final showValues = days.length <= 14 && constraints.maxWidth / days.length >= 24;
              return Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  for (var i = 0; i < days.length; i++)
                    Expanded(
                      child: Tooltip(
                        message: '${_shortDate(days[i]['date'])} · $label ${_fmt(values[i])}',
                        child: Padding(
                          padding: EdgeInsets.symmetric(horizontal: gap / 2),
                          child: Column(
                            mainAxisAlignment: MainAxisAlignment.end,
                            children: [
                              if (showValues)
                                Text(
                                  _fmt(values[i]),
                                  maxLines: 1,
                                  style: const TextStyle(fontSize: 10, color: AppColors.muted),
                                ),
                              if (showValues) const SizedBox(height: 2),
                              Container(
                                height: top == 0 ? 2 : math.max(2, (showValues ? 156 : 176) * values[i] / top),
                                decoration: BoxDecoration(
                                  color: values[i] == 0 ? AppColors.line : AppColors.primary,
                                  borderRadius: const BorderRadius.vertical(top: Radius.circular(3)),
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                ],
              );
            }),
          ),
          const Divider(height: 1),
          const SizedBox(height: 6),
          Row(
            children: [
              Text(_shortDate(days.first['date']), style: AppText.small.copyWith(fontSize: 11)),
              const Spacer(),
              if (days.length > 2)
                Text(_shortDate(days[days.length ~/ 2]['date']), style: AppText.small.copyWith(fontSize: 11)),
              const Spacer(),
              Text(_shortDate(days.last['date']), style: AppText.small.copyWith(fontSize: 11)),
            ],
          ),
        ],
      ],
    );
  }
}

// ───────────────────────── 분포 ─────────────────────────

class _Distribution extends StatelessWidget {
  final Map<String, dynamic> values;
  final Map<String, String> labels;

  const _Distribution({required this.values, required this.labels});

  @override
  Widget build(BuildContext context) {
    // 정해진 순서(labels) 먼저, 모르는 값은 뒤에
    final keys = [
      ...labels.keys.where(values.containsKey),
      ...values.keys.where((k) => !labels.containsKey(k)),
    ];
    if (keys.isEmpty) return const _Empty('아직 자료가 없어요.');
    final total = keys.fold<int>(0, (a, k) => a + _int(values[k]));
    final top = keys.map((k) => _int(values[k])).fold<int>(0, math.max);
    return Column(
      children: [
        for (final k in keys)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Row(
              children: [
                SizedBox(
                  width: 128,
                  child: Text(
                    labels[k] ?? k,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 13, height: 1.3, color: AppColors.ink2),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: _HBar(fraction: top == 0 ? 0 : _int(values[k]) / top, color: AppColors.clay, height: 10),
                ),
                const SizedBox(width: 8),
                SizedBox(
                  width: 76,
                  child: Text(
                    '${_fmt(_int(values[k]))}  ${_percent(_int(values[k]), total)}',
                    textAlign: TextAlign.right,
                    maxLines: 1,
                    style: const TextStyle(fontSize: 12.5, color: AppColors.ink),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

// ───────────────────────── 표 ─────────────────────────

class _RequestsTable extends StatelessWidget {
  final List<Map<String, dynamic>> rows;

  const _RequestsTable({required this.rows});

  @override
  Widget build(BuildContext context) {
    if (rows.isEmpty) return const _Empty('아직 구독 신청이 없어요.');
    return _Table(
      minWidth: 760,
      columns: const [('이메일', 3), ('대화 상대', 2), ('요금제', 2), ('상태', 1.2), ('날짜', 1.6)],
      rows: [
        for (final r in rows)
          [
            _who(r),
            _personaText(r['personaTitle'], r['relation']),
            '${r['planName'] ?? _planLabels[r['plan']] ?? r['plan'] ?? ''}',
            _requestStatus['${r['status']}'] ?? '${r['status'] ?? ''}',
            _dateTime(r['createdAt']),
          ],
      ],
    );
  }
}

/// 신청한(취소 안 한) 사람 이메일을 한 번에 복사(결제 열림 안내 메일용).
class _CopyEmails extends StatelessWidget {
  final List<Map<String, dynamic>> rows;

  const _CopyEmails({required this.rows});

  @override
  Widget build(BuildContext context) {
    final emails = <String>{
      for (final r in rows)
        if (r['status'] == 'REQUESTED' && '${r['email'] ?? ''}'.contains('@')) '${r['email']}',
    };
    if (emails.isEmpty) return const SizedBox.shrink();
    return _SmallButton(
      icon: Icons.copy_rounded,
      label: '신청자 이메일 복사 (${emails.length})',
      onPressed: () async {
        await Clipboard.setData(ClipboardData(text: emails.join(', ')));
        if (!context.mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('신청자 이메일 ${emails.length}개를 복사했어요.')),
        );
      },
    );
  }
}

class _ScreensTable extends StatelessWidget {
  final List<Map<String, dynamic>> rows;

  const _ScreensTable({required this.rows});

  @override
  Widget build(BuildContext context) {
    if (rows.isEmpty) return const _Empty('아직 화면 기록이 없어요.');
    return _Table(
      minWidth: 360,
      columns: const [('화면', 3), ('본 횟수', 1), ('사람 수', 1)],
      numeric: const {1, 2},
      rows: [
        for (final r in rows)
          [
            _screenText('${r['screen'] ?? ''}'),
            _fmt(_int(r['views'])),
            _fmt(_int(r['users'])),
          ],
      ],
    );
  }
}

class _SignupsTable extends StatelessWidget {
  final List<Map<String, dynamic>> rows;

  const _SignupsTable({required this.rows});

  @override
  Widget build(BuildContext context) {
    if (rows.isEmpty) return const _Empty('이 기간에 가입한 사람이 없어요.');
    return _Table(
      minWidth: 420,
      columns: const [('이메일', 3), ('가입 방법', 1.2), ('날짜', 1.8)],
      rows: [
        for (final r in rows)
          [
            _who(r),
            _methodLabels['${r['method']}'] ?? '${r['method'] ?? ''}',
            _dateTime(r['createdAt']),
          ],
      ],
    );
  }
}

/// 간단한 표. 칸 글은 골라 복사할 수 있다. 좁은 화면에서는 [minWidth] 로 두고 옆으로 민다.
class _Table extends StatelessWidget {
  final List<(String, double)> columns;
  final List<List<String>> rows;
  final double minWidth;
  final Set<int> numeric;

  /// 글 대신 그릴 칸(예: 단계 배지). null 이면 그 칸은 글로 그린다.
  final Widget? Function(int row, int col)? custom;

  const _Table({
    required this.columns,
    required this.rows,
    required this.minWidth,
    this.numeric = const {},
    this.custom,
  });

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, constraints) {
      final width = math.max(constraints.maxWidth, minWidth);
      Widget cell(String text, int col, {bool head = false}) => Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
            child: Text(
              text,
              textAlign: numeric.contains(col) ? TextAlign.right : TextAlign.left,
              style: TextStyle(
                fontSize: 13,
                height: 1.35,
                fontWeight: head ? FontWeight.w700 : FontWeight.w400,
                color: head ? AppColors.ink2 : AppColors.ink,
              ),
            ),
          );
      Widget? special(int row, int col) {
        final child = custom?.call(row, col);
        if (child == null) return null;
        return Padding(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
          child: Align(alignment: Alignment.centerLeft, child: child),
        );
      }

      final table = Table(
        columnWidths: {for (var i = 0; i < columns.length; i++) i: FlexColumnWidth(columns[i].$2)},
        defaultVerticalAlignment: TableCellVerticalAlignment.middle,
        children: [
          TableRow(
            decoration: const BoxDecoration(color: AppColors.sunk),
            children: [for (var i = 0; i < columns.length; i++) cell(columns[i].$1, i, head: true)],
          ),
          for (var r = 0; r < rows.length; r++)
            TableRow(
              decoration: const BoxDecoration(border: Border(top: BorderSide(color: AppColors.line))),
              children: [
                for (var i = 0; i < columns.length; i++)
                  special(r, i) ?? cell(i < rows[r].length ? rows[r][i] : '', i),
              ],
            ),
        ],
      );
      return ClipRRect(
        borderRadius: BorderRadius.circular(10),
        child: SelectionArea(
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: SizedBox(width: width, child: table),
          ),
        ),
      );
    });
  }
}

// ───────────────────────── 작은 조각 ─────────────────────────

class _Section extends StatelessWidget {
  final String title;
  final String? note;
  final Widget? trailing;
  final Widget child;

  const _Section({required this.title, required this.child, this.note, this.trailing});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(18, 16, 18, 18),
      decoration: BoxDecoration(
        color: AppColors.card,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.line),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 10,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            alignment: WrapAlignment.spaceBetween,
            children: [
              Wrap(
                spacing: 8,
                crossAxisAlignment: WrapCrossAlignment.end,
                children: [
                  Text(title, style: AppText.heading),
                  if (note != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 2),
                      child: Text(note!, style: AppText.small.copyWith(fontSize: 12)),
                    ),
                ],
              ),
              if (trailing != null) trailing!,
            ],
          ),
          const SizedBox(height: 14),
          child,
        ],
      ),
    );
  }
}

class _Chip extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _Chip({required this.label, required this.selected, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final radius = BorderRadius.circular(999);
    return Material(
      color: selected ? AppColors.primary : AppColors.card,
      shape: RoundedRectangleBorder(
        borderRadius: radius,
        side: BorderSide(color: selected ? AppColors.primary : AppColors.line2),
      ),
      child: InkWell(
        onTap: onTap,
        borderRadius: radius,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 14,
              fontWeight: selected ? FontWeight.w700 : FontWeight.w400,
              color: selected ? Colors.white : AppColors.ink,
            ),
          ),
        ),
      ),
    );
  }
}

class _SmallButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback? onPressed;

  const _SmallButton({required this.icon, required this.label, required this.onPressed});

  @override
  Widget build(BuildContext context) {
    return OutlinedButton.icon(
      onPressed: onPressed,
      icon: Icon(icon, size: 17),
      label: Text(label),
      style: OutlinedButton.styleFrom(
        minimumSize: const Size(0, 38),
        padding: const EdgeInsets.symmetric(horizontal: 14),
        textStyle: const TextStyle(fontSize: 13.5, fontWeight: FontWeight.w600, fontFamilyFallback: koreanFallback),
      ),
    );
  }
}

class _Badge extends StatelessWidget {
  final String text;
  final Color color;
  final Color background;

  const _Badge(this.text, {required this.color, required this.background});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: background, borderRadius: BorderRadius.circular(999)),
      child: Text(text, style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: color)),
    );
  }
}

class _Empty extends StatelessWidget {
  final String text;

  const _Empty(this.text);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Text(text, style: AppText.small),
    );
  }
}

class _LoadingBox extends StatelessWidget {
  const _LoadingBox();

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 220,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: AppColors.card,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.line),
      ),
      child: const Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(width: 26, height: 26, child: CircularProgressIndicator(strokeWidth: 2.6)),
          SizedBox(height: 12),
          Text('통계를 불러오는 중이에요…', style: AppText.small),
        ],
      ),
    );
  }
}

class _ErrorBox extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;

  const _ErrorBox({required this.message, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: AppColors.dangerSoft,
        borderRadius: BorderRadius.circular(18),
        border: Border.all(color: AppColors.danger.withValues(alpha: 0.3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text('통계를 불러오지 못했어요.',
              style: TextStyle(fontSize: 15, fontWeight: FontWeight.w700, color: AppColors.danger)),
          const SizedBox(height: 4),
          Text(message, style: const TextStyle(fontSize: 13.5, height: 1.5, color: AppColors.danger)),
          const SizedBox(height: 12),
          _SmallButton(icon: Icons.refresh_rounded, label: '다시 불러오기', onPressed: onRetry),
        ],
      ),
    );
  }
}

// ───────────────────────── 이름표·숫자 ─────────────────────────

const _stepLabels = {
  'UPLOAD': '녹음 올리는 중',
  'SPEAKERS': '목소리 고르는 중',
  'QUESTIONS': '질문 답하는 중',
  'BUILDING': '만드는 중',
  'READY': '다 만듦',
};

const _lifeLabels = {
  'deceased': '돌아가신 분',
  'alive': '살아 계신 분',
  // 2026-10-22부터 새로 만들지 않는다(예전 대화 상대만 남음 — /admin/unable)
  'unable': '대화가 어려우신 분(예전 상태)',
};

const _voiceLabels = {
  'fish': 'Fish drama-3',
  // 예전 목소리. 모두 Fish 로 다시 만들 때까지 남아 있다
  'minimax': 'MiniMax (전화 녹음, 종료)',
  'elevenlabs': 'ElevenLabs (종료)',
  'elevenlabs_pvc': '똑 닮은 목소리 (PVC, 종료)',
  'none': '아직 없음',
};

const _planLabels = {
  'memories': '추억',
  'family': '가족',
  // 없어진 요금제. 예전 신청 기록(통계 줄)을 읽을 수 있게 이름만 남긴다
  'premium': '똑 닮은 목소리 (종료)',
};

const _requestStatus = {
  'REQUESTED': '신청함',
  'CANCELED': '취소함',
  'CONVERTED': '결제함',
};

const _methodLabels = {
  'email': '이메일',
  'kakao': '카카오',
  'google': '구글',
};

/// 사람별 진행 단계(서버 USER_STAGES 차례, 가족 갈래는 끝). 서버가 이름표를 주지 않을 때 쓴다
const _stageNames = {
  'signed_up': '가입만',
  'consented': '동의 완료',
  'persona_started': '만들기 시작',
  'materials': '녹음·자료 올림',
  'voice': '목소리 고름',
  'building': '만드는 중',
  'ready': '완성',
  'chatting': '대화 중',
  'requested': '구독 신청',
  'subscribed': '결제함',
  'family_member': '가족 초대로 들어옴',
};

const _screenLabels = {
  'home': '첫 화면(목록)',
  'persona_chat': '대화 상대(만들기·대화)',
  'persona_more': '기억 더 채우기',
  'persona_gaps': '더 알려 주실 기억',
  // 없어진 화면. 예전 통계 줄과 옛 주소(/p/:id/voice → 대화 화면)를 읽을 수 있게 이름만 남긴다
  'persona_voice': '똑 닮은 목소리 (종료)',
  'persona_recordings': '녹음 더하기',
  'persona_materials': '남긴 글',
  'persona_material_add': '남긴 글 더하기',
  'persona_edit': '정보 고치기',
  'persona_family': '가족과 함께',
  'persona_interview': '기억 인터뷰 받기',
  'persona_support': '고객센터(대화 화면에서)',
  'persona_create': '새 대화 상대 만들기',
  'plans': '요금제',
  'account': '내 계정',
  'support': '고객센터',
  'consent': '필수 동의',
  // 2026-10-18~21 에만 있던 화면(지금은 모든 동의를 가입할 때 받는다). 지난 기록의 이름
  'consent_create': '만들기 전 동의 (예전)',
  'legal': '약관·안내 문서',
  'login': '로그인',
  'join': '초대 받기',
  'admin': '피드백 대시보드',
  'admin_stats': '사용 통계',
  'admin_feedback': '대화 평가',
};

String _screenText(String key) {
  final label = _screenLabels[key];
  return label == null ? key : '$label · $key';
}

/// 이메일 칸. 이메일을 주지 않은 소셜 가입자(카카오 동의 항목 전)는 빈칸 대신 이름으로 알아본다.
String _who(Map<String, dynamic> r) {
  final email = '${r['email'] ?? ''}'.trim();
  if (email.isNotEmpty) return email;
  final name = '${r['name'] ?? ''}'.trim();
  return name.isEmpty ? '' : '$name · 이메일 없음';
}

/// 구독 칸: 결제했으면 '결제함 · 가족', 신청만 했으면 '가족 · 신청함'
String _subscriptionText(Map<String, dynamic> r) {
  final paid = r['subscriptionPlan'];
  if (paid != null) return '결제함 · ${_planLabels['$paid'] ?? paid}';
  final status = r['requestStatus'];
  if (status == null) return '';
  final plan = r['requestPlan'];
  final name = plan == null ? '' : '${_planLabels['$plan'] ?? plan} · ';
  return '$name${_requestStatus['$status'] ?? status}';
}

/// 가족 칸: 남의 대화 상대에 초대받아 들어왔나, 내 대화 상대에 가족이 몇 명 들어왔나
String _familyText(Map<String, dynamic> r) {
  final member = _int(r['memberOf']);
  final joined = _int(r['familyJoined']);
  return [
    if (member > 0) member == 1 ? '초대받은 가족' : '초대받은 가족 ($member곳)',
    if (joined > 0) '가족 ${_fmt(joined)}명 들어옴',
  ].join(' · ');
}

String _personaText(Object? title, Object? relation) {
  final t = '${title ?? ''}'.trim();
  final r = '${relation ?? ''}'.trim();
  if (t.isEmpty) return r;
  if (r.isEmpty || r == t) return t;
  return '$t ($r)';
}

int _int(Object? value) => value is num ? value.toInt() : int.tryParse('${value ?? ''}') ?? 0;

Map<String, dynamic> _map(Object? value) => value is Map ? Map<String, dynamic>.from(value) : const {};

List<Map<String, dynamic>> _list(Object? value) =>
    value is List ? [for (final v in value) if (v is Map) Map<String, dynamic>.from(v)] : const [];

/// 1234567 → 1,234,567
String _fmt(int n) {
  final s = n.abs().toString();
  final out = StringBuffer(n < 0 ? '-' : '');
  for (var i = 0; i < s.length; i++) {
    if (i > 0 && (s.length - i) % 3 == 0) out.write(',');
    out.write(s[i]);
  }
  return out.toString();
}

String _percent(int part, int whole) => whole <= 0 ? '—' : '${(part * 100 / whole).round()}%';

String _duration(num seconds) {
  final s = seconds.round();
  if (s < 60) return '$s초';
  final m = s ~/ 60;
  final rest = s % 60;
  if (m < 60) return rest == 0 ? '$m분' : '$m분 $rest초';
  return '${m ~/ 60}시간 ${m % 60}분';
}

String _two(int n) => n.toString().padLeft(2, '0');

/// 서버 시각을 읽는다. 시간대가 없으면(SQLite) UTC 로 읽는다.
DateTime? _parseTime(String text) {
  final hasZone = RegExp(r'(Z|[+-]\d\d:?\d\d)$').hasMatch(text);
  return DateTime.tryParse(hasZone || !text.contains('T') && !text.contains(' ') ? text : '${text}Z');
}

/// 서버 시각 → 이 기기 시각 'YYYY-MM-DD HH:MM'.
String _dateTime(Object? iso) {
  final text = '${iso ?? ''}'.trim();
  if (text.isEmpty) return '';
  final parsed = _parseTime(text);
  if (parsed == null) return text;
  final t = parsed.toLocal();
  return '${t.year}-${_two(t.month)}-${_two(t.day)} ${_two(t.hour)}:${_two(t.minute)}';
}

/// 마지막 활동: 하루 안이면 '3시간 전', 한 달 안이면 '5일 전', 그보다 오래면 날짜
String _ago(Object? iso) {
  final text = '${iso ?? ''}'.trim();
  final at = text.isEmpty ? null : _parseTime(text);
  if (at == null) return text;
  final gap = DateTime.now().difference(at);
  if (gap.inMinutes < 1) return '방금';
  if (gap.inHours < 1) return '${gap.inMinutes}분 전';
  if (gap.inDays < 1) return '${gap.inHours}시간 전';
  if (gap.inDays < 30) return '${gap.inDays}일 전';
  return _dateTime(text).split(' ').first;
}

/// '2026-10-13' → '10/13'
String _shortDate(Object? date) {
  final text = '${date ?? ''}';
  return text.length >= 10 ? '${text.substring(5, 7)}/${text.substring(8, 10)}' : text;
}

/// AI 회사(OpenAI, aiProviderTrouble) · 목소리 회사(Fish Audio, voiceProviderTrouble) 문제가
/// 풀리지 않았을 때 맨 위에 띄우는 경고(대화·익히기·목소리가 멈춘다).
class _ProviderWarning extends StatelessWidget {
  final Map trouble;

  const _ProviderWarning(this.trouble);

  @override
  Widget build(BuildContext context) {
    final at = _dateTime(trouble['at']);
    final text = switch (trouble['code']) {
      'AI_PROVIDER_QUOTA' => 'OpenAI 크레딧이 떨어져 대화 대답과 “익히기”가 멈췄어요. '
          'platform.openai.com → Settings → Billing 에서 충전해 주세요(자동 충전을 켜 두면 다시 멈추지 않아요).',
      'AI_PROVIDER_AUTH' => 'OpenAI 키가 막혔어요. 키가 바뀌었거나 사용이 정지됐는지 확인해 주세요.',
      'VOICE_PROVIDER_CREDITS' => 'Fish Audio 크레딧이 떨어져 목소리 대답과 목소리 만들기가 멈췄어요. '
          'fish.audio 의 Billing 에서 충전해 주세요. 글 대답은 계속돼요.',
      'VOICE_PROVIDER_KEY' => 'Fish Audio 키가 막혔어요. 키가 바뀌었거나 사용이 정지됐는지 확인해 주세요.',
      'VOICE_PROVIDER_RATE_LIMIT' => 'Fish Audio 요청이 잠시 몰려 일부 목소리 대답이 실패했어요. 계속되면 사용 한도를 확인해 주세요.',
      'VOICE_PROVIDER_UNAVAILABLE' => 'Fish Audio 가 잠시 응답하지 않아 일부 목소리 대답·목소리 만들기가 실패했어요.',
      _ => 'OpenAI 요청이 잠시 몰려 일부 대답이 실패했어요. 계속되면 사용 한도(Limits)를 확인해 주세요.',
    };
    return Notice(
      '$text${at.isEmpty ? '' : ' (마지막: $at)'}',
      icon: Icons.warning_amber_rounded,
      color: AppColors.danger,
      background: AppColors.dangerSoft,
    );
  }
}
