import 'package:flutter/material.dart';

import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import 'legal_api.dart';

/// 동의문 본문(서버 app/static/legal/*.md)을 그린다. 쓰는 문법은 일부만:
/// `## 제목`, `### 소제목`, 빈 줄로 나눈 문단, `- 목록`(두 칸 들여쓰면 하위 목록), `**굵게**`.
class LegalText extends StatelessWidget {
  final String body;

  /// 있으면 본문 위에 문서 제목을 함께 그린다.
  final String? title;

  const LegalText(this.body, {super.key, this.title});

  @override
  Widget build(BuildContext context) {
    final children = <Widget>[
      if (title != null && title!.trim().isNotEmpty)
        Padding(
          padding: const EdgeInsets.only(bottom: 14),
          child: Text(keepAll(title!), style: AppText.title),
        ),
    ];
    final paragraph = <String>[];

    void flush() {
      if (paragraph.isEmpty) return;
      children.add(Padding(
        padding: const EdgeInsets.only(bottom: 10),
        child: _rich(paragraph.join(' '), AppText.body),
      ));
      paragraph.clear();
    }

    for (final raw in body.split('\n')) {
      final line = raw.trimRight();
      if (line.trim().isEmpty) {
        flush();
        continue;
      }
      if (line.startsWith('### ')) {
        flush();
        children.add(Padding(
          padding: const EdgeInsets.only(top: 8, bottom: 6),
          child: _rich(line.substring(4), AppText.label),
        ));
      } else if (line.startsWith('## ')) {
        flush();
        children.add(Padding(
          padding: const EdgeInsets.only(top: 14, bottom: 8),
          child: _rich(line.substring(3), AppText.heading),
        ));
      } else if (line.trimLeft().startsWith('- ')) {
        flush();
        final depth = (line.length - line.trimLeft().length) ~/ 2;
        children.add(Padding(
          padding: EdgeInsets.only(left: 4.0 + depth * 18, bottom: 6),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Padding(
                padding: const EdgeInsets.only(top: 9, right: 9),
                child: Container(
                  width: depth == 0 ? 5 : 4,
                  height: depth == 0 ? 5 : 4,
                  decoration: BoxDecoration(
                    color: depth == 0 ? AppColors.primary : AppColors.muted,
                    shape: BoxShape.circle,
                  ),
                ),
              ),
              Expanded(child: _rich(line.trimLeft().substring(2), AppText.body)),
            ],
          ),
        ));
      } else {
        paragraph.add(line.trim());
      }
    }
    flush();
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: children);
  }

  /// `**굵게**` 를 굵은 글씨로. 단어 중간에서 줄이 끊기지 않게 keepAll 을 쓴다.
  static Widget _rich(String text, TextStyle style) {
    final spans = <TextSpan>[];
    final parts = text.split('**');
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].isEmpty) continue;
      spans.add(TextSpan(
        text: keepAll(parts[i]),
        style: i.isOdd ? const TextStyle(fontWeight: FontWeight.w700, color: AppColors.ink) : null,
      ));
    }
    return Text.rich(TextSpan(style: style, children: spans));
  }
}

/// 사업자 정보(전자상거래법 표시 항목). 값은 서버(GET /legal 의 business)에서만 받는다. 없으면 '준비 중'.
class BusinessInfo extends StatelessWidget {
  /// 사업자 정보를 읽는다. 시험에서는 서버 없이 바꿔 끼운다.
  final Future<Map<String, dynamic>> Function() load;

  const BusinessInfo({super.key, this.load = LegalApi.business});

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<Map<String, dynamic>>(
      future: load(),
      builder: (context, snap) {
        final b = snap.data;
        if (b == null) return const SizedBox.shrink();
        String v(String key, [String empty = '준비 중']) =>
            (b[key] as String?)?.trim().isNotEmpty == true ? b[key] as String : empty;
        // 전자상거래법 제10조 제1항: 상호·대표자·주소·전화·이메일·사업자등록번호·통신판매업 신고번호·호스팅 제공자
        final rows = [
          '상호 ${v('name')} · 대표 ${v('owner')}',
          // 신고번호는 공개된 값이라 서버가 비어 있을 때도 그대로 보인다(신고 마침 2026-10-16)
          '사업자등록번호 ${v('regNo')} · 통신판매업 신고번호 ${v('mailOrderNo', '제2026-서울용산-1112호')}',
          '주소 ${v('address')}',
          '전화 ${v('phone')} · 이메일 ${v('email')}',
          '호스팅 제공 ${v('hosting', 'Amazon Web Services(AWS)')}',
        ];
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [for (final r in rows) Text(keepAll(r), style: AppText.small.copyWith(fontSize: 12))],
        );
      },
    );
  }
}

/// 사업자 정보를 띄워 보여 준다(로그인 화면 아래 '사업자 정보').
Future<void> showBusinessInfo(BuildContext context) => showDialog<void>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: const Text('사업자 정보'),
        content: const SizedBox(width: 420, child: BusinessInfo()),
        actions: [TextButton(onPressed: () => Navigator.pop(dialog), child: const Text('닫기'))],
      ),
    );

/// 문서 하나를 띄워 보여 준다(넓으면 다이얼로그, 좁으면 아래에서 올라오는 시트).
/// [callName] 은 본문의 {{호칭}} 을 바꿀 말(예: 할머니).
Future<void> showLegalDoc(BuildContext context, String key, {String? callName}) async {
  Widget body(BuildContext sheet) => FutureBuilder<Map<String, dynamic>>(
        future: LegalApi.doc(key),
        builder: (context, snap) {
          if (snap.hasError) {
            return Padding(padding: const EdgeInsets.all(20), child: ErrorBox(snap.error.toString()));
          }
          final doc = snap.data;
          if (doc == null) return const Padding(padding: EdgeInsets.all(40), child: Loading());
          return Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(22, 20, 8, 4),
                child: Row(
                  children: [
                    Expanded(child: Text(doc['title'] as String? ?? '', style: AppText.title)),
                    IconButton(
                      tooltip: '닫기',
                      onPressed: () => Navigator.pop(sheet),
                      icon: const Icon(Icons.close_rounded),
                    ),
                  ],
                ),
              ),
              Flexible(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.fromLTRB(22, 4, 22, 20),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      LegalText(fillCallName(doc['body'] as String? ?? '', callName)),
                      const SizedBox(height: 10),
                      Text('버전 ${doc['version'] ?? ''}', style: AppText.small.copyWith(fontSize: 12)),
                    ],
                  ),
                ),
              ),
            ],
          );
        },
      );

  // 부른 화면의 글 크기를 그대로 쓴다. 창은 앱 맨 위 Navigator 에 따로 그려져서, 그 화면만 키운 글 크기
  // (초대 받기 화면은 어르신을 위해 1.15배)를 물려받지 않는다.
  final textScaler = MediaQuery.textScalerOf(context);
  Widget content(BuildContext sheet) =>
      MediaQuery(data: MediaQuery.of(sheet).copyWith(textScaler: textScaler), child: body(sheet));

  if (Breakpoints.isWide(context)) {
    await showDialog<void>(
      context: context,
      builder: (sheet) => Dialog(
        child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: 640, maxHeight: MediaQuery.sizeOf(context).height * 0.85),
          child: content(sheet),
        ),
      ),
    );
  } else {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      backgroundColor: AppColors.card,
      constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.9),
      builder: (sheet) => SafeArea(child: content(sheet)),
    );
  }
}
