import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../core/network/api_client.dart';
import '../../core/session.dart';
import '../../shared/widgets/ui.dart';
import '../share/pending_invite.dart';

/// 카카오·구글 로그인을 마치고 돌아온 곳. 서버가 준 한 번 쓰는 표를 토큰으로 바꾼다.
class AuthCallbackPage extends StatefulWidget {
  final String? ticket;

  const AuthCallbackPage({super.key, this.ticket});

  @override
  State<AuthCallbackPage> createState() => _AuthCallbackPageState();
}

class _AuthCallbackPageState extends State<AuthCallbackPage> {
  @override
  void initState() {
    super.initState();
    // 화면을 그리는 중에 다른 화면으로 옮기지 않게, 첫 화면을 그린 뒤에 시작한다
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _exchange();
    });
  }

  /// 표를 바꾸지 못한 까닭 → 로그인 화면에 붙이는 이름(code). 로그인 화면이 정해 둔 글로 바꿔 보인다
  /// ([loginErrorText]). 주소에 글을 그대로 싣지 않는다(남이 만든 주소로 아무 글이나 띄우지 못하게).
  static String _errorCode(Object error) {
    if (error is! ApiException) return 'failed';
    return switch (error.statusCode) {
      401 => 'expired',
      403 => 'unavailable',
      429 => 'retry',
      null => 'network',
      _ => 'failed',
    };
  }

  Future<void> _exchange() async {
    // 이미 로그인한 채로 열면 표를 쓰지 않는다. 남이 자기 계정으로 만든 로그인 주소(#/auth?ticket=…)를
    // 보내 그 계정으로 몰래 바꿔 넣지 못하게(로그인 위조). 정상 흐름은 로그인 화면·다른 계정으로
    // 들어가기 모두 먼저 로그아웃한 뒤 여기로 온다.
    if (session.loggedIn) {
      context.go(PendingInvite.path ?? '/');
      return;
    }
    final ticket = widget.ticket;
    String? error;
    if (ticket == null || ticket.isEmpty) {
      error = 'state';
    } else {
      try {
        await session.exchangeTicket(ticket);
      } catch (e) {
        error = _errorCode(e);
      }
    }
    if (!mounted) return;
    if (error == null) {
      // 가족 초대 링크에서 로그인했으면 그 초대로 돌아간다(필수 동의가 남았으면 라우터가 동의 화면을 거친다)
      context.go(PendingInvite.path ?? '/');
    } else {
      context.go(Uri(path: '/login', queryParameters: {'error': error}).toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    return const Scaffold(
      body: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [Breathing(), SizedBox(height: 18), Text('로그인하는 중이에요')],
        ),
      ),
    );
  }
}
