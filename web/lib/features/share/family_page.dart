import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_client.dart';
import '../../core/session.dart';
import '../../core/theme.dart';
import '../../shared/korean.dart';
import '../../shared/widgets/ui.dart';
import '../legal/consent_checks.dart';
import '../legal/legal_api.dart';
import '../legal/legal_text.dart';
import '../personas/data/persona_api.dart';
import '../personas/data/persona_store.dart';
import '../personas/imported.dart';
import '../personas/owner_actions.dart';
import 'invite_sheet.dart';
import 'member_profile_sheet.dart';
import 'roles.dart';
import 'share_api.dart';

/// 가족과 함께(/p/:id/family). 만든 사람이 초대 링크를 만들어 보내고, 함께 쓰는 가족을 본다.
///
/// 가족마다 자기 휴대폰·자기 계정으로 들어와 각자 이야기한다(서로의 대화는 볼 수 없다). 남은 대화 횟수는
/// 함께 쓴다. 링크는 만들거나 다시 보낼 때 이 화면에서 한 번만 보이고, 한 링크에 한 분만 들어온다.
class FamilyPage extends StatefulWidget {
  final String personaId;

  const FamilyPage({super.key, required this.personaId});

  @override
  State<FamilyPage> createState() => _FamilyPageState();
}

class _FamilyPageState extends State<FamilyPage> {
  Map<String, dynamic>? _detail;
  Map<String, dynamic>? _data;
  Object? _error;
  List<String> _relations = const [];

  /// 처음 한 번 체크한 주인 확인(다음 링크를 만들 때 함께 보낸다).
  Set<String> _agreed = {};
  bool _busy = false;

  /// 방금 만든(다시 보낸) 링크. 목록에는 열쇠가 없어서 이 화면에서만 보인다.
  /// [prefill] 은 받는 분에 대해 적어 둔 것(보내는 글의 세 단계가 이것에 따라 달라진다).
  ({String shareId, String url, String? name, Map prefill})? _link;

  String get _id => widget.personaId;
  String get _title => _detail == null ? '그분' : personaTitle(_detail!);
  bool get _self => _data?['subjectKind'] == 'self';
  bool get _imported => isImportedPersona(_detail);

  /// 아이(만 19세 미만)의 대화 상대: 법정대리인과 직계가족(부모·조부모)만 함께 쓴다. 관계는 서버가 준
  /// 목록(relations)에서만 고르고 직접 적지 않는다.
  bool get _minorScope => _data?['minorScope'] == true;

  /// 관계 보기. 아이의 대화 상대면 서버가 준 목록, 아니면 /intake/meta 의 myRelations.
  List<String> get _relationChoices {
    final scoped = _data?['relations'];
    if (_minorScope && scoped is List) return [for (final r in scoped) '$r'];
    return _relations;
  }

  /// 주인 확인 글과 가족 공유 안내의 {{호칭}}. 나 자신 대화 상대면 호칭이 '나'라서 '그분'으로 읽는다
  /// ('나께 …'가 되지 않게).
  String? get _callName => _self ? null : _title;

  List<Map<String, dynamic>> get _items =>
      [for (final i in _data?['items'] as List? ?? const []) Map<String, dynamic>.from(i as Map)];

  /// 링크와 함께 보내는 글의 '○○님'(만든 사람 이름: 기본 정보의 내 이름 → 계정 이름).
  String get _ownerName {
    final basics = _detail?['basics'];
    final mine = basics is Map ? '${basics['myName'] ?? ''}'.trim() : '';
    return mine.isNotEmpty ? mine : (session.name.trim().isEmpty ? '가족' : session.name.trim());
  }

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final detail = await PersonaApi.detail(_id);
      // 함께하는 가족에게는 이 화면이 없다(서버도 막는다)
      final data = isMember(detail) ? null : await ShareApi.list(_id);
      var relations = _relations;
      if (relations.isEmpty) {
        try {
          relations = [for (final r in (await PersonaApi.meta())['myRelations'] as List? ?? const []) '$r'];
        } catch (_) {
          // 관계 목록이 없어도 직접 적을 수 있다
        }
      }
      if (!mounted) return;
      setState(() {
        _detail = detail;
        _data = data;
        _relations = relations;
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = error);
    }
  }

  /// 아직 체크하지 않은 주인 확인 종류.
  List<String> get _need => [for (final t in _data?['need'] as List? ?? const []) '$t'];

  bool get _confirmed => _need.every(_agreed.contains);

  /// 확인을 받을 수 있는가. 서버가 확인할 것이 있다고 하면, 링크를 만들 수 없는 때(자리가 다 참 ·
  /// 요금제 · 잠시 멈춤)에도 받는다(그분 상태를 바꿔 거둬진 확인은 다시 해야 함께 쓰는 가족이 이어 간다).
  /// 아직 다 만들지 않았거나 함께 쓸 수 없는 대화 상대는 서버도 받지 않는다.
  bool get _confirmable => _need.isNotEmpty && !const {'not_ready', 'unsupported'}.contains(_data?['reason']);

  /// '확인했어요': 링크는 만들지 않고 확인만 남긴 뒤 화면을 다시 읽는다.
  Future<void> _confirm() async {
    // 이 확인 때문에 멈춰 있던 가족이 있었나(요금제로도 멈췄으면 확인만으로는 이어지지 않는다)
    final resumes = _items.any((i) => i['status'] == 'joined') && _data?['reason'] != 'plan';
    await _run(() async {
      final left = await ShareApi.confirm(_id, [for (final t in _need) if (_agreed.contains(t)) t]);
      _agreed = {};
      await _load();
      if (!mounted) return;
      showMessage(
        context,
        left.isNotEmpty
            ? '아직 확인할 것이 남았어요.'
            : resumes
                ? '확인했어요. 함께 쓰는 가족도 다시 이야기할 수 있어요.'
                : '확인했어요.',
      );
    });
  }

  Future<void> _invite([Map<String, dynamic>? row]) async {
    if (!_confirmed) {
      showMessage(context, '먼저 위 확인에 모두 체크해 주세요.');
      return;
    }
    try {
      final created = await showInviteSheet(
        context,
        personaId: _id,
        title: _title,
        row: row,
        agreements: _need,
        relations: _relationChoices,
        suggestions: _data?['addressSuggestions'] as Map?,
        self: _self,
        imported: _imported,
        minorScope: _minorScope,
      );
      if (created == null || !mounted) return;
      _showLink(created);
      await _load();
    } on ApiException catch (error) {
      if (mounted) showMessage(context, error);
    }
  }

  void _showLink(Map<String, dynamic> created) {
    final share = created['share'] is Map ? Map<String, dynamic>.from(created['share'] as Map) : const {};
    setState(() {
      _link = (
        shareId: '${share['id'] ?? ''}',
        url: '${created['url'] ?? ''}',
        name: share['label'] as String?,
        prefill: share['prefill'] is Map ? share['prefill'] as Map : const {},
      );
    });
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
    } catch (error) {
      if (!mounted) return;
      showMessage(context, error);
      // 그사이 바뀌었을 수 있다(다른 기기에서 내보냄 등)
      if (error is ApiException && (error.statusCode == 404 || error.statusCode == 409)) await _load();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _renew(Map<String, dynamic> item) => _run(() async {
        final created = await ShareApi.renew(_id, '${item['id']}');
        if (!mounted) return;
        _showLink(created);
        await _load();
      });

  Future<void> _cancel(Map<String, dynamic> item) async {
    final ok = await confirmAction(
      context,
      '이 링크를 취소할까요?',
      '받은 분이 눌러도 함께할 수 없어요.',
      action: '링크 취소',
    );
    if (!ok || !mounted) return;
    await _run(() async {
      await ShareApi.end(_id, '${item['id']}');
      if (_link?.shareId == item['id']) _link = null;
      await _load();
    });
  }

  Future<void> _remove(Map<String, dynamic> item) async {
    final name = _itemName(item);
    final ok = await confirmAction(
      context,
      '$name님을 내보낼까요?',
      '$name님은 더는 ${josa(_title, '과', '와')} 이야기할 수 없고, $name님이 나눈 대화도 지워져요.',
      action: '내보내기',
    );
    if (!ok || !mounted) return;
    String? told;
    await _run(() async {
      told = await ShareApi.end(_id, '${item['id']}');
      await _load();
      personaStore.load();
    });
    // 그 가족이 나눈 대화를 지웠으면 서버 백업에 남는 날까지 알린다
    if (told != null && mounted) showDeletionNote(context, told!);
  }

  /// 들어온 가족 정보 고치기(이름·관계·부르는 말·말 높이) · 쓰지 않은 링크에 적어 둔 것 고치기.
  Future<void> _edit(Map<String, dynamic> item) async {
    final joined = item['status'] == 'joined';
    final source = joined ? item['profile'] : item['prefill'];
    final name = _itemName(item);
    final saved = await showProfileSheet(
      context,
      heading: joined ? '$name님 정보 고치기' : '적어 둔 것 고치기',
      lead: joined ? '고치면 $name님과의 다음 대화부터 이대로 알아보고 불러요.' : null,
      initial: source is Map ? source : null,
      voice: ProfileVoice.owner,
      title: _title,
      self: _self,
      imported: _imported,
      relations: _relationChoices,
      suggestions: _data?['addressSuggestions'] as Map?,
      requireAll: joined,
      minorScope: _minorScope,
      onSave: (profile) async {
        final updated = await ShareApi.update(_id, '${item['id']}', profile);
        // 방금 만든 링크에 적어 둔 것을 고쳤으면 보내는 글도 그에 맞게
        final link = _link;
        if (!joined && link != null && link.shareId == '${item['id']}' && mounted) {
          setState(() {
            _link = (
              shareId: link.shareId,
              url: link.url,
              name: updated['label'] as String?,
              prefill: updated['prefill'] is Map ? updated['prefill'] as Map : const {},
            );
          });
        }
      },
    );
    if (saved != null && mounted) {
      showMessage(context, '고쳤어요.');
      await _load();
    }
  }

  Future<void> _stop() async {
    final ok = await confirmAction(
      context,
      '가족과 함께 쓰기를 그만둘까요?',
      '모든 가족이 더는 이야기할 수 없고, 가족이 나눈 대화도 지워져요.',
      action: '그만두기',
    );
    if (!ok || !mounted) return;
    String? told;
    await _run(() async {
      told = (await ShareApi.stop(_id)).deletion;
      _link = null;
      _agreed = {};
      await _load();
      personaStore.load();
    });
    // 가족이 나눈 대화를 지웠으면 서버 백업에 남는 날까지 알린다
    if (told != null && mounted) showDeletionNote(context, told!);
  }

  static String _itemName(Map<String, dynamic> item) {
    final label = '${item['label'] ?? ''}'.trim();
    return label.isEmpty ? '이 가족' : label;
  }

  // ────────────────────────── 그리기 ──────────────────────────

  @override
  Widget build(BuildContext context) {
    final detail = _detail;
    if (detail == null) {
      return Scaffold(
        appBar: AppBar(automaticallyImplyLeading: !Breakpoints.isWide(context)),
        body: _error != null
            ? LoadFailed(_error!, onRetry: () {
                setState(() => _error = null);
                _load();
              })
            : const Loading(),
      );
    }
    final blocked = PersonaBlocked.of(detail, personaId: _id, imported: false);
    if (blocked != null) return blocked;
    final data = _data ?? const <String, dynamic>{};
    final allowed = data['allowed'] == true;
    final cap = (data['cap'] as num?)?.toInt() ?? 0;
    final used = (data['used'] as num?)?.toInt() ?? _items.length;
    final items = _items;
    final members = items.where((i) => i['status'] == 'joined').length;
    return WizardScaffold(
      title: '가족과 함께',
      headline: '$_title, 가족과 함께',
      lead: '링크를 받은 가족도 각자 자기 휴대폰에서 ${josa(_title, '과', '와')} 이야기할 수 있어요. '
          '가족마다 부르던 말과 말투로 대답해요.',
      secondary: TextButton.icon(
        onPressed: () => context.canPop() ? context.pop() : context.go('/p/$_id'),
        icon: const Icon(Icons.arrow_back_rounded, size: 18),
        label: const Text('대화로 돌아가기'),
      ),
      children: [
        ..._gate(data, members: members),
        if (_confirmable) ..._confirmations(data, members: members),
        if (allowed) ..._whom(data, full: cap > 0 && used >= cap, cap: cap),
        if (_link != null) ...[
          const SizedBox(height: 18),
          InviteLinkCard(
            key: ValueKey(_link!.url),
            url: _link!.url,
            name: _link!.name,
            prefilled: invitePrefilled(_link!.prefill),
            shareText: inviteShareText(
              ownerName: _ownerName,
              addressTerm: _link!.prefill['addressTerm'] as String?,
              prefilled: invitePrefilled(_link!.prefill),
            ),
            onClose: () => setState(() => _link = null),
          ),
        ],
        if (items.isNotEmpty) ...[
          const SizedBox(height: 30),
          SectionLabel('함께 쓰는 가족 $used/$cap'),
          AppCard(
            padding: const EdgeInsets.fromLTRB(16, 6, 12, 6),
            child: Column(
              children: [
                for (var i = 0; i < items.length; i++) ...[
                  if (i > 0) const Divider(height: 1),
                  items[i]['status'] == 'joined' ? _joinedRow(items[i]) : _openRow(items[i]),
                ],
              ],
            ),
          ),
        ],
        const SizedBox(height: 24),
        Notice(
          '가족이 나눈 대화는 그 가족만 볼 수 있어요. 회원님도 볼 수 없어요. '
          "다만 가족이 고쳐 보낸 대답과 보낸 답은 '더 알려 주실 기억'에서 확인할 수 있어요. 남은 대화 횟수는 함께 써요. "
          '$_title의 기억(녹음과 적어 주신 이야기)은 가족과의 대화에서도 쓰여요.',
          icon: Icons.lock_outline_rounded,
          color: AppColors.primary,
          background: AppColors.primarySoft,
        ),
        if (items.isNotEmpty) ...[
          const SizedBox(height: 18),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton(
              onPressed: _busy ? null : _stop,
              style: TextButton.styleFrom(foregroundColor: AppColors.danger),
              child: const Text('가족과 함께 쓰기 그만두기'),
            ),
          ),
        ],
      ],
    );
  }

  /// 지금 링크를 만들 수 있는지에 따른 안내(만들기 전 · 요금제 · 잠시 멈춤 · 결제 전 · 목소리).
  List<Widget> _gate(Map<String, dynamic> data, {required int members}) {
    final reason = data['reason'];
    final out = <Widget>[];
    void add(Widget w) => out
      ..add(w)
      ..add(const SizedBox(height: 12));
    if (_minorScope) add(const Notice(minorScopeText, icon: Icons.child_care_rounded));
    // 멈춤 · 법정대리인 확인 전 · 유족 확인 전: 서버 게이트의 글 그대로(갈 곳이 있으면 단추)
    final gates = PersonaGates.of(_detail);
    final gated = const {'stopped', 'guardian', 'bereaved'}.contains(reason) || !gates.invite;
    if (gated) {
      final code = gates.code ?? '$reason';
      final route = switch (code) {
        'guardian' => '/p/$_id/guardian',
        'bereaved' => '/p/$_id/bereaved',
        _ => null,
      };
      add(Notice(
        gates.message ??
            switch (code) {
              'guardian' => '먼저 법정대리인 확인을 마쳐 주세요.',
              'bereaved' => '돌아가신 분의 대화 상대는 이제 신청 자격과 유족 명단을 적어 주셔야 이어서 대화할 수 있어요.',
              _ => '이 대화 상대는 지금 멈췄어요. 자세한 내용은 알림을 확인해 주세요.',
            },
        icon: Icons.pause_circle_outline_rounded,
        trailing: route == null
            ? null
            : TextButton(onPressed: () => context.go(route), child: Text(code == 'guardian' ? '확인하기' : '적기')),
      ));
    }
    switch (reason) {
      case 'not_ready':
        add(const Notice('다 만든 뒤에 가족과 함께 쓸 수 있어요.'));
      case 'unsupported':
        add(const Notice('이 대화 상대는 아직 가족과 함께 쓸 수 없어요.'));
      case 'closed':
        add(const Notice('잠시 가족 초대를 멈췄어요.'));
      case 'plan':
        add(AppCard(
          padding: const EdgeInsets.fromLTRB(20, 18, 16, 16),
          color: AppColors.claySoft,
          borderColor: Colors.transparent,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                keepAll(members > 0
                    ? '지금은 함께 쓰는 가족이 이야기할 수 없어요. 가족 요금제로 바꾸면 다시 함께 쓸 수 있어요.'
                    : '가족과 함께 쓰기는 가족 요금제에서 할 수 있어요.'),
                style: const TextStyle(fontWeight: FontWeight.w700, height: 1.5),
              ),
              const SizedBox(height: 10),
              FilledButton(
                onPressed: () => context.go('/plans?persona=$_id'),
                style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                child: const Text('요금제 보기'),
              ),
            ],
          ),
        ));
    }
    if (data['allowed'] == true && data['prelaunch'] == true) {
      add(const Notice(
        '결제가 열리기 전에는 무료로 함께 쓸 수 있어요. 결제가 열리면 가족 요금제에서 함께 써요.',
        icon: Icons.card_giftcard_rounded,
      ));
    }
    // 함께 쓰는 가족은 지금 방식(Fish)으로 만든 목소리만 듣는다. 어느 목소리인지는 서버가 정한다
    if (data['voiceShareable'] == false) {
      add(const Notice(
        '예전에 만든 목소리라 가족에게는 아직 들려 드릴 수 없어요(가족에게는 글로 대답해요). '
        '목소리를 새로 만들면 가족도 들을 수 있어요.',
        icon: Icons.graphic_eq_rounded,
      ));
    }
    return out;
  }

  /// 주인 확인(그분 허락 · 건강·종교 이야기 따로 허락 등). 체크한 채 링크를 만들면 함께 보내고,
  /// '확인했어요'로 확인만 할 수도 있다. 그분 상태를 바꿔 거둬졌으면 다시 받는다([members] = 들어온 가족 수).
  List<Widget> _confirmations(Map<String, dynamic> data, {required int members}) {
    final labels = {
      for (final c in data['consentItems'] as List? ?? const [])
        if (c is Map) '${c['type']}': '${c['label'] ?? ''}',
    };
    return [
      const SizedBox(height: 10),
      // 링크나 가족이 있으면 한 번 확인했던 것이다
      SectionLabel(_items.isEmpty ? '처음 한 번만 확인해 주세요' : '다시 확인해 주세요'),
      if (members > 0) ...[
        const Notice('다시 확인할 때까지 함께 쓰는 가족은 이야기할 수 없어요.', icon: Icons.pause_circle_outline_rounded),
        const SizedBox(height: 10),
      ],
      AppCard(
        padding: const EdgeInsets.fromLTRB(8, 8, 12, 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (final type in _need)
              ConsentCheck(
                tag: '필수',
                label: fillCallName(labels[type] ?? type, _callName),
                value: _agreed.contains(type),
                enabled: !_busy,
                onChanged: (v) => setState(() => _agreed = v ? {..._agreed, type} : ({..._agreed}..remove(type))),
              ),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton(
                onPressed: () => showLegalDoc(context, 'family_share', callName: _callName),
                child: const Text('가족 공유 안내 보기'),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(8, 4, 0, 6),
              child: Align(
                alignment: Alignment.centerLeft,
                child: FilledButton(
                  onPressed: _confirmed && !_busy ? _confirm : null,
                  style: FilledButton.styleFrom(minimumSize: const Size(0, 44)),
                  child: const Text('확인했어요'),
                ),
              ),
            ),
          ],
        ),
      ),
    ];
  }

  /// 누구를 초대할까요: 가족 표의 살아 계신 분(서버가 돌아가신 분은 빼고 준다. 이미 함께하거나 링크를 보낸
  /// 분은 고를 수 없다) + 다른 가족.
  List<Widget> _whom(Map<String, dynamic> data, {required bool full, required int cap}) {
    final rows = [for (final r in data['familyRows'] as List? ?? const []) if (r is Map) Map<String, dynamic>.from(r)];
    final ready = _confirmed && !_busy;
    return [
      const SizedBox(height: 22),
      const SectionLabel('누구를 초대할까요?'),
      if (full)
        Notice('함께 쓸 수 있는 가족은 $cap명까지예요. 쓰지 않은 링크를 취소하거나 가족을 내보낸 뒤 다시 만들어 주세요.')
      else ...[
        Opacity(
          opacity: _confirmed ? 1 : 0.55,
          child: Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final row in rows) _rowPill(row, enabled: ready),
              Pill(
                label: '다른 가족',
                icon: Icons.person_add_alt_1_outlined,
                onTap: ready ? () => _invite() : null,
              ),
            ],
          ),
        ),
        if (!_confirmed)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(keepAll('위 확인에 모두 체크하면 초대할 수 있어요.'), style: AppText.small),
          ),
      ],
    ];
  }

  Widget _rowPill(Map<String, dynamic> row, {required bool enabled}) {
    final name = '${row['myName'] ?? row['familyRow'] ?? ''}'.trim();
    final relation = '${row['myRelation'] ?? ''}'.trim();
    final label = relation.isEmpty ? name : '$name · $relation';
    if (row['taken'] != true) return Pill(label: label, onTap: enabled ? () => _invite(row) : null);
    // 이미 함께하거나 링크를 보낸 분은 고를 수 없다
    final joined = _items.any((i) => i['status'] == 'joined' && (i['profile'] as Map?)?['familyRow'] == row['familyRow']);
    return Opacity(
      opacity: 0.55,
      child: Pill(label: '$label · ${joined ? '함께하는 중' : '링크 보냄'}', tint: AppColors.sunk),
    );
  }

  Widget _joinedRow(Map<String, dynamic> item) {
    final profile = item['profile'] is Map ? Map<String, dynamic>.from(item['profile'] as Map) : <String, dynamic>{};
    final name = _itemName(item);
    final term = '${profile['addressTerm'] ?? ''}'.trim();
    final since = monthDay(parseServerTime(item['joinedAt'] as String?));
    final account = '${item['accountName'] ?? ''}'.trim();
    final relation = '${profile['myRelation'] ?? ''}'.trim();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Avatar(name, size: 40),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      keepAll(relation.isEmpty ? name : '$name · $relation'),
                      style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      keepAll([
                        if (term.isNotEmpty) callsText(term),
                        if (since.isNotEmpty) '$since부터',
                      ].join(' · ')),
                      style: AppText.small.copyWith(color: AppColors.ink2),
                    ),
                    if (account.isNotEmpty) Text('계정 이름 $account', style: AppText.small.copyWith(fontSize: 12)),
                  ],
                ),
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(left: 44),
            child: Wrap(
              children: [
                TextButton(onPressed: _busy ? null : () => _edit(item), child: const Text('정보 고치기')),
                TextButton(
                  onPressed: _busy ? null : () => _remove(item),
                  style: TextButton.styleFrom(foregroundColor: AppColors.danger),
                  child: const Text('내보내기'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _openRow(Map<String, dynamic> item) {
    final label = '${item['label'] ?? ''}'.trim();
    final until = monthDay(parseServerTime(item['expiresAt'] as String?));
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 40,
                height: 40,
                decoration: const BoxDecoration(color: AppColors.sunk, shape: BoxShape.circle),
                child: const Icon(Icons.link_rounded, size: 20, color: AppColors.ink2),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      keepAll(label.isEmpty ? '이름 없이 보낸 링크' : label),
                      style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15.5),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      until.isEmpty ? '아직 들어오지 않았어요' : '아직 들어오지 않았어요 · $until까지',
                      style: AppText.small,
                    ),
                  ],
                ),
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(left: 44),
            child: Wrap(
              children: [
                TextButton(onPressed: _busy ? null : () => _renew(item), child: const Text('다시 보내기')),
                TextButton(onPressed: _busy ? null : () => _edit(item), child: const Text('고치기')),
                TextButton(
                  onPressed: _busy ? null : () => _cancel(item),
                  style: TextButton.styleFrom(foregroundColor: AppColors.muted),
                  child: const Text('취소'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
