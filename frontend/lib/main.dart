import 'package:flutter/material.dart';

import 'core/app_update.dart';
import 'core/router.dart';
import 'core/session.dart';
import 'core/theme.dart';
import 'features/legal/legal_footer.dart';
import 'shared/widgets/ui.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await session.init();
  // 정보를 받는 화면(만들기 단계·녹음·가족 공유·동의 녹음 등) 아래에 늘 개인정보 처리방침 링크를 붙인다
  WizardScaffold.defaultFooter = (_) => const LegalFooter(compact: true);
  runApp(const TalkToApp());
  startUpdateWatch();
}

class TalkToApp extends StatelessWidget {
  const TalkToApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'TalkTo',
      debugShowCheckedModeBanner: false,
      scaffoldMessengerKey: appMessengerKey,
      theme: buildTheme(),
      routerConfig: router,
    );
  }
}
