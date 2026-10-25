import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_client.dart';
import '../../core/session.dart';
import '../../core/theme.dart';
import '../../shared/widgets/ui.dart';
import 'legal_api.dart';
import 'legal_text.dart';

/// 문서 한 장(/legal/:key). 로그인하지 않아도 볼 수 있다(로그인 화면 아래 '이용약관' 등).
class LegalDocPage extends StatefulWidget {
  final String docKey;

  const LegalDocPage({super.key, required this.docKey});

  @override
  State<LegalDocPage> createState() => _LegalDocPageState();
}

class _LegalDocPageState extends State<LegalDocPage> {
  late Future<Map<String, dynamic>> _doc = LegalApi.doc(widget.docKey);

  @override
  Widget build(BuildContext context) {
    final wide = Breakpoints.isWide(context);
    return Scaffold(
      appBar: AppBar(
        title: const Text('약관과 안내'),
        leading: Navigator.of(context).canPop()
            ? null
            : IconButton(
                tooltip: '돌아가기',
                icon: const Icon(Icons.arrow_back_rounded),
                onPressed: () => context.go(session.loggedIn ? '/' : '/login'),
              ),
      ),
      body: FutureBuilder<Map<String, dynamic>>(
        future: _doc,
        builder: (context, snap) {
          if (snap.hasError) {
            final error = snap.error;
            return Center(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    ErrorBox(error is ApiException ? error.message : '문서를 불러오지 못했어요. 잠시 뒤 다시 해 주세요.'),
                    const SizedBox(height: 8),
                    TextButton(
                      onPressed: () => setState(() {
                        _doc = LegalApi.doc(widget.docKey);
                      }),
                      child: const Text('다시 불러오기'),
                    ),
                  ],
                ),
              ),
            );
          }
          final doc = snap.data;
          if (doc == null) return const Loading();
          return SingleChildScrollView(
            padding: EdgeInsets.fromLTRB(20, wide ? 28 : 8, 20, 48),
            child: PageBody(
              maxWidth: 720,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  LegalText(
                    fillCallName(doc['body'] as String? ?? '', null),
                    title: doc['title'] as String?,
                  ),
                  const SizedBox(height: 8),
                  Text('버전 ${doc['version'] ?? ''}', style: AppText.small.copyWith(fontSize: 12)),
                  const SizedBox(height: 28),
                  const Divider(height: 1),
                  const SizedBox(height: 16),
                  const BusinessInfo(),
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}
