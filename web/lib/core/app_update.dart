import 'dart:async';

import 'package:flutter/material.dart';

import 'platform/browser.dart' as browser;

/// 이 화면을 만든 웹 빌드 번호(배포할 때 --dart-define=APP_BUILD 로 넣는다). 로컬·시험 빌드는 빈칸.
const appBuild = String.fromEnvironment('APP_BUILD');

/// 앱 전체에 알림 띠를 띄우는 곳.
final appMessengerKey = GlobalKey<ScaffoldMessengerState>();

bool _shown = false;
bool _checking = false;

/// 새 버전이 올라오면 '새로 고침' 띠를 띄운다. 열어 둔 탭·홈 화면 앱은 스스로 새로 읽지 않아서다.
/// 스스로 새로 고치지는 않는다(녹음을 올리는 중일 수 있다).
void startUpdateWatch() {
  if (appBuild.isEmpty) return;
  Future<void> check() async {
    if (_shown || _checking) return;
    _checking = true;
    final String? remote;
    try {
      remote = await browser.fetchDeployedBuild();
    } finally {
      _checking = false;
    }
    // 같은 모양(숫자 14자리)의 더 새 번호일 때만
    if (remote == null || remote.length != appBuild.length || remote.compareTo(appBuild) <= 0) return;
    final messenger = appMessengerKey.currentState;
    if (messenger == null) return;
    _shown = true;
    messenger.showMaterialBanner(
      MaterialBanner(
        content: const Text('새 버전이 나왔어요. 새로 고치면 바로 바뀌어요.'),
        actions: [
          TextButton(onPressed: messenger.hideCurrentMaterialBanner, child: const Text('나중에')),
          const TextButton(onPressed: browser.reloadPage, child: Text('새로 고침')),
        ],
      ),
    );
  }

  WidgetsBinding.instance.addPostFrameCallback((_) => unawaited(check()));
  browser.onPageVisible(() => unawaited(check()));
  Timer.periodic(const Duration(minutes: 10), (_) => unawaited(check()));
}
